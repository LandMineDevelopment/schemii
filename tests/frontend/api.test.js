import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, requestJson } from "../../src/schemii/schemii/web/assets/api.js";

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
