import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, appendFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openJournal, journalRecords, readSnapshot, writeSnapshot, journalBudget } from './journal.mjs';
const tag = 'load_0123456789abcdef';
async function fixture(t) { const dir = await mkdtemp(join(tmpdir(), 'schemii-load-journal-')); t.after(() => rm(dir, { recursive: true, force: true })); return dir; }
const budget = records => ({ records, bytes: records * 2048 + 65536, recordBytes: 2048, chunkRecords: 64 });
test('1000 registered accounts and all execution IDs survive bounded chunk iteration', async t => {
  const dir = await fixture(t), path = join(dir, 'log'), journal = await openJournal(path, { tag, budget: budget(1100) });
  for (let index = 0; index < 1000; index++) await journal.append({ kind: 'account-intent', index });
  await journal.close();
  let count = 0; for await (const record of journalRecords(path)) { assert.equal(record.sequence, ++count); assert.equal(record.value.index, count - 1); }
  assert.equal(count, 1000);
  const tail = []; for await (const record of journalRecords(path, { from: 997 })) tail.push(record.sequence);
  assert.deepEqual(tail, [997, 998, 999, 1000]);
});
test('exhausted or oversized intent prevents its mutation and poisons writer', async t => {
  const dir = await fixture(t), journal = await openJournal(join(dir, 'log'), { tag, budget: budget(1) }); let mutations = 0;
  await journal.append({ intent: 1 }); mutations++;
  await assert.rejects(journal.append({ intent: 2 }).then(() => { mutations++; }), /budget_exhausted/);
  assert.equal(mutations, 1); await assert.rejects(journal.append({ intent: 3 }), /budget_exhausted/);
  await assert.rejects(journal.close());
});
test('peer and unknown ownership are preserved; live writers cannot be adopted', async t => {
  const dir = await fixture(t), path = join(dir, 'log'), journal = await openJournal(path, { tag, budget: budget(8) });
  const header = await readFile(join(path, 'header.json'));
  await assert.rejects(openJournal(path, { tag: 'load_aaaaaaaaaaaaaaaa', budget: budget(8), recover: true }), /scope_collision/);
  await assert.rejects(openJournal(path, { tag, budget: budget(8), recover: true }), /still_live/);
  assert.deepEqual(await readFile(join(path, 'header.json')), header); await journal.close();
});
test('partial append is never parsed as an issued mutation; recovery retains original tail', async t => {
  const dir = await fixture(t), path = join(dir, 'log'), journal = await openJournal(path, { tag, budget: budget(8) });
  await journal.append({ intent: 'durable' }); await journal.close();
  await appendFile(join(path, 'chunk-000000001.jsonl'), '{"sequence":2');
  await assert.rejects(async () => { for await (const _record of journalRecords(path)) { /* validate complete prefix */ } }, /partial_append/);
  const recovered = await openJournal(path, { tag, budget: budget(8), recover: true });
  await recovered.append({ intent: 'next' }); await recovered.close();
  const records = []; for await (const record of journalRecords(path)) records.push(record.value.intent);
  assert.deepEqual(records, ['durable', 'next']);
});
test('snapshot boundaries reject aliases, cap bytes and preserve foreign files', async t => {
  const dir = await fixture(t), file = join(dir, 'data.json');
  await writeSnapshot(file, { private: true }); await symlink(file, join(dir, 'alias.json'));
  await assert.rejects(readSnapshot(join(dir, 'alias.json')));
  await assert.rejects(writeSnapshot(file, { large: 'x'.repeat(100) }, { maximumBytes: 32 }), /budget_exhausted/);
  assert.deepEqual(await readSnapshot(file), { private: true });
  await symlink(dir, join(dir, 'parent-alias')); await assert.rejects(readSnapshot(join(dir, 'parent-alias/data.json')));
  assert(journalBudget({ stages: [{ callsPerMinute: 100, seconds: 6 }], registeredIdentities: 1000, activeIdentities: 100, profilesPerOwner: 1 }).records > 1000);
});
test('missing or reordered chunks cannot silently erase owned execution history', async t => {
  const dir = await fixture(t), path = join(dir, 'log'), journal = await openJournal(path, { tag, budget: budget(130) });
  for (let index = 0; index < 65; index++) await journal.append({ execution: index });
  await journal.close();
  await rm(join(path, 'chunk-000000001.jsonl'));
  await assert.rejects(async () => { for await (const _record of journalRecords(path, { from: 65 })) { /* history identity must remain complete */ } }, /sequence_gap/);
});
