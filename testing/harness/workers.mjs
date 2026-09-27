import { spawn } from 'node:child_process';
import { mkdir, open, readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;

async function birthTick(pid) {
  const text = await readFile(`/proc/${pid}/stat`, 'utf8');
  return text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/)[19];
}

export function workerAssignment(root, sessionFile, brief) {
  const command = `${quote(join(root, 'test.sh'))}`;
  const session = `--session-file ${quote(sessionFile)}`;
  const authorization = brief.writeAuthorization;
  const writable = authorization?.enabled === true
    && Array.isArray(authorization.resources) && authorization.resources.length > 0
    && authorization.resources.every(value => typeof value === 'string' && value.trim())
    && Array.isArray(authorization.operations) && authorization.operations.length > 0
    && authorization.operations.every(value => typeof value === 'string' && value.trim());
  const writeBoundary = writable
    ? `This lane may perform application writes through its owned browser session ONLY when the assigned scenario calls for them, against these exact disposable resources: ${JSON.stringify(authorization.resources)}. Allowed operations: ${JSON.stringify(authorization.operations)}. A generic feature name or UI capability does not expand this authorization. Inspect the saved result after each write and before any retry. Do not modify retained data, other accounts, or any resource outside this list. If a requested step falls outside this scope, record it blocked and continue with independent authorized steps.`
    : 'This lane is read-only. Do not save, create, delete, send AI chat turns, approve proposals, run write SQL, apply migrations, or change account settings or application data. Record write-dependent steps blocked.';
  return `You are one independent manual UI tester assigned exclusively to this lane.
Use only the harness CLI below for browser interaction. Your session file is a scoped ownership handle: pass its path, never read or print its contents. Never inspect other lanes, credential files, environment secrets, or account passwords. Never use T3 preview, another browser, raw application APIs, network clients, arbitrary browser JavaScript, application tests, or deployment commands. Do not spawn subagents or background processes. Do not modify files or source code, or create scripts to automate a sequence or manufacture passing results. Harness screenshots, downloads, findings, and checkpoints are recorded by the CLI inside your lane artifact directory.

Write boundary: ${writeBoundary}

Read the entire assignment, including each scenario's instructions and expected data. Execute all feasible substeps in each selected scenario: normal flow, validation/error and recovery paths, persistence or reload checks, and relevant cross-feature effects. Use individual CLI actions, inspect returned snapshots and actual data, and use your image viewing tool to examine screenshots. Desktop and mobile require separate observations and separate exact scenario IDs; never infer one viewport from another. Record concrete expected and actual results for each significant step. Report failed or blocked honestly; a later workaround does not turn a failed step into a pass. Harness capability probes are not application acceptance evidence.

Commands (execute the absolute command directly; no shell wrapper scripts):
${command} action ${session} --kind identity
${command} action ${session} --kind snapshot
${command} action ${session} --kind navigate --args-json '{"url":"/"}'
${command} action ${session} --kind resize --args-json '{"width":1280,"height":800}'
${command} action ${session} --kind click --args-json '{"role":"button","name":"EXACT VISIBLE NAME"}'
${command} action ${session} --kind type --args-json '{"role":"textbox","name":"EXACT VISIBLE NAME","value":"TEXT"}'
${command} action ${session} --kind press --args-json '{"key":"Enter"}'
${command} action ${session} --kind screenshot
${command} action ${session} --kind upload --args-json '{"selector":"input[type=file]","fileName":"scratch.csv","content":"sku,qty,price\\nD-400,1,2.00\\n"}'
${command} heartbeat ${session}
${command} finding ${session} --scenario EXACT_SCENARIO_ID --title 'Concise observed defect' --severity medium --steps 'Exact UI steps and input data' --expected 'Expected observable behavior' --actual 'Observed behavior and persistence result' --evidence ABSOLUTE_SCREENSHOT_PATH
${command} checkpoint ${session} --scenario EXACT_SCENARIO_ID --functional passed --visual passed --note 'Expected and actual observations for this viewport' --evidence ABSOLUTE_SCREENSHOT_PATH
${command} finish ${session}

Start with identity and snapshot. For EVERY scenario, resize to its exact viewport and navigate to the exact assignment url when provided, including its workspace query parameter; otherwise navigate to its product (schemii: /, schemoo: /schemoo, schemer: /schemer). Perform the scenario's complete authorized UI workflow, including its stated edge cases, then capture fresh screenshots at material states and view them. Inspect style for clipping, overlap, readability, responsive navigation, and visible errors. Use functional/visual passed, failed, or blocked separately. Do not claim visual passed without viewing fresh evidence. If an action returns a pending dialog, inspect its meaning and accept only when the exact write is authorized; otherwise dismiss it with action kind dialog and args-json {"action":"dismiss"}. Never blindly retry a potentially mutating click. If an outcome is uncertain, inspect current and persisted state before proceeding.

For each observed product defect, immediately record a finding with the exact scenario ID, severity low|medium|high|critical, repeatable UI steps, test data, expected and actual behavior, and owned screenshot or download evidence. Use a concise title and do not include credentials. Record the scenario checkpoint separately, keeping functional or visual failure even if later steps succeed. A prerequisite blocker without an observed product defect belongs in the checkpoint note. If an issue needs independent reproduction, say so in the finding's actual behavior.

Checkpoint every scenario using its exact ID and its own evidence before finish. The checkpoint note must identify covered substeps, actual results, gaps, and relevant finding titles; a single harmless click or screenshot does not constitute coverage of a multi-step scenario. If your session becomes stale, stop and report it; never recover or claim another lane yourself. Finish with a concise factual summary of tested scenarios, findings, and gaps. Do not claim application-wide correctness.

Assignment:
${JSON.stringify(brief, null, 2)}
`;
}

/** One installed Codex process per lane; no dependency on T3 subagent capacity. */
export class CodexWorkers {
  constructor({ root, runDir, model, reasoning, timeoutSeconds = 600, onEvent = () => {}, onExit = () => {} }) {
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) throw new Error('Agent timeout must be positive');
    this.root = resolve(root);
    this.runDir = resolve(runDir);
    this.model = model;
    this.reasoning = reasoning;
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
      if (this.reasoning) args.push('-c', `model_reasoning_effort=${JSON.stringify(this.reasoning)}`);
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
      child.stdin.end(workerAssignment(this.root, sessionFile, brief));
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
