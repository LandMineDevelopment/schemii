import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  cacheKey, fontConfiguration, missingFonts, outlineFonts, packageRecord,
  playwrightCLI, requireRunner, verifyArchive, verifyFaces,
} from './browser-dependencies.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const runner = { ci: 'true', platform: 'linux', arch: 'x64', release: 'ID=ubuntu\nVERSION_ID="24.04"\n' };
const missing = names => ({ status: 1, stdout: `Missing system dependencies (${names.length}):\n${names.map(name => `  ${name}`).join('\n')}\n` });
const metadata = (name, version, bytes, extra = '') => `Package: ${name}\nVersion: ${version}\nArchitecture: all\nFilename: pool/universe/f/fonts-test/example.deb\nSize: ${bytes.length}\nSHA256: ${digest(bytes)}\n${extra}`;

function fixture(t) {
  const path = mkdtempSync(join(tmpdir(), 'schemii-browser-dependencies-'));
  t.after(() => rmSync(path, { recursive: true, force: true }));
  return path;
}

function fontFixture(t) {
  const path = fixture(t);
  const stock = join(path, 'stock');
  const payload = join(path, 'payload');
  const runtime = join(path, 'runtime');
  for (const directory of [join(stock, 'conf.d'), join(stock, 'conf.avail'), join(payload, 'etc/fonts/conf.avail'), join(payload, 'etc/fonts/conf.d')]) mkdirSync(directory, { recursive: true });
  writeFileSync(join(stock, 'fonts.conf'), '<fontconfig>\n<dir>/usr/share/fonts</dir>\n<include ignore_missing="yes">conf.d</include>\n<cachedir prefix="xdg">fontconfig</cachedir>\n</fontconfig>');
  for (const name of ['10-hinting.conf', '60-generic.conf']) {
    writeFileSync(join(stock, 'conf.avail', name), `<fontconfig><!--${name}--></fontconfig>`);
    symlinkSync(join('../conf.avail', name), join(stock, 'conf.d', name));
  }
  return { stock, payload, runtime };
}

test('Ubuntu preparation is explicit CI only; the local Arch quick loop cannot invoke it', () => {
  assert.doesNotThrow(() => requireRunner(runner));
  for (const override of [{ ci: undefined }, { platform: 'darwin' }, { arch: 'arm64' }, { release: 'ID=arch\nVERSION_ID=24.04\n' }, { release: 'ID=ubuntu\nVERSION_ID="22.04"\n' }]) {
    assert.throws(() => requireRunner({ ...runner, ...override }), /only for CI Ubuntu 24.04 x64/);
  }
  const result = spawnSync(process.execPath, ['scripts/ci/browser-dependencies.mjs', 'plan'], { cwd: root, env: { ...process.env, CI: 'false' }, encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /only for CI Ubuntu 24.04 x64/);
  assert.doesNotMatch(result.stderr, /apt-get|sudo|ENOENT/);
});

test('public Playwright dry-run keeps the full font/xfont closure and rejects missing libraries', () => {
  const names = ['xfonts-utils', 'fonts-unifont', 'xfonts-encodings', 'fonts-freefont-ttf'];
  assert.deepEqual(missingFonts(missing(names)), [...names].sort());
  assert.deepEqual(missingFonts({ status: 0, stdout: 'All system dependencies are installed.\n' }), []);
  for (const library of ['libgbm1', 'libnss3', 'libfontconfig1', 'libwayland-server0']) {
    assert.throws(() => missingFonts(missing(['fonts-unifont', library])), /missing required runtime packages/);
  }
});

test('a changed CLI format, failed APT simulation or truncated/duplicate plan never passes', () => {
  for (const result of [
    { status: 0, stdout: 'warning: skipped checks' },
    { status: 1, stdout: '', stderr: 'APT lists unavailable' },
    { status: 2, stdout: missing(['fonts-unifont']).stdout },
    { status: 1, stdout: 'Missing system dependencies (2):\n  fonts-unifont\n' },
    missing(['fonts-unifont', 'fonts-unifont']),
    { status: 1, stdout: 'Missing system dependencies (1):\n  fonts-unifont\n  unexpected output\n' },
  ]) assert.throws(() => missingFonts(result), /dependency check failed|Incomplete/);
});

test('package receipts require an exact authenticated candidate, architecture, size and SHA256', () => {
  const bytes = Buffer.from('immutable font package');
  const text = metadata('fonts-test', '1:2.3-4', bytes);
  const record = packageRecord('fonts-test', '1:2.3-4', text);
  assert.deepEqual(record, { name: 'fonts-test', version: '1:2.3-4', architecture: 'all', sha256: digest(bytes), size: bytes.length });
  for (const invalid of [text.replace('SHA256:', 'MD5sum:'), text.replace('Architecture: all', 'Architecture: arm64'), text.replace('Version: 1:2.3-4', 'Version: 1.0'), text.replace('Size: 22', 'Size: 0'), text.replace('pool/universe', '../universe')]) {
    assert.throws(() => packageRecord('fonts-test', '1:2.3-4', invalid), /Missing authenticated/);
  }
  assert.throws(() => packageRecord('fonts-test', '1:2.3-4', `${text}\n${text.replace(digest(bytes), 'a'.repeat(64))}`), /Ambiguous/);
  assert.throws(() => packageRecord('../fonts-test', '1.0', text), /Invalid package identity/);
});

test('font cache key changes when Playwright or any authenticated payload identity changes', () => {
  const record = packageRecord('fonts-test', '1.0', metadata('fonts-test', '1.0', Buffer.from('font')));
  const key = cacheKey('1.62.1', [record]);
  assert.equal(key, cacheKey('1.62.1', [{ ...record }]));
  for (const changed of [{ ...record, version: '1.1' }, { ...record, sha256: 'a'.repeat(64) }, { ...record, architecture: 'amd64' }]) assert.notEqual(key, cacheKey('1.62.1', [changed]));
  assert.notEqual(key, cacheKey('1.63.0', [record]));
});

test('a restored archive is checked before extraction; corruption and links fail closed', t => {
  const path = fixture(t);
  const bytes = Buffer.from('exact cached font payload');
  const record = packageRecord('fonts-test', '1.0', metadata('fonts-test', '1.0', bytes));
  const archive = join(path, 'font.deb');
  writeFileSync(archive, bytes);
  assert.doesNotThrow(() => verifyArchive(archive, record));
  writeFileSync(archive, Buffer.from('wrong cached font payload'));
  assert.throws(() => verifyArchive(archive, record), /hash\/size mismatch/);
  writeFileSync(archive, Buffer.alloc(bytes.length));
  assert.throws(() => verifyArchive(archive, record), /hash\/size mismatch/);
  writeFileSync(archive, bytes);
  const link = join(path, 'linked.deb'); symlinkSync(archive, link);
  assert.throws(() => verifyArchive(link, record), /Unsafe/);
  assert.throws(() => verifyArchive(path, record), /Unsafe/);
});

test('font rules keep stock lexical positions plus exact package rules and all stock font directories', t => {
  const { stock, payload, runtime } = fontFixture(t);
  const rule = '25-wqy-zenhei.conf';
  const content = '<fontconfig><alias><family>WenQuanYi Zen Hei</family></alias></fontconfig>';
  writeFileSync(join(payload, 'etc/fonts/conf.avail', rule), content);
  symlinkSync(`../conf.avail/${rule}`, join(payload, 'etc/fonts/conf.d', rule));
  const config = fontConfiguration(stock, payload, runtime);
  assert.deepEqual(readdirSync(join(runtime, 'conf.d')).sort(), ['10-hinting.conf', rule, '60-generic.conf']);
  assert.equal(readFileSync(join(runtime, 'conf.d', rule), 'utf8'), content);
  const generated = readFileSync(config, 'utf8');
  assert.match(generated, /<dir>\/usr\/share\/fonts<\/dir>/);
  assert.match(generated, /<cachedir prefix="xdg">fontconfig<\/cachedir>/);
  assert.ok(generated.includes(`<include>${join(runtime, 'conf.d')}</include>`));
  assert.ok(generated.includes(`<dir>${join(payload, 'usr/share/fonts')}</dir>`));
  assert.doesNotMatch(generated, /<reset-dirs|>conf\.d<\/include>/);
  assert.ok(generated.indexOf(join(runtime, 'font-cache')) < generated.indexOf('prefix="xdg"'));
});

test('absolute Debian conf.d links resolve to package paths instead of host paths', t => {
  const { stock, payload, runtime } = fontFixture(t);
  const rule = '65-ipafont.conf';
  mkdirSync(join(payload, 'usr/share/fontconfig/conf.avail'), { recursive: true });
  writeFileSync(join(payload, 'usr/share/fontconfig/conf.avail', rule), '<fontconfig/>');
  symlinkSync(`/usr/share/fontconfig/conf.avail/${rule}`, join(payload, 'etc/fonts/conf.d', rule));
  fontConfiguration(stock, payload, runtime);
  assert.equal(readFileSync(join(runtime, 'conf.d', rule), 'utf8'), '<fontconfig/>');
});

test('unrecognized master config or conflicting stock rule cannot alter font preference silently', t => {
  const { stock, payload, runtime } = fontFixture(t);
  writeFileSync(join(payload, 'etc/fonts/conf.d/60-generic.conf'), '<fontconfig>different</fontconfig>');
  assert.throws(() => fontConfiguration(stock, payload, runtime), /Conflicting/);
  rmSync(join(payload, 'etc/fonts/conf.d/60-generic.conf'));
  rmSync(runtime, { recursive: true });
  writeFileSync(join(stock, 'fonts.conf'), '<fontconfig><include>new-layout</include></fontconfig>');
  assert.throws(() => fontConfiguration(stock, payload, runtime), /Unknown stock/);
});

test('font rule links escaping extraction are rejected without touching a live peer', t => {
  const { stock, payload, runtime } = fontFixture(t);
  const peer = join(stock, 'peer.conf'); writeFileSync(peer, 'peer unchanged');
  symlinkSync('../../../../stock/peer.conf', join(payload, 'etc/fonts/conf.d/25-escape.conf'));
  assert.throws(() => fontConfiguration(stock, payload, runtime), /escapes extraction/);
  assert.equal(readFileSync(peer, 'utf8'), 'peer unchanged');
});

test('every outline font asset, TTC face and existing stock face must remain visible', t => {
  const path = fixture(t); mkdirSync(join(path, 'nested'));
  for (const name of ['normal.ttf', 'normal.otf', 'collection.ttc', 'bitmap.pcf.gz', 'type1.pfb', 'README']) writeFileSync(join(path, 'nested', name), 'asset');
  assert.deepEqual(outlineFonts(path).map(name => name.split('/').at(-1)).sort(), ['collection.ttc', 'normal.otf', 'normal.ttf', 'type1.pfb']);
  const stock = '/stock/noto.ttf\t0\tNoto\tRegular\n';
  const expected = '/new/wqy.ttc\t0\tWQY\tRegular\n/new/wqy.ttc\t1\tWQY Mono\tRegular\n';
  assert.doesNotThrow(() => verifyFaces(stock, expected, expected + stock));
  assert.throws(() => verifyFaces(stock, expected, expected), /lost a font face/);
  assert.throws(() => verifyFaces(stock, expected, stock + expected.split('\n')[0]), /lost a font face/);
  symlinkSync('/stock/peer.ttf', join(path, 'nested/alias.ttf'));
  assert.throws(() => outlineFonts(path), /Unexpected font payload link/);
});

test('workflow preserves engine, device lanes, one-worker scheduling, launcher and strict gates', () => {
  const workflow = readFileSync(join(root, '.github/workflows/ci.yml'), 'utf8');
  // Use full workflow for ordered commands; do not run a browser or application.
  assert.match(workflow, /project: \[desktop-chromium, android-chromium\]/);
  assert.match(workflow, /shard: \[1, 2\]/);
  assert.match(workflow, /run: node scripts\/ci\/browser-dependencies.mjs plan/);
  assert.match(workflow, /uses: actions\/cache@v4/);
  assert.match(workflow, /key: \$\{\{ steps.browser-dependencies.outputs.cache_key \}\}/);
  assert.doesNotMatch(workflow, /restore-keys:|playwright install --with-deps|SKIP_VALIDATE_HOST/);
  assert.ok(workflow.indexOf('browser-dependencies.mjs plan') < workflow.indexOf('browser-dependencies.mjs prepare'));
  assert.ok(workflow.indexOf('browser-dependencies.mjs prepare') < workflow.indexOf('run: ./start.sh'));
  assert.match(workflow, /run: node scripts\/ci\/run-browser-shard.mjs --project=\$\{\{ matrix.project \}\} --shard=\$\{\{ matrix.shard \}\}\/2/);
  assert.match(workflow, /run: node --test tests\/browser-infrastructure\/shards.test.mjs/);
});

test('browser CLI uses the installed public package bin rather than an unexported subpath', () => {
  assert.equal(playwrightCLI('/work/node_modules/playwright/package.json', { bin: { playwright: 'cli.js' } }), '/work/node_modules/playwright/cli.js');
  for (const metadata of [{}, { bin: '/host/cli.js' }, { bin: { playwright: '../private.js' } }]) assert.throws(() => playwrightCLI('/work/package.json', metadata), /public CLI entry/);
});
