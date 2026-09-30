import net from 'node:net';
import { releaseAccounts, renewWorkerLease, expireWorkerLease } from './leases.mjs';
import { chmod, readFile, writeFile, rm, stat, realpath } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute } from 'node:path';
import { randomBytes, randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import { CodexWorkers } from './workers.mjs';
import { BrowserFleet } from './browser.mjs';
import { assertScenarioPrerequisite } from './prerequisites.mjs';
import { assertLaneReadyToClaim, openProductsAfterIsolation, recoverFleetFailClosed } from './readiness.mjs';
import { startDeployment, sourceIdentity } from './deployment.mjs';
import { nativeMode, nativeBrowserRoots, recordNativeResource, recordNativeAuthentication, releaseNativeTransport, bindNative, assertNativeConnection, closeNativeReceipt, beginScenario, assertCaptureState, importNativeFile, currentCapture, inspectionReceipt, inspectDownload, pendingScenarios, recordReview, acceptance } from './native.mjs';
import { root, runPath, readJSON, privateJSON, writeJSON, credentials, reportHTML, event, stamp, findingInput } from './store.mjs';

process.umask(0o077);
const [id, mode] = process.argv.slice(2), dir = runPath(id);
const run = await readJSON(join(dir,'manifest.json'));
const control = await privateJSON(join(dir,'control.json'));
const privateConfig = await privateJSON(join(dir,'private.json'));
const accounts = await credentials(privateConfig.credentialPath);
let saving = Promise.resolve(), transition = Promise.resolve(), stopping = false, starting = true;
const operations = new Map();
const tokens = new Map();
const LEASE_MS = 10*60*1000;
const browserRoot = nativeBrowserRoots(root);
const equal = (a,b) => typeof a==='string' && typeof b==='string' && /^[a-f0-9]{64}$/.test(a) && /^[a-f0-9]{64}$/.test(b) && timingSafeEqual(Buffer.from(a),Buffer.from(b));
const terminalStatus = () => run.lanes.filter(x=>x.role!=='reviewer').every(x=>x.scenarios.every(s=>s.functional==='passed'&&s.visual==='passed'))?'execution-complete':'finished-with-gaps';
const safeError = error => error?.name === 'TimeoutError' || /Call log:|locator\.|page\./.test(error?.message || '') ? 'Browser action failed or timed out. Inspect the current page before repeating a write.' : String(error?.message || 'Unknown error').split('\n')[0];
const fleet = new BrowserFleet({baseURL:run.baseURL,runDir:dir,headless:run.headless,
  onLaunch: async ({laneId,...ownership}) => {
    const l=lane(laneId);l.browser=ownership;l.browserLaunchPending=false;await persist();
  },
  onEvent: data => {
  event(dir,{...data,source:'browser'}).catch(()=>{});
  if(data.kind==='disconnected'&&!stopping){ const l=run.lanes.find(x=>x.id===data.lane);if(l){l.status='paused';tokens.delete(l.id);l.generation++;void persist();} }
}});
const workers = new CodexWorkers({root,runDir:dir,model:run.agentModel,reasoning:run.agentReasoning,timeoutSeconds:run.agentTimeout || 600,
  onEvent: async data => {
    const l=lane(data.laneId);
    renewWorkerLease(l,data);
    if(data.kind==='worker-log-error'){l.workerRecording='unavailable';run.recordingStatus='unavailable';}
    run.workers={peakProcesses:workers.peakProcesses,peakTurns:workers.peakTurns,active:workers.handles.size};
    if(data.kind!=='worker-heartbeat'&&data.kind!=='worker-stderr')await event(dir,{...data,source:'codex'});
    await persist();
  },
  onExit: async result => {
    const l=lane(result.laneId);l.workerExit=result;
    if(l.status==='claimed'&&result.generation===l.generation) {
      l.status='paused';l.generation++;tokens.delete(l.id);
      l.error=`Agent exited (${result.reason}) before completing all assigned checkpoints.`;
      run.status='needs-attention';
      await queueLane(l.id,()=>fleet.closeLane(l.id));
    }
    await persist();
  }
});
async function claim(l,agent) {
  assertLaneReadyToClaim(l);
  if(!agent||typeof agent!=='string'||agent.length>200)throw new Error('Claim requires the actual assigned agent ID.');
  if(run.lanes.some(x=>x.status==='claimed'&&x.agent===agent))throw new Error('Agent already owns an active lane.');
  const token=randomBytes(32).toString('hex');tokens.set(l.id,token);l.status='claimed';l.agent=agent;l.heartbeatAt=stamp();
  const sessionFile=join(dir,l.id,`session-${l.generation}.json`);
  await writeJSON(sessionFile,{socket:control.socket,runId:id,laneId:l.id,generation:l.generation,token});
  if (nativeMode(run)) {
    const credentialFile = join(dir, l.id, `login-${l.generation}.json`);
    await writeJSON(credentialFile, accounts.get(l.username));
    l.credentialFile = credentialFile;
  }
  await writeJSON(join(dir,l.id,'brief.json'),brief(l));await persist();return {...brief(l),sessionFile};
}
function persist() {
  run.updatedAt=stamp();
  run.acceptance=acceptance(run);
  const snapshot=JSON.parse(JSON.stringify(run));
  saving=saving.catch(()=>{}).then(async()=>{await writeJSON(join(dir,'manifest.json'),snapshot);await writeFile(join(dir,'report.html'),reportHTML(snapshot),{mode:0o600});});
  return saving.catch(error=>{run.recordingStatus='unavailable';throw error;});
}
function lane(id){const l=run.lanes.find(x=>x.id===id);if(!l)throw new Error('Unknown lane.');return l;}
function assertLease(req) {
  const l=lane(req.laneId);
  if(l.status!=='claimed'||req.generation!==l.generation||!equal(req.token,tokens.get(l.id)))throw new Error('Session handle is stale or not owned by this agent. Claim a ready lane through the coordinator.');
  if(Date.now()-Date.parse(l.heartbeatAt)>LEASE_MS)throw new Error('Agent lease expired. Coordinator recovery is required.');
  l.heartbeatAt=stamp();return l;
}
function queueLane(id,fn){const task=(operations.get(id)||Promise.resolve()).then(fn);operations.set(id,task.catch(()=>{}));return task;}
async function checkSource(){const actual=sourceIdentity(root);if(run.deployment&&actual.fingerprint!==run.deployment.identity.fingerprint)throw new Error('Source changed since startup. Stop and prepare a new run; the tested build is no longer attributable to this checkout.');}
async function readyLane(l) {
  l.status='preparing';l.generation++;tokens.delete(l.id);l.browserLaunchPending=true;await persist();
  if(stopping)throw new Error('Controller is stopping.');
  try{l.browser=await fleet.openLane(l,accounts.get(l.username));await persist();}
  catch(e){await fleet.closeLane(l.id);l.browserLaunchPending=false;await persist();throw e;}
  if(stopping){await fleet.closeLane(l.id);throw new Error('Controller is stopping.');}
  l.probes=await fleet.probeCapabilities(l.id);
  l.checkResults=await fleet.verifyChecks(l.id,l.checks);
  l.status='ready';l.agent=null;l.heartbeatAt=null;
  await event(dir,{kind:'lane-ready',lane:l.id,generation:l.generation});
}
async function verifyIsolation() {
  run.isolation=await fleet.proveIsolation();
  const record={at:stamp(),...run.isolation};
  run.isolationHistory ||= [];run.isolationHistory.push(record);
  await event(dir,{kind:'isolation-verified',...record});
  await persist();
}
async function prepareWave() {
  if(workers.handles.size || fleet.lanes.size || run.lanes.some(l=>l.role!=='reviewer'&&['claimed','ready','preparing','paused'].includes(l.status)))throw new Error('Finish or recover the current wave before advancing.');
  await checkSource();
  const selected=[...run.lanes.filter(l=>l.role!=='reviewer'&&l.status==='queued').slice(0,run.parallel),...run.lanes.filter(l=>l.role==='reviewer'&&l.status==='queued')];
  if(!selected.length){run.status=run.lanes.every(l=>l.status==='complete')?terminalStatus():'blocked';run.summary='No queued lanes remain; inspect scenario results and blocked lanes.';await persist();return;}
  run.status='preparing';await persist();
  try {
    for(const l of selected)await readyLane(l);
    await openProductsAfterIsolation(selected, {
      parkLanes: () => fleet.parkLanesForIsolation(),
      proveIsolation: verifyIsolation,
      navigateLane: laneId => fleet.navigateLane(laneId),
    });
    if (nativeMode(run)) {
      await fleet.close();
      for (const l of selected) { l.preflightBrowser = l.browser; delete l.browser; l.nativeReadiness = 'awaiting-thread-binding-and-visible-login'; }
      run.isolation.scope = 'disposable-preflight-browsers-only';
    }
    run.status='ready';run.summary='Readiness passed for current wave. Waiting for agent dispatch.';
  }catch(error){
    for(const l of selected){l.status='blocked';l.error=safeError(error);tokens.delete(l.id);l.generation++;}
    await fleet.close();run.status='blocked';run.error=safeError(error);
    throw error;
  }finally{await persist();}
}
function brief(l) {
  return {run:id,lane:l.id,username:l.username,products:l.products,track:l.track,url:l.url,resources:l.resources,
    browser:nativeMode(run)?'native thread-owned schemii_browser; CLI is an ownership/evidence ledger':'one independent Chromium process/context; use test.sh action exclusively',role:l.role||'tester',credentialFile:l.credentialFile,recovery:{pendingOnly:true,completed:l.scenarios.filter(s=>!pendingScenarios(l).includes(s)).map(s=>({id:s.id,functional:s.functional,visual:s.visual,note:s.note})),instructions:'Inspect saved state of any uncertain prior write before proceeding; never replay completed scenarios.'},viewports:l.viewports,scenarios:pendingScenarios(l),
    writeAuthorization:l.writeAuthorization,
    boundaries:l.writeAuthorization?.enabled ? 'Only the exact assigned disposable resources and listed operations may be edited. Retained resources remain read-only.' : 'Read-only app walkthrough. Do not save, create, delete or change account settings.',
    instructions:nativeMode(run)?'Bind the actual claimed generation to the session directory returned by your own native browser before login. Read only your assigned private login file; sign in visibly using ordinary schemii_browser tools. Record native-auth with fresh /account screenshot and signed-in identity snapshot invocation. Never use harness action or shared preview tabs. Begin each scenario before native captures. Import selected output before expiry, inspect fresh images and record receipts. Reconcile uncertain saved writes before replay. Sign out, call browser_close, then finish; thread completion does not close transport.': 'Never sign into another account, use global T3 tabs, call app APIs, or rebuild/reset the stack. Use your session file for every browser action. Record scenario outcomes and evidence before finish. If a write outcome is uncertain, inspect saved state before repeating. Record structured draft findings with owned screenshot evidence as they arise.',
    commands:{action:`${root}/test.sh action --session-file SESSION_FILE --kind snapshot`,checkpoint:`${root}/test.sh checkpoint --session-file SESSION_FILE --scenario SCENARIO --functional passed --visual passed --note DESCRIPTION --evidence RELATIVE_PATH`,finding:`${root}/test.sh finding --session-file SESSION_FILE --scenario SCENARIO --title TITLE --severity medium --steps STEPS --expected EXPECTED --actual ACTUAL --evidence SCREENSHOT`,finish:`${root}/test.sh finish --session-file SESSION_FILE`}};
}
async function ownedEvidence(paths,l){
  const output=[];
  for(const p of paths||[]) {
    const target=await realpath(isAbsolute(p)?p:resolve(dir,p));
    const rel=relative(await realpath(join(dir,l.id)),target);
    if(rel.startsWith('..')||isAbsolute(rel)||(await stat(target)).isDirectory())throw new Error('Evidence must be a file inside this lane artifact directory.');
    output.push(relative(dir,target));
  }
  return output;
}
async function workerRequest(req){
  if(starting || stopping)throw new Error('Controller is not accepting worker actions.');
  return queueLane(req.laneId,async()=>{
    const l=assertLease(req);
    if(req.command!=='heartbeat')await checkSource();
    if(req.command==='native-bind') { const binding=await bindNative(run,l,req.args?.directory,browserRoot);await persist();return binding; }
    if(req.command==='native-auth') {
      const receipt=await recordNativeAuthentication(run,l,req.args,{runDir:dir,browserRoot});await persist();return receipt;
    }
    if(req.command==='resource-receipt') {
      const receipt=await recordNativeResource(run,l,req.args,{runDir:dir,browserRoot});await persist();return receipt;
    }
    if(req.command==='begin') {
      if(nativeMode(run)) {
        await assertNativeConnection(l,browserRoot);
        if(l.native.authentication?.generation!==l.generation)throw new Error('Record the visible authenticated account observation before scenarios.');
      }
      const scenario=l.scenarios.find(s=>s.id===req.scenario);
      if(!scenario)throw new Error('Unknown scenario.');
      assertScenarioPrerequisite(l,scenario);
      const state=beginScenario(run,l,req.scenario,req.args);await persist();return state;
    }
    if(req.command==='capture') {
      if(!nativeMode(run))throw new Error('Use harness screenshot actions for isolated lanes.');
      const capture=await importNativeFile(run,l,req.args?.file,{...req.args,runDir:dir,browserRoot});await persist();return capture;
    }
    if(req.command==='inspect') {
      if(req.evidence?.length!==1)throw new Error('Inspect needs exactly one recorded screenshot.');
      let target=l;
      if(req.targetLane) {
        if(l.role!=='reviewer')throw new Error('Only the assigned reviewer may inspect another lane evidence.');
        target=lane(req.targetLane);
      }
      const capture=(target.captures||[]).find(c=>c.path===req.evidence[0]);
      if(!capture||capture.kind!=='image')throw new Error('Inspect needs a registered image.');
      const paths=await ownedEvidence([capture.path],target);
      const bytes=await readFile(resolve(dir,paths[0]));
      if(createHash('sha256').update(bytes).digest('hex')!==capture.sha256)throw new Error('Image changed after capture.');
      if(target===l)currentCapture(run,l,capture.path);
      const receipt=inspectionReceipt(capture,l.agent,{...req.args,note:req.note});await persist();return receipt;
    }
    if(req.command==='inspect-download') {
      if(req.evidence?.length!==1)throw new Error('Download inspection needs exactly one file.');
      const capture=(l.captures||[]).find(c=>c.path===req.evidence[0]);
      if(!capture)throw new Error('Download must be registered to this lane.');
      const result=await inspectDownload(capture,dir,req.args);await persist();return result;
    }
    if(req.command==='review') {
      if(nativeMode(run))await assertNativeConnection(l,browserRoot);
      const evidence=await ownedEvidence(req.evidence,l);
      for(const path of evidence)currentCapture(run,l,path,{inspection:true});
      const receipt=recordReview(run,l,lane(req.targetLane),req.scenario,{...req.args,verdict:req.verdict,note:req.note,evidence,finding:req.finding});await persist();return receipt;
    }
    if(req.command==='action') {
      if(nativeMode(run))throw new Error('Native lanes use their own schemii_browser connection; legacy action handles cannot drive it.');
      if(req.kind==='upload' && !l.writeAuthorization?.operations?.some(operation=>operation.includes('upload')))
        throw new Error('File upload requires an explicitly assigned writable fixture operation.');
      try {
        const result=await fleet.action(l.id,req.kind,req.args||{});
        await event(dir,{kind:'action',lane:l.id,agent:l.agent,action:req.kind});
        if(result.screenshot){
          const h=fleet.lanes.get(l.id);
          l.captures ||= [];
          const state=l.currentScenario?assertCaptureState(run,l,{viewport:h.page.viewportSize(),url:h.page.url()}):{phase:'preflight',generation:l.generation,agent:l.agent,source:run.deployment.identity.fingerprint};
          const bytes=await readFile(result.screenshot);
          l.captures.push({...state,kind:'image',path:relative(dir,result.screenshot),viewport:h.page.viewportSize(),url:h.page.url(),sha256:createHash('sha256').update(bytes).digest('hex'),at:stamp()});
        }
        if(result.download) {
          const h=fleet.lanes.get(l.id),state=assertCaptureState(run,l,{viewport:h.page.viewportSize(),url:h.page.url()}),bytes=await readFile(result.download);
          l.captures ||= [];l.captures.push({...state,kind:'download',path:relative(dir,result.download),sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length});
        }
        l.actionCount=(l.actionCount||0)+1;l.lastActionAt=stamp();await persist();return result;
      }catch(error){
        await event(dir,{kind:'action-failed',lane:l.id,action:req.kind,error:safeError(error)});
        // An expired identity must never become another account's UI session.
        if(/identity|session cookie|origin/i.test(error.message)){l.status='paused';tokens.delete(l.id);l.generation++;}
        await persist();throw new Error(safeError(error));
      }
    }
    if(req.command==='heartbeat'){await persist();return {lane:l.id,heartbeatAt:l.heartbeatAt};}
    if(req.command==='finding') {
      const s=l.scenarios.find(s=>s.id===req.scenario);if(!s)throw new Error('Finding needs an assigned scenario ID.');
      const fields=findingInput(req);
      if(l.currentScenario?.scenario!==s.id)throw new Error('Finding must belong to the current assigned scenario.');
      const evidence=await ownedEvidence(req.evidence,l);
      if(!evidence.length || !evidence.some(path=>(l.captures||[]).some(c=>c.path===path&&c.generation===l.generation&&c.scenario===s.id&&c.scenarioAttempt===l.currentScenario.id&&c.phase==='scenario')))throw new Error('Finding needs at least one screenshot captured by this lane in its current session.');
      const finding={id:`finding-${randomUUID()}`,lane:l.id,username:l.username,scenario:s.id,agent:l.agent,...fields,evidence,verificationStatus:'unverified',at:stamp()};
      run.findings ||= [];run.findings.push(finding);
      await event(dir,{kind:'finding',id:finding.id,lane:l.id,scenario:s.id,severity:finding.severity,title:finding.title,evidence});
      await persist();return finding;
    }
    if(req.command==='checkpoint') {
      const s=l.scenarios.find(s=>s.id===req.scenario);if(!s)throw new Error('Unknown scenario.');
      for(const key of ['functional','visual'])if(!['passed','failed','blocked'].includes(req[key]))throw new Error(`${key} needs passed, failed, or blocked.`);
      if(!req.note?.trim())throw new Error('Checkpoint needs expected/actual observations in --note.');
      const evidence=await ownedEvidence(req.evidence,l);
      if(req.visual==='passed') {
        if(l.currentScenario?.scenario!==s.id)throw new Error('Checkpoint needs this scenario current begin/capture.');
        if(!evidence.some(path=>{try{currentCapture(run,l,path,{inspection:true});return true;}catch{return false;}}))throw new Error('Visual pass requires current scenario/generation/source/viewport/resource screenshot and image inspection receipt.');
      }
      if(req.functional==='passed'&&s.downloadOracle&&!l.captures?.some(c=>c.kind==='download'&&c.scenario===s.id&&c.scenarioAttempt===l.currentScenario?.id&&c.contentInspection?.result==='passed'))throw new Error('This scenario requires actual downloaded content inspection before functional pass.');
      s.attempts ||= [];
      s.attempts.push({id:randomUUID(),generation:l.generation,scenarioAttempt:l.currentScenario?.id,source:run.deployment.identity.fingerprint,functional:req.functional,visual:req.visual,note:req.note,evidence,reviewer:l.agent,at:stamp()});
      const functional=s.functional==='failed'?'failed':req.functional;
      const visual=s.visual==='failed'?'failed':req.visual;
      Object.assign(s,{functional,visual,note:s.attempts.map((a,i)=>`Attempt ${i+1}: ${a.note}`).join('\n'),evidence:[...new Set([...s.evidence,...evidence])],reviewer:l.agent,at:stamp()});
      await event(dir,{kind:'checkpoint',lane:l.id,scenario:s.id,functional:s.functional,visual:s.visual});await persist();return s;
    }
    if(req.command==='finish') {
      if(l.role!=='reviewer'&&l.scenarios.some(s=>s.functional==='not-run'||s.visual==='not-run'))throw new Error('Every scenario needs explicit results; use blocked for unavailable checks.');
      if(nativeMode(run))await closeNativeReceipt(l);
      else await fleet.closeLane(l.id);
      l.status='complete';l.generation++;tokens.delete(l.id);
      if(l.credentialFile)await rm(l.credentialFile,{force:true});
      await releaseAccounts({runId:id,accounts:[l.username]});
      if(run.lanes.every(x=>x.status==='complete')){
        run.status=terminalStatus();
        run.summary='All assigned scenarios recorded. Review functional and visual results separately.';
      }
      await persist();return {lane:l.id,status:l.status,runStatus:run.status};
    }
    throw new Error('Unsupported worker operation.');
  });
}
async function controllerRequest(req){
  if(!equal(req.token,control.token))throw new Error('Controller authorization failed.');
  if(req.command==='ping')return {alive:true};
  if(stopping&&['stop','cleanup'].includes(req.command))return {status:'stopping',preserved:'QA accounts, app data, credentials, evidence and reports'};
  if(starting || stopping)throw new Error(starting?'Startup is still running; inspect status.':'Controller is stopping.');
  if(req.command==='run')return {status:'awaiting-agent-dispatch',available:run.lanes.filter(l=>l.status==='ready'&&l.role!=='reviewer').map(brief),reviewer:run.lanes.filter(l=>l.status==='ready'&&l.role==='reviewer').map(brief),queued:run.lanes.filter(l=>l.status==='queued').map(l=>l.id),requiredSlots:'One coordinator + requested active testers + independent verifier. Check actual runtime availability before claim.'};
  const task=transition.then(async()=>{
    if(req.command==='claim'){
      return claim(lane(req.laneId),req.agent);
    }
    if(req.command==='launch-codex') {
      if(run.controller!=='codex')throw new Error('This run uses the T3 controller.');
      const selected=run.lanes.filter(l=>l.status==='ready');
      if(selected.length){run.status='running';run.summary='Independent Codex testers are inspecting their assigned browser sessions.';await persist();}
      for(const l of selected) {
        const assignment=await claim(l,`codex:${id}:${l.id}:${l.generation}`);
        l.workerLaunchPending=true;await persist();
        try {
          l.worker=await workers.start({lane:l,sessionFile:assignment.sessionFile,brief:assignment});
          l.workerLaunchPending=false;await persist();
        }catch(e){l.status='paused';l.generation++;tokens.delete(l.id);l.error=safeError(e);await persist();throw e;}
      }
      return {status:'agents-running',started:selected.map(l=>({lane:l.id,pid:l.worker.pid})),active:workers.handles.size};
    }
    if(req.command==='native-release') {
      if(!nativeMode(run))throw new Error('Native release requires a prepared native run.');
      const l=lane(req.laneId);
      if(!['complete','paused','blocked'].includes(l.status))throw new Error('Finish or pause the owning worker before transport release.');
      const cleanup=await releaseNativeTransport(l,browserRoot);await persist();return {lane:l.id,cleanup};
    }
    if(req.command==='advance'){await prepareWave();return {status:run.status,available:run.lanes.filter(l=>l.status==='ready').map(brief)};}
    if(req.command==='recover'){
      const l=lane(req.laneId);
      if(l.status==='complete'||l.status==='queued')throw new Error('Only an active, blocked or paused lane can be recovered.');
      if(nativeMode(run)) {
        if(l.native) { await closeNativeReceipt(l);if((l.native.cleanup.transport!=='stopped'||l.native.cleanup.guardian!=='stopped'||l.native.cleanup.temporaryOutput!=='removed-observed'))throw new Error('Close the old native session transport before reassignment; browser_close alone is insufficient.'); }
        l.status='paused';l.generation++;tokens.delete(l.id);await operations.get(l.id);await checkSource();
        // Preflight only this account. Native peers have independent transports.
        await readyLane(l);await fleet.closeLane(l.id);l.preflightBrowser=l.browser;delete l.browser;l.agent=null;
        run.status='ready';await persist();return {lane:l.id,status:l.status,generation:l.generation,pending:pendingScenarios(l).map(s=>s.id),reconcile:'Inspect saved state before repeating an uncertain write.'};
      }
      if(run.lanes.some(x=>x.id!==l.id&&x.status==='claimed'))throw new Error('Pause recovery until other agents finish their wave; isolation probes change logins.');
      return recoverFleetFailClosed({
        recover:async()=>{
          l.status='paused';l.generation++;tokens.delete(l.id);
          await workers.stop(l.id);await operations.get(l.id);await fleet.closeLane(l.id);
          await checkSource();await readyLane(l);
          const activeLanes=run.lanes.filter(active=>fleet.lanes.has(active.id));
          await openProductsAfterIsolation(activeLanes, {
            parkLanes: () => fleet.parkLanesForIsolation(),
            proveIsolation: verifyIsolation,
            navigateLane: laneId => fleet.navigateLane(laneId),
          });
          run.status='ready';delete run.error;await persist();
          return {lane:l.id,status:l.status,generation:l.generation};
        },
        lanes:run.lanes,
        affectedLaneIds:()=>[l.id,...fleet.lanes.keys()],
        messageFor:safeError,
        closeFleet:()=>fleet.close(),
        invalidateLane:active=>{
          tokens.delete(active.id);active.generation++;active.agent=null;active.heartbeatAt=null;
          active.browserLaunchPending=false;active.workerLaunchPending=false;
        },
        onFailure:({message,cleanupError})=>{
          run.status='blocked';run.error=message;
          run.summary=cleanupError
            ?'Recovery isolation failed; every active lane was blocked, but browser cleanup reported an error.'
            :'Recovery isolation failed; every active lane was blocked and must pass recovery before use.';
        },
        persist,
      });
    }
    if(['stop','cleanup'].includes(req.command)){await shutdown(req.command);return {status:'stopped',preserved:'QA accounts, app data, credentials, evidence and reports'};}
    throw new Error('Unknown controller command.');
  });transition=task.catch(()=>{});return task;
}
async function shutdown(reason){
  if(stopping)return;stopping=true;
  for(const l of run.lanes){if(['claimed','ready','preparing'].includes(l.status))l.status='paused';l.generation++;tokens.delete(l.id);}
  if(nativeMode(run)) {
    for(const l of run.lanes)if(l.native) {
      try { await releaseNativeTransport(l,browserRoot); } catch(error) { run.status='cleanup-pending';l.cleanupError=safeError(error);stopping=false;await persist();throw error; }
      if((l.native.cleanup.transport!=='stopped'||l.native.cleanup.guardian!=='stopped'||l.native.cleanup.temporaryOutput!=='removed-observed')) { run.status='cleanup-pending';stopping=false;await persist();throw new Error('Native transport cleanup remains pending. Use supported native session closure; keep the deployment lease until owned transports exit.'); }
    }
  }
  await workers.close();await fleet.close();await releaseAccounts({runId:id,accounts:run.lanes.map(l=>l.username)});if(nativeMode(run)) {run.executionStatus=run.status;run.status='stopped';}else run.status=['passed','execution-complete','finished-with-gaps'].includes(run.status)?run.status:'stopped';run.summary=`Browser controller stopped (${reason}); retained accounts and resources preserved.`;
  await event(dir,{kind:'stopped',reason});await persist();
  setTimeout(()=>{server.close();void rm(control.socket,{force:true}).finally(()=>process.exit(0));},200).unref();
}
await rm(control.socket,{force:true});
const server=net.createServer(socket=>{
  let input='',handled=false;
  socket.setTimeout(190000,()=>socket.destroy());
  socket.on('error',()=>{});
  socket.on('data',chunk=>{
    if(handled)return;input+=chunk;if(input.length>65536){socket.destroy();return;}
    if(!input.includes('\n'))return;handled=true;
    (async()=>{try{const req=JSON.parse(input.split('\n')[0]);const result=['action','checkpoint','finding','finish','heartbeat','native-bind','native-auth','resource-receipt','begin','capture','inspect','inspect-download','review'].includes(req.command)?await workerRequest(req):await controllerRequest(req);socket.end(JSON.stringify({ok:true,result})+'\n');}catch(e){socket.end(JSON.stringify({ok:false,error:safeError(e)})+'\n');}})();
  });
});
await new Promise((res,rej)=>{server.once('error',rej);server.listen(control.socket,res);});await chmod(control.socket,0o600);
process.on('SIGTERM',()=>void shutdown('SIGTERM').catch(()=>{}));process.on('SIGINT',()=>void shutdown('SIGINT').catch(()=>{}));
try{
  if(mode==='resume'){
    await checkSource();
    for(const l of run.lanes)if(l.status!=='complete'){l.status='queued';l.generation++;tokens.delete(l.id);}
    // Resume verifies the active deployment or rebuilds through the supported launcher.
  }
  run.deployment=await startDeployment({root,runDir:dir});
  if(stopping)throw new Error('Controller was stopped during startup.');
  for(const l of run.lanes)if(l.status==='preparing')l.status='queued';
  await prepareWave();starting=false;
}catch(e){starting=false;run.status='blocked';run.error=safeError(e);await persist();await workers.close();await fleet.close();await releaseAccounts({runId:id,accounts:run.lanes.map(l=>l.username)});server.close();await rm(control.socket,{force:true});process.exitCode=4;}
if(run.status!=='blocked')setInterval(()=>{
  for(const l of run.lanes) {
    void expireWorkerLease(l,{
      leaseMs:LEASE_MS,managedProcess:run.controller==='codex',
      invalidate:active=>tokens.delete(active.id),
      // Termination is deliberately outside operations: a stalled UI action
      // cannot postpone stopping an expired worker's inference process.
      stopWorker:(laneId,reason)=>workers.stop(laneId,reason),
      closeBrowser:laneId=>queueLane(laneId,async()=>{
        if(nativeMode(run))await closeNativeReceipt(lane(laneId));
        else await fleet.closeLane(laneId);
      }),
      persist,
      onEvent:async data=>{run.status='needs-attention';run.summary='An execution lease expired; inspect retained results and reconcile uncertain writes before recovery.';await event(dir,data);},
    }).catch(()=>{run.recordingStatus='unavailable';void persist().catch(()=>{});});
  }
},30000).unref();
