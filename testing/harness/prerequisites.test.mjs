import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { assertProviderAvailable, expandFixtureScenario, assertScenarioPrerequisite } from './prerequisites.mjs';

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
