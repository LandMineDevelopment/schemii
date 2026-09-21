import assert from "node:assert/strict";
import test from "node:test";
import { ensureDatabaseFixtures } from "../e2e/helpers/database-fixtures.js";

function fixtureApi(connections = [], workspaces = []) {
  const writes = [];
  const response = value => ({ ok: () => true, json: async () => structuredClone(value) });
  return {
    writes,
    get: async path => response(path === "/api/v1/connections" ? { connections }
      : path === "/api/v1/schemii/workspaces" ? { workspaces } : { tables: [] }),
    post: async (path, { data }) => {
      writes.push({ path, data });
      if (path === "/api/v1/connections") {
        const connection = { ...data, id: `connection-${connections.length}` };
        connections.push(connection);
        return response(connection);
      }
      const workspace = { ...data, id: `workspace-${workspaces.length}` };
      workspaces.push(workspace);
      return response({ workspace });
    },
  };
}

test("browser setup prepares fresh metadata and is idempotent", async () => {
  const api = fixtureApi();
  const credentials = { username: "test-user", password: async () => "test-password" };
  await ensureDatabaseFixtures(api, credentials);
  assert.equal(api.writes.length, 5);
  assert.equal(api.writes.filter(write => write.path === "/api/v1/connections").length, 3);
  await ensureDatabaseFixtures(api, credentials);
  assert.equal(api.writes.length, 5);
});

test("browser setup preserves existing connections, credentials and designs", async () => {
  const connections = ["schemii_test", "schemii_migration_demo", "organization"].map((database, index) => ({
    id: `existing-${index}`, database, host: "user-owned-host", revision: 7,
  }));
  const workspaces = [{ connectionId: "existing-0", namespace: "bookstore", revision: 9 },
    { connectionId: "existing-1", namespace: "public", revision: 5 }];
  const api = fixtureApi(connections, workspaces);
  await ensureDatabaseFixtures(api, { password: () => { throw new Error("Must not read credentials"); } });
  assert.deepEqual(api.writes, []);
});

test("browser setup reports fixture failures before running dependent tests", async () => {
  const api = fixtureApi();
  api.post = async () => ({ ok: () => false, status: () => 503, text: async () => "Target unavailable" });
  await assert.rejects(ensureDatabaseFixtures(api, { password: async () => "fixture" }),
    /create schemii_test connection failed \(503\): Target unavailable/);
});
