const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const chromium = [process.env.CHROMIUM_BIN, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"].find(candidate => candidate && fs.existsSync(candidate));
if (!chromium) {
  if (process.env.SCHEMII_REQUIRE_CHROMIUM === "1") throw new Error("Chromium is required but no supported browser executable was found");
  console.log("Schemer browser contracts skipped: Chromium is unavailable");
  process.exit(0);
}
const schemaFixture = JSON.parse(fs.readFileSync(path.join(root, "examples/schema_starter.json"), "utf8"));
schemaFixture.revision = 1;
schemaFixture.layoutToken = "1".repeat(64);
let schemaRecord = clone(schemaFixture);

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

let dashboard = {
  id: "dashboard_browser",
  version: 3,
  revision: 1,
  updatedAt: "2026-08-23T00:00:00Z",
  dashboard: {
    title: "Browser contract",
    archived: false,
    widgets: [
      { id: "widget_one", kind: "placeholder", title: "First", configuration: {} },
      { id: "widget_two", kind: "placeholder", title: "Second", configuration: {} },
    ],
    slicers: [],
    viewport: { desktop: { y: 0 }, mobile: { y: 0 } },
  },
};
let conflictNext = false;
let dashboardWrites = 0;
let detailFixture = false;
let detailSequence = 0;
let slowSearchRequests = 0;
let maximumActiveDetailResources = 0;
let detailCapacity = 1;
const activeDetailResources = new Set();
const detailEvents = [];
const busyReleaseOnce = new Set();
let aggregateDelayMs = 0;
let aggregateRequests = 0;
let activeAggregateRequests = 0;
let maximumActiveAggregateRequests = 0;
let legacyPreviewRequests = 0;
let legacyApplyRequests = 0;
let legacyPreviewFailureOnce = false;
let legacyApplyFailureOnce = false;
let legacyBatchFixture = false;
let schemiiWrites = 0;
let schemiiPostgresRequests = 0;
let schemiiConflictNext = false;
let schemiiSchemaGetDelayMs = 0;
const schemiiWriteRequests = [];
const fixtureSource = {
  profileId: "profile_browser", database: "browser", namespace: "public", relation: "orders",
  kind: "table", fingerprint: "b".repeat(64), snapshotVersion: 2,
  columns: [
    { name: "status", type: "text", nullable: false, ordinal: 1, capabilities: {} },
    { name: "amount", type: "numeric", nullable: false, ordinal: 2, capabilities: {} },
  ],
};
const fixtureQuery = {
  version: 2,
  dimensions: [{ id: "dimension_status", label: "Status", column: "status", temporal: null, nullBehavior: "preserve", numberFormat: { style: "auto" } }],
  measures: [{ id: "measure_amount", label: "Amount", column: "amount", aggregation: "sum", distinct: false, nullBehavior: "preserve", numberFormat: { style: "decimal", fractionDigits: 0 } }],
  filters: [], sort: [], limit: 100,
};

function sourcedDashboard() {
  return {
    id: "dashboard_browser", version: 3, revision: 20, updatedAt: "2026-08-23T00:00:00Z",
    dashboard: {
      title: "Detail browser contract", archived: false,
      widgets: [{
        id: "widget_detail", kind: "aggregate_report", title: "Orders by status",
        configuration: {
          source: clone(fixtureSource), query: clone(fixtureQuery),
          table: { version: 1, columns: [{ targetId: "dimension_status", width: 160, hidden: false, pinned: false, label: "Status" }, { targetId: "measure_amount", width: 160, hidden: false, pinned: false, label: "Amount" }], pageSize: 25 },
          visualization: { version: 1, mode: "bar", selections: { kpi: { measureIds: ["measure_amount"] }, bar: { dimensionId: "dimension_status", measureIds: ["measure_amount"] }, line: { dimensionId: "dimension_status", measureIds: ["measure_amount"] }, donut: { dimensionId: "dimension_status", measureId: "measure_amount" } } },
          detail: { version: 1, columns: [{ sourceColumn: "status", label: "Status", width: 160, hidden: false, searchable: true, numberFormat: { style: "auto" } }, { sourceColumn: "amount", label: "Amount", width: 160, hidden: false, searchable: true, numberFormat: { style: "decimal", fractionDigits: 0 } }], defaultSort: null, rowIdentifier: null, pageSize: 25 },
        },
      }],
      slicers: [], viewport: { desktop: { y: 0 }, mobile: { y: 0 } },
    },
  };
}

function sourcedDashboardWithWidgets(count) {
  const record = sourcedDashboard();
  record.dashboard.title = `${count} slow widgets`;
  record.dashboard.widgets = Array.from({ length: count }, (_, index) => {
    const widget = clone(record.dashboard.widgets[0]);
    widget.id = `widget_slow_${String(index).padStart(3, "0")}`;
    widget.title = `Slow widget ${index + 1}`;
    return widget;
  });
  return record;
}

function legacyDashboard() {
  return {
    id: "dashboard_browser", version: 3, revision: 40, updatedAt: "2026-08-23T00:00:00Z",
    dashboard: {
      title: "Legacy source browser contract", archived: false,
      widgets: [{
        id: "widget_legacy", kind: "placeholder", title: "Legacy orders",
        configuration: { source: {
          profileId: "profile_browser", database: "browser", namespace: "public", relation: "orders", kind: "table",
          fingerprint: "a".repeat(64), columns: fixtureSource.columns.map(({ capabilities, ...column }) => column),
        } },
      }],
      slicers: [], viewport: { desktop: { y: 0 }, mobile: { y: 0 } },
    },
  };
}

function legacyBatchDashboard() {
  const record = legacyDashboard();
  record.revision = 50;
  record.dashboard.title = "Deferred legacy source browser contract";
  record.dashboard.widgets = Array.from({ length: 5 }, (_, index) => ({
    id: `widget_legacy_batch_${index}`, kind: "placeholder", title: `Legacy batch ${index + 1}`,
    configuration: { source: {
      profileId: `profile_batch_${index}`, database: `browser_${index}`, namespace: "public", relation: `orders_${index}`,
      kind: "table", fingerprint: "a".repeat(64), columns: fixtureSource.columns.map(({ capabilities, ...column }) => column),
    } },
  }));
  return record;
}

function aggregateResult() {
  return {
    source: clone(fixtureSource), queryVersion: 2,
    columns: [
      { id: "dimension_status", label: "Status", sourceColumn: "status", kind: "dimension", numberFormat: { style: "auto" } },
      { id: "measure_amount", label: "Amount", sourceColumn: "amount", kind: "measure", numberFormat: { style: "decimal", fractionDigits: 0 } },
    ],
    rows: [["open", 10], ["closed", 7]], rowCount: 2, limit: 100, truncated: false,
    sql: "SELECT status, sum(amount) FROM orders GROUP BY status", parameters: [], effectiveQuery: clone(fixtureQuery), slicerLineage: [],
    queriedAt: "2026-08-24T00:00:00Z", queryDurationMs: 1,
    lineage: { measures: [{ id: "measure_amount", label: "Amount", column: "amount", aggregation: "sum" }], filterGroups: [] },
  };
}

function detailResult(resourceId, body, label) {
  const offset = body.offset || 0;
  return {
    source: clone(fixtureSource), queryVersion: 2,
    columns: [
      { id: "detail_column_1", label: "Status", sourceColumn: "status", numberFormat: { style: "auto" } },
      { id: "detail_column_2", label: "Amount", sourceColumn: "amount", numberFormat: { style: "decimal", fractionDigits: 0 } },
    ],
    rows: [[label, detailSequence]], offset, nextOffset: offset + 1, limit: body.limit, matchingRowCount: 1, hasMore: false, truncated: false,
    sql: "SELECT status, amount FROM orders", parameters: [], effectiveQuery: clone(fixtureQuery), slicerLineage: [],
    queriedAt: "2026-08-24T00:00:01Z", queryDurationMs: 1, lineage: { filterGroups: [] },
    resultResource: {
      version: 1, id: resourceId, binding: `binding_${resourceId}`, kind: "detail", state: "retained", processLocal: true,
      expiresAt: "2026-08-24T00:05:00Z", availableRows: 1,
      page: { offset, returnedRows: 1, hasNext: false, hasPrevious: false, nextCursor: null, previousCursor: null },
      export: { formats: ["json", "csv"] },
    },
  };
}

function readJson(request, callback) {
  let body = "";
  request.on("data", chunk => { body += chunk; });
  request.on("end", () => callback(JSON.parse(body || "{}")));
}

function sendJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body), "Cache-Control": "no-store" });
  response.end(body);
}

function staticFile(requestPath) {
  if (requestPath === "/" || requestPath === "/index.html") return path.join(root, "src/schemii/schemer_web/index.html");
  if (requestPath === "/app.js" || requestPath === "/styles.css") return path.join(root, "src/schemii/schemer_web", requestPath.slice(1));
  if (["/schemii", "/schemii/", "/schemii/index.html"].includes(requestPath)) return path.join(root, "src/schemii/web/index.html");
  if (requestPath === "/schemii/app.js" || requestPath === "/schemii/styles.css") return path.join(root, "src/schemii/web", requestPath.slice("/schemii/".length));
  if (requestPath.startsWith("/shared/") && !requestPath.includes("..")) return path.join(root, "src/schemii/shared_web", requestPath.slice("/shared/".length));
  return null;
}

const server = http.createServer((request, response) => {
  const requestPath = new URL(request.url, "http://localhost").pathname;
  if (requestPath === "/favicon.ico") return response.writeHead(204).end();
  if (requestPath === "/api/session") return sendJson(response, 200, { token: "browser-test-token", serverId: "browser-test-server" });
  if (requestPath === "/api/schemas" && request.method === "GET") return sendJson(response, 200, { schemas: [clone(schemaRecord)] });
  if (requestPath === `/api/schemas/${schemaRecord.id}` && request.method === "GET") {
    const send = () => sendJson(response, 200, clone(schemaRecord));
    if (schemiiSchemaGetDelayMs) {
      const delay = schemiiSchemaGetDelayMs;
      schemiiSchemaGetDelayMs = 0;
      return setTimeout(send, delay);
    }
    return send();
  }
  if (requestPath === `/api/schemas/${schemaRecord.id}` && request.method === "PUT") {
    schemiiWrites += 1;
    return readJson(request, body => {
      schemiiWriteRequests.push({ body: clone(body), layoutToken: request.headers["x-schemii-layout-token"] || null });
      if (schemiiConflictNext) {
        schemiiConflictNext = false;
        schemaRecord = clone(schemaRecord);
        schemaRecord.revision += 1;
        schemaRecord.layoutToken = "2".repeat(64);
        schemaRecord.updatedAt = "2026-08-25T12:00:00Z";
        schemaRecord.schema.projectName = "Authoritative browser design";
        schemaRecord.schema.tables[0].x = 777;
        return sendJson(response, 409, { error: { code: "schema_conflict", message: "The saved design changed in another session" } });
      }
      schemaRecord = {
        id: body.id,
        revision: schemaRecord.revision + 1,
        layoutToken: String(schemaRecord.revision + 1).repeat(64).slice(0, 64),
        updatedAt: "2026-08-25T12:01:00Z",
        schema: clone(body.schema),
      };
      return sendJson(response, 200, { saved: schemaRecord.id, revision: schemaRecord.revision, updatedAt: schemaRecord.updatedAt, layoutToken: schemaRecord.layoutToken });
    });
  }
  if (requestPath.startsWith("/api/schemas/") && request.method !== "GET") schemiiWrites += 1;
  if (requestPath.startsWith("/api/postgres/") && requestPath !== "/api/postgres/profiles") schemiiPostgresRequests += 1;
  if (requestPath === "/api/postgres/profiles") return sendJson(response, 200, { profiles: detailFixture ? [{ id: "profile_browser", name: "Browser profile", host: "localhost", port: 5432, dbname: "browser", user: "browser", sslmode: "prefer", timeout: 5, contextFingerprint: "a".repeat(64) }] : [] });
  if (detailFixture && requestPath === "/api/postgres/profiles/profile_browser/namespaces") {
    return sendJson(response, 200, { profileId: "profile_browser", profileFingerprint: "a".repeat(64), database: "browser", catalogFingerprint: "c".repeat(64), scope: "user", namespaces: ["public"], entries: [{ name: "public", classification: "user", system: false }], page: { pageSize: 100, returned: 1, hasMore: false, nextCursor: null } });
  }
  if ((detailFixture && requestPath === "/api/postgres/profiles/profile_browser/relation/verify-batch" || legacyBatchFixture && /^\/api\/postgres\/profiles\/[^/]+\/relation\/verify-batch$/.test(requestPath)) && request.method === "POST") {
    return readJson(request, body => sendJson(response, 200, { results: body.sources.map(source => source.profileId === "profile_batch_4" ? {
      ...source, matches: false, status: "changed", expectedKind: "table", currentKind: "table",
      missingColumns: [], changedColumns: [{ name: "amount", changes: ["capabilities"] }], addedColumns: [],
    } : { ...source, matches: true, status: "current", missingColumns: [], changedColumns: [], addedColumns: [] }) }));
  }
  if (detailFixture && requestPath === "/api/postgres/profiles/profile_browser/saved-widgets/aggregate" && request.method === "POST") {
    return readJson(request, () => {
      aggregateRequests += 1;
      activeAggregateRequests += 1;
      maximumActiveAggregateRequests = Math.max(maximumActiveAggregateRequests, activeAggregateRequests);
      const send = () => {
        activeAggregateRequests -= 1;
        sendJson(response, 200, aggregateResult());
      };
      if (aggregateDelayMs) setTimeout(send, aggregateDelayMs);
      else send();
    });
  }
  if (detailFixture && requestPath === "/api/postgres/profiles/profile_browser/saved-widgets/detail" && request.method === "POST") {
    return readJson(request, body => {
      const search = body.searches?.[0]?.value;
      const slowOrdinal = search === "slow" ? ++slowSearchRequests : 0;
      detailSequence += 1;
      const resourceId = `detail_resource_${detailSequence}`;
      detailEvents.push(`post:${resourceId}:${search || body.selection?.dimensions?.[0]?.value || "all"}`);
      if (activeDetailResources.size >= detailCapacity) {
        return sendJson(response, 429, { error: { code: "structured_result_capacity_exhausted", message: "Detail capacity is exhausted" } });
      }
      activeDetailResources.add(resourceId);
      maximumActiveDetailResources = Math.max(maximumActiveDetailResources, activeDetailResources.size);
      const send = () => sendJson(response, 200, detailResult(resourceId, body, slowOrdinal === 1 ? "stale slow" : search ? `search ${slowOrdinal}` : `selection ${detailSequence}`));
      if (slowOrdinal === 1) setTimeout(send, 350);
      else send();
    });
  }
  const structuredResult = detailFixture && requestPath.match(/^\/api\/postgres\/profiles\/profile_browser\/structured-results\/(detail_resource_\d+)$/);
  if (structuredResult && request.method === "DELETE") {
    const resourceId = structuredResult[1];
    detailEvents.push(`delete:${resourceId}`);
    if (busyReleaseOnce.delete(resourceId)) return sendJson(response, 409, { error: { code: "result_busy", message: "Structured result is serving another page or export" } });
    activeDetailResources.delete(resourceId);
    return sendJson(response, 200, { resultId: resourceId, state: "cancelled", closed: true });
  }
  if (requestPath === "/api/dashboards/summary") {
    return sendJson(response, 200, { summaries: [{ id: dashboard.id, title: dashboard.dashboard.title, archived: false, revision: dashboard.revision, widgetCount: dashboard.dashboard.widgets.length }], page: { pageSize: 100, returned: 1, hasMore: false, nextCursor: null } });
  }
  if (requestPath === "/api/dashboards/legacy-sources/preview" && request.method === "POST") {
    return readJson(request, body => {
      legacyPreviewRequests += 1;
      if (legacyPreviewFailureOnce) {
        legacyPreviewFailureOnce = false;
        return sendJson(response, 503, { error: { code: "introspection_failed", message: "PostgreSQL review is temporarily unavailable" } });
      }
      if (legacyBatchFixture) {
        const allIds = dashboard.dashboard.widgets.map(widget => widget.id);
        const firstBatch = body.widgetIds.length === allIds.length;
        assert.deepEqual(body, {
          dashboardId: "dashboard_browser", expectedRevision: 50,
          widgetIds: firstBatch ? allIds : [allIds[4]],
        });
        const widgetIds = firstBatch ? allIds.slice(0, 4) : [allIds[4]];
        const compatible = firstBatch ? [] : [allIds[4]];
        const results = widgetIds.map((widgetId, index) => firstBatch ? {
          widgetId, title: `Legacy batch ${index + 1}`, status: "incompatible",
          error: { code: "legacy_source_changed", message: "Reselect this source" },
        } : {
          widgetId, title: "Legacy batch 5", status: "compatible",
          source: { profileId: "profile_batch_4", database: "browser_4", namespace: "public", relation: "orders_4", kind: "table" },
          profileFingerprint: "9".repeat(64), savedLegacyFingerprint: "a".repeat(64),
          currentLegacyFingerprint: "a".repeat(64), currentFingerprint: "b".repeat(64),
          columnCount: 2, columns: "exact", query: "not_configured",
        });
        return sendJson(response, 200, {
          dashboardId: body.dashboardId, expectedRevision: body.expectedRevision, widgetIds,
          deferredWidgetIds: firstBatch ? [allIds[4]] : [], maximumUniqueProfileDatabases: 4, maximumDigestLength: 28604,
          results, compatibleWidgetIds: compatible, incompatibleWidgetIds: firstBatch ? widgetIds : [],
          digest: firstBatch ? "signed-batch-first" : "signed-batch-second", expiresAt: "2026-08-24T23:59:59Z",
        });
      }
      sendJson(response, 200, {
        dashboardId: body.dashboardId, expectedRevision: body.expectedRevision, widgetIds: body.widgetIds,
        deferredWidgetIds: [], maximumUniqueProfileDatabases: 4, maximumDigestLength: 28604,
        results: [{
          widgetId: "widget_legacy", title: "Legacy orders", status: "compatible",
          source: { profileId: "profile_browser", database: "browser", namespace: "public", relation: "orders", kind: "table" },
          profileFingerprint: "9".repeat(64),
          savedLegacyFingerprint: "a".repeat(64), currentLegacyFingerprint: "a".repeat(64), currentFingerprint: "b".repeat(64),
          columnCount: 2, columns: "exact", query: "not_configured",
        }],
        compatibleWidgetIds: ["widget_legacy"], incompatibleWidgetIds: [], digest: "signed-browser-review", expiresAt: "2026-08-24T23:59:59Z",
      });
    });
  }
  if (requestPath === "/api/dashboards/legacy-sources/apply" && request.method === "POST") {
    return readJson(request, body => {
      legacyApplyRequests += 1;
      if (legacyBatchFixture) {
        assert.deepEqual(body, {
          dashboardId: "dashboard_browser", expectedRevision: 50, widgetIds: ["widget_legacy_batch_4"],
          digest: "signed-batch-second", confirmed: true,
        });
        const source = dashboard.dashboard.widgets[4].configuration.source;
        dashboard.dashboard.widgets[4].configuration.source = {
          ...source, fingerprint: "b".repeat(64), snapshotVersion: 2, columns: clone(fixtureSource.columns),
        };
        dashboard.revision = 51;
        dashboard.updatedAt = "2026-08-24T00:00:01Z";
        return sendJson(response, 200, {
          dashboardId: "dashboard_browser", previousRevision: 50, revision: 51,
          upgradedWidgetIds: ["widget_legacy_batch_4"], incompatibleWidgetIds: [],
          postWriteVerification: { status: "changed", changedWidgetIds: ["widget_legacy_batch_4"], unavailableWidgetIds: [] },
        });
      }
      assert.deepEqual(body, {
        dashboardId: "dashboard_browser", expectedRevision: 40, widgetIds: ["widget_legacy"],
        digest: "signed-browser-review", confirmed: true,
      });
      if (legacyApplyFailureOnce) {
        legacyApplyFailureOnce = false;
        return sendJson(response, 409, { error: { code: "legacy_source_upgrade_changed", message: "Legacy source verification changed" } });
      }
      dashboard.dashboard.widgets[0].configuration.source = clone(fixtureSource);
      dashboard.revision = 41;
      dashboard.updatedAt = "2026-08-24T00:00:00Z";
      sendJson(response, 200, {
        dashboardId: "dashboard_browser", previousRevision: 40, revision: 41,
        upgradedWidgetIds: ["widget_legacy"], incompatibleWidgetIds: [],
        postWriteVerification: { status: "current", changedWidgetIds: [], unavailableWidgetIds: [] },
      });
    });
  }
  if (requestPath === `/api/dashboards/${dashboard.id}` && request.method === "GET") return sendJson(response, 200, clone(dashboard));
  if (requestPath === `/api/dashboards/${dashboard.id}` && request.method === "PUT") {
    let body = "";
    request.on("data", chunk => { body += chunk; });
    request.on("end", () => {
      dashboardWrites += 1;
      if (conflictNext) {
        conflictNext = false;
        return sendJson(response, 409, { error: { code: "dashboard_changed", message: "Dashboard changed in the browser fixture", details: { currentRevision: dashboard.revision } } });
      }
      const payload = JSON.parse(body);
      dashboard = { ...clone(payload.record), revision: dashboard.revision + 1, updatedAt: new Date().toISOString() };
      sendJson(response, 200, clone(dashboard));
    });
    return;
  }
  const file = staticFile(requestPath);
  if (file && fs.existsSync(file)) {
    const type = file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "text/html";
    response.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" });
    fs.createReadStream(file).pipe(response);
    return;
  }
  sendJson(response, 404, { error: { code: "not_found", message: "Browser fixture route not found" } });
});

class CdpPipe {
  constructor(process) {
    this.process = process;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.buffer = Buffer.alloc(0);
    process.stdio[4].on("data", chunk => this.read(chunk));
  }

  read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    let delimiter;
    while ((delimiter = this.buffer.indexOf(0)) >= 0) {
      const message = JSON.parse(this.buffer.subarray(0, delimiter).toString("utf8"));
      this.buffer = this.buffer.subarray(delimiter + 1);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(message.error.message));
        else resolve(message.result);
      } else if (message.method) {
        for (const listener of this.listeners.get(message.method) ?? []) listener(message.params);
      }
    }
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const message = { id, method, params, ...(sessionId ? { sessionId } : {}) };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.process.stdio[3].write(`${JSON.stringify(message)}\0`);
    });
  }

  once(method) {
    return new Promise(resolve => {
      const listener = params => {
        this.listeners.set(method, (this.listeners.get(method) ?? []).filter(item => item !== listener));
        resolve(params);
      };
      this.listeners.set(method, [...(this.listeners.get(method) ?? []), listener]);
    });
  }

  on(method, listener) {
    this.listeners.set(method, [...(this.listeners.get(method) ?? []), listener]);
  }
}

async function main() {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "schemer-browser-"));
  const browser = spawn(chromium, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--remote-debugging-pipe",
    `--user-data-dir=${userData}`, "--window-size=1440,900", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
  const stderr = [];
  browser.stderr.on("data", chunk => stderr.push(chunk.toString()));
  const cdp = new CdpPipe(browser);
  const exceptions = [];
  let sessionId;

  try {
    await cdp.send("Browser.getVersion");
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    ({ sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true }));
    await Promise.all([
      cdp.send("Page.enable", {}, sessionId),
      cdp.send("Runtime.enable", {}, sessionId),
      cdp.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId),
    ]);
    cdp.on("Runtime.exceptionThrown", params => exceptions.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text || "Browser exception"));

    const evaluate = async expression => {
      const response = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
      return response.result.value;
    };
    const waitFor = async (expression, label, timeout = 8000) => {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        if (await evaluate(`Boolean(${expression})`)) return;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      const diagnostic = await evaluate('({ title: document.title, text: document.body?.innerText?.slice(0, 500), widgets: document.querySelectorAll(".widget").length })');
      throw new Error(`Timed out waiting for ${label}: ${JSON.stringify(diagnostic)}; exceptions: ${exceptions.join(" | ")}`);
    };
    const waitForNode = async (predicate, label, timeout = 8000) => {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        if (predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      throw new Error(`Timed out waiting for ${label}; detail events: ${detailEvents.join(" | ")}`);
    };
    const navigate = async url => {
      const loaded = cdp.once("Page.loadEventFired");
      await cdp.send("Page.navigate", { url }, sessionId);
      await loaded;
    };
    const walkOnboarding = async (appName, expectedReplayLabel = null) => {
      await waitFor('document.querySelector("#onboarding-dialog")?.open && document.querySelectorAll("[data-onboarding-page]").length === 7', `${appName} seven-page introduction`);
      assert.equal(await evaluate('document.querySelectorAll("#onboarding-progress i").length'), 7, `${appName} must render seven progress markers`);
      for (let page = 0; page < 7; page += 1) {
        await waitFor(`document.querySelector("#onboarding-step-label").textContent === "${page + 1} of 7"`, `${appName} introduction page ${page + 1}`);
        const state = await evaluate(`(() => {
          const visiblePages = [...document.querySelectorAll("[data-onboarding-page]")].filter(page => !page.hidden);
          const active = visiblePages[0];
          const replay = active?.querySelector(".tour-demo-toggle");
          const status = active?.querySelector(".tour-demo-status");
          const shot = active?.querySelector(".onboarding-screenshot");
           return {
             visiblePages: visiblePages.length,
             page: active?.dataset.onboardingPage,
            replayVisible: Boolean(replay && replay.getBoundingClientRect().width && replay.getBoundingClientRect().height),
            replayLabel: replay?.textContent,
            statusRole: status?.getAttribute("role"),
            statusLive: status?.getAttribute("aria-live"),
            statusText: status?.textContent,
             statusClipped: Boolean(status && (status.scrollWidth > status.clientWidth || status.scrollHeight > status.clientHeight)),
             sceneVisible: Boolean(shot && shot.getBoundingClientRect().width && shot.getBoundingClientRect().height),
             documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
             workspace: (() => {
               const workspace = active?.querySelector(".tour-workspace-demo");
               if (!workspace || active?.dataset.onboardingPage !== "3") return null;
               const originalClassName = workspace.className;
               const visible = element => {
                 const rect = element?.getBoundingClientRect();
                 const bounds = shot?.getBoundingClientRect();
                 const style = element && getComputedStyle(element);
                 return Boolean(rect && bounds && style && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 && rect.right > bounds.left && rect.left < bounds.right && rect.bottom > bounds.top && rect.top < bounds.bottom);
               };
               const rect = element => {
                 const bounds = element.getBoundingClientRect();
                 return { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom, width: bounds.width, height: bounds.height };
               };
               const editor = workspace.querySelector('[data-tour-sql-pane="editor"]');
               const results = workspace.querySelector('[data-tour-sql-pane="results"]');
                const sqlState = {
                  selectorColor: getComputedStyle(workspace.querySelector('[data-onboarding-target="sql"]')).color,
                  stopDisplay: getComputedStyle(workspace.querySelector(".tour-peer-stop")).display,
                  completedVisible: visible(workspace.querySelector(".tour-peer-result-body")),
                  drawerVisible: visible(workspace.querySelector(".tour-peer-sql-drawer")),
                  queryMenuVisible: visible(workspace.querySelector(".tour-peer-query-menu")),
                  editor: rect(editor),
                  results: rect(results),
                };
               workspace.className = "onboarding-screenshot tour-workspace-demo demo-views demo-catalog";
               const catalogState = {
                 catalogVisible: visible(workspace.querySelector('[data-tour-view-pane="catalog"]')),
                 rootVisible: visible(workspace.querySelector('[data-tour-view-role="root-query-block"]')),
                 finalVisible: visible(workspace.querySelector('[data-tour-view-role="final-view"]')),
                 selectorColor: getComputedStyle(workspace.querySelector('[data-onboarding-target="views"]')).color,
               };
               workspace.className = "onboarding-screenshot tour-workspace-demo demo-views demo-catalog demo-lineage demo-definition";
                const definitionState = {
                  previewVisible: visible(workspace.querySelector('[data-onboarding-target="preview-view"]')),
                  catalogVisible: visible(workspace.querySelector('[data-tour-view-pane="catalog"]')),
                  rootVisible: visible(workspace.querySelector('[data-tour-view-role="root-query-block"]')),
                  finalVisible: visible(workspace.querySelector('[data-tour-view-role="final-view"]')),
                };
                workspace.className = "onboarding-screenshot tour-workspace-demo demo-views demo-catalog demo-lineage demo-definition demo-sql demo-queries";
                const queriesState = {
                  drawerVisible: visible(workspace.querySelector(".tour-peer-sql-drawer")),
                  queryMenuVisible: visible(workspace.querySelector(".tour-peer-query-menu")),
                };
                workspace.className = "onboarding-screenshot tour-workspace-demo demo-views demo-catalog demo-lineage demo-definition demo-sql demo-queries demo-query-menu";
                const queryMenuState = {
                  drawerVisible: visible(workspace.querySelector(".tour-peer-sql-drawer")),
                  queryMenuVisible: visible(workspace.querySelector(".tour-peer-query-menu")),
                };
                workspace.className = originalClassName;
                return { sqlState, catalogState, definitionState, queriesState, queryMenuState };
             })(),
           };
         })()`);
        assert.equal(state.visiblePages, 1, `${appName} page ${page + 1} must be the only visible tutorial page`);
        assert.equal(state.page, String(page));
        assert.equal(state.replayVisible, true, `${appName} page ${page + 1} needs a visible replay control`);
        assert.match(state.replayLabel, /^(?:Pause|Play) demo$/);
        if (expectedReplayLabel) assert.equal(state.replayLabel, expectedReplayLabel, `${appName} page ${page + 1} must start in its reduced-motion static state`);
        assert.equal(state.statusRole, "status");
        assert.equal(state.statusLive, "polite");
        assert.ok(state.statusText, `${appName} page ${page + 1} needs a changing status description`);
        assert.equal(state.statusClipped, false, `${appName} page ${page + 1} must show its complete status description`);
         assert.equal(state.sceneVisible, true, `${appName} page ${page + 1} needs a visible synthetic scene`);
         assert.equal(state.documentOverflow, false, `${appName} page ${page + 1} must not overflow the viewport`);
         if (state.workspace) {
            const { sqlState, catalogState, definitionState, queriesState, queryMenuState } = state.workspace;
           assert.equal(sqlState.selectorColor, "rgb(159, 216, 255)", `${appName} SQL selector must use the production blue accent`);
            assert.equal(sqlState.stopDisplay, "none", `${appName} completed SQL result must not retain the running-only Stop action`);
            assert.equal(sqlState.completedVisible, true, `${appName} reduced-motion state must show the retained result`);
            assert.equal(sqlState.drawerVisible, false, `${appName} completed SQL result must close the transient Queries drawer`);
            assert.equal(sqlState.queryMenuVisible, false, `${appName} completed SQL result must close the query menu`);
           assert.ok(Math.abs(sqlState.editor.left - sqlState.results.left) < 1 && Math.abs(sqlState.editor.width - sqlState.results.width) < 1, `${appName} Editor and Results must share one vertical pane column`);
           assert.ok(sqlState.editor.bottom <= sqlState.results.top + 1, `${appName} Results must exchange vertical space below Editor rather than overlay it`);
           assert.ok(sqlState.results.height > 80, `${appName} retained Results pane must expand enough to expose its actions`);
           assert.equal(catalogState.selectorColor, "rgb(185, 167, 255)", `${appName} Views selector must use the production purple accent`);
           assert.equal(catalogState.catalogVisible, true, `${appName} Browse state must show the right view catalog`);
           assert.equal(catalogState.rootVisible, true, `${appName} view catalog state must keep the outer-SELECT root visible`);
           assert.equal(catalogState.finalVisible, true, `${appName} view catalog state must keep the final view visible`);
            assert.equal(definitionState.previewVisible, true, `${appName} definition state must expose Preview changes in its own pane`);
            assert.equal(definitionState.catalogVisible, true, `${appName} definition review must retain the open catalog drawer`);
            assert.equal(definitionState.rootVisible, false, `${appName} definition review must collapse the lineage body to its header`);
            assert.equal(definitionState.finalVisible, false, `${appName} definition review must give the main pane to PostgreSQL SQL`);
            assert.equal(queriesState.drawerVisible, true, `${appName} Queries must open in the right drawer`);
            assert.equal(queriesState.queryMenuVisible, false, `${appName} opening Queries must not also open the View menu`);
            assert.equal(queryMenuState.drawerVisible, false, `${appName} opening the View menu must close the Queries drawer`);
            assert.equal(queryMenuState.queryMenuVisible, true, `${appName} the query menu must attach below the View control`);
         }
         if (page < 6) await evaluate('document.querySelector("#onboarding-next").click()');
      }
      assert.equal(await evaluate('document.querySelector("#onboarding-next [data-onboarding-next-label], #onboarding-next span").textContent'), "Finish");
      await evaluate('document.querySelector("#onboarding-next").click()');
      await waitFor('!document.querySelector("#onboarding-dialog").open', `${appName} introduction close`);
    };

    await navigate(`http://127.0.0.1:${port}/`);
    await waitFor('document.querySelectorAll(".dashboard-canvas .widget").length === 2', "desktop widgets");
    const hiddenLayoutInstructions = await evaluate(`(() => {
      const element = document.querySelector("#layout-instructions");
      const style = getComputedStyle(element);
      return { width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height, position: style.position, overflow: style.overflow };
    })()`);
    assert.deepEqual(hiddenLayoutInstructions, { width: 1, height: 1, position: "absolute", overflow: "hidden" }, "assistive layout instructions must not render as dashboard content");
    const writesBeforeSchemerOnboarding = dashboardWrites;
    const postgresBeforeSchemerOnboarding = schemiiPostgresRequests;
    await walkOnboarding("Schemer");
    assert.equal(dashboardWrites, writesBeforeSchemerOnboarding, "Schemer tutorial pages must not mutate a dashboard");
    assert.equal(schemiiPostgresRequests, postgresBeforeSchemerOnboarding, "Schemer tutorial pages must not query PostgreSQL");
    await evaluate('document.querySelector("#dashboard-menu").open = true; document.querySelector("#show-onboarding-button").click()');
    await waitFor('document.querySelector("#onboarding-dialog").open && document.querySelector("#onboarding-step-label").textContent === "1 of 7"', "Schemer menu introduction replay");
    await evaluate('document.querySelector("#onboarding-skip").click()');
    await waitFor('!document.querySelector("#onboarding-dialog").open', "Schemer replay close");
    const topbar = await evaluate(`(() => {
      const controls = ["ai-button", "postgres-console-button", "connections-button", "edit-mode-button", "dashboard-menu"]
        .map(id => id === "dashboard-menu" ? document.querySelector("#dashboard-menu > summary") : document.querySelector("#" + id));
      return {
        nav: document.querySelector(".top-actions").tagName,
        navLabel: document.querySelector(".top-actions").getAttribute("aria-label"),
        controls: controls.map(control => ({
          className: control.className,
          label: control.getAttribute("aria-label"),
          tooltip: control.dataset.tooltip,
          width: control.getBoundingClientRect().width,
          height: control.getBoundingClientRect().height,
          svg: Boolean(control.querySelector("svg")),
        })),
        consoleDialog: {
          popup: document.querySelector("#postgres-console-button").getAttribute("aria-haspopup"),
          controls: document.querySelector("#postgres-console-button").getAttribute("aria-controls"),
          expanded: document.querySelector("#postgres-console-button").getAttribute("aria-expanded"),
        },
      };
    })()`);
    assert.equal(topbar.nav, "NAV");
    assert.equal(topbar.navLabel, "Dashboard tools");
    assert.deepEqual(topbar.controls.map(control => control.label), ["AI dashboard assistant", "Open PostgreSQL Console", "Data sources", "Edit dashboard", "Dashboard actions"]);
    assert.ok(topbar.controls.every(control => control.className.includes("shared-icon-button") && control.className.includes("top-action-icon") && control.tooltip && control.svg), "every desktop top-bar action must use the shared icon and tooltip contract");
    assert.ok(topbar.controls.every(control => control.width === 31 && control.height === 31), "desktop top-bar actions must use one standard geometry");
    assert.equal(topbar.consoleDialog.popup, "dialog");
    assert.ok(topbar.consoleDialog.controls, "the Console icon must retain its dialog ownership");
    assert.equal(topbar.consoleDialog.expanded, "false");
    const secondaryToolbar = await evaluate(`(() => {
      const systemInput = document.querySelector("#system-namespaces");
      const controls = [systemInput.closest("label"), document.querySelector("#refresh-button"), document.querySelector("#date-range-button")];
      return {
        controls: controls.map(control => ({ className: control.className, size: [control.getBoundingClientRect().width, control.getBoundingClientRect().height], tooltip: control.dataset.tooltip, svg: Boolean(control.querySelector("svg")) })),
        systemType: systemInput.type,
        systemLabel: systemInput.getAttribute("aria-label"),
        datePopup: controls[2].getAttribute("aria-haspopup"),
        dateControls: controls[2].getAttribute("aria-controls"),
      };
    })()`);
    assert.ok(secondaryToolbar.controls.every(control => control.className.includes("shared-icon-button") && control.svg && control.tooltip), "secondary toolbar actions must use shared graphical controls and tooltips");
    assert.deepEqual(secondaryToolbar.controls.map(control => control.size), [[30, 30], [30, 30], [30, 30]], "System schemas, Refresh, and Date ranges must use identical geometry");
    assert.equal(secondaryToolbar.systemType, "checkbox", "System schemas must retain native checkbox semantics");
    assert.equal(secondaryToolbar.systemLabel, "Show system schemas");
    assert.equal(secondaryToolbar.datePopup, "dialog");
    assert.equal(secondaryToolbar.dateControls, "slicer-dialog");
    const desktop = await evaluate(`(() => {
      const canvas = document.querySelector("#dashboard-canvas");
      const cards = [...canvas.querySelectorAll(".widget")];
      return {
        width: canvas.getBoundingClientRect().width,
        columns: getComputedStyle(canvas).gridTemplateColumns.split(" ").length,
        gap: getComputedStyle(canvas).gap,
        positions: cards.map(card => getComputedStyle(card).position),
        heights: cards.map(card => card.getBoundingClientRect().height),
        inlineGeometry: cards.map(card => [card.style.left, card.style.top, card.style.width, card.style.height]),
      };
    })()`);
    assert.ok(desktop.width > 900, "desktop canvas must use its available responsive width");
    assert.equal(desktop.columns, 3);
    assert.equal(desktop.gap, "12px");
    assert.deepEqual(desktop.positions, ["relative", "relative"]);
    assert.deepEqual(desktop.heights, [260, 260]);
    assert.deepEqual(desktop.inlineGeometry, [["", "", "", ""], ["", "", "", ""]], "version-3 cards must have no inline geometry");

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 800, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    assert.equal(await evaluate('getComputedStyle(document.querySelector("#dashboard-canvas")).gridTemplateColumns.split(" ").length'), 2, "601-900px dashboards must use two equal columns");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);

    await evaluate('document.querySelector("#edit-mode-button").click()');
    const childKeys = await evaluate(`(() => {
      const card = document.querySelector('[data-widget-id="widget_one"]');
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Native details";
      details.append(summary);
      const controls = [document.createElement("button"), document.createElement("a"), document.createElement("input"), document.createElement("strong"), summary, document.createElement("div")];
      controls[1].href = "#native-link";
      controls[3].tabIndex = 0;
      controls[5].tabIndex = 0;
      controls[5].setAttribute("role", "region");
      for (const control of controls) if (control !== summary) card.append(control);
      card.append(details);
      const before = [...document.querySelectorAll(".dashboard-canvas .widget")].map(item => item.dataset.widgetId);
      const prevented = controls.map(control => {
        control.focus();
        const event = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
        control.dispatchEvent(event);
        return event.defaultPrevented;
      });
      controls.forEach(control => control === summary ? details.remove() : control.remove());
      return { before, after: [...document.querySelectorAll(".dashboard-canvas .widget")].map(item => item.dataset.widgetId), prevented };
    })()`);
    assert.deepEqual(childKeys.after, childKeys.before, "child controls, links, inputs, marks, summaries, and scroll regions must not reorder their card");
    assert.deepEqual(childKeys.prevented, [false, false, false, false, false, false], "child arrow keys must retain native handling");
    const writesBeforeDragPreview = dashboardWrites;
    await evaluate(`new Promise(resolve => {
      const source = document.querySelector('[data-widget-id="widget_one"]');
      const rect = source.getBoundingClientRect();
      window.__widgetDragDataTransfer = new DataTransfer();
      source.querySelector("header").dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: window.__widgetDragDataTransfer, clientX: rect.left + 20, clientY: rect.top + 20 }));
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    })`);
    await evaluate(`(() => {
      const target = document.querySelector('[data-widget-id="widget_two"]');
      const rect = target.getBoundingClientRect();
      target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: window.__widgetDragDataTransfer, clientX: rect.right - 10, clientY: rect.top + rect.height / 2 }));
    })()`);
    await waitFor('document.querySelector("#layout-status").textContent.includes("position 2 of 2")', "drag insertion announcement");
    const dragPreview = await evaluate(`(() => {
      const placeholder = document.querySelector(".widget.order-dragging");
      return {
        order: [...document.querySelectorAll(".dashboard-canvas .widget")].map(card => card.dataset.widgetId),
        placeholderId: placeholder?.dataset.widgetId,
        label: placeholder?.dataset.dropLabel,
        status: document.querySelector("#layout-status").textContent,
      };
    })()`);
    assert.deepEqual(dragPreview.order, ["widget_two", "widget_one"], "surrounding cards must reflow around the live drop position before persistence");
    assert.equal(dragPreview.placeholderId, "widget_one");
    assert.equal(dragPreview.label, "Position 2 of 2");
    assert.match(dragPreview.status, /Drop .* at position 2 of 2/);
    assert.equal(dashboardWrites, writesBeforeDragPreview, "moving the drop placeholder must not persist before release");
    await evaluate(`(() => {
      const source = document.querySelector('[data-widget-id="widget_one"]');
      source.querySelector("header").dispatchEvent(new DragEvent("dragend", { bubbles: true, dataTransfer: window.__widgetDragDataTransfer }));
      delete window.__widgetDragDataTransfer;
    })()`);
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".dashboard-canvas .widget")].map(card => card.dataset.widgetId)'), ["widget_one", "widget_two"], "cancelling a drag must restore canonical array order");
    assert.equal(dashboardWrites, writesBeforeDragPreview, "cancelling a drag preview must not save");
    await evaluate(`new Promise(resolve => {
      const source = document.querySelector('[data-widget-id="widget_one"]');
      const rect = source.getBoundingClientRect();
      window.__widgetDragDataTransfer = new DataTransfer();
      source.querySelector("header").dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: window.__widgetDragDataTransfer, clientX: rect.left + 20, clientY: rect.top + 20 }));
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    })`);
    await evaluate(`(() => {
      const target = document.querySelector('[data-widget-id="widget_two"]');
      const rect = target.getBoundingClientRect();
      const options = { bubbles: true, cancelable: true, dataTransfer: window.__widgetDragDataTransfer, clientX: rect.right - 10, clientY: rect.top + rect.height / 2 };
      target.dispatchEvent(new DragEvent("dragover", options));
      target.dispatchEvent(new DragEvent("drop", options));
      delete window.__widgetDragDataTransfer;
    })()`);
    await waitFor('[...document.querySelectorAll(".dashboard-canvas .widget")].map(card => card.dataset.widgetId).join(",") === "widget_two,widget_one"', "previewed drag order change");
    await waitFor('document.querySelector("#save-status").textContent === "Saved"', "order save");
    assert.equal(dashboardWrites, 1, "one completed drag must produce one debounced save");
    assert.deepEqual(dashboard.dashboard.widgets.map(widget => widget.id), ["widget_two", "widget_one"]);
    assert.equal(dashboard.dashboard.widgets.some(widget => Object.hasOwn(widget, "layout")), false, "order persistence must not recreate widget geometry");

    conflictNext = true;
    await evaluate('document.querySelector(\'[data-widget-id="widget_one"] [data-action="move-widget-earlier"]\').click()');
    await waitFor('document.querySelector("#conflict-dialog").open', "conflict quarantine");
    const quarantine = await evaluate(`(() => ({
      status: document.querySelector("#save-status").textContent,
      editDisabled: document.querySelector("#edit-mode-button").disabled,
      dateDisabled: document.querySelector("#date-range-button").disabled,
      exportVisible: !document.querySelector("#conflict-export").hidden,
      localOrder: [...document.querySelectorAll(".dashboard-canvas .widget")].map(card => card.dataset.widgetId),
      statusState: document.querySelector("#save-status").dataset.state,
      statusError: document.querySelector("#save-status").classList.contains("error"),
    }))()`);
    assert.match(quarantine.status, /quarantined/);
    assert.equal(quarantine.editDisabled, true);
    assert.equal(quarantine.dateDisabled, true);
    assert.equal(quarantine.exportVisible, true);
    assert.deepEqual(quarantine.localOrder, ["widget_one", "widget_two"], "quarantine must preserve the unsaved local order");
    assert.equal(quarantine.statusState, "error");
    assert.equal(quarantine.statusError, true, "shared status handling must expose the error state consistently");
    const conflictDownload = await evaluate(`(() => {
      window.__browserDownloads = [];
      URL.createObjectURL = blob => { window.__browserDownloads.push({ type: blob.type }); return "blob:browser-conflict"; };
      URL.revokeObjectURL = url => { window.__browserDownloads.at(-1).revoked = url; };
      HTMLAnchorElement.prototype.click = function () { window.__browserDownloads.at(-1).filename = this.download; };
      document.querySelector("#conflict-export").click();
      return window.__browserDownloads[0];
    })()`);
    assert.equal(conflictDownload.type, "application/json");
    assert.match(conflictDownload.filename, /^schemer-local-edits-dashboard_browser-/);
    assert.equal(conflictDownload.revoked, "blob:browser-conflict");
    await evaluate('document.querySelector("#conflict-refresh").click()');
    await waitFor('!document.querySelector("#conflict-dialog").open', "server-authoritative conflict refresh");
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".dashboard-canvas .widget")].map(card => card.dataset.widgetId)'), ["widget_two", "widget_one"], "explicit refresh must restore server array order");

    await evaluate('document.querySelector(\'[data-widget-id="widget_one"]\').click()');
    await waitFor('!document.querySelector("#widget-focus").hidden', "focused widget");
    const focusWidths = await evaluate('({ pane: document.querySelector(".widget-focus-main").getBoundingClientRect().width, overlay: document.querySelector("#widget-focus").getBoundingClientRect().width, viewport: window.innerWidth })');
    assert.ok(focusWidths.pane > focusWidths.overlay * .9, `the removed inspector must not reserve half of the focused workspace: ${JSON.stringify(focusWidths)}`);
    await evaluate('document.querySelector(".focused-widget-close").click()');

    await evaluate('document.querySelector("#date-range-button").click(); document.querySelector("#add-slicer").click()');
    await waitFor('document.querySelector("#slicer-dialog").open && document.querySelector(".query-calendar-toggle")', "slicer date editor");
    await evaluate('document.querySelector(".query-calendar-toggle").click()');
    const calendar = await evaluate(`(() => {
      const grid = document.querySelector('.query-calendar-grid[role="grid"]');
      const before = document.activeElement.dataset.calendarDate;
      document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      return new Promise(resolve => requestAnimationFrame(() => resolve({ before, after: document.activeElement.dataset.calendarDate, cells: grid.querySelectorAll('[role="gridcell"]').length, rows: grid.querySelectorAll('[role="row"]').length, statusState: document.querySelector("#slicer-status").dataset.state })));
    })()`);
    assert.notEqual(calendar.before, calendar.after, "calendar arrow keys must move the roving grid focus");
    assert.equal(calendar.cells, 42);
    assert.equal(calendar.rows, 7);
    assert.equal(calendar.statusState, "", "slicer status must retain its app-owned neutral state through the shared wrapper");
    await evaluate('document.querySelector("#cancel-slicers").click()');

    const orderedDashboard = clone(dashboard);
    detailFixture = true;
    dashboard = sourcedDashboard();
    await navigate(`http://127.0.0.1:${port}/`);
    await waitFor('document.querySelectorAll(".dashboard-canvas .live-bar-mark").length === 2', "verified aggregate chart marks");
    await evaluate('document.querySelector("#onboarding-dialog")?.open && document.querySelector("#onboarding-dialog").close(); document.querySelectorAll(".dashboard-canvas .live-bar-mark")[0].click()');
    await waitFor('document.querySelector("#detail-drawer").classList.contains("open") && document.querySelector("#detail-report-body").textContent.includes("selection 1")', "first retained detail selection");
    const firstResource = [...activeDetailResources][0];
    await evaluate('document.querySelector("#expand-detail-report").click(); document.querySelectorAll("#widget-focus-content .live-bar-mark")[1].click()');
    await waitForNode(() => detailEvents.filter(event => event.startsWith("post:")).length >= 2, "second detail request");
    await waitFor('document.querySelector("#detail-report-body").textContent.includes("selection 2")', "second retained detail selection");
    assert.ok(detailEvents.indexOf(`delete:${firstResource}`) < detailEvents.findIndex(event => event.startsWith("post:detail_resource_2")), `the first snapshot must close before the second selection: ${detailEvents.join(" | ")}`);
    assert.equal(maximumActiveDetailResources, 1, "repeated detail selections must not exceed retained snapshot capacity");

    const busyResource = [...activeDetailResources][0];
    busyReleaseOnce.add(busyResource);
    const postsBeforeBusyRelease = detailEvents.filter(event => event.startsWith("post:")).length;
    await evaluate('document.querySelector("#expand-detail-report").click(); document.querySelectorAll("#widget-focus-content .live-bar-mark")[0].click()');
    await waitFor('!document.querySelector("#detail-retry").hidden && document.querySelector("#detail-report-body").textContent.includes("could not be released")', "explicit failed detail cleanup state");
    assert.equal(detailEvents.filter(event => event.startsWith("post:")).length, postsBeforeBusyRelease, "failed cleanup must not allocate another snapshot");
    await evaluate('document.querySelector("#detail-retry").click()');
    await waitFor('document.querySelector("#detail-report-body").textContent.includes("selection 3")', "detail selection after explicit cleanup retry");
    assert.equal(detailEvents.filter(event => event === `delete:${busyResource}`).length, 2, "result_busy cleanup must retry after the prior operation settles");
    assert.equal(maximumActiveDetailResources, 1, "busy cleanup retry must still release before allocating another snapshot");

    detailCapacity = 2;
    maximumActiveDetailResources = activeDetailResources.size;
    await evaluate(`(() => {
      document.querySelector("#detail-report-body .detail-column-search-toggle").click();
      const input = document.querySelector("#detail-report-body .detail-column-search");
      input.value = "slow";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    })()`);
    await waitForNode(() => slowSearchRequests === 1, "first delayed detail search");
    await evaluate('document.querySelector("#detail-report-body .detail-column-search").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }))');
    await waitForNode(() => slowSearchRequests === 2, "replacement detail search");
    await waitFor('document.querySelector("#detail-report-body").textContent.includes("search 2") && document.querySelector("#detail-retry").hidden', "non-aborted replacement search state");
    assert.equal(slowSearchRequests, 2, "Enter must dispatch a replacement instead of reusing the aborted promise");
    await waitForNode(() => detailEvents.includes("delete:detail_resource_4"), "superseded delayed detail response cleanup");
    assert.ok(detailEvents.indexOf("delete:detail_resource_4") > detailEvents.indexOf("post:detail_resource_5:slow"), `the superseded response must be observed and released after its replacement dispatch: ${detailEvents.join(" | ")}`);
    await evaluate('document.querySelector("#close-detail-report").click(); document.querySelector(".focused-widget-close").click()');
    await waitForNode(() => activeDetailResources.size === 0, "final detail snapshot release");

    aggregateDelayMs = 50;
    aggregateRequests = 0;
    activeAggregateRequests = 0;
    maximumActiveAggregateRequests = 0;
    dashboard = sourcedDashboardWithWidgets(100);
    await navigate(`http://127.0.0.1:${port}/`);
    await waitForNode(() => maximumActiveAggregateRequests === 3 && aggregateRequests < 100, "bounded aggregate scheduler saturation");
    assert.equal(await evaluate('[...document.querySelectorAll(".query-result-status")].some(item => item.textContent.includes("Queued for bounded target execution"))'), true, "queued widgets must expose target-capacity waiting state");
    await waitForNode(() => aggregateRequests === 100 && activeAggregateRequests === 0, "one hundred scheduled aggregate completions", 12000);
    await waitFor('document.querySelectorAll(".dashboard-canvas .live-bar-mark").length === 200', "one hundred rendered aggregate widgets", 12000);
    assert.equal(maximumActiveAggregateRequests, 3, "one target must retain one default admission slot for interactive detail work");
    aggregateDelayMs = 0;

    detailFixture = false;
    dashboard = legacyDashboard();
    legacyPreviewFailureOnce = true;
    legacyApplyFailureOnce = true;
    const writesBeforeLegacyReview = dashboardWrites;
    await navigate(`http://127.0.0.1:${port}/`);
    await waitFor('!document.querySelector("#review-legacy-sources").hidden', "legacy source review action");
    await evaluate('document.querySelector("#onboarding-dialog")?.open && document.querySelector("#onboarding-dialog").close(); document.querySelector("#review-legacy-sources").click()');
    await waitFor('document.querySelector("#legacy-source-dialog").open && !document.querySelector("#retry-legacy-sources").hidden && document.querySelector("#legacy-source-status").dataset.state === "error"', "failed legacy preview recovery action");
    assert.equal(legacyPreviewRequests, 1);
    await evaluate('document.querySelector("#retry-legacy-sources").click()');
    await waitFor('document.querySelector("#legacy-source-dialog").open && document.querySelector(".legacy-source-result.compatible")', "compatible legacy source review");
    assert.equal(legacyPreviewRequests, 2);
    assert.equal(dashboardWrites, writesBeforeLegacyReview, "legacy preview must not write the dashboard");
    assert.equal(dashboard.revision, 40);
    await evaluate('document.querySelector("#legacy-source-confirm").click(); document.querySelector("#apply-legacy-sources").click()');
    await waitFor('document.querySelector("#legacy-source-dialog").open && !document.querySelector("#retry-legacy-sources").hidden && document.querySelector("#legacy-source-confirm").disabled', "failed legacy apply requires a fresh review");
    assert.equal(legacyApplyRequests, 1);
    assert.equal(dashboard.revision, 40);
    await evaluate('document.querySelector("#retry-legacy-sources").click()');
    await waitFor('document.querySelector("#legacy-source-dialog").open && document.querySelector(".legacy-source-result.compatible") && document.querySelector("#retry-legacy-sources").hidden', "refreshed legacy source review");
    assert.equal(legacyPreviewRequests, 3);
    await evaluate('document.querySelector("#legacy-source-confirm").click(); document.querySelector("#apply-legacy-sources").click()');
    await waitFor('!document.querySelector("#legacy-source-dialog").open && document.querySelector("#review-legacy-sources").hidden', "applied legacy source upgrade");
    assert.equal(legacyApplyRequests, 2);
    assert.equal(dashboardWrites, writesBeforeLegacyReview, "legacy apply must use its atomic server route rather than a raw browser save");
    assert.equal(dashboard.revision, 41);
    assert.equal(dashboard.dashboard.widgets[0].configuration.source.snapshotVersion, 2);

    legacyBatchFixture = true;
    dashboard = legacyBatchDashboard();
    const previewsBeforeDeferredReview = legacyPreviewRequests;
    const appliesBeforeDeferredReview = legacyApplyRequests;
    await navigate(`http://127.0.0.1:${port}/`);
    await waitFor('!document.querySelector("#review-legacy-sources").hidden', "deferred legacy source review action");
    await evaluate('document.querySelector("#onboarding-dialog")?.open && document.querySelector("#onboarding-dialog").close(); document.querySelector("#review-legacy-sources").click()');
    await waitFor('document.querySelectorAll("#legacy-source-results .legacy-source-result.incompatible").length === 4 && !document.querySelector("#retry-legacy-sources").hidden && document.querySelector("#apply-legacy-sources").disabled', "all-incompatible first bounded batch");
    assert.equal(dashboard.revision, 50, "an incompatible batch must not write a revision");
    assert.equal(legacyApplyRequests, appliesBeforeDeferredReview, "an incompatible batch must never auto-apply");
    await evaluate('document.querySelector("#retry-legacy-sources").click()');
    await waitFor('document.querySelectorAll("#legacy-source-results .legacy-source-result.compatible").length === 1 && document.querySelector("#legacy-source-results").textContent.includes("Legacy batch 5")', "compatible deferred batch");
    assert.equal(legacyPreviewRequests, previewsBeforeDeferredReview + 2, "continuation must preview the server-deferred IDs exactly once");
    assert.equal(dashboard.revision, 50, "previewing a deferred batch must remain read-only");
    await evaluate('document.querySelector("#legacy-source-confirm").click(); document.querySelector("#apply-legacy-sources").click()');
    await waitFor('document.querySelector("#legacy-source-dialog").open && document.querySelector("#legacy-source-status").textContent.includes("changed subsequently") && document.querySelector(".widget[data-widget-id=widget_legacy_batch_4]").dataset.sourceState === "error"', "subsequent post-upgrade source change warning");
    assert.equal(legacyApplyRequests, appliesBeforeDeferredReview + 1, "the deferred compatible batch requires exactly one confirmed apply");
    assert.equal(dashboard.revision, 51, "one confirmed compatible batch must increment the revision once");
    assert.equal(dashboard.dashboard.widgets[4].configuration.source.snapshotVersion, 2);
    assert.equal(await evaluate('document.querySelector("#legacy-source-confirm").disabled && document.querySelector("#apply-legacy-sources").disabled'), true, "a post-write change must not trigger rewrite or replay controls");
    await evaluate('document.querySelector("#close-legacy-sources").click()');
    legacyBatchFixture = false;

    dashboard = orderedDashboard;
    activeDetailResources.clear();
    await navigate(`http://127.0.0.1:${port}/`);
    await waitFor('document.querySelectorAll(".dashboard-canvas .widget").length === 2', "restored order fixture");
    await evaluate('document.querySelector("#onboarding-dialog")?.open && document.querySelector("#onboarding-dialog").close()');

    await evaluate(`(() => {
      const launcher = document.createElement("button");
      launcher.id = "browser-console-launcher";
      document.body.append(launcher);
      let resolveExecution;
      const calls = [];
      const execution = new Promise(resolve => { resolveExecution = resolve; });
      const client = { request: (url, options = {}) => {
        calls.push({ url, method: options.method || "GET" });
        if (url === "/api/postgres/console/settings") return Promise.resolve({ revision: 1, defaultMode: "managed_read", writeIntent: "disabled", statementLimit: 10, rowPageSize: 25 });
        if (url.endsWith("/console/executions") && options.method === "POST") return execution;
        if (url.includes("/console/executions/") && options.method === "DELETE") return Promise.resolve({ cancellationRequested: true });
        return Promise.resolve({});
      } };
      const adapter = window.SchemiiShared.createPostgresConsole({
        button: launcher, postgresClient: client,
        getTarget: () => ({ profileId: "profile_browser", profile: "Browser profile", database: "browser", namespace: "public", profileFingerprint: "a".repeat(64) }),
      });
      window.__browserConsole = { launcher, adapter, calls, resolveExecution };
      launcher.click();
    })()`);
    await waitFor('document.querySelector(".shared-postgres-console[open] [data-console-settings-status]")?.textContent.includes("Revision 1")', "shared Console settings");
    await evaluate('document.querySelector(".shared-postgres-console[open] [data-console-sql]").value = "SELECT 1"; document.querySelector(".shared-postgres-console[open] [data-console-run]").click()');
    await waitFor('!document.querySelector(".shared-postgres-console[open] [data-console-stop]").hidden && document.querySelector(".shared-postgres-console[open] [data-console-sql]").readOnly', "one running Console execution");
    await evaluate('document.querySelector(".shared-postgres-console[open] [data-console-stop]").click()');
    await waitFor('window.__browserConsole.calls.some(call => call.method === "DELETE")', "server-side Console cancellation request");
    assert.match(await evaluate('document.querySelector(".shared-postgres-console[open] [data-console-status]").textContent'), /Cancellation requested/);
    await evaluate('window.__browserConsole.resolveExecution({ outcome: "cancelled", committed: false, statements: [] })');
    await waitFor('!document.querySelector(".shared-postgres-console[open] [data-console-run]").disabled && document.querySelector(".shared-postgres-console[open] [data-console-stop]").hidden', "terminal Console cancellation");
    assert.match(await evaluate('document.querySelector(".shared-postgres-console[open] [data-console-status]").textContent'), /cancelled by PostgreSQL/);
    await evaluate('document.querySelector(".shared-postgres-console[open] [data-console-close]").click()');

    await evaluate(`(() => {
      const launcher = document.createElement("button");
      launcher.id = "browser-console-admission-launcher";
      document.body.append(launcher);
      let executionCount = 0;
      let resolveCleanup;
      let resolveWrite;
      const cleanup = new Promise(resolve => { resolveCleanup = resolve; });
      const write = new Promise(resolve => { resolveWrite = resolve; });
      const calls = [];
      const client = { request: (url, options = {}) => {
        calls.push({ url, method: options.method || "GET" });
        if (url === "/api/postgres/console/settings") return Promise.resolve({ revision: 1, defaultMode: "managed_read", writeIntent: "enabled", statementLimit: 10, rowPageSize: 25 });
        if (url.includes("/results/") && options.method === "DELETE") return cleanup;
        if (url.endsWith("/console/executions") && options.method === "POST") {
          executionCount += 1;
          const body = JSON.parse(options.body);
          if (executionCount === 1) return Promise.resolve({ outcome: "transaction_open", committed: false, statements: [{ executionId: body.executionId, resultId: "retained_admission_result", statementIndex: 0, resultIndex: 0, columns: [{ name: "value" }], rows: [[1]], hasMore: true, nextCursor: "next", snapshotRetention: "managed_read_transaction", truncationEvents: [] }] });
          return write;
        }
        if (url.includes("/console/executions/") && options.method === "DELETE") return Promise.resolve({ cancellationRequested: true });
        return Promise.resolve({});
      } };
      window.SchemiiShared.createPostgresConsole({
        button: launcher, postgresClient: client,
        getTarget: () => ({ profileId: "profile_browser", profile: "Browser profile", database: "browser", namespace: "public", profileFingerprint: "a".repeat(64) }),
      });
      window.__admissionConsole = { launcher, calls, get executionCount() { return executionCount; }, resolveCleanup, resolveWrite };
      launcher.click();
    })()`);
    await waitFor('document.querySelector("#postgres-console-" + "missing") === null && [...document.querySelectorAll(".shared-postgres-console[open]")].some(dialog => dialog.querySelector("[data-console-settings-status]")?.textContent.includes("Revision 1"))', "pre-admission Console settings");
    await evaluate(`(() => {
      const dialog = [...document.querySelectorAll(".shared-postgres-console[open]")].at(-1);
      dialog.querySelector("[data-console-sql]").value = "SELECT retained";
      dialog.querySelector("[data-console-run]").click();
    })()`);
    await waitFor('[...document.querySelectorAll(".shared-postgres-console[open]")].at(-1).querySelector("[data-console-close-result]")', "retained Console result");
    await evaluate(`(() => {
      const dialog = [...document.querySelectorAll(".shared-postgres-console[open]")].at(-1);
      dialog.querySelector("[data-console-mode]").value = "managed";
      dialog.querySelector("[data-console-mode]").dispatchEvent(new Event("change", { bubbles: true }));
      dialog.querySelector("[data-console-sql]").value = "UPDATE must_wait SET value = 1";
      dialog.querySelector("[data-console-run]").click();
    })()`);
    await waitFor('window.__admissionConsole.calls.some(call => call.url.includes("/results/") && call.method === "DELETE")', "preparatory retained-result cleanup");
    const preparatory = await evaluate(`(() => {
      const dialog = [...document.querySelectorAll(".shared-postgres-console[open]")].at(-1);
      const stop = dialog.querySelector("[data-console-stop]");
      stop.click();
      return { stopHidden: stop.hidden, executionCount: window.__admissionConsole.executionCount, cancellationCalls: window.__admissionConsole.calls.filter(call => call.method === "DELETE" && !call.url.includes("/results/")).length };
    })()`);
    assert.deepEqual(preparatory, { stopHidden: true, executionCount: 1, cancellationCalls: 0 }, "preparatory busy state must expose no cancellable execution or dispatch the pending write");
    await evaluate('window.__admissionConsole.resolveCleanup({ closed: true })');
    await waitFor('window.__admissionConsole.executionCount === 2 && ![...document.querySelectorAll(".shared-postgres-console[open]")].at(-1).querySelector("[data-console-stop]").hidden', "write execution dispatch");
    await evaluate(`(() => {
      const dialog = [...document.querySelectorAll(".shared-postgres-console[open]")].at(-1);
      dialog.querySelector("[data-console-stop]").click();
    })()`);
    await waitFor('window.__admissionConsole.calls.filter(call => call.method === "DELETE" && !call.url.includes("/results/")).length === 1', "post-dispatch write cancellation");
    await evaluate('window.__admissionConsole.resolveWrite({ outcome: "cancelled", committed: false, statements: [] })');
    await waitFor('![...document.querySelectorAll(".shared-postgres-console[open]")].at(-1).querySelector("[data-console-run]").disabled', "terminal pre-admission Console POST observation");
    await evaluate('[...document.querySelectorAll(".shared-postgres-console[open]")].at(-1).querySelector("[data-console-close]").click()');

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
    const reloaded = cdp.once("Page.loadEventFired");
    await cdp.send("Page.reload", { ignoreCache: true }, sessionId);
    await reloaded;
    await waitFor('document.querySelectorAll(".dashboard-canvas .widget").length === 2', "mobile widgets");
    await evaluate('document.querySelector("#onboarding-dialog")?.open && document.querySelector("#onboarding-dialog").close()');
    const mobile = await evaluate(`(() => {
      const canvas = document.querySelector("#dashboard-canvas");
      const cards = [...canvas.querySelectorAll(".widget")];
      return {
        width: canvas.getBoundingClientRect().width,
        columns: getComputedStyle(canvas).gridTemplateColumns.split(" ").length,
        positions: cards.map(card => getComputedStyle(card).position),
        heights: cards.map(card => card.getBoundingClientRect().height),
        tops: cards.map(card => card.getBoundingClientRect().top),
        ids: cards.map(card => card.dataset.widgetId),
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      };
    })()`);
    assert.ok(mobile.width <= 390);
    assert.equal(mobile.columns, 1);
    assert.deepEqual(mobile.positions, ["relative", "relative"]);
    assert.deepEqual(mobile.heights, [260, 260]);
    assert.ok(mobile.tops[0] < mobile.tops[1], "mobile cards must follow array order");
    assert.deepEqual(mobile.ids, ["widget_two", "widget_one"], "every breakpoint must use the same saved array order");
    assert.equal(mobile.overflow, false, "390px layout must not overflow the document");
    const mobileTopbar = await evaluate(`(() => {
      const topbar = document.querySelector(".topbar");
      const actions = document.querySelector(".top-actions");
      const create = document.querySelector("#mobile-new-dashboard");
      const bounds = actions.getBoundingClientRect();
      return {
        overflow: topbar.scrollWidth > topbar.clientWidth,
        actionsInsideViewport: bounds.left >= 0 && bounds.right <= innerWidth,
        createClass: create.className,
        createLabel: create.getAttribute("aria-label"),
        createSize: [create.getBoundingClientRect().width, create.getBoundingClientRect().height],
        createSvg: Boolean(create.querySelector("svg")),
      };
    })()`);
    assert.equal(mobileTopbar.overflow, false, "the narrow top bar must contain its standard action group");
    assert.equal(mobileTopbar.actionsInsideViewport, true, "the narrow top-bar actions must remain reachable");
    assert.match(mobileTopbar.createClass, /shared-icon-button.*top-action-icon/);
    assert.equal(mobileTopbar.createLabel, "Create dashboard");
    assert.deepEqual(mobileTopbar.createSize, [31, 31]);
    assert.equal(mobileTopbar.createSvg, true);
    const mobileSecondaryToolbar = await evaluate(`(() => {
      const toolbar = document.querySelector(".toolbar-actions");
      const controls = [document.querySelector("#system-namespaces").closest("label"), document.querySelector("#refresh-button"), document.querySelector("#date-range-button")];
      return {
        insideViewport: toolbar.getBoundingClientRect().right <= innerWidth,
        sizes: controls.map(control => [control.getBoundingClientRect().width, control.getBoundingClientRect().height]),
        tops: controls.map(control => control.getBoundingClientRect().top),
      };
    })()`);
    assert.equal(mobileSecondaryToolbar.insideViewport, true, "the mobile secondary toolbar must remain contained");
    assert.deepEqual(mobileSecondaryToolbar.sizes, [[30, 30], [30, 30], [30, 30]]);
    assert.equal(new Set(mobileSecondaryToolbar.tops).size, 1, "all mobile secondary actions must stay aligned on one row");

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] }, sessionId);
    await navigate(`http://127.0.0.1:${port}/schemii/`);
    await waitFor('!document.body.classList.contains("app-hydrating") && document.querySelector("#save-status").textContent === "Saved to file"', "Schemii schema fixture");
    const writesBeforeSchemiiOnboarding = schemiiWrites;
    const postgresBeforeSchemiiOnboarding = schemiiPostgresRequests;
    await walkOnboarding("Schemii", "Play demo");
    assert.equal(schemiiWrites, writesBeforeSchemiiOnboarding, "Schemii tutorial pages must not mutate a saved design");
    assert.equal(schemiiPostgresRequests, postgresBeforeSchemiiOnboarding, "Schemii tutorial pages must not query PostgreSQL");
    await evaluate('document.querySelector("#app-menu").open = true; document.querySelector("#show-onboarding-button").click()');
    await waitFor('document.querySelector("#onboarding-dialog").open && document.querySelector("#onboarding-step-label").textContent === "1 of 7"', "Schemii app-menu introduction replay");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
    await walkOnboarding("Schemii mobile", "Play demo");
    assert.equal(schemiiWrites, writesBeforeSchemiiOnboarding, "replayed Schemii tutorial pages must remain non-mutating");
    assert.equal(schemiiPostgresRequests, postgresBeforeSchemiiOnboarding, "replayed Schemii tutorial pages must remain synthetic");

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    schemiiConflictNext = true;
    await evaluate(`(() => {
      const input = document.querySelector("#project-name");
      input.value = "Quarantined local design";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    })()`);
    await waitFor('!document.querySelector("#schema-conflict-banner").hidden', "Schemii save conflict quarantine");
    const writesAfterConflict = schemiiWrites;
    assert.equal(await evaluate('document.querySelector("#project-name").value'), "Quarantined local design", "the local draft must remain visible while quarantined");
    schemiiSchemaGetDelayMs = 250;
    await evaluate('window.confirm = () => true; document.querySelector("#refresh-conflicted-schema").click()');
    await new Promise(resolve => setTimeout(resolve, 75));
    assert.equal(await evaluate('document.querySelector("#schema-conflict-banner").hidden'), false, "the quarantine banner must remain until the authoritative record is active");
    assert.equal(schemiiWrites, writesAfterConflict, "authoritative conflict refresh must not issue a replacement PUT");
    await waitFor('document.querySelector("#schema-conflict-banner").hidden && document.querySelector("#project-name").value === "Authoritative browser design"', "Schemii authoritative conflict activation");
    assert.equal(await evaluate('document.querySelector("#save-status").textContent'), "Saved to file");
    await evaluate(`(() => {
      const input = document.querySelector("#project-name");
      input.value = "Post-recovery design";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    })()`);
    await waitForNode(() => schemiiWrites === writesAfterConflict + 1, "Schemii post-recovery PUT");
    await waitFor('document.querySelector("#save-status").textContent === "Saved to file"', "Schemii post-recovery save status");
    const postRecoveryWrite = schemiiWriteRequests.at(-1);
    assert.equal(postRecoveryWrite.body.revision, 2, "the next save must use the refreshed authoritative revision");
    assert.equal(postRecoveryWrite.layoutToken, "2".repeat(64), "the next save must use the refreshed authoritative layout token");
    assert.deepEqual(exceptions, [], `browser exceptions: ${exceptions.join(" | ")}`);
    console.log("Schemii and Schemer Chromium onboarding and desktop/mobile contracts passed");
  } finally {
    browser.kill("SIGTERM");
    await new Promise(resolve => browser.once("exit", resolve)).catch(() => {});
    server.close();
    fs.rmSync(userData, { recursive: true, force: true });
  }
}

main().catch(error => {
  server.close();
  console.error(error);
  process.exitCode = 1;
});
