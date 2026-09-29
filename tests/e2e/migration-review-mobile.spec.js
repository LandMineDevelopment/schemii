import { expect, test } from "@playwright/test";

const sql = `CREATE TABLE public.qa_projects (
  project_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code character varying(24) NOT NULL UNIQUE,
  budget numeric(12, 2) NOT NULL DEFAULT 0,
${Array.from({ length: 32 }, (_, index) => `  detail_${String(index + 1).padStart(2, "0")} text,`).join("\n")}
  CONSTRAINT qa_projects_budget_check CHECK (budget >= 0),
  CONSTRAINT qa_projects_long_identifier_check CHECK (very_long_column_identifier_that_must_wrap_in_the_mobile_review_without_changing_the_sql_text IS NOT NULL)
);`;

test("mobile migration review exposes complete readable SQL and keeps actions in place", async ({ page, request }) => {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const { workspaces } = await response.json();
  const workspace = workspaces.find(item => item.database === "schemii_test" && item.namespace === "bookstore" && item.connectionId)
    || workspaces.find(item => item.connectionId && item.database && item.namespace);
  expect(workspace, "An existing database-backed workspace is required").toBeTruthy();

  let planRequests = 0;
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/migration-executions?*`, route => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: { executions: [] } });
  });
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/migration-plans`, route => {
    expect(route.request().method()).toBe("POST");
    planRequests += 1;
    return route.fulfill({ json: {
      id: "plan_mobile_review_fixture",
      status: "reviewable",
      applyCapable: false,
      complete: false,
      designRevision: 1,
      baselineRevision: 1,
      driftStatus: "none",
      expiresAt: "2026-12-01T00:00:00Z",
      destructive: false,
      requiresExternalChangeAcknowledgement: false,
      blockingDifferences: [],
      warnings: [],
      externalChanges: [],
      conflicts: [],
      columnOrderRebuilds: [],
      columnTypeConversions: [],
      steps: [{ index: 1, operation: "create_table", objectPath: "public.qa_projects", objectKind: "table", sql,
        destructive: false, dataMovement: false, requiresLock: false }],
    } });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/?workspace=${encodeURIComponent(workspace.id)}`);
  const openReview = page.locator("#review-migration-button");
  await expect(openReview).toBeEnabled();
  await openReview.click();
  const dialog = page.locator("#migration-dialog");
  const step = dialog.locator(".migration-step");
  const summary = step.locator("summary");
  const showSql = summary.locator(".migration-step-show");
  const hideSql = summary.locator(".migration-step-hide");
  await expect(showSql).toBeVisible();
  await expect(hideSql).toBeHidden();
  await expect(step.locator("pre")).toBeHidden();
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(summary).toBeFocused();
  await expect(hideSql).toBeVisible();
  await expect(showSql).toBeHidden();
  await expect(step.locator("pre")).toBeVisible();
  expect(await step.locator("pre code").textContent()).toBe(sql);
  expect(planRequests).toBe(1);

  const layout = await dialog.evaluate(element => {
    const pre = element.querySelector(".migration-step > pre");
    const style = getComputedStyle(pre);
    const footer = element.querySelector(".ui-dialog__actions");
    const body = element.querySelector(".migration-review-body");
    const dialogRect = element.getBoundingClientRect();
    const footerRect = footer.getBoundingClientRect();
    const bodyRect = body.getBoundingClientRect();
    return {
      fontSize: parseFloat(style.fontSize),
      whiteSpace: style.whiteSpace,
      preFits: pre.scrollWidth <= pre.clientWidth + 1,
      dialogFits: element.scrollWidth <= element.clientWidth + 1,
      dialogInsideViewport: dialogRect.left >= -1 && dialogRect.right <= window.innerWidth + 1,
      footerInsideViewport: footerRect.bottom <= window.innerHeight + 1,
      footerBelowBody: footerRect.top >= bodyRect.bottom - 1,
      footerTop: footerRect.top,
      bodyCanScroll: body.scrollHeight > body.clientHeight,
    };
  });
  expect(layout.fontSize).toBeGreaterThanOrEqual(12);
  expect(layout.whiteSpace).toBe("pre-wrap");
  expect(layout.preFits).toBe(true);
  expect(layout.dialogFits).toBe(true);
  expect(layout.dialogInsideViewport).toBe(true);
  expect(layout.footerInsideViewport).toBe(true);
  expect(layout.footerBelowBody).toBe(true);
  expect(layout.bodyCanScroll).toBe(true);

  await dialog.locator(".migration-review-body").evaluate(body => { body.scrollTop = body.scrollHeight; });
  const footerTopAfterScroll = await dialog.locator(".ui-dialog__actions").first().evaluate(footer => footer.getBoundingClientRect().top);
  expect(Math.abs(footerTopAfterScroll - layout.footerTop)).toBeLessThan(1);
  await expect(dialog.getByRole("button", { name: "Apply migration" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeVisible();
  await summary.click();
  await expect(showSql).toBeVisible();
  await expect(hideSql).toBeHidden();
  await expect(step.locator("pre")).toBeHidden();
});
