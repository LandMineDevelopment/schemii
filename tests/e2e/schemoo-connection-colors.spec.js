import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { chooseModelOption } from "./helpers/schemoo-select.js";

test("enabled historical connections become purple only when Preview uses them, with mobile-accessible color help", async ({ page }) => {
  const catalog = {
    database: "fixture", namespace: "public", fingerprint: "colors-v1", notice: "Isolated color fixture",
    tables: [
      { name: "people", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer" }, { name: "name", dataType: "text" }] },
      { name: "assignments", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer" }, { name: "person_id", dataType: "integer" }] },
    ],
    relationships: [{ id: "person_fk", sourceTable: "assignments", sourceColumn: "person_id", targetTable: "people", targetColumn: "id" }],
    positions: [{ name: "people", x: 0, y: 0 }, { name: "assignments", x: 400, y: 0 }],
  };
  const draft = importedDraft(catalog);
  draft.nodes.push({ id: "history", table: "assignments", label: "Historical assignments", x: 400, y: 300 });
  draft.edges = [
    { id: "current_path", relationshipId: "person_fk", source: "assignments", target: "people", enabled: false },
    { id: "history_path", relationshipId: "person_fk", source: "history", target: "people", enabled: true },
  ];
  draft.root = "people";
  draft.exposedFields.push({ table: "history", column: "id" });
  draft.fields = [{ table: "people", column: "name", aggregate: "none" }];
  const saved = { id: "model_colors", connectionId: "pg_fixture", namespace: "public", name: "Connection colors", revision: 1, layoutRevision: 1, exploreRevision: 1, catalogFingerprint: catalog.fingerprint, ...splitDraft(draft) };
  const writes = [];
  await page.route("**/api/v1/schemoo/models/model_colors", route => {
    if (route.request().method() !== "GET") writes.push(route.request().method());
    return route.fulfill({ json: saved });
  });
  await page.route("**/api/v1/schemoo/catalog?*", route => route.fulfill({ json: catalog }));
  await page.route("**/api/v1/schemoo/models/model_colors/validate", route => {
    const { explore } = route.request().postDataJSON();
    return route.fulfill({ json: { sql: "SELECT 1;", usedRelationships: explore.fields.some(field => field.table === "history") ? ["history_path"] : [], grain: "detail rows", warnings: [], sourceIssues: [], activeScopes: [] } });
  });
  await page.goto("/schemoo?model=model_colors");
  const historical = page.locator('[data-edge-id="history_path"]');
  await expect(historical).toHaveAttribute("aria-label", /enabled · not in current preview$/);
  await expect(historical).not.toHaveClass(/sc-disabled|sc-used/);
  await expect(page.locator('[data-edge-id="current_path"]')).toHaveClass(/sc-disabled/);
  const help = page.getByRole("button", { name: "About Connection colors", exact: true });
  await expect(help).toBeVisible();
  await help.click();
  const dialog = page.getByRole("dialog", { name: "Connection colors" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("exposing a field on the canvas alone does not select it for Preview");
  await page.screenshot({ path: `artifacts/schemoo-connection-help-${test.info().project.name}.png` });
  await dialog.getByRole("button", { name: "Close information", exact: true }).click();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await chooseModelOption(page, "Preview source field", "Historical assignments · id");
  await page.getByRole("button", { name: "Add preview output", exact: true }).click();
  await expect(historical).toHaveClass(/sc-used/);
  await expect(historical).toHaveAttribute("aria-label", /current preview path$/);
  await expect(historical).toHaveCSS("color", "rgb(173, 141, 239)");
  expect(writes).toEqual([]);
});
