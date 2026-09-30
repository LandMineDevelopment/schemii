import { readFile, readdir, mkdir, lstat, unlink, rmdir } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { privateJSON } from '../harness/store.mjs';

export function runtimeSocket(runId) {
  if (!/^load-[a-z0-9-]{6,80}$/.test(runId || '')) throw new Error('invalid_load_run');
  return join('/tmp', `schemii-load-${process.getuid()}`, runId, 'controller.sock');
}
function privateDirectory(stat) {
  return stat.isDirectory() && stat.uid === process.getuid() && !(stat.mode & 0o077);
}
export async function createRuntime(runId) {
  const path = dirname(runtimeSocket(runId));
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  if (!privateDirectory(await lstat(dirname(path)))) throw new Error('runtime_parent_ownership_changed');
  // A run must create its own directory, never adopt an existing peer path.
  await mkdir(path, { mode: 0o700 });
  const stat = await lstat(path);
  return { path, uid: stat.uid, device: stat.dev, inode: stat.ino };
}
export async function disposeRuntime(runId, receipt) {
  const socket = runtimeSocket(runId), path = dirname(socket);
  if (receipt?.path !== path || receipt.uid !== process.getuid() ||
      !Number.isSafeInteger(receipt.device) || !Number.isSafeInteger(receipt.inode)) throw new Error('unknown_runtime_owner');
  let stat;
  try { stat = await lstat(path); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
  if (!privateDirectory(await lstat(dirname(path))) || !privateDirectory(stat) ||
      stat.dev !== receipt.device || stat.ino !== receipt.inode) throw new Error('runtime_ownership_changed');
  const entries = await readdir(path);
  if (entries.some(name => name !== 'controller.sock')) throw new Error('unknown_runtime_contents');
  if (entries.length) {
    if (!(await lstat(socket)).isSocket()) throw new Error('unknown_runtime_contents');
    await unlink(socket);
  }
  // No recursive deletion: unexpected files or a replacement remain intact.
  await rmdir(path);
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function identity(pid) {
  try {
    const text = await readFile(`/proc/${pid}/stat`, 'utf8');
    const fields = text.slice(text.lastIndexOf(')') + 2).split(' ');
    return { state: fields[0], group: Number(fields[2]), session: Number(fields[3]), birthTick: fields[19] };
  } catch (error) { if (['ENOENT', 'ESRCH'].includes(error.code)) return null; throw error; }
}
export async function awaitControllerHandoff(dir, runId, { timeoutMs = 10000 } = {}) {
  const self = await identity(process.pid), deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    const launch = await privateJSON(join(dir, 'controller-launch.json'));
    if (launch.version !== 1 || launch.runId !== runId) throw new Error('unknown_controller_launch');
    if (launch.status === 'started') {
      if (launch.owner?.pid !== process.pid || launch.owner.birthTick !== self.birthTick) throw new Error('controller_handoff_owner_mismatch');
      return;
    }
    if (launch.status !== 'pending') throw new Error('controller_launch_stopped');
    await pause(10);
  }
  throw new Error('controller_handoff_timeout');
}

export function observeChild(child) {
  return new Promise(resolve => {
    let error;
    child.once('error', value => { error = value; });
    child.once('close', (code, signal) => resolve({ code, signal, error }));
  });
}
async function groupMembers(pid) {
  const owned = [];
  for (const entry of await readdir('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    const value = await identity(Number(entry));
    if (value?.group === pid && value.session === pid && value.state !== 'Z') owned.push({ pid: Number(entry), ...value });
  }
  return owned;
}

// Call only with the actual ChildProcess returned by this coordinator. Each
// managed launch uses detached:true, creating a private session/process group.
// Joining the child and its group prevents leaked work outliving a failed
// ownership write. Peer sessions and reused PID identities are never signalled.
export async function stopChild(child, completion, { graceMs = 5000 } = {}) {
  if (!child?.pid) return completion;
  const pid = child.pid;
  const initial = await identity(pid);
  if (initial && (initial.group !== pid || initial.session !== pid)) throw new Error('child_group_not_owned');
  const started = performance.now(); let killed = false;
  while (true) {
    const members = await groupMembers(pid);
    if (!members.length) break;
    const parent = await identity(pid);
    if (parent && initial && parent.birthTick !== initial.birthTick) throw new Error('child_identity_changed');
    if (!killed) {
      for (const member of members) {
        const current = await identity(member.pid);
        if (current?.birthTick === member.birthTick && current.group === pid && current.session === pid) {
          try { process.kill(member.pid, performance.now() - started < graceMs ? 'SIGTERM' : 'SIGKILL'); }
          catch (error) { if (error.code !== 'ESRCH') throw error; }
        }
      }
      killed = performance.now() - started >= graceMs;
    }
    if (performance.now() - started > graceMs + 2000) throw new Error('owned_child_not_reaped');
    await pause(25);
  }
  return completion;
}
