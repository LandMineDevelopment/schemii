const API_ROOT = "/api/v1";

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

async function request(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (error) {
    throw new ApiError("The active server could not be reached", {
      code: "network_error",
      details: { cause: error instanceof Error ? error.name : "NetworkError" },
      retryable: true,
    });
  }

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
  session: () => request(`${API_ROOT}/session`),
  readiness: () => request(`${API_ROOT}/readiness`),
  async listConnections() {
    const response = await request(`${API_ROOT}/connections`);
    return response.connections.map(publicConnection);
  },
  async getConnection(id) {
    return publicConnection(await request(`${API_ROOT}/connections/${encodeURIComponent(id)}`));
  },
  async createConnection(body) {
    return publicConnection(await request(`${API_ROOT}/connections`, { method: "POST", body }));
  },
  async updateConnection(id, body) {
    return publicConnection(await request(`${API_ROOT}/connections/${encodeURIComponent(id)}`, { method: "PATCH", body }));
  },
  testConnection: id => request(`${API_ROOT}/connections/${encodeURIComponent(id)}/test`, { method: "POST" }),
  deleteConnection: (id, expectedRevision) => request(`${API_ROOT}/connections/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { method: "DELETE" }),
  async listWorkspaces() {
    const response = await request(`${API_ROOT}/schemii/workspaces`);
    return response.workspaces;
  },
  createWorkspace: body => request(`${API_ROOT}/schemii/workspaces`, { method: "POST", body }),
  getWorkspace: id => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}`),
  updateLayout: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/layout`, { method: "PUT", body }),
  deleteWorkspace: (id, expectedRevision) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { method: "DELETE" }),
  getCatalog: id => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/catalog`),
  getDesign: id => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design`),
  replaceDesign: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design`, { method: "PUT", body }),
  analyzeDesignType: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/type-analysis`, { method: "POST", body }),
  analyzeDesignRoutine: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/routine-analysis`, { method: "POST", body }),
  analyzeDesignTrigger: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/trigger-analysis`, { method: "POST", body }),
  analyzeDesignView: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/view-analysis`, { method: "POST", body }),
  getDesignLayout: id => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/layout`),
  replaceDesignLayout: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/layout`, { method: "PUT", body }),
  exportDesign: (id, body) => request(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/exports`, { method: "POST", body }),
});
