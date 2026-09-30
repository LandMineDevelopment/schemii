import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { createOrganizationModel } from "./helpers/schemoo-model.js";

const modelId = "model_editor_audit_fixture";
const keyboardFieldLabel = "Expose people.field_35";

async function recordKeyboardEvents(page, { label = keyboardFieldLabel, suppressChange = false } = {}) {
  await page.addInitScript(({ label, suppressChange }) => {
    window.__schemooKeyboardAudit = { events: [] };
    for (const type of ["keydown", "keyup", "input", "change"]) {
      for (const capture of [true, false]) document.addEventListener(type, event => {
        if (event.target?.getAttribute("aria-label") !== label) return;
        window.__schemooKeyboardAudit.events.push({
          type, phase: capture ? "capture" : "bubble", key: event.key ?? null, trusted: event.isTrusted,
          checked: event.target.checked, connected: event.target.isConnected,
          focusedField: document.activeElement?.getAttribute("aria-label") === label,
          workbenchInert: document.querySelector("#workbench")?.inert ?? null,
          draftStatus: document.querySelector("#draft-status")?.textContent ?? null,
          saveDisabled: document.querySelector("#save-model")?.disabled ?? null,
        });
      }, capture);
    }
    if (suppressChange) document.addEventListener("change", event => {
      if (event.target?.getAttribute("aria-label") === label) event.stopImmediatePropagation();
    }, true);
  }, { label, suppressChange });
}

async function keyboardSnapshot(page, label = keyboardFieldLabel) {
  return page.evaluate(label => {
    const field = document.querySelector(`input[aria-label="${label}"]`);
    return {
      field: field ? { checked: field.checked, connected: field.isConnected, disabled: field.disabled,
        focused: document.activeElement === field } : null,
      activeElement: { tag: document.activeElement?.tagName, id: document.activeElement?.id },
      workbenchInert: document.querySelector("#workbench")?.inert ?? null,
      draftStatus: document.querySelector("#draft-status")?.textContent ?? null,
      saveDisabled: document.querySelector("#save-model")?.disabled ?? null,
      events: window.__schemooKeyboardAudit?.events ?? [],
    };
  }, label);
}

async function assertKeyboardToggle(page, field, observations, label = keyboardFieldLabel, saveReady = true) {
  await expect(field, "the intended exposure starts checked before Space").toBeChecked();
  await field.focus();
  await expect(field, "Space must reach the intended checkbox").toBeFocused();
  observations.beforeSpace = await keyboardSnapshot(page, label);
  await page.keyboard.press("Space");
  observations.afterSpace = await keyboardSnapshot(page, label);
  await expect(field, "one keyboard Space toggle changes the intended exposure").not.toBeChecked();
  await expect(field, "canvas redraw retains keyboard focus on the exposure").toBeFocused();
  const events = observations.afterSpace.events.filter(event => event.phase === "capture");
  for (const type of ["keydown", "keyup", "input", "change"]) {
    expect(events.filter(event => event.type === type), `one trusted ${type} event for the intended field`).toHaveLength(1);
  }
  expect(events.every(event => event.trusted)).toBe(true);
  expect(events.find(event => event.type === "keydown").key).toBe(" ");
  expect(events.find(event => event.type === "input").checked).toBe(false);
  expect(events.find(event => event.type === "change").checked).toBe(false);
  expect(events.findIndex(event => event.type === "input")).toBeLessThan(events.findIndex(event => event.type === "change"));
  await expect(page.locator("#draft-status"), "change reaches the model dirty-state owner").toHaveText("Unsaved changes");
  await expect(page.locator("#save-model"), "the intended Save action remains visible").toBeVisible();
  if (saveReady) await expect(page.locator("#save-model"), "a dirty ready model enables Save").toBeEnabled();
  else await expect(page.locator("#save-model"), "a dirty model keeps Save disabled while preview loading blocks admission").toBeDisabled();
}

async function attachKeyboardDiagnostics(page, testInfo, observations, transport, label = keyboardFieldLabel) {
  observations.final = page.isClosed() ? { pageClosed: true }
    : await keyboardSnapshot(page, label).catch(() => ({ snapshotUnavailable: true }));
  // These attachments stay private. Public reports retain only reviewed outcome
  // counts/stages; no cookies, headers, account fields, console or page dumps.
  await testInfo.attach("keyboard-toggle-diagnostics", {
    body: Buffer.from(JSON.stringify({ transport, ...observations }, null, 2)),
    contentType: "application/json",
  });
}

async function openFixture(page, { wideSavedLayout = false, modelName = "Editor audit fixture", beforeCatalog } = {}) {
  const fields = Array.from({ length: 36 }, (_, index) => ({ name: `field_${index}`, dataType: "integer", nullable: false }));
  const catalog = { database: "fixture", namespace: "public", fingerprint: "fixture-v1", notice: "Isolated editor fixture",
    tables: [{ name: "people", primaryKey: ["field_0"], columns: fields }, { name: "teams", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer", nullable: false }] }],
    relationships: [] };
  const draft = importedDraft(catalog);
  draft.edges.push({ id: "late_field", kind: "logical", source: "people", sourceColumn: "field_20", target: "teams", targetColumn: "id", enabled: true });
  if (wideSavedLayout) {
    draft.nodes[0].x = 0; draft.nodes[0].y = 0;
    draft.nodes[1].x = 2400; draft.nodes[1].y = 0;
  }
  const saved = { id: modelId, connectionId: "fixture_connection", namespace: "public", name: modelName, revision: 1,
    layoutRevision: 1, exploreRevision: 1, catalogFingerprint: catalog.fingerprint, ...splitDraft(draft),
    ...(wideSavedLayout ? {} : { layout: { positions: [] } }) };
  await page.route(`**/api/v1/schemoo/models/${modelId}`, route => route.fulfill({ json: saved }));
  await page.route("**/api/v1/schemoo/catalog?*", async route => {
    await beforeCatalog?.();
    await route.fulfill({ json: catalog });
  });
  await page.route(`**/api/v1/schemoo/models/${modelId}/validate`, route => route.fulfill({ json: {
    sql: "SELECT 1;", usedRelationships: [], grain: "one row per person", warnings: [], sourceIssues: [], activeScopes: [], cycleEdges: [],
  } }));
  await page.route(`**/api/v1/schemoo/models/${modelId}/previews`, route => route.fulfill({ json: { previews: [] } }));
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(2);
}

test("wide saved models open on a readable starting object; Fit still shows the full graph", async ({ page }) => {
  await openFixture(page, { wideSavedLayout: true });
  const zoom = () => page.locator(".sc-stage").evaluate(stage => new DOMMatrixReadOnly(stage.style.transform).a);
  await expect.poll(zoom).toBeGreaterThanOrEqual(.95);
  await expect(page.locator("#save-model")).toBeDisabled();
  const root = await page.locator('.sc-node[data-node-id="people"]').boundingBox();
  const canvas = await page.locator("#canvas-host").boundingBox();
  expect(root.x).toBeGreaterThanOrEqual(canvas.x);
  expect(root.x + root.width).toBeLessThanOrEqual(canvas.x + canvas.width);
  await page.locator("#fit").click();
  await expect.poll(zoom).toBeLessThan(.5);
  await expect(page.locator("#save-model")).toBeDisabled();
});

test("long model names show truncation while keeping the full accessible value", async ({ page }) => {
  const modelName = "QA report-author model qa_reports_orders_dashboard";
  await openFixture(page, { modelName });
  const name = page.getByRole("textbox", { name: "Model name", exact: true });
  const display = page.locator("#model-name-display");
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect(name).toBeVisible();
    await expect(name).toHaveValue(modelName);
    await expect(page.getByRole("heading", { name: modelName, exact: true })).toBeVisible();
    await expect(name).toHaveAttribute("title", modelName);
    const layout = await display.evaluate(text => {
      const bounds = text.getBoundingClientRect();
      const brand = text.closest(".brand").getBoundingClientRect();
      return {
        overflow: getComputedStyle(text).overflow,
        textOverflow: getComputedStyle(text).textOverflow,
        width: bounds.width,
        overflows: text.scrollWidth > text.clientWidth,
        insideBrand: bounds.left >= brand.left && bounds.right <= brand.right,
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
      };
    });
    expect(layout.overflow).toBe("hidden");
    expect(layout.textOverflow).toBe("ellipsis");
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.overflows).toBe(true);
    expect(layout.insideBrand).toBe(true);
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    await name.focus();
    await expect(display).toHaveCSS("visibility", "hidden");
    await expect(name).not.toHaveCSS("color", "rgba(0, 0, 0, 0)");
    await name.blur();
    await expect(display).toHaveCSS("visibility", "visible");
  }
});

test("missing saved positions stay clean; tall fields scroll with visible relationship anchors", async ({ page }, testInfo) => {
  await recordKeyboardEvents(page);
  const observations = {};
  try {
    await openFixture(page);
    await expect(page.locator("#save-model"), "the owned mocked model opens clean").toBeDisabled();
    await expect(page.locator("#draft-status")).toHaveText("Saved · revision 1 · read-only preview");
    observations.initial = await keyboardSnapshot(page);
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
    const field = list.getByRole("checkbox", { name: keyboardFieldLabel, exact: true });
    await assertKeyboardToggle(page, field, observations);
  } finally {
    await attachKeyboardDiagnostics(page, testInfo, observations, "immutable mocked GET/PUT; keyboard and dirty-state evidence only");
  }
});

async function verifyOwnedKeyboardSave(page, request, testInfo, { delayPreviews = false } = {}) {
  const label = "Expose certification_dim.id";
  const observations = {};
  let ownedModelId;
  let releasePreviews = () => {}, markRequested;
  const previewReady = delayPreviews ? new Promise(resolve => { releasePreviews = resolve; }) : Promise.resolve();
  const previewRequested = delayPreviews ? new Promise(resolve => { markRequested = resolve; }) : Promise.resolve();
  await recordKeyboardEvents(page, { label });
  const exposed = model => model.definition.exposedFields.some(field => field.table === "certification_dim" && field.column === "id");
  try {
    ownedModelId = await createOrganizationModel(request, "Keyboard save audit");
    observations.fixture = { ownedModelId };
    const initialResponse = await request.get(`/api/v1/schemoo/models/${ownedModelId}`);
    expect(initialResponse.status(), "read only the newly created owned model").toBe(200);
    const initialModel = await initialResponse.json();
    expect(exposed(initialModel), "the owned server model initially exposes the field").toBe(true);
    if (delayPreviews) await page.route(`**/api/v1/schemoo/models/${ownedModelId}/previews`, async route => {
      markRequested(); await previewReady; await route.continue();
    });
    await page.goto(`/schemoo?model=${ownedModelId}`);
    await previewRequested;
    const field = page.getByRole("checkbox", { name: label, exact: true });
    await expect(page.locator("#save-model"), "the real owned model opens clean").toBeDisabled();
    await expect(page.locator("#draft-status")).toHaveText(`Saved · revision ${initialModel.revision} · read-only preview`);
    observations.initial = await keyboardSnapshot(page, label);
    await assertKeyboardToggle(page, field, observations, label, !delayPreviews);
    if (delayPreviews) {
      await expect(page.locator('.saved-previews [role="status"]')).toHaveText("Loading or saving previews…");
      observations.previewPending = await keyboardSnapshot(page, label);
      releasePreviews();
      await expect(page.locator("#save-model"), "preview readiness restores the dirty model's Save action").toBeEnabled();
      observations.previewReady = await keyboardSnapshot(page, label);
    }
    const savedResponse = page.waitForResponse(response => response.request().method() === "PUT"
      && new URL(response.url()).pathname === `/api/v1/schemoo/models/${ownedModelId}`);
    await page.getByRole("button", { name: "Save model", exact: true }).click();
    const response = await savedResponse;
    expect(response.status(), "the single intended semantic save succeeds").toBe(200);
    const savedModel = await response.json();
    expect(savedModel.revision).toBe(initialModel.revision + 1);
    expect(exposed(savedModel), "save response retains the keyboard exposure edit").toBe(false);
    await expect(page.locator("#draft-status")).toHaveText(`Saved · revision ${savedModel.revision} · read-only preview`);
    await expect(page.locator("#save-model")).toBeDisabled();
    const persistedResponse = await request.get(`/api/v1/schemoo/models/${ownedModelId}`);
    expect(persistedResponse.status()).toBe(200);
    const persisted = await persistedResponse.json();
    expect(persisted.revision).toBe(savedModel.revision);
    expect(exposed(persisted), "a separate API GET observes the saved exposure").toBe(false);
    observations.persisted = { revisionAdvancedOnce: true, exposed: exposed(persisted) };
    await page.reload();
    await expect(field, "reload reads the saved unchecked field").not.toBeChecked();
    await expect(page.locator("#draft-status")).toHaveText(`Saved · revision ${persisted.revision} · read-only preview`);
    await expect(page.locator("#save-model")).toBeDisabled();
    observations.reloaded = await keyboardSnapshot(page, label);
  } finally {
    releasePreviews();
    try {
      if (ownedModelId) {
        const currentResponse = await request.get(`/api/v1/schemoo/models/${ownedModelId}`);
        expect(currentResponse.status(), "cleanup reads the exact owned model revision").toBe(200);
        const current = await currentResponse.json();
        const deleted = await request.delete(`/api/v1/schemoo/models/${ownedModelId}?expected_revision=${current.revision}`);
        expect(deleted.ok(), "cleanup deletes only the exact model created by this attempt").toBe(true);
        expect((await request.get(`/api/v1/schemoo/models/${ownedModelId}`)).status(), "owned model is absent after cleanup").toBe(404);
        observations.cleanup = { ownedModelDeleted: true };
      }
    } finally {
      await attachKeyboardDiagnostics(page, testInfo, observations, "owned real API model; Save, independent GET, reload and revision-checked delete", label);
    }
  }
}

test("keyboard Space exposure saves through the owned API model and stays changed after reload", async ({ page, request }, testInfo) => {
  await verifyOwnedKeyboardSave(page, request, testInfo);
});

test("controlled delayed previews keep Save disabled until ready and then persist one keyboard edit", async ({ page, request }, testInfo) => {
  await verifyOwnedKeyboardSave(page, request, testInfo, { delayPreviews: true });
});

test("controlled missed change fails at model dirty state after a trusted keyboard toggle", async ({ page }, testInfo) => {
  const observations = { perturbation: "stop change before the checkbox model handler" };
  await recordKeyboardEvents(page, { suppressChange: true });
  try {
    await openFixture(page);
    await expect(page.locator("#save-model")).toBeDisabled();
    const field = page.getByRole("checkbox", { name: keyboardFieldLabel, exact: true });
    let failure;
    try { await assertKeyboardToggle(page, field, observations); } catch (error) { failure = error; }
    expect(failure?.message, "the controlled defect must reach and fail the intermediate dirty-state assertion").toContain("change reaches the model dirty-state owner");
    await expect(field).not.toBeChecked();
    await expect(page.locator("#draft-status")).toHaveText("Saved · revision 1 · read-only preview");
    await expect(page.locator("#save-model")).toBeDisabled();
    observations.detectedStage = "model-dirty-state";
  } finally {
    await attachKeyboardDiagnostics(page, testInfo, observations, "immutable mock with controlled missing change delivery; diagnostic control only");
  }
});

test("controlled delayed catalog keeps the editor inert until keyboard editing is ready", async ({ page }, testInfo) => {
  const observations = { perturbation: "hold initial catalog response behind an explicit gate" };
  await recordKeyboardEvents(page);
  let releaseCatalog, markRequested;
  const catalogReady = new Promise(resolve => { releaseCatalog = resolve; });
  const catalogRequested = new Promise(resolve => { markRequested = resolve; });
  const opening = openFixture(page, { beforeCatalog: async () => { markRequested(); await catalogReady; } });
  try {
    await catalogRequested;
    await expect(page.locator("#workbench"), "the editor is inert while its initial catalog is unresolved").toHaveJSProperty("inert", true);
    await expect(page.getByRole("checkbox", { name: keyboardFieldLabel, exact: true })).toHaveCount(0);
    await expect(page.locator("#save-model")).toBeDisabled();
    observations.loading = await keyboardSnapshot(page);
    releaseCatalog();
    await opening;
    await expect(page.locator("#workbench")).toHaveJSProperty("inert", false);
    await expect(page.locator("#draft-status")).toHaveText("Saved · revision 1 · read-only preview");
    await assertKeyboardToggle(page, page.getByRole("checkbox", { name: keyboardFieldLabel, exact: true }), observations);
  } finally {
    releaseCatalog();
    await opening.catch(() => {});
    await attachKeyboardDiagnostics(page, testInfo, observations, "immutable mock with explicitly delayed initial catalog; readiness control only");
  }
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
  expect(await dialog.locator(".mf-inline-error").first().evaluate(error => error.getBoundingClientRect().bottom <= document.querySelector(".mf-dialog-body").getBoundingClientRect().bottom)).toBe(true);
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
  expect(await dialog.locator(`#${describedBy}`).evaluate(error => error.getBoundingClientRect().bottom <= document.querySelector(".derived-body").getBoundingClientRect().bottom)).toBe(true);
});
