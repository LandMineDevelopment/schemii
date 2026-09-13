import { expect, test } from '@playwright/test';

test('console actions use shared icons in the Copy and Delete header row', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'SQL', exact: true }).click();
  const header = page.locator('.sql-editor-panel .sql-pane-header');
  const actions = [
    ['Explain', 'explain'], ['Run & Analyze', 'analyze'],
    ['Open transactions', 'transactions'], ['COPY file', 'copy-file'],
    ['Copy current query', 'copy'], ['Clear current query', 'delete'],
  ];
  const boxes = [];
  for (const [name, icon] of actions) {
    const button = header.getByRole('button', { name, exact: true });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute('data-ui-icon', icon);
    await expect(button.locator('svg')).toHaveCount(1);
    await expect(button).toHaveText('');
    boxes.push(await button.boundingBox());
  }
  expect(Math.max(...boxes.map(box => box.y)) - Math.min(...boxes.map(box => box.y))).toBeLessThan(2);
  const bounds = await header.boundingBox();
  expect(boxes[0].x).toBeGreaterThanOrEqual(bounds.x);
  expect(boxes.at(-1).x + boxes.at(-1).width).toBeLessThanOrEqual(bounds.x + bounds.width);
  await expect(page.locator('.sql-editor-panel > .query-plan-actions')).toHaveCount(0);
  await page.screenshot({ path: `artifacts/console-toolbar-${test.info().project.name}.png`, fullPage: true });
});

test('write controls stay in one slim row on desktop and mobile', async ({ page, request }) => {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);
  await page.getByRole('button', { name: 'Write', exact: true }).click();
  const bar = page.locator('#sql-transaction-bar');
  await expect(bar).toBeVisible();
  const bounds = await bar.boundingBox();
  expect(bounds.height).toBeLessThanOrEqual(40);
  for (const name of ['Begin', 'Roll back', 'Commit']) {
    const button = bar.getByRole('button', { name, exact: true });
    await expect(button.locator('svg')).toHaveCount(1);
    const box = await button.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(bounds.y);
    expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height);
    expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  }
  await expect(bar.getByLabel('Auto-commit timing')).toBeDisabled();
  await bar.getByLabel('Auto-commit', { exact: true }).check();
  await bar.getByLabel('Auto-commit timing').selectOption('whole_run');
  await expect(page.locator('#sql-transaction-status')).toContainText('after the whole run');
  expect(await bar.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ path: `artifacts/console-slim-bar-${test.info().project.name}.png`, fullPage: true });
});
