// Launcher-only resource receipt. Never invoke Docker from an observer.
import { readFile, stat, lstat, open, rename, unlink, mkdir, link } from 'node:fs/promises';
import { dirname, resolve, posix } from 'node:path';
import { hostname, cpus, totalmem } from 'node:os';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const services = new Set(['schemii', 'ingress', 'metadata-postgres', 'demo-postgres', 'qa-postgres', 'ai-prototype-runtime']);
export async function processIdentity(pid, procRoot = '/proc') {
  if (!Number.isSafeInteger(pid) || pid < 1) throw new Error('invalid_process_identity');
  const text = await readFile(`${procRoot}/${pid}/stat`, 'utf8');
  const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
  if (fields.length < 22 || ['Z', 'X'].includes(fields[0]) || !/^\d+$/.test(fields[19])) throw new Error('process_exited');
  return { pid, birthTick: fields[19] };
}
export async function cgroupIdentity(pid, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup' } = {}) {
  const lines = (await readFile(`${procRoot}/${pid}/cgroup`, 'utf8')).trim().split('\n');
  const unified = lines.find(line => line.startsWith('0::'))?.slice(3);
  if (!unified || !unified.startsWith('/') || unified.includes('\0') || posix.normalize(unified) !== unified || unified === '/')
    throw new Error('dedicated_cgroup_v2_unavailable');
  const path = resolve(cgroupRoot, `.${unified}`);
  if (!path.startsWith(`${resolve(cgroupRoot)}/`)) throw new Error('invalid_cgroup_path');
  const information = await stat(path);
  if (!information.isDirectory()) throw new Error('invalid_cgroup_directory');
  return { path: unified, device: information.dev, inode: information.ino };
}
export async function buildReceipt(records, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup', revision, origin } = {}) {
  if (!Array.isArray(records) || records.length > 16 || !/^[0-9a-f]{40}(\+dirty)?$/.test(revision || '') ||
      !/^https:\/\/localhost:\d{4,5}$/.test(origin || '')) throw new Error('invalid_runtime_export');
  const bootId = (await readFile(`${procRoot}/sys/kernel/random/boot_id`, 'utf8')).trim();
  if (!/^[0-9a-f-]{36}$/.test(bootId)) throw new Error('invalid_boot_identity');
  const containers = [], seen = new Set();
  for (const record of records) {
    if (record.project !== 'schemii-test' || !services.has(record.service) || seen.has(record.service) ||
        !/^[0-9a-f]{64}$/.test(record.id || '') || !/^sha256:[0-9a-f]{64}$/.test(record.image || '') || record.running !== true ||
        !Number.isSafeInteger(record.memoryBytes) || record.memoryBytes < 0 || !Number.isSafeInteger(record.nanoCpus) || record.nanoCpus < 0)
      throw new Error('invalid_container_identity');
    seen.add(record.service);
    const rootProcess = await processIdentity(record.pid, procRoot);
    const cgroup = await cgroupIdentity(record.pid, { procRoot, cgroupRoot });
    if ((await processIdentity(record.pid, procRoot)).birthTick !== rootProcess.birthTick) throw new Error('process_replaced_during_export');
    containers.push({ service: record.service, id: record.id, image: record.image, rootProcess, cgroup,
      configuredMemoryBytes: record.memoryBytes, configuredCpus: record.nanoCpus / 1e9 });
  }
  if (!seen.has('schemii') || !seen.has('ingress') || !seen.has('metadata-postgres')) throw new Error('incomplete_runtime_topology');
  return { version: 1, at: new Date().toISOString(), revision, origin, bootId,
    topology: { host: hostname(), logicalCpus: cpus().length, hostMemoryBytes: totalmem(), generatorPlacement: 'cohosted_unqualified' }, containers };
}
export async function writePrivateJSON(path, data, { exclusive = false } = {}) {
  if (!path || !path.startsWith('/')) throw new Error('absolute_private_path_required');
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const parent = await lstat(dirname(path));
  if (!parent.isDirectory() || parent.isSymbolicLink() || (parent.mode & 0o077)) throw new Error('private_parent_required');
  const temporary = `${path}.${randomUUID()}.tmp`;
  let file;
  try {
    file = await open(temporary, 'wx', 0o600);
    await file.writeFile(`${JSON.stringify(data, null, 2)}\n`); await file.close(); file = null;
    if (exclusive) {
      // An existing receipt may belong to another live deployment/campaign.
      await link(temporary, path);
    } else await rename(temporary, path);
  } finally { await file?.close(); await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}
export async function readPrivateJSON(path) {
  const file = await open(path, 'r');
  try {
    const information = await file.stat(), entry = await lstat(path);
    if (!information.isFile() || (information.mode & 0o077) || entry.isSymbolicLink() ||
        entry.dev !== information.dev || entry.ino !== information.ino || information.size > 1024 * 1024) throw new Error('invalid_private_receipt');
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 5 || process.argv[2] !== 'export') throw new Error('Usage: runtime.mjs export PRIVATE_RECEIPT REVISION');
    let input = ''; for await (const chunk of process.stdin) { input += chunk; if (input.length > 65536) throw new Error('runtime_export_budget'); }
    const records = input.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    const receipt = await buildReceipt(records, { revision: process.argv[4], origin: `https://localhost:${process.env.SCHEMII_TEST_APP_PORT || '8001'}` });
    await writePrivateJSON(resolve(process.argv[3]), receipt, { exclusive: true });
  } catch (error) { process.stderr.write(`Runtime observation receipt unavailable: ${error.message}\n`); process.exitCode = 1; }
}
