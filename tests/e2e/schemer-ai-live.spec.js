import { expect, test } from '@playwright/test';
import { checked, sharedReportFixture } from './helpers/shared-report-fixture.js';

// Explicit opt-in: sends one read-only prompt to a connected real provider.
// The report and chat are disposable; no source data is changed.
test('live Schemer assistant reads a dashboard and answers with its saved tile', async ({ page, request }) => {
  test.skip(process.env.SCHEMII_LIVE_AI !== '1', 'Requires explicit real-provider test opt-in');
  test.setTimeout(180_000);

  const status = await checked(await request.get('/api/v1/ai/status?refresh=true'));
  const codex = status.providers.find(provider => provider.id === 'openai-codex' && provider.authenticated);
  expect(codex, 'Connect a Codex account before running this live smoke test').toBeTruthy();
  const available = codex.models.filter(model => model.status === 'active');
  const model = available.find(model => model.id === 'gpt-5.6-luna') || available[0];
  expect(model, 'The connected Codex account needs an available model').toBeTruthy();

  const fixture = await sharedReportFixture(request);
  let chat;
  let failure;
  try {
    const settings = await checked(await request.get('/api/v1/schemer/ai/settings'));
    const modes = Object.fromEntries(settings.actions.map(action => [action.id,
      action.id === 'get_dashboard' ? 'automatic' : 'disabled']));
    chat = await checked(await request.post('/api/v1/schemer/ai/chats', { data: {
      dashboardId: fixture.dashboard.id, providerId: 'openai-codex', aiModelId: model.id, modes,
    } }), 201);

    await page.goto(`/schemer?dashboard=${fixture.dashboard.id}`);
    await page.getByRole('button', { name: 'Open dashboard assistant' }).click();
    const composer = page.getByRole('textbox', { name: 'Message to dashboard assistant' });
    await expect(composer).toBeVisible();
    await composer.fill('Use get_dashboard to read this saved dashboard. Name its tile and explain what it is configured to show. Do not run a query or edit anything.');
    const send = page.getByRole('button', { name: 'Send', exact: true });
    await expect(send).toBeEnabled();
    await send.click();

    const path = `/api/v1/schemer/ai/chats/${chat.id}`;
    await expect.poll(async () => (await checked(await request.get(path))).messages.some(message => message.role === 'user')).toBe(true);
    await expect.poll(async () => (await checked(await request.get(path))).status, { timeout: 120_000 }).toBe('idle');
    const result = await checked(await request.get(path));
    expect(result.error).toBeNull();
    expect(result.activity).toEqual(expect.arrayContaining([
      expect.objectContaining({ operation: 'get_dashboard', status: 'succeeded' }),
    ]));
    expect(result.activity.every(action => action.operation === 'get_dashboard')).toBe(true);
    await expect(page.locator('.model-ai-message--assistant').last()).toContainText('Sales by region');
    await expect(page.locator('.model-ai-status')).toHaveText('Ready');
  } catch (error) {
    failure = error;
  } finally {
    const errors = failure ? [failure] : [];
    if (chat) try {
      await checked(await request.delete(`/api/v1/schemer/ai/chats/${chat.id}`), 204);
    } catch (error) { errors.push(error); }
    try { await fixture.cleanup(); } catch (error) { errors.push(error); }
    if (errors.length === 1) throw errors[0];
    if (errors.length) throw new AggregateError(errors, 'Live Schemer assistant test and cleanup failed');
  }
});
