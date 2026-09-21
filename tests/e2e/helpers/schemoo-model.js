import { expect } from "@playwright/test";
import { findOrganizationConnection } from "./database-fixtures.js";
export { findOrganizationConnection } from "./database-fixtures.js";
import { importedDraft } from "../../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../../src/schemii/schemoo/web/model-state.js";

export async function createOrganizationModel(request, label) {
  const connections = await (await request.get("/api/v1/connections")).json();
  const connection = findOrganizationConnection(connections.connections);
  expect(connection, "Organization connection is required for the Schemoo browser fixture").toBeTruthy();
  const catalogResponse = await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`);
  expect(catalogResponse.ok(), await catalogResponse.text()).toBeTruthy();
  const catalog = await catalogResponse.json();
  const response = await request.post("/api/v1/schemoo/models", { data: {
    name: `${label} ${Date.now()}`,
    connectionId: connection.id,
    namespace: "public",
    catalogFingerprint: catalog.fingerprint,
    ...splitDraft(importedDraft(catalog)),
  } });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).id;
}

export async function deleteModel(request, modelId) {
  if (!modelId) return;
  const response = await request.get(`/api/v1/schemoo/models/${modelId}`);
  if (!response.ok()) return;
  const model = await response.json();
  const deleted = await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${model.revision}`);
  expect(deleted.ok(), await deleted.text()).toBeTruthy();
}
