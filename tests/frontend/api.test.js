import assert from "node:assert/strict";
import test from "node:test";

import { api, ApiError, requestJson } from "../../src/schemii/schemii/web/assets/api.js";

test("requestJson forwards caller cancellation to fetch", async () => {
  const caller = new AbortController();
  let receivedSignal;
  const request = requestJson("/example", {
    signal: caller.signal,
    timeoutMs: 0,
    fetcher: (_path, options) => {
      receivedSignal = options.signal;
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    },
  });
  caller.abort();

  await assert.rejects(request, error => (
    error instanceof ApiError
    && error.code === "request_cancelled"
    && error.retryable === false
  ));
  assert.equal(receivedSignal.aborted, true);
});

test("requestJson applies one standard request deadline", async () => {
  const request = requestJson("/example", {
    timeoutMs: 5,
    fetcher: (_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }),
  });

  await assert.rejects(request, error => (
    error instanceof ApiError
    && error.code === "request_timeout"
    && error.retryable === true
  ));
});

test("requestJson retains the server error envelope", async () => {
  await assert.rejects(requestJson("/example", {
    timeoutMs: 0,
    fetcher: async () => ({
      ok: false,
      status: 409,
      headers: new Headers({ "x-request-id": "header-request" }),
      json: async () => ({
        error: {
          code: "design_conflict",
          message: "The design changed",
          requestId: "body-request",
          retryable: true,
          details: { expected: 2 },
        },
      }),
    }),
  }), error => (
    error instanceof ApiError
    && error.status === 409
    && error.code === "design_conflict"
    && error.requestId === "body-request"
    && error.details.expected === 2
  ));
});

test("migration API methods retain the reviewed server contract", async () => {
  const requests = [];
  const fetcher = async (path, options) => {
    requests.push({ path, method: options.method, body: options.body });
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => path.includes("migration-executions?") ? { executions: [] } : { id: "result" },
    };
  };
  const body = {
    expectedWorkspaceRevision: 3,
    expectedDesignRevision: 7,
    expectedCatalogFingerprint: null,
    allowDestructive: false,
  };

  await api.createMigrationPlan("ws_demo", body, { fetcher, timeoutMs: 0 });
  await api.listMigrationExecutions("ws_demo", { limit: 25, fetcher, timeoutMs: 0 });

  assert.deepEqual(requests, [
    {
      path: "/api/v1/schemii/workspaces/ws_demo/migration-plans",
      method: "POST",
      body: JSON.stringify(body),
    },
    {
      path: "/api/v1/schemii/workspaces/ws_demo/migration-executions?limit=25",
      method: "GET",
      body: undefined,
    },
  ]);
});

test("console API methods bind executions and result cursors to a workspace", async () => {
  const requests = [];
  const fetcher = async (path, options) => {
    requests.push({ path, method: options.method, body: options.body });
    return {
      ok: options.method === "DELETE" && path.includes("/results/") ? true : true,
      status: options.method === "DELETE" && path.includes("/results/") ? 204 : 200,
      headers: new Headers(),
      json: async () => ({ id: "result" }),
    };
  };
  const options = { fetcher, timeoutMs: 0 };

  await api.getConsoleSettings(options);
  await api.createConsoleExecution("ws_demo", { statements: ["SELECT 1"] }, options);
  await api.getConsoleExecution("ws_demo", "cex_demo", options);
  await api.cancelConsoleExecution("ws_demo", "cex_demo", options);
  await api.getConsoleResultPage("ws_demo", "cex_demo", "res_demo", { ...options, cursor: "crc_demo" });
  await api.closeConsoleResult("ws_demo", "cex_demo", "res_demo", options);

  assert.deepEqual(requests.map(request => [request.method, request.path]), [
    ["GET", "/api/v1/schemii/console/settings"],
    ["POST", "/api/v1/schemii/workspaces/ws_demo/console/executions"],
    ["GET", "/api/v1/schemii/workspaces/ws_demo/console/executions/cex_demo"],
    ["DELETE", "/api/v1/schemii/workspaces/ws_demo/console/executions/cex_demo"],
    ["GET", "/api/v1/schemii/workspaces/ws_demo/console/executions/cex_demo/results/res_demo?cursor=crc_demo"],
    ["DELETE", "/api/v1/schemii/workspaces/ws_demo/console/executions/cex_demo/results/res_demo"],
  ]);
});

test("relation browser API binds discovery, lineage, and rows to a workspace", async () => {
  const requests = [];
  const fetcher = async (path, options) => {
    requests.push([path, options.method || "GET"]);
    return new Response(JSON.stringify({ relations: [], rows: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  await api.listRelations("ws_1", { search: "orders", pageSize: 25, fetcher });
  await api.getRelationLineage("ws_1", "rel_1", { fetcher });
  await api.getRelationRows("ws_1", "rel_1", { cursor: "next", pageSize: 20, fetcher });
  assert.deepEqual(requests, [
    ["/api/v1/schemii/workspaces/ws_1/relations?pageSize=25&search=orders", "GET"],
    ["/api/v1/schemii/workspaces/ws_1/relations/rel_1/lineage", "GET"],
    ["/api/v1/schemii/workspaces/ws_1/relations/rel_1/rows?pageSize=20&cursor=next", "GET"],
  ]);
});
