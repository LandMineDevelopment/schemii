import test from 'node:test';
import assert from 'node:assert/strict';
import fs, { mkdtemp, rm, writeFile, access } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import os from 'node:os';
import { randomBytes } from 'node:crypto';
import { observeChild, stopChild, createRuntime, disposeRuntime, runtimeSocket, awaitControllerHandoff } from './children.mjs';
import { writeJSON } from '../harness/store.mjs';
import { runK6, birthTick } from './engine.mjs';
import { plan } from './plan.mjs';

async function temporary(t) {
  const dir = await mkdtemp(join(os.tmpdir(), 'schemii-load-child-test-'));
  t.after(() => rm(dir, { recursive: true, force: true })); return dir;
}
test('post-spawn generator ownership-write failure stops and joins its exact dummy child', async t => {
  const dir = await temporary(t), binary = join(dir, 'dummy-generator');
  await writeFile(binary, '#!/usr/bin/env node\nsetInterval(() => {}, 1000);\n', { mode: 0o700 });
  const aborter = new AbortController(); let owner;
  await assert.rejects(runK6({ spec: plan(), stage: { callsPerMinute: 100, seconds: 6 },
    receipt: { accounts: [{ username: 'fixture', userId: 'fixture', cookie: 'dummy-private' }] },
    dir, origin: 'https://localhost:8001', binary, signal: aborter.signal,
    onGenerator: async value => { if (value.pid) { owner = value; throw new Error('ownership_write_failed'); } } }), /ownership_write_failed/);
  assert.ok(owner?.pid);
  assert.notEqual(await birthTick(owner.pid), owner.birthTick);
  aborter.abort();
  await assert.rejects(access(join(dir, 'k6-input-private.json')), { code: 'ENOENT' });
});
test('managed cancellation is installed before recording the launched generator', async t => {
  const dir = await temporary(t), binary = join(dir, 'dummy-generator');
  await writeFile(binary, '#!/usr/bin/env node\nsetInterval(() => {}, 1000);\n', { mode: 0o700 });
  const aborter = new AbortController(); let owner;
  await assert.rejects(runK6({ spec: plan(), stage: { callsPerMinute: 100, seconds: 6 },
    receipt: { accounts: [{ username: 'fixture', userId: 'fixture', cookie: 'dummy-private' }] },
    dir, origin: 'https://localhost:8001', binary, signal: aborter.signal,
    onGenerator: async value => { if (value.pid) { owner = value; aborter.abort(); } } }), /k6_generator_failed/);
  assert.notEqual(await birthTick(owner.pid), owner.birthTick);
});
function procFixture(t, members, { scan, read, signal } = {}) {
  const signals = [];
  let scans = 0, now = 0;
  t.mock.method(performance, 'now', () => now);
  t.mock.method(fs, 'readdir', async path => {
    assert.equal(path, '/proc');
    now = scan?.(++scans) ?? now;
    return [...members.keys()].map(String);
  });
  t.mock.method(fs, 'readFile', async path => {
    const pid = Number(/^\/proc\/(\d+)\/stat$/.exec(path)?.[1]);
    assert.ok(Number.isSafeInteger(pid), path);
    read?.(pid);
    const member = members.get(pid);
    if (!member) throw Object.assign(new Error('fixture process gone'), { code: 'ENOENT' });
    const fields = Array(20).fill('0');
    fields[0] = 'S';
    fields[2] = member.group;
    fields[3] = member.group;
    fields[19] = member.birth;
    return `${pid} (fixture) ${fields.join(' ')}`;
  });
  t.mock.method(process, 'kill', (pid, name) => {
    signals.push([pid, members.get(pid)?.birth, name, now]);
    signal?.(pid, name);
    return true;
  });
  syncBuiltinESMExports();
  t.after(() => {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  });
  return signals;
}

test('owned stop escalates a late member and skips a stale identity and a live peer', async t => {
  const leader = { group: 101, birth: '10' };
  const stale = { group: 101, birth: '20' };
  const late = { group: 101, birth: '30' };
  const peer = { group: 201, birth: '40' };
  const members = new Map([[101, leader], [102, stale], [201, peer]]);
  let staleReads = 0;
  const signals = procFixture(t, members, {
    scan: number => {
      if (number === 2) members.set(102, { group: 201, birth: '21' });
      return [50, 75, 100, 2051][number - 1];
    },
    read: pid => {
      if (pid === 102 && ++staleReads === 2) members.set(102, { ...stale, birth: '21' });
    },
    signal: (pid, name) => {
      if (name !== 'SIGKILL') return;
      if (pid === 101) {
        members.set(103, late);
        members.delete(pid);
      } else if (pid === 103 && members.get(pid) === late) {
        members.set(pid, { ...late, birth: '31' });
      } else if (pid === 103) {
        members.delete(pid);
      }
    },
  });
  const outcome = { code: null, signal: 'SIGKILL' };
  assert.equal(await stopChild({ pid: 101 }, Promise.resolve(outcome), { graceMs: 50 }), outcome);
  assert.deepEqual(signals, [
    [101, '10', 'SIGKILL', 50], [103, '30', 'SIGKILL', 75], [103, '31', 'SIGKILL', 100],
  ]);
  assert.deepEqual(members.get(102), { group: 201, birth: '21' });
  assert.equal(members.get(201), peer);
});

test('owned stop refuses a replaced session leader without sending a signal', async t => {
  const members = new Map([[101, { group: 101, birth: '10' }]]);
  const signals = procFixture(t, members, {
    scan: () => { members.set(101, { group: 101, birth: '11' }); },
  });
  await assert.rejects(stopChild({ pid: 101 }, Promise.resolve(), { graceMs: 50 }), /child_identity_changed/);
  assert.deepEqual(signals, []);
});

test('owned stop reports a live member at the existing deadline', async t => {
  const members = new Map([[101, { group: 101, birth: '10' }]]);
  const signals = procFixture(t, members, {
    scan: number => {
      if (number === 4) members.set(103, { group: 101, birth: '30' });
      return [0, 50, 75, 2051][number - 1];
    },
  });
  await assert.rejects(stopChild({ pid: 101 }, Promise.resolve(), { graceMs: 50 }), /owned_child_not_reaped/);
  assert.deepEqual(signals, [[101, '10', 'SIGTERM', 0], [101, '10', 'SIGKILL', 50]]);
  assert.deepEqual([...members.keys()], [101, 103]);
});

test('stopping an owned controller-style process group joins descendants and preserves a live peer', async t => {
  const ipc = ['ignore', 'ignore', 'ignore', 'ipc'];
  const child = spawn(process.execPath, ['-e', `
    const { spawn } = require('node:child_process');
    const descendant = spawn(process.execPath, ['-e', \`
      const expiry = setTimeout(() => process.exit(1), 5000);
      process.on('message', () => { clearTimeout(expiry); process.exit(0); });
      process.send(process.pid);
    \`], { stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
    descendant.once('message', pid => { if (process.connected) process.send(pid); });
    descendant.once('exit', code => process.exit(code ?? 0));
    // Stay around to reap the direct child when the cleanup helper signals both.
    process.on('SIGTERM', () => {});
    process.on('disconnect', () => { if (descendant.connected) descendant.send('stop', () => {}); });
  `], { detached: true, stdio: ipc });
  const completion = observeChild(child);
  const peer = spawn(process.execPath, ['-e', `
    setTimeout(() => process.exit(0), 5000);
    process.on('disconnect', () => process.exit(0));
    process.send(process.pid);
  `], { detached: true, stdio: ipc });
  const fixtures = [child, peer];
  const fixtureExits = Promise.allSettled(fixtures.map(fixture => once(fixture, 'exit')));
  let descendantPid;
  t.after(async () => {
    // Both fixtures get an independent shutdown request before either join.
    for (const fixture of fixtures) {
      if (fixture.connected) fixture.disconnect();
    }
    await fixtureExits;
    assert.equal(await birthTick(child.pid), null);
    assert.equal(await birthTick(peer.pid), null);
    if (descendantPid) assert.equal(await birthTick(descendantPid), null);
  });
  const options = { signal: AbortSignal.timeout(2000) };
  const [[pid]] = await Promise.all([once(child, 'message', options), once(peer, 'message', options)]);
  descendantPid = pid;
  assert.ok(await birthTick(descendantPid));
  const peerBirth = await birthTick(peer.pid);
  assert.ok(peerBirth);
  const stopped = await stopChild(child, completion, { graceMs: 50 });
  assert.ok(stopped.code === 0 || stopped.signal === 'SIGKILL', JSON.stringify(stopped));
  assert.equal(await birthTick(child.pid), null);
  assert.equal(await birthTick(descendantPid), null);
  assert.equal(await birthTick(peer.pid), peerBirth);
});
test('runtime disposal requires the exact created directory and preserves peers and unknown contents', async t => {
  const id = `load-runtime-${randomBytes(6).toString('hex')}`, peerId = `${id}-peer`;
  const owned = await createRuntime(id), peer = await createRuntime(peerId);
  t.after(async () => { await disposeRuntime(id, owned); await disposeRuntime(peerId, peer); });
  await assert.rejects(createRuntime(id), { code: 'EEXIST' });
  await assert.rejects(disposeRuntime(id, { ...owned, inode: owned.inode + 1 }), /runtime_ownership_changed/);
  await assert.rejects(disposeRuntime(id, null), /unknown_runtime_owner/);
  const unknown = join(owned.path, 'unknown.txt');
  await writeFile(unknown, 'fixture-owned unknown content');
  await assert.rejects(disposeRuntime(id, owned), /unknown_runtime_contents/);
  await access(unknown); await access(peer.path);
  // Dispose this planted fixture only after proving it was preserved.
  await rm(unknown);
  await writeFile(runtimeSocket(id), 'not a socket');
  await assert.rejects(disposeRuntime(id, owned), /unknown_runtime_contents/);
  await access(runtimeSocket(id));
  await rm(runtimeSocket(id));
  await disposeRuntime(id, owned);
  await assert.rejects(access(owned.path), { code: 'ENOENT' });
  await access(peer.path);
  await disposeRuntime(id, owned); // stopped cleanup is idempotent
});
test('controller handoff blocks application work until its exact process identity is durably recorded', async t => {
  const dir = await temporary(t), runId = 'load-handoff-fixture';
  const file = join(dir, 'controller-launch.json'), launch = { version: 1, runId, status: 'pending' };
  await writeJSON(file, launch);
  await assert.rejects(awaitControllerHandoff(dir, runId, { timeoutMs: 20 }), /controller_handoff_timeout/);
  launch.status = 'started'; launch.owner = { pid: process.pid, birthTick: await birthTick(process.pid) };
  await writeJSON(file, launch); await awaitControllerHandoff(dir, runId);
  launch.owner.birthTick = 'unowned-birth'; await writeJSON(file, launch);
  await assert.rejects(awaitControllerHandoff(dir, runId), /controller_handoff_owner_mismatch/);
  launch.status = 'stopped'; await writeJSON(file, launch);
  await assert.rejects(awaitControllerHandoff(dir, runId), /controller_launch_stopped/);
});
