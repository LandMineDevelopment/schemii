import { execFile, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';
import { isDeepStrictEqual } from 'node:util';
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { open, readFile, readdir, lstat, realpath, readlink, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute, dirname, basename } from 'node:path';
import { readWorkspaceNavigation } from '../../src/schemii/schemii/web/assets/workspace-navigation.js';
import { workerCleanupReasons } from './leases.mjs';

export const nativeMode = run => run.browser === 'native';
export function nativeBrowserRoots(root) {
  const list=execFileSync('git',['-C',root,'worktree','list','--porcelain','-z'],{encoding:'utf8'});
  return list.split('\0').filter(field=>field.startsWith('worktree ')).map(field=>join(field.slice(9),'artifacts/native-browsers'));
}
async function selectBrowserRoot(directory,roots) {
  const parent=dirname(resolve(directory));
  for(const root of Array.isArray(roots)?roots:[roots]) {
    if(parent===resolve(root))return root;
  }
  throw new Error('Native output must belong to an actual registered project checkout.');
}
const requiredText = (value, name, limit = 4000) => {
  if (typeof value !== 'string' || !value.trim() || value.length > limit) throw new Error(`${name} requires bounded nonblank text.`);
  return value.trim();
};
export async function processIdentity(pid) {
  try {
    const text = await readFile(`/proc/${pid}/stat`, 'utf8');
    const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
    if (fields[0] === 'Z') return null;
    return { pid: Number(pid), parent: Number(fields[1]), birthTick: fields[19] };
  } catch (error) { if (['ENOENT','ESRCH'].includes(error.code)) return null; throw error; }
}
async function literalPath(path, base) {
  const target = resolve(path), root = await realpath(base);
  const rel = relative(root, target);
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('Native output must stay inside its recorded connection directory.');
  let part = root;
  for (const name of rel.split('/')) {
    part = join(part, name);
    if ((await lstat(part)).isSymbolicLink()) throw new Error('Native output paths cannot contain symlinks.');
  }
  return target;
}
export async function readNativeSession(directory, browserRoot) {
  browserRoot=await selectBrowserRoot(directory,browserRoot);
  const target = await literalPath(directory, browserRoot);
  if (dirname(target) !== await realpath(browserRoot) || !/^session-[a-f0-9]{32}$/.test(target.split('/').at(-1))) throw new Error('Bind the exact native connection directory from its own browser output.');
  const info = await lstat(target);
  if (!info.isDirectory() || (info.mode & 0o077)) throw new Error('Native connection metadata must be private.');
  const metadataPath = await literalPath(join(target, 'session.json'), target);
  const metadataInfo = await lstat(metadataPath);
  if (!metadataInfo.isFile() || (metadataInfo.mode & 0o077) || metadataInfo.size > 4096) throw new Error('Native metadata must be a bounded private regular file.');
  const outputInfo = await lstat(await literalPath(join(target,'output'),target));
  if (!outputInfo.isDirectory() || (outputInfo.mode & 0o077)) throw new Error('Native output must be a private directory.');
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8'));
  if (metadata.schema !== 1 || metadata.session_id !== target.split('/').at(-1) || metadata.state !== 'running') throw new Error('Native connection is not running.');
  for (const [pid, tick] of [[metadata.pid, metadata.birth_tick], [metadata.child_pid, metadata.child_birth_tick]]) {
    if (!Number.isSafeInteger(pid) || pid < 1 || String(tick) !== (await processIdentity(pid))?.birthTick) throw new Error('Native connection process ownership is stale.');
  }
  return { directory: target, sessionId: metadata.session_id, pid: metadata.pid, birthTick: String(metadata.birth_tick), childPid: metadata.child_pid, childBirthTick: String(metadata.child_birth_tick), guardianPid: metadata.guardian_pid, guardianBirthTick: String(metadata.guardian_birth_tick), dev: info.dev, ino: info.ino };
}
export async function bindNative(run, lane, directory, browserRoot) {
  if (!nativeMode(run) || run.controller !== 't3' || lane.status !== 'claimed' || !lane.agent) throw new Error('Native binding requires a claimed native T3 lane.');
  if (lane.native && !lane.native.closedAt) throw new Error('This lane already owns a native connection.');
  const connection = await readNativeSession(directory, browserRoot);
  if (run.lanes.some(peer => peer !== lane && peer.native?.sessionId === connection.sessionId)) throw new Error('Native connections cannot be shared across accounts or reviewer lanes.');
  const browserProcesses = await nativeBrowserProcesses(connection);
  const bindingPath = join(connection.directory, 'qa-binding.json');
  const bindingHandle = await open(bindingPath,'wx',0o600).catch(error=>{if(error.code==='EEXIST')throw new Error('Native connection is already assigned to a QA run; use the new worker own connection.');throw error;});
  try { await bindingHandle.writeFile(JSON.stringify({run:run.id,lane:lane.id,agent:lane.agent,generation:lane.generation})); } finally { await bindingHandle.close(); }
  lane.native = { ...connection, browserProcesses, agent: lane.agent, generation: lane.generation, boundAt: new Date().toISOString(), assurance: 'cooperative-worker-binding' };
  return lane.native;
}
export async function assertNativeConnection(lane, browserRoot) {
  const binding = lane.native;
  if (!binding || binding.closedAt || binding.agent !== lane.agent || binding.generation !== lane.generation) throw new Error('Bind this claimed generation to its own native connection before login or evidence.');
  const actual = await readNativeSession(binding.directory, browserRoot);
  if (actual.dev !== binding.dev || actual.ino !== binding.ino || actual.pid !== binding.pid || actual.birthTick !== binding.birthTick || actual.childPid !== binding.childPid || actual.childBirthTick !== binding.childBirthTick) throw new Error('Native connection ownership changed.');
  return binding;
}
export async function nativeBrowserProcesses(binding) {
  const owner = await processIdentity(binding.childPid);
  if (!owner || owner.birthTick !== binding.childBirthTick) return [];
  const rows = (await Promise.all((await readdir('/proc')).filter(name => /^\d+$/.test(name)).map(processIdentity))).filter(Boolean);
  const descendants = new Set([binding.childPid]);
  for (let changed = true; changed;) {
    changed = false;
    for (const row of rows) if (descendants.has(row.parent) && !descendants.has(row.pid)) { descendants.add(row.pid); changed = true; }
  }
  const browsers = [];
  for (const row of rows) if (descendants.has(row.pid)) {
    const executable = basename(await readlink(`/proc/${row.pid}/exe`).catch(() => ''));
    // MCP's own argv includes --browser chromium; arguments do not identify
    // Chromium processes. Inspect the executable, not an arbitrary argv token.
    if (['chrome','chromium','chrome-headless-shell','chrome_crashpad_handler'].includes(executable)) browsers.push(row);
  }
  return browsers;
}
export async function closeNativeReceipt(lane) {
  if (!lane.native) return { state: 'unbound' };
  const known = await Promise.all((lane.native.browserProcesses || []).map(async owner => (await processIdentity(owner.pid))?.birthTick === owner.birthTick ? owner : null));
  if (known.some(Boolean) || (await nativeBrowserProcesses(lane.native)).length) throw new Error('Call browser_close in the owning native thread and wait for its Chromium processes to exit before finish/cleanup.');
  lane.native.closedAt ||= new Date().toISOString();
  const live = await processIdentity(lane.native.pid), child = await processIdentity(lane.native.childPid);
  const guardian = lane.native.guardianPid ? await processIdentity(lane.native.guardianPid) : null;
  const outputExists = await lstat(lane.native.directory).then(() => true, error => { if(error.code === 'ENOENT')return false;throw error; });
  lane.native.cleanup = { context: 'closed-observed', transport: live?.birthTick === lane.native.birthTick || child?.birthTick === lane.native.childBirthTick ? 'live' : 'stopped', guardian: guardian?.birthTick === lane.native.guardianBirthTick ? 'live' : 'stopped', temporaryOutput: outputExists ? 'present' : 'removed-observed', checkedAt: new Date().toISOString() };
  return lane.native.cleanup;
}
// The interface has no native session-close control. Teardown therefore ends
// only the bound extension transport; it does not close an agent/thread. pidfd
// signaling cannot hit a newly reused PID between ownership checking and SIGTERM.
const nativeCleanupComplete = cleanup => cleanup.transport === 'stopped' && cleanup.guardian === 'stopped' && cleanup.temporaryOutput === 'removed-observed';
function terminalReleaseRequired() {
  const error = new Error('Native generation output was removed, but its captured transport is still live. Call browser_release with no arguments in the owning native thread, then retry native-release. If unavailable, preserve the cleanup ledger and deployment lease; missing output cannot authorize terminating a reopened endpoint.');
  error.code = 'NATIVE_TERMINAL_RELEASE_REQUIRED';
  return error;
}
async function validateRecordedNativeOwnership(binding, browserRoot) {
  const selected = await selectBrowserRoot(binding.directory, browserRoot);
  if (dirname(binding.directory) !== await realpath(selected) || basename(binding.directory) !== binding.sessionId || !/^session-[a-f0-9]{32}$/.test(binding.sessionId)) throw new Error('Native cleanup requires its exact recorded connection directory.');
  const owners = [[binding.pid,binding.birthTick],[binding.childPid,binding.childBirthTick]];
  if (binding.guardianPid !== undefined) owners.push([binding.guardianPid,binding.guardianBirthTick]);
  if (!Array.isArray(binding.browserProcesses)) throw new Error('Native browser ownership is incomplete; preserve cleanup for inspection.');
  owners.push(...binding.browserProcesses.map(owner=>[owner.pid,owner.birthTick]));
  if (owners.some(([pid,tick])=>!Number.isSafeInteger(pid)||pid<1||typeof tick!=='string'||!/^\d+$/.test(tick))) throw new Error('Native process ownership is incomplete; preserve cleanup for inspection.');
}
export async function releaseNativeTransport(lane, browserRoot, { timeoutMs = 12000, persist } = {}) {
  if (!lane.native) return { state: 'unbound' };
  const attempt = { startedAt:new Date().toISOString(), status:'pending' };
  (lane.native.releaseAttempts ||= []).push(attempt);
  try {
    await validateRecordedNativeOwnership(lane.native, browserRoot);
    let cleanup = await closeNativeReceipt(lane);
    if (nativeCleanupComplete(cleanup)) { attempt.status='complete'; return cleanup; }
    if (cleanup.transport === 'live') {
      if (cleanup.temporaryOutput === 'removed-observed') throw terminalReleaseRequired();
      let actual;
      try { actual = await readNativeSession(lane.native.directory, browserRoot); }
      catch (error) {
        if (error.code !== 'ENOENT') throw error;
        // Close/release may finish during validation. Missing inner metadata or
        // a live replacement remains unverified; never signal from old births.
        cleanup = await closeNativeReceipt(lane);
        if (nativeCleanupComplete(cleanup)) { attempt.status='complete'; return cleanup; }
        if (cleanup.temporaryOutput === 'removed-observed') throw terminalReleaseRequired();
        throw error;
      }
      for (const field of ['dev','ino','pid','birthTick','childPid','childBirthTick']) if (actual[field] !== lane.native[field]) throw new Error('Native transport ownership changed; preserve this connection for inspection.');
      const script = `import os,signal,sys
pid=int(sys.argv[1]);tick=sys.argv[2]
fd=os.pidfd_open(pid)
try:
 fields=open('/proc/%s/stat'%pid).read().rsplit(')',1)[1].split()
 if fields[0]=='Z' or fields[19]!=tick: raise RuntimeError('Native supervisor ownership changed')
 signal.pidfd_send_signal(fd,signal.SIGTERM)
finally: os.close(fd)
`;
      await promisify(execFile)('python3',['-c',script,String(actual.pid),actual.birthTick],{timeout:5000});
      lane.native.termination = { mode:'harness-owned-extension-SIGTERM', agentSessionClosure:'unavailable-in-current-interface', at:new Date().toISOString() };
    }
    const deadline = Date.now() + timeoutMs;
    do {
      cleanup = await closeNativeReceipt(lane);
      if (nativeCleanupComplete(cleanup)) { attempt.status='complete'; return cleanup; }
      await new Promise(resolve => setTimeout(resolve,100));
    } while (Date.now() < deadline);
    throw new Error('Native transport cleanup is pending; preserve its ledger and deployment lease. No files were manually deleted.');
  } catch (error) {
    attempt.error = String(error.message).split('\n')[0].slice(0,2000);
    throw error;
  } finally {
    attempt.finishedAt = new Date().toISOString();
    if (persist) await persist();
  }
}
function scenarioURL(run, lane, scenario, expectedState, requestedURL) {
  if (expectedState === 'denied') {
    if (!(lane.deniedProducts || []).includes(scenario.product)) throw new Error('Access denial must be explicitly expected by this lane.');
    return new URL('/account', run.baseURL).href;
  }
  if (expectedState !== 'product') throw new Error('Scenario state must be product or explicitly expected denied.');
  if (requestedURL) {
    const requested = new URL(requestedURL,run.baseURL), workspace = requested.searchParams.get('workspace');
    const allowed = new Set([...Object.entries(lane.resources || {}).filter(([key,value]) => key.endsWith('WorkspaceId') && typeof value === 'string').map(([,value]) => value), ...(lane.resources?.cleanupReceipts || []).filter(receipt => receipt.kind === 'workspace').map(receipt => receipt.id)]);
    if (scenario.product !== 'schemii' || requested.origin !== new URL(run.baseURL).origin || requested.pathname !== '/' || [...requested.searchParams.keys()].length !== 1 || !allowed.has(workspace) || requested.username || requested.password) throw new Error('Scenario URL can select only an explicitly assigned or creation-receipted owned workspace.');
    return requested.href;
  }
  const target = scenario.url || lane.url || (scenario.product === 'schemii' ? '/' : `/${scenario.product}`);
  const url = new URL(target, run.baseURL);
  if (url.origin !== new URL(run.baseURL).origin || url.username || url.password) throw new Error('Scenario route must stay on its assigned application origin.');
  return url.href;
}
export function beginScenario(run, lane, scenarioId, request = {}, now = new Date().toISOString()) {
  const scenario = lane.scenarios.find(item => item.id === scenarioId);
  if (!scenario) throw new Error('Begin requires an assigned scenario.');
  if (!run.deployment?.identity?.fingerprint) throw new Error('Scenario requires a verified source identity.');
  const url = scenarioURL(run, lane, scenario, request.expectedState || 'product', request.url);
  const session = { id: randomUUID(), scenario: scenario.id, generation: lane.generation, agent: lane.agent, startedAt: now,
    expectedState: request.expectedState || 'product', url, viewport: scenario.viewport, source: run.deployment.identity.fingerprint,
    resources: scenario.resources || lane.resources, phase: 'scenario' };
  lane.currentScenario = session;
  return session;
}
const selectionParameters = new Set(['layer', 'tableId', 'table', 'viewId', 'view', 'viewKind']);
function captureRouteMatches(lane, active, actual, expected) {
  if (actual.origin !== expected.origin || actual.pathname !== expected.pathname || actual.hash !== expected.hash || actual.username || actual.password) return false;
  const actualEntries = [...actual.searchParams];
  if (new Set(actualEntries.map(([name]) => name)).size !== actualEntries.length) return false;
  if (actual.search === expected.search) return true;
  const scenario = lane.scenarios.find(item => item.id === active.scenario);
  if (active.expectedState !== 'product' || scenario?.product !== 'schemii' || expected.pathname !== '/') return false;
  const assigned = readWorkspaceNavigation(expected), selected = readWorkspaceNavigation(actual);
  if (!assigned.workspaceId || selected.workspaceId !== assigned.workspaceId) return false;
  for (const [name, value] of actualEntries) {
    if (selectionParameters.has(name) && selected[name] !== value) return false;
  }
  // An explicitly assigned selection remains part of its scenario's expected state.
  for (const name of selectionParameters) {
    if (expected.searchParams.has(name) && actual.searchParams.get(name) !== expected.searchParams.get(name)) return false;
  }
  const fixedQuery = url => [...url.searchParams].filter(([name]) => !selectionParameters.has(name)).sort(([a], [b]) => a.localeCompare(b));
  return isDeepStrictEqual(fixedQuery(actual), fixedQuery(expected));
}
export function assertCaptureState(run, lane, input, now = new Date().toISOString()) {
  const active = lane.currentScenario;
  if (!active || active.phase !== 'scenario' || active.generation !== lane.generation || active.agent !== lane.agent || active.source !== run.deployment?.identity?.fingerprint) throw new Error('Evidence requires a current scenario-begin in this claimed generation/source.');
  const actual = new URL(input.url, run.baseURL), expected = new URL(active.url);
  if (!captureRouteMatches(lane, active, actual, expected)) throw new Error('Capture route/resource does not match the current assigned scenario.');
  if (input.viewport?.width !== active.viewport.width || input.viewport?.height !== active.viewport.height) throw new Error('Capture viewport does not match the current assigned scenario.');
  return { scenario: active.scenario, scenarioAttempt: active.id, generation: active.generation, agent: lane.agent, source: active.source,
    resources: active.resources, phase: 'scenario', url: actual.href, viewport: { ...active.viewport }, at: now };
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export async function importNativeFile(run, lane, file, { runDir, browserRoot, kind = 'image', url, viewport }) {
  const binding = await assertNativeConnection(lane, browserRoot);
  binding.browserProcesses = [...new Map([...(binding.browserProcesses || []), ...await nativeBrowserProcesses(binding)].map(owner => [`${owner.pid}:${owner.birthTick}`, owner])).values()];
  const state = assertCaptureState(run, lane, { url, viewport });
  const source = await literalPath(file, join(binding.directory, 'output'));
  const handle = await open(source, constants.O_RDONLY | constants.O_NOFOLLOW);
  let bytes, info;
  try {
    info = await handle.stat();
    if (!info.isFile() || !info.size || info.size > 20 * 1024 * 1024 || info.mtimeMs < Date.parse(lane.currentScenario.startedAt)) throw new Error('Evidence must be fresh, bounded native output captured after scenario-begin.');
    bytes = await handle.readFile();
  } finally { await handle.close(); }
  let extension;
  if (kind === 'image') {
    if (bytes.length < 24 || bytes.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Native visual evidence must be a PNG screenshot.');
    const width = bytes.readUInt32BE(16), height = bytes.readUInt32BE(20);
    if (width !== viewport.width || height !== viewport.height) throw new Error('PNG dimensions must match the scenario viewport; use a viewport screenshot at CSS scale.');
    extension = 'png';
  } else if (kind === 'download') {
    extension = source.split('.').at(-1).toLowerCase();
    if (!['csv', 'json', 'txt', 'sql'].includes(extension)) throw new Error('Download inspection supports bounded CSV, JSON, SQL or text.');
  } else throw new Error('Unsupported native evidence kind.');
  const target = join(runDir, lane.id, `${kind}-${randomUUID()}.${extension}`);
  await mkdir(dirname(target), { recursive: true, mode: 0o700 });
  await writeFile(target, bytes, { mode: 0o600, flag: 'wx' });
  const capture = { ...state, kind, path: relative(runDir, target), sha256: hash(bytes), bytes: bytes.length, nativeSession: binding.sessionId, outputMtime: new Date(info.mtimeMs).toISOString(), provenance: 'native-output-file-and-worker-observation' };
  lane.captures ||= []; lane.captures.push(capture);
  return capture;
}
export async function recordNativeAuthentication(run, lane, input, options) {
  await assertNativeConnection(lane,options.browserRoot);
  if (input.username?.toLowerCase() !== lane.username.toLowerCase() || new URL(input.url,run.baseURL).href !== new URL('/account',run.baseURL).href) throw new Error('Record the assigned account identity from its visible signed-in account page.');
  const prior = lane.currentScenario;
  lane.currentScenario = { id:randomUUID(),scenario:'authentication',phase:'scenario',generation:lane.generation,agent:lane.agent,startedAt:lane.native.boundAt,url:new URL('/account',run.baseURL).href,viewport:input.viewport,source:run.deployment.identity.fingerprint,resources:lane.resources };
  try {
    const capture = await importNativeFile(run,lane,input.file,{...options,url:input.url,viewport:input.viewport});
    capture.phase = 'authentication';
    lane.native.authentication = { username:lane.username,generation:lane.generation,source:run.deployment.identity.fingerprint,evidence:capture.path,invocation:requiredText(input.invocation,'Visible account snapshot invocation',1000),note:requiredText(input.note,'Visible authentication observation'),at:new Date().toISOString(),assurance:'visible-UI-worker-attestation-requires-independent-review' };
    return lane.native.authentication;
  } finally { lane.currentScenario = prior; }
}
export async function recordNativeResource(run,lane,input,options) {
  await assertNativeConnection(lane,options.browserRoot);
  const prefix = lane.resources?.scratchPrefix;
  if (input.kind !== 'workspace' || !/^ws_[0-9a-f]{32}$/.test(input.id || '') || !prefix || typeof input.name !== 'string' || !input.name.startsWith(prefix) || !lane.writeAuthorization?.enabled || !lane.writeAuthorization.operations?.some(operation => /create/i.test(operation))) throw new Error('Resource receipt requires an explicitly authorized new prefixed owned workspace.');
  const created = Date.parse(input.createdAt), started = Date.parse(run.createdAt);
  if (!Number.isFinite(created) || !Number.isFinite(started) || created < started || created > Date.now()+5000) throw new Error('Resource creation timestamp must belong to this run.');
  const url = new URL(input.url,run.baseURL);
  if (url.origin !== new URL(run.baseURL).origin || url.pathname !== '/' || [...url.searchParams.keys()].length !== 1 || url.searchParams.get('workspace') !== input.id || url.username || url.password) throw new Error('Resource route must identify the exact newly created workspace.');
  if ((lane.resources.cleanupReceipts || []).some(receipt => receipt.id === input.id)) throw new Error('Resource creation was already recorded; inspect saved state instead of replaying it.');
  if (run.lanes.some(peer => peer !== lane && (Object.values(peer.resources || {}).includes(input.id) || peer.resources?.cleanupReceipts?.some(receipt => receipt.id === input.id)))) throw new Error('Resource belongs to another lane.');
  const prior = lane.currentScenario;
  lane.currentScenario = { id:randomUUID(),scenario:'resource-creation',phase:'scenario',generation:lane.generation,agent:lane.agent,startedAt:input.createdAt,url:url.href,viewport:input.viewport,source:run.deployment.identity.fingerprint,resources:{workspace:input.id} };
  try {
    const capture = await importNativeFile(run,lane,input.file,{...options,url:url.href,viewport:input.viewport});capture.phase='resource-receipt';
    const receipt = {kind:'workspace',id:input.id,name:requiredText(input.name,'Resource name',200),createdAt:input.createdAt,agent:lane.agent,generation:lane.generation,source:run.deployment.identity.fingerprint,evidence:capture.path,
      invocation:requiredText(input.invocation,'Creation UI observation invocation',1000),note:requiredText(input.note,'Creation expected/actual observation'),assurance:'worker-observation-rechecked-by-scoped-cleanup',at:new Date().toISOString()};
    lane.resources.cleanupReceipts ||= [];lane.resources.cleanupReceipts.push(receipt);return receipt;
  } finally { lane.currentScenario=prior; }
}
export function currentCapture(run, lane, path, { inspection = false } = {}) {
  const active = lane.currentScenario;
  const capture = (lane.captures || []).find(item => item.path === path);
  if (!active || !capture || capture.kind !== 'image' || capture.phase !== 'scenario' || capture.scenario !== active.scenario || capture.scenarioAttempt !== active.id || capture.generation !== lane.generation || capture.agent !== lane.agent || capture.source !== run.deployment?.identity?.fingerprint) throw new Error('Visual evidence must belong to this current scenario, generation, resource and source.');
  if (inspection && !(capture.inspections || []).some(item => item.agent === lane.agent && item.sha256 === capture.sha256)) throw new Error('Visual evidence needs an explicit image delivery/view receipt and expected/actual inspection.');
  return capture;
}
export function inspectionReceipt(capture, agent, input) {
  if (!['native-inline-image', 'view_image'].includes(input.tool)) throw new Error('Image inspection must record native inline delivery or view_image invocation.');
  const receipt = { id: randomUUID(), agent, tool: input.tool, invocation: requiredText(input.invocation, 'Image invocation', 1000), note: requiredText(input.note, 'Expected/actual image inspection'), sha256: capture.sha256, at: new Date().toISOString(), assurance: 'worker-attestation-requires-independent-review' };
  capture.inspections ||= []; capture.inspections.push(receipt);
  return receipt;
}
export async function inspectDownload(capture, directory, input) {
  if (capture.kind !== 'download') throw new Error('Download inspection requires an imported download.');
  const path = await literalPath(resolve(directory, capture.path), directory);
  const bytes = await readFile(path);
  if (hash(bytes) !== capture.sha256 || bytes.length !== capture.bytes) throw new Error('Download changed after capture.');
  if (bytes.length > 1024 * 1024 || bytes.includes(0)) throw new Error('Download text inspection is bounded to 1 MiB.');
  const expected = input.expected;
  if (!expected || typeof expected !== 'object' || Array.isArray(expected) || !Object.keys(expected).length) throw new Error('Download requires explicit expected values, bytes, hash or row count.');
  let checked = false;
  if (expected.sha256 !== undefined) { checked = true; if (expected.sha256 !== capture.sha256) throw new Error('Download hash did not match expected contents.'); }
  if (expected.bytes !== undefined) { checked = true; if (expected.bytes !== bytes.length) throw new Error('Download bytes did not match expected contents.'); }
  const text = bytes.toString('utf8');
  if (expected.text !== undefined) { checked = true; if (expected.text !== text) throw new Error('Download text did not match expected contents.'); }
  if (expected.json !== undefined) {
    checked = true;
    if (!isDeepStrictEqual(JSON.parse(text), expected.json)) throw new Error('Download JSON did not match expected values.');
  }
  if (!checked) throw new Error('Supported download oracles: exact text/JSON, bytes or SHA256.');
  const receipt = { at: new Date().toISOString(), sha256: capture.sha256, bytes: bytes.length, expectedSha256: hash(Buffer.from(JSON.stringify(expected))), result: 'passed' };
  capture.contentInspection = receipt; return receipt;
}
export function pendingScenarios(lane) {
  return lane.scenarios.filter(scenario => scenario.functional === 'not-run' || scenario.visual === 'not-run');
}
export function recordReview(run, reviewer, target, scenarioId, input) {
  if (reviewer.role !== 'reviewer' || reviewer === target || reviewer.agent === target.agent || reviewer.username.toLowerCase() === target.username.toLowerCase()) throw new Error('Independent review requires the assigned distinct reviewer lane, agent and account.');
  if (nativeMode(run) && (!reviewer.native || reviewer.native.sessionId === target.native?.sessionId)) throw new Error('Independent review requires a separately bound native connection.');
  const scenario = target.scenarios.find(item => item.id === scenarioId), attempt = scenario?.attempts?.at(-1);
  if (!attempt || !attempt.id || attempt.source !== run.deployment?.identity?.fingerprint) throw new Error('Review requires a recorded current-source attempt.');
  if (!['accepted', 'confirmed-defect', 'prerequisite-mistake', 'unsupported-action', 'not-reproduced'].includes(input.verdict)) throw new Error('Review needs an explicit adjudication verdict.');
  const captures = (target.captures || []).filter(capture => attempt.evidence.includes(capture.path) && capture.phase === 'scenario' && capture.scenario === scenarioId && capture.generation === attempt.generation && capture.scenarioAttempt === attempt.scenarioAttempt && capture.source === attempt.source && capture.agent === attempt.reviewer);
  if (input.verdict === 'accepted' && (attempt.functional !== 'passed' || attempt.visual !== 'passed')) throw new Error('A failed or blocked attempt cannot become accepted.');
  if (!captures.some(capture => capture.kind === 'image' && (capture.inspections || []).some(item => item.agent === reviewer.agent && item.sha256 === capture.sha256))) throw new Error('Reviewer must inspect the target attempt image and record its hash-bound receipt first.');
  const receipt = { id: randomUUID(), reviewerLane: reviewer.id, agent: reviewer.agent, username: reviewer.username, nativeSession: reviewer.native?.sessionId,
    targetLane: target.id, scenario: scenarioId, attempt: attempt.id, source: run.deployment.identity.fingerprint,
    verdict: input.verdict, note: requiredText(input.note, 'Review expected/actual reason'), evidence: input.evidence || [], provenance: input.provenance || 'assigned-reviewer-session', at: new Date().toISOString() };
  if (input.finding) {
    if (input.verdict === 'accepted') throw new Error('Finding adjudication needs a defect/prerequisite/unsupported/not-reproduced verdict.');
    const finding = (run.findings || []).find(item => item.id === input.finding && item.lane === target.id && item.scenario === scenarioId);
    if (!finding) throw new Error('Review finding must belong to the target scenario.');
    if (!receipt.evidence.length) throw new Error('Finding adjudication requires reviewer-owned reproduction evidence.');
    finding.reviews ||= []; finding.reviews.push(receipt); finding.verificationStatus = input.verdict;
  }
  run.reviews ||= []; run.reviews.push(receipt);
  return receipt;
}
export function acceptance(run) {
  const testers = run.lanes.filter(lane => lane.role !== 'reviewer');
  const reasons = [];
  let reviewed = 0;
  if(run.recordingStatus === 'unavailable')reasons.push('evidence recording unavailable');
  for (const lane of run.lanes) for (const reason of workerCleanupReasons(lane)) reasons.push(`${lane.id}: ${reason}`);
  for (const lane of testers) for (const scenario of lane.scenarios) {
    const attempt = scenario.attempts?.at(-1);
    if (scenario.functional !== 'passed' || scenario.visual !== 'passed') reasons.push(`${lane.id}/${scenario.id}: execution gap`);
    const review = attempt?.id ? (run.reviews || []).filter(review => review.targetLane === lane.id && review.scenario === scenario.id && review.attempt === attempt.id && review.source === run.deployment?.identity?.fingerprint && review.agent !== lane.agent).at(-1) : null;
    if (review?.verdict === 'accepted') reviewed++;
    else reasons.push(`${lane.id}/${scenario.id}: independent review pending${review ? ` (${review.verdict})` : ''}`);
  }
  for (const finding of run.findings || []) {
    if (!finding.reviews?.length || finding.verificationStatus === 'unverified') reasons.push(`${finding.id}: finding adjudication pending`);
    if (finding.verificationStatus === 'confirmed-defect' || finding.reviews?.some(review=>review.verdict==='confirmed-defect')) reasons.push(`${finding.id}: confirmed defect remains unresolved; independent remediation verification required`);
  }
  if (nativeMode(run)) for (const lane of testers) if(!lane.native?.authentication || lane.native.authentication.source !== run.deployment?.identity?.fingerprint)reasons.push(`${lane.id}: native authenticated UI observation pending`);
  if (nativeMode(run)) for (const lane of run.lanes) if (lane.native && (lane.native.cleanup?.transport !== 'stopped' || lane.native.cleanup?.guardian !== 'stopped' || lane.native.cleanup?.temporaryOutput !== 'removed-observed' || lane.native.releaseAttempts?.at(-1)?.status === 'pending')) reasons.push(`${lane.id}: native transport cleanup pending`);
  return { status: reasons.length ? 'review-pending' : 'reviewed-acceptance', reasons, intended: testers.reduce((sum, lane) => sum + lane.scenarios.length, 0), completed: testers.reduce((sum, lane) => sum + lane.scenarios.filter(s => s.functional !== 'not-run' && s.visual !== 'not-run').length, 0), reviewed };
}

/** Fence before any closure prerequisite, so interrupted owners can be released. */
export async function recoverNativeLane(run,lane,{invalidate,persist,drain,closeOldTransport,prepare}) {
  if(!nativeMode(run)||run.controller!=='t3'||['complete','queued'].includes(lane.status))throw new Error('Native recovery requires an active, paused or blocked native lane.');
  const owner={agent:lane.agent,generation:lane.generation};
  lane.status='paused';lane.generation++;invalidate(lane);
  lane.recovery={status:'fenced',...owner,pending:pendingScenarios(lane).map(scenario=>scenario.id),reconciliationRequired:true,at:new Date().toISOString()};
  await persist();
  try {
    await drain();
    if(lane.native)await closeOldTransport(lane);
    await prepare(lane);
    lane.recovery.status='ready-for-owned-state-reconciliation';
    await persist();
    return {lane:lane.id,status:lane.status,generation:lane.generation,pending:lane.recovery.pending,reconcile:'Inspect saved state before repeating an uncertain write.'};
  } catch(error) {
    lane.status='paused';lane.recovery.status='blocked';
    lane.recovery.reason='Old native context/transport or stable readiness is unresolved; preserve ownership and reconcile before reassignment.';
    await persist();throw error;
  }
}
