import { execFileSync } from 'node:child_process';
import { isDeepStrictEqual } from 'node:util';
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { readFileSync, lstatSync } from 'node:fs';
import { open, readFile, readdir, lstat, realpath, readlink, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute, dirname, basename } from 'node:path';
import { readWorkspaceNavigation } from '../../src/schemii/schemii/web/assets/workspace-navigation.js';
import { workerCleanupReasons } from './leases.mjs';
import { sourceIdentity } from './deployment.mjs';

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
// Only the owning endpoint can atomically decide whether its generation is idle.
// A PID birth check cannot fence backend replacement on that reusable endpoint.
const nativeCleanupComplete = cleanup => cleanup.transport === 'stopped' && cleanup.guardian === 'stopped' && cleanup.temporaryOutput === 'removed-observed';
function terminalReleaseRequired() {
  const error = new Error('The captured native transport is still live. Call browser_release with no arguments in the owning native thread, then retry native-release. If unavailable, preserve the cleanup ledger and deployment lease; historical output or PID ownership cannot authorize terminating a reopened endpoint.');
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
      if (cleanup.temporaryOutput === 'present') {
        let actual;
        try { actual = await readNativeSession(lane.native.directory, browserRoot); }
        catch (error) {
          if (error.code !== 'ENOENT') throw error;
          // Close/release may finish during validation. Missing inner metadata
          // remains unverified; never signal from historical ownership.
          cleanup = await closeNativeReceipt(lane);
          if (nativeCleanupComplete(cleanup)) { attempt.status='complete'; return cleanup; }
          if (cleanup.temporaryOutput === 'removed-observed') throw terminalReleaseRequired();
          throw error;
        }
        for (const field of ['dev','ino','pid','birthTick','childPid','childBirthTick']) if (actual[field] !== lane.native[field]) throw new Error('Native transport ownership changed; preserve this connection for inspection.');
      }
      // Even still-present validated metadata can disappear and be replaced by
      // a new backend before a signal. Use the endpoint's supported idle fence.
      throw terminalReleaseRequired();
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
  const priorFailures = (scenario.attempts || []).slice(0, -1).filter(item => item.functional !== 'passed' || item.visual !== 'passed');
  if (input.verdict === 'accepted' && priorFailures.length && input.historyAcknowledged !== true) throw new Error('Review must explicitly acknowledge the preserved failed/blocked attempt history.');
  if (!captures.some(capture => capture.kind === 'image' && (capture.inspections || []).some(item => item.agent === reviewer.agent && item.sha256 === capture.sha256))) throw new Error('Reviewer must inspect the target attempt image and record its hash-bound receipt first.');
  const receipt = { id: randomUUID(), reviewerLane: reviewer.id, agent: reviewer.agent, username: reviewer.username, nativeSession: reviewer.native?.sessionId,
    targetLane: target.id, scenario: scenarioId, attempt: attempt.id, source: run.deployment.identity.fingerprint,
    verdict: input.verdict, note: requiredText(input.note, 'Review expected/actual reason'), evidence: input.evidence || [], provenance: input.provenance || 'assigned-reviewer-session',
    priorAttempts: priorFailures.map(item => ({ id: item.id, digest: hash(Buffer.from(JSON.stringify(item))) })), at: new Date().toISOString() };
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

export function recordCheckpoint(run, lane, scenario, input, evidence, at = new Date().toISOString()) {
  const attempt = { id: randomUUID(), generation: lane.generation, scenarioAttempt: lane.currentScenario?.id,
    source: run.deployment.identity.fingerprint, functional: input.functional, visual: input.visual,
    note: input.note, evidence, reviewer: lane.agent, at };
  scenario.attempts ||= [];
  scenario.attempts.push(attempt);
  scenario.initialResult ||= { functional: scenario.attempts[0].functional, visual: scenario.attempts[0].visual };
  Object.assign(scenario, { functional: attempt.functional, visual: attempt.visual, latestAttemptId: attempt.id,
    note: scenario.attempts.map((item, index) => `Attempt ${index + 1}: ${item.note}`).join('\n'),
    evidence: [...new Set([...(scenario.evidence || []), ...evidence])], reviewer: lane.agent, at });
  return attempt;
}

function boundFile(path) {
  for (let parent = resolve(path); parent !== dirname(parent); parent = dirname(parent)) {
    if (lstatSync(parent).isSymbolicLink()) throw new Error('Imported evidence paths cannot contain symlinks.');
  }
  const info = lstatSync(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 64 * 1024 * 1024 || (info.mode & 0o077)) throw new Error('Imported evidence must be bounded private regular files.');
  return { path, sha256: hash(readFileSync(path)) };
}
function ownedImportFile(directory, path) {
  const target = resolve(directory, path), rel = relative(resolve(directory), target);
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('Imported evidence must stay inside its original run.');
  return boundFile(target);
}
function filesUnchanged(files) {
  try { return files.every(file => boundFile(file.path).sha256 === file.sha256); } catch { return false; }
}

// Validate original local-CI records through the existing planner and timing
// validators. This executes no tests and is deliberately not another runner.
const verificationImport = `
import json,sys
from pathlib import Path
from scripts.ci.local_ci import source_identity, lane_for, evidence, verify_original_run
root,path,phase,base=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4]
r=json.loads(path.read_text()); identity=source_identity(root)
if r.get('schema')!=1 or r.get('source')!=identity: raise ValueError('wrong or stale source')
origin=verify_original_run(root,path,r)
if phase not in ('focused','final'): raise ValueError('invalid verification phase')
if r.get('base_commit')!=base: raise ValueError('receipt immutable selected base differs from frozen acceptance scope')
plan=origin['plan']
if not plan['classification']['valid'] or r.get('profile')!=plan['classification']['profile']: raise ValueError('wrong plan/profile')
required=plan['feedback']['commands'] if phase=='focused' else plan['commands']
entries=r.get('commands',[])
chosen=entries[:len(required)]
if not required or [x['command'] for x in chosen]!=required: raise ValueError('wrong required command inventory')
if phase=='final' and (len(entries)!=len(required) or r.get('status')!='passed' or r.get('acceptance') is not True or r.get('pending_layers') or r.get('pending_commands')): raise ValueError('failed pending cancelled or incomplete acceptance')
if phase=='focused' and r.get('status') not in ('passed','feedback-passed'): raise ValueError('failed or incomplete focused checks')
out=[]; files=[str(path.resolve()),origin['path']]
for entry in chosen:
 if entry.get('status')!='passed' or entry.get('exit_code')!=0: raise ValueError('required check did not pass')
 item={'command':entry['command'],'status':entry['status'],'duration_ms':entry['duration_ms']}
 key=lane_for(entry['command'])
 if key:
  relative=Path(entry.get('timing_evidence',''))
  if relative.is_absolute() or '..' in relative.parts or not relative.parts: raise ValueError('missing or unsafe original timing records')
  timing=path.parent/relative
  summary=evidence(timing,key,r['profile'],identity,r['run_id'])
  item['summary']=summary;files.append(str(timing.resolve()))
 out.append(item)
if source_identity(root)!=identity: raise ValueError('source changed during receipt import')
print(json.dumps({'source':identity,'commands':out,'files':files,'profile':r['profile'],'mode':r['mode'],'origin':{'path':origin['path'],'sha256':origin['sha256']}}))
`;
export function importVerification(run, receiptPath, { root, phase = 'final', note, python = 'python3' }) {
  const attempt = { id: randomUUID(), phase, at: new Date().toISOString(), status: 'rejected', note: requiredText(note, 'Verification provenance') };
  run.verificationHistory ||= [];
  run.verificationHistory.push(attempt);
  try {
    const before = sourceIdentity(root);
    if (before.commit !== run.deployment?.identity?.commit || before.fingerprint !== run.deployment?.identity?.fingerprint) throw new Error('Changed source requires a new prepared run; old failures cannot be relabeled.');
    const file = boundFile(resolve(receiptPath));
    attempt.receipt = file;
    const base = run.verificationBase || execFileSync('git', ['-C',root,'rev-parse','--verify','origin/main^{commit}'], {encoding:'utf8'}).trim();
    if (!/^[0-9a-f]{40}$/.test(base)) throw new Error('Verification requires the prepared frozen base commit.');
    let validated;
    try { validated = JSON.parse(execFileSync(python, ['-c', verificationImport, root, file.path, phase, base], { cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, stdio: ['ignore','pipe','pipe'] })); }
    catch { throw new Error('Original CI receipt rejected: source, inventory, outcome or timing evidence is stale, incomplete or invalid.'); }
    const files = validated.files.map(boundFile);
    if (files[0].sha256 !== file.sha256 || !isDeepStrictEqual(before, sourceIdentity(root))) throw new Error('Source or receipt changed during import.');
    Object.assign(attempt, { status: 'passed', source: validated.source, profile: validated.profile,
      commands: validated.commands, files, origin: validated.origin, assurance: 'validated-original-local-ci-receipt' });
    return attempt;
  } catch (error) { attempt.reason = error.message; throw error; }
}

function reproductionProof(run, targetLane, scenarioId) {
  const target = run.lanes.find(lane => lane.id === targetLane), scenario = target?.scenarios.find(item => item.id === scenarioId);
  const attempt = scenario?.attempts?.at(-1);
  const review = (run.reviews || []).filter(item => item.targetLane === targetLane && item.scenario === scenarioId && item.attempt === attempt?.id).at(-1);
  const reviewer = run.lanes.find(lane => lane.id === review?.reviewerLane);
  if (!attempt || attempt.source !== run.deployment?.identity?.fingerprint || attempt.functional !== 'passed' || attempt.visual !== 'passed'
      || review?.verdict !== 'accepted' || review.source !== attempt.source || !reviewer || reviewer.role !== 'reviewer'
      || review.agent !== reviewer.agent || review.agent === target.agent || review.username !== reviewer.username
      || review.username.toLowerCase() === target.username.toLowerCase()
      || (nativeMode(run) && (!review.nativeSession || review.nativeSession === target.native?.sessionId))) throw new Error('Remediation requires a current-source passing reproduction and exact independent accepted review.');
  const captures = (target.captures || []).filter(capture => attempt.evidence.includes(capture.path));
  if (!captures.some(capture => capture.kind === 'image' && capture.sha256 && capture.source === attempt.source
      && capture.scenarioAttempt === attempt.scenarioAttempt && capture.inspections?.some(item => item.agent === review.agent && item.sha256 === capture.sha256))) throw new Error('Remediation review lacks hash-bound original image inspection.');
  return { targetLane, scenarioId, attempt, review, captures: captures.map(capture => ({ path: capture.path, sha256: capture.sha256 })) };
}
function originalFindingProof(run, finding, manifestPath) {
  const source = run.deployment?.identity?.fingerprint;
  const lane = run.lanes?.find(item => item.id === finding.lane);
  const scenario = lane?.scenarios?.find(item => item.id === finding.scenario);
  if (!/^[0-9a-f]{64}$/.test(source || '') || !lane || !scenario || !finding.agent
      || !finding.username || lane.username !== finding.username) throw new Error('Original finding lacks authentic lane/scenario/source provenance; preserve it as unverified.');
  const captures = [];
  for (const path of finding.evidence) {
    const matched = (lane.captures || []).filter(capture => capture.path === path);
    if (matched.length !== 1) throw new Error('Original evidence needs one authentic retained capture.');
    const capture = matched[0];
    const attempts = (scenario.attempts || []).filter(attempt => attempt.evidence?.includes(path)
      && attempt.scenarioAttempt === capture.scenarioAttempt && attempt.generation === capture.generation
      && attempt.source === source && attempt.reviewer === finding.agent);
    if (capture.kind !== 'image' || capture.phase !== 'scenario' || capture.scenario !== finding.scenario
        || capture.source !== source || capture.agent !== finding.agent || !capture.scenarioAttempt
        || !Number.isSafeInteger(capture.generation) || capture.generation < 1
        || !/^[0-9a-f]{64}$/.test(capture.sha256 || '') || attempts.length !== 1 || !attempts[0].id
        || finding.source && finding.source !== source
        || finding.scenarioAttempt && finding.scenarioAttempt !== capture.scenarioAttempt
        || finding.generation !== undefined && finding.generation !== capture.generation) throw new Error('Original capture and checkpoint attempt/source/scenario binding is missing or changed.');
    const file = ownedImportFile(dirname(manifestPath), path);
    if (file.sha256 !== capture.sha256) throw new Error('Original evidence bytes differ from the authentic retained capture digest.');
    captures.push({ capture: { path: capture.path, kind: capture.kind, phase: capture.phase, scenario: capture.scenario,
      scenarioAttempt: capture.scenarioAttempt, generation: capture.generation, source: capture.source,
      agent: capture.agent, sha256: capture.sha256, viewport: capture.viewport, url: capture.url, resources: capture.resources }, attempt: attempts[0] });
  }
  if (new Set(finding.evidence).size !== finding.evidence.length) throw new Error('Original finding evidence cannot repeat capture paths.');
  return { finding, source, lane: lane.id, username: lane.username, scenario: scenario.id, captures };
}
export function importRemediation(original, current, findingId, input, { originalPath, currentPath }) {
  const finding = (original.findings || []).find(item => item.id === findingId);
  if (!finding || !Array.isArray(finding.evidence) || !finding.evidence.length || original.id === current.id) throw new Error('Import requires an original finding with retained evidence and a separately prepared reproduction run.');
  if (!['resolved-defect','not-reproduced','prerequisite-mistake','unsupported-action'].includes(input.disposition)) throw new Error('Import requires an explicit remediation/adjudication disposition.');
  if ((finding.verificationStatus === 'confirmed-defect' || finding.reviews?.some(review => review.verdict === 'confirmed-defect')) && input.disposition !== 'resolved-defect') throw new Error('A confirmed defect requires independently verified remediation, not reclassification.');
  if (!original.deployment?.identity?.fingerprint || original.deployment.identity.fingerprint === current.deployment?.identity?.fingerprint && input.disposition === 'resolved-defect') throw new Error('Confirmed defect remediation requires a later changed source in a new prepared run.');
  const originalProof = originalFindingProof(original, finding, originalPath);
  const proof = reproductionProof(current, input.targetLane, input.scenario);
  if (proof.review.agent === finding.agent || proof.review.username?.toLowerCase() === finding.username?.toLowerCase()) throw new Error('The original reporter cannot independently resolve their own finding.');
  const files = [
    ...finding.evidence.map(path => ownedImportFile(dirname(originalPath), path)),
    ...proof.captures.map(capture => { const file = ownedImportFile(dirname(currentPath), capture.path); if (file.sha256 !== capture.sha256) throw new Error('Reproduction image changed after review.'); return file; }),
  ];
  const receipt = { id: randomUUID(), at: new Date().toISOString(), originalRun: original.id, findingId,
    originalPath, currentPath, source: current.deployment.identity.fingerprint, disposition: input.disposition,
    provenance: requiredText(input.provenance, 'External review provenance'), note: requiredText(input.note, 'Resolution expected/actual reason'),
    originalDigest: hash(Buffer.from(JSON.stringify(originalProof))), proofDigest: hash(Buffer.from(JSON.stringify(proof))),
    reproduction: { targetLane: input.targetLane, scenario: input.scenario, attempt: proof.attempt.id, review: proof.review.id, reviewer: proof.review.agent }, files };
  original.remediations ||= []; original.remediations.push(receipt);
  current.importedFindings ||= []; current.importedFindings.push(receipt);
  return receipt;
}
function remediationCurrent(receipt, run) {
  try {
    if (receipt.source !== run.deployment?.identity?.fingerprint || !filesUnchanged(receipt.files)) return false;
    const original = JSON.parse(readFileSync(receipt.originalPath, 'utf8'));
    const current = JSON.parse(readFileSync(receipt.currentPath, 'utf8'));
    const finding = original.findings.find(item => item.id === receipt.findingId);
    return receipt.originalDigest === hash(Buffer.from(JSON.stringify(originalFindingProof(original, finding, receipt.originalPath))))
      && receipt.proofDigest === hash(Buffer.from(JSON.stringify(reproductionProof(current, receipt.reproduction.targetLane, receipt.reproduction.scenario))));
  } catch { return false; }
}
export function acceptance(run) {
  const testers = run.lanes.filter(lane => lane.role !== 'reviewer');
  const reasons = [];
  let reviewed = 0;
  if(run.recordingStatus === 'unavailable')reasons.push('evidence recording unavailable');
  if (run.acceptanceContract >= 2) {
    const final = run.verificationHistory?.filter(item => item.phase === 'final').at(-1);
    if (!final || final.status !== 'passed' || !final.origin
        || !final.files?.some(file => file.path === final.origin.path && file.sha256 === final.origin.sha256)
        || final.source?.fingerprint !== run.deployment?.identity?.fingerprint || !filesUnchanged(final.files)) reasons.push('current-source final application verification pending or changed; original runner provenance required');
  }
  for (const receipt of run.importedFindings || []) if (!remediationCurrent(receipt, run)) reasons.push(`${receipt.findingId}: imported remediation evidence changed or no longer matches current source/review`);
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
  return { status: reasons.length ? 'review-pending' : 'reviewed-acceptance', reasons, intended: testers.reduce((sum, lane) => sum + lane.scenarios.length, 0),
    dispatched: testers.reduce((sum, lane) => sum + (lane.dispatchedAt ? lane.scenarios.length : 0), 0),
    started: testers.reduce((sum, lane) => sum + lane.scenarios.filter(s => s.attempts?.length || lane.currentScenario?.scenario === s.id).length, 0),
    completed: testers.reduce((sum, lane) => sum + lane.scenarios.filter(s => s.functional !== 'not-run' && s.visual !== 'not-run').length, 0),
    firstAttemptFailures: testers.reduce((sum, lane) => sum + lane.scenarios.filter(s => s.attempts?.[0] && (s.attempts[0].functional !== 'passed' || s.attempts[0].visual !== 'passed')).length, 0), reviewed };
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
