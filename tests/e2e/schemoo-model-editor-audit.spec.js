import { expect, request as requestFactory, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { createOrganizationModel } from "./helpers/schemoo-model.js";

const modelId = "model_editor_audit_fixture";
const keyboardFieldLabel = "Expose people.field_35";
const cleanupIds = new Set();
let ownedKeyboardAttempt;

test.afterEach(async ({}, testInfo) => {
  const attempt = ownedKeyboardAttempt;
  ownedKeyboardAttempt = undefined;
  if (!attempt) return;
  attempt.finished = true;
  attempt.releasePreviews();
  const errors = [];
  let cleanup;
  attempt.observations.cleanup = { ownedModelDeleted: false, requestContextDisposed: false };
  try {
    if (cleanupIds.size) {
      // The body's request fixture may already be disposed after a timeout.
      // Keep the same private authentication in an independently owned context.
      const baseURL = testInfo.project.use.baseURL;
      cleanup = await requestFactory.newContext({
        baseURL, ignoreHTTPSErrors: true, storageState: testInfo.project.use.storageState,
        extraHTTPHeaders: { Origin: new URL(baseURL).origin }, timeout: 5_000,
      });
      for (const ownedModelId of cleanupIds) {
        try {
          const path = `/api/v1/schemoo/models/${ownedModelId}`;
          const response = await cleanup.get(path);
          expect(response.status(), "cleanup reads the exact owned model revision").toBe(200);
          const current = await response.json();
          expect(current.id, "cleanup never adopts another model identity").toBe(ownedModelId);
          expect(Number.isSafeInteger(current.revision) && current.revision >= 1, "cleanup requires a valid current revision").toBe(true);
          const deleted = await cleanup.delete(`${path}?expected_revision=${current.revision}`);
          expect(deleted.status(), "cleanup deletes only the exact model created by this attempt").toBe(204);
          expect((await cleanup.get(path)).status(), "owned model is absent after cleanup").toBe(404);
          cleanupIds.delete(ownedModelId);
          attempt.observations.cleanup.ownedModelDeleted = true;
        } catch (error) {
          errors.push(error);
        }
      }
    }
  } catch (error) {
    errors.push(error);
  } finally {
    if (cleanup) {
      try {
        await cleanup.dispose();
        attempt.observations.cleanup.requestContextDisposed = true;
      } catch (error) {
        errors.push(error);
      }
    }
    attempt.observations.cleanup.pendingOwnedModelIds = [...cleanupIds];
    attempt.observations.cleanup.errors = errors.map(error => ({ name: error.name, message: error.message }));
    attempt.observations.outcome = { status: testInfo.status, primaryErrors: testInfo.errors.map(error => error.message) };
    try {
      await attachKeyboardDiagnostics(attempt.page, testInfo, attempt.observations,
        "owned real API model; Save, independent GET, reload and revision-checked delete", attempt.label);
    } catch (error) {
      errors.push(error);
    }
  }
  // Playwright retains the body's primary failure alongside this hook failure.
  if (errors.length) throw new AggregateError(errors,
    `Owned keyboard model cleanup or diagnostics failed: ${errors.map(error => error.message).join("; ")}`);
});

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
  const events = observations.afterSpace.events.filter(event => event.phase === "capture");
  for (const type of ["keydown", "keyup", "input", "change"]) {
    expect(events.filter(event => event.type === type), `one trusted ${type} event for the intended field`).toHaveLength(1);
  }
  expect(events.every(event => event.trusted)).toBe(true);
  expect(events.find(event => event.type === "keydown").key).toBe(" ");
  expect(events.find(event => event.type === "input").checked).toBe(false);
  expect(events.find(event => event.type === "change").checked).toBe(false);
  expect(events.findIndex(event => event.type === "input")).toBeLessThan(events.findIndex(event => event.type === "change"));
  // The change handler updates dirty state synchronously. Check that receipt
  // before a later validation redraw can restore an unchanged model's input.
  expect(observations.afterSpace.draftStatus, "change reaches the model dirty-state owner").toBe("Unsaved changes");
  await expect(field, "one keyboard Space toggle changes the intended exposure").not.toBeChecked();
  await expect(field, "canvas redraw retains keyboard focus on the exposure").toBeFocused();
  await expect(page.locator("#save-model"), "the intended Save action remains visible").toBeVisible();
  if (saveReady) await expect(page.locator("#save-model"), "a dirty ready model enables Save").toBeEnabled();
  else await expect(page.locator("#save-model"), "a dirty model keeps Save disabled while preview loading blocks admission").toBeDisabled();
}

async function fieldScrollGeometry(page, { bottom = false } = {}) {
  return page.evaluate(bottom => {
    // Resolve, scroll and measure in one browser callback. A locator's resolved
    // element can have been detached by the canvas validation redraw.
    const list = document.querySelector('.sc-node[data-node-id="people"] .sc-node-fields');
    const row = list?.querySelector('[data-column-name="field_20"]');
    const anchor = document.querySelector('[data-edge-id="late_field"] circle');
    if (!list || !row || !anchor) return null;
    const beforeScroll = list.scrollTop;
    if (bottom) list.scrollTop = list.scrollHeight;
    const listBounds = list.getBoundingClientRect(), rowBounds = row.getBoundingClientRect();
    const point = new DOMPoint(Number(anchor.getAttribute("cx")), Number(anchor.getAttribute("cy")))
      .matrixTransform(anchor.getScreenCTM());
    const rowCenter = rowBounds.top + rowBounds.height / 2;
    // Hidden rows attach at the first/last visible row centre. Use actual DOM
    // bounds rather than repeating the renderer's fixed row/port arithmetic.
    const top = listBounds.top + rowBounds.height / 2;
    const last = listBounds.bottom - rowBounds.height / 2;
    const expectedY = Math.max(top, Math.min(last, rowCenter));
    const tolerance = 3 * rowBounds.height / row.offsetHeight;
    const maxScroll = list.scrollHeight - list.clientHeight;
    return { connected: list.isConnected, beforeScroll, scrollTop: list.scrollTop, maxScroll,
      atBottom: Math.abs(list.scrollTop - maxScroll) <= 1,
      anchorY: point.y, expectedY, tolerance,
      anchorAligned: Math.abs(point.y - expectedY) <= tolerance,
      clamp: rowCenter < top ? "top" : rowCenter > last ? "bottom" : "row" };
  }, bottom);
}

async function attachKeyboardDiagnostics(page, testInfo, observations, transport, label = keyboardFieldLabel) {
  observations.final = testInfo.status === "timedOut" ? { testTimedOut: true } : page.isClosed() ? { pageClosed: true }
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
    await expect(page.locator("#plan-status"), "initial validation settles before targeting a native scroll gesture")
      .toHaveText("0 required connections · one row per person");
    observations.initial = await keyboardSnapshot(page);
    const card = page.locator('.sc-node[data-node-id="people"]');
    const list = card.locator(".sc-node-fields");
    expect(await card.evaluate(node => node.getBoundingClientRect().height)).toBeLessThan(370);
    expect(await list.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
    observations.gesture = await page.evaluate(() => {
      const list = document.querySelector('.sc-node[data-node-id="people"] .sc-node-fields');
      const bounds = list.getBoundingClientRect(), canvas = document.querySelector("#canvas-host").getBoundingClientRect();
      // The mobile canvas can clip a tall list. A gesture on the full list's
      // bounding box can land on the dock or background instead of its fields.
      const left = Math.max(bounds.left, canvas.left, 0), right = Math.min(bounds.right, canvas.right, innerWidth);
      const top = Math.max(bounds.top, canvas.top, 0), bottom = Math.min(bounds.bottom, canvas.bottom, innerHeight);
      const x = left + (right - left) * .7;
      const start = top + (bottom - top) * .8, end = top + (bottom - top) * .2, middle = (start + end) / 2;
      const owned = y => document.elementFromPoint(x, y)?.closest(".sc-node-fields") === list;
      return { x, start, end, middle, width: right - left, height: bottom - top,
        listHeight: bounds.height, canvasHeight: canvas.height,
        ownedStart: owned(start), ownedEnd: owned(end), ownedMiddle: owned(middle) };
    });
    const gesture = observations.gesture;
    expect(gesture.width).toBeGreaterThan(0);
    expect(gesture.height).toBeGreaterThan(0);
    expect([gesture.ownedStart, gesture.ownedMiddle, gesture.ownedEnd], "the native gesture targets only visible owned fields")
      .toEqual([true, true, true]);
    if (test.info().project.name === "android-chromium") {
      const session = await page.context().newCDPSession(page);
      const { x, start, end } = gesture;
      try {
        await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: start }] });
        for (let step = 1; step <= 8; step++) {
          await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: start + (end - start) * step / 8 }] });
        }
        await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      } finally {
        await session.detach();
      }
    } else {
      await page.mouse.move(gesture.x, gesture.middle);
      await page.mouse.wheel(0, 420);
    }
    await expect.poll(async () => {
      observations.userScroll = await fieldScrollGeometry(page);
      return { scrolled: observations.userScroll?.scrollTop > 0, anchorAligned: observations.userScroll?.anchorAligned };
    }, "ordinary field scrolling keeps the source anchor aligned").toEqual({ scrolled: true, anchorAligned: true });
    observations.scrollAssignment = await fieldScrollGeometry(page, { bottom: true });
    expect(observations.scrollAssignment.connected).toBe(true);
    expect(observations.scrollAssignment.maxScroll).toBeGreaterThan(0);
    await expect.poll(async () => {
      observations.scrolledGeometry = await fieldScrollGeometry(page);
      return { atBottom: observations.scrolledGeometry?.atBottom, anchorAligned: observations.scrolledGeometry?.anchorAligned };
    }, "the scrolled source anchor follows its row or visible clipping boundary").toEqual({ atBottom: true, anchorAligned: true });
    // Repeating an already-bottom scroll is valid even when the clipped anchor
    // cannot move. Its position must still agree with the visible field list.
    observations.alreadyBottom = await fieldScrollGeometry(page, { bottom: true });
    expect(observations.alreadyBottom.atBottom).toBe(true);
    expect(observations.alreadyBottom.anchorAligned).toBe(true);
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
  const attempt = { page, observations, label, finished: false,
    releasePreviews: () => { releasePreviews(); markRequested?.(); } };
  ownedKeyboardAttempt = attempt;
  await recordKeyboardEvents(page, { label });
  const exposed = model => model.definition.exposedFields.some(field => field.table === "certification_dim" && field.column === "id");
  try {
    ownedModelId = await createOrganizationModel(request, "Keyboard save audit");
    expect(typeof ownedModelId === "string" && ownedModelId.length > 0, "fixture creation returns an exact owned model ID").toBe(true);
    cleanupIds.add(ownedModelId);
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
    if (attempt.finished) return; // A timeout hook released the custom promise.
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
    expect(observations.afterSpace.events.filter(event => event.type === "change" && event.phase === "bubble"),
      "the controlled change was captured but never reached the model handler").toHaveLength(0);
    await expect(page.locator("#draft-status")).toHaveText("Saved · revision 1 · read-only preview");
    await expect(page.locator("#save-model")).toBeDisabled();
    await page.locator("#reload-model").click();
    await expect(page.locator("#workbench"), "reload completes before checking the unchanged exposure").toHaveJSProperty("inert", false);
    await expect(field, "reload renders the unchanged saved model's exposure").toBeChecked();
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
