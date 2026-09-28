import { expect, test } from '@playwright/test';

test('icon-only controls remain legible before JavaScript hydration', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'The fixed desktop rail is checked once.');
  const baseURL = testInfo.project.use.baseURL;
  const context = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
    ignoreHTTPSErrors: true,
    javaScriptEnabled: false,
    storageState: testInfo.project.use.storageState,
    viewport: { width: 1280, height: 800 },
  });
  try {
    const page = await context.newPage();
    await page.goto('/');
    const createTable = page.getByRole('button', { name: 'Create table', exact: true });
    await expect(createTable).toBeVisible();
    const fallback = await createTable.evaluate(element => {
      const style = getComputedStyle(element, '::before');
      const bounds = element.getBoundingClientRect();
      const rail = document.querySelector('#tool-rail').getBoundingClientRect();
      return {
        content: style.content,
        display: style.display,
        label: element.getAttribute('aria-label'),
        buttonWidth: bounds.width,
        buttonRight: bounds.right,
        railWidth: rail.width,
        railRight: rail.right,
        hydrated: element.classList.contains('ui-icon-hydrated'),
      };
    });
    expect(fallback.content).toContain('Create table');
    expect(fallback.display).not.toBe('none');
    expect(fallback.label).toBe('Create table');
    expect(fallback.hydrated).toBe(false);
    expect(fallback.buttonWidth).toBeLessThanOrEqual(fallback.railWidth);
    expect(fallback.buttonRight).toBeLessThanOrEqual(fallback.railRight + 1);
  } finally {
    await context.close();
  }
});

test('catalog and API canvases pan from host focus and dialogs restore their invokers', async ({ page }) => {
  await page.goto('/');
  const catalogCanvas = page.locator('#canvas');
  const catalogStage = page.locator('#canvas-stage');
  const catalogBefore = await catalogStage.evaluate(element => element.style.transform);
  await catalogCanvas.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => catalogStage.evaluate(element => element.style.transform)).not.toBe(catalogBefore);

  const apiPan = await page.evaluate(async () => {
    const { ApiGraph } = await import('/assets/api-graph.js');
    const host = document.createElement('div');
    host.tabIndex = 0;
    const stage = document.createElement('div');
    host.append(stage);
    document.body.append(host);
    const graph = new ApiGraph({ host, stage, nodeLayer: document.createElement('div'), lines: document.createElementNS('http://www.w3.org/2000/svg', 'svg'), zoomOutput: null, onSelectOperation() {} });
    const before = stage.style.transform;
    host.focus();
    const event = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
    host.dispatchEvent(event);
    const result = { before, after: stage.style.transform, prevented: event.defaultPrevented };
    graph.viewport.destroy();
    host.remove();
    return result;
  });
  expect(apiPan.prevented).toBe(true);
  expect(apiPan.after).not.toBe(apiPan.before);

  await page.goto('/');
  const connectionsTrigger = page.locator('#connections-button');
  const connections = page.locator('#connections-dialog');
  await connectionsTrigger.click();
  await expect(connections).toBeVisible();
  const addConnection = page.locator('#add-connection-button');
  const editor = page.locator('#connection-editor-dialog');
  await addConnection.click();
  await expect(editor).toBeVisible();
  await editor.locator('[data-close-dialog]').first().click();
  await expect(editor).toBeHidden();
  await expect(addConnection).toBeFocused();

  await addConnection.click();
  await expect(editor).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(editor).toBeHidden();
  await expect(addConnection).toBeFocused();
  await connections.locator('[data-close-dialog]').first().click();
  await expect(connections).toBeHidden();
  await expect(connectionsTrigger).toBeFocused();
});
