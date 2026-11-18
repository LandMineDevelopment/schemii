import { disposableGuestBinding, correlateGuestRequests, assertDisposableSource } from './deployment.mjs';
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { assertProviderAvailable, expandFixtureScenario, assertScenarioPrerequisite, readinessChecks } from './prerequisites.mjs';

const policy = { providerId: 'instance-codex', modelId: 'gpt-6-luna', reasoningEffort: 'default' };
const ready = { providers: [{ id: 'instance-codex', authenticated: true, available: true,
  models: [{ id: 'gpt-6-luna', status: 'active', reasoningLevels: ['default'] }] }] };

test('chat preflight accepts an authenticated active model and reasoning level', () => {
  assert.doesNotThrow(() => assertProviderAvailable(ready, policy));
});

test('mobile readback has a distinct result and requires the completed desktop write', () => {
  const base = {id:'create',title:'Create',viewportContracts:{
    desktop:{mode:'write',instructions:'Create and persist the assigned design'},
    mobile:{mode:'readback',dependsOnDesktop:true,instructions:'Inspect only the saved design'},
  }};
  const desktop = expandFixtureScenario(base,'schemii','desktop');
  const mobile = expandFixtureScenario(base,'schemii','mobile');
  assert.equal(desktop.readbackOnly,false);
  assert.equal(mobile.readbackOnly,true);
  assert.equal(mobile.instructions,'Inspect only the saved design');
  assert.equal(mobile.dependsOn,desktop.id);
  assert.match(mobile.title,/saved-state readback/);
  const lane = {scenarios:[desktop,mobile]};
  assert.throws(()=>assertScenarioPrerequisite(lane,mobile),/must pass/);
  desktop.functional='failed';
  assert.throws(()=>assertScenarioPrerequisite(lane,mobile),/must pass/);
  desktop.functional='passed';
  assert.doesNotThrow(()=>assertScenarioPrerequisite(lane,mobile));
  assert.throws(()=>expandFixtureScenario({...base,viewportContracts:{desktop:base.viewportContracts.desktop}},'schemii','mobile'),/valid mobile contract/);
});

test('chat preflight rejects missing grants, inactive models, and unavailable reasoning', () => {
  for (const status of [
    { providers: [] },
    { providers: [{ ...ready.providers[0], authenticated: false }] },
    { providers: [{ ...ready.providers[0], available: false }] },
    { providers: [{ ...ready.providers[0], models: [{ ...ready.providers[0].models[0], status: 'unavailable' }] }] },
  ]) assert.throws(() => assertProviderAvailable(status, policy), /Chat prerequisite unavailable/);
  assert.throws(() => assertProviderAvailable(ready, { ...policy, reasoningEffort: 'high' }), /Chat prerequisite unavailable/);
  assert.throws(() => assertProviderAvailable(ready, { modelId: 'gpt-6-luna' }), /providerId and modelId/);
});


test('initial empty-design check is enforced before dispatch; recovery retains identity and grants without erasing owned saved state',()=>{
  const initial={path:'/api/v1/schemii/workspaces/owned/design',phase:'initial',status:200,equals:{'content.tables.length':0}};
  const identity={path:'/api/v1/auth/me',equals:{'user.username':'qa_designer_012'}};
  const resource={path:'/api/v1/schemii/workspaces/owned',equals:{id:'owned'}};
  const lane={checks:[identity,resource,initial],scenarios:[]};
  const first=readinessChecks(lane);assert.equal(first.phase,'initial');assert.deepEqual(first.checks,[identity,resource,initial]);
  lane.dispatchedAt='2026-09-30T00:00:00Z';
  const recovered=readinessChecks(lane);assert.equal(recovered.phase,'recovery');assert.deepEqual(recovered.checks,[identity,resource]);
  assert.equal(recovered.reconciliationRequired,true);assert.deepEqual(recovered.initialStateChecks,[initial.path]);
  // No broad skip: an unclassified user check still applies on recovery.
  const unchanged={...initial};delete unchanged.phase;lane.checks.push(unchanged);
  assert.ok(readinessChecks(lane).checks.includes(unchanged));
  assert.throws(()=>readinessChecks({checks:[{...initial,phase:'skip-after-error'}]}),/initial or stable/);
});

test('retained legacy claim metadata supports recovery without claiming new initial fixtures were checked',()=>{
  const initial={path:'/api/v1/schemii/workspaces/owned/design',phase:'initial',equals:{'content.tables.length':0}};
  const retained={agent:'actual-previous-owner',checks:[initial],scenarios:[]};
  assert.equal(readinessChecks(retained).phase,'recovery');
  assert.equal(readinessChecks({checks:[initial],scenarios:[]}).phase,'initial');
});


const binding = {
  bootId:'12345678-1234-1234-1234-123456789012', commit:'a'.repeat(40), certificateSha256:'b'.repeat(64),
};

test('guest binding refuses network overrides, dirty source, wrong boot, and a non-VM runner', () => {
  const identity = {commit:binding.commit, dirty:false};
  assert.deepEqual(disposableGuestBinding(binding), binding);
  assert.throws(() => disposableGuestBinding({...binding, origin:'https://other.test.ts.net'}), /VM boot ID/);
  assert.throws(() => disposableGuestBinding({...binding, certificateSha256:'bad'}), /certificate SHA-256/);
  assert.doesNotThrow(() => assertDisposableSource(binding, identity, binding.bootId, true));
  assert.throws(() => assertDisposableSource(binding, {...identity,dirty:true}, binding.bootId, true), /clean checkout/);
  assert.throws(() => assertDisposableSource(binding, {...identity,commit:'b'.repeat(40)}, binding.bootId, true), /clean checkout/);
  assert.throws(() => assertDisposableSource(binding, identity, 'different-boot', true), /bound VM boot/);
  assert.throws(() => assertDisposableSource(binding, identity, binding.bootId, false), /bound VM boot/);
});

test('guest responses require fresh paired ingress/upstream records for each canonical endpoint', () => {
  const endpoints = [
    {url:'https://localhost:8001/',status:200,requestId:'a'.repeat(32)},
    {url:'https://localhost:8001/api-map',status:401,requestId:'b'.repeat(32)},
  ];
  const events = endpoints.map(({url,status,requestId}) => ({
    request_id:requestId, application_request_id:requestId, method:'GET', path:new URL(url).pathname, status,
  }));
  assert.equal(correlateGuestRequests(endpoints, [], events).length, 2);
  assert.throws(() => correlateGuestRequests(endpoints, [events[1]], events), /fresh request/);
  assert.throws(() => correlateGuestRequests(endpoints, [], [events[0]]), /fresh request/);
  assert.throws(() => correlateGuestRequests(endpoints, [], [...events, events[1]]), /fresh request/);
  for (const mismatch of [{application_request_id:'different'}, {method:'POST'}, {path:'/'}, {status:200}]) {
    assert.throws(() => correlateGuestRequests(endpoints, [], [events[0],{...events[1],...mismatch}]), /owned ingress and application/);
  }
  assert.throws(() => correlateGuestRequests([endpoints[0],endpoints[0]], [], events), /distinct ingress/);
});
