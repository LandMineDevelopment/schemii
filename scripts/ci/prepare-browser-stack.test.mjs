import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { parseOptions, preparationCommands } from './prepare-browser-stack.mjs';

const source = fileURLToPath(new URL('../../', import.meta.url));
const put = (path, text) => { mkdirSync(dirname(path), {recursive:true}); writeFileSync(path, text); };
const live = pid => {
  try { process.kill(pid, 0); return readFileSync(`/proc/${pid}/stat`, 'utf8').split(' ')[2] !== 'Z'; }
  catch { return false; }
};

function fixture() {
  const owned = mkdtempSync(join(tmpdir(), 'schemii-prepare-overlap-'));
  const root = join(owned, 'source');
  const unrelated = join(owned, 'invocation-cwd');
  mkdirSync(unrelated);
  for (const name of ['prepare-browser-stack.mjs', 'run-browser-shard.mjs', 'browser-shards.mjs', 'timing.mjs']) {
    const path = join(root, 'scripts/ci', name);
    mkdirSync(dirname(path), {recursive:true}); copyFileSync(resolve(source, 'scripts/ci', name), path);
  }
  put(join(root, 'package.json'), '{"type":"module"}');
  put(join(root, 'control.mjs'), `
    import {appendFileSync,existsSync,writeFileSync} from 'node:fs';
    import {resolve} from 'node:path';
    import {setTimeout as delay} from 'node:timers/promises';
    export async function control(role){
      const root=${JSON.stringify(root)};
      const marker=name=>resolve(root,name+'.json');
      appendFileSync(resolve(root,'starts.jsonl'),JSON.stringify(role)+'\\n');
      writeFileSync(marker(role+'-started'),JSON.stringify({pid:process.pid,cwd:process.cwd(),
        argv:role==='launcher'?[resolve(process.argv[2]),...process.argv.slice(3)]:process.argv.slice(1),
        testContext:Boolean(process.env.NODE_TEST_CONTEXT)}));
      if(process.env.FIXTURE_MODE!=='default'){
        const peer=role==='launcher'?'discovery':'launcher';
        const deadline=Date.now()+3000;
        while(!existsSync(marker(peer+'-started')) && Date.now()<deadline)await delay(10);
        if(!existsSync(marker(peer+'-started')))throw new Error('Simultaneous-start barrier failed');
        writeFileSync(marker(role+'-barrier'),JSON.stringify({peerStarted:true}));
        if(['hold','launcher-fail','discovery-fail'].includes(process.env.FIXTURE_MODE)){
          const releaseDeadline=Date.now()+5000;
          while(!existsSync(marker(role+'-release')) && Date.now()<releaseDeadline)await delay(10);
          if(!existsSync(marker(role+'-release')))throw new Error('Controlled release missing');
        }
      }
      writeFileSync(marker(role+'-ended'),JSON.stringify({complete:true}));
      if(process.env.FIXTURE_MODE===role+'-fail')return role==='launcher'?7:1;
      return 0;
    }
  `);
  put(join(root, 'start.sh'), '#!/usr/bin/env bash\nexec node ./launcher.mjs "$0" "$@"\n');
  put(join(root, 'launcher.mjs'), 'import {control} from "./control.mjs";process.exitCode=await control("launcher");\n');
  chmodSync(join(root, 'start.sh'), 0o700);
  put(join(root, 'tests/browser-infrastructure/shards.test.mjs'), `
    import assert from 'node:assert/strict';import test from 'node:test';
    import {control} from '../../control.mjs';
    test('synthetic collection-only fixture',async()=>{assert.equal(await control('discovery'),0);});
  `);
  const marker = name => join(root, `${name}.json`);
  return {owned, root, unrelated, marker};
}

function launch(fixture, discovery, mode) {
  const env = {...process.env, FIXTURE_MODE:mode};
  // This is a fresh synthetic CI command, outside this test runner's worker.
  delete env.NODE_TEST_CONTEXT;
  const child = spawn(process.execPath, [join(fixture.root, 'scripts/ci/prepare-browser-stack.mjs'),
    ...(discovery ? ['--discovery'] : [])],
  {cwd:fixture.unrelated, env, stdio:['ignore','pipe','pipe']});
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const done = new Promise(resolveExit => child.on('close', (status, signal) => resolveExit({status, signal, output})));
  return {child, done};
}

async function waitFor(path) {
  for (let index = 0; index < 300 && !existsSync(path); index++) await delay(10);
  assert.ok(existsSync(path), 'controlled child did not reach its declared barrier');
}

async function dispose(fixture, running) {
  if (running?.child.exitCode === null && running.child.signalCode === null) {
    running.child.kill('SIGTERM');
    await running.done;
  }
  rmSync(fixture.owned, {recursive:true, force:true});
  assert.equal(existsSync(fixture.owned), false);
}

test('closed wrapper commands retain exact launcher and collection-only argv at original source cwd', () => {
  assert.equal(parseOptions([]), false);
  assert.equal(parseOptions(['--discovery']), true);
  for (const args of [['--discovery','--discovery'],['--command=anything'],['--grep=one'],['./start.sh'],['--discovery','--plan']]) {
    assert.throws(() => parseOptions(args));
  }
  for (const value of [1,'true',{},null]) assert.throws(() => preparationCommands(value));
  const commands = preparationCommands(true);
  assert.deepEqual(commands.map(({command,args,cwd}) => ({command,args,cwd})), [
    {command:'./start.sh', args:[], cwd:resolve(source)},
    {command:process.execPath,args:['--test','tests/browser-infrastructure/shards.test.mjs'],cwd:resolve(source)},
  ]);
  assert.equal(preparationCommands().length, 1);
});

test('default actual wrapper launches only the canonical launcher from its original repository', async () => {
  const prepared = fixture(); let running;
  try {
    running = launch(prepared, false, 'default');
    const result = await running.done;
    assert.equal(result.status, 0, result.output);
    const launcher = JSON.parse(readFileSync(prepared.marker('launcher-started'), 'utf8'));
    assert.equal(launcher.cwd, prepared.root);
    assert.deepEqual(launcher.argv, [join(prepared.root, 'start.sh')]);
    assert.equal(existsSync(prepared.marker('discovery-started')), false);
    assert.equal(live(launcher.pid), false);
  } finally { await dispose(prepared, running); }
});

test('actual optional discovery and startup both reach the simultaneous-start barrier before completion', async () => {
  const prepared = fixture(); let running;
  try {
    running = launch(prepared, true, 'overlap');
    const result = await running.done;
    assert.equal(result.status, 0, result.output);
    for (const role of ['launcher','discovery']) {
      assert.equal(JSON.parse(readFileSync(prepared.marker(`${role}-barrier`), 'utf8')).peerStarted, true);
      assert.equal(existsSync(prepared.marker(`${role}-ended`)), true);
      const started = JSON.parse(readFileSync(prepared.marker(`${role}-started`), 'utf8'));
      assert.equal(started.cwd, prepared.root);
      assert.deepEqual(started.argv, [join(prepared.root, role === 'launcher' ? 'start.sh' : 'tests/browser-infrastructure/shards.test.mjs')]);
      if (role === 'discovery') assert.equal(started.testContext, true);
      assert.equal(live(started.pid), false);
    }
  } finally { await dispose(prepared, running); }
});

for (const role of ['launcher','discovery']) {
  test(`a real ${role} failure propagates after waiting for the other child without replay`, async () => {
    const prepared = fixture(); let running;
    try {
      running = launch(prepared, true, `${role}-fail`);
      for (const name of ['launcher','discovery']) await waitFor(prepared.marker(`${name}-barrier`));
      put(prepared.marker(`${role}-release`), '{}');
      await waitFor(prepared.marker(`${role}-ended`));
      assert.equal(running.child.exitCode, null);
      const peer = role === 'launcher' ? 'discovery' : 'launcher';
      assert.equal(existsSync(prepared.marker(`${peer}-ended`)), false);
      put(prepared.marker(`${peer}-release`), '{}');
      const result = await running.done;
      assert.equal(result.status, role === 'launcher' ? 7 : 1, result.output);
      assert.equal(existsSync(prepared.marker(`${peer}-ended`)), true);
      assert.deepEqual(readFileSync(join(prepared.root, 'starts.jsonl'), 'utf8').trim().split('\n').map(JSON.parse).sort(),
        ['discovery','launcher']);
      if (role === 'discovery') assert.match(result.output, /synthetic collection-only fixture/);
    } finally { await dispose(prepared, running); }
  });
}

for (const signal of ['SIGINT','SIGTERM']) {
  test(`${signal} stops both owned children and preserves an independent live peer`, async () => {
    const prepared = fixture(); let running;
    const peer = spawn(process.execPath, ['-e','setInterval(()=>{},1000)'], {stdio:'ignore'});
    try {
      running = launch(prepared, true, 'hold');
      for (const role of ['launcher','discovery']) await waitFor(prepared.marker(`${role}-barrier`));
      const owned = ['launcher','discovery'].map(role => JSON.parse(readFileSync(prepared.marker(`${role}-started`), 'utf8')).pid);
      running.child.kill(signal);
      const result = await running.done;
      assert.equal(result.status, signal === 'SIGINT' ? 130 : 143, result.output);
      assert.ok(owned.every(pid => !live(pid)));
      assert.ok(live(peer.pid));
    } finally { peer.kill('SIGKILL'); await dispose(prepared, running); }
  });
}
