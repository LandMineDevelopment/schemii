import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";

let modelId;
test.beforeEach(async ({ request }) => {
  const response=await request.get("/api/v1/connections");
  const connection=(await response.json()).connections.find(c=>c.database==="organization");
  expect(connection,"Organization connection is required for the Schemoo end-to-end fixture").toBeTruthy();
  const catalogResponse=await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`);
  expect(catalogResponse.ok()).toBeTruthy();
  const catalog=await catalogResponse.json();
  const created=await request.post("/api/v1/schemoo/models",{data:{name:`E2E graphical ${Date.now()}`,connectionId:connection.id,namespace:"public",catalogFingerprint:catalog.fingerprint,...splitDraft(importedDraft(catalog))}});
  expect(created.ok(),await created.text()).toBeTruthy(); modelId=(await created.json()).id;
});
test.afterEach(async({request})=>{
  if(!modelId)return;
  const response=await request.get(`/api/v1/schemoo/models/${modelId}`);
  if(response.ok())await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
  modelId=null;
});

test("query preview maximizes and restores without losing its contents", async ({ page }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await page.getByRole("button", { name: "Show query preview", exact: true }).click();
  const sql = await page.locator("#sql").textContent();
  const split = await page.locator("#query-dock").boundingBox();
  await page.getByRole("button", { name: "Maximize query preview", exact: true }).click();
  await expect(page.locator("#model-shell")).toBeHidden();
  const expanded = await page.locator("#query-dock").boundingBox();
  const workbench = await page.locator("#workbench").boundingBox();
  expect(expanded.height).toBeGreaterThan(split.height);
  expect(Math.abs(expanded.height - workbench.height)).toBeLessThan(2);
  await expect(page.locator("#sql")).toHaveText(sql);
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(page.locator("#results")).toBeVisible();
  await page.screenshot({ path: `artifacts/schemoo-query-expanded-${test.info().project.name}.png` });
  await page.getByRole("button", { name: "Restore split view", exact: true }).click();
  await expect(page.locator("#model-shell")).toBeVisible();
  await expect(page.locator("#results")).toBeVisible();
  await page.locator("#result-status").click({ button: "right" });
  await expect(page.locator("#model-shell")).toBeHidden();
  await page.getByRole("button", { name: "Close query preview", exact: true }).click();
  await expect(page.locator("#model-shell")).toBeVisible();
  await page.getByRole("button", { name: "Show query preview", exact: true }).click();
  await expect(page.getByRole("button", { name: "Maximize query preview", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "Maximize query preview", exact: true }).click();
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await expect(page.locator("#model-shell")).toBeVisible();
});

test("opening Preview while the catalog loads preserves the user's panel choice", async ({ page }) => {
  let releaseCatalog, markIntercepted;
  const release = new Promise(resolve => { releaseCatalog = resolve; });
  const intercepted = new Promise(resolve => { markIntercepted = resolve; });
  await page.route("**/api/v1/schemoo/catalog?*", async route => {
    markIntercepted();
    await release;
    await route.continue();
  });
  await page.goto(`/schemoo?model=${modelId}`, { waitUntil: "commit" });
  await intercepted;
  try {
    await page.getByRole("button", { name: "Explore model", exact: true }).click();
    await expect(page.locator("#inspector")).toBeVisible();
    await expect(page.locator("#explore-pane")).toBeVisible();
  } finally {
    releaseCatalog();
  }
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.locator("#workbench")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#inspector")).toBeVisible();
  await expect(page.locator("#explore-pane")).toBeVisible();
});

test("graphical model: cycles, aliases, required scope, temporal alternatives and report filters", async ({ page }) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#target")).toHaveText("organization.public");
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.locator(".sc-edge")).toHaveCount(18);
  await expect(page.locator(".sc-cycle").first()).toBeVisible();
  await expect(page.locator("#run")).toBeDisabled();
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  const fittedTransform = await page.locator(".sc-stage").getAttribute("style");
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect(page.locator(".sc-stage")).not.toHaveAttribute("style", fittedTransform);
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.screenshot({ path: `artifacts/schemoo-imported-${test.info().project.name}.png` });
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await page.getByRole("button", { name: "Create alias", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.getByRole("textbox", { name: "Alias name for certification_dim", exact: true })).toHaveValue("certification_dim (alias)");
  await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.locator(".edge-name").filter({ hasText: "personnel_certification_fact.certification_id → certification_dim (alias).id" }).click();
  await page.getByRole("checkbox", { name: "Relationship enabled", exact: true }).check();
  await expect(page.locator(".sc-edge").filter({ has: page.locator("title", { hasText: "personnel_certification_fact.certification_id to certification_dim (alias).id" }) })).toHaveCount(1);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.getByRole("button", { name: "Staffing example", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(15);
  await expect(page.locator(".sc-cycle")).toHaveCount(0);
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await expect(page.locator("#run")).toBeDisabled();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  const parentValue = page.getByRole("combobox", { name: "Organization tree: Parent organization", exact: true });
  await expect(parentValue).toHaveValue("");
  await parentValue.click();
  const values = page.locator(".ui-async-value-select__list:visible [role=option]");
  await expect(values.first()).toBeVisible({ timeout: 30000 });
  await values.first().click();
  await expect(parentValue).toHaveAttribute("aria-expanded", "false");
  await chooseModelOption(page, "Alternative for Time scope", "All history (no date restriction)");
  await expect(page.locator("#run")).toBeEnabled();
  await page.getByRole("button", { name: "Show query preview", exact: true }).click();
  await expect(page.locator("#sql")).toContainText("EXISTS");
  await expect(page.locator("#sql")).toContainText('"org_hier"');
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("#result-status")).toContainText("rows", { timeout: 30000 });
  await expect(page.locator("#error")).toBeHidden();
  await page.screenshot({ path: `artifacts/schemoo-scoped-results-${test.info().project.name}.png` });
  await page.getByRole("button", { name: "Close query preview", exact: true }).click();
  await chooseModelOption(page, "Alternative for Time scope", "Active at any point during a period");
  await expect(page.locator("#run")).toBeDisabled();
  await page.getByRole("textbox", { name: "Time scope: Period start", exact: true }).fill("2020-01-01");
  await expect(page.locator("#run")).toBeDisabled();
  await page.getByRole("textbox", { name: "Time scope: Period end", exact: true }).fill("2030-01-01");
  await expect(page.locator("#run")).toBeEnabled();
  await chooseModelOption(page, "Alternative for Time scope", "All history (no date restriction)");
  await page.getByRole("button", { name: "Add report filter group", exact: true }).click();
  await chooseModelOption(page, "Report filter 1 condition 1 field", "personnel_dim · name");
  await page.getByRole("textbox", { name: "Report filter 1 condition 1 value", exact: true }).fill("schemoo-no-such-person-'--");
  await expect(page.locator("#run")).toBeEnabled();
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("#result-status")).toContainText("0 rows", { timeout: 30000 });
  await expect(page.locator("#results")).toContainText("No matching rows");
  await page.getByRole("button", { name: "Close query preview", exact: true }).click();
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Report filter 1 condition 1 value", exact: true })).toHaveValue("schemoo-no-such-person-'--");
  await page.getByRole("button", { name: "Delete Report filter 1", exact: true }).click();
  await expect(page.locator("#run")).toBeEnabled();
  await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.screenshot({ path: `artifacts/schemoo-model-${test.info().project.name}.png` });
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("model library creates from an explicit source, persists edits and protects concurrent saves", async ({page,request})=>{
  test.setTimeout(90_000);
  page.on("dialog",dialog=>dialog.accept());
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  const connections=(await (await request.get("/api/v1/connections")).json()).connections;
  const connection=connections.find(c=>c.database==="organization");
  const modelName=`E2E library ${Date.now()}`;
  let createdId;
  try {
    await page.goto("/schemoo");
    await expect(page.locator("#model-library")).toBeVisible();
    await page.locator("#model-library").getByRole("textbox",{name:"Model name",exact:true}).fill(modelName);
    await chooseModelOption(page,"Source connection",`${connection.name} · ${connection.database} · ${connection.username}`);
    await chooseModelOption(page,"Source schema","public");
    await page.getByRole("button",{name:"Create model",exact:true}).click();
    await expect(page.locator("#model-library")).toBeHidden();
    await expect(page.locator("#model-name")).toHaveValue(modelName);
    createdId=new URL(page.url()).searchParams.get("model");
    await page.locator("#model-name").fill(`${modelName} saved`);
    await page.getByRole("button",{name:"Save model",exact:true}).click();
    await expect(page.locator("#draft-status")).toContainText("Saved");
    await page.reload();
    await expect(page.locator("#model-name")).toHaveValue(`${modelName} saved`);
    const current=await (await request.get(`/api/v1/schemoo/models/${createdId}`)).json();
    const remote=await request.put(`/api/v1/schemoo/models/${createdId}`,{data:{expectedRevision:current.revision,name:`${modelName} other tab`,definition:current.definition,catalogFingerprint:current.catalogFingerprint}});
    expect(remote.ok()).toBeTruthy();
    await page.locator("#model-name").fill(`${modelName} local`);
    await page.getByRole("button",{name:"Save model",exact:true}).click();
    await expect(page.locator("#draft-status")).toContainText("conflict");
    await expect(page.locator("#model-name")).toHaveValue(`${modelName} local`);
    await expect(page.getByRole("button",{name:"Save model",exact:true})).toBeDisabled();
    await page.getByRole("button",{name:"Reload saved model and source",exact:true}).click();
    await expect(page.locator("#model-name")).toHaveValue(`${modelName} other tab`);
    await page.screenshot({path:`artifacts/schemoo-library-${test.info().project.name}.png`});
    expect(errors).toEqual([]);
  } finally {
    if(createdId){const latest=await (await request.get(`/api/v1/schemoo/models/${createdId}`)).json();await request.delete(`/api/v1/schemoo/models/${createdId}?expected_revision=${latest.revision}`);}
  }
});
