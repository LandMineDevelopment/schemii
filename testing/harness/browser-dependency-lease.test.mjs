import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, open, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { acquireBrowserDependencyLease, BROWSER_DEPENDENCY_LEASE_FD } from './browser-dependency-lease.mjs';

const repo = fileURLToPath(new URL('../..', import.meta.url));
const moduleURL = new URL('./browser-dependency-lease.mjs', import.meta.url).href;
const pythonWriter = `
import subprocess
import sys
from pathlib import Path
from scripts.ci.local_ci import browser_worktree_lease

try:
    with browser_worktree_lease(Path(sys.argv[1])):
        if sys.argv[2:]:
            raise SystemExit(subprocess.run(sys.argv[2:], check=False, timeout=10).returncode)
except BlockingIOError:
    raise SystemExit(75)
`;

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'schemii-browser-dependency-lease-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = join(directory, 'source checkout');
  const linked = join(directory, 'linked checkout');
  execFileSync('git', ['init', '--quiet', root]);
  execFileSync('git', [
    '-C', root, '-c', 'user.name=QA fixture', '-c', 'user.email=qa@example.invalid',
    '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet', '-m', 'fixture',
  ]);
  execFileSync('git', ['-C', root, 'worktree', 'add', '--quiet', '--detach', linked, 'HEAD']);
  return { root, linked, lock: join(root, '.git', 'qa-browser-worktree.lock') };
}

function writer(root, ...argv) {
  const result = spawnSync('python3', ['-B', '-c', pythonWriter, root, ...argv], {
    cwd: repo, encoding: 'utf8', timeout: 15000,
  });
  assert.ifError(result.error);
  return result;
}

function brokerFds(fd) {
  const stdio = ['pipe', 'pipe', 'inherit', 'ignore', 'ignore'];
  stdio[BROWSER_DEPENDENCY_LEASE_FD] = fd;
  return stdio;
}

test('manual dependency readers exclude the CI writer only in their own worktree', async t => {
  const { root, linked, lock } = await fixture(t);
  const reader = await acquireBrowserDependencyLease(root);
  try {
    const before = await stat(lock);
    assert.equal(before.mode & 0o777, 0o600);
    const secondReader = await acquireBrowserDependencyLease(root);
    await secondReader.close();
    assert.equal(writer(root).status, 75);
    assert.equal(writer(linked).status, 0);
  } finally {
    await reader.close();
  }
  const beforeWriter = await stat(lock);
  assert.equal(writer(root).status, 0);
  assert.equal((await stat(lock)).ino, beforeWriter.ino);
});

test('a CI writer blocks manual preflight with a distinct contention error', async t => {
  const { root } = await fixture(t);
  const script = `
    import assert from 'node:assert/strict';
    import { acquireBrowserDependencyLease } from ${JSON.stringify(moduleURL)};
    await assert.rejects(acquireBrowserDependencyLease(process.argv[1]), {
      code: 'QA_BROWSER_DEPENDENCY_BUSY',
    });
  `;
  const result = writer(root, process.execPath, '--input-type=module', '-e', script, root);
  assert.equal(result.status, 0, result.stderr);
  const reader = await acquireBrowserDependencyLease(root);
  await reader.close();
});

test('the broker inherits the real shared lease on fd 5 after the CLI closes its copy', async t => {
  const { root, linked } = await fixture(t);
  const script = `
    import assert from 'node:assert/strict';
    import { assertBrowserDependencyLease } from ${JSON.stringify(moduleURL)};
    assertBrowserDependencyLease(process.argv[1]);
    assert.throws(() => assertBrowserDependencyLease(process.argv[2]), /requires the held/);
    console.log('ready');
    process.stdin.resume();
  `;
  const reader = await acquireBrowserDependencyLease(root);
  let child;
  let done;
  try {
    child = spawn('bash', [
      '-c', 'exec "$@"', 'fixture-broker', process.execPath,
      '--input-type=module', '-e', script, root, linked,
    ], { detached: true, stdio: brokerFds(reader.fd) });
    done = once(child, 'close');
    const output = createInterface({ input: child.stdout });
    const ready = done.then(([code]) => { throw new Error(`Broker exited before readiness (exit ${code}).`); });
    const [line] = await Promise.race([
      once(output, 'line', { signal: AbortSignal.timeout(10000) }),
      ready,
    ]);
    assert.equal(line, 'ready');
    await reader.close();
    assert.equal(writer(root).status, 75);
    assert.equal(writer(linked).status, 0);
  } finally {
    await reader.close();
    if (child) {
      child.stdin.end();
      assert.deepEqual(await done, [0, null]);
    }
  }
  assert.equal(writer(root).status, 0);
});

test('an open but unlocked fd is rejected before broker package use', async t => {
  const { root, lock } = await fixture(t);
  const handle = await open(lock, 'a+', 0o600);
  try {
    const script = `
      import assert from 'node:assert/strict';
      import { assertBrowserDependencyLease } from ${JSON.stringify(moduleURL)};
      assert.throws(() => assertBrowserDependencyLease(process.argv[1]), /requires the held/);
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], {
      stdio: brokerFds(handle.fd),
      timeout: 10000,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0);
  } finally {
    await handle.close();
  }
  assert.equal(writer(root).status, 0);
});

test('fallible lock paths fail closed without classifying them as contention', async t => {
  const { root, lock } = await fixture(t);
  const peerFile = join(root, '.git', 'peer');
  await writeFile(peerFile, 'untouched', { mode: 0o600 });
  await symlink(peerFile, lock);
  await assert.rejects(acquireBrowserDependencyLease(root), { code: 'ELOOP' });
  await assert.rejects(acquireBrowserDependencyLease(join(root, 'missing checkout')), { status: 128 });
  assert.equal((await stat(peerFile)).size, 'untouched'.length);
  await rm(lock);
  const reader = await acquireBrowserDependencyLease(root);
  await reader.close();
});
