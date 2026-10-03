export function findOrganizationConnection(connections) {
  return connections.find(item => item.database === "organization" &&
    item.name === "Browser fixture: organization" && ["postgres", "demo-postgres"].includes(item.host));
}

// The launcher installs target schemas, while application metadata starts empty.
// Register these fixtures through the same API as the UI without resetting any
// existing connection, workspace, design, or credential.
async function json(response, operation, privateOperation = false) {
  if (!response.ok()) {
    throw new Error(`Browser fixture ${operation} failed (${response.status()})${privateOperation ? '.' : `: ${await response.text()}`}`);
  }
  return response.json();
}

export async function ensureDatabaseFixtures(request, credentials, ownership = {}) {
  const fixtureJson = (response, operation) => json(response, operation, Boolean(ownership.ownerId));
  const { connections } = await fixtureJson(await request.get("/api/v1/connections"), "list connections");
  const { workspaces } = await fixtureJson(await request.get("/api/v1/schemii/workspaces"), "list workspaces");
  if (ownership.fresh && (connections.length || workspaces.length)) {
    throw new Error('Fresh parallel browser account already has database fixtures.');
  }
  const selected = { connections: [], workspaces: [] };
  for (const fixture of [
    { database: "schemii_test", namespace: "bookstore" },
    { database: "schemii_migration_demo", namespace: "public" },
    { database: "organization" },
  ]) {
    let connection = fixture.database === "organization" ? findOrganizationConnection(connections)
      : connections.find(item => item.database === fixture.database);
    if (!connection) {
      await ownership.beforeCreate?.('connection', { database: fixture.database, name: `Browser fixture: ${fixture.database}` });
      connection = await fixtureJson(await request.post("/api/v1/connections", {
        data: {
          name: `Browser fixture: ${fixture.database}`,
          host: "postgres", port: 5432, database: fixture.database,
          username: credentials.username, password: await credentials.password(), sslMode: "disable",
        },
      }), `create ${fixture.database} connection`);
      await ownership.onCreate?.('connection', connection);
      connections.push(connection);
    }
    if (ownership.ownerId && (connection.ownerId !== ownership.ownerId || connection.ownership !== 'user')) {
      throw new Error('Parallel browser connection is not owned by its account.');
    }
    selected.connections.push(connection);
    if (fixture.namespace && !workspaces.some(item => (
      item.connectionId === connection.id && item.namespace === fixture.namespace
    ))) {
      await ownership.beforeCreate?.('workspace', { connectionId: connection.id, namespace: fixture.namespace, database: fixture.database });
      const opened = await fixtureJson(await request.post("/api/v1/schemii/workspaces/postgres", {
        data: { connectionId: connection.id, namespace: fixture.namespace },
      }), `open ${fixture.database}.${fixture.namespace} workspace`);
      await ownership.onCreate?.('workspace', opened.workspace);
      workspaces.push(opened.workspace);
    }
    if (fixture.namespace) selected.workspaces.push(workspaces.find(item => item.connectionId === connection.id && item.namespace === fixture.namespace));
    // Fail once at setup with an actionable error, rather than hundreds of
    // downstream missing-fixture assertions when target seeding is broken.
    await fixtureJson(await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=${fixture.namespace || "public"}`),
      `verify ${fixture.database} catalog (run ./start.sh first)`);
  }
  return selected;
}
