import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, access } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import os from 'node:os';
import { observeChild, stopChild } from './children.mjs';
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
test('stopping an owned controller-style process group joins descendants and preserves a live peer', async t => {
  const child = spawn(process.execPath, ['-e', `
    const {spawn}=require('node:child_process');
    spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
    setInterval(()=>{},1000);
  `], { detached: true, stdio: 'ignore' });
  const completion = observeChild(child);
  const peer = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { detached: true, stdio: 'ignore' });
  const peerCompletion = observeChild(peer);
  t.after(async () => { await stopChild(child, completion); await stopChild(peer, peerCompletion); });
  await new Promise(resolve => setTimeout(resolve, 80));
  const peerBirth = await birthTick(peer.pid);
  await stopChild(child, completion, { graceMs: 50 });
  assert.equal(await birthTick(child.pid), null);
  assert.equal(await birthTick(peer.pid), peerBirth);
});
