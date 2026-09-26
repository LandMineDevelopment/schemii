import { spawn } from 'node:child_process';
import { mkdir, open, readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;

async function birthTick(pid) {
  const text = await readFile(`/proc/${pid}/stat`, 'utf8');
  return text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/)[19];
}

function assignment(root, sessionFile, brief) {
  const command = `${quote(join(root, 'test.sh'))}`;
  const session = `--session-file ${quote(sessionFile)}`;
  return `You are one independent manual UI tester assigned exclusively to this lane.
Use only the harness CLI below for browser interaction. Your session file is a scoped ownership handle: pass its path, never read or print its contents. Never inspect other lanes, credential files, environment secrets, or account passwords. Never use T3 preview, another browser, raw application APIs, network clients, arbitrary browser JavaScript, application tests, or deployment commands. Do not spawn subagents or background processes. Do not modify files, application data, account settings, or source code. Do not create scripts or automate a sequence to manufacture passing results. This is a read-only manual harness walkthrough.

Read the assignment and genuinely inspect each selected scenario and viewport. Use individual CLI actions, inspect returned snapshots, and use your image viewing tool to examine screenshots. Desktop and mobile require separate observations and separate exact scenario IDs; never infer one viewport from another. Report failed or blocked honestly. Harness capability probes are not application acceptance evidence.

Commands (execute the absolute command directly; no shell wrapper scripts):
${command} action ${session} --kind identity
${command} action ${session} --kind snapshot
${command} action ${session} --kind navigate --args-json '{"url":"/"}'
${command} action ${session} --kind resize --args-json '{"width":1280,"height":800}'
${command} action ${session} --kind click --args-json '{"role":"button","name":"EXACT VISIBLE NAME"}'
${command} action ${session} --kind screenshot
${command} heartbeat ${session}
${command} checkpoint ${session} --scenario EXACT_SCENARIO_ID --functional passed --visual passed --note 'Expected and actual observations for this viewport' --evidence ABSOLUTE_SCREENSHOT_PATH
${command} finish ${session}

Start with identity and snapshot. For EVERY scenario, resize to its exact viewport, navigate to its product (schemii: /, schemoo: /schemoo, schemer: /schemer) unless its assignment specifies another URL, perform a harmless ordinary UI interaction appropriate to the scenario, capture a fresh screenshot, view that image, and record expected/actual observations. Inspect style for clipping, overlap, readability, responsive navigation, and visible errors. Use functional/visual passed, failed, or blocked separately. Do not claim visual passed without viewing fresh evidence. If a scenario needs writes or inaccessible prerequisites, record blocked and explain rather than performing writes. If an action returns a pending dialog, use action kind dialog with args-json {"action":"dismiss"} unless a harmless confirmation is explicitly needed. Never blindly retry a potentially mutating click.

Checkpoint every scenario using its exact ID and its own evidence before finish. If your session becomes stale, stop and report it; never recover or claim another lane yourself. Finish with a concise factual summary of tested scenarios and gaps. Do not claim application-wide correctness.

Assignment:
${JSON.stringify(brief, null, 2)}
`;
}

/** One installed Codex process per lane; no dependency on T3 subagent capacity. */
export class CodexWorkers {
  constructor({ root, runDir, model, timeoutSeconds = 600, onEvent = () => {}, onExit = () => {} }) {
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) throw new Error('Agent timeout must be positive');
    this.root = resolve(root);
    this.runDir = resolve(runDir);
    this.model = model;
    this.timeoutSeconds = timeoutSeconds;
    this.onEvent = onEvent;
    this.onExit = onExit;
    this.handles = new Map();
    this.closing = false;
    this.peakProcesses = 0;
    this.peakTurns = 0;
  }

  #emit(data) {
    try { Promise.resolve(this.onEvent({ at: new Date().toISOString(), ...data })).catch(() => {}); } catch {}
  }

  async start({ lane, sessionFile, brief }) {
    const laneId = typeof lane === 'string' ? lane : lane.id;
    if (!/^[a-zA-Z0-9_-]+$/.test(laneId)) throw new Error('Invalid worker lane ID');
    if (this.closing) throw new Error('Worker pool is closing');
    if (this.handles.has(laneId)) throw new Error('Lane already has an agent process');
    if (this.handles.size >= 10) throw new Error('At most ten Codex workers may run concurrently');
    const directory = join(this.runDir, laneId);
    // Reserve synchronously so concurrent starts cannot launch two owners.
    const handle = { laneId, startedAt: new Date().toISOString(), finished: false, reason: null, turnStarted: false };
    this.handles.set(laneId, handle);
    let resolveDone;
    handle.done = new Promise(resolve => { resolveDone = resolve; });
    const finish = (code, signal, error = false) => {
      if (handle.finished) return;
      if (handle.draining) { handle.leaderExit = { code, signal, error }; return; }
      handle.finished = true;
      clearTimeout(handle.timer);
      const result = { laneId, pid: handle.pid ?? null, birthTick: handle.birthTick ?? null, code, signal, reason: handle.reason ?? (error ? 'spawn-failed' : code === 0 ? 'completed' : 'agent-failed'), turnStarted: handle.turnStarted, startedAt: handle.startedAt, endedAt: new Date().toISOString() };
      this.handles.delete(laneId);
      resolveDone(result);
      this.#emit({ laneId, kind: 'worker-exited', reason: result.reason, code, signal });
      try { Promise.resolve(this.onExit(result)).catch(() => {}); } catch {}
    };
    handle.finish = finish;
    let output;
    try {
      await mkdir(directory, { recursive: true, mode: 0o700 });
      output = await open(join(directory, 'agent-events.jsonl'), 'a', 0o600);
      if (this.closing || handle.reason) throw new Error('Worker pool stopped before agent launch');
      const args = ['--no-daemon', '-a', 'never', 'exec', '--json', '--ephemeral', '--sandbox', 'workspace-write', '-c', 'sandbox_workspace_write.network_access=true', '-c', 'sandbox_workspace_write.writable_roots=[]', '-c', 'sandbox_workspace_write.exclude_slash_tmp=true', '-c', 'sandbox_workspace_write.exclude_tmpdir_env_var=true', '--skip-git-repo-check', '-C', directory, '-o', join(directory, 'agent-final.txt')];
      if (this.model) args.push('-m', this.model);
      args.push('-');
      const child = spawn('codex', args, { cwd: directory, detached: true, stdio: ['pipe', 'pipe', 'pipe'] });
      handle.child = child;
      handle.pid = child.pid;
      this.peakProcesses = Math.max(this.peakProcesses, [...this.handles.values()].filter(item => item.pid && !item.finished).length);
      let pending = '';
      let writing = Promise.resolve();
      child.stdout.on('data', chunk => {
        writing = writing.then(() => output.write(chunk)).catch(() => { this.#emit({ laneId, kind: 'worker-log-error' }); });
        pending += chunk.toString('utf8');
        const lines = pending.split('\n');
        pending = lines.pop();
        // Bound parser memory while the original stream remains preserved.
        if (pending.length > 1024 * 1024) pending = '';
        for (const line of lines) {
          let data;
          try { data = JSON.parse(line); } catch { continue; }
          if (data.type === 'turn.started') {
            handle.turnStarted = true;
            handle.turnActive = true;
            this.peakTurns = Math.max(this.peakTurns, [...this.handles.values()].filter(item => item.turnActive && !item.finished).length);
          }
          if (['turn.completed', 'turn.failed', 'error'].includes(data.type)) handle.turnActive = false;
          if (['thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'].includes(data.type)) {
            this.#emit({ laneId, kind: 'worker-progress', event: data.type });
          } else this.#emit({ laneId, kind: 'worker-heartbeat' });
        }
      });
      // Avoid persisting unstructured stderr, which may contain local credentials.
      child.stderr.on('data', () => this.#emit({ laneId, kind: 'worker-stderr' }));
      child.stdin.on('error', () => {});
      child.once('error', () => finish(null, null, true));
      child.once('exit', (code, signal) => finish(code, signal));
      child.once('close', () => { void writing.finally(() => output.close()).catch(() => {}); });
      if (!handle.pid) throw new Error('Cannot launch installed Codex executable');
      handle.birthTick = await birthTick(handle.pid);
      if (!handle.birthTick || handle.finished) throw new Error('Codex agent exited before process ownership was recorded');
      if (handle.reason || this.closing) throw new Error('Worker stopped during process startup');
      this.#emit({ laneId, kind: 'worker-started', pid: handle.pid, birthTick: handle.birthTick });
      handle.timer = setTimeout(() => { handle.reason = 'timeout'; void this.stop(laneId); }, this.timeoutSeconds * 1000);
      child.stdin.end(assignment(this.root, sessionFile, brief));
      return { pid: handle.pid, birthTick: handle.birthTick, startedAt: handle.startedAt };
    } catch (error) {
      handle.reason ??= 'startup-failed';
      if (handle.child && !handle.finished) {
        if (handle.birthTick) await this.stop(laneId);
        else handle.child.kill('SIGTERM');
      }
      if (!handle.child) { await output?.close(); finish(null, null, true); }
      throw error;
    }
  }

  async #signal(handle, signal) {
    if (handle.finished || !handle.pid || !handle.birthTick) return;
    let current;
    try { current = await birthTick(handle.pid); } catch { return; }
    if (current !== handle.birthTick || handle.finished) return;
    try { process.kill(-handle.pid, signal); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
  }

  async #groupMembers(handle) {
    const names = await readdir('/proc');
    const members = await Promise.all(names.filter(name => /^\d+$/.test(name)).map(async name => {
      try {
        const text = await readFile(`/proc/${name}/stat`, 'utf8');
        const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
        if (fields[0] === 'Z' || Number(fields[2]) !== handle.pid || Number(fields[3]) !== handle.pid) return null;
        return { pid: Number(name), birthTick: fields[19] };
      } catch { return null; }
    }));
    return members.filter(Boolean);
  }

  async #drain(handle) {
    while (!handle.birthTick && !handle.leaderExit && !handle.finished) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    if (!handle.birthTick || handle.finished) return;
    // Stop the owned group before taking the membership snapshot so descendants
    // cannot fork between enumeration and the initial termination signal.
    if (await birthTick(handle.pid).catch(() => null) !== handle.birthTick) return;
    await this.#signal(handle, 'SIGSTOP');
    let members;
    try {
      members = await this.#groupMembers(handle);
      await this.#signal(handle, 'SIGTERM');
    } finally { await this.#signal(handle, 'SIGCONT'); }
    const deadline = Date.now() + 5000;
    while (members.length) {
      const alive = (await Promise.all(members.map(async member =>
        await birthTick(member.pid).catch(() => null) === member.birthTick ? member : null))).filter(Boolean);
      if (!alive.length) return;
      // A surviving birth-matched member proves the detached group still belongs
      // to this launch, even after its leader has exited. Never use just a PGID.
      const current = await this.#groupMembers(handle);
      if (!current.some(member => alive.some(known => known.pid === member.pid && known.birthTick === member.birthTick))) return;
      members = current;
      if (Date.now() >= deadline) {
        try { process.kill(-handle.pid, 'SIGKILL'); }
        catch (error) { if (error.code !== 'ESRCH') throw error; }
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }

  async stop(laneId) {
    const handle = this.handles.get(laneId);
    if (!handle) return;
    handle.reason ??= 'stopped';
    if (!handle.child) return handle.done;
    if (!handle.stopTask) {
      handle.draining = true;
      handle.stopTask = this.#drain(handle).catch(() => {
        this.#emit({ laneId, kind: 'worker-stop-error' });
      }).finally(() => {
        handle.draining = false;
        if (handle.leaderExit) {
          const { code, signal, error } = handle.leaderExit;
          handle.finish(code, signal, error);
        }
      });
    }
    await handle.stopTask;
    return handle.done;
  }

  async close() {
    this.closing = true;
    await Promise.all([...this.handles.keys()].map(id => this.stop(id)));
  }
}
