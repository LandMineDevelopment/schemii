import { execFileSync, spawn } from 'node:child_process';
import { constants, fstatSync, readFileSync, readlinkSync } from 'node:fs';
import { open } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';

export const BROWSER_DEPENDENCY_LEASE_FD = 5;
const CONFLICT_EXIT = 75;

function lockPath(root) {
  // Match scripts/ci/local_ci.py. The Git-common directory is shared across worktrees.
  const gitDir = execFileSync('git', ['-C', root, 'rev-parse', '--absolute-git-dir'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).replace(/\n$/, '');
  if (!isAbsolute(gitDir)) throw new Error('Cannot resolve the worktree Git directory.');
  return join(gitDir, 'qa-browser-worktree.lock');
}

function lockShared(fd) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      'flock',
      ['--shared', '--nonblock', '--conflict-exit-code', String(CONFLICT_EXIT), '3'],
      { stdio: ['ignore', 'ignore', 'ignore', fd] },
    );
    child.once('error', cause => {
      reject(new Error('Cannot run flock for the browser dependency lease.', { cause }));
    });
    child.once('close', (code, signal) => {
      if (code === 0) {
        resolve();
      } else if (code === CONFLICT_EXIT) {
        const error = new Error('Browser dependencies in this worktree are busy. Wait for the owning local CI run to finish.');
        reject(Object.assign(error, { code: 'QA_BROWSER_DEPENDENCY_BUSY' }));
      } else {
        reject(new Error(`Cannot acquire the browser dependency lease (flock ${signal || `exit ${code}`}).`));
      }
    });
  });
}

// Acquire before the first Playwright import or any shared deployment gate.
// The caller closes its FileHandle in finally. To start a broker, explicitly
// map handle.fd to BROWSER_DEPENDENCY_LEASE_FD before closing the parent's copy.
export async function acquireBrowserDependencyLease(root) {
  const flags = constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW;
  const handle = await open(lockPath(root), flags, 0o600);
  try {
    if (!(await handle.stat()).isFile()) throw new Error('Browser dependency lease requires a regular file.');
    await lockShared(handle.fd);
    return handle;
  } catch (error) {
    await handle.close();
    throw error;
  }
}

// Run in the broker before importing Playwright. An open descriptor by itself
// does not prove ownership. The broker keeps the inherited descriptor until exit.
export function assertBrowserDependencyLease(root) {
  const path = lockPath(root);
  const fd = BROWSER_DEPENDENCY_LEASE_FD;
  const message = `Broker requires the held worktree browser dependency lease on descriptor ${fd}.`;
  try {
    if (
      fstatSync(fd).isFile() &&
      readlinkSync(`/proc/self/fd/${fd}`) === path &&
      /^lock:.*FLOCK +ADVISORY +READ\s/m.test(readFileSync(`/proc/self/fdinfo/${fd}`, 'utf8'))
    ) {
      return;
    }
  } catch (error) {
    throw new Error(message, { cause: error });
  }
  throw new Error(message);
}
