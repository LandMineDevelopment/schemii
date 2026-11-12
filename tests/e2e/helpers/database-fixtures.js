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

export async function browserFixtureOwner(request, expectedOwnerId) {
  const identity = await json(await request.get("/api/v1/auth/me"), "verify account", true);
  const ownerId = identity?.user?.id;
  if (typeof ownerId !== "string" || !ownerId || identity.user.disabled ||
      (expectedOwnerId && ownerId !== expectedOwnerId)) {
    throw new Error("Browser fixture account identity is missing or differs from its owner.");
  }
  return ownerId;
}

// A reserved name is an ownership assertion, never a reason to adopt a target
// with different credentials or to create a second profile around its drift.
export function findBrowserFixtureConnection(connections, { database, username, ownerId }) {
  if (!username || !ownerId) throw new Error("Browser fixture target username and account owner are required.");
  const named = connections.filter(item => item.name === `Browser fixture: ${database}`);
  if (named.length > 1) throw new Error(`Browser fixture ${database} profile identity is ambiguous.`);
  const connection = named[0];
  if (connection && (connection.database !== database || connection.host !== "postgres" || connection.port !== 5432 ||
      connection.username !== username || connection.sslMode !== "disable" || connection.ownership !== "user" ||
      connection.ownerId !== ownerId || !connection.id || !Number.isInteger(connection.revision) || connection.revision < 1)) {
    throw new Error(`Browser fixture ${database} reserved profile target or ownership drifted; preserve it for inspection.`);
  }
  return connection;
}

export function findBrowserFixtureWorkspace(workspaces, connection, namespace) {
  const bound = workspaces.filter(item => item.connectionId === connection.id && item.namespace === namespace);
  if (bound.length > 1) throw new Error(`Browser fixture ${connection.database}.${namespace} workspace identity is ambiguous.`);
  const workspace = bound[0];
  if (workspace && (workspace.database !== connection.database || !workspace.id ||
      !Number.isInteger(workspace.revision) || workspace.revision < 1)) {
    throw new Error("Browser fixture workspace target or revision drifted; preserve it for inspection.");
  }
  return workspace;
}

export async function ensureDatabaseFixtures(request, credentials, ownership = {}) {
  const fixtureJson = (response, operation) => json(response, operation, Boolean(ownership.ownerId));
  const ownerId = await browserFixtureOwner(request, ownership.ownerId);
  const { connections } = await fixtureJson(await request.get("/api/v1/connections"), "list connections");
  const { workspaces } = await fixtureJson(await request.get("/api/v1/schemii/workspaces"), "list workspaces");
  if (ownership.fresh && (connections.length || workspaces.length)) {
    throw new Error('Fresh parallel browser account already has database fixtures.');
  }
  // Preflight every reserved identity before provisioning any missing resource.
  const fixtures = [
    { database: "schemii_test", namespace: "bookstore" },
    { database: "schemii_migration_demo", namespace: "public" },
    { database: "organization" },
  ].map(fixture => {
    const connection = findBrowserFixtureConnection(connections, { database: fixture.database, username: credentials.username, ownerId });
    const workspace = connection && fixture.namespace ? findBrowserFixtureWorkspace(workspaces, connection, fixture.namespace) : undefined;
    return { ...fixture, connection, workspace };
  });
  const selected = { connections: [], workspaces: [] };
  for (const fixture of fixtures) {
    let { connection, workspace } = fixture;
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
      if (!findBrowserFixtureConnection([connection], { database: fixture.database, username: credentials.username, ownerId })) {
        throw new Error("Created browser fixture profile identity differs from its request.");
      }
    }
    selected.connections.push(connection);
    if (fixture.namespace && !workspace) {
      await ownership.beforeCreate?.('workspace', { connectionId: connection.id, namespace: fixture.namespace, database: fixture.database });
      const opened = await fixtureJson(await request.post("/api/v1/schemii/workspaces/postgres", {
        data: { connectionId: connection.id, namespace: fixture.namespace },
      }), `open ${fixture.database}.${fixture.namespace} workspace`);
      await ownership.onCreate?.('workspace', opened.workspace);
      workspace = findBrowserFixtureWorkspace([opened.workspace], connection, fixture.namespace);
      if (!workspace) throw new Error("Created browser fixture workspace identity differs from its request.");
    }
    if (fixture.namespace) selected.workspaces.push(workspace);
    // Fail once at setup with an actionable error, rather than hundreds of
    // downstream missing-fixture assertions when target seeding is broken.
    await fixtureJson(await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=${fixture.namespace || "public"}`),
      `verify ${fixture.database} catalog (run ./start.sh first)`);
  }
  return selected;
}
