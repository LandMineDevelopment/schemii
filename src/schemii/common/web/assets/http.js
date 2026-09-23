import { loginUrl } from './login-return.js';
export const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(message, { status = 0, code = "request_failed", requestId = null, retryable = false, details = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryable = retryable;
    this.details = details;
  }
}

export async function requestJson(path, {
  method = "GET",
  body,
  signal = null,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  fetcher = globalThis.fetch,
} = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort(signal?.reason);
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timeout = Number.isFinite(timeoutMs) && timeoutMs > 0
    ? globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs)
    : null;

  try {
    const response = await fetcher(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status === 204) return null;
    let document = null;
    try {
      document = await response.json();
    } catch {
      if (response.ok) {
        throw new ApiError("The server returned an unreadable response", {
          status: response.status,
          code: "invalid_response",
          requestId: response.headers.get("x-request-id"),
        });
      }
    }

    if (!response.ok) {
      if (response.status === 401 && !path.startsWith('/api/v1/auth/') && globalThis.location?.pathname !== '/login') {
        globalThis.location?.replace(loginUrl());
      }
      const envelope = document?.error;
      throw new ApiError(envelope?.message || "The request could not be completed", {
        status: response.status,
        code: envelope?.code || "request_failed",
        requestId: envelope?.requestId || response.headers.get("x-request-id"),
        retryable: Boolean(envelope?.retryable),
        details: envelope?.details && typeof envelope.details === "object" ? envelope.details : {},
      });
    }
    return document;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) {
      throw new ApiError(timedOut ? "The active server took too long to respond" : "The request was cancelled", {
        code: timedOut ? "request_timeout" : "request_cancelled",
        details: { cause: error instanceof Error ? error.name : "AbortError" },
        retryable: timedOut,
      });
    }
    throw new ApiError("The active server could not be reached", {
      code: "network_error",
      details: { cause: error instanceof Error ? error.name : "NetworkError" },
      retryable: true,
    });
  } finally {
    if (timeout !== null) globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
  }
}
