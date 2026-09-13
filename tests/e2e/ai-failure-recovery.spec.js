import { test, expect } from '@playwright/test';

async function fixture(page, request) {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(w => w.database === 'schemii_test' && w.namespace === 'bookstore');
  let chat = { id: 'chat_recovery_fixture', workspaceId: workspace.id, providerId: 'fixture', modelId: 'model', revision: 1, status: 'working', capabilities: {}, title: 'Recovery' };
  let eventFailure = false, brokenStream = false, sent = [], cancelled = false, failedPolls = 0;
  const events = [
    { sequence: 1, kind: 'status', payload: { turnId: 'turn_fixture', stage: 'context', state: 'running', label: 'Loading context' } },
    { sequence: 2, kind: 'error', payload: { turnId: 'turn_fixture', tool: 'schemii_raw_console', code: 'console_session_expired' } },
    { sequence: 3, kind: 'status', payload: { turnId: 'turn_fixture', stage: 'model', state: 'running', label: 'Reviewing tool results' } },
  ];
  await page.route('**/api/v1/ai/status*', route => route.fulfill({ json: { healthy: true, providers: [{ id: 'fixture', name: 'Fixture', available: true, authenticated: true, models: [{ id: 'model', name: 'Model', status: 'active' }] }] } }));
  await page.route('**/api/v1/schemii/ai/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (path.endsWith('/stream') && brokenStream) { brokenStream = false; failedPolls++; return route.fulfill({ status: 503, json: { error: { message: 'Temporary stream failure' } } }); }
    if (path.endsWith('/cancel')) { cancelled = true; chat = { ...chat, status: 'idle' }; }
    if (path.endsWith('/messages') && route.request().method() === 'POST') { sent.push(route.request().postDataJSON()); chat = { ...chat, status: 'working' }; return route.fulfill({ json: { id: 'turn_next' } }); }
    const json = path.endsWith('/settings') ? { revision: 1, defaultProviderId: 'fixture', defaultModelId: 'model', defaultCapabilities: {}, permissionActions: [] }
      : path.endsWith('/chats') ? { chats: [chat] }
      : path.endsWith('/messages') ? { messages: [{ id: 'question', role: 'user', turnId: 'turn_fixture', text: 'Check the session', createdAt: new Date().toISOString() }] }
      : path.endsWith('/proposals') ? { proposals: [] }
      : path.endsWith('/operations') ? { operations: [] }
      : path.endsWith('/activity') ? { nextSequence: eventFailure ? 4 : 3, events: [...events, ...(eventFailure ? [{ sequence: 4, kind: 'error', payload: { turnId: 'turn_fixture', message: 'Provider failed' } }] : [])].filter(e => e.sequence > Number(url.searchParams.get('after') || 0)) }
      : path.endsWith('/transient-responses') ? { responses: [] }
      : path.endsWith('/stream') ? { turnId: 'turn_fixture', text: '' } : chat;
    return route.fulfill({ json });
  });
  await page.goto(`/?workspace=${workspace.id}`);
  await page.getByRole('button', { name: 'AI schema assistant' }).click();
  return { fail: () => { chat = { ...chat, status: 'failed' }; eventFailure = true; }, breakPoll: () => { brokenStream = true; }, sent: () => sent, cancelled: () => cancelled, failedPolls: () => failedPolls };
}

test('a failed tool keeps Stop available and stopping restores the drafted follow-up', async ({ page, request }) => {
  const state = await fixture(page, request);
  await expect(page.locator('.ai-run-title')).toHaveText('Working with this workspace');
  await expect(page.locator('.ai-run')).not.toHaveClass(/failed/);
  await page.locator('#ai-assistant-input').fill('Try again');
  await page.getByRole('button', { name: 'Stop assistant turn' }).click();
  await expect(page.locator('#ai-assistant-form button[type=submit]')).toBeEnabled();
  expect(state.cancelled()).toBe(true);
  await expect(page.locator('#ai-assistant-input')).toHaveValue('Try again');
  await page.locator('#ai-assistant-form button[type=submit]').click();
  await expect.poll(() => state.sent().length).toBe(1);
  expect(state.sent()[0].text).toBe('Try again');
});

test('terminal failure unlocks Send even after a temporary progress-request failure', async ({ page, request }) => {
  const state = await fixture(page, request);
  await page.locator('#ai-assistant-input').fill('Continue from the failure');
  state.breakPoll();
  await expect.poll(() => state.failedPolls()).toBe(1);
  state.fail();
  await expect(page.locator('#ai-assistant-form button[type=submit]')).toBeEnabled();
  await expect(page.locator('#ai-assistant-input')).toHaveValue('Continue from the failure');
  await page.locator('#ai-assistant-input').press('Enter');
  await expect.poll(() => state.sent().length).toBe(1);
  expect(state.sent()[0].text).toBe('Continue from the failure');
});
