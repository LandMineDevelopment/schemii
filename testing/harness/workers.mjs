import { spawn } from 'node:child_process';
import { mkdir, open, readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;

async function birthTick(pid) {
  const text = await readFile(`/proc/${pid}/stat`, 'utf8');
  const fields=text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
  return fields[0] === 'Z' ? null : fields[19];
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
  const assignedScenarios = brief.scenarios.filter(s => s.functional === undefined || s.functional === 'not-run' || s.visual === 'not-run');
  brief = { ...brief, scenarios: assignedScenarios };
  return `You are one independent manual UI tester assigned exclusively to this lane.
Use only the harness CLI below for browser interaction. Your session file is a scoped ownership handle: pass its path, never read or print its contents. Never inspect other lanes, credential files, environment secrets, or account passwords. Never use T3 preview, another browser, raw application APIs, network clients, arbitrary browser JavaScript, application tests, or deployment commands. Do not spawn subagents or background processes. Do not modify files or source code, or create scripts to automate a sequence or manufacture passing results. Harness screenshots, downloads, findings, and checkpoints are recorded by the CLI inside your lane artifact directory.

Write boundary: ${writeBoundary}

Read the entire assignment, including each scenario's instructions and expected data. Execute all feasible substeps in each selected scenario: normal flow, validation/error and recovery paths, persistence or reload checks, and relevant cross-feature effects. Use individual CLI actions, inspect returned snapshots and actual data, and use your image viewing tool to examine screenshots. Record an inspect receipt after actually viewing each selected screenshot; its invocation/expected/actual notes are attestation and remain subject to independent review. Desktop and mobile require separate observations and separate exact scenario IDs; never infer one viewport from another. Record concrete expected and actual results for each significant step. Report failed or blocked honestly; a later workaround does not turn a failed step into a pass. Harness capability probes are not application acceptance evidence.

Commands (execute the absolute command directly; no shell wrapper scripts):
${command} action ${session} --kind identity
${command} action ${session} --kind snapshot
${command} action ${session} --kind navigate --args-json '{"url":"/"}'
${command} action ${session} --kind resize --args-json '{"width":1280,"height":800}'
${command} action ${session} --kind click --args-json '{"role":"button","name":"EXACT VISIBLE NAME"}'
${command} action ${session} --kind type --args-json '{"role":"textbox","name":"EXACT VISIBLE NAME","value":"TEXT"}'
${command} action ${session} --kind press --args-json '{"key":"Enter"}'
${command} begin ${session} --scenario EXACT_SCENARIO_ID
${command} action ${session} --kind screenshot
${command} inspect ${session} --evidence LANE_RELATIVE_SCREENSHOT_PATH --note 'Expected and actual visual observations' --args-json '{"tool":"view_image","invocation":"Actual image-view tool invocation reference"}'
${command} action ${session} --kind upload --args-json '{"selector":"input[type=file]","fileName":"scratch.csv","content":"sku,qty,price\\nD-400,1,2.00\\n"}'
${command} heartbeat ${session}
${command} finding ${session} --scenario EXACT_SCENARIO_ID --title 'Concise observed defect' --severity medium --steps 'Exact UI steps and input data' --expected 'Expected observable behavior' --actual 'Observed behavior and persistence result' --evidence ABSOLUTE_SCREENSHOT_PATH
${command} checkpoint ${session} --scenario EXACT_SCENARIO_ID --functional passed --visual passed --note 'Expected and actual observations for this viewport' --evidence ABSOLUTE_SCREENSHOT_PATH
${command} finish ${session}

Start with identity and snapshot. For each pending scenario, begin it with its exact ID before fresh captures, resize to its exact viewport and navigate to the exact assignment url when provided, including its workspace query parameter; otherwise navigate to its product (schemii: /, schemoo: /schemoo, schemer: /schemer). Perform the scenario's complete authorized UI workflow, including its stated edge cases, then capture fresh screenshots at material states and view them. Inspect style for clipping, overlap, readability, responsive navigation, and visible errors. Use functional/visual passed, failed, or blocked separately. Do not claim visual passed without viewing fresh evidence. If an action returns a pending dialog, inspect its meaning and accept only when the exact write is authorized; otherwise dismiss it with action kind dialog and args-json {"action":"dismiss"}. Never blindly retry a potentially mutating click. If an outcome is uncertain, inspect current and persisted state before proceeding.

For each observed product defect, immediately record a finding with the exact scenario ID, severity low|medium|high|critical, repeatable UI steps, test data, expected and actual behavior, and owned screenshot or download evidence. Use a concise title and do not include credentials. Record the scenario checkpoint separately, keeping functional or visual failure even if later steps succeed. A prerequisite blocker without an observed product defect belongs in the checkpoint note. If an issue needs independent reproduction, say so in the finding's actual behavior.

Checkpoint every scenario using its exact ID and its own evidence before finish. The checkpoint note must identify covered substeps, actual results, gaps, and relevant finding titles; a single harmless click or screenshot does not constitute coverage of a multi-step scenario. If your session becomes stale, stop and report it; never recover or claim another lane yourself. Finish with a concise factual summary of tested scenarios, findings, and gaps. Do not claim application-wide correctness.

Assignment:
${JSON.stringify(brief, null, 2)}
`;
}

/** One installed Codex process per lane; no dependency on T3 subagent capacity. */
export class CodexWorkers {
  constructor({ root, runDir, model, reasoning, timeoutSeconds = 600, spawnProcess = spawn, onEvent = () => {}, onExit = () => {} }) {
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) throw new Error('Agent timeout must be positive');
    this.root = resolve(root);
    this.runDir = resolve(runDir);
    this.model = model;
    this.reasoning = reasoning;
    this.timeoutSeconds = timeoutSeconds;
    this.spawnProcess = spawnProcess;
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
    const handle = { laneId, startedAt: new Date().toISOString(), finished: false, reason: null, turnStarted: false, generation:lane.generation };
    this.handles.set(laneId, handle);
    let resolveDone;
    handle.done = new Promise(resolve => { resolveDone = resolve; });
    const exitResult = cleanup => {
      const { code, signal, error } = handle.leaderExit;
      return { laneId, generation:handle.generation, pid: handle.pid ?? null, birthTick: handle.birthTick ?? null, code, signal, reason: handle.reason ?? (error ? 'spawn-failed' : code === 0 ? 'completed' : 'agent-failed'), cleanup, turnStarted: handle.turnStarted, startedAt: handle.startedAt, endedAt: new Date().toISOString() };
    };
    const notifyExit = result => {
      try { Promise.resolve(this.onExit(result)).catch(() => {}); } catch {}
    };
    const release = () => {
      if (handle.finished) return;
      handle.finished = true;
      clearTimeout(handle.timer);
      clearInterval(handle.groupTimer);
      const result = exitResult('stopped');
      if (this.handles.get(laneId) === handle) this.handles.delete(laneId);
      resolveDone(result);
      this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-exited', reason: result.reason, code:result.code, signal:result.signal, cleanup:result.cleanup });
      notifyExit(result);
    };
    const finish = (code, signal, error = false) => {
      if (handle.finished || handle.leaderExit) return;
      handle.leaderExit = { code, signal, error };
      handle.reason ??= error ? 'spawn-failed' : code === 0 ? 'completed' : 'agent-failed';
      handle.turnActive = false;
      clearTimeout(handle.timer);clearInterval(handle.groupTimer);
      // Fence through the generation-aware controller now, while cleanup still
      // owns the execution slot, recording streams and any surviving children.
      const result = exitResult('pending');
      this.#emit({laneId,generation:handle.generation,pid:handle.pid,birthTick:handle.birthTick,kind:'worker-leader-exited',code,signal,reason:result.reason,cleanup:result.cleanup});
      notifyExit(result);
      if (!handle.child) { release();return; }
      if (!handle.draining && !handle.cleanupPending) void this.stop(laneId,handle.reason).catch(() => {});
    };
    handle.finish = finish;
    handle.release = release;
    let output;
    try {
      await mkdir(directory, { recursive: true, mode: 0o700 });
      output = await open(join(directory, 'agent-events.jsonl'), 'a', 0o600);
      if (this.closing || handle.reason) throw new Error('Worker pool stopped before agent launch');
      const args = ['--no-daemon', '-a', 'never', 'exec', '--json', '--ephemeral', '--sandbox', 'workspace-write', '-c', 'sandbox_workspace_write.network_access=true', '-c', 'sandbox_workspace_write.writable_roots=[]', '-c', 'sandbox_workspace_write.exclude_slash_tmp=true', '-c', 'sandbox_workspace_write.exclude_tmpdir_env_var=true', '--skip-git-repo-check', '-C', directory, '-o', join(directory, 'agent-final.txt')];
      if (this.model) args.push('-m', this.model);
      if (this.reasoning) args.push('-c', `model_reasoning_effort=${JSON.stringify(this.reasoning)}`);
      args.push('-');
      const child = this.spawnProcess('codex', args, { cwd: directory, detached: true, stdio: ['pipe', 'pipe', 'pipe'] });
      handle.child = child;
      handle.closed = new Promise((resolve,reject) => { handle.resolveClosed = resolve;handle.rejectClosed = reject; });
      // Exit/startup cleanup awaits this promise; attach an observer immediately
      // so an early stream failure is not an unhandled rejection.
      void handle.closed.catch(() => {});
      handle.pid = child.pid;
      this.peakProcesses = Math.max(this.peakProcesses, [...this.handles.values()].filter(item => item.pid && !item.finished).length);
      let pending = '';
      let writing = Promise.resolve();
      child.stdout.on('data', chunk => {
        writing = writing.then(() => output.write(chunk)).catch(error => { handle.recordingError ??= error.message;this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-log-error' }); });
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
            this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-progress', event: data.type });
          } else if (['item.started','item.updated','item.completed'].includes(data.type) && data.item && typeof data.item === 'object') {
            this.#emit({laneId,generation:handle.generation,pid:handle.pid,birthTick:handle.birthTick,kind:'worker-activity',event:data.type});
          } else this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-heartbeat' });
        }
      });
      // Avoid persisting unstructured stderr, which may contain local credentials.
      child.stderr.on('data', () => this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-stderr' }));
      child.stdin.on('error', () => {});
      child.once('error', () => finish(null, null, true));
      child.once('exit', (code, signal) => finish(code, signal));
      child.once('close', () => { void writing.finally(() => output.close()).then(() => {
        if (handle.recordingError) handle.rejectClosed(new Error('Worker recording is unavailable; preserve its ownership for review.'));
        else handle.resolveClosed();
      }, error => { this.#emit({laneId,generation:handle.generation,pid:handle.pid,birthTick:handle.birthTick,kind:'worker-log-error'});handle.rejectClosed(error); }); });
      if (!handle.pid) throw new Error('Cannot launch installed Codex executable');
      handle.birthTick = await birthTick(handle.pid);
      if (!handle.birthTick || handle.finished) throw new Error('Codex agent exited before process ownership was recorded');
      if (handle.reason || this.closing) throw new Error('Worker stopped during process startup');
      await this.#ownedGroup(handle);
      if (handle.leaderExit || handle.reason || this.closing) throw new Error('Worker exited during process ownership recording');
      // Observe child birth identities while the leader can still prove this
      // detached group. An unknown group after a crash is never adopted blindly.
      handle.groupTimer = setInterval(() => {
        if (handle.groupScan || handle.draining || handle.cleanupPending || handle.leaderExit) return;
        handle.groupScan = this.#ownedGroup(handle).catch(error => {
          handle.groupObservationError = error.message;
        }).finally(() => { handle.groupScan = null; });
      },1000).unref();
      this.#emit({ laneId, generation:handle.generation, pid:handle.pid, birthTick:handle.birthTick, kind: 'worker-started', pid: handle.pid, birthTick: handle.birthTick });
      handle.timer = setTimeout(() => { handle.reason = 'timeout'; void this.stop(laneId).catch(() => {}); }, this.timeoutSeconds * 1000);
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

  async #groupMembers(handle) {
    const names = await readdir('/proc');
    const members = await Promise.all(names.filter(name => /^\d+$/.test(name)).map(async name => {
      try {
        const text = await readFile(`/proc/${name}/stat`, 'utf8');
        const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
        if (fields[0] === 'Z' || Number(fields[2]) !== handle.pid || Number(fields[3]) !== handle.pid) return null;
        return { pid: Number(name), birthTick: fields[19] };
      } catch (error) { if (['ENOENT','ESRCH'].includes(error.code)) return null;throw error; }
    }));
    return members.filter(Boolean);
  }

  async #ownedGroup(handle) {
    const current = await this.#groupMembers(handle);
    const leaderMatches = current.some(owner => owner.pid === handle.pid && owner.birthTick === handle.birthTick);
    if (current.some(owner => owner.pid === handle.pid) && !leaderMatches) throw new Error('Worker process group ownership changed; preserve its reservation for inspection.');
    const memberMatches = current.some(owner => owner.pid !== handle.pid && (handle.ownedGroup || []).some(known => known.pid === owner.pid && known.birthTick === owner.birthTick));
    if (current.length && !leaderMatches && !memberMatches) throw new Error('Worker process group ownership changed; preserve its reservation for inspection.');
    handle.ownedGroup = current;
    return current;
  }

  async #signal(handle, signal) {
    const members = await this.#ownedGroup(handle);
    if (!members.length) return;
    // A birth-matched live leader or previously captured group member proves
    // ownership. Never signal a group from a lane label or a bare saved PGID.
    try { process.kill(-handle.pid, signal); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
  }

  async #drain(handle) {
    const startupDeadline = Date.now() + 1000;
    while (!handle.birthTick && !handle.leaderExit && !handle.finished) {
      if (Date.now() >= startupDeadline) throw new Error('Worker process ownership is unresolved; cleanup remains pending.');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    if (!handle.birthTick) {
      if (handle.leaderExit && (!handle.pid || !(await this.#groupMembers(handle)).length)) return;
      throw new Error('Worker process ownership is missing; cleanup remains pending.');
    }
    // Stop the owned group before inspecting it so children cannot fork between
    // ownership capture and termination. Continue it so SIGTERM can be handled.
    await this.#signal(handle, 'SIGSTOP');
    try { await this.#signal(handle, 'SIGTERM'); }
    finally { await this.#signal(handle, 'SIGCONT'); }
    const gracefulDeadline = Date.now() + 5000, finalDeadline = gracefulDeadline + 1000;
    let escalated = false;
    while ((await this.#ownedGroup(handle)).length) {
      if (!escalated && Date.now() >= gracefulDeadline) {
        await this.#signal(handle, 'SIGKILL');escalated = true;
      }
      if (Date.now() >= finalDeadline) throw new Error('Owned worker processes remain live after termination; cleanup remains pending.');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  }

  async stop(laneId, reason = 'stopped') {
    const handle = this.handles.get(laneId);
    if (!handle) return;
    handle.reason ??= reason;
    clearTimeout(handle.timer);
    clearInterval(handle.groupTimer);
    if (!handle.child) return handle.done;
    if (!handle.stopTask) {
      handle.draining = true;handle.cleanupPending = false;
      handle.stopTask = (async () => {
        try {
          await handle.groupScan;
          await this.#drain(handle);
          // ChildProcess exit reaps the leader; close confirms inherited stdout
          // descriptors and the private recording have drained before release.
          let timer;
          try { await Promise.race([handle.closed, new Promise((_,reject) => {timer=setTimeout(() => reject(new Error('Worker recording closure is pending.')),1000);})]); }
          finally { clearTimeout(timer); }
          if (!handle.leaderExit) throw new Error('Worker exit has not been reaped; cleanup remains pending.');
          handle.draining = false;
          handle.release();
          return handle.done;
        } catch (error) {
          handle.draining = false;handle.cleanupPending = true;
          handle.cleanupError = error.message;
          this.#emit({laneId,generation:handle.generation,pid:handle.pid,birthTick:handle.birthTick,kind:'worker-stop-error',cleanup:'pending',reason:handle.reason,error:handle.cleanupError});
          throw error;
        }
      })();
    }
    try { return await handle.stopTask; }
    catch(error) { handle.stopTask=null;throw error; }
  }

  async close() {
    this.closing = true;
    await Promise.all([...this.handles.keys()].map(id => this.stop(id)));
  }
}
