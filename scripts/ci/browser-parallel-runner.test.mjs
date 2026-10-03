import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { joinBrowserReceipts, runOwnedChildren, validateBrowserReceipt } from './run-browser-shard.mjs';
import { hash, metadata } from './timing.mjs';
import { balanceParallelFiles, PARALLEL_FILES } from './browser-shards.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const meta = metadata('browser', 'desktop-chromium', 1);
const caseFor = (name, line = 3) => ({file:`tests/e2e/${name}.spec.js`, test_id:hash(name), source_id:hash(`tests/e2e/${name}.spec.js`), source_line:line});
const cases = ['accounts', 'sql-console', 'inspector-data'].map(name => caseFor(name));
const receipt = (items, outcomes = ['passed']) => [
  {...meta, kind:'start', planned:items.length}, ...items.map(item => ({...meta, kind:'plan', test_id:item.test_id})),
  ...items.flatMap(item => outcomes.map((outcome, index) => ({...meta, kind:'attempt', test_id:item.test_id,
    source_id:item.source_id, source_line:item.source_line, attempt:index, outcome,
    skip:outcome === 'skipped' ? 'declared-or-runtime' : 'none', setup_ms:1, execution_ms:2, teardown_ms:1}))),
  {...meta, kind:'end', outcome:outcomes.at(-1) === 'failed' ? 'failed' : 'passed', wall_ms:10},
];
const children = () => cases.map(item => ({cases:[item], records:receipt([item]), status:0}));

test('joined receipt preserves the entire original plan, skips, failure/recovery and actual parent wall', () => {
  const inputs = children();
  inputs[0].records = receipt([cases[0]], ['skipped']);
  inputs[1].records = receipt([cases[1]], ['failed', 'passed']);
  const joined = joinBrowserReceipts(cases, inputs, meta, 123);
  const validated = validateBrowserReceipt(joined, cases, meta);
  assert.equal(validated.complete, true);
  assert.deepEqual(joined.filter(record => record.kind === 'plan').map(record => record.test_id), cases.map(item => item.test_id));
  assert.deepEqual(validated.attempts.map(record => record.outcome), ['skipped', 'failed', 'passed', 'passed']);
  assert.equal(joined.at(-1).wall_ms, 123);
  assert.equal(joined.at(-1).outcome, 'passed');
  const failed = children();
  failed[1] = {cases:[cases[1]], records:receipt([cases[1]], ['failed']), status:1};
  assert.equal(joinBrowserReceipts(cases, failed, meta, 10).at(-1).outcome, 'failed');
});

test('missing, duplicate, partial, cancelled and damaged child proof cannot fabricate a joined pass', () => {
  for (const damage of ['missing', 'empty', 'end', 'attempt', 'duplicate-plan', 'duplicate-attempt', 'unplanned',
    'metadata', 'source', 'line', 'unknown-field', 'nonfinite', 'wrong-status', 'cancelled']) {
    const inputs = children();
    const records = inputs[1].records;
    if (damage === 'missing') delete inputs[1].records;
    if (damage === 'empty') inputs[1].records = [];
    if (damage === 'end') records.pop();
    if (damage === 'attempt') records.splice(2, 1);
    if (damage === 'duplicate-plan') records.splice(2, 0, {...records[1]});
    if (damage === 'duplicate-attempt') records.splice(3, 0, {...records[2]});
    if (damage === 'unplanned') records[2].test_id = hash('other');
    if (damage === 'metadata') records[2].shard = 2;
    if (damage === 'source') records[2].source_id = hash('private/cwd/source');
    if (damage === 'line') records[2].source_line++;
    if (damage === 'unknown-field') records[2].password = 'PLANTED_SECRET';
    if (damage === 'nonfinite') records[2].execution_ms = Infinity;
    if (damage === 'wrong-status') inputs[1].status = 1;
    if (damage === 'cancelled') { records[2].outcome = 'cancelled'; records[3].outcome = 'cancelled'; inputs[1].signal = 'SIGTERM'; }
    const joined = joinBrowserReceipts(cases, inputs, meta, 12);
    assert.notEqual(joined.at(-1).outcome, 'passed', damage);
    assert.ok(!JSON.stringify(joined).includes('PLANTED_SECRET'));
    assert.equal(joined.filter(record => record.kind === 'plan').length, cases.length);
    assert.ok(joined.filter(record => record.kind === 'attempt').some(record => record.test_id === cases[1].test_id));
  }
  const duplicate = children();
  duplicate[1].cases = [cases[0]];
  assert.throws(() => joinBrowserReceipts(cases, duplicate, meta, 1), /case union/);
  const changed = children();
  changed[1].cases = [{...cases[1], source_line:999}];
  assert.throws(() => joinBrowserReceipts(cases, changed, meta, 1), /case union/);
});

function put(path, content) { mkdirSync(dirname(path), {recursive:true}); writeFileSync(path, content); }
function fixture(directory) {
  for (const file of ['run-browser-shard.mjs', 'browser-shards.mjs', 'playwright-reporter.mjs', 'timing.mjs']) {
    const target = join(directory, 'scripts/ci', file);
    mkdirSync(dirname(target), {recursive:true}); copyFileSync(resolve(root, 'scripts/ci', file), target);
  }
  for (const file of ['package.json', 'playwright.config.js']) copyFileSync(resolve(root, file), join(directory, file));
  const files = [...PARALLEL_FILES, ...['account-audit', 'accounts', 'ai-design-batch-live', 'ai-read-live',
    'ai-structured-read-live', 'bulk-jobs', 'console-shared-capacity', 'query-diagnostics', 'raw-console',
    'raw-copy-streaming', 'schemer-ai-live', 'semantic-api-owned', 'shared-report-live'].map(name => `tests/e2e/${name}.spec.js`)];
  for (const file of files) put(join(directory, file), '// fixture source\n');
  put(join(directory, 'tests/e2e/helpers/parallel-account.js'), `import {readFileSync} from 'node:fs';
    export const readParallelAccounts = path => JSON.parse(readFileSync(path,'utf8'));`);
  const accounts = [1,2].map(index => {
    const accountRoot = join(directory, 'ready', `process-${index}`); mkdirSync(accountRoot, {recursive:true, mode:0o700});
    const state = join(accountRoot, 'auth.json'); put(state, '{}');
    return {index,root:accountRoot,storageState:state};
  });
  const manifest = join(directory, 'ready/accounts.json');
  put(manifest, JSON.stringify({schema:1,baseURL:'https://localhost:8001', sourceRoot:directory, physicalTargets:'shared',accounts}));
  // The real subprocess runner/reporter operate on a bounded fake command;
  // no browser, network, application or setup fixture is invoked.
  put(join(directory, 'node_modules/@playwright/test/package.json'), JSON.stringify({type:'module'}));
  put(join(directory, 'node_modules/@playwright/test/cli.js'), `
    import {appendFileSync,readFileSync} from 'node:fs';
    import {resolve} from 'node:path';
    import {setTimeout as delay} from 'node:timers/promises';
    import Reporter from '../../../scripts/ci/playwright-reporter.mjs';
    const files=${JSON.stringify(files)};
    const root=${JSON.stringify(directory)};
    const project='desktop-chromium';
    if(process.argv.includes('--list')) {
      console.log(JSON.stringify({config:{rootDir:resolve(root,'tests/e2e')},suites:[{specs:files.map(file=>({
        file:resolve(root,file),id:file,line:3,tests:[{projectName:project}]}))}]}));
    } else {
      const selected=JSON.parse(process.env.SCHEMII_E2E_FILE_MANIFEST);
      const phase=process.cwd().endsWith('/serial')?'serial':'parallel';
      const index=process.env.SCHEMII_E2E_PROCESS_INDEX;
      const event=value=>appendFileSync(resolve(root,'events.jsonl'),JSON.stringify({phase,index,...value})+'\\n');
      let secretRead;
      if(process.env.FIXTURE_READ_SECRET === '1') {
        const secretDirectory=process.env.SCHEMII_SECRET_DIRECTORY || resolve('.schemii/secrets');
        const password=readFileSync(resolve(secretDirectory,'demo_target_password'),'utf8').split('\\n',1)[0];
        if(password!==process.env.FIXTURE_EXPECTED_SECRET) throw new Error('Wrong fixture secret source');
        secretRead=true;
      }
      event({kind:'start',time:Date.now(),files:selected,cwd:process.cwd(),auth:resolve(process.cwd(),'../auth.json'),
        sourceRoot:process.env.SCHEMII_E2E_SOURCE_ROOT,bootstrap:process.env.SCHEMII_E2E_BOOTSTRAP,
        telemetry:process.env.CI_TELEMETRY_FILE,args:process.argv.slice(2),secretRead});
      if(process.env.FIXTURE_HANG === '1' && phase === 'parallel') await new Promise(()=>setInterval(()=>{},1000));
      await delay(80);
      if(process.env.FIXTURE_MISSING && process.env.FIXTURE_MISSING === index && phase === 'parallel') process.exit(1);
      const reporter=new Reporter();
      const tests=selected.map(file=>({id:file,location:{file:resolve(root,file),line:3},parent:{project:()=>({name:project})}}));
      reporter.onBegin({shard:null},{allTests:()=>tests});
      const fail=(Boolean(process.env.FIXTURE_FAIL) && process.env.FIXTURE_FAIL === index && phase === 'parallel') || (process.env.FIXTURE_SERIAL_FAIL === '1' && phase === 'serial');
      for(const test of tests) reporter.onTestEnd(test,{status:fail?'failed':'passed',retry:0,duration:5});
      reporter.onEnd({status:fail?'failed':'passed'});
      event({kind:'end',time:Date.now()});
      process.exitCode=fail?1:0;
    }
  `);
  return {manifest,files};
}

for (const selector of ['default', 'empty', 'relative explicit', 'absolute explicit']) {
  test(`parallel child launcher-secret inputs preserve ${selector} source directory`, () => {
    const owned = mkdtempSync(join(tmpdir(), 'schemii-child-secret-source-'));
    const directory = join(owned, 'source');
    mkdirSync(directory, {mode:0o700});
    try {
      const {manifest} = fixture(directory);
      const output = join(directory, 'joined.jsonl');
      const supplied = selector === 'relative explicit' ? 'private-secrets'
        : selector === 'absolute explicit' ? join(owned, 'external-private-secrets') : '';
      const secretDirectory = resolve(directory, supplied || '.schemii/secrets');
      const secret = 'PLANTED_PRIVATE_TARGET_PASSWORD_DO_NOT_PUBLISH';
      const secretFile = join(secretDirectory, 'demo_target_password');
      put(secretFile, `${secret}\n`);
      const env = {...process.env, SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE:manifest,
        CI_TELEMETRY_FILE:output, FIXTURE_READ_SECRET:'1', FIXTURE_EXPECTED_SECRET:secret};
      if (selector === 'default') delete env.SCHEMII_SECRET_DIRECTORY;
      else env.SCHEMII_SECRET_DIRECTORY = supplied;
      const result = spawnSync(process.execPath, [join(directory, 'scripts/ci/run-browser-shard.mjs'),
        '--project=desktop-chromium', '--shard=1/6', '--profile=full', '--parallel=2'],
      {cwd:directory, env, encoding:'utf8'});
      assert.equal(result.status, 0, result.stderr);
      const starts = readFileSync(join(directory, 'events.jsonl'), 'utf8').trim().split('\n')
        .map(JSON.parse).filter(event => event.kind === 'start');
      assert.equal(starts.length, 3);
      assert.ok(starts.every(event => event.cwd !== directory && event.secretRead === true));
      for (const event of starts) {
        assert.equal(existsSync(join(event.cwd, '.schemii')), false);
        assert.equal(existsSync(join(dirname(event.cwd), '.schemii')), false);
        const receipt = readFileSync(event.telemetry, 'utf8');
        assert.ok(!receipt.includes(secret) && !receipt.includes(secretDirectory));
      }
      assert.ok(!readFileSync(output, 'utf8').includes(secret));
      assert.equal(readFileSync(secretFile, 'utf8'), `${secret}\n`);
    } finally { rmSync(owned, {recursive:true, force:true}); }
    assert.equal(existsSync(owned), false);
  });
}

test('actual CLI isolates serial/pair paths, overlaps two workers and joins source-bound failed/missing controls', () => {
  for (const damage of ['none', 'failed', 'missing']) {
    const directory = mkdtempSync(join(tmpdir(), 'schemii-parallel-cli-'));
    try {
      const {manifest} = fixture(directory);
      const output = join(directory, 'joined.jsonl');
      const env = {...process.env, SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE:manifest, CI_TELEMETRY_FILE:output,
        ...(damage === 'failed' ? {FIXTURE_FAIL:'2'} : {}), ...(damage === 'missing' ? {FIXTURE_MISSING:'2'} : {})};
      const result = spawnSync(process.execPath, [join(directory, 'scripts/ci/run-browser-shard.mjs'),
        '--project=desktop-chromium', '--shard=1/6', '--profile=full', '--parallel=2'], {cwd:directory,env,encoding:'utf8'});
      assert.equal(result.status, damage === 'none' ? 0 : 1, result.stderr);
      const events = readFileSync(join(directory,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
      const starts = events.filter(event => event.kind === 'start');
      assert.equal(starts.filter(event => event.phase === 'parallel').length, 2);
      const serialEnd = events.find(event => event.phase === 'serial' && event.kind === 'end');
      if (serialEnd) assert.ok(starts.filter(event => event.phase === 'parallel').every(event => event.time >= serialEnd.time));
      const firstParallelEnd = events.find(event => event.phase === 'parallel' && event.kind === 'end');
      assert.ok(starts.filter(event => event.phase === 'parallel').every(event => event.time < firstParallelEnd.time));
      assert.equal(new Set(starts.map(event => event.cwd)).size, starts.length);
      assert.equal(new Set(starts.map(event => event.telemetry)).size, starts.length);
      assert.ok(starts.every(event => event.sourceRoot === directory && event.bootstrap === '0'));
      assert.ok(starts.every(event => !event.args.some(arg => arg.startsWith('--workers') || arg.startsWith('--shard'))));
      const records = readFileSync(output,'utf8').trim().split('\n').map(JSON.parse);
      const originals = starts.flatMap(event => event.files).map(file => ({file,test_id:hash(file),source_id:hash(file),source_line:3}));
      assert.equal(validateBrowserReceipt(records, originals, meta).complete, damage !== 'missing');
      assert.equal(records.at(-1).outcome, damage === 'none' ? 'passed' : damage === 'failed' ? 'failed' : 'error');
      if (damage === 'failed') assert.ok(records.some(record => record.outcome === 'failed'));
      if (damage === 'missing') assert.ok(records.some(record => record.outcome === 'not-run'));
      const before = readFileSync(output,'utf8');
      const duplicate = spawnSync(process.execPath,[join(directory,'scripts/ci/run-browser-shard.mjs'),
        '--project=desktop-chromium','--shard=1/6','--profile=full','--parallel=2'],{cwd:directory,env,encoding:'utf8'});
      assert.equal(duplicate.status,1);
      assert.equal(readFileSync(output,'utf8'),before);
    } finally { rmSync(directory,{recursive:true,force:true}); }
    assert.equal(existsSync(directory),false);
  }
});

test('actual default CLI remains one original-cwd process without a ready account dependency', () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-default-cli-'));
  try {
    fixture(directory);
    rmSync(join(directory,'tests/e2e/helpers/parallel-account.js'));
    const env = {...process.env,CI_TELEMETRY_FILE:join(directory,'default.jsonl')};
    for (const key of ['SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE','SCHEMII_E2E_PROCESS_INDEX','SCHEMII_E2E_SOURCE_ROOT']) delete env[key];
    const result = spawnSync(process.execPath,[join(directory,'scripts/ci/run-browser-shard.mjs'),
      '--project=desktop-chromium','--shard=1/6','--profile=full'],{cwd:directory,env,encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    const events=readFileSync(join(directory,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(event=>event.kind==='start').length,1);
    assert.equal(events[0].cwd,directory);
    assert.equal(events[0].sourceRoot,undefined);
    assert.equal(existsSync(join(directory,'ready/process-1/serial')),false);
    assert.equal(existsSync(join(directory,'ready/process-1/parallel')),false);
  } finally { rmSync(directory,{recursive:true,force:true}); }
  assert.equal(existsSync(directory),false);
});

test('a failed serial prerequisite retains failure and leaves both overlap children explicitly not-run', () => {
  const directory=mkdtempSync(join(tmpdir(),'schemii-serial-fail-'));
  try {
    const {manifest,files}=fixture(directory);
    const output=join(directory,'joined.jsonl');
    const result=spawnSync(process.execPath,[join(directory,'scripts/ci/run-browser-shard.mjs'),
      '--project=desktop-chromium','--shard=1/6','--profile=full','--parallel=2'],
    {cwd:directory,env:{...process.env,SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE:manifest,CI_TELEMETRY_FILE:output,FIXTURE_SERIAL_FAIL:'1'},encoding:'utf8'});
    assert.equal(result.status,1,result.stderr);
    const events=readFileSync(join(directory,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    assert.ok(events.every(event=>event.phase==='serial'));
    const selected=balanceParallelFiles(files,'desktop-chromium').shards[0].files;
    const records=readFileSync(output,'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(records.filter(record=>record.kind==='plan').map(record=>record.test_id).sort(),selected.map(hash).sort());
    assert.ok(records.some(record=>record.outcome==='failed'));
    assert.ok(records.some(record=>record.outcome==='not-run'));
    assert.equal(records.at(-1).outcome,'error');
  } finally { rmSync(directory,{recursive:true,force:true}); }
  assert.equal(existsSync(directory),false);
});

test('actual parent SIGTERM cancels its two children and publishes the original complete cancelled plan', async () => {
  const directory=mkdtempSync(join(tmpdir(),'schemii-parent-cancel-'));
  let parent;
  try {
    const {manifest,files}=fixture(directory);
    const output=join(directory,'joined.jsonl');
    parent=spawn(process.execPath,[join(directory,'scripts/ci/run-browser-shard.mjs'),
      '--project=desktop-chromium','--shard=1/6','--profile=full','--parallel=2'],
    {cwd:directory,env:{...process.env,SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE:manifest,CI_TELEMETRY_FILE:output,FIXTURE_HANG:'1'},stdio:'ignore'});
    const finished=new Promise(resolveExit=>parent.on('exit',(status,signal)=>resolveExit({status,signal})));
    let events=[];
    for(let index=0;index<100;index++) {
      try { events=readFileSync(join(directory,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse); } catch {}
      if(events.filter(event=>event.phase==='parallel' && event.kind==='start').length===2) break;
      await delay(20);
    }
    assert.equal(events.filter(event=>event.phase==='parallel' && event.kind==='start').length,2);
    parent.kill('SIGTERM');
    const timer=setTimeout(()=>parent.kill('SIGKILL'),3000);
    const result=await finished;clearTimeout(timer);
    assert.deepEqual(result,{status:143,signal:null});
    const records=readFileSync(output,'utf8').trim().split('\n').map(JSON.parse);
    const selected=balanceParallelFiles(files,'desktop-chromium').shards[0].files;
    assert.deepEqual(records.filter(record=>record.kind==='plan').map(record=>record.test_id).sort(),selected.map(hash).sort());
    assert.ok(records.some(record=>record.outcome==='cancelled'));
    assert.equal(records.at(-1).outcome,'cancelled');
  } finally {
    if(parent && parent.exitCode===null && parent.signalCode===null) parent.kill('SIGKILL');
    rmSync(directory,{recursive:true,force:true});
  }
  assert.equal(existsSync(directory),false);
});

const live = pid => {
  try { process.kill(pid,0); return readFileSync(`/proc/${pid}/stat`,'utf8').split(' ')[2] !== 'Z'; }
  catch { return false; }
};

test('leader exit automatically stops an owned piped descendant before awaiting transport close', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-orphan-child-'));
  const marker = join(directory,'descendant.json');
  let pid;
  try {
    const script = `const {spawn}=require('node:child_process');const {writeFileSync}=require('node:fs');
      const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:['ignore','inherit','inherit']});
      writeFileSync(${JSON.stringify(marker)},JSON.stringify(child.pid));setTimeout(()=>process.exit(0),50);`;
    const started = performance.now();
    const result = await runOwnedChildren([{command:process.execPath,args:['-e',script],cwd:directory,env:process.env,stdio:'pipe'}],{graceMs:100});
    pid = JSON.parse(readFileSync(marker,'utf8'));
    assert.equal(result[0].status,0);
    assert.ok(performance.now()-started < 2000);
    assert.equal(live(pid),false);
  } finally {
    if (pid && live(pid)) process.kill(pid,'SIGKILL');
    rmSync(directory,{recursive:true,force:true});
  }
  assert.equal(existsSync(directory),false);
});

test('owned process cancellation escalates through descendants while preserving an independent peer', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-owned-children-'));
  const peer = spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
  let pids = [];
  try {
    const marker = join(directory,'pids.json');
    const script = `const {spawn}=require('node:child_process'); const {writeFileSync}=require('node:fs');
      process.on('SIGTERM',()=>{}); const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'ignore'});
      writeFileSync(${JSON.stringify(marker)},JSON.stringify([process.pid,child.pid]));setInterval(()=>{},1000);`;
    const controller = new AbortController();
    const promise = runOwnedChildren([{command:process.execPath,args:['-e',script],cwd:directory,env:process.env,stdio:'ignore'}],
      {signal:controller.signal,graceMs:100});
    for (let index=0;index<100 && !existsSync(marker);index++) await delay(20);
    assert.ok(existsSync(marker));
    pids = JSON.parse(readFileSync(marker,'utf8'));
    await delay(50); controller.abort();
    const result = await promise;
    assert.equal(result[0].signal,'SIGKILL');
    for (let index=0;index<100 && pids.some(live);index++) await delay(20);
    assert.ok(pids.every(pid => !live(pid)));
    assert.ok(live(peer.pid));
  } finally {
    peer.kill('SIGKILL');
    for (const pid of pids) if (live(pid)) process.kill(pid,'SIGKILL');
    rmSync(directory,{recursive:true,force:true});
  }
  assert.equal(existsSync(directory),false);
});
