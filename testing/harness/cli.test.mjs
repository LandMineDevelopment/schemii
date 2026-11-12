import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, writeFile, rm, readdir } from 'node:fs/promises';
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
