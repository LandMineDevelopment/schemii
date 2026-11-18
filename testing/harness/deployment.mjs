import { createHash, X509Certificate } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, open, readFile, writeFile } from 'node:fs/promises';
import { lstatSync, readFileSync, readlinkSync, closeSync, constants } from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import { writeJSON } from './store.mjs';
import { processIdentity, cgroupIdentity } from '../load/runtime.mjs';

export const LOCAL_ORIGIN = 'https://localhost:8001';
export const DESKTOP_PREVIEW = 'https://omarchy.taile4f57f.ts.net';

export async function readPrivateDeploymentJSON(file, maximum = 1024 * 1024) {
  const acceptable = stat => stat.isFile() && stat.uid === process.getuid() &&
    !(stat.mode & 0o077) && stat.size <= maximum;
  const entry = lstatSync(file);
  if (!acceptable(entry)) throw new Error('Deployment input must be a bounded private regular file.');
  const descriptor = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await descriptor.stat();
    if (!acceptable(info) || info.dev !== entry.dev || info.ino !== entry.ino) {
      throw new Error('Deployment input changed or is not a bounded private regular file.');
    }
    const data = Buffer.alloc(maximum + 1);
    let offset = 0;
    while (offset < data.length) {
      const { bytesRead } = await descriptor.read(data, offset, data.length - offset, offset);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    if (offset > maximum) throw new Error('Deployment input exceeds its size limit.');
    return JSON.parse(data.toString('utf8', 0, offset));
  } finally { await descriptor.close(); }
}

export function disposableGuestBinding(value) {
  const { bootId, commit, certificateSha256 } = value ?? {};
  if (Object.keys(value ?? {}).length !== 3 ||
      typeof bootId !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(bootId) ||
      typeof commit !== 'string' || !/^[a-f0-9]{40}$/.test(commit) ||
      typeof certificateSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(certificateSha256)) {
    throw new Error('Disposable guest binding requires a VM boot ID, clean commit and exact certificate SHA-256.');
  }
  return { bootId, commit, certificateSha256 };
}

export function assertDisposableSource(binding, identity, bootId, virtualMachine) {
  if (!virtualMachine || identity.dirty || identity.commit !== binding.commit || bootId !== binding.bootId) {
    throw new Error('Disposable QA requires the bound VM boot and clean checkout.');
  }
}

function checkDisposableConfiguration(root) {
  if (['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'COMPOSE_ENV_FILES', 'SCHEMII_TLS_DIRECTORY']
      .some(key => process.env[key]) || ![undefined, '8001'].includes(process.env.SCHEMII_TEST_APP_PORT)) {
    throw new Error('Disposable QA requires the guest engine and canonical ingress configuration.');
  }
  for (const file of ['.env', '.schemii/compose.local.yaml']) {
    try { lstatSync(path.join(root, file)); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    throw new Error('Disposable QA refuses an unverified machine-local Compose override.');
  }
}

async function verifyGuestSource(root, binding, identity) {
  let virtualMachine = false;
  try { virtualMachine = Boolean(execFileSync('systemd-detect-virt', ['--vm'], {
    encoding: 'utf8', timeout: 3000, stdio: ['ignore','pipe','ignore'],
  }).trim()); } catch {}
  const bootId = (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
  assertDisposableSource(binding, identity, bootId, virtualMachine);
  checkDisposableConfiguration(root);
}

async function verifyRuntime(runtime, binding) {
  const required = ['schemii', 'ingress', 'metadata-postgres', 'demo-postgres'];
  if (runtime?.version !== 1 || runtime.bootId !== binding.bootId || runtime.revision !== binding.commit ||
      runtime.origin !== LOCAL_ORIGIN || !required.every(service => runtime.containers?.some(c => c.service === service))) {
    throw new Error('Disposable QA lacks an exact launcher runtime receipt for this boot and source.');
  }
  try {
    for (const { rootProcess, cgroup } of runtime.containers) {
      const current = await processIdentity(rootProcess.pid);
      const group = await cgroupIdentity(current.pid);
      if (current.birthTick !== rootProcess.birthTick || group.path !== cgroup.path ||
          group.device !== cgroup.device || group.inode !== cgroup.inode) throw new Error();
    }
  } catch {
    throw new Error('The disposable launcher runtime changed during preview verification.');
  }
}

function git(root, args) {
  return execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
}

export function deploymentLockPath(root) {
  return path.join(git(root, ['rev-parse', '--path-format=absolute', '--git-common-dir']).trim(), 'qa-deployment.lock');
}

// Hash the working source, including uncommitted changes and nonignored new files.
// No file content or secret value is included in the returned identity.
export function sourceIdentity(root) {
  const files = [...new Set(git(root, ['ls-files', '-z', '--cached', '--others', '--exclude-standard']).split('\0').filter(Boolean))].sort();
  const hash = createHash('sha256');
  for (const file of files) {
    hash.update(file).update('\0');
    let stat;
    try { stat = lstatSync(path.join(root, file)); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      hash.update('deleted\0');
      continue;
    }
    hash.update(`${stat.mode}\0`);
    if (stat.isSymbolicLink()) hash.update(readlinkSync(path.join(root, file)));
    else if (stat.isFile()) hash.update(readFileSync(path.join(root, file)));
    hash.update('\0');
  }
  return {
    commit: git(root, ['rev-parse', 'HEAD']).trim(),
    dirty: git(root, ['status', '--porcelain']).length > 0,
    fingerprint: hash.digest('hex'),
  };
}

export function checkHTTPS(url, { allowSelfSigned = false, ca, requestIdentity = false, allowAuthChallenge = false } = {}, redirects = 0) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    if (target.protocol !== 'https:') return reject(new Error('HTTPS is required for deployment verification.'));
    const request = https.get(target, { rejectUnauthorized: !allowSelfSigned, ca, agent: false, timeout: 20000 }, response => {
      response.resume();
      const requestId = response.headers['x-request-id'];
      if (requestIdentity && (typeof requestId !== 'string' || !/^[a-f0-9]{32}$/.test(requestId))) {
        reject(new Error('Canonical HTTPS response lacks an ingress/application request ID.'));
        return;
      }
      const status = response.statusCode;
      if (status >= 300 && status < 400 && response.headers.location) {
        let next;
        try { next = new URL(response.headers.location, target); }
        catch { return reject(new Error('Invalid redirect during deployment HTTPS verification.')); }
        if (requestIdentity || next.origin !== target.origin || next.username || next.password || redirects >= 4) {
          reject(new Error('Unexpected redirect during deployment HTTPS verification.'));
          return;
        }
        checkHTTPS(next.href, { allowSelfSigned, ca, allowAuthChallenge }, redirects + 1)
          .then(result => resolve({ ...result, url }), reject);
      } else if ((status >= 200 && status < 300) || (allowAuthChallenge && status === 401)) {
        resolve({ url, status, authenticationRequired: status === 401, checkedAt: new Date().toISOString(),
          ...(requestIdentity ? { requestId } : {}) });
      } else reject(new Error(`Deployment HTTPS verification failed: HTTP ${status}.`));
    });
    request.on('timeout', () => request.destroy(new Error('Deployment HTTPS verification timed out.')));
    request.on('error', reject);
  });
}

function held(fd, file, access) {
  try {
    return readlinkSync(`/proc/self/fd/${fd}`) === file &&
      new RegExp(`^lock:.*FLOCK +ADVISORY +${access}\\s`, 'm').test(readFileSync(`/proc/self/fdinfo/${fd}`, 'utf8'));
  } catch { return false; }
}

function assertHeldDeployment(root, access) {
  const lockPath = deploymentLockPath(root);
  if (process.env.SCHEMII_QA_LEASE_FD !== '3' || !held(3, lockPath, access)) {
    throw new Error('Deployment verification requires the held coordinator lease.');
  }
  return lockPath;
}

export function assertDeploymentLease(root) {
  const mode = process.env.SCHEMII_QA_LEASE_MODE;
  if (!['exclusive','shared'].includes(mode)) throw new Error('Deployment verification requires the held coordinator lease.');
  const lockPath = assertHeldDeployment(root, mode === 'exclusive' ? 'WRITE' : 'READ');
  if (!held(4, path.join(path.dirname(lockPath), 'qa-startup.lock'), 'WRITE')) {
    throw new Error('Deployment verification requires the held startup gate.');
  }
  return lockPath;
}

async function launcherFailure(logPath, code, signal) {
  const log = await readFile(logPath, 'utf8');
  // Prefer the launcher's explicit diagnosis over service logs, which can contain
  // application data. Full output remains private in the run directory.
  const exact = log.split('\n').filter(line => line.startsWith('Schemii startup error:')).at(-1);
  return new Error(exact || `./start.sh failed (${signal ? `signal ${signal}` : `exit ${code}`}); see ${logPath}`);
}

function readIngressEvents(root) {
  const output = execFileSync('./start.sh', ['--logs','ingress'], {
    cwd:root, timeout:10000, maxBuffer:1024*1024, encoding:'utf8', stdio:['ignore','pipe','ignore'],
  });
  return output.split('\n').flatMap(line => {
    const start = line.indexOf('{');
    if (start < 0) return [];
    try {
      const event = JSON.parse(line.slice(start));
      return event?.event === 'ingress_request' ? [event] : [];
    } catch { return []; }
  });
}

export function correlateGuestRequests(endpoints, before, after) {
  const ids = new Set(endpoints.map(endpoint => endpoint.requestId));
  if (ids.size !== endpoints.length) throw new Error('Guest requests must have distinct ingress IDs.');
  return endpoints.map(({url,status,requestId}) => {
    const rows = after.filter(event => event.request_id === requestId);
    if (!/^[a-f0-9]{32}$/.test(requestId) ||
        before.some(event => event.request_id === requestId) || rows.length !== 1 ||
        rows[0].application_request_id !== requestId || rows[0].method !== 'GET' ||
        rows[0].path !== new URL(url).pathname || rows[0].status !== status) {
      throw new Error('Canonical response was not a fresh request through the owned ingress and application.');
    }
    return {requestId, path:rows[0].path, status};
  });
}

async function probeDisposableGuest(root, binding, runtime) {
  await verifyRuntime(runtime, binding);
  const ca = await readFile(path.join(root, '.schemii/tls/localhost.crt'));
  if (new X509Certificate(ca).fingerprint256.replaceAll(':','').toLowerCase() !== binding.certificateSha256) {
    throw new Error('Launcher certificate does not match the approved guest pin.');
  }
  const before = readIngressEvents(root);
  const local = {ca, requestIdentity:true};
  const endpoints = await Promise.all([
    checkHTTPS(LOCAL_ORIGIN, local),
    checkHTTPS(`${LOCAL_ORIGIN}/api-map`, {...local,allowAuthChallenge:true}),
  ]);
  const requests = correlateGuestRequests(endpoints, before, readIngressEvents(root));
  await verifyRuntime(runtime, binding);
  checkDisposableConfiguration(root);
  return { endpoints, requests, verifiedAt:new Date().toISOString() };
}

export async function revalidateDisposableGuest(root, deployment) {
  if (!deployment?.guestBinding) throw new Error('Disposable run lacks a verified guest deployment.');
  const binding = disposableGuestBinding(deployment.guestBinding);
  assertHeldDeployment(root, 'READ');
  const identity = sourceIdentity(root);
  await verifyGuestSource(root, binding, identity);
  if (identity.fingerprint !== deployment.identity?.fingerprint) {
    throw new Error('Disposable guest source has changed since preparation.');
  }
  return probeDisposableGuest(root, binding, deployment.runtime);
}

export async function startDeployment({
  root, runDir, localURL = LOCAL_ORIGIN, previewURL = DESKTOP_PREVIEW, guestBinding,
}) {
  const binding = guestBinding === undefined ? undefined : disposableGuestBinding(guestBinding);
  if (localURL !== LOCAL_ORIGIN || previewURL !== (binding ? null : DESKTOP_PREVIEW)) {
    throw new Error('QA verification requires canonical localhost and the declared preview origin.');
  }
  const lockPath = assertDeploymentLease(root);
  try {
    await mkdir(runDir, { recursive: true, mode: 0o700 });
    const before = sourceIdentity(root);
    if (binding) await verifyGuestSource(root, binding, before);
    let runtime;
    let logPath;
    const ledgerPath = path.join(path.dirname(lockPath), 'qa-deployment.json');
    const reused = process.env.SCHEMII_QA_LEASE_MODE === 'shared';
    if (reused) {
      let prior;
      try { prior = await readPrivateDeploymentJSON(ledgerPath); }
      catch { throw new Error('The active deployment has no readable QA identity; stop its owner and prepare again.'); }
      if (prior.identity?.fingerprint !== before.fingerprint || prior.identity?.commit !== before.commit) {
        throw new Error('Another run holds a different source deployment; finish it before rebuilding this checkout.');
      }
      if (JSON.stringify(prior.guestBinding) !== JSON.stringify(binding)) {
        throw new Error('The shared lease belongs to a different guest binding.');
      }
      runtime = prior.runtime;
    } else {
      const launchDir = binding ? await mkdtemp(path.join(runDir, 'launcher-')) : runDir;
      const runtimePath = path.join(launchDir, 'runtime.json');
      logPath = path.join(launchDir, 'start.log');
      const log = await open(logPath, 'w', 0o600);
      let result;
      try {
        result = await new Promise((resolve, reject) => {
          const child = spawn('./start.sh', [], {
            cwd: root,
            env: { ...process.env, SCHEMII_QA_LEASE_FD: '3', SCHEMII_QA_GATE_FD: '4',
              ...(binding ? { SCHEMII_RUNTIME_RECEIPT: runtimePath, SCHEMII_TEST_APP_PORT: '8001' } : {}) },
            stdio: ['ignore', log.fd, log.fd, 3, 4],
          });
          child.once('error', reject);
          child.once('close', (code, signal) => resolve({ code, signal }));
        });
      } finally { await log.close(); }
      if (result.code !== 0) throw await launcherFailure(logPath, result.code, result.signal);
      if (binding) runtime = await readPrivateDeploymentJSON(runtimePath);
    }

    const localVerification = binding && await probeDisposableGuest(root, binding, runtime);
    const endpoints = localVerification?.endpoints ?? await Promise.all([
      checkHTTPS(localURL, {allowSelfSigned:true}),
      checkHTTPS(`${localURL}/api-map`, {allowSelfSigned:true,allowAuthChallenge:true}),
      checkHTTPS(previewURL),
      checkHTTPS(`${previewURL}/api-map`, {allowAuthChallenge:true}),
    ]);
    const after = sourceIdentity(root);
    if (before.commit !== after.commit || before.fingerprint !== after.fingerprint) {
      throw new Error('Source changed during deployment; finish the run and prepare again before assigning testers');
    }
    const deployment = { identity: after, endpoints, ...(reused ? {} : {logPath}),
      ...(binding ? { guestBinding: binding, localVerification, runtime, remotePreview:'not-tested' } : {}), reused, verifiedAt: new Date().toISOString() };
    if (!reused) await writeJSON(ledgerPath, deployment);
    await writeFile(path.join(runDir, 'deployment.json'), `${JSON.stringify(deployment, null, 2)}\n`, { mode: 0o600 });
    // Gate prevents a concurrent launcher taking the lock during EX -> SH conversion.
    execFileSync('flock', ['-s', '3'], { stdio: ['ignore','pipe','pipe',3] });
    return deployment;
  } finally {
    try { execFileSync('flock', ['-u', '4'], { stdio: ['ignore','pipe','pipe','ignore',4] }); }
    finally { closeSync(4); }
  }
}
