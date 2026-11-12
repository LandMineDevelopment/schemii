import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, writeFile, rm, readdir, mkdir, copyFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { reportHTML } from './store.mjs';

const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
const testers = Array.from({ length: 10 }, (_, i) => `qa_designer_${String(i + 1).padStart(3, '0')}`);
const reviewers = ['qa_designer_014', 'qa_report_author_014'];
const accounts = [...testers, ...reviewers];
const selection = ['--browser', 'native', '--controller', 't3', '--accounts', accounts.join(','),
  '--reviewer-accounts', reviewers.join(','), '--agents', '12', '--parallel', '10', '--runtime-slots', '13'];
function invoke(command, args) {
  const result = spawnSync(process.execPath, [cli, command, ...args], { encoding: 'utf8' });
  assert.equal(result.error, undefined);
  return { ...result, output: result.stdout + result.stderr };
}
function replace(args, flag, value) {
  const result = [...args], index = result.indexOf(flag);
  if (value === undefined) result.splice(index, 2);
  else result[index + 1] = value;
  return result;
}

test('actual native CLI retains ten testers and reserves both selected reviewer identities within thirteen slots', () => {
  const result = invoke('plan', selection);
  assert.equal(result.status, 0, result.output);
  const plan = JSON.parse(result.stdout);
  assert.equal(plan.agents, 12);
  assert.equal(plan.parallel, 10);
  assert.equal(plan.runtimeSlots, 13);
  assert.equal(plan.reviewerAccount, null);
  assert.deepEqual(plan.reviewerAccounts, reviewers);
  assert.deepEqual(plan.actorCounts, { coordinators: 1, testers: 10, reviewers: 2, activeTesters: 10, requiredSlots: 13 });
  assert.deepEqual(plan.lanes.filter(l => l.role === 'tester').map(l => l.username), testers);
  assert.deepEqual(plan.lanes.filter(l => l.role === 'reviewer').map(l => l.username), reviewers);
  assert.ok(plan.lanes.every(l => l.status === 'queued' && l.agent === undefined));
  assert.equal(plan.fixtureMode, 'harness-only-no-app-data-writes');
  assert.ok(plan.lanes.every(l => l.writeAuthorization === null));
  // Planning and pending handoff cannot establish independent acceptance.
  const html = reportHTML({ ...plan, id: 'qa-offline-plan', status: 'ready' });
  assert.match(html, /review-pending/);
  assert.match(html, /10 tester accounts.*2 reviewer accounts/);
});

test('legacy singular reviewer and plural singleton preserve the same lanes and legacy field', () => {
  const common = ['--browser', 'native', '--accounts', 'tester,reviewer', '--parallel', '1', '--runtime-slots', '3'];
  const singular = invoke('plan', [...common, '--reviewer-account', 'reviewer']);
  const plural = invoke('plan', [...common, '--reviewer-accounts', 'reviewer']);
  assert.equal(singular.status, 0, singular.output);
  assert.equal(plural.status, 0, plural.output);
  assert.deepEqual(JSON.parse(singular.stdout), JSON.parse(plural.stdout));
  assert.equal(JSON.parse(singular.stdout).reviewerAccount, 'reviewer');
  assert.deepEqual(JSON.parse(singular.stdout).reviewerAccounts, ['reviewer']);
});

test('every reviewer is reserved while smaller tester waves stay queued without lowering the request', () => {
  const result = invoke('plan', replace(replace(selection, '--parallel', '2'), '--runtime-slots', '5'));
  assert.equal(result.status, 0, result.output);
  const plan = JSON.parse(result.stdout);
  assert.equal(plan.lanes.filter(l => l.role === 'tester').length, 10);
  assert.equal(plan.lanes.filter(l => l.role === 'reviewer').length, 2);
  assert.equal(plan.parallel, 2);
  assert.equal(plan.actorCounts.requiredSlots, 5);
});

test('plan and prepare reject ambiguous, unknown, duplicate, empty, absent or over-budget reviewers before runtime', () => {
  const cases = [
    [[...selection, '--reviewer-account', reviewers[0]], /mutually exclusive/],
    [replace(replace(selection, '--agents', undefined), '--reviewer-accounts', 'unknown'), /selected --accounts member/],
    [replace(selection, '--reviewer-accounts', `${reviewers[0]},${reviewers[0].toUpperCase()}`), /unique usernames/],
    [replace(selection, '--reviewer-accounts', ','), /nonempty list/],
    [replace(selection, '--reviewer-accounts', `${reviewers.join(',')},`), /nonempty list/],
    [replace(selection, '--reviewer-accounts', undefined), /requires --reviewer-account/],
    [replace(selection, '--runtime-slots', '12'), /every selected reviewer/],
    [replace(selection, '--runtime-slots', '13.1'), /observed --runtime-slots/],
    [replace(selection, '--parallel', '11'), /1 through 10/],
    [replace(selection, '--agents', '13'), /1 through 12/],
    [replace(replace(selection, '--agents', undefined), '--accounts', [...accounts, 'extra_tester'].join(',')), /1–10 tester/],
    [replace(replace(selection, '--agents', '12'), '--reviewer-accounts', accounts.join(',')), /1–10 tester/],
    [replace(replace(replace(selection, '--agents', undefined), '--reviewer-accounts', accounts.slice(0, 3).join(',')), '--runtime-slots', '14'), /reserve every selected reviewer/],
  ];
  for (const [args, message] of cases) for (const command of ['plan', 'prepare']) {
    const result = invoke(command, args);
    assert.equal(result.status, 2, `${command}: ${result.output}`);
    assert.match(result.output, message);
  }
  for (const args of [
    ['--reviewer-accounts', 'reviewer'],
    ['--reviewer-account', 'reviewer'],
  ]) assert.match(invoke('plan', args).output, /require --browser native/);
});

test('reviewer role keeps declared product capability and resource checks without granting permissions', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'schemii-cli-reviewers-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const fixture = { lanes: {
    [reviewers[0]]: { resources: { workspace: 'designer-review-only' }, expectedCapabilities: ['schemii:access'], deniedProducts: ['schemoo', 'schemer'], checks: [{ path: '/api/v1/schemoo/models', status: 403 }] },
    [reviewers[1]]: { resources: { model: 'author-review-only' }, expectedCapabilities: ['schemoo:access', 'schemer:access'], deniedProducts: ['schemii'], checks: [{ path: '/api/v1/schemii/workspaces', status: 403 }] },
  } };
  const path = join(directory, 'fixtures.json');
  await writeFile(path, JSON.stringify(fixture));
  const result = invoke('plan', [...selection, '--fixtures', path]);
  assert.equal(result.status, 0, result.output);
  const plan = JSON.parse(result.stdout);
  for (const username of reviewers) {
    const lane = plan.lanes.find(l => l.username === username);
    assert.equal(lane.role, 'reviewer');
    for (const key of ['resources', 'expectedCapabilities', 'deniedProducts', 'checks']) assert.deepEqual(lane[key], fixture.lanes[username][key]);
    assert.equal(lane.writeAuthorization, null);
  }
});

test('prepare requires credentials for every actual selected reviewer before browser or API prerequisites', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'schemii-cli-credentials-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, 'credentials.json');
  await writeFile(path, JSON.stringify({ accounts: accounts.slice(0, -1).map(username => ({ username, password: 'offline-fixture-only' })) }), { mode: 0o600 });
  const result = invoke('prepare', [...selection, '--credentials-file', path]);
  assert.equal(result.status, 4, result.output);
  assert.match(result.output, /Credential missing for qa_report_author_014/);
  assert.deepEqual(await readdir(directory), ['credentials.json']);
});

test('viewer CLI forwards optional expectation unchanged while deployment and account guards remain held', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'schemii-cli-viewer-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const harness = join(directory, 'testing', 'harness');
  await mkdir(harness, { recursive: true });
  // Run the actual dispatcher and account/deployment lock implementation against
  // disposable paths. The Python stand-in observes locks, never an application.
  for (const name of ['cli.mjs', 'leases.mjs']) await copyFile(new URL(name, import.meta.url), join(harness, name));
  await writeFile(join(harness, 'store.mjs'), `
    import { readFile, stat } from 'node:fs/promises';
    export const root = ${JSON.stringify(directory)};
    export const privateJSON = async path => {
      const info = await stat(path);
      if (!info.isFile() || (info.mode & 0o077)) throw new Error('Invalid private fixture');
      return JSON.parse(await readFile(path, 'utf8'));
    };
    export const runPath = () => { throw new Error('Unexpected run access'); };
    export const readJSON = privateJSON, writeJSON = runPath, privateDir = runPath,
      credentials = runPath, reportHTML = runPath, stamp = runPath;
  `);
  await writeFile(join(harness, 'deployment.mjs'), `
    export const deploymentLockPath = root => root + '/deployment.lock';
    export const sourceIdentity = () => { throw new Error('Unexpected deployment access'); };
  `);
  await writeFile(join(harness, 'native.mjs'), `
    export const importVerification = () => { throw new Error('Unexpected native action'); };
    export const importRemediation = importVerification, acceptance = importVerification;
  `);
  await writeFile(join(harness, 'prerequisites.mjs'), `
    export const expandFixtureScenario = () => { throw new Error('Unexpected prerequisite action'); };
  `);
  const adminFile = join(directory, 'admin.json');
  await writeFile(adminFile, '{}', { mode: 0o600 });
  await writeFile(join(directory, 'python'), `#!/usr/bin/env node
    const { spawnSync } = require('node:child_process');
    const { join } = require('node:path');
    const root = process.cwd();
    const held = name => spawnSync('flock', ['--nonblock', join(root, name), 'true']).status === 1;
    console.log(JSON.stringify({ args: process.argv.slice(2),
      deploymentHeld: held('deployment.lock'), accountsHeld: held('qa-account-reservations.lock') }));
  `, { mode: 0o700 });
  const base = ['--account', 'qa_report_viewer_001', '--author', 'qa_report_author_001', '--admin-credentials', adminFile];
  const expectation = './private fixture/expectation.json';
  for (const command of ['provision-viewer', 'unpair-viewer']) {
    for (const optional of [[], ['--expected-pairing-file', expectation]]) {
      const result = spawnSync(process.execPath, [join(harness, 'cli.mjs'), command, ...base, ...optional], {
        encoding: 'utf8', env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, SCHEMII_QA_STATE_DIRECTORY: join(directory, 'state') },
      });
      assert.equal(result.status, 0, result.stdout + result.stderr);
      const receipt = JSON.parse(result.stdout);
      assert.equal(receipt.deploymentHeld, true);
      assert.equal(receipt.accountsHeld, true);
      assert.deepEqual(receipt.args, ['testing/provision.py', command === 'provision-viewer' ? 'viewer' : command,
        ...base, '--state-dir', join(directory, 'state'), ...optional]);
    }
    for (const invalid of [['--expected-pairing-file'], ['--expected-pairing-file', ''],
      ['--expected-pairing-file', expectation, '--expected-pairing-file', expectation]]) {
      const result = invoke(command, [...base, ...invalid]);
      assert.equal(result.status, 2, result.output);
      assert.match(result.output, /needs a value|Invalid or repeated option/);
    }
  }
});
