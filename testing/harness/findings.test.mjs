import test from 'node:test';
import assert from 'node:assert/strict';
import { findingInput, reportHTML } from './store.mjs';

const draft = {
  title: 'Saving a table leaves the dialog open',
  severity: 'medium',
  steps: '1. Open the scratch design.\n2. Save a table.',
  expected: 'The dialog closes and the table persists.',
  actual: 'The dialog remains open after the save completes.',
};

test('finding input requires a complete, bounded draft issue', () => {
  assert.deepEqual(findingInput(draft), draft);
  for (const field of ['title', 'steps', 'expected', 'actual']) {
    assert.throws(() => findingInput({ ...draft, [field]: ' ' }), /Finding .*non-empty text/);
  }
  assert.throws(() => findingInput({ ...draft, severity: 'unknown' }), /Finding severity/);
  assert.throws(() => findingInput({ ...draft, title: 'x'.repeat(181) }), /at most 180/);
});

test('report renders scenario-linked findings while escaping agent text', () => {
  const html = reportHTML({
    id: 'qa-example', status: 'finished-with-gaps', updatedAt: 'now', baseURL: 'https://localhost:8001',
    lanes: [], parallel: 1, findings: [{ id: 'finding-one', lane: 'lane-1', username: 'qa_designer_001', scenario: 'save-table',
      ...draft, title: '<script>alert(1)</script>', evidence: ['lane-1/screen.png'], verificationStatus: 'unverified' }],
  });
  assert.match(html, /Draft findings \(1\)/);
  assert.match(html, /save-table/);
  assert.match(html, /lane-1\/screen\.png/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
