import { createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, open, readFile, writeFile } from 'node:fs/promises';
import { lstatSync, readFileSync, readlinkSync, closeSync } from 'node:fs';
import https from 'node:https';
import { BlockList, isIP } from 'node:net';
import path from 'node:path';
import { writeJSON } from './store.mjs';
import { readPrivateJSON, processIdentity, cgroupIdentity } from '../load/runtime.mjs';

export const LOCAL_ORIGIN = 'https://localhost:8001';
export const DESKTOP_PREVIEW = 'https://omarchy.taile4f57f.ts.net';

export function disposablePreviewBinding(value) {
  const { origin, nodeId, bootId, commit } = value ?? {};
  const label = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
  const testOrigin = new RegExp(`^https://${label}\\.${label}\\.ts\\.net$`);
  if (Object.keys(value ?? {}).length !== 4 || typeof origin !== 'string' || !testOrigin.test(origin) ||
      origin.endsWith('.taile4f57f.ts.net') || typeof nodeId !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(nodeId) ||
      typeof bootId !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(bootId) ||
      typeof commit !== 'string' || !/^[a-f0-9]{40}$/.test(commit)) {
    throw new Error('Disposable preview requires an exact test-tailnet HTTPS origin, node ID, guest boot ID and clean commit.');
  }
  return { origin, nodeId, bootId, commit };
}

const tailnetAddresses = new BlockList();
tailnetAddresses.addSubnet('100.64.0.0', 10);
tailnetAddresses.addSubnet('fd7a:115c:a1e0::', 48, 'ipv6');

function addressKey(address) {
  if (typeof address !== 'string') return null;
  const mapped = address.replace(/^::ffff:(?=\d+\.)/i, '');
  if (isIP(mapped) === 4) return mapped;
  if (isIP(mapped) === 6) return new URL(`https://[${mapped}]`).hostname.slice(1, -1);
  return null;
}

export function disposableRoute(binding, status, serve) {
  const hostname = new URL(binding.origin).hostname;
  const hostPort = `${hostname}:443`;
  const self = status?.Self;
  const addresses = Array.isArray(self?.TailscaleIPs) ? self.TailscaleIPs.map(addressKey).sort() : [];
  const handlers = serve?.Web?.[hostPort]?.Handlers;
  const target = handlers?.['/']?.Proxy;
  if (status?.BackendState !== 'Running' || self?.ID !== binding.nodeId ||
      self?.DNSName !== `${hostname}.` || !addresses.length ||
      !addresses.every(address => address && tailnetAddresses.check(address, isIP(address) === 4 ? 'ipv4' : 'ipv6')) ||
      serve?.TCP?.['443']?.HTTPS !== true || Object.keys(handlers ?? {}).length !== 1 ||
      target !== 'https+insecure://localhost:8001' || Object.values(serve?.AllowFunnel ?? {}).some(Boolean) || serve?.Foreground) {
    throw new Error('Disposable preview must use the bound guest node and its persistent, tailnet-only Serve proxy to canonical localhost.');
  }
  return { nodeId: self.ID, addresses, target };
}

function observeDisposableRoute(binding) {
  function query(args) {
    try {
      return JSON.parse(execFileSync('tailscale', ['--socket=/var/run/tailscale/tailscaled.sock', ...args], {
        encoding: 'utf8', timeout: 5000, maxBuffer: 4 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'],
      }));
    } catch {
      throw new Error('Unable to inspect the disposable guest’s local Tailscale daemon.');
    }
  }
  return disposableRoute(binding, query(['status', '--json']), query(['serve', 'status', '--json']));
}

export function assertDisposableSource(binding, identity, bootId) {
  if (identity.dirty || identity.commit !== binding.commit || bootId !== binding.bootId) {
    throw new Error('Disposable preview binding does not match this boot and clean checkout.');
  }
}

async function verifyRuntime(runtime, binding) {
  const required = ['schemii', 'ingress', 'metadata-postgres', 'demo-postgres'];
  if (runtime?.version !== 1 || runtime.bootId !== binding.bootId || runtime.revision !== binding.commit ||
      runtime.origin !== LOCAL_ORIGIN || !required.every(service => runtime.containers?.some(c => c.service === service))) {
    throw new Error('Disposable preview lacks an exact launcher runtime receipt for this boot and source.');
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

export function checkHTTPS(url, { allowSelfSigned = false, ca, addresses, allowAuthChallenge = false } = {}, redirects = 0) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    if (target.protocol !== 'https:') return reject(new Error('HTTPS is required for deployment verification.'));
    const request = https.get(target, { rejectUnauthorized: !allowSelfSigned, ca, agent: false, timeout: 20000 }, response => {
      const address = addressKey(response.socket.remoteAddress);
      response.resume();
      if (addresses && !addresses.includes(address)) {
        reject(new Error('Preview HTTPS connected to an address outside the bound guest node.'));
        return;
      }
      const status = response.statusCode;
      if (status >= 300 && status < 400 && response.headers.location) {
        let next;
        try { next = new URL(response.headers.location, target); }
        catch { return reject(new Error('Invalid redirect during deployment HTTPS verification.')); }
        if (next.origin !== target.origin || next.username || next.password || redirects >= 4) {
          reject(new Error('Unexpected redirect during deployment HTTPS verification.'));
          return;
        }
        checkHTTPS(next.href, { allowSelfSigned, ca, addresses, allowAuthChallenge }, redirects + 1)
          .then(result => resolve({ ...result, url }), reject);
      } else if ((status >= 200 && status < 300) || (allowAuthChallenge && status === 401)) {
        resolve({ url, status, authenticationRequired: status === 401, checkedAt: new Date().toISOString(),
          ...(addresses ? { address } : {}) });
      } else reject(new Error(`Deployment HTTPS verification failed: HTTP ${status}.`));
    });
    request.on('timeout', () => request.destroy(new Error('Deployment HTTPS verification timed out.')));
    request.on('error', reject);
  });
}

export function assertDeploymentLease(root) {
  const lockPath = deploymentLockPath(root);
  const gatePath = path.join(path.dirname(lockPath), 'qa-startup.lock');
  const mode = process.env.SCHEMII_QA_LEASE_MODE;
  function held(fd, file, access) {
    try {
      return readlinkSync(`/proc/self/fd/${fd}`) === file &&
        new RegExp(`^lock:.*FLOCK +ADVISORY +${access}\\s`, 'm').test(readFileSync(`/proc/self/fdinfo/${fd}`, 'utf8'));
    } catch { return false; }
  }
  if (!['exclusive','shared'].includes(mode) || process.env.SCHEMII_QA_LEASE_FD !== '3' ||
      !held(3, lockPath, mode === 'exclusive' ? 'WRITE' : 'READ') || !held(4, gatePath, 'WRITE')) {
    throw new Error('Deployment verification requires the held coordinator lease and startup gate.');
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

export async function startDeployment({
  root, runDir, localURL = LOCAL_ORIGIN, previewURL = DESKTOP_PREVIEW, previewBinding,
}) {
  const binding = previewBinding === undefined ? undefined : disposablePreviewBinding(previewBinding);
  if (localURL !== LOCAL_ORIGIN || previewURL !== (binding?.origin ?? DESKTOP_PREVIEW)) {
    throw new Error('QA verification requires canonical localhost and the declared preview origin.');
  }
  const lockPath = assertDeploymentLease(root);
  try {
    await mkdir(runDir, { recursive: true, mode: 0o700 });
    const before = sourceIdentity(root);
    const bootId = binding && (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
    if (binding) assertDisposableSource(binding, before, bootId);
    const route = binding && observeDisposableRoute(binding);
    let runtime;
    let logPath;
    const ledgerPath = path.join(path.dirname(lockPath), 'qa-deployment.json');
    const reused = process.env.SCHEMII_QA_LEASE_MODE === 'shared';
    if (reused) {
      let prior;
      try { prior = JSON.parse(await readFile(ledgerPath, 'utf8')); }
      catch { throw new Error('The active deployment has no readable QA identity; stop its owner and prepare again.'); }
      if (prior.identity?.fingerprint !== before.fingerprint || prior.identity?.commit !== before.commit) {
        throw new Error('Another run holds a different source deployment; finish it before rebuilding this checkout.');
      }
      if (JSON.stringify(prior.previewBinding) !== JSON.stringify(binding)) {
        throw new Error('The shared lease belongs to a different preview binding.');
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
      if (binding) runtime = await readPrivateJSON(runtimePath);
    }

    if (binding) await verifyRuntime(runtime, binding);
    const localTrust = binding
      ? { ca: await readFile(path.resolve(root, process.env.SCHEMII_TLS_DIRECTORY || '.schemii/tls', 'localhost.crt')) }
      : { allowSelfSigned: true };
    const remoteTrust = binding ? { addresses: route.addresses } : {};
    const endpoints = await Promise.all([
      checkHTTPS(localURL, localTrust),
      checkHTTPS(`${localURL}/api-map`, { ...localTrust, allowAuthChallenge: true }),
      checkHTTPS(previewURL, remoteTrust),
      checkHTTPS(`${previewURL}/api-map`, { ...remoteTrust, allowAuthChallenge: true }),
    ]);
    if (binding) {
      if (JSON.stringify(observeDisposableRoute(binding)) !== JSON.stringify(route)) {
        throw new Error('Disposable Serve route changed during HTTPS verification.');
      }
      await verifyRuntime(runtime, binding);
    }
    const after = sourceIdentity(root);
    if (before.commit !== after.commit || before.fingerprint !== after.fingerprint) {
      throw new Error('Source changed during deployment; finish the run and prepare again before assigning testers');
    }
    const deployment = { identity: after, endpoints, ...(reused ? {} : {logPath}),
      ...(binding ? { previewBinding: binding, route, runtime } : {}), reused, verifiedAt: new Date().toISOString() };
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
