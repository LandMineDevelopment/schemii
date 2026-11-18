import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { cp, mkdir, mkdtemp, open, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
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
const selectedCI = `
import json
import os
import sys
from pathlib import Path
from scripts.ci import local_ci as runner

root = Path(sys.argv[1])
plan = runner.selected_plan(root, "HEAD", full=True)
plan["commands"] = runner.commands("e2e-tests", "HEAD")[1:]
plan["classification"]["profile"] = "e2e-tests"
calls = []
def invoke(root, command, environment, lease):
    assert command == ["npm", "ci"] or command[:2] == ["node", "--test"], command
    calls.append(command[:2])
    return 0

runner.invoke = invoke
runner.evidence = lambda *args: {}
os.environ["SCHEMII_E2E_BOOTSTRAP"] = "1"
code, receipt = runner.run_selected(root, plan, feedback=False, base="HEAD")
record = json.loads(receipt.read_text())
print(json.dumps(dict(code=code, reason=record["reason"], acceptance=record["acceptance"],
    statuses=[row["status"] for row in record["commands"]], calls=calls,
    admitted=(receipt.parent / "lease-admission.json").exists())))
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

function runCI(root) {
  const result = execFileSync('python3', ['-B', '-c', selectedCI, root], {
    cwd: repo, encoding: 'utf8', timeout: 15000, stdio: ['ignore', 'pipe', 'pipe'],
  });
  return JSON.parse(result.trimEnd().split('\n').at(-1));
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

async function harness(t) {
  const repos = await fixture(t);
  const { root, linked } = repos;
  const harnessDir = join(root, 'testing/harness');
  await mkdir(harnessDir, { recursive: true });
  for (const module of ['cli', 'daemon', 'browser-dependency-lease', 'leases', 'native', 'prerequisites', 'readiness', 'store', 'workers']) {
    await cp(join(repo, 'testing/harness', module + '.mjs'), join(harnessDir, module + '.mjs'));
  }
  const assets = 'src/schemii/schemii/web/assets';
  await mkdir(join(root, assets), { recursive: true });
  for (const name of ['workspace-navigation.js', 'request-coordinator.js']) {
    await cp(join(repo, assets, name), join(root, assets, name));
  }
  await writeFile(join(root, 'package.json'), '{"type":"module"}');
  await mkdir(join(root, '.schemii'));
  await writeFile(join(root, '.gitignore'), '.schemii/\nnode_modules/\nartifacts/\n');
  const credentials = join(root, '.schemii/accounts.json');
  await writeFile(credentials, JSON.stringify({ fixture: { password: 'synthetic' } }), { mode: 0o600 });
  const packageDir = join(root, 'node_modules/@playwright/test');
  await mkdir(packageDir, { recursive: true });
  await writeFile(join(packageDir, 'package.json'), '{"type":"module","exports":"./index.js"}');
  const imports = join(root, '.schemii/package-imports');
  await writeFile(imports, '');
  await writeFile(join(packageDir, 'index.js'), `
    import { appendFileSync } from 'node:fs';
    appendFileSync(${JSON.stringify(imports)}, process.argv[1].split('/').at(-1) + '\\n');
    export const chromium = { executablePath: () => process.execPath };
  `);
  // The real CLI and daemon never have a launcher or Chromium to invoke here.
  await writeFile(join(harnessDir, 'deployment.mjs'), `
    import { closeSync, readlinkSync } from 'node:fs';
    import { execFileSync } from 'node:child_process';
    import { join } from 'node:path';
    export const deploymentLockPath = root => join(root, '.git', 'qa-deployment.lock');
    export const sourceIdentity = () => ({commit:'fixture', fingerprint:'fixture'});
    export function startDeployment({root}) {
      if (readlinkSync('/proc/self/fd/3') !== deploymentLockPath(root) ||
          readlinkSync('/proc/self/fd/4') !== join(root, '.git', 'qa-startup.lock')) throw new Error('fixture gate mismatch');
      execFileSync('flock', ['--shared', '3'], {stdio:['ignore','ignore','ignore',3]});
      closeSync(4);
      // Bound fixture lifetime on assertion failure, without changing production timers.
      setTimeout(() => process.exit(97), 30000).unref();
      return {identity:sourceIdentity(), endpoints:[]};
    }
  `);
  await writeFile(join(harnessDir, 'browser.mjs'), `
    import { chromium } from '@playwright/test';
    import { assertBrowserDependencyLease } from './browser-dependency-lease.mjs';
    export class BrowserFleet {
      lanes = new Map();
      constructor({onLaunch}) { this.onLaunch = onLaunch; }
      async openLane(lane) {
        chromium.executablePath();
        await this.onLaunch({laneId:lane.id});
      }
      async probeCapabilities() { return {}; }
      async verifyChecks() { return []; }
      async parkLanesForIsolation() {}
      async proveIsolation() { return {}; }
      async navigateLane() {}
      async close() { assertBrowserDependencyLease(process.cwd()); }
    }
  `);
  execFileSync('git', ['-C', root, 'add', '.']);
  execFileSync('git', [
    '-C', root, '-c', 'user.name=QA fixture', '-c', 'user.email=qa@example.invalid',
    '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'offline harness',
  ]);
  const head = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  execFileSync('git', ['-C', linked, 'checkout', '--quiet', '--detach', head]);
  const cli = join(harnessDir, 'cli.mjs');
  const preflight = ['--accounts', 'fixture', '--credentials-file', credentials, '--headless'];
  const command = (...args) => spawnSync(process.execPath, [cli, ...args], {
    cwd: root, encoding: 'utf8', timeout: 15000,
  });
  return { ...repos, cli, daemon: join(harnessDir, 'daemon.mjs'), preflight, command, imports };
}

async function stopFixture(h, run) {
  const result = h.command('stop', '--run', run.id);
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  // Stop acknowledges cleanup just before broker exit closes the descriptor.
  const deadline = Date.now() + 5000;
  for (let check = writer(h.root); check.status !== 0; check = writer(h.root)) {
    assert.equal(check.status, 75, check.stderr);
    assert.ok(Date.now() < deadline, 'fixture broker still holds the checkout lease');
    await delay(25);
  }
}

test('same-worktree manual broker keeps Playwright available while selected CI defers npm', async t => {
  const h = await harness(t);
  const prepared = h.command('prepare', ...h.preflight);
  assert.ifError(prepared.error);
  assert.equal(prepared.status, 0, prepared.stderr);
  const run = JSON.parse(prepared.stdout);
  assert.match(run.id, /^qa-[a-z0-9-]{6,80}$/);
  t.after(() => rm(join('/tmp', `schemii-qa-${process.getuid()}`, run.id), { recursive: true, force: true }));
  try {
    assert.equal(run.status, 'ready');
    const same = runCI(h.root);
    assert.deepEqual(same, {
      code: 2, reason: 'browser-worktree-leased', acceptance: false, calls: [],
      statuses: Array(same.statuses.length).fill('pending'), admitted: false,
    });
    const linked = runCI(h.linked);
    assert.equal(linked.reason, 'deployment-leased');
    assert.deepEqual(linked.calls, [['npm', 'ci'], ['node', '--test']]);
    assert.deepEqual(linked.statuses.slice(0, 3), ['passed', 'passed', 'pending']);
    assert.equal(linked.acceptance, false);
  } finally {
    await stopFixture(h, run);
  }
  assert.equal(writer(h.root).status, 0);
  const resumed = h.command('resume', '--run', run.id);
  assert.ifError(resumed.error);
  assert.equal(resumed.status, 0, resumed.stderr || resumed.stdout);
  try {
    assert.equal(JSON.parse(resumed.stdout).status, 'ready');
    assert.equal(writer(h.root).status, 75);
  } finally {
    await stopFixture(h, run);
  }
  assert.equal(writer(h.root).status, 0);
  assert.equal(await readFile(h.imports, 'utf8'), 'cli.mjs\ndaemon.mjs\ndaemon.mjs\n');
});

test('the CI writer defers doctor, prepare, and resume before they touch Playwright', async t => {
  const h = await harness(t);
  const id = 'qa-stopped-fixture';
  const dir = join(h.root, 'artifacts/qa', id);
  await mkdir(dir, { recursive: true });
  const manifest = JSON.stringify({ id, status: 'stopped', lanes: [] });
  await writeFile(join(dir, 'manifest.json'), manifest);
  await writeFile(join(dir, 'control.json'), JSON.stringify({ socket: join(dir, 'absent.sock') }), { mode: 0o600 });
  await writeFile(join(dir, 'controller-owner.json'), JSON.stringify({ pid: process.pid, birthTick: '0' }), { mode: 0o600 });
  for (const args of [['doctor', ...h.preflight], ['prepare', ...h.preflight], ['resume', '--run', id]]) {
    const result = writer(h.root, process.execPath, h.cli, ...args);
    assert.equal(result.status, 4, result.stderr);
  }
  assert.equal(await readFile(h.imports, 'utf8'), '');
  assert.equal(await readFile(join(dir, 'manifest.json'), 'utf8'), manifest);
  const direct = spawnSync(process.execPath, [h.daemon, id, 'resume'], { cwd: h.root, encoding: 'utf8' });
  assert.match(direct.stderr, /requires the held worktree browser dependency lease/);
  assert.equal(await readFile(h.imports, 'utf8'), '');
  const doctor = h.command('doctor', ...h.preflight);
  assert.ifError(doctor.error);
  assert.equal(doctor.status, 0, doctor.stderr);
  assert.equal(await readFile(h.imports, 'utf8'), 'cli.mjs\n');
  assert.equal(writer(h.root).status, 0);
  await rm(join(h.root, 'node_modules/@playwright/test/index.js'));
  const missing = h.command('doctor', ...h.preflight);
  assert.ifError(missing.error);
  assert.equal(missing.status, 4, missing.stderr);
  assert.match(missing.stderr, /Prerequisite check failed/);
  assert.equal(writer(h.root).status, 0);
});
