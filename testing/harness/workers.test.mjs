import test from 'node:test';
import assert from 'node:assert/strict';
import { workerAssignment } from './workers.mjs';

const brief = {
  lane: 'lane-1',
  scenarios: [{ id: 'design-schemii-desktop', instructions: 'Save a design and reload it.' }],
  resources: { workspace: 'scratch-design-1' },
};

test('worker stays read-only without explicit write authorization', () => {
  const prompt = workerAssignment('/repo', '/private/session.json', brief);
  assert.match(prompt, /This lane is read-only/);
  assert.match(prompt, /Record write-dependent steps blocked/);
  assert.match(prompt, /finding .* --scenario EXACT_SCENARIO_ID/);
  assert.match(prompt, /Execute all feasible substeps/);
});

test('worker may write only exact disposable resources and operations', () => {
  const prompt = workerAssignment('/repo', '/private/session.json', {
    ...brief,
    writeAuthorization: {
      enabled: true,
      resources: ['scratch-design-1'],
      operations: ['save design', 'approve chat proposal'],
    },
  });
  assert.match(prompt, /may perform application writes through its owned browser session ONLY/);
  assert.match(prompt, /scratch-design-1/);
  assert.match(prompt, /approve chat proposal/);
  assert.match(prompt, /Do not modify retained data, other accounts/);
  assert.doesNotMatch(prompt, /This lane is read-only/);
});

test('empty or incomplete authorization does not enable writes', () => {
  for (const writeAuthorization of [
    { enabled: true, resources: [], operations: ['save design'] },
    { enabled: true, resources: ['scratch-design-1'], operations: [] },
    { enabled: false, resources: ['scratch-design-1'], operations: ['save design'] },
  ]) {
    assert.match(workerAssignment('/repo', '/private/session.json', { ...brief, writeAuthorization }), /This lane is read-only/);
  }
});
