import { expect, test } from '@playwright/test';

test('API map traces the installed Schemoo AI service completely', async ({ request }) => {
  const status = await request.get('/api/v1/ai/status');
  expect(status.ok()).toBeTruthy();
  const providers = (await status.json()).providers;
  for (const provider of providers) for (const model of provider.models) expect(model.reasoningLevels).toContain('default');
  const response = await request.get('/_developer/system');
  expect(response.ok()).toBeTruthy();
  const document = await response.json();
  const routes = document.routes.filter(route => route.id.includes('/schemoo/ai/'));
  expect(routes.length).toBeGreaterThan(0);
  expect(Object.values(document.analysis.truncated)).not.toContain(true);
  for (const route of document.routes) {
    expect(route.journey.status, route.id).toBe('complete');
    expect(route.journey.issues, route.id).toEqual([]);
  }
});

test('diagnostic permissions are independent, saved, and restored in Assistant settings', async ({ page, request }) => {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace).toBeTruthy();
  const serverSettings = await (await request.get('/api/v1/schemii/ai/settings')).json();
  expect(serverSettings.permissionActions.map(item => item.id)).toEqual(expect.arrayContaining(['query.explain', 'query.analyze']));
  const capabilities = { liveCatalog: false, structuredDataRead: false, monitorQueries: false,
    actionModes: { 'query.read': 'automatic', 'query.explain': 'disabled', 'query.analyze': 'disabled' } };
  let chat = { id: `chat_${'d'.repeat(32)}`, workspaceId: workspace.id, providerId: 'fixture', modelId: 'model',
    title: 'Diagnostic permission test', revision: 1, status: 'idle', capabilities };
  let settings = { ...serverSettings, defaultProviderId: 'fixture', defaultModelId: 'model', defaultCapabilities: capabilities };
  const saved = [];
  await page.route('**/api/v1/ai/status*', route => route.fulfill({ json: { healthy: true, enabled: true,
    providers: [{ id: 'fixture', name: 'Fixture', available: true, models: [{ id: 'model', name: 'Test model', status: 'active', reasoningLevels: ['low', 'medium', 'high'] }] }] } }));
  await page.route('**/api/v1/schemii/ai/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let json;
    if (path.endsWith('/preferences')) {
      const body = route.request().postDataJSON();
      saved.push(body.capabilities);
      chat = { ...chat, capabilities: body.capabilities, reasoningEffort: body.reasoningEffort, revision: chat.revision + 1 };
      settings = { ...settings, defaultCapabilities: body.capabilities, defaultReasoningEffort: body.reasoningEffort, revision: settings.revision + 1 };
      json = { chat, settings };
    } else if (path.endsWith('/settings')) json = settings;
    else if (path.endsWith('/chats')) json = { chats: [chat] };
    else if (path.endsWith('/messages')) json = { messages: [] };
    else if (path.endsWith('/proposals')) json = { proposals: [] };
    else if (path.endsWith('/operations')) json = { operations: [] };
    else if (path.endsWith('/activity')) json = { events: [], nextSequence: 0 };
    else if (path.endsWith('/transient-responses')) json = { responses: [] };
    else json = chat;
    await route.fulfill({ json });
  });
  await page.goto(`/?workspace=${workspace.id}`);
  await page.getByRole('button', { name: 'AI schema assistant' }).click();
  await page.locator('#ai-assistant-permissions').click();
  const dialog = page.locator('#ai-settings-dialog');
  const explain = dialog.locator('select[name="query.explain"]');
  const analyze = dialog.locator('select[name="query.analyze"]');
  const monitor = dialog.getByRole('checkbox', { name: /Monitor live queries/ });
  await expect(explain).toHaveValue('disabled');
  await expect(analyze).toHaveValue('disabled');
  await expect(monitor).not.toBeChecked();
  const allContext = dialog.getByRole('checkbox', { name: 'Enable all context access', exact: true });
  await allContext.check();
  await expect(monitor).toBeChecked();
  await monitor.uncheck();
  expect(await allContext.evaluate(input => input.indeterminate)).toBe(true);
  await allContext.check();
  await allContext.uncheck();
  await expect(monitor).not.toBeChecked();
  const queryBundle = dialog.getByRole('checkbox', { name: 'Select Queries', exact: true });
  await queryBundle.check();
  await expect(explain).toHaveValue('disabled');
  await expect(analyze).toHaveValue('disabled');
  expect(saved).toHaveLength(0);
  await queryBundle.uncheck();
  await explain.selectOption('automatic');
  await analyze.selectOption('ask');
  await monitor.check();
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(saved[0].actionModes).toMatchObject({ 'query.read': 'automatic', 'query.explain': 'automatic', 'query.analyze': 'ask' });
  expect(saved[0].monitorQueries).toBe(true);
  expect(saved[0].structuredDataRead).toBe(false);
  await page.locator('#ai-assistant-permissions').click();
  await expect(explain).toHaveValue('automatic');
  await expect(analyze).toHaveValue('ask');
  await expect(monitor).toBeChecked();
  await analyze.selectOption('disabled');
  await monitor.uncheck();
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(saved[1].actionModes['query.explain']).toBe('automatic');
  expect(saved[1].actionModes['query.analyze']).toBe('disabled');
  expect(saved[1].monitorQueries).toBe(false);
  const reasoning = page.getByRole('combobox', { name: 'AI reasoning level', exact: true });
  await reasoning.selectOption('high');
  await expect(reasoning).toHaveValue('high');
  expect(chat.reasoningEffort).toBe('high');
  expect(settings.defaultReasoningEffort).toBe('high');
  await page.locator('#ai-assistant-permissions').click();
  await expect(dialog.getByRole('combobox', { name: 'Reasoning level', exact: true })).toHaveValue('high');
  await dialog.getByRole('combobox', { name: 'Reasoning level', exact: true }).selectOption('low');
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(reasoning).toHaveValue('low');
  expect(chat.capabilities.actionModes['query.explain']).toBe('automatic');
});
