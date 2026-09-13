import { expect, test } from '@playwright/test';

for (const attribute of ['data-permission-action', 'data-action']) {
  test(`permission bundles keep independent draft values (${attribute})`, async ({ page }) => {
    await page.goto('/');
    await page.evaluate(async attribute => {
      const { renderPermissionBundles } = await import('/assets/common/ai-permissions.js');
      document.body.replaceChildren();
      const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/assets/common/ai-permissions.css'; document.head.append(link);
      const host = document.createElement('div'); host.style.maxWidth = '600px'; document.body.append(host);
      const actions = [
        { id: 'tables.create', label: 'Create tables', group: 'Design' },
        { id: 'tables.delete', label: 'Delete tables', group: 'Design', destructive: true },
        { id: 'query.read', label: 'Run read SQL', group: 'Queries' },
      ];
      window.permissionHost = host;
      window.reopenPermissions = modes => { window.permissionEditor = renderPermissionBundles(host, actions, modes, { attribute }); };
      window.reopenPermissions({ 'tables.create': 'ask', 'tables.delete': 'disabled', 'query.read': 'automatic' });
    }, attribute);
    const all = page.getByRole('checkbox', { name: 'Select all actions', exact: true });
    const tables = page.getByRole('checkbox', { name: 'Select Tables', exact: true });
    const create = page.getByRole('combobox', { name: 'Create tables', exact: true });
    const remove = page.getByRole('combobox', { name: 'Delete tables', exact: true });
    await expect(page.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    await tables.check();
    await expect(create).toHaveValue('ask');
    await expect(remove).toHaveValue('disabled');
    expect(await all.evaluate(input => input.indeterminate)).toBe(true);
    await page.getByRole('checkbox', { name: 'Select Delete tables', exact: true }).uncheck();
    expect(await tables.evaluate(input => input.indeterminate)).toBe(true);
    await page.getByRole('combobox', { name: 'Permission for selected actions' }).selectOption('automatic');
    await page.getByRole('button', { name: 'Apply selected' }).click();
    await expect(create).toHaveValue('automatic');
    await expect(remove).toHaveValue('disabled');
    await remove.selectOption('ask');
    const saved = await page.evaluate(attribute => Object.fromEntries([...window.permissionHost.querySelectorAll(`select[${attribute}]`)].map(input => [input.getAttribute(attribute), input.value])), attribute);
    expect(saved).toEqual({ 'tables.create': 'automatic', 'tables.delete': 'ask', 'query.read': 'automatic' });
    await page.evaluate(saved => window.reopenPermissions(saved), saved);
    await expect(all).not.toBeChecked();
    await expect(remove).toHaveValue('ask');
    await all.check();
    await expect(tables).toBeChecked();
    await page.evaluate(() => window.permissionEditor.setBusy(true));
    await expect(all).toBeDisabled();
    await expect(create).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    await page.evaluate(() => window.permissionEditor.setBusy(false));
    await all.uncheck();
    await expect(tables).not.toBeChecked();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
  });
}
