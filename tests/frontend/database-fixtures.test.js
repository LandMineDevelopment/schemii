import assert from "node:assert/strict";
import test from "node:test";
import { ensureDatabaseFixtures } from "../e2e/helpers/database-fixtures.js";

const ownerId = "fixture-owner";
const credentials = { username: "fixture-user", password: async () => "fixture-password" };
const databases = ["schemii_test", "schemii_migration_demo", "organization"];
const profile = (database, overrides = {}) => ({
  id: `fixture-${database}`, name: `Browser fixture: ${database}`, database,
  host: "postgres", port: 5432, sslMode: "disable", username: credentials.username,
  ownerId, ownership: "user", revision: 1, ...overrides,
});
const workspace = (database, namespace, overrides = {}) => ({
  id: `workspace-${database}`, connectionId: `fixture-${database}`, database, namespace, revision: 1, ...overrides,
});

// Unexpected routes fail: generic success can hide wrong identity reads,
// accidental target adoption and unrecorded mutations.
function fixtureApi(connections = [], workspaces = [], identity = { user: { id: ownerId, disabled: false } }) {
  const writes = [], reads = [];
  const response = value => ({ ok: () => true, json: async () => structuredClone(value) });
  return {
    writes, reads,
    get: async path => {
      reads.push(path);
      if (path === "/api/v1/auth/me") return response(identity);
      if (path === "/api/v1/connections") return response({ connections });
      if (path === "/api/v1/schemii/workspaces") return response({ workspaces });
      const catalog = /^\/api\/v1\/schemoo\/catalog\?connection_id=([^&]+)&namespace=([^&]+)$/.exec(path);
      assert.ok(catalog, `Unexpected fixture read: ${path}`);
      const connection = connections.find(item => item.id === catalog[1]);
      assert.ok(connection?.name === `Browser fixture: ${connection.database}`);
      return response({ tables: [] });
    },
    post: async (path, { data }) => {
      writes.push({ path, data });
      if (path === "/api/v1/connections") {
        const connection = { ...data, id: `created-${connections.length}`, ownerId, ownership: "user", revision: 1 };
        delete connection.password;
        connections.push(connection);
        return response(connection);
      }
      assert.equal(path, "/api/v1/schemii/workspaces/postgres");
      const connection = connections.find(item => item.id === data.connectionId);
      assert.ok(connection);
      const opened = { ...data, database: connection.database, id: `created-workspace-${workspaces.length}`, revision: 1 };
      workspaces.push(opened);
      return response({ workspace: opened });
    },
  };
}

test("browser setup prepares exact owned metadata and reuses it without rereading passwords", async () => {
  const api = fixtureApi();
  const selected = await ensureDatabaseFixtures(api, credentials);
  assert.equal(api.writes.length, 5);
  assert.equal(selected.connections.length, 3); assert.equal(selected.workspaces.length, 2);
  const reused = await ensureDatabaseFixtures(api, { username: credentials.username, password: () => { throw new Error("Must not reread credentials"); } });
  assert.deepEqual(reused, selected);
  assert.equal(api.writes.length, 5);
});

test("same-database seed profiles and workspaces remain untouched while exact test fixtures are provisioned", async () => {
  const connections = databases.map(database => profile(database, { id: `seed-${database}`, name: "User profile", host: "user-owned-host", revision: 7 }));
  const workspaces = [workspace(databases[0], "bookstore", { connectionId: connections[0].id, revision: 9 }),
    workspace(databases[1], "public", { connectionId: connections[1].id, revision: 5 })];
  const original = structuredClone({ connections, workspaces });
  const api = fixtureApi(connections, workspaces);
  const selected = await ensureDatabaseFixtures(api, credentials);
  assert.equal(api.writes.length, 5);
  assert.deepEqual(connections.slice(0, 3), original.connections);
  assert.deepEqual(workspaces.slice(0, 2), original.workspaces);
  assert.deepEqual(selected.workspaces.map(item => item.connectionId), selected.connections.slice(0, 2).map(item => item.id));
  assert.ok(selected.connections.every(item => item.name === `Browser fixture: ${item.database}`));
});

test("setup selects the exact bound workspace even when same-database seed workspaces come first", async () => {
  const connections = databases.map(database => profile(database));
  const workspaces = [workspace(databases[0], "bookstore", { id: "seed", connectionId: "unrelated" }),
    workspace(databases[0], "bookstore"), workspace(databases[1], "public")];
  const api = fixtureApi(connections, workspaces);
  const selected = await ensureDatabaseFixtures(api, credentials);
  assert.deepEqual(selected.workspaces.map(item => item.id), workspaces.slice(1).map(item => item.id));
  assert.equal(api.writes.length, 0);
});

test("a reserved profile with conflicting identity stops all provisioning without adoption or credential changes", async t => {
  for (const drift of [{ database: "other" }, { host: "demo-postgres" }, { port: 5433 }, { username: "reader" },
    { sslMode: "require" }, { ownerId: "other-owner" }, { ownership: "schemii" }, { revision: 0 }]) {
    await t.test(JSON.stringify(drift), async () => {
      // Organization is last: earlier missing profiles must not be created first.
      const api = fixtureApi([profile("organization", drift)]);
      await assert.rejects(ensureDatabaseFixtures(api, credentials), /reserved profile target or ownership drifted/);
      assert.deepEqual(api.writes, []);
    });
  }
});

test("ambiguous fixture profiles or bound workspaces fail before setup mutations", async () => {
  for (const api of [fixtureApi([profile("organization"), profile("organization", { id: "duplicate" })]),
    fixtureApi([profile(databases[0])], [workspace(databases[0], "bookstore"), workspace(databases[0], "bookstore", { id: "duplicate" })])]) {
    await assert.rejects(ensureDatabaseFixtures(api, credentials), /identity is ambiguous/);
    assert.deepEqual(api.writes, []);
  }
  const api = fixtureApi([profile(databases[0])], [workspace(databases[0], "bookstore", { database: "other" })]);
  await assert.rejects(ensureDatabaseFixtures(api, credentials), /workspace target or revision drifted/);
  assert.deepEqual(api.writes, []);
});

test("setup requires the actual account identity and preserves parallel freshness", async () => {
  for (const [identity, ownership] of [[{}, {}], [{ user: { id: ownerId, disabled: true } }, {}],
    [{ user: { id: ownerId } }, { ownerId: "another-account" }]]) {
    const api = fixtureApi([], [], identity);
    await assert.rejects(ensureDatabaseFixtures(api, credentials, ownership), /account identity/);
    assert.deepEqual(api.writes, []);
  }
  const api = fixtureApi([profile(databases[0])]);
  await assert.rejects(ensureDatabaseFixtures(api, credentials, { ownerId, fresh: true }), /Fresh parallel browser account/);
  assert.deepEqual(api.writes, []);
});

test("parallel fixture creation retains beforeCreate/onCreate ledgers in dependency order", async () => {
  const api = fixtureApi(), ledger = [];
  const selected = await ensureDatabaseFixtures(api, credentials, { ownerId, fresh: true,
    beforeCreate: async (kind, identity) => { ledger.push({ kind, identity, writes: api.writes.length }); },
    onCreate: async (kind, resource) => { ledger.push({ kind, id: resource.id, writes: api.writes.length }); },
  });
  assert.deepEqual(ledger.map(item => item.kind), ["connection", "connection", "workspace", "workspace", "connection", "connection", "workspace", "workspace", "connection", "connection"]);
  for (let index = 0; index < ledger.length; index += 2) {
    assert.equal(ledger[index + 1].writes, ledger[index].writes + 1);
    assert.ok(ledger[index + 1].id);
  }
  assert.deepEqual(ledger[0].identity, { database: databases[0], name: `Browser fixture: ${databases[0]}` });
  assert.deepEqual(ledger[2].identity, { connectionId: selected.connections[0].id, namespace: "bookstore", database: databases[0] });
});

test("browser setup reports fixture failures before running dependent tests", async () => {
  const api = fixtureApi();
  api.post = async () => ({ ok: () => false, status: () => 503, text: async () => "Target unavailable" });
  await assert.rejects(ensureDatabaseFixtures(api, credentials),
    /create schemii_test connection failed \(503\): Target unavailable/);
});
