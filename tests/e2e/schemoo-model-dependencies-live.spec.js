import { expect, test } from "@playwright/test";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

test("saved dashboards protect a model until their author deletes them", async ({ page, request }) => {
  const modelId = await createOrganizationModel(request, "Dependency protection");
  let dashboard;
  try {
    const model = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
    const created = await request.post("/api/v1/schemer/dashboards", { data: {
      name: "Staffing dashboard — dependency protection", modelId, modelRevision: model.revision,
    } });
    expect(created.status(), await created.text()).toBe(201);
    dashboard = await created.json();
    const blocked = await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${model.revision}`);
    expect(blocked.status()).toBe(409);
    expect((await blocked.json()).error.code).toBe("model_in_use");
    await page.goto("/schemoo");
    const remove = page.getByRole("button", { name: `Delete model ${model.name}`, exact: true });
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
    await expect(dialog.getByRole("status")).toContainText("1 saved dashboard depends on this model");
    await expect(dialog.getByRole("link")).toHaveAttribute("href", `/schemer?dashboard=${dashboard.id}`);
    await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
    await page.screenshot({ path: `artifacts/model-dependencies-live-${test.info().project.name}.png` });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(remove).toBeFocused();
    const deleted = await request.delete(`/api/v1/schemer/dashboards/${dashboard.id}?expectedRevision=${dashboard.revision}`);
    expect(deleted.status()).toBe(204); dashboard = null;
    await remove.click();
    await expect(dialog.getByRole("status")).toHaveText("No saved dashboards depend on this model.");
    await dialog.getByRole("button", { name: "Delete model", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(remove).toHaveCount(0);
    expect((await request.get(`/api/v1/schemoo/models/${modelId}`)).status()).toBe(404);
  } finally {
    if (dashboard) {
      const response = await request.delete(`/api/v1/schemer/dashboards/${dashboard.id}?expectedRevision=${dashboard.revision}`);
      expect(response.status()).toBe(204);
    }
    await deleteModel(request, modelId);
  }
});

test("concurrent live dashboard creation and model deletion never leave an orphan", async ({ request }) => {
  const sourceId = await createOrganizationModel(request, "Dependency race fixture");
  try {
    const source = await (await request.get(`/api/v1/schemoo/models/${sourceId}`)).json();
    for (let attempt = 0; attempt < 6; attempt++) {
      const copied = await request.post(`/api/v1/schemoo/models/${sourceId}/duplicate`, { data: {
        name: `Dependency race ${attempt}`, expectedRevision: source.revision,
        expectedLayoutRevision: source.layoutRevision, expectedExploreRevision: source.exploreRevision,
      } });
      expect(copied.status(), await copied.text()).toBe(201);
      const model = await copied.json();
      let dashboard;
      try {
        const create = () => request.post("/api/v1/schemer/dashboards", { data: {
          name: "Concurrent dependency fixture", modelId: model.id, modelRevision: model.revision,
        } });
        const remove = () => request.delete(`/api/v1/schemoo/models/${model.id}?expected_revision=${model.revision}`);
        // Alternate the leading request so both operations compete for the parent.
        const results = await Promise.all(attempt % 2 ? [remove(), create()] : [create(), remove()]);
        const [created, deleted] = attempt % 2 ? results.reverse() : results;
        if (created.status() === 201) dashboard = await created.json();
        if (dashboard) {
          expect(deleted.status(), await deleted.text()).toBe(409);
          expect((await deleted.json()).error.code).toBe("model_in_use");
          expect((await request.get(`/api/v1/schemoo/models/${model.id}`)).status()).toBe(200);
        } else {
          expect(created.status(), await created.text()).toBe(404);
          expect(deleted.status(), await deleted.text()).toBe(204);
        }
      } finally {
        if (dashboard) expect((await request.delete(`/api/v1/schemer/dashboards/${dashboard.id}?expectedRevision=${dashboard.revision}`)).status()).toBe(204);
        await deleteModel(request, model.id);
      }
    }
  } finally {
    await deleteModel(request, sourceId);
  }
});
