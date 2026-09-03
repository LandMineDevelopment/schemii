const API_ROOT = "/api/v1";
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

function publicConnection(value) {
  return {
    id: value.id,
    revision: value.revision,
    name: value.name,
    host: value.host,
    port: value.port,
    database: value.database,
    username: value.username,
    sslMode: value.sslMode,
    connectTimeout: value.connectTimeout,
    credentialStored: value.credentialStored,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
  };
}

export const api = Object.freeze({
  session: options => requestJson(`${API_ROOT}/session`, options),
  readiness: options => requestJson(`${API_ROOT}/readiness`, options),
  async listConnections(options) {
    const response = await requestJson(`${API_ROOT}/connections`, options);
    return response.connections.map(publicConnection);
  },
  async getConnection(id, options) {
    return publicConnection(await requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}`, options));
  },
  async createConnection(body, options = {}) {
    return publicConnection(await requestJson(`${API_ROOT}/connections`, { ...options, method: "POST", body }));
  },
  async updateConnection(id, body, options = {}) {
    return publicConnection(await requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}`, { ...options, method: "PATCH", body }));
  },
  testConnection: (id, options = {}) => requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}/test`, { ...options, method: "POST" }),
  deleteConnection: (id, expectedRevision, options = {}) => requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { ...options, method: "DELETE" }),
  async listWorkspaces(options) {
    const response = await requestJson(`${API_ROOT}/schemii/workspaces`, options);
    return response.workspaces;
  },
  createWorkspace: (body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces`, { ...options, method: "POST", body }),
  createWorkspaceImport: (body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/imports`, { ...options, method: "POST", body }),
  getWorkspace: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}`, options),
  updateLayout: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/layout`, { ...options, method: "PUT", body }),
  deleteWorkspace: (id, expectedRevision, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { ...options, method: "DELETE" }),
  getCatalog: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/catalog`, options),
  getDesign: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design`, options),
  getDesignSnapshot: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/snapshot`, options),
  getDesignHistory: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/history`, options),
  undoDesign: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/undo`, { ...options, method: "POST", body }),
  redoDesign: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/redo`, { ...options, method: "POST", body }),
  previewDesignBaselineReset: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/baseline-reset`, options),
  resetDesignToBaseline: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/baseline-reset`, { ...options, method: "POST", body }),
  getDesignDeletionImpact: (id, objectId, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/deletion-impact/${encodeURIComponent(objectId)}`, options),
  replaceDesign: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design`, { ...options, method: "PUT", body }),
  analyzeDesignType: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/type-analysis`, { ...options, method: "POST", body }),
  analyzeDesignRoutine: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/routine-analysis`, { ...options, method: "POST", body }),
  analyzeDesignTrigger: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/trigger-analysis`, { ...options, method: "POST", body }),
  analyzeDesignView: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/view-analysis`, { ...options, method: "POST", body }),
  getDesignLayout: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/layout`, options),
  replaceDesignLayout: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/layout`, { ...options, method: "PUT", body }),
  exportDesign: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/exports`, { ...options, method: "POST", body }),
  createMigrationPlan: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/migration-plans`, { ...options, method: "POST", body }),
  getMigrationPlan: (id, options) => requestJson(`${API_ROOT}/schemii/migration-plans/${encodeURIComponent(id)}`, options),
  resolveMigrationDrift: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/migration-plans/${encodeURIComponent(id)}/drift-resolutions`, { ...options, method: "POST", body }),
  createMigrationExecution: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/migration-plans/${encodeURIComponent(id)}/executions`, { ...options, method: "POST", body }),
  getMigrationExecution: (id, options) => requestJson(`${API_ROOT}/schemii/migration-executions/${encodeURIComponent(id)}`, options),
  reconcileMigrationExecution: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/migration-executions/${encodeURIComponent(id)}/reconciliation`, { ...options, method: "POST", body }),
  async listMigrationExecutions(workspaceId, { limit = 100, ...options } = {}) {
    const response = await requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/migration-executions?limit=${encodeURIComponent(limit)}`, options);
    return response.executions;
  },
  getConsoleSettings: options => requestJson(`${API_ROOT}/schemii/console/settings`, options),
  createConsoleExecution: (workspaceId, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions`, { ...options, method: "POST", body }),
  getConsoleExecution: (workspaceId, executionId, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}`, options),
  cancelConsoleExecution: (workspaceId, executionId, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}`, { ...options, method: "DELETE" }),
  getConsoleResultPage(workspaceId, executionId, resultId, { cursor = null, ...options } = {}) {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}/results/${encodeURIComponent(resultId)}${query}`, options);
  },
  closeConsoleResult: (workspaceId, executionId, resultId, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}/results/${encodeURIComponent(resultId)}`, { ...options, method: "DELETE" }),
});
