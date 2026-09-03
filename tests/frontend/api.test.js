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
