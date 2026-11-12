import assert from "node:assert/strict";
import test from "node:test";
import { ownedSchemooSource } from "../e2e/helpers/schemoo-source-fixtures.js";

const profile = overrides => ({ id: "pg_owned", name: "Browser fixture: schemii_test", database: "schemii_test",
  username: "schemii", host: "postgres", port: 5432, sslMode: "disable", ownerId: "test-account", ownership: "user", revision: 2, ...overrides });
const workspace = overrides => ({ id: "ws_owned", connectionId: "pg_owned", database: "schemii_test", namespace: "bookstore", revision: 4, ...overrides });

// Exercise the helper itself. No live transport exists; every allowed Console
// operation is tied to the selected workspace and its generated schema marker.
function sourceApi({ connections = [profile()], workspaces = [workspace()], identity = { user: { id: "test-account" } } } = {}) {
  const calls = [], executions = new Map();
  const sessions = "/api/v1/schemii/workspaces/ws_owned/console/sessions", session = `${sessions}/owned-session`;
  let marker, namespace;
  const response = (value, status = 200) => ({ ok: () => status < 400, status: () => status,
    json: async () => structuredClone(value), text: async () => "offline fixture response" });
  return {
    calls,
    get: async path => {
      calls.push({ method: "GET", path });
      if (path === "/api/v1/auth/me") return response(identity);
      if (path === "/api/v1/connections") return response({ connections });
      if (path === "/api/v1/schemii/workspaces") return response({ workspaces });
      if (path === "/api/v1/schemii/console/settings") return response({ revision: 3 });
      if (path === "/api/v1/schemoo/models") return response({ models: [] });
      assert.ok(executions.has(path), `Unexpected source fixture read: ${path}`);
      return response(executions.get(path));
    },
    post: async (path, { data }) => {
      calls.push({ method: "POST", path, data });
      if (path === sessions) {
        assert.equal(data.expectedWorkspaceRevision, 4); assert.equal(data.expectedSettingsRevision, 3);
        return response({ id: "owned-session" }, 201);
      }
      assert.equal(path, `${session}/executions`, "Console must use the exact bound workspace");
      assert.equal(data.commitMode, "whole_run");
      const created = /^CREATE SCHEMA "(source_[a-f0-9]{32})"; COMMENT ON SCHEMA "\1" IS '(schemii_e2e_source_[a-f0-9]{32})'$/.exec(data.sql);
      let rows = [];
      if (created) { [, namespace, marker] = created; }
      else if (data.sql === `SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = '${namespace}'`) rows = [[marker]];
      else assert.equal(data.sql, `DROP SCHEMA "${namespace}" CASCADE`, "only marker-checked owned schema cleanup is permitted");
      const id = `execution-${executions.size}`;
      executions.set(`${session}/executions/${id}`, { status: "succeeded", results: [{ rows }] });
      return response({ id }, 201);
    },
    delete: async path => {
      calls.push({ method: "DELETE", path });
      assert.equal(path, session);
      return response(null, 204);
    },
  };
}

test("source fixture chooses the exact authorized profile and its bound workspace past unrelated seeds", async () => {
  const selected = profile({ username: "configured-writer" });
  const api = sourceApi({ connections: [profile({ id: "seed", name: "Seed profile", username: "reader" }), selected],
    workspaces: [workspace({ id: "seed-workspace", connectionId: "seed" }), workspace()] });
  const source = await ownedSchemooSource(api, { environment: { SCHEMII_TEST_POSTGRES_USER: "configured-writer" } });
  assert.deepEqual(source.connection, selected);
  assert.match(source.namespace, /^source_[a-f0-9]{32}$/);
  await source.cleanup();
  const statements = api.calls.filter(call => call.data?.sql).map(call => call.data.sql);
  assert.equal(statements.length, 3);
  assert.match(statements[0], /^CREATE SCHEMA/); assert.match(statements[1], /^SELECT obj_description/); assert.match(statements[2], /^DROP SCHEMA/);
  assert.equal(api.calls.at(-1).method, "DELETE");
});

test("source fixture rejects missing and ambiguous ownership before Console settings, session or DDL", async t => {
  const scenarios = {
    "missing account": { identity: {} },
    "disabled account": { identity: { user: { id: "test-account", disabled: true } } },
    "missing reserved profile": { connections: [profile({ name: "Seed profile" })] },
    "wrong owner": { connections: [profile({ ownerId: "other" })] },
    "shared ownership": { connections: [profile({ ownership: "schemii" })] },
    "wrong target database": { connections: [profile({ database: "other" })] },
    "wrong host": { connections: [profile({ host: "user-database" })] },
    "wrong port": { connections: [profile({ port: 5433 })] },
    "wrong SSL": { connections: [profile({ sslMode: "require" })] },
    "wrong PostgreSQL grants identity": { connections: [profile({ username: "reader" })] },
    "duplicate reserved profile": { connections: [profile(), profile({ id: "duplicate" })] },
    "missing bound workspace": { workspaces: [workspace({ connectionId: "seed" })] },
    "wrong bound database": { workspaces: [workspace({ database: "other" })] },
    "wrong bound namespace": { workspaces: [workspace({ namespace: "public" })] },
    "duplicate bound workspace": { workspaces: [workspace(), workspace({ id: "duplicate" })] },
    "missing workspace revision": { workspaces: [workspace({ revision: undefined })] },
  };
  for (const [name, scenario] of Object.entries(scenarios)) await t.test(name, async () => {
    const api = sourceApi(scenario);
    await assert.rejects(ownedSchemooSource(api, { environment: {} }));
    assert.ok(api.calls.every(call => call.method === "GET" && ["/api/v1/auth/me", "/api/v1/connections", "/api/v1/schemii/workspaces"].includes(call.path)),
      "ownership must fail before opening a Console session or issuing any SQL");
  });
});
