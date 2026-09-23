import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshedSelections, staleModelMessage } from '../../src/schemii/schemer/web/dashboard-refresh.js';

const scope = (id, requirement = 'optional') => ({ id, kind: 'required', requirement, alternatives: [{ id: 'a', inputs: [{ id: 'value', type: 'text' }] }] });
const selection = value => ({ alternativeId: 'a', active: true, values: { value } });
const model = scopes => ({ definition: { scopes } });

test('refresh keeps compatible viewer filters and accepts new server defaults', () => {
  const scopes = [scope('region'), scope('required', 'required'), scope('new')];
  const previous = { modelId: 'm', selections: { region: selection('east'), required: selection('one') } };
  const next = { modelId: 'm', optionalFilters: ['region', 'new'], selections: { region: selection('west'), new: selection('default') } };
  const result = refreshedSelections(previous, model(scopes.slice(0, 2)), next, model(scopes));
  assert.deepEqual(result, { ...previous.selections, new: selection('default') });
  result.region.values.value = 'changed';
  assert.equal(previous.selections.region.values.value, 'east');
});

test('refresh drops removed filters and resets changed filter contracts or source models', () => {
  const before = [scope('removed'), scope('changed'), scope('unoffered')];
  const after = [scope('changed'), scope('unoffered')];
  after[0].alternatives[0].inputs[0].type = 'number';
  const previous = { modelId: 'm', selections: Object.fromEntries(before.map(s => [s.id, selection('old')])) };
  const next = { modelId: 'm', optionalFilters: ['changed'], selections: { changed: selection(42) } };
  assert.deepEqual(refreshedSelections(previous, model(before), next, model(after)), next.selections);
  assert.deepEqual(refreshedSelections(previous, model(before), { ...next, modelId: 'other' }, model(before)), next.selections);
});

test('stale report guidance gives viewers an achievable recovery step', () => {
  assert.match(staleModelMessage(false), /owner.*refresh/);
  assert.match(staleModelMessage(true), /Schemoo/);
});
