import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { chmod, lstat, mkdir, open, readdir, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const digest = /^[0-9a-f]{64}$/;
const MB = 1024 * 1024;
const npmDir = 'npm-cache/_cacache/content-v2/sha512';
const lockFiles = ['ai/prototype/package-lock.json', 'constraints.docker.txt', 'pyproject.toml'];
const composeFile = 'compose.test.yaml';
const composeLimit = 128 * 1024;

function committedFile(root, commit, file, maxBuffer = MB) {
  if (!/^[0-9a-f]{40}$/.test(commit)) throw new Error('Invalid offline build revision.');
  return execFileSync('git', ['--no-replace-objects', '-C', root, 'cat-file', 'blob', `${commit}:${file}`],
    {maxBuffer, stdio:['ignore','pipe','ignore']});
}

async function committedCompose(root, commit) {
  const bytes = committedFile(root, commit, composeFile, composeLimit);
  const working = await open(path.join(root, composeFile), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await working.stat();
    const buffer = Buffer.alloc(bytes.length + 1);
    if (!info.isFile() || info.size !== bytes.length ||
        (await working.read(buffer, 0, buffer.length, 0)).bytesRead !== bytes.length ||
        !bytes.equals(buffer.subarray(0, bytes.length))) {
      throw new Error('Working Compose definition differs from the bound commit.');
    }
    return bytes;
  } finally { await working.close(); }
}

function buildOverlay(stage) {
  const context = path.join(stage, 'context').replaceAll('$', () => '$$');
  const build = {context, network:'none', pull:false, args:{SCHEMII_BUILD_MODE:'offline'}};
  return Buffer.from(JSON.stringify({services:{schemii:{build},'ai-prototype-runtime':{build}}}));
}

export async function verifyOfflineCompose(root, stage, commit, readPrivateFile, phase) {
  await privateDirectory(stage);
  const revision = (await readPrivateFile(path.join(stage, 'revision'), 41)).toString().trim();
  const head = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim();
  if (head !== commit || revision !== commit ||
      !(await committedCompose(root, commit)).equals(await readPrivateFile(path.join(stage, composeFile), composeLimit)) ||
      (phase === 'build' && !buildOverlay(stage).equals(await readPrivateFile(path.join(stage, 'compose.json'), 4096)))) {
    throw new Error('Staged Compose inputs differ from the bound guest build.');
  }
}

function sameKeys(record, keys) {
  return record && typeof record === 'object' && !Array.isArray(record) &&
    Object.keys(record).length === keys.length && keys.every(key => Object.hasOwn(record, key));
}

export function buildInputBinding(value) {
  if (!sameKeys(value, ['manifest', 'sha256']) || typeof value.manifest !== 'string' ||
      !path.isAbsolute(value.manifest) || value.manifest !== path.normalize(value.manifest) ||
      typeof value.sha256 !== 'string' || !digest.test(value.sha256)) {
    throw new Error('Offline build requires a private manifest path and SHA-256.');
  }
  return {manifest: value.manifest, sha256: value.sha256};
}

async function privateDirectory(directory) {
  const info = await lstat(directory);
  if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o077) ||
      await realpath(directory) !== directory) {
    throw new Error('Offline build inputs require a canonical private owner-controlled directory.');
  }
}

async function listMembers(directory, prefix = '') {
  await privateDirectory(directory);
  const entries = await readdir(directory, {withFileTypes:true});
  if (entries.length > 256 || prefix.split('/').length > 7) {
    throw new Error('Offline build input directory exceeds its inventory limit.');
  }
  const members = [];
  for (const entry of entries) {
    const relative = prefix + entry.name;
    if (entry.isFile()) members.push(relative);
    else if (entry.isDirectory()) members.push(...await listMembers(path.join(directory, entry.name), relative + '/'));
    else throw new Error('Offline build inputs must be regular files and private directories.');
    if (members.length > 128) throw new Error('Offline build input inventory is too large.');
  }
  return members;
}

function npmDigests(lock) {
  const packages = Object.entries(lock.packages ?? {}).filter(([name]) => name);
  if (lock.lockfileVersion !== 3 || !packages.length) throw new Error('Unsupported offline npm lock.');
  return new Map(packages.map(([, info]) => {
    if (info.link || info.dev || info.optional || info.os || info.cpu || info.libc ||
        !/^https:\/\/registry\.npmjs\.org\/[^\s]+$/.test(info.resolved ?? '') ||
        !/^sha512-[A-Za-z0-9+/]{86}==$/.test(info.integrity ?? '')) {
      throw new Error('Unsupported offline npm package in the tracked lock.');
    }
    const hash = Buffer.from(info.integrity.slice(7), 'base64').toString('hex');
    return [`${npmDir}/${hash.slice(0, 2)}/${hash.slice(2, 4)}/${hash.slice(4)}`, hash];
  }));
}

const normalize = name => name.toLowerCase().replace(/[-_.]+/g, '-');

function acceptWheel(member, versions, seen) {
  const match = /^wheelhouse\/([a-zA-Z0-9_]+)-([0-9]+(?:\.[0-9]+)+)-(py3-none-any|(?:cp312-cp312|cp3(?:[89]|1[012])-abi3)-manylinux[0-9_a-z.]+_x86_64)\.whl$/.exec(member);
  if (!match) throw new Error('Unsupported offline wheel filename or platform.');
  const [, distribution, version, tag] = match;
  const name = normalize(distribution);
  const approved = name === 'setuptools' ? /^8[0-4]\./.test(version) && tag === 'py3-none-any' :
    versions.get(name) === version;
  if (!approved || seen.has(name)) throw new Error('Offline wheel inventory differs from the tracked constraints.');
  seen.add(name);
}

export async function stageOfflineBuild(root, commit, binding, stage, readPrivateFile) {
  const approval = buildInputBinding(binding);
  const directory = path.dirname(approval.manifest);
  if (directory === root || directory.startsWith(root + path.sep) || path.basename(approval.manifest) !== 'manifest.json') {
    throw new Error('Offline build manifest must be in a private bundle outside the checkout.');
  }
  await privateDirectory(stage);
  await privateDirectory(directory);
  const manifestBytes = await readPrivateFile(approval.manifest, 64 * 1024);
  if (sha256(manifestBytes) !== approval.sha256) throw new Error('Offline build manifest differs from its approved digest.');
  const manifest = JSON.parse(manifestBytes);
  if (!sameKeys(manifest, ['version', 'locks', 'images', 'members']) || manifest.version !== 1 ||
      !sameKeys(manifest.locks, lockFiles) || !Array.isArray(manifest.members) ||
      !manifest.members.length || manifest.members.length > 127) {
    throw new Error('Unsupported offline build manifest.');
  }
  const compose = await committedCompose(root, commit);
  const lockBytes = lockFiles.map(file => committedFile(root, commit, file));
  if (!lockFiles.every((file, index) => manifest.locks[file] === sha256(lockBytes[index]))) {
    throw new Error('Offline build manifest does not match the tracked dependency locks.');
  }
  const contract = JSON.parse(committedFile(root, commit, 'dev/runtime-contract.json'));
  const imageKeys = ['node', 'python', 'postgres', 'ingress'];
  if (!sameKeys(manifest.images, imageKeys) || !imageKeys.every(key =>
    /^[a-z0-9.]+:[a-z0-9.-]+@sha256:[0-9a-f]{64}$/.test(contract.images[key]) &&
    manifest.images[key] === contract.images[key])) {
    throw new Error('Offline build image inventory does not match the tracked image pins.');
  }
  const dockerfiles = ['Dockerfile', 'ai/prototype/Dockerfile'].map(file => committedFile(root, commit, file).toString());
  const sources = dockerfiles.map(text => [...text.matchAll(/^FROM\s+(\S+)/gm)].map(match => match[1]));
  const serviceImages = [...compose.toString().matchAll(/^\s+image:\s+(\S+)/gm)].map(match => match[1]);
  const localImages = ['schemii-test-application', 'schemii-test-ai-prototype'];
  if (sources[0].length !== 2 || sources[0].some(ref => ref !== manifest.images.python) ||
      sources[1].length !== 1 || sources[1][0] !== manifest.images.node ||
      !['postgres','ingress'].every(key => serviceImages.includes(manifest.images[key])) ||
      serviceImages.some(ref => ![...localImages, manifest.images.postgres, manifest.images.ingress].includes(ref))) {
    throw new Error('Offline build image pins disagree with the tracked build definitions.');
  }
  const npm = npmDigests(JSON.parse(lockBytes[0]));
  const versions = new Map(lockBytes[1].toString().split('\n').map(line => line.trim())
    .filter(line => line && !line.startsWith('#')).map(line => {
      const match = /^([a-z0-9_.-]+)==([0-9]+(?:\.[0-9]+)+)$/.exec(line);
      if (!match) throw new Error('Unsupported tracked offline Python constraint.');
      return [normalize(match[1]), match[2]];
    }));
  if (!/^requires = \["setuptools>=80,<85"\]$/m.test(lockBytes[2].toString())) {
    throw new Error('Unsupported tracked offline Python build backend.');
  }
  const expected = new Set(['manifest.json']);
  const wheels = new Set();
  let total = 0;
  for (const member of manifest.members) {
    if (!sameKeys(member, ['path', 'bytes', 'sha256']) || typeof member.path !== 'string' ||
        expected.has(member.path) || typeof member.sha256 !== 'string' || !digest.test(member.sha256) ||
        !Number.isSafeInteger(member.bytes) || member.bytes < 4 || member.bytes > 32 * MB) {
      throw new Error('Invalid or duplicate offline build member.');
    }
    total += member.bytes;
    if (total > 128 * MB) throw new Error('Offline build inputs exceed their size budget.');
    if (!npm.has(member.path)) acceptWheel(member.path, versions, wheels);
    expected.add(member.path);
  }
  if (wheels.size !== versions.size + 1 || ![...npm.keys()].every(member => expected.has(member))) {
    throw new Error('Offline build bundle is missing a locked package.');
  }
  const actual = await listMembers(directory);
  if (actual.length !== expected.size || actual.some(member => !expected.has(member))) {
    throw new Error('Offline build bundle has missing or additional files.');
  }

  const context = path.join(stage, 'context');
  const input = path.join(context, 'qa-build-inputs');
  await mkdir(context, {mode:0o700});
  // Archive only Git content, excluding the guest's local secrets and runtime state.
  const archive = execFileSync('git', ['--no-replace-objects', '-C', root, '-c', 'tar.umask=0022', 'archive', '--format=tar', commit],
    {maxBuffer:64 * MB, stdio:['ignore','pipe','ignore']});
  execFileSync('tar', ['--no-same-owner','--same-permissions','-xf','-','-C',context],
    {input:archive, stdio:['pipe','ignore','ignore']});
  await mkdir(input, {mode:0o700});
  for (const member of manifest.members) {
    const source = path.join(directory, member.path);
    if (await realpath(source) !== source) throw new Error('Offline build member path changed.');
    const bytes = await readPrivateFile(source, member.bytes);
    const npmHash = npm.get(member.path);
    if (bytes.length !== member.bytes || sha256(bytes) !== member.sha256 ||
        (npmHash ? createHash('sha512').update(bytes).digest('hex') !== npmHash :
          !bytes.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04])))) {
      throw new Error('Offline build member bytes do not match the approved package.');
    }
    const file = path.join(input, member.path);
    await mkdir(path.dirname(file), {recursive:true, mode:0o700});
    await writeFile(file, bytes, {flag:'wx', mode:0o444});
    await chmod(file, 0o444);
  }
  await writeFile(path.join(stage, composeFile), compose, {flag:'wx', mode:0o400});
  await writeFile(path.join(stage, 'compose.json'), buildOverlay(stage), {flag:'wx', mode:0o400});
  await writeFile(path.join(stage, 'revision'), commit + '\n', {flag:'wx', mode:0o400});
}

export function verifyOfflineBuildConfig(config, stage, commit) {
  for (const [name, dockerfile] of [['schemii', 'Dockerfile'], ['ai-prototype-runtime', 'ai/prototype/Dockerfile']]) {
    const build = config?.services?.[name]?.build;
    if (!build || build.context !== path.join(stage, 'context') || build.dockerfile !== dockerfile ||
        build.network !== 'none' || (build.pull ?? false) !== false ||
        !sameKeys(build.args, ['SCHEMII_BUILD_MODE', 'SCHEMII_SOURCE_COMMIT']) ||
        build.args.SCHEMII_BUILD_MODE !== 'offline' || build.args.SCHEMII_SOURCE_COMMIT !== commit ||
        Object.keys(build).some(key => !['context','dockerfile','network','pull','args'].includes(key))) {
      throw new Error('Effective Compose build does not match the approved offline configuration.');
    }
  }
}
