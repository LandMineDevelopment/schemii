import { createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, open, readFile, writeFile } from 'node:fs/promises';
import { lstatSync, readFileSync, readlinkSync } from 'node:fs';
import https from 'node:https';
import path from 'node:path';

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

function checkHTTPS(url, allowSelfSigned, redirects = 0, allowAuthChallenge = false) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    if (target.protocol !== 'https:') return reject(new Error(`HTTPS is required: ${url}`));
    const request = https.get(target, { rejectUnauthorized: !allowSelfSigned, timeout: 20000 }, response => {
      response.resume();
      const status = response.statusCode;
      if (status >= 300 && status < 400 && response.headers.location) {
        const next = new URL(response.headers.location, target);
        if (next.origin !== target.origin || redirects >= 4) {
          reject(new Error(`Unexpected redirect from ${url}`));
          return;
        }
        checkHTTPS(next.href, allowSelfSigned, redirects + 1, allowAuthChallenge).then(result => resolve({ ...result, url }), reject);
      } else if ((status >= 200 && status < 300) || (allowAuthChallenge && status === 401)) {
        resolve({ url, status, authenticationRequired: status === 401, checkedAt: new Date().toISOString() });
      } else reject(new Error(`HTTPS verification failed for ${url}: HTTP ${status}`));
    });
    request.on('timeout', () => request.destroy(new Error(`HTTPS verification timed out for ${url}`)));
    request.on('error', reject);
  });
}

async function launcherFailure(logPath, code, signal) {
  const log = await readFile(logPath, 'utf8');
  // Prefer the launcher's explicit diagnosis over service logs, which can contain
  // application data. Full output remains private in the run directory.
  const exact = log.split('\n').filter(line => line.startsWith('Schemii startup error:')).at(-1);
  return new Error(exact || `./start.sh failed (${signal ? `signal ${signal}` : `exit ${code}`}); see ${logPath}`);
}

export async function startDeployment({
  root, runDir,
  localURL = 'https://localhost:8001',
  previewURL = 'https://omarchy.taile4f57f.ts.net',
}) {
  if (localURL !== 'https://localhost:8001' || previewURL !== 'https://omarchy.taile4f57f.ts.net') {
    throw new Error('QA deployment verification requires the canonical local and Tailscale HTTPS origins');
  }
  const lockPath = deploymentLockPath(root);
  if (process.env.SCHEMII_QA_LEASE_FD !== '3' || readlinkSync('/proc/self/fd/3') !== lockPath) {
    throw new Error('startDeployment requires the coordinator deployment lease on inherited descriptor 3');
  }
  await mkdir(runDir, { recursive: true, mode: 0o700 });
  const before = sourceIdentity(root);
  const logPath = path.join(runDir, 'start.log');
  const log = await open(logPath, 'w', 0o600);
  let result;
  try {
    result = await new Promise((resolve, reject) => {
      const child = spawn('./start.sh', [], {
        cwd: root,
        env: { ...process.env, SCHEMII_QA_LEASE_FD: '3' },
        stdio: ['ignore', log.fd, log.fd, 3],
      });
      child.once('error', reject);
      child.once('close', (code, signal) => resolve({ code, signal }));
    });
  } finally { await log.close(); }
  if (result.code !== 0) throw await launcherFailure(logPath, result.code, result.signal);

  const endpoints = await Promise.all([
    checkHTTPS(localURL, true),
    checkHTTPS(`${localURL}/api-map`, true, 0, true),
    checkHTTPS(previewURL, false),
    checkHTTPS(`${previewURL}/api-map`, false, 0, true),
  ]);
  const after = sourceIdentity(root);
  if (before.commit !== after.commit || before.fingerprint !== after.fingerprint) {
    throw new Error('Source changed during deployment; finish the run and prepare again before assigning testers');
  }
  const deployment = { identity: after, endpoints, logPath, verifiedAt: new Date().toISOString() };
  await writeFile(path.join(runDir, 'deployment.json'), `${JSON.stringify(deployment, null, 2)}\n`, { mode: 0o600 });
  return deployment;
}
