// CI fonts are immutable Ubuntu package payloads, not a cached browser or OS image.
// The public Playwright dry-run owns the dependency list; a new missing library
// fails instead of silently weakening host requirements. See:
// https://playwright.dev/docs/browsers#install-system-dependencies
// https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  appendFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync,
  readlinkSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const xml = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const rules = directory => existsSync(directory)
  ? readdirSync(directory).filter(name => /^\d.*\.conf$/.test(name)).sort() : [];

export function requireRunner({ platform = process.platform, arch = process.arch,
  ci = process.env.CI, release = readFileSync('/etc/os-release', 'utf8') } = {}) {
  if (ci !== 'true' || platform !== 'linux' || arch !== 'x64'
      || !/^ID=ubuntu$/m.test(release) || !/^VERSION_ID="?24\.04"?$/m.test(release)) {
    throw new Error('Browser dependency preparation is only for CI Ubuntu 24.04 x64; no OS packages are installed.');
  }
}

export function missingFonts(result) {
  if (result.status === 0 && result.stdout.trim() === 'All system dependencies are installed.') return [];
  const match = /^Missing system dependencies \((\d+)\):\n((?:  [a-z0-9][a-z0-9+.-]*\n?)+)$/.exec(result.stdout.trimEnd());
  if (result.status !== 1 || !match) throw new Error(`Playwright dependency check failed: ${result.stderr || result.stdout}`);
  const packages = match[2].trim().split(/\n  /);
  if (packages.length !== Number(match[1]) || new Set(packages).size !== packages.length) {
    throw new Error('Incomplete Playwright dependency plan.');
  }
  const libraries = packages.filter(name => !/^(?:fonts|xfonts)-/.test(name));
  if (libraries.length) throw new Error(`Runner is missing required runtime packages: ${libraries.join(', ')}`);
  return packages.sort();
}

// apt-cache rebuilds its package view per invocation. Query the complete set
// once, but require an unambiguous candidate owned by each requested name.
export function policyCandidates(names, output) {
  const required = new Set(names);
  if (required.size !== names.length) throw new Error('Duplicate requested package policy.');
  const candidates = new Map();
  for (const block of output.trimEnd().split(/\n(?=\S)/).filter(Boolean)) {
    const name = /^([a-z0-9][a-z0-9+.-]*):\n/.exec(block)?.[1];
    const values = [...block.matchAll(/^\s+Candidate: ([\w.+:~\-]+)$/gm)];
    if (!name || !required.has(name) || candidates.has(name) || values.length !== 1) {
      throw new Error('Missing, duplicate or misattributed package candidate.');
    }
    candidates.set(name, values[0][1]);
  }
  if (candidates.size !== required.size) throw new Error('Incomplete package candidates.');
  return candidates;
}

export function packageRecord(name, candidate, output) {
  if (!/^[a-z0-9][a-z0-9+.-]*$/.test(name) || !/^[\w.+:~\-]+$/.test(candidate)) throw new Error('Invalid package identity.');
  const records = output.trim().split(/\n\s*\n/).map(stanza => Object.fromEntries(
    stanza.split('\n').flatMap(line => { const match = /^([\w-]+): (.*)$/.exec(line); return match ? [[match[1], match[2]]] : []; }),
  )).filter(record => record.Package === name && record.Version === candidate);
  const record = records.find(item => ['all', 'amd64'].includes(item.Architecture));
  if (!record || !/^[a-f0-9]{64}$/.test(record.SHA256) || !/^[1-9]\d*$/.test(record.Size)
      || !record.Filename?.startsWith('pool/') || record.Filename.includes('..')) {
    throw new Error(`Missing authenticated package metadata for ${name}=${candidate}.`);
  }
  if (records.some(item => item.SHA256 !== record.SHA256)) throw new Error(`Ambiguous package metadata for ${name}.`);
  return { name, version: candidate, architecture: record.Architecture, sha256: record.SHA256, size: Number(record.Size) };
}

export function playwrightCLI(packagePath, metadata) {
  const binary = typeof metadata.bin === 'string' ? metadata.bin : metadata.bin?.playwright;
  if (typeof binary !== 'string' || isAbsolute(binary) || binary.includes('..')) throw new Error('Playwright public CLI entry is missing.');
  return join(dirname(packagePath), binary);
}

export function cacheKey(version, packages) {
  return `browser-fonts-noble-x64-v1-${version}-${sha256(JSON.stringify(packages))}`;
}

export function verifyArchive(path, record) {
  if (!lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) throw new Error(`Unsafe package archive: ${path}`);
  const bytes = readFileSync(path);
  if (bytes.length !== record.size || sha256(bytes) !== record.sha256) throw new Error(`Package archive hash/size mismatch: ${record.name}`);
}

function run(command, args, options = {}) {
  return execFileSync(command, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...options });
}

function contained(root, path) {
  const suffix = relative(root, path);
  if (suffix.startsWith('..') || isAbsolute(suffix)) throw new Error(`Package path escapes extraction: ${path}`);
  return path;
}

// Preserve stock rule positions, replacing only the conf.d location. Package
// symlinks are resolved inside the fresh extraction, never through host /etc.
export function fontConfiguration(stockDirectory, payload, runtime) {
  const merged = join(runtime, 'conf.d');
  mkdirSync(merged, { recursive: true });
  for (const name of rules(join(stockDirectory, 'conf.d'))) {
    symlinkSync(realpathSync(join(stockDirectory, 'conf.d', name)), join(merged, name));
  }
  const supplied = join(payload, 'etc/fonts/conf.d');
  for (const name of rules(supplied)) {
    const path = join(supplied, name);
    let target = path;
    if (lstatSync(path).isSymbolicLink()) {
      // Debian font links use ../conf.avail/... (or an absolute package path).
      const link = readlinkSync(path);
      target = isAbsolute(link) ? join(payload, link) : resolve(dirname(path), link);
    }
    contained(payload, target);
    const actual = realpathSync(target);
    contained(payload, actual);
    if (existsSync(join(merged, name))) {
      if (!readFileSync(join(merged, name)).equals(readFileSync(actual))) throw new Error(`Conflicting font rule: ${name}`);
    } else symlinkSync(actual, join(merged, name));
  }
  const stock = readFileSync(join(stockDirectory, 'fonts.conf'), 'utf8');
  const include = /<include\b[^>]*>\s*conf\.d\s*<\/include>/g;
  if ([...stock.matchAll(include)].length !== 1) throw new Error('Unknown stock fontconfig include layout.');
  const fontRoot = join(payload, 'usr/share/fonts');
  const configuration = stock.replace(include, `<include>${xml(merged)}</include>`)
    .replace('<fontconfig>', `<fontconfig>\n<cachedir>${xml(join(runtime, 'font-cache'))}</cachedir>`)
    .replace('</fontconfig>', `<dir>${xml(fontRoot)}</dir>\n</fontconfig>`);
  const path = join(runtime, 'fonts.conf');
  writeFileSync(path, configuration);
  return path;
}

export function outlineFonts(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return outlineFonts(path);
    if (entry.isSymbolicLink()) throw new Error(`Unexpected font payload link: ${path}`);
    return /\.(?:ttf|otf|ttc|pfb|pfa)$/i.test(entry.name) ? [path] : [];
  });
}

export function verifyFaces(stock, expected, actual) {
  const faces = new Set(actual.trim().split('\n').filter(Boolean));
  for (const face of [...stock.trim().split('\n'), ...expected.trim().split('\n')].filter(Boolean)) {
    if (!faces.has(face)) throw new Error(`Fontconfig lost a font face: ${face}`);
  }
}

function paths() {
  if (!process.env.RUNNER_TEMP || !process.env.GITHUB_OUTPUT || !process.env.GITHUB_ENV) throw new Error('Required CI runner paths are missing.');
  const root = join(process.env.RUNNER_TEMP, 'schemii-browser-dependencies');
  mkdirSync(root, { recursive: true });
  return { root, cache: join(root, 'archives'), plan: join(root, 'plan.json') };
}

function plan() {
  const { root, cache, plan: planPath } = paths();
  const aptConfig = join(root, 'apt.conf');
  // Required libraries already exist on this runner. --no-upgrade prevents an
  // unrelated installed Mesa upgrade from becoming a font/download dependency.
  writeFileSync(aptConfig, 'APT::Get::Upgrade "false";\n');
  const env = { ...process.env, APT_CONFIG: aptConfig, LC_ALL: 'C' };
  const cli = playwrightCLI(require.resolve('playwright/package.json'), require('playwright/package.json'));
  let result;
  try { result = { status: 0, stdout: run(process.execPath, [cli, 'install-deps', '--dry-run', 'chromium'], { env }) }; }
  catch (error) { result = { status: error.status, stdout: error.stdout?.toString() || '', stderr: error.stderr?.toString() || '' }; }
  const names = missingFonts(result);
  const candidates = policyCandidates(names, names.length ? run('apt-cache', ['policy', ...names], { env }) : '');
  const records = names.length ? run('apt-cache', ['show', ...names.map(name => `${name}=${candidates.get(name)}`)], { env }) : '';
  const packages = names.map(name => packageRecord(name, candidates.get(name), records));
  const version = require('playwright/package.json').version;
  const key = cacheKey(version, packages);
  mkdirSync(cache, { recursive: true });
  writeFileSync(planPath, `${JSON.stringify({ version, packages, key, aptConfig }, null, 2)}\n`);
  appendFileSync(process.env.GITHUB_OUTPUT, `cache_key=${key}\ncache_dir=${cache}\n`);
  console.log(`Required runtime packages already installed; ${packages.length} exact font payloads (${packages.reduce((sum, item) => sum + item.size, 0)} bytes).`);
}

async function prepare() {
  const { root, cache, plan: planPath } = paths();
  const receipt = JSON.parse(readFileSync(planPath, 'utf8'));
  if (receipt.version !== require('playwright/package.json').version || receipt.key !== cacheKey(receipt.version, receipt.packages)) throw new Error('Dependency plan identity changed.');
  const env = { ...process.env, APT_CONFIG: receipt.aptConfig, LC_ALL: 'C' };
  if (!lstatSync(cache).isDirectory() || lstatSync(cache).isSymbolicLink()) throw new Error('Unsafe font archive cache directory.');
  const downloaded = [];
  for (const record of receipt.packages) {
    const archive = join(cache, `${record.name}-${record.sha256}.deb`);
    if (!existsSync(archive)) {
      const download = join(root, `download-${record.name}`);
      mkdirSync(download);
      try {
        run('apt-get', ['download', `${record.name}=${record.version}`], { cwd: download, env });
        const files = readdirSync(download).filter(name => name.endsWith('.deb'));
        if (files.length !== 1) throw new Error(`Incomplete font download: ${record.name}`);
        verifyArchive(join(download, files[0]), record);
        renameSync(join(download, files[0]), archive);
        downloaded.push(record.name);
      } finally { rmSync(download, { recursive: true, force: true }); }
    }
    verifyArchive(archive, record);
    const identity = run('dpkg-deb', ['--field', archive, 'Package', 'Version', 'Architecture']);
    if (!identity.includes(`Package: ${record.name}\n`) || !identity.includes(`Version: ${record.version}\n`) || !identity.includes(`Architecture: ${record.architecture}\n`)) throw new Error(`Archive identity mismatch: ${record.name}`);
  }
  const runtime = join(root, 'runtime');
  const payload = join(runtime, 'packages');
  if (existsSync(runtime)) throw new Error('Font runtime must be a fresh extraction.');
  mkdirSync(payload, { recursive: true });
  for (const record of receipt.packages) run('dpkg-deb', ['--extract', join(cache, `${record.name}-${record.sha256}.deb`), payload]);
  const format = '%{file}\t%{index}\t%{family}\t%{style}\n';
  mkdirSync(join(payload, 'usr/share/fonts'), { recursive: true });
  const stock = run('fc-list', ['--format', format]);
  const configuration = fontConfiguration('/etc/fonts', payload, runtime);
  const fontEnv = { ...process.env, FONTCONFIG_FILE: configuration, FONTCONFIG_PATH: '/etc/fonts' };
  run('fc-cache', ['--force', join(payload, 'usr/share/fonts')], { env: fontEnv });
  const expected = outlineFonts(join(payload, 'usr/share/fonts')).map(path => run('fc-scan', ['--format', format, path], { env: fontEnv })).join('');
  const actual = run('fc-list', ['--format', format], { env: fontEnv });
  verifyFaces(stock, expected, actual);
  if (receipt.packages.some(item => item.name.startsWith('fonts-')) && !expected.trim()) throw new Error('Font packages supplied no readable faces.');
  appendFileSync(process.env.GITHUB_ENV, `FONTCONFIG_FILE=${configuration}\nFONTCONFIG_PATH=/etc/fonts\n`);
  // Browser installation and launch use supported public Playwright APIs. No
  // host-validation bypass, channel switch, browser cache or OS installation.
  run(process.execPath, [playwrightCLI(require.resolve('playwright/package.json'), require('playwright/package.json')), 'install', 'chromium'], { env: fontEnv, stdio: 'inherit' });
  const { chromium } = await import('playwright');
  const browser = await chromium.launch({ env: fontEnv });
  try {
    const page = await browser.newPage();
    await page.setContent('<p style="font: 24px sans-serif">Schemii Ω Ж 日本語 中文 ไทย 😀</p>');
    if (!(await page.locator('p').boundingBox())?.width) throw new Error('Chromium font rendering smoke failed.');
    const png = await page.screenshot();
    if (!png.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error('Chromium produced no rendered PNG.');
  } finally { await browser.close(); }
  const evidence = { ...receipt, downloaded, archiveCacheHits: receipt.packages.length - downloaded.length, stockFaces: stock.trim().split('\n').filter(Boolean).length,
    packageFaces: expected.trim().split('\n').filter(Boolean).length, configuration, runtimeSmoke: 'passed' };
  const evidencePath = resolve('artifacts/ci-timing/browser-dependencies.json');
  mkdirSync(dirname(evidencePath), { recursive: true });
  writeFileSync(evidencePath, `${JSON.stringify({ ...evidence, sourceSha: process.env.CI_TELEMETRY_SHA, headSha: process.env.CI_TELEMETRY_HEAD_SHA, runId: process.env.CI_TELEMETRY_RUN_ID, runAttempt: process.env.CI_TELEMETRY_RUN_ATTEMPT, runnerImage: process.env.ImageVersion }, null, 2)}\n`);
  console.log(`Preserved all stock faces and ${evidence.packageFaces} package faces; Chromium launch/render passed.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    requireRunner();
    if (process.argv[2] === 'plan') plan();
    else if (process.argv[2] === 'prepare') await prepare();
    else throw new Error('Usage: browser-dependencies.mjs plan|prepare');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
