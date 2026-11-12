import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { installConsoleCleanup } from "./helpers/console-cleanup.js";

installConsoleCleanup(test);

const SQL_CONSOLE_DEMO = readFileSync(
  new URL("../../dev/postgres/sql-console-demo.sql", import.meta.url),
  "utf8",
);

async function databaseWorkspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const workspace = body.workspaces.find(item => item.connectionId && item.database);
  expect(workspace, "the development seed should provide a database-backed workspace").toBeTruthy();
  return workspace;
}

async function expectEditorContained(page) {
  await expect.poll(() => page.locator(".sql-editor-panel").evaluate(panel => {
    const editor = panel.querySelector("textarea").getBoundingClientRect();
    const footer = panel.querySelector("footer").getBoundingClientRect();
    const bounds = panel.getBoundingClientRect();
    return {
      usableEditor: editor.height >= 260,
      panelFitsEditorAndControls: bounds.height >= editor.height + 90,
      editorContained: editor.bottom <= bounds.bottom,
      footerAfterEditor: footer.top >= editor.bottom,
      footerContained: footer.bottom <= bounds.bottom,
      noClippedContent: panel.scrollHeight <= panel.clientHeight + 1,
    };
  })).toEqual({
    usableEditor: true,
    panelFitsEditorAndControls: true,
    editorContained: true,
    footerAfterEditor: true,
    footerContained: true,
    noClippedContent: true,
  });
}

async function attachConsoleLayout(page, testInfo, name, { captureImage = true } = {}) {
  const geometry = await page.locator("#sql-workspace").evaluate(workspace => {
    const bounds = node => {
      const rect = node.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom, height: rect.height, width: rect.width,
        scrollHeight: node.scrollHeight, clientHeight: node.clientHeight, scrollTop: node.scrollTop };
    };
    return {
      workspace: bounds(workspace), panes: bounds(workspace.querySelector("#sql-panes")),
      editor: bounds(workspace.querySelector(".sql-editor-panel")),
      footer: bounds(workspace.querySelector(".sql-editor-panel footer")),
      resultsHeader: bounds(workspace.querySelector("#show-sql-results")),
      resultsPanel: bounds(workspace.querySelector(".sql-results-panel")),
      results: bounds(workspace.querySelector("#sql-results")),
      toolbar: bounds(document.querySelector("#tool-rail")),
      guidanceExpanded: document.querySelector(".compatibility-import-details").open,
    };
  });
  await testInfo.attach(`${name}-geometry`, { body: Buffer.from(JSON.stringify(geometry, null, 2)), contentType: "application/json" });
  if (captureImage) await testInfo.attach(`${name}-layout`, { body: await page.screenshot(), contentType: "image/png" });
}

test("workspace toolbar follows Tables, Views, and SQL contexts", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=tables`);
  const toolbar = page.locator("#tool-rail");

  await expect(toolbar).toHaveAttribute("aria-label", "Tables tools");
  await expect(toolbar.getByRole("button", { name: "Create table" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Run all statements" })).toBeHidden();

  await page.getByRole("button", { name: "Views", exact: true }).click();
  await expect(toolbar).toHaveAttribute("aria-label", "Views tools");
  await expect(toolbar.getByRole("button", { name: "Browse views" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Create view" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Create table" })).toBeHidden();

  await page.getByRole("button", { name: "SQL", exact: true }).click();
  await expect(toolbar).toHaveAttribute("aria-label", "SQL tools");
  await expect(toolbar.getByRole("button", { name: "New query" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Run current statement" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Run all statements" })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Undo design change" })).toBeHidden();
});

for (const loadingPhase of ["workspaces", "design snapshot"]) {
  test(`a layer chosen while ${loadingPhase} loads survives startup and browser history`, async ({ page, request }) => {
    const workspace = await databaseWorkspace(request);
    const endpoint = loadingPhase === "workspaces"
      ? "**/api/v1/schemii/workspaces"
      : `**/api/v1/schemii/workspaces/${workspace.id}/design/snapshot`;
    let release, observed;
    const held = new Promise(resolve => { release = resolve; });
    const requested = new Promise(resolve => { observed = resolve; });
    await page.route(endpoint, async route => {
      observed();
      await held;
      await route.continue();
    });
    try {
      await page.goto(`/?workspace=${workspace.id}&layer=tables`);
      await requested;
      const toolbar = page.locator("#tool-rail");
      await page.getByRole("button", { name: "Views", exact: true }).click();
      await expect(toolbar).toHaveAttribute("aria-label", "Views tools");
      release();
      await expect(toolbar.getByRole("button", { name: "Create view", exact: true })).toBeEnabled();
      await expect(toolbar).toHaveAttribute("aria-label", "Views tools");
      await expect(page).toHaveURL(new RegExp(`workspace=${workspace.id}.*layer=views`));

      await page.getByRole("button", { name: "SQL", exact: true }).click();
      await expect(toolbar).toHaveAttribute("aria-label", "SQL tools");
      await page.goBack();
      await expect(toolbar).toHaveAttribute("aria-label", "Views tools");
      await page.goForward();
      await expect(toolbar).toHaveAttribute("aria-label", "SQL tools");
    } finally { release(); }
  });
}

for (const landing of ["empty", "stale"]) {
  for (const layer of ["SQL", "Views"]) {
    test(`a layer chosen on an ${landing} workspace landing survives delayed startup (${layer})`, async ({ page }) => {
      let release, observed;
      const held = new Promise(resolve => { release = resolve; });
      const requested = new Promise(resolve => { observed = resolve; });
      await page.route("**/api/v1/schemii/workspaces", async route => {
        observed();
        await held;
        await route.fulfill({ json: { workspaces: [] } });
      });
      try {
        await page.goto(landing === "empty" ? "/" : `/?workspace=ws_${"f".repeat(32)}&layer=tables`);
        await requested;
        const toolbar = page.locator("#tool-rail");
        await page.getByRole("button", { name: layer, exact: true }).click();
        await expect(toolbar).toHaveAttribute("aria-label", `${layer} tools`);
        release();
        await expect(page.locator("#catalog-state")).toContainText("Open a schema workspace");
        await expect(page.locator("#workspace-title")).toHaveText("No workspace open");
        await expect(toolbar).toHaveAttribute("aria-label", `${layer} tools`);
        await expect(page.getByRole("button", { name: layer, exact: true })).toHaveAttribute("aria-pressed", "true");
        await expect(page).not.toHaveURL(/workspace=/);
      } finally { release(); }
    });
  }
}

test("safe-read Console runs the cursor statement and renders PostgreSQL column types", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  await expect(page.getByRole("heading", { name: "SQL Console" })).toBeVisible();
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await expect(editor).toHaveAttribute("rows", "14");
  await expect(editor).toHaveAttribute("placeholder", /Write PostgreSQL here/);
  const editorBox = await editor.boundingBox();
  expect(editorBox?.height).toBeGreaterThanOrEqual(260);
  const panelBox = await page.locator(".sql-editor-panel").boundingBox();
  expect(panelBox?.height).toBeGreaterThanOrEqual((editorBox?.height ?? 0) + 90);
  expect(editorBox?.y + editorBox?.height).toBeLessThanOrEqual(
    (panelBox?.y ?? 0) + (panelBox?.height ?? 0),
  );
  await expectEditorContained(page);
  await expect(page.getByRole("button", { name: "Run current statement" })).toBeVisible();
  await editor.fill("SELECT current_database() AS database, 42::integer AS answer;");
  await page.getByRole("button", { name: "Run current statement" }).click();

  const result = page.locator(".sql-result-card");
  await expect(result).toBeVisible();
  await expect(result.locator("thead")).toContainText("database");
  await expect(result.locator("thead")).toContainText("name");
  await expect(result.locator("thead")).toContainText("answer");
  await expect(result.locator("thead")).toContainText("integer");
  await expect(result.locator("tbody")).toContainText(workspace.database);
  await expect(result.locator("tbody")).toContainText("42");
});

for (const guidance of ["collapsed", "expanded"]) {
  test(`SQL Console keeps editor, footer, and results reachable with ${guidance} compatibility guidance in a short viewport`, async ({ page, request }, testInfo) => {
    await page.setViewportSize(testInfo.project.use.isMobile
      ? { width: 390, height: 620 }
      : { width: 1280, height: 540 });
    const workspace = await databaseWorkspace(request);
    const imported = { ...workspace, importSummary: {
      ...workspace.importSummary, complete: false,
      catalogFingerprint: workspace.importSummary?.catalogFingerprint ?? "a".repeat(64),
      catalogCapturedAt: workspace.importSummary?.catalogCapturedAt ?? "2026-11-11T12:00:00Z",
      importedObjects: workspace.importSummary?.importedObjects ?? {},
      migrationGuidance: "Only changes outside the proven protected dependency scope may be reviewed.",
      issues: [{ category: "table", objectName: "public.partitioned_orders", featureCode: "partitioning",
        reason: "Partition structure is protected and omitted from the editable design.",
        dependencyComplete: false, unknownReasons: ["Partition dependency evidence is incomplete."], protectedScope: [] }],
    } };
    // Exercise the actual compatibility renderer without changing the retained workspace.
    await page.route("**/api/v1/schemii/workspaces", async route => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: {
        ...body, workspaces: body.workspaces.map(item => item.id === workspace.id ? imported : item),
      } });
    });
    await page.route(`**/api/v1/schemii/workspaces/${workspace.id}`, route => route.fulfill({ json: imported }));
    await page.goto(`/?workspace=${workspace.id}&layer=sql`);
    const banner = page.locator("#workspace-compatibility");
    await expect(banner).toBeVisible();
    await expect(page.locator("#review-migration-button")).toBeEnabled();
    if (guidance === "expanded") {
      await banner.getByText("known limits and recovery", { exact: false }).click();
      await expect(banner.locator("details")).toHaveAttribute("open", "");
    }
    await expectEditorContained(page);
    const panes = page.locator("#sql-panes");
    await panes.evaluate(node => { node.scrollTop = node.scrollHeight; });
    await attachConsoleLayout(page, testInfo, "editor");
    const footer = page.locator(".sql-editor-panel footer");
    await expect(footer).toBeInViewport({ ratio: 1 });
    const showResults = page.locator("#show-sql-results");
    await expect(showResults).toBeInViewport({ ratio: 1 });
    if (testInfo.project.use.isMobile) {
      const resultsHeaderBox = await showResults.boundingBox();
      const railBox = await page.locator("#tool-rail").boundingBox();
      expect(resultsHeaderBox.y + resultsHeaderBox.height).toBeLessThanOrEqual(railBox.y + 1);
    }
    await expect(banner.locator("details")).toHaveJSProperty("open", guidance === "expanded");
    // The collapsed header is a real pointer target, including above the mobile rail.
    await showResults.click();
    await expect(showResults).toHaveAttribute("aria-expanded", "true");
    await page.locator("#show-sql-editor").click();
    await expectEditorContained(page);

    const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
    await editor.fill("SELECT number, repeat('wide result ', 50) AS long_value FROM generate_series(1, 150) AS generated(number) ORDER BY number;");
    await page.getByRole("button", { name: "Run current statement" }).click();
    await expect(page.locator("#show-sql-results")).toHaveAttribute("aria-expanded", "true");
    // The inactive editor's hidden content must not hold the results pane open.
    await expect.poll(() => page.locator(".sql-editor-panel").evaluate(panel =>
      panel.getBoundingClientRect().height <= panel.querySelector("header").getBoundingClientRect().height + 2,
    )).toBe(true);
    const results = page.locator("#sql-results");
    const rows = results.locator("tbody tr");
    await expect(rows).toHaveCount(100);
    await expect(results.locator("thead")).toContainText("integer");
    await results.scrollIntoViewIfNeeded();
    await attachConsoleLayout(page, testInfo, "results-before-assertions", { captureImage: false });
    await expect.poll(() => results.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const parents = [node.closest(".sql-results-panel"), node.closest("#sql-panes")];
      const contained = parents.every(parent => {
        const outer = parent.getBoundingClientRect();
        const top = outer.top + parent.clientTop;
        return rect.top >= top - 1 && rect.bottom <= top + parent.clientHeight + 1;
      });
      const style = getComputedStyle(node);
      const minimumData = [".sql-result-card > header", "thead", "tbody tr"].reduce((height, selector) =>
        height + node.querySelector(selector).getBoundingClientRect().height, 0,
      ) + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      // Fractional flex heights can round the outer scroller's edge by <1 CSS px.
      // The actual column header and data cell below still require full intersection.
      const inViewport = rect.top >= -1 && rect.bottom <= innerHeight + 1
        && rect.left >= -1 && rect.right <= innerWidth + 1;
      return contained && inViewport && node.clientHeight >= minimumData;
    })).toBe(true);
    const numberHeader = results.locator("thead th").filter({ hasText: "number" });
    const firstValue = rows.first().locator("td").first();
    await expect(numberHeader).toBeInViewport({ ratio: 1 });
    await expect(firstValue).toHaveText("1");
    await expect(firstValue).toBeInViewport({ ratio: 1 });
    await expect.poll(() => results.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
    // Exercise paging through the visible data scroller, as a pointer user does.
    await results.hover();
    await page.mouse.wheel(0, await results.evaluate(node => node.scrollHeight));
    await expect(rows).toHaveCount(150);
    await expect(rows.last()).toContainText("150");
    await results.evaluate(node => { node.scrollTop = 0; });
    await expect(numberHeader).toBeInViewport({ ratio: 1 });
    await expect(firstValue).toBeInViewport({ ratio: 1 });
    await expect.poll(() => results.locator(".sql-result-viewport").evaluate(node =>
      node.scrollWidth > node.clientWidth,
    )).toBe(testInfo.project.use.isMobile === true);
    await expect.poll(() => page.locator(".app-shell, #sql-workspace, #sql-panes").evaluateAll(nodes =>
      nodes.every(node => node.scrollWidth <= node.clientWidth + 1),
    )).toBe(true);
    await expect(banner.locator("details")).toHaveJSProperty("open", guidance === "expanded");
    await attachConsoleLayout(page, testInfo, "results");

    await page.locator("#show-sql-editor").click();
    await expectEditorContained(page);
    await expect(editor).toHaveValue(/SELECT number/);
  });
}

test("safe-read Console explains rejected mutation without clearing the draft", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  const draft = "DELETE FROM definitely_not_a_console_target";
  await editor.fill(draft);
  await page.getByRole("button", { name: "Run current statement" }).click();

  await expect(page.locator("#sql-results")).toContainText("Query could not start");
  await expect(page.locator("#sql-results")).toContainText("Read-only Console accepts");
  await expect(editor).toHaveValue(draft);
});

test("a SQL deep link preloads one browser-local workspace draft", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  const fragment = new URLSearchParams({ sql: SQL_CONSOLE_DEMO }).toString();

  await page.goto(`/?workspace=${workspace.id}&layer=sql#${fragment}`);

  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await expect(editor).toHaveValue(SQL_CONSOLE_DEMO);
  await expect(page).not.toHaveURL(/#.*sql=/);

  await editor.evaluate((node, cursor) => {
    node.setSelectionRange(cursor, cursor);
    node.dispatchEvent(new Event("select", { bubbles: true }));
  }, SQL_CONSOLE_DEMO.indexOf("WITH author_sales"));
  await editor.press("Control+Enter");
  await expect(page.locator(".sql-result-card tbody")).toContainText("Maya Chen");

  await page.getByRole("button", { name: "Editor" }).click();
  const writeStart = SQL_CONSOLE_DEMO.indexOf("CREATE TEMP TABLE");
  await editor.evaluate((node, selection) => {
    node.setSelectionRange(selection.start, selection.end);
    node.dispatchEvent(new Event("select", { bubbles: true }));
  }, { start: writeStart, end: SQL_CONSOLE_DEMO.length });
  await page.getByRole("button", { name: "Write", exact: true }).click();
  await page.getByRole("button", { name: "Run selection" }).click();
  await page.getByRole("tab", { name: /^Result 3 SELECT/ }).click();
  await expect(page.locator(".sql-result-card tbody")).toContainText("This row exists only inside");
  await expect(page.locator("#sql-transaction-status")).toContainText("No transaction open");
});

test("SQL typed before workspace startup completes is kept with the requested workspace", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  let release, observed;
  const held = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { observed = resolve; });
  await page.route("**/api/v1/schemii/workspaces", async route => {
    observed();
    await held;
    await route.continue();
  });
  try {
    await page.goto(`/?workspace=${workspace.id}&layer=sql`);
    await requested;
    const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
    const sql = "SELECT 2468 AS entered_during_startup;";
    await editor.fill(sql);
    release();
    await expect(page.getByRole("heading", { name: "SQL Console" })).toBeVisible();
    await expect(editor).toHaveValue(sql);
    await page.getByRole("button", { name: "Run current statement" }).click();
    await expect(page.locator(".sql-result-card tbody")).toContainText("2468");
  } finally { release(); }
});

test("selection, cursor, and Run all target the intended statements", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  const sql = "SELECT 11 AS marker;\nSELECT 22 AS marker;\nSELECT 33 AS marker;";
  await editor.fill(sql);
  await expect(page.getByRole("button", { name: "Run current statement" })).toBeEnabled();
  await editor.evaluate((node, cursor) => {
    node.setSelectionRange(cursor, cursor);
    node.dispatchEvent(new Event("select", { bubbles: true }));
  }, sql.indexOf("22"));
  await editor.press("Control+Enter");
  await expect(page.locator(".sql-result-card tbody")).toContainText("22");
  await expect(page.locator(".sql-result-card tbody")).not.toContainText("11");

  await page.getByRole("button", { name: "Editor" }).click();
  await editor.evaluate((node, end) => {
    node.setSelectionRange(0, end);
    node.dispatchEvent(new Event("select", { bubbles: true }));
  }, sql.indexOf("SELECT 33"));
  await page.getByRole("button", { name: "Run selection" }).click();
  await expect(page.locator(".sql-result-tab")).toHaveCount(2);
  await expect(page.locator(".sql-result-card")).toHaveCount(1);

  await page.getByRole("button", { name: "Editor" }).click();
  await page.getByRole("button", { name: "Run all" }).click();
  await expect(page.locator(".sql-result-tab")).toHaveCount(3);
  await expect(page.locator(".sql-result-card")).toHaveCount(1);
});

test("query tabs persist drafts and metadata-saved queries reopen from the server drawer", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  const savedName = `Reusable first query ${Date.now()}`;
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await editor.fill("SELECT 101 AS first_query;");
  await page.getByRole("button", { name: "Rename Query 1" }).click();
  await page.getByRole("textbox", { name: "Query name" }).fill("First query");
  await page.getByRole("textbox", { name: "Query name" }).press("Enter");

  await page.getByRole("button", { name: "New query" }).click();
  await editor.fill("SELECT 202 AS second_query;");
  await expect(page.locator(".sql-query-tab")).toHaveCount(2);
  await page.getByRole("tab", { name: "First query", exact: true }).click();
  await expect(editor).toHaveValue("SELECT 101 AS first_query;");

  await page.getByRole("button", { name: "Save current query" }).click();
  const saveDialog = page.getByRole("dialog", { name: "Save query" });
  await saveDialog.getByRole("textbox", { name: "Name" }).fill(savedName);
  await saveDialog.getByRole("button", { name: "Save query", exact: true }).click();
  await expect(page.locator("#sql-query-drawer")).toContainText(savedName);

  await page.reload();
  await expect(page.locator(".sql-query-tab")).toHaveCount(2);
  await expect(editor).toHaveValue("SELECT 101 AS first_query;");
  await page.getByRole("button", { name: "Open saved queries and history" }).click();
  await expect(page.locator("#sql-saved-queries")).toContainText(savedName);

  const saved = await request.get(`/api/v1/schemii/workspaces/${workspace.id}/console/saved-queries`);
  expect(saved.ok()).toBe(true);
  const savedQuery = (await saved.json()).queries.find(item => item.name === savedName);
  expect(savedQuery).toBeTruthy();
  const removed = await request.delete(
    `/api/v1/schemii/workspaces/${workspace.id}/console/saved-queries/${savedQuery.id}?expectedRevision=${savedQuery.revision}`,
  );
  expect(removed.status()).toBe(204);
});

test("pinned result tabs survive later runs while unpinned tabs are replaced", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });

  await editor.fill("SELECT 1 AS first; SELECT 2 AS second;");
  await page.getByRole("button", { name: "Run all statements" }).click();
  await expect(page.locator(".sql-result-tab")).toHaveCount(2);
  const pin = page.getByRole("button", { name: "Pin Result 1" });
  await expect(pin).toHaveAttribute("data-ui-icon", "pin");
  await pin.click();
  const unpin = page.getByRole("button", { name: "Unpin Result 1" });
  await expect(unpin).toHaveAttribute("data-ui-icon", "pin-filled");
  await page.locator("#sql-results").hover({ position: { x: 2, y: 2 } });
  await expect(unpin).toHaveCSS("color", "rgb(173, 139, 255)");
  await expect(unpin).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");

  await page.getByRole("button", { name: "Editor" }).click();
  await editor.fill("SELECT 3 AS third;");
  await page.getByRole("button", { name: "Run current statement" }).click();
  await expect(page.locator(".sql-result-tab")).toHaveCount(2);
  await expect(page.locator(".sql-result-tabs")).toContainText("Result 1");
  await expect(page.locator(".sql-result-tabs")).toContainText("Result 1 (2)");
  await page.getByRole("tab", { name: /^Result 1 SELECT/ }).click();
  await expect(page.locator(".sql-result-card tbody")).toContainText("1");
});

test("large query results append cursor pages automatically near the scroll boundary", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await editor.fill("SELECT number FROM generate_series(1, 250) AS generated(number) ORDER BY number;");
  await page.getByRole("button", { name: "Run current statement" }).click();

  const rows = page.locator(".sql-result-card tbody tr");
  await expect(rows).toHaveCount(100);
  await expect(page.getByRole("button", { name: /Load more result rows/i })).toHaveCount(0);
  await page.locator("#sql-results").evaluate(container => {
    container.scrollTop = container.scrollHeight;
    container.dispatchEvent(new Event("scroll"));
  });
  await expect(rows).toHaveCount(200);
  await page.locator("#sql-results").evaluate(container => {
    container.scrollTop = container.scrollHeight;
    container.dispatchEvent(new Event("scroll"));
  });
  await expect(rows).toHaveCount(250);
  await expect(page.locator("[data-result-progress]")).toHaveText("250 rows loaded · end of result");
});

test("write mode retains changes until a confirmed rollback", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  await expect(page.locator(".sql-mode-switch")).toHaveCount(0);
  await page.getByRole("button", { name: "Write", exact: true }).click();
  await expect(page.locator("#sql-workspace")).toHaveAttribute("data-write-mode", "true");
  await expect(page.locator("#sql-transaction-bar")).toBeVisible();
  await expect(page.locator("#sql-transaction-bar")).toContainText("WRITE MODE");
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await page.getByRole("button", { name: "Begin", exact: true }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");
  if (await page.locator("#show-sql-editor").getAttribute("aria-expanded") !== "true") await page.locator("#show-sql-editor").click();
  await editor.fill("CREATE TEMP TABLE console_probe(value integer); INSERT INTO console_probe VALUES (7); SELECT value FROM console_probe;");
  await page.getByRole("button", { name: "Run all" }).click();
  await page.getByRole("tab", { name: /^Result 3 SELECT/ }).click();
  await expect(page.locator(".sql-result-card tbody")).toContainText("7");
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");

  await page.getByRole("button", { name: "Roll back" }).click();
  await expect(page.getByRole("dialog", { name: "Roll back write transaction" })).toBeVisible();
  await page.getByRole("dialog", { name: "Roll back write transaction" }).getByRole("button", { name: "Roll back" }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("No transaction open");
});

test("write transactions commit from either the confirmed button or typed SQL", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  await page.getByRole("button", { name: "Write", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await page.getByRole("button", { name: "Begin", exact: true }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");
  if (await page.locator("#show-sql-editor").getAttribute("aria-expanded") !== "true") await page.locator("#show-sql-editor").click();
  await editor.fill("SELECT 41 AS pending_value;");
  await page.getByRole("button", { name: "Run current statement" }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");
  await page.getByRole("button", { name: "Commit", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Commit write transaction" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Commit", exact: true }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("No transaction open");

  if (await page.locator("#show-sql-editor").getAttribute("aria-expanded") !== "true") await page.locator("#show-sql-editor").click();
  await editor.fill("BEGIN; SELECT 42 AS typed_value; COMMIT;");
  await page.getByRole("button", { name: "Run all" }).click();
  await page.getByRole("tab", { name: /^Result 2 SELECT/ }).click();
  await expect(page.locator(".sql-result-card tbody")).toContainText("42");
  await expect(page.locator("#sql-transaction-status")).toContainText("No transaction open");
});

test("a browser-local draft and its open transaction survive a same-tab refresh", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  await page.getByRole("button", { name: "Write", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  const draft = "SELECT 73 AS refresh_probe;";
  await page.getByRole("button", { name: "Begin", exact: true }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");
  if (await page.locator("#show-sql-editor").getAttribute("aria-expanded") !== "true") await page.locator("#show-sql-editor").click();
  await editor.fill(draft);
  await page.getByRole("button", { name: "Run current statement" }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");

  page.once("dialog", dialog => dialog.accept());
  await page.reload();

  await expect(editor).toHaveValue(draft);
  await expect(page.locator("#write-mode-tool")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#sql-transaction-status")).toContainText("Transaction open");

  await page.getByRole("button", { name: "Roll back" }).click();
  const dialog = page.getByRole("dialog", { name: "Roll back write transaction" });
  await dialog.getByRole("button", { name: "Roll back" }).click();
  await expect(page.locator("#sql-transaction-status")).toContainText("No transaction open");
});
