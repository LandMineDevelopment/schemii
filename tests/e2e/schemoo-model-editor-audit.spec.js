import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";

const modelId = "model_editor_audit_fixture";

async function openFixture(page) {
  const fields = Array.from({ length: 36 }, (_, index) => ({ name: `field_${index}`, dataType: "integer", nullable: false }));
  const catalog = { database: "fixture", namespace: "public", fingerprint: "fixture-v1", notice: "Isolated editor fixture",
    tables: [{ name: "people", primaryKey: ["field_0"], columns: fields }, { name: "teams", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer", nullable: false }] }],
    relationships: [] };
  const draft = importedDraft(catalog);
  draft.edges.push({ id: "late_field", kind: "logical", source: "people", sourceColumn: "field_20", target: "teams", targetColumn: "id", enabled: true });
  const saved = { id: modelId, connectionId: "fixture_connection", namespace: "public", name: "Editor audit fixture", revision: 1,
    layoutRevision: 1, exploreRevision: 1, catalogFingerprint: catalog.fingerprint, ...splitDraft(draft), layout: { positions: [] } };
  await page.route(`**/api/v1/schemoo/models/${modelId}`, route => route.fulfill({ json: saved }));
  await page.route("**/api/v1/schemoo/catalog?*", route => route.fulfill({ json: catalog }));
  await page.route(`**/api/v1/schemoo/models/${modelId}/validate`, route => route.fulfill({ json: {
    sql: "SELECT 1;", usedRelationships: [], grain: "one row per person", warnings: [], sourceIssues: [], activeScopes: [], cycleEdges: [],
  } }));
  await page.route(`**/api/v1/schemoo/models/${modelId}/previews`, route => route.fulfill({ json: { previews: [] } }));
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(2);
}

test("missing saved positions stay clean; tall fields scroll with visible relationship anchors", async ({ page }) => {
  await openFixture(page);
  await expect(page.locator("#save-model")).toBeDisabled();
  const card = page.locator('.sc-node[data-node-id="people"]');
  const list = card.locator(".sc-node-fields");
  expect(await card.evaluate(node => node.getBoundingClientRect().height)).toBeLessThan(370);
  expect(await list.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  const bounds = await list.boundingBox();
  if (test.info().project.name === "android-chromium") {
    const session = await page.context().newCDPSession(page);
    const x = bounds.x + bounds.width * .7, start = bounds.y + bounds.height * .8, end = bounds.y + bounds.height * .2;
    await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: start }] });
    for (let step = 1; step <= 8; step++) {
      await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: start + (end - start) * step / 8 }] });
    }
    await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await session.detach();
  } else {
    await page.mouse.move(bounds.x + bounds.width * .7, bounds.y + bounds.height * .5);
    await page.mouse.wheel(0, 420);
  }
  await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  const before = await page.locator('[data-edge-id="late_field"] circle').first().getAttribute("cy");
  await list.evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect.poll(() => page.locator('[data-edge-id="late_field"] circle').first().getAttribute("cy")).not.toBe(before);
  await list.locator('input[aria-label="Expose people.field_35"]').focus();
  await page.keyboard.press("Space");
  await expect(page.locator('input[aria-label="Expose people.field_35"]')).toBeFocused();
  await expect(page.locator("#save-model")).toBeEnabled();
});

test("multiple issues on one input keep one description and clear after correction", async ({ page }) => {
  await page.route("**/editor-validation-fixture", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><html><body>
    <div id="body"><div class="mf-label"><input aria-label="Source value" aria-describedby="hint" data-validation-key="source"><span id="hint">Existing hint</span></div></div>
    <p id="summary" role="alert"></p>
    <script type="module">import { clearEditorValidation, showEditorValidation } from "/schemoo-assets/editor-validation.js";
      const body=document.querySelector("#body"), summary=document.querySelector("#summary");
      body.addEventListener("input",()=>clearEditorValidation(body,summary));
      window.editorValidation={body,summary,showEditorValidation,clearEditorValidation};</script></body></html>` }));
  await page.goto("/editor-validation-fixture");
  await page.waitForFunction(() => !!window.editorValidation);
  await page.evaluate(() => {
    const {body,summary,showEditorValidation}=window.editorValidation;
    showEditorValidation(body,summary,[{key:"source",message:"Choose a value."},{key:"source",message:"The selected domain is unavailable."}]);
  });
  const input=page.getByRole("textbox", { name: "Source value" });
  await expect(input).toBeFocused();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator(".mf-inline-error")).toHaveCount(1);
  const describedBy=(await input.getAttribute("aria-describedby")).split(" ");
  expect(describedBy).toHaveLength(2);
  expect(describedBy[0]).toBe("hint");
  await expect(page.locator(`#${describedBy[1]}`)).toContainText("The selected domain is unavailable.");
  await input.fill("corrected");
  await expect(input).toHaveAttribute("aria-describedby", "hint");
  await expect(input).not.toHaveAttribute("aria-invalid", "true");
  await expect(page.locator(".mf-inline-error")).toHaveCount(0);
});

test("fixed filter validation reveals and associates the missing source field", async ({ page }) => {
  await openFixture(page);
  await page.locator("#show-filters").click();
  await page.getByRole("button", { name: "Add model filter scope" }).click();
  await page.getByRole("button", { name: "Fixed rule" }).click();
  const dialog = page.getByRole("dialog", { name: "Add model filter" });
  await dialog.getByRole("button", { name: "Apply to model" }).click();
  const invalid = dialog.locator('[aria-invalid="true"]');
  await expect(invalid).toHaveCount(1);
  await expect(invalid).toBeFocused();
  expect(await invalid.getAttribute("aria-describedby")).toBeTruthy();
  await expect(dialog.locator(".mf-inline-error")).toContainText("Choose a valid source column");
  expect(await invalid.evaluate((input, body) => {
    const field = input.getBoundingClientRect(), area = document.querySelector(body).getBoundingClientRect();
    return field.top >= area.top && field.bottom <= area.bottom;
  }, ".mf-dialog-body")).toBe(true);
  await invalid.click();
  await page.getByRole("option", { name: "people · field_0" }).click();
  await dialog.getByRole("button", { name: "Apply to model" }).click();
  await expect(dialog).toHaveCount(0);
});

test("grouped calculation reports only missing fields and focuses the first one", async ({ page }) => {
  await openFixture(page);
  await page.locator('.sc-node[data-node-id="people"] .sc-node-header').click();
  await page.getByRole("button", { name: "Add calculated source" }).click();
  const dialog = page.getByRole("dialog", { name: "Add calculated source" });
  await dialog.getByRole("combobox", { name: "Calculation kind" }).click();
  await page.getByRole("option", { name: "Grouped summary · one row per group" }).click();
  await dialog.getByRole("button", { name: "Apply to model" }).click();
  await expect(dialog.locator('[aria-invalid="true"]')).toHaveCount(2);
  await expect(dialog.getByRole("textbox", { name: "Field 1 name" })).toBeFocused();
  await expect(dialog.getByRole("alert")).not.toContainText("grouping");
  const describedBy = await dialog.getByRole("textbox", { name: "Field 1 name" }).getAttribute("aria-describedby");
  await expect(dialog.locator(`#${describedBy}`)).toContainText("Give field 1 a name");
});
