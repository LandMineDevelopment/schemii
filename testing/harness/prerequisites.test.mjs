import { disposablePreviewBinding, disposableRoute, assertDisposableSource } from './deployment.mjs';
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
  origin:'https://test-unit.test-network.ts.net', nodeId:'test-device',
  bootId:'12345678-1234-1234-1234-123456789012', commit:'a'.repeat(40),
};

test('disposable preview only accepts an exact non-desktop test origin and clean source binding', () => {
  assert.deepEqual(disposablePreviewBinding(binding), binding);
  for (const origin of [
    'http://test-unit.test-network.ts.net', 'https://test-unit.test-network.ts.net/',
    'https://test-unit.test-network.ts.net:443', 'https://user@test-unit.test-network.ts.net',
    'https://localhost:8001', 'https://test-unit.example.net',
    'https://other-device.taile4f57f.ts.net',
  ]) assert.throws(() => disposablePreviewBinding({...binding, origin}), /test-tailnet HTTPS origin/);
  assert.throws(() => disposablePreviewBinding({...binding, extra:'value'}), /test-tailnet HTTPS origin/);
  assert.doesNotThrow(() => assertDisposableSource(binding, {commit:binding.commit, dirty:false}, binding.bootId));
  assert.throws(() => assertDisposableSource(binding, {commit:binding.commit, dirty:true}, binding.bootId), /clean checkout/);
  assert.throws(() => assertDisposableSource(binding, {commit:'b'.repeat(40), dirty:false}, binding.bootId), /clean checkout/);
  assert.throws(() => assertDisposableSource(binding, {commit:binding.commit, dirty:false}, 'different-boot'), /this boot/);
});

test('disposable route requires the bound guest node and a single persistent private Serve proxy', () => {
  const host = new URL(binding.origin).hostname;
  const hostPort = `${host}:443`;
  const target = 'https+insecure://localhost:8001';
  const status = {BackendState:'Running', Self:{ID:binding.nodeId, DNSName:`${host}.`, TailscaleIPs:['100.65.0.1','fd7a:115c:a1e0::1']}};
  const serve = {TCP:{443:{HTTPS:true}}, Web:{[hostPort]:{Handlers:{'/':{Proxy:target}}}}};
  assert.deepEqual(disposableRoute(binding, status, serve), {nodeId:binding.nodeId, addresses:status.Self.TailscaleIPs, target});
  for (const fields of [{ID:'different'}, {DNSName:'other.test-network.ts.net.'}, {TailscaleIPs:['127.0.0.1']}]) {
    assert.throws(() => disposableRoute(binding, {...status, Self:{...status.Self, ...fields}}, serve), /bound guest node/);
  }
  for (const handlers of [{'/':{Proxy:'https+insecure://localhost:8002'}}, {'/':{Proxy:target}, '/api-map':{Proxy:target}}]) {
    assert.throws(() => disposableRoute(binding, status, {...serve, Web:{[hostPort]:{Handlers:handlers}}}), /bound guest node/);
  }
  assert.throws(() => disposableRoute(binding, status, {...serve, AllowFunnel:{[hostPort]:true}}), /tailnet-only/);
  assert.throws(() => disposableRoute(binding, status, {...serve, Foreground:{session:{}}}), /persistent/);
});
