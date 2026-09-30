import { readFile, readdir } from 'node:fs/promises';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function identity(pid) {
  try {
    const text = await readFile(`/proc/${pid}/stat`, 'utf8');
    const fields = text.slice(text.lastIndexOf(')') + 2).split(' ');
    return { state: fields[0], group: Number(fields[2]), session: Number(fields[3]), birthTick: fields[19] };
  } catch (error) { if (['ENOENT', 'ESRCH'].includes(error.code)) return null; throw error; }
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
