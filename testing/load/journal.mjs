// Private load-only persistence. Config/source-witness limits remain unchanged.
import { constants } from 'node:fs';
import { open, lstat, mkdir, readdir, rename, unlink, link } from 'node:fs/promises';
import { resolve, dirname, basename } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { processIdentity } from './runtime.mjs';

const uid = () => process.getuid();
const hash = value => createHash('sha256').update(value).digest('hex');
async function parent(path) {
  if (typeof path !== 'string' || resolve(path) !== path || path.includes('\0')) throw new Error('absolute_journal_path_required');
  const parts = dirname(path).split('/').filter(Boolean), chain = [];
  let handle = await open('/', constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    for (const part of parts) {
      const child = await open(`/proc/self/fd/${handle.fd}/${part}`, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      await handle.close(); handle = child;
      const info = await handle.stat();
      if (![0, uid()].includes(info.uid) || (info.mode & 0o022) && !(info.uid === 0 && info.mode & 0o1000)) throw new Error('unsafe_journal_ancestor');
      chain.push([info.dev, info.ino]);
    }
    const info = await handle.stat();
    if (info.uid !== uid() || (info.mode & 0o777) !== 0o700) throw new Error('private_journal_directory_required');
    return { handle, leaf: `/proc/self/fd/${handle.fd}/${basename(path)}`, chain };
  } catch (error) { await handle.close(); throw error; }
}
const privateFile = (info, maximum) => {
  if (!info.isFile() || info.uid !== uid() || (info.mode & 0o777) !== 0o600 || info.size > maximum) throw new Error('invalid_private_load_journal');
};
async function boundedBytes(file, maximum) {
  const chunks = []; let size = 0;
  for (;;) {
    const buffer = Buffer.alloc(Math.min(65536, maximum + 1 - size)), { bytesRead } = await file.read(buffer);
    if (!bytesRead) return Buffer.concat(chunks);
    size += bytesRead;
    if (size > maximum) throw new Error('load_journal_byte_budget');
    chunks.push(buffer.subarray(0, bytesRead));
  }
}
export async function readSnapshot(path, maximumBytes = 64 * 1024 * 1024, withIdentity = false) {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > 128 * 1024 * 1024) throw new Error('invalid_snapshot_budget');
  const directory = await parent(path); let file;
  try {
    const entry = await lstat(directory.leaf); privateFile(entry, maximumBytes);
    file = await open(directory.leaf, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const before = await file.stat(); privateFile(before, maximumBytes);
    if (before.ino !== entry.ino || before.dev !== entry.dev) throw new Error('journal_replaced');
    const data = await boundedBytes(file, maximumBytes);
    const after = await file.stat(), current = await lstat(directory.leaf);
    if (data.length > maximumBytes || before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs || current.ino !== before.ino || current.dev !== before.dev)
      throw new Error('journal_changed_during_read');
    const check = await parent(path);
    try { if (JSON.stringify(check.chain) !== JSON.stringify(directory.chain)) throw new Error('journal_parent_changed'); } finally { await check.handle.close(); }
    const value = JSON.parse(data);
    return withIdentity ? { value, sha256: hash(data) } : value;
  } finally { await file?.close(); await directory.handle.close(); }
}
export async function readFixtureSecret(path, directory) {
  if (!path.startsWith(directory + '/')) throw new Error('fixture_secret_outside_authority');
  const location = await parent(path); let file;
  try {
    const before = await lstat(location.leaf);
    if (!before.isFile()) throw new Error('invalid_fixture_secret');
    file = await open(location.leaf, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = await file.stat();
    if (!info.isFile() || info.uid !== uid() || ![0o600, 0o640].includes(info.mode & 0o777) || info.size > 4096 || info.ino !== before.ino || info.dev !== before.dev) throw new Error('invalid_fixture_secret');
    const bytes = await boundedBytes(file, 4096), after = await file.stat(), current = await lstat(location.leaf);
    if (info.size !== bytes.length || info.size !== after.size || info.mtimeMs !== after.mtimeMs || info.ctimeMs !== after.ctimeMs || current.ino !== info.ino || current.dev !== info.dev) throw new Error('fixture_secret_changed');
    const check = await parent(path);
    try { if (JSON.stringify(check.chain) !== JSON.stringify(location.chain)) throw new Error('fixture_secret_parent_changed'); } finally { await check.handle.close(); }
    const value = bytes.toString('utf8').replace(/\n$/, '');
    if (!value || /[\r\n]/.test(value)) throw new Error('invalid_fixture_secret');
    return value;
  } finally { await file?.close(); await location.handle.close(); }
}
export async function writeSnapshot(path, value, { maximumBytes = 64 * 1024 * 1024, exclusive = false } = {}) {
  const payload = Buffer.from(JSON.stringify(value) + '\n');
  if (payload.length > maximumBytes || maximumBytes > 128 * 1024 * 1024) throw new Error('load_snapshot_budget_exhausted');
  const directory = await parent(path), temporary = `${directory.leaf}.${randomUUID()}.tmp`;
  let file;
  try {
    try { privateFile(await lstat(directory.leaf), maximumBytes); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    file = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await file.writeFile(payload); await file.sync(); await file.close(); file = null;
    if (exclusive) { await link(temporary, directory.leaf); await unlink(temporary); }
    else await rename(temporary, directory.leaf);
    await directory.handle.sync();
  } finally { await file?.close(); await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); await directory.handle.close(); }
}
export function journalBudget(spec) {
  const journeys = spec.stages.reduce((total, stage) => total + Math.floor(stage.callsPerMinute * stage.seconds / 60), 0);
  const records = journeys * 8 + spec.activeIdentities * spec.profilesPerOwner * 32 + spec.registeredIdentities * 8 + 4096;
  const bytes = records * 2048 + 65536;
  if (!Number.isSafeInteger(records) || records > 4000000 || bytes > 4 * 1024 ** 3 || spec.activeIdentities * spec.profilesPerOwner * 32768 > 64 * 1024 ** 2) throw new Error('load_journal_envelope_exceeded');
  return { records, bytes, recordBytes: 2048, chunkRecords: 64, snapshotBytes: 64 * 1024 ** 2 };
}
async function chunkLines(path, maximum) {
  const directory = await parent(path); let file;
  try {
    file = await open(directory.leaf, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    privateFile(await file.stat(), maximum);
    const data = await boundedBytes(file, maximum);
    if (data.length > maximum) throw new Error('journal_chunk_budget');
    const text = data.toString('utf8'), boundary = text.lastIndexOf('\n') + 1;
    return { lines: text.slice(0, boundary).split('\n').filter(Boolean), partial: text.slice(boundary), bytes: data.length };
  } finally { await file?.close(); await directory.handle.close(); }
}
export async function* journalRecords(directory, { from = 1 } = {}) {
  const header = await readSnapshot(`${directory}/header.json`, 65536), names = (await readdir(directory)).filter(name => /^chunk-\d{9}\.jsonl$/.test(name)).sort();
  validateBudget(header.budget);
  if (header.version !== 1 || !/^load_[a-f0-9]{16}$/.test(header.tag) || !Number.isSafeInteger(from) || from < 1) throw new Error('invalid_journal_header');
  for (const [index, name] of names.entries()) {
    const first = Number(name.slice(6, 15));
    if (first !== index * header.budget.chunkRecords + 1 || first > header.budget.records) throw new Error('journal_sequence_gap');
    if (first + header.budget.chunkRecords <= from) continue;
    const chunk = await chunkLines(`${directory}/${name}`, header.budget.chunkRecords * header.budget.recordBytes);
    if (chunk.lines.length > header.budget.chunkRecords || index < names.length - 1 && chunk.lines.length !== header.budget.chunkRecords) throw new Error('journal_chunk_sequence_invalid');
    let expected = first;
    for (const line of chunk.lines) {
      const record = JSON.parse(line);
      if (Buffer.byteLength(line) + 1 > header.budget.recordBytes || record.sequence > header.budget.records || record.sequence !== expected++ || record.checksum !== hash(JSON.stringify({ sequence: record.sequence, value: record.value }))) throw new Error('journal_corrupt_record');
      if (record.sequence >= from) yield record;
    }
    if (chunk.partial) throw new Error('journal_partial_append');
  }
}
function validateBudget(budget) {
  if (!budget || budget.recordBytes !== 2048 || budget.chunkRecords !== 64 || !Number.isSafeInteger(budget.records) || budget.records < 1 || budget.records > 4000000 || !Number.isSafeInteger(budget.bytes) || budget.bytes < budget.records * budget.recordBytes || budget.bytes > 4 * 1024 ** 3) throw new Error('invalid_journal_budget');
}
export async function openJournal(directory, { tag, budget, recover = false }) {
  validateBudget(budget);
  if (!/^load_[a-f0-9]{16}$/.test(tag)) throw new Error('invalid_journal_budget');
  const outer = await parent(directory);
  try { await mkdir(outer.leaf, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; }); }
  finally { await outer.handle.close(); }
  const scope = { version: 1, tag, budget };
  try { await writeSnapshot(`${directory}/header.json`, scope, { exclusive: true, maximumBytes: 65536 }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; if (JSON.stringify(await readSnapshot(`${directory}/header.json`)) !== JSON.stringify(scope)) throw new Error('journal_scope_collision'); }
  const ownerPath = `${directory}/owner.json`, owner = await processIdentity(process.pid);
  try { await writeSnapshot(ownerPath, { ...owner, tag }, { exclusive: true, maximumBytes: 65536 }); }
  catch (error) {
    if (error.code !== 'EEXIST' || !recover) throw error;
    const previous = await readSnapshot(ownerPath);
    if (previous.tag !== tag) throw new Error('journal_owner_unknown');
    try { if ((await processIdentity(previous.pid)).birthTick === previous.birthTick) throw new Error('journal_writer_still_live'); }
    catch (problem) { if (!['ENOENT', 'ESRCH'].includes(problem.code) && problem.message !== 'process_exited') throw problem; }
    await writeSnapshot(ownerPath, { ...owner, tag }, { maximumBytes: 65536 });
  }
  let sequence = 0, bytes = 0, serial = Promise.resolve(), closed = false;
  const names = (await readdir(directory)).filter(name => /^chunk-\d{9}\.jsonl$/.test(name)).sort();
  for (const [index, name] of names.entries()) {
    const path = `${directory}/${name}`, chunk = await chunkLines(path, budget.chunkRecords * budget.recordBytes);
    if (chunk.partial) {
      if (!recover || index !== names.length - 1) throw new Error('journal_partial_append');
      await writeSnapshot(`${directory}/partial-${randomUUID()}.json`, { name, tail: chunk.partial }, { exclusive: true });
      const handle = await open(path, constants.O_WRONLY | constants.O_NOFOLLOW);
      try { await handle.truncate(chunk.bytes - Buffer.byteLength(chunk.partial)); await handle.sync(); } finally { await handle.close(); }
    }
    bytes += chunk.bytes - Buffer.byteLength(chunk.partial);
  }
  for await (const record of journalRecords(directory)) {
    if (record.sequence !== sequence + 1) throw new Error('journal_sequence_gap'); sequence = record.sequence;
  }
  return {
    append(value) {
      const pending = serial.then(async () => {
        if (closed) throw new Error('journal_closed');
        const record = { sequence: sequence + 1, value }, payload = JSON.stringify({ ...record, checksum: hash(JSON.stringify(record)) }) + '\n';
        if (Buffer.byteLength(payload) > budget.recordBytes || sequence >= budget.records || bytes + Buffer.byteLength(payload) > budget.bytes) throw new Error('load_journal_budget_exhausted');
        const first = Math.floor(sequence / budget.chunkRecords) * budget.chunkRecords + 1, path = `${directory}/chunk-${String(first).padStart(9, '0')}.jsonl`;
        const location = await parent(path); let file;
        try {
          file = await open(location.leaf, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
          privateFile(await file.stat(), budget.chunkRecords * budget.recordBytes);
          await file.writeFile(payload); await file.sync(); await location.handle.sync();
          sequence++; bytes += Buffer.byteLength(payload); return sequence;
        } finally { await file?.close(); await location.handle.close(); }
      });
      serial = pending; return pending; // A failed append poisons this writer; no unrecorded mutation proceeds.
    },
    async close() {
      await serial; closed = true;
      const current = await readSnapshot(ownerPath);
      if (current.pid !== owner.pid || current.birthTick !== owner.birthTick || current.tag !== tag) throw new Error('journal_owner_changed');
      const location = await parent(ownerPath);
      try { await unlink(location.leaf); await location.handle.sync(); } finally { await location.handle.close(); }
    },
  };
}
