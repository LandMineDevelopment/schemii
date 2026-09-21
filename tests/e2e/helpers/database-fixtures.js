// The launcher installs target schemas, while application metadata starts empty.
// Register these fixtures through the same API as the UI without resetting any
// existing connection, workspace, design, or credential.
async function json(response, operation) {
  if (!response.ok()) {
    throw new Error(`Browser fixture ${operation} failed (${response.status()}): ${await response.text()}`);
  }
  return response.json();
}

export async function ensureDatabaseFixtures(request, credentials) {
  const { connections } = await json(await request.get("/api/v1/connections"), "list connections");
  const { workspaces } = await json(await request.get("/api/v1/schemii/workspaces"), "list workspaces");
  for (const fixture of [
    { database: "schemii_test", namespace: "bookstore" },
    { database: "schemii_migration_demo", namespace: "public" },
    { database: "organization" },
  ]) {
    let connection = connections.find(item => item.database === fixture.database);
    if (!connection) {
      connection = await json(await request.post("/api/v1/connections", {
        data: {
          name: `Browser fixture: ${fixture.database}`,
          host: "postgres", port: 5432, database: fixture.database,
          username: credentials.username, password: await credentials.password(), sslMode: "disable",
        },
      }), `create ${fixture.database} connection`);
      connections.push(connection);
    }
    if (fixture.namespace && !workspaces.some(item => (
      item.connectionId === connection.id && item.namespace === fixture.namespace
    ))) {
      const opened = await json(await request.post("/api/v1/schemii/workspaces/postgres", {
        data: { connectionId: connection.id, namespace: fixture.namespace },
      }), `open ${fixture.database}.${fixture.namespace} workspace`);
      workspaces.push(opened.workspace);
    }
    // Fail once at setup with an actionable error, rather than hundreds of
    // downstream missing-fixture assertions when target seeding is broken.
    await json(await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=${fixture.namespace || "public"}`),
      `verify ${fixture.database} catalog (run ./start.sh first)`);
  }
}
