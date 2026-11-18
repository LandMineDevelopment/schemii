import { randomUUID } from "node:crypto";
import { expect } from "@playwright/test";
import { importedDraft } from "../../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../../src/schemii/schemoo/web/model-state.js";

// Uses only the existing explicitly provisioned Browser fixture profile. No
// credentials, direct database transport or user profile is created here.
export async function ownedSchemooSource(request) {
  const { workspaces } = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = workspaces.find(item => item.database === "schemii_test" && item.namespace === "bookstore");
  expect(workspace, "test-owned bookstore workspace is required").toBeTruthy();
  const { connections } = await (await request.get("/api/v1/connections")).json();
  const connection = connections.find(item => item.id === workspace.connectionId && item.name === "Browser fixture: schemii_test" && item.ownership === "user");
  expect(connection, "explicit Browser fixture PostgreSQL profile with schema-write permission is required").toBeTruthy();
  const nonce = randomUUID().replaceAll("-", ""), namespace = `source_${nonce}`, marker = `schemii_e2e_source_${nonce}`;
  const settings = await (await request.get("/api/v1/schemii/console/settings")).json();
  const sessions = `/api/v1/schemii/workspaces/${workspace.id}/console/sessions`;
  const opened = await request.post(sessions, { data: { consoleId: `con_${nonce}`, expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision } });
  expect(opened.status(), await opened.text()).toBe(201);
  const session = `${sessions}/${(await opened.json()).id}`, models = new Set();
  const name = suffix => `Source selection ${nonce} ${suffix}`;
  async function sql(statement) {
    const response = await request.post(`${session}/executions`, { data: { sql: statement, commitMode: "whole_run" } });
    expect(response.status(), await response.text()).toBe(201);
    const receipt = await response.json(); let execution;
    await expect.poll(async () => {
      const response = await request.get(`${session}/executions/${receipt.id}`);
      expect(response.ok(), await response.text()).toBeTruthy(); execution = await response.json();
      return ["succeeded", "failed", "cancelled", "uncertain"].includes(execution.status);
    }, { message: "owned source write must reach a definite outcome; uncertain writes are never replayed" }).toBe(true);
    expect(execution.status, JSON.stringify(execution)).toBe("succeeded");
    return execution.results.flatMap(result => result.rows || []);
  }
  async function catalog() {
    const response = await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=${namespace}`);
    expect(response.ok(), await response.text()).toBeTruthy(); return response.json();
  }
  async function createModel(suffix, selection) {
    const source = await catalog(), parts = splitDraft(importedDraft(source, selection));
    const response = await request.post("/api/v1/schemoo/models", { data: { name: name(suffix), connectionId: connection.id, namespace, catalogFingerprint: source.fingerprint, ...parts } });
    expect(response.status(), await response.text()).toBe(201);
    const model = await response.json(); models.add(model.id); return model;
  }
  async function cleanup() {
    const failures = [];
    try {
      const list = await request.get("/api/v1/schemoo/models"); expect(list.ok()).toBeTruthy();
      // Reconcile a possibly lost create response, without replaying the write.
      for (const model of (await list.json()).models) if (model.connectionId === connection.id && model.namespace === namespace && model.name.startsWith(name(""))) models.add(model.id);
      for (const id of models) {
        const response = await request.get(`/api/v1/schemoo/models/${id}`);
        if (response.status() === 404) continue;
        expect(response.ok(), await response.text()).toBeTruthy(); const saved = await response.json();
        expect(saved.connectionId).toBe(connection.id); expect(saved.namespace).toBe(namespace);
        const removed = await request.delete(`/api/v1/schemoo/models/${id}?expected_revision=${saved.revision}`);
        expect(removed.status(), await removed.text()).toBe(204);
      }
    } catch (error) { failures.push(error); }
    try {
      const rows = await sql(`SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = '${namespace}'`);
      if (rows.length) { expect(rows).toEqual([[marker]]); await sql(`DROP SCHEMA "${namespace}" CASCADE`); }
    } catch (error) { failures.push(error); }
    try { const response = await request.delete(session); expect([204, 404]).toContain(response.status()); }
    catch (error) { failures.push(error); }
    if (failures.length) throw new AggregateError(failures, `Owned source fixture cleanup failed for ${namespace}; do not silently discard these resources.`);
  }
  const source = { namespace, connection, name, sql, catalog, createModel, trackModel: model => models.add(model.id), cleanup };
  try {
    // No IF NOT EXISTS: an unexpected collision is a failure, never ownership.
    await sql(`CREATE SCHEMA "${namespace}"; COMMENT ON SCHEMA "${namespace}" IS '${marker}'`);
  } catch (error) { try { await cleanup(); } catch (cleanupError) { throw new AggregateError([error, cleanupError], "Owned source preparation and cleanup failed"); } throw error; }
  return source;
}
