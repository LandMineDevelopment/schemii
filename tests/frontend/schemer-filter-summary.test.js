import assert from 'node:assert/strict';
import test from 'node:test';
import { appliedFilterSummary } from '../../src/schemii/schemer/web/filter-summary.js';

const scope = (id, inputs, extra = {}) => ({ id, label: id, kind: 'required', alternatives: [{ id: 'choose', label: 'Selected rule', inputs, conditions: inputs.map(input => ({ parameterId: input.id })) }], ...extra });

test('applied filter summaries use defaults for empty values while preserving false and zero', () => {
  const filters = [scope('Organization', [{ id: 'org', label: 'Root', defaultValue: 'HQ' }]), scope('Readiness', [{ id: 'ready', defaultValue: true }]), scope('Count', [{ id: 'count', defaultValue: 10 }])];
  assert.deepEqual(appliedFilterSummary(filters, { Organization: { values: { org: [] } }, Readiness: { values: { ready: false } }, Count: { values: { count: 0 } } }), ['Organization: HQ', 'Readiness: false', 'Count: 0']);
  assert.deepEqual(appliedFilterSummary([filters[0]], {}), ['Organization: HQ']);
});

test('only activated optional filters and restrictive alternatives appear in the summary', () => {
  const region = scope('Region', [{ id: 'region' }], { requirement: 'optional' });
  const fixed = scope('Employees', [], { alternatives: [{ id: 'active', label: 'Active only', inputs: [], conditions: [{ value: true }] }, { id: 'all', label: 'All', inputs: [], conditions: [] }] });
  assert.deepEqual(appliedFilterSummary([region, fixed], { Region: { values: { region: 'West' } } }), ['Employees: Active only']);
  assert.deepEqual(appliedFilterSummary([region, fixed], { Region: { active: true, values: { region: ['West', 'East'] } }, Employees: { alternativeId: 'all' } }), ['Region: West, East']);
});

test('multiple condition inputs retain labels and flag missing values', () => {
  const range = scope('Dates', [{ id: 'from', label: 'From' }, { id: 'to', label: 'To' }, { id: 'unused', label: 'Unused' }]);
  range.alternatives[0].conditions.pop();
  assert.deepEqual(appliedFilterSummary([range], { Dates: { values: { from: '2026-01-01' } } }), ['Dates: From: 2026-01-01 · To: Not set']);
});
