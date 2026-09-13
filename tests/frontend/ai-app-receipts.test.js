import assert from 'node:assert/strict';
import test from 'node:test';
import { designExportReceipt, workspaceReceiptUrl } from '../../src/schemii/schemii/web/assets/ai-app-receipts.js';
const workspaceId = `ws_${'a'.repeat(32)}`;
const receipt = { workspaceId, designRevision: 1, sha256: 'b'.repeat(64), fileName: 'design.sql', mediaType: 'application/sql', content: 'CREATE TABLE sample (id int);' };
test('design export requires a typed receipt and only allows SQL or JSON downloads', () => {
  assert.equal(designExportReceipt(receipt).content, receipt.content);
  assert.ok(designExportReceipt({ ...receipt, fileName: 'design.json', mediaType: 'application/json', content: '{}' }));
  for (const changes of [{ fileName: '../design.sql' }, { fileName: 'design.html', mediaType: 'text/html' }, { content: null }, { workspaceId: 'bad' }, { sha256: null }]) assert.equal(designExportReceipt({ ...receipt, ...changes }), null);
});
test('only workspace creation and opening receipts supply navigation links', () => {
  assert.equal(workspaceReceiptUrl({ id: workspaceId }, 'create_workspace'), `/?workspace=${workspaceId}`);
  assert.equal(workspaceReceiptUrl({ workspace: { id: workspaceId }, created: true }, 'open_postgres_workspace'), `/?workspace=${workspaceId}`);
  assert.equal(workspaceReceiptUrl({ id: workspaceId }, 'delete_workspace'), null);
  assert.equal(workspaceReceiptUrl({ id: '//evil.test' }, 'open_postgres_workspace'), null);
});
