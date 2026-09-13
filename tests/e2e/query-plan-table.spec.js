import { expect, test } from '@playwright/test';

const plan = {
  'Planning Time': 0.125,
  'Execution Time': 12.75,
  Plan: {
    'Node Type': 'Nested Loop', 'Join Type': 'Inner', 'Startup Cost': 0.25, 'Total Cost': 45.5,
    'Plan Rows': 20, 'Actual Rows': 4, 'Actual Loops': 1, 'Actual Total Time': 12.5,
    Plans: [
      { 'Node Type': 'Seq Scan', 'Relation Name': 'orders', 'Alias': 'o', 'Startup Cost': 0,
        'Total Cost': 10, 'Plan Rows': 2, 'Actual Rows': 2, 'Actual Loops': 1,
        'Actual Total Time': 2.5, 'Filter': '(total > 100)', 'Rows Removed by Filter': 98,
        'Shared Hit Blocks': 5 },
      { 'Node Type': 'Index Scan', 'Relation Name': 'customers', 'Index Name': 'customers_pkey',
        'Startup Cost': 0.25, 'Total Cost': 1.5, 'Plan Rows': 1, 'Actual Rows': 2,
        'Actual Loops': 2, 'Actual Total Time': 4.5, 'Index Cond': '(id = o.customer_id)' },
    ],
  },
};

async function mount(page, request, analyze = true, fixture = plan) {
  const workspaces = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace).toBeTruthy();
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);
  await expect(page.getByRole('textbox', { name: 'Unsaved SQL draft' })).toBeVisible();
  await page.evaluate(async ({ plan, analyze }) => {
    const { createQueryPlanView } = await import('/assets/common/query-plan.js');
    if (!analyze) {
      const stripMeasurements = node => {
        for (const key of Object.keys(node)) {
          if (key.startsWith('Actual ') || key.startsWith('Shared ') || key === 'Rows Removed by Filter') delete node[key];
        }
        for (const child of node.Plans || []) stripMeasurements(child);
      };
      stripMeasurements(plan.Plan);
      delete plan['Execution Time'];
    }
    // Use the real results surface and shipped CSS without submitting fixture SQL.
    const target = document.querySelector('#sql-results');
    target.replaceChildren(createQueryPlanView({ plan, analyze, sql: 'SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id WHERE total > 100', settings: {} }));
    target.scrollIntoView();
  }, { plan: fixture, analyze });
  await page.locator('#show-sql-results').click();
  return page.locator('.query-plan-view');
}

test('plan table preserves hierarchy, per-loop measurements and selected operation properties', async ({ page, request }) => {
  const view = await mount(page, request);
  const table = view.locator('.query-plan-table');
  await expect(table.getByRole('columnheader')).toHaveText(['Operation', 'Object', 'Cost', 'Est. rows', 'Actual rows', 'Loops', 'Time (ms)']);
  await expect(table.locator('tbody tr:visible')).toHaveCount(3);
  const indexRow = table.getByRole('row').filter({ has: page.getByRole('button', { name: 'Select Index Scan', exact: true }) });
  await expect(indexRow).toContainText('customers');
  // PostgreSQL reports rows/time per loop, not multiplied totals.
  await expect(indexRow).toContainText('4.5');
  await view.getByRole('button', { name: 'Select Seq Scan', exact: true }).click();
  const properties = view.locator('.query-plan-properties');
  await expect(properties).toContainText('Filter');
  await expect(properties).toContainText('(total > 100)');
  await expect(properties).toContainText('98');
  await expect(properties).toContainText('Shared Hit Blocks');
  await view.getByRole('button', { name: 'Collapse Nested Loop', exact: true }).click();
  await expect(table.locator('tbody tr:visible')).toHaveCount(1);
  await view.getByRole('button', { name: 'Expand Nested Loop', exact: true }).click();
  await expect(table.locator('tbody tr:visible')).toHaveCount(3);
  await view.getByRole('button', { name: 'Collapse all', exact: true }).click();
  await expect(table.locator('tbody tr:visible')).toHaveCount(1);
  await view.getByRole('button', { name: 'Expand all', exact: true }).click();
  await expect(table.locator('tbody tr:visible')).toHaveCount(3);
  await view.getByRole('button', { name: 'Select Index Scan', exact: true }).click();
  await expect(properties).toContainText('(id = o.customer_id)');
  await expect(properties).toContainText('customers_pkey');
  if (test.info().project.name === 'android-chromium') {
    const horizontal = await table.evaluate(node => {
      let parent = node.parentElement;
      while (parent && parent !== document.body) {
        if (parent.scrollWidth > parent.clientWidth && ['auto', 'scroll'].includes(getComputedStyle(parent).overflowX)) {
          parent.scrollLeft = parent.scrollWidth;
          return parent.scrollLeft > 0;
        }
        parent = parent.parentElement;
      }
      return false;
    });
    expect(horizontal, 'wide plan columns should scroll inside their own container').toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: `artifacts/query-plan-table-${test.info().project.name}.png`, fullPage: true });
});

test('estimated plans omit actual measurement columns', async ({ page, request }) => {
  const view = await mount(page, request, false);
  await expect(view.locator('.query-plan-table').getByRole('columnheader')).toHaveText(['Operation', 'Object', 'Cost', 'Est. rows']);
});


test('never-executed operations do not report zero as a measured time or row count', async ({ page, request }) => {
  const fixture = structuredClone(plan);
  Object.assign(fixture.Plan.Plans[1], { 'Actual Rows': 0, 'Actual Loops': 0, 'Actual Total Time': 0 });
  const view = await mount(page, request, true, fixture);
  const row = view.locator('.query-plan-table').getByRole('row').filter({ has: page.getByRole('button', { name: 'Select Index Scan', exact: true }) });
  await expect(row.getByRole('cell').nth(4)).toHaveText('—');
  await expect(row.getByRole('cell').nth(5)).toHaveText('0');
  await expect(row.getByRole('cell').nth(6)).toHaveText('—');
  await row.getByRole('button', { name: 'Select Index Scan', exact: true }).click();
  await expect(view.getByRole('region', { name: 'Operation details' })).toContainText('Never executed');
  await expect(view.locator('.query-plan-warning')).not.toContainText('differ');
});
