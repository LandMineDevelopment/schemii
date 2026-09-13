import { expect, test } from '@playwright/test';

test('AI COPY handoff requires user file selection and sends bytes only to approved ticket', async ({ page }) => {
  await page.goto('/');
  const path = '/api/v1/schemii/workspaces/ws_test/console/sessions/raw_test/copy/uploads/ticket_test';
  let body = null;
  await page.route(`**${path}`, async route => { expect(route.request().method()).toBe('PUT'); body = route.request().postDataBuffer(); await route.fulfill({ json: { status: 'open', transactionStatus: 'intrans' } }); });
  await page.evaluate(async path => {
    const { openCopyHandoff } = await import('/assets/ai-copy-handoff.js');
    openCopyHandoff({ effect: 'browser_copy_handoff', direction: 'upload', method: 'PUT', sessionId: 'raw_test', ticketId: 'ticket_test', url: path, sqlExecuted: false, sql: 'COPY sample FROM STDIN WITH CSV' }, 'ws_test');
  }, path);
  const dialog = page.getByRole('dialog', { name: 'AI COPY transfer' });
  expect(body).toBeNull();
  await dialog.getByRole('button', { name: 'Upload selected file' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Choose a file first.');
  await dialog.getByLabel('COPY input file').setInputFiles({ name: 'sample.csv', mimeType: 'text/csv', buffer: Buffer.from('1,sample\n') });
  await dialog.getByRole('button', { name: 'Upload selected file' }).click();
  await expect(dialog.getByRole('status')).toHaveText('COPY upload completed.');
  expect(body.toString()).toBe('1,sample\n');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});
