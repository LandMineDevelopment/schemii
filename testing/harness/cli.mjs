#!/usr/bin/env node
import { spawn, execFileSync } from 'node:child_process';
import net from 'node:net';
import { readFile, writeFile, open, rm, access } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { randomBytes } from 'node:crypto';
import { root, runPath, readJSON, privateJSON, writeJSON, privateDir, credentials, reportHTML, stamp } from './store.mjs';
import { deploymentLockPath } from './deployment.mjs';
import { reserveAccounts, reserveAvailable, releaseAccounts, availableAccounts, withFileLock, withAvailableAccounts } from './leases.mjs';
import { expandFixtureScenario } from './prerequisites.mjs';
const stateDir=resolve(process.env.SCHEMII_QA_STATE_DIRECTORY || join(root,'.schemii/testing'));
const catalog=()=>readJSON(join(root,'testing/personas.json'));
const runCommand=(file,args,options={})=>new Promise((res,rej)=>{const child=spawn(file,args,{cwd:root,stdio:'inherit',...options});child.once('error',rej);child.once('close',code=>code===0?res():rej(blocked(`${file} exited ${code}; inspect the exact diagnostic above.`)));});

const help = `Usage: ./test.sh COMMAND [options]

  setup      Create/extend stable persona credentials, prepare QA DB, provision accounts
  cleanup-sweep Delete only exact-prefix objects from a stopped owned sweep
  cleanup-author-fixture Delete selected report-author starter dashboards and models
  provision-chat Grant one retained designer access to the tested shared AI model
  provision-writer Prepare and attach one isolated writable Schemii QA target
  reset-writer Reset one marked writable QA target after a run
  verify-writer Verify one marked writable QA target is empty
  export-credentials Export a private portable credential bundle (--output FILE)
  import-credentials Import into empty private state (--input FILE)
  personas   List available permission personas
  reset      Restore registered QA data spaces; preserve credentials and app accounts
  check-reset Exercise and restore one registered fixture space
  verify-data Verify the stable baseline and PostgreSQL isolation
  load       Opt-in HTTP/stream load plan, prepare, run, report and owned cleanup
  plan       Show account/track assignments without mutations
  doctor     Check installed browser, credentials and display prerequisites
  prepare    Start canonical app once, open isolated browsers, prove readiness
  run        Dispatch ready lanes through T3 or start isolated Codex workers
  claim      Bind a lane to an agent; returns private session-file path
  action     Drive only that agent's browser through its session file
  native-release Stop only a finished/paused lane's owned extension transport
  native-auth Record visibly authenticated account identity and fresh image
  native-bind Bind claimed native lane to its live thread connection before login
  resource-receipt Record exact owned UI creation ID/name/time and fresh image
  begin      Begin an assigned scenario before fresh captures (--scenario ID)
  capture    Export a selected native screenshot/download (--args-json)
  inspect    Record hash-bound image viewing and expected/actual observations
  inspect-download Verify actual downloaded bytes against an expected oracle
  review     Independently adjudicate a target scenario/finding from reviewer lane
  checkpoint Record a scenario's function/style results and evidence
  finding    Record a documented issue candidate with lane-owned evidence
  heartbeat  Renew an agent’s ownership lease using its session file
  finish     Release a completed lane after all scenarios have results
  status     Read durable progress
  report     Generate an HTML report
  advance    Prepare the next wave after all active lanes finish
  recover    Close/reopen one lane, invalidate old handles, repeat preflight
  resume     Restart an interrupted broker; reauthenticate unfinished lanes
  stop       Close all browser processes and release the deployment lease
  cleanup    Stop owned browsers; preserve accounts, data and evidence

Setup:     --copies-per-persona 20 --admin-credentials /private/admin.json
Author:    --accounts qa_report_author_001[,qa_report_author_002]
Chat:      --account qa_designer_001 --admin-credentials /private/admin.json
           [--model gpt-6-luna --reasoning default]
Writer:    --account qa_designer_010 --admin-credentials /private/admin.json
Data:      --space qa_modeler_001|all
Selection: --persona modeler --agents 1..10 OR --accounts USER1,USER2
           --products schemoo,schemer
           --tracks lifecycle,canvas,rules,query,chat --parallel 1..10
           --viewports desktop,mobile --fixtures /path/manifest.json
           --credentials-file /private/accounts.json --headless
Native:    --browser native --controller t3 --reviewer-account USER --runtime-slots N
           Native UI tools act directly; session CLI records ownership/evidence only.
Workers:   --controller t3|codex --agent-model MODEL --agent-reasoning EFFORT
           --agent-timeout SECONDS
           Default controller: t3. Default timeout: 600 (range 30..3600).
Run:       --run RUN_ID
Claim:     --lane LANE_ID --agent AGENT_ID
Action:    --session-file FILE --kind snapshot --args-json '{"...":"..."}'
Checkpoint:--session-file FILE --scenario ID --functional passed|failed|blocked
           --visual passed|failed|blocked --note TEXT --evidence FILE1,FILE2
Finding:   --session-file FILE --scenario ID --title TEXT
           --severity low|medium|high|critical --steps TEXT
           --expected TEXT --actual TEXT --evidence FILE1,FILE2

All actions emit JSON. Browser backend: isolated or explicit native thread connection.
T3 dispatch is an explicit handoff; Codex dispatch launches independent CLI workers.
Worker model and reasoning follow the installed Codex configuration unless selected.
Reasoning: none|minimal|low|medium|high|xhigh|max|ultra; model support varies.
Exit codes: 0 success, 1 failure, 2 invalid args, 3 awaiting agent dispatch,
4 blocked prerequisite. No application test suite is invoked.
`;
function invalid(message) { return Object.assign(new Error(message), { exitCode: 2 }); }
function blocked(message) { return Object.assign(new Error(message), { exitCode: 4 }); }
const selectionOptions = ['accounts','products','tracks','parallel','viewports','fixtures','browser','controller','agents','agent-model','agent-reasoning','agent-timeout','persona','reviewer-account','runtime-slots'];
const commandOptions = {
  help: [], '--help': [], '-h': [], personas: [],
  setup: ['copies-per-persona','admin-credentials'], 'provision-chat': ['account','admin-credentials','model','reasoning'],
  'cleanup-author-fixture': ['accounts'],
  'cleanup-sweep': ['run','workspace-fixtures'],
  'provision-writer': ['account','admin-credentials'], 'reset-writer': ['account'], 'verify-writer': ['account'],
  reset: ['space'], 'verify-data': ['space'], 'check-reset': ['space'],
  'export-credentials': ['output'], 'import-credentials': ['input'],
  plan: [...selectionOptions,'headless'],
  doctor: [...selectionOptions,'credentials-file','headless'],
  prepare: [...selectionOptions,'credentials-file','headless'],
  run: ['run'], 'native-release': ['run','lane'], claim: ['run','lane','agent'],
  action: ['session-file','kind','args-json'],
  'native-bind': ['session-file','args-json'],
  'native-auth': ['session-file','args-json'],
  'resource-receipt': ['session-file','args-json'],
  begin: ['session-file','scenario','args-json'],
  capture: ['session-file','args-json'],
  inspect: ['session-file','evidence','note','args-json','target-lane'],
  'inspect-download': ['session-file','evidence','args-json'],
  review: ['session-file','target-lane','scenario','verdict','note','evidence','finding','args-json'],
  checkpoint: ['session-file','scenario','functional','visual','note','evidence'],
  finding: ['session-file','scenario','title','severity','steps','expected','actual','evidence'],
  finish: ['session-file'], heartbeat: ['session-file'],
  status: ['run'], report: ['run'], advance: ['run'], recover: ['run','lane'],
  resume: ['run'], stop: ['run'], cleanup: ['run'],
};
function parse(argv) {
  const [command = 'help', ...rest] = argv, options = {};
  if (!Object.hasOwn(commandOptions, command)) throw invalid(`Unknown command: ${command}`);
  const allowed = new Set([...commandOptions[command], 'json', 'help']);
  for (let i = 0; i < rest.length; i++) {
    const key = rest[i].replace(/^--/, '');
    if (!rest[i].startsWith('--') || !allowed.has(key) || key in options) throw invalid(`Invalid or repeated option for ${command}: ${rest[i]}`);
    if (['headless','json','help'].includes(key)) options[key] = true;
    else { if (!rest[i+1] || rest[i+1].startsWith('--') || !rest[i+1].trim()) throw invalid(`--${key} needs a value.`); options[key] = rest[++i]; }
  }
  return { command, options };
}
const list = (s, fallback) => (s || fallback).split(',').map(x => x.trim()).filter(Boolean);
const trackSteps = {
  lifecycle: 'Create a disposable object in your assigned namespace; save, reload, rename, duplicate and delete only the copy. Capture desktop/mobile states.',
  canvas: 'On an owned scratch model, inspect fit/zoom, drag, relationships and overlaps; save and reload. Retained models must not be overwritten.',
  rules: 'Use an owned scratch model to exercise valid and invalid filters/parameters, save/reload and visible validation recovery.',
  query: 'Use assigned data to verify known results, paging, cancellation and actual exported contents. Pagination needs more than one real page.',
  chat: 'Perform every scoped model action through in-app AI chat. Verify provider/model first, approvals, results, persistence and recovery. No editor/API substitute counts.',
  harness: 'Inspect the assigned app in your own browser, capture desktop and mobile evidence, and verify ordinary controls. This is a harness walkthrough, not full app acceptance.',
};
async function selection(o, reservedAccounts) {
  const browser = o.browser || 'isolated';
  if (!['isolated','native'].includes(browser)) throw new Error('--browser must be isolated or native.');
  const controller = o.controller || 't3';
  if (!['t3','codex'].includes(controller)) throw new Error('--controller must be t3 or codex.');
  if (browser === 'native' && controller !== 't3') throw new Error('--browser native requires --controller t3 and thread-owned schemii_browser tools.');
  if (o.agents !== undefined && !(browser === 'native' ? /^(?:[1-9]|10|11)$/ : /^(?:[1-9]|10)$/).test(o.agents)) throw new Error('--agents must be 1 through 10 (11 including reserved reviewer for native).');
  const defaultAccounts = Array.from({length: Number(o.agents || 2)}, (_,i)=>`qa_subagent_${String(i+1).padStart(2,'0')}`).join(',');
  let persona=null,poolCandidates=[];
  if(o.persona) {
    if(o.accounts)throw new Error('--persona and --accounts are mutually exclusive.');
    persona=(await catalog()).personas.find(p=>p.id===o.persona);
    if(!persona)throw new Error('Unknown persona; use ./test.sh personas.');
    let registry;
    try{registry=await privateJSON(join(stateDir,'registry.json'));}catch(e){if(e.code!=='ENOENT')throw e;}
    poolCandidates=(registry?.slots || []).filter(s=>s.persona===persona.id&&s.accountId&&s.provisioned).map(s=>s.username);
    if(!registry)poolCandidates=Array.from({length:20},(_,i)=>`qa_${persona.id}_${String(i+1).padStart(3,'0')}`);
    const candidates=reservedAccounts || await availableAccounts(poolCandidates);
    if(candidates.length<Number(o.agents||2))throw new Error('Not enough available provisioned accounts for this persona. Run setup, increase its copies, or clean up completed runs.');
    if(!o.products)o={...o,products:persona.defaultProducts.join(',')};
    if(!o.fixtures)try{await access(join(stateDir,'fixtures.json'));o={...o,fixtures:join(stateDir,'fixtures.json')};}catch(e){if(e.code!=='ENOENT')throw e;}
  }
  const accounts=reservedAccounts || (persona?(await availableAccounts(poolCandidates)).slice(0,Number(o.agents||2)):list(o.accounts,defaultAccounts));
  if (o.agents !== undefined && Number(o.agents) !== accounts.length) throw new Error('--agents must match the number of selected --accounts.');
  const agentModel = o['agent-model'] || null;
  const agentReasoning = o['agent-reasoning'] ?? null;
  if (agentReasoning !== null && !['none','minimal','low','medium','high','xhigh','max','ultra'].includes(agentReasoning)) throw new Error('--agent-reasoning must be none, minimal, low, medium, high, xhigh, max, or ultra (supported levels depend on the model).');
  if (controller !== 'codex' && (agentModel !== null || agentReasoning !== null)) throw new Error('--agent-model and --agent-reasoning require --controller codex; T3 agents use their own runtime settings.');
  if (agentModel !== null && !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(agentModel)) throw new Error('--agent-model must be a model identifier.');
  if (o['agent-timeout'] !== undefined && !/^[0-9]+$/.test(o['agent-timeout'])) throw new Error('--agent-timeout must be an integer number of seconds.');
  const agentTimeout = Number(o['agent-timeout'] || 600);
  if (!Number.isInteger(agentTimeout) || agentTimeout < 30 || agentTimeout > 3600) throw new Error('--agent-timeout must be 30 through 3600 seconds.');
  if (new Set(accounts.map(a => a.toLowerCase())).size !== accounts.length || accounts.some(a => !/^[a-zA-Z0-9_.@-]{1,64}$/.test(a))) throw new Error('Accounts must be unique usernames.');
  if (o.parallel !== undefined && !/^(?:[1-9]|10)$/.test(o.parallel)) throw new Error('--parallel must be an integer from 1 through 10.');
  const parallel = Number(o.parallel || Math.min(2, accounts.length));
  if (!Number.isInteger(parallel) || parallel < 1 || parallel > 10 || parallel > accounts.length) throw new Error('Parallelism must be 1–10 and no greater than the selected accounts.');
  const reviewerAccount = o['reviewer-account'] || null;
  const runtimeSlots = o['runtime-slots'] === undefined ? null : Number(o['runtime-slots']);
  if (browser === 'native') {
    if (!reviewerAccount || !accounts.includes(reviewerAccount) || accounts.length < 2) throw new Error('Native QA requires --reviewer-account naming a separately selected account.');
    if (!/^[0-9]+$/.test(o['runtime-slots'] || '') || !Number.isSafeInteger(runtimeSlots) || runtimeSlots < parallel + 2) throw new Error('Native QA requires observed --runtime-slots for active testers plus coordinator and independent reviewer.');
    if (parallel > accounts.length - 1) throw new Error('Native --parallel counts testers; reserve one selected reviewer account separately.');
  } else if (reviewerAccount || runtimeSlots !== null) throw new Error('--reviewer-account and --runtime-slots currently require --browser native.');
  const products = list(o.products, 'schemoo');
  if (products.some(x => !['schemii','schemoo','schemer'].includes(x))) throw new Error('Unknown product.');
  const tracks = list(o.tracks, 'harness');
  if (tracks.some(x => !trackSteps[x])) throw new Error('Unknown track.');
  if (tracks.length > accounts.length) throw new Error('Select at least one account per track; otherwise selected tracks would be omitted.');
  const viewports = list(o.viewports, 'desktop,mobile');
  if (viewports.some(x => !['desktop','mobile'].includes(x))) throw new Error('Viewports must be desktop,mobile.');
  for (const [name, values] of Object.entries({ accounts, products, tracks, viewports })) {
    if (!values.length || new Set(values).size !== values.length) throw new Error(`${name} must be a nonempty list without duplicates.`);
  }
  const fixture = o.fixtures ? await readJSON(resolve(o.fixtures)) : { lanes: {} };
  if (!fixture.lanes || Array.isArray(fixture.lanes)) throw new Error('Fixture manifest needs a lanes object keyed by username.');
  const lanes = accounts.map((username, i) => {
    const spec = fixture.lanes[username] || {}, track = tracks[i % tracks.length];
    const writeAuthorization=spec.writeAuthorization || null;
    if(writeAuthorization !== null) {
      if(!o.fixtures || writeAuthorization.enabled !== true ||
        !Array.isArray(writeAuthorization.resources) || !writeAuthorization.resources.length ||
        !Array.isArray(writeAuthorization.operations) || !writeAuthorization.operations.length ||
        [...writeAuthorization.resources,...writeAuthorization.operations].some(x=>typeof x!=='string'||!x.trim()||x.length>200))
        throw new Error(`${username}: writeAuthorization needs a fixture with enabled:true and nonempty resource/operation lists.`);
    }
    if (track !== 'harness' && (!spec.checks?.length || !spec.resources)) throw new Error(`${username}: feature tracks require fixture resources and effective-access checks.`);
    const checks = [...(spec.checks || [])];
    if (track === 'chat') {
      if (!spec.chatProvider?.providerId || !spec.chatProvider?.modelId)
        throw new Error(`${username}: chat track needs a provisioned model; run ./test.sh provision-chat first.`);
      checks.push({path:'/api/v1/ai/status',providerAvailable:spec.chatProvider});
    }
    for (const c of checks) {
      if (typeof c.path !== 'string' || !c.path.startsWith('/api/v1/') || c.path.includes('..') || c.path.includes('://')) throw new Error('Fixture checks must be same-origin /api/v1/ GET paths.');
      if (!c.equals && !c.minLength && !c.providerAvailable && c.status === undefined) throw new Error('Each fixture check requires an expected status or value.');
    }
    const baseScenarios = spec.scenarios || [{id:track,title:`${track} walkthrough`,instructions:trackSteps[track]}];
    if (!Array.isArray(baseScenarios) || !baseScenarios.length || baseScenarios.some(s => !s || !/^[a-z0-9-]+$/.test(s.id || ''))) throw new Error('Scenario IDs must be unique kebab-case values.');
    const scenarios = baseScenarios.flatMap(s => {
      if (Object.hasOwn(s, 'product') && !products.includes(s.product)) throw new Error(`Scenario ${s.id} product must be one of the selected products.`);
      return (s.product ? [s.product] : products).flatMap(product => viewports.map(viewportName => expandFixtureScenario(s,product,viewportName)));
    });
    return { id: `lane-${i+1}`, username, products, track, role: username === reviewerAccount ? 'reviewer' : 'tester', status: 'queued', generation: 0,
      viewport: viewports[0] === 'mobile' ? {width:390,height:844} : {width:1280,height:800},
      viewports, resources: spec.resources || {}, checks, url: spec.url,
      persona:persona?.id || spec.persona || null, expectedCapabilities:spec.expectedCapabilities, deniedProducts:spec.deniedProducts || [], writeAuthorization,
      scenarios };
  });
  for (const l of lanes) {
    if (!l.scenarios.length || new Set(l.scenarios.map(x=>x.id)).size !== l.scenarios.length || l.scenarios.some(x=>!/^[a-z0-9-]+$/.test(x.id || ''))) throw new Error('Scenario IDs must be unique kebab-case values.');
  }
  return { browser, reviewerAccount, runtimeSlots, accounts, persona:persona?.id || null, poolCandidates, agents:accounts.length, parallel, controller, agentModel, agentReasoning, agentTimeout, products, tracks, viewports, lanes, headless:!!o.headless,
    baseURL:'https://localhost:8001',previewURL:'https://omarchy.taile4f57f.ts.net',
    fixtureMode:o.fixtures ? 'declared-retained-resources' : 'harness-only-no-app-data-writes',
    fixtureVersion:fixture.version || fixture.fixtureVersion || null,
    fixturePath:o.fixtures ? resolve(o.fixtures) : null };
}
async function rpc(config, request) {
  return new Promise((resolvePromise,reject) => {
    let data = '';
    const socket = net.createConnection(config.socket);
    socket.setTimeout(180000);
    socket.on('connect',()=>socket.write(JSON.stringify({...request,token:config.token,generation:config.generation,laneId:request.laneId || config.laneId})+'\n'));
    socket.on('data',chunk=>{ data += chunk; if(data.includes('\n')) {socket.end();try {const r=JSON.parse(data.split('\n')[0]);if(!r.ok)reject(Object.assign(new Error(r.error), {code:r.code,exitCode:r.exitCode}));else resolvePromise(r.result);}catch(e){reject(e);} }});
    socket.on('error',reject);socket.on('timeout',()=>socket.destroy(new Error('Controller request timed out. Check status before repeating an action.')));
    socket.on('end',()=>{ if(!data.includes('\n'))reject(new Error('Controller disconnected. Do not repeat writes without checking saved state.')); });
  });
}
async function startBroker(run, dir, resume=false) {
  const lock = await deploymentLockPath(root);
  const log = await open(join(dir,'controller.log'),'a',0o600);
  const gate=join(dirname(lock),'qa-startup.lock');
  const child = spawn('bash',['-c','exec 4> "$1"; flock --wait 180 4 || exit 4; exec 3> "$2"; if flock --nonblock 3; then export SCHEMII_QA_LEASE_MODE=exclusive; else flock --shared --nonblock 3 || exit 4; export SCHEMII_QA_LEASE_MODE=shared; fi; export SCHEMII_QA_LEASE_FD=3 SCHEMII_QA_GATE_FD=4; exec node "$3" "$4" "$5"','qa-controller',gate,lock,join(root,'testing/harness/daemon.mjs'),run.id,resume?'resume':'prepare'],{cwd:root,detached:true,stdio:['ignore',log.fd,log.fd]});
  child.unref(); await log.close();
  await writeJSON(join(dir,'controller-owner.json'),{pid:child.pid,birthTick:await birthTick(child.pid)});
  for(let i=0;i<360;i++) {
    await new Promise(r=>setTimeout(r,1000));
    const state=await readJSON(join(dir,'manifest.json'));
    if(['ready','blocked','complete','passed','execution-complete','finished-with-gaps'].includes(state.status)) return state;
    try{process.kill(child.pid,0);}catch{ throw blocked('Controller stopped before readiness. Inspect controller.log; another run may hold the deployment lease.'); }
  }
  throw blocked('Preparation is still running. Use status; do not start another run.');
}
async function birthTick(pid) {
  try {
    const content=await readFile(`/proc/${pid}/stat`,'utf8');
    const fields=content.slice(content.lastIndexOf(')')+2).split(' ');
    return fields[0]==='Z' ? null : fields[19];
  } catch(error) { if(error.code==='ENOENT')return null;throw error; }
}
async function requireStoppedOwner(dir) {
  let owner;
  try { owner=await privateJSON(join(dir,'controller-owner.json')); }
  catch(error) { if(error.code==='ENOENT')throw blocked('Controller ownership record is missing; inspect surviving browser processes before recovery.');throw error; }
  const actual=await birthTick(owner.pid);
  if(actual&&actual===owner.birthTick)throw blocked('Controller process is still running but unavailable; wait for it to stop before recovery or cleanup.');
  const savedRun=await readJSON(join(dir,'manifest.json'));
  for(const lane of savedRun.lanes||[]) {
    if (lane.native) {
      const { closeNativeReceipt } = await import('./native.mjs');
      await closeNativeReceipt(lane);
      if (lane.native.cleanup.transport !== 'stopped' || lane.native.cleanup.guardian !== 'stopped' || lane.native.cleanup.temporaryOutput !== 'removed-observed') throw blocked(`Native transport for ${lane.id} remains live; close its supported native session before resume/cleanup.`);
    }
    for(const kind of ['browser','worker']) {
      if(lane[`${kind}LaunchPending`])throw blocked(`${kind} launch ownership for ${lane.id} is unresolved; inspect that owned ${kind} and confirm it stopped before recovery or cleanup.`);
      const owned=lane[kind];
      if(!owned)continue;
      if(!Number.isSafeInteger(owned.pid)||owned.pid<1||typeof owned.birthTick!=='string'||!/^\d+$/.test(owned.birthTick))throw blocked(`${kind} ownership record for ${lane.id} is incomplete; verify that its ${kind} stopped before recovery or cleanup.`);
      const actualBirth=await birthTick(owned.pid);
      if(actualBirth&&actualBirth===owned.birthTick)throw blocked(`${kind} PID ${owned.pid} for ${lane.id} is still live; stop that owned ${kind} before resume or cleanup.`);
    }
  }
}
async function main() {
  if (process.argv[2] === 'load') {
    const { loadMain } = await import('../load/cli.mjs');
    return loadMain(process.argv.slice(3));
  }
  const {command,options:o}=parse(process.argv.slice(2));
  if(!o.help&&['resume','stop','cleanup'].includes(command)) {
    if(!/^qa-[a-z0-9-]{6,80}$/.test(o.run||''))throw invalid('--run must identify a valid QA run.');
    // Re-read the manifest and process ownership only after locking. Concurrent
    // recovery and cleanup must not act on a previous controller generation.
    return withFileLock(join(runPath(o.run),'lifecycle.lock'),()=>execute(command,o));
  }
  return execute(command,o);
}
async function execute(command,o) {
  if(['help','--help','-h'].includes(command)||o.help){console.log(help);return;}
  if(['export-credentials','import-credentials'].includes(command)) {
    const exporting=command==='export-credentials', key=exporting?'output':'input';
    if(!o[key])throw invalid(`--${key} is required.`);
    await runCommand('python',['testing/provision.py',exporting?'export':'import',`--${key}`,resolve(o[key]),'--state-dir',stateDir]);return;
  }
  if(command==='personas'){console.log(JSON.stringify(await catalog(),null,2));return;}
  if(command==='setup') {
    if(!o['admin-credentials'])throw invalid('--admin-credentials is required; existing admin passwords are never guessed or reset.');
    const copies=o['copies-per-persona'] || '20';
    if(!/^[1-9][0-9]{0,2}$/.test(copies)||Number(copies)>100)throw invalid('--copies-per-persona must be 1 through 100.');
    await privateJSON(resolve(o['admin-credentials']));
    await runCommand('./testing/setup.sh',[copies,resolve(o['admin-credentials']),stateDir]);
    return;
  }
  if(command==='cleanup-sweep') {
    if(!/^qa-[a-z0-9-]{6,80}$/.test(o.run||'')||!o['workspace-fixtures'])throw invalid('cleanup-sweep requires --run RUN and --workspace-fixtures MAP.');
    await runCommand('python',['-m','testing.harness.cleanup_schemii_sweep','--run',o.run,'--workspace-fixtures',resolve(o['workspace-fixtures']),'--state-dir',stateDir]);return;
  }
  if(command==='cleanup-author-fixture') {
    if(!o.accounts)throw invalid('--accounts is required; name each retained report-author fixture to delete.');
    const accounts=list(o.accounts,'');
    if(!accounts.length||accounts.some(account=>!/^qa_report_author_[0-9]{3}$/.test(account))||new Set(accounts).size!==accounts.length)
      throw invalid('--accounts must contain unique retained report-author QA usernames.');
    // Wait for the deployment to be idle and hold its exclusive lease; the
    // account reservation guard closes the race with a newly starting lane.
    return withFileLock(deploymentLockPath(root),()=>withAvailableAccounts(accounts,()=>
      runCommand('python',['testing/provision.py','cleanup-author','--accounts',accounts.join(','),'--state-dir',stateDir]),{root}));
  }
  if(command==='provision-chat') {
    if(!o['admin-credentials']||!o.account)throw invalid('--admin-credentials and --account are required.');
    if(!/^qa_designer_[0-9]{3}$/.test(o.account))throw invalid('--account must name a retained designer QA account.');
    await privateJSON(resolve(o['admin-credentials']));
    await runCommand('python',['testing/provision.py','chat','--admin-credentials',resolve(o['admin-credentials']),
      '--account',o.account,'--model',o.model||'gpt-6-luna','--reasoning',o.reasoning||'default','--state-dir',stateDir]);
    return;
  }
  if(command==='provision-writer') {
    if(!o['admin-credentials']||!o.account)throw invalid('--admin-credentials and --account are required.');
    if(!/^qa_designer_[0-9]{3}$/.test(o.account))throw invalid('--account must name a retained designer QA account.');
    await privateJSON(resolve(o['admin-credentials']));
    await runCommand('./start.sh',['--prepare-testing-writable',o.account]);
    await runCommand('python',['testing/provision.py','writer','--admin-credentials',resolve(o['admin-credentials']),
      '--account',o.account,'--state-dir',stateDir]);
    return;
  }
  if(['reset-writer','verify-writer'].includes(command)) {
    if(!/^qa_designer_[0-9]{3}$/.test(o.account||''))throw invalid('--account must name a retained designer QA account.');
    await runCommand('./start.sh',[command==='reset-writer'?'--reset-testing-writable':'--verify-testing-writable',o.account]);
    return;
  }
  if(['reset','verify-data','check-reset'].includes(command)) {
    if(['reset','check-reset'].includes(command)&&!o.space)throw invalid('reset requires --space ACCOUNT or --space all.');
    const space=o.space || 'all';
    if(space!=='all'&&!/^qa_[a-z_]+_[0-9]{3}$/.test(space))throw invalid('Invalid registered testing space.');
    if(command==='check-reset'&&space==='all')throw invalid('check-reset requires one registered account, not all.');
    await runCommand('./start.sh',[command==='reset'?'--reset-testing':command==='check-reset'?'--check-testing-reset':'--verify-testing',space]);return;
  }
  if(['plan','doctor','prepare'].includes(command)) {
    let config;
    try { config=await selection(o); } catch(error) { throw invalid(error.message); }
    if(command==='plan'){console.log(JSON.stringify(config,null,2));return;}
    if(o.persona&&!o['credentials-file'])o['credentials-file']=join(stateDir,'credentials.json');
    if(!o['credentials-file'])throw invalid('--credentials-file is required; existing passwords are never guessed or reset.');
    const credentialPath=resolve(o['credentials-file']);
    try {
      const available=await credentials(credentialPath);
      if(new Set([...available.keys()].map(a=>a.toLowerCase())).size!==available.size) throw new Error('Credential usernames must be unique case-insensitively.');
      for(const a of config.persona?config.poolCandidates:config.accounts)if(!available.has(a))throw new Error(`Credential missing for ${a}.`);
      if(config.controller==='codex') {
        for(const args of [['--version'],['login','status']]) {
          try { execFileSync('codex',args,{stdio:'ignore',timeout:15000}); }
          catch { throw new Error(args[0]==='--version' ? 'Codex CLI is unavailable; install it before using --controller codex.' : 'Codex CLI login is unavailable; run codex login before preparing workers.'); }
        }
      }
      const {chromium}=await import('@playwright/test');
      await access(chromium.executablePath());
      if(!config.headless&&!process.env.DISPLAY&&!process.env.WAYLAND_DISPLAY) throw new Error('No display available. Use explicit --headless for functional-only browser operation.');
    } catch(error) { throw blocked(`Prerequisite check failed: ${error instanceof SyntaxError ? 'Malformed private credential JSON.' : error.message}`); }
    if(command==='doctor'){console.log(JSON.stringify({dependencies:'ready',browser:config.browser === 'native' ? 'thread-owned-native-after-claim' : 'isolated-processes',accounts:config.accounts,requestedParallel:config.parallel,controller:config.controller,agentModel:config.agentModel,agentReasoning:config.agentReasoning,agentTimeout:config.agentTimeout,agentCapacity:config.controller==='codex'?'Independent Codex CLI workers; capacity limited by --parallel (maximum 10).':'Coordinator must verify live runtime slots before claiming lanes.',browserPreflight:'pending prepare'},null,2));return;}
    const id=`qa-${Date.now().toString(36)}-${randomBytes(4).toString('hex')}`,dir=runPath(id);
    await privateDir(dir);
    const runtime=join('/tmp',`schemii-qa-${process.getuid()}`,id);
    await privateDir(runtime);
    const control={socket:join(runtime,'controller.sock'),token:randomBytes(32).toString('hex')};
    await writeJSON(join(dir,'control.json'),control);
    await writeJSON(join(dir,'private.json'),{credentialPath});
    let run={id,...config,status:'preparing',createdAt:stamp(),updatedAt:stamp(),root,summary:'Browser preflight pending.'};
    await writeJSON(join(dir,'manifest.json'),run);
    await writeJSON(join(dir,'controller-owner.json'),{pid:process.pid,birthTick:await birthTick(process.pid)});
    const result=await withFileLock(join(dir,'lifecycle.lock'),async()=>{
      try {
        if(config.persona) {
          const selected=await reserveAvailable({runId:id,runDir:dir,candidates:config.poolCandidates,count:config.accounts.length});
          config=await selection(o,selected);
          run={...run,...config};await writeJSON(join(dir,'manifest.json'),run);
        }else await reserveAccounts({runId:id,runDir:dir,accounts:run.accounts});
        return await startBroker(run,dir);
      }catch(e){
        // Release only if startup never transferred ownership, or its recorded
        // controller and browsers are confirmed stopped. Otherwise preserve leases.
        const owner=await readJSON(join(dir,'controller-owner.json'));
        let mayUpdate=owner.pid===process.pid;
        if(!mayUpdate)try{await requireStoppedOwner(dir);mayUpdate=true;}catch{}
        if(mayUpdate) {
          await releaseAccounts({runId:id});
          const latest=await readJSON(join(dir,'manifest.json'));
          latest.status='blocked';latest.error=e.message;latest.updatedAt=stamp();await writeJSON(join(dir,'manifest.json'),latest);
        }
        throw e;
      }
    });
    console.log(JSON.stringify(result,null,2));if(result.status==='blocked')process.exitCode=4;return;
  }
  if(['action','checkpoint','finding','finish','heartbeat','native-bind','native-auth','resource-receipt','begin','capture','inspect','inspect-download','review'].includes(command)) {
    if(!o['session-file'])throw invalid('--session-file is required.');
    if(command==='action'&&!o.kind)throw invalid('--kind is required.');
    if(command==='checkpoint'&&(!o.scenario||!['passed','failed','blocked'].includes(o.functional)||!['passed','failed','blocked'].includes(o.visual)))throw invalid('Checkpoint requires --scenario and passed|failed|blocked values for --functional and --visual.');
    if(command==='finding'&&(!o.scenario||!o.title||!['low','medium','high','critical'].includes(o.severity)||!o.steps||!o.expected||!o.actual||!o.evidence))throw invalid('Finding requires --scenario, --title, --severity, --steps, --expected, --actual and --evidence.');
    const session=await privateJSON(resolve(o['session-file']));
    let args={};
    try { args=o['args-json']?JSON.parse(o['args-json']):{}; } catch { throw invalid('--args-json must contain valid JSON.'); }
    if(!args||typeof args!=='object'||Array.isArray(args))throw invalid('--args-json must be an object.');
    const result=await rpc(session,{command,kind:o.kind,args,scenario:o.scenario,functional:o.functional,visual:o.visual,note:o.note,evidence:list(o.evidence,''),title:o.title,severity:o.severity,steps:o.steps,expected:o.expected,actual:o.actual,targetLane:o['target-lane'],verdict:o.verdict,finding:o.finding});
    console.log(JSON.stringify(result,null,2));return;
  }
  if(!/^qa-[a-z0-9-]{6,80}$/.test(o.run||''))throw invalid('--run must identify a valid QA run.');
  if(['claim','recover','native-release'].includes(command)&&!o.lane)throw invalid('--lane is required.');
  if(command==='claim'&&!o.agent)throw invalid('--agent is required.');
  const dir=runPath(o.run),run=await readJSON(join(dir,'manifest.json'));
  if(command==='status'){console.log(JSON.stringify(run,null,2));return;}
  if(command==='report') {const path=join(dir,'report.html');await writeFile(path,reportHTML(run),{mode:0o600});console.log(JSON.stringify({report:path,status:run.status}));return;}
  if(command==='resume') {
    if(['complete','passed','execution-complete','finished-with-gaps'].includes(run.status)) {
      const report=join(dir,'report.html');
      await writeFile(report,reportHTML(run),{mode:0o600});
      console.log(JSON.stringify({status:run.status,report,message:'Completed run preserved; use prepare for a new run.'}));
      return;
    }
    const control=await privateJSON(join(dir,'control.json'));
    try{await rpc(control,{command:'ping'});throw new Error('Controller is still live. Use recover for a paused lane or advance for the next wave.');}catch(e){if(!['ENOENT','ECONNREFUSED'].includes(e.code))throw e;}
    await requireStoppedOwner(dir);
    await rm(control.socket,{force:true});
    run.status='preparing';run.updatedAt=stamp();await writeJSON(join(dir,'manifest.json'),run);
    await reserveAccounts({runId:run.id,runDir:dir,accounts:run.lanes.filter(l=>l.status!=='complete').map(l=>l.username)});
    const resumed=await startBroker(run,dir,true);
    console.log(JSON.stringify(resumed,null,2));if(resumed.status==='blocked')process.exitCode=4;return;
  }
  if(!['run','claim','advance','recover','stop','cleanup','native-release'].includes(command))throw new Error(`Unknown command: ${command}`);
  const control=await privateJSON(join(dir,'control.json'));
  let result;
  try { result=await rpc(control,{command:command==='run'&&run.controller==='codex'?'launch-codex':command,laneId:o.lane,agent:o.agent}); }
  catch(error) {
    if(!['stop','cleanup'].includes(command)||!['ENOENT','ECONNREFUSED'].includes(error.code))throw error;
    await requireStoppedOwner(dir);
    await releaseAccounts({runId:run.id});
    result={status:run.status,preserved:'QA accounts, app data, credentials, evidence and reports',controller:'already stopped'};
  }
  console.log(JSON.stringify(result,null,2));if(command==='run'&&run.controller!=='codex'&&result.status==='awaiting-agent-dispatch')process.exitCode=3;
}
main().catch(error=>{console.error(JSON.stringify({error:error.message}));process.exitCode=error.exitCode||1;});
