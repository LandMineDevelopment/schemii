import assert from 'node:assert/strict';
import test from 'node:test';
import { validateCopyHandoff } from '../../src/schemii/schemii/web/assets/ai-copy-handoff.js';
const origin = 'https://localhost:8001';
const base = '/api/v1/schemii/workspaces/ws_test/console/sessions/raw_test';
const receipt = { effect: 'browser_copy_handoff', direction: 'upload', method: 'PUT', sessionId: 'raw_test', ticketId: 'ticket_test', url: `${base}/copy/uploads/ticket_test`, sqlExecuted: false };
test('COPY handoff allows only the exact workspace session ticket and direction', () => {
  assert.equal(validateCopyHandoff(receipt, 'ws_test', origin).base, base);
  assert.ok(validateCopyHandoff({ ...receipt, direction: 'download', method: 'GET', url: `${base}/copy/downloads/ticket_test` }, 'ws_test', origin));
  for (const changes of [{ url: 'https://evil.test'+receipt.url }, { url: '/api/v1/settings' }, { method: 'DELETE' }, { url: receipt.url+'?x=1' }, { sessionId: '../settings' }, { sqlExecuted: true }, { direction: 'other' }]) assert.equal(validateCopyHandoff({ ...receipt, ...changes }, 'ws_test', origin), null);
  assert.equal(validateCopyHandoff(receipt, 'ws_other', origin), null);
});
