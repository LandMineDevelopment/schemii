import { requestJson } from "#common/http.js";

const API_ROOT = "/api/v1";
const CONSOLE_REQUEST_TIMEOUT_MS = 900_000;

const consoleRequest = (path, options = {}) => (
  requestJson(path, { timeoutMs: CONSOLE_REQUEST_TIMEOUT_MS, ...options })
);

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
  listConnectionNamespaces: (id, options = {}) => requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}/namespaces`, options),
  getConnectionDeletionImpact: (id, options) => requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}/deletion-impact`, options),
  deleteConnection: (id, expectedRevision, options = {}) => requestJson(`${API_ROOT}/connections/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { ...options, method: "DELETE" }),
  async listWorkspaces(options) {
    const response = await requestJson(`${API_ROOT}/schemii/workspaces`, options);
    return response.workspaces;
  },
  createWorkspace: (body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces`, { ...options, method: "POST", body }),
  openPostgresWorkspace: (body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/postgres`, { ...options, method: "POST", body }),
  getWorkspace: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}`, options),
  updateLayout: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/layout`, { ...options, method: "PUT", body }),
  renameWorkspace: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}`, { ...options, method: "PATCH", body }),
  deleteWorkspace: (id, expectedRevision, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { ...options, method: "DELETE" }),
  getCatalog: (id, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/catalog`, options),
  listRelations(workspaceId, { cursor = null, pageSize = 100, search = "", ...options } = {}) {
    const query = new URLSearchParams({ pageSize: String(pageSize) });
    if (cursor) query.set("cursor", cursor);
    if (search) query.set("search", search);
    return requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/relations?${query}`, options);
  },
  getRelation: (workspaceId, relationRef, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/relations/${encodeURIComponent(relationRef)}`, options),
  getRelationLineage: (workspaceId, relationRef, options) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/relations/${encodeURIComponent(relationRef)}/lineage`, options),
  getRelationRows(workspaceId, relationRef, { cursor = null, pageSize = 100, ...options } = {}) {
    const query = new URLSearchParams({ pageSize: String(pageSize) });
    if (cursor) query.set("cursor", cursor);
    return requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/relations/${encodeURIComponent(relationRef)}/rows?${query}`, options);
  },
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
  analyzeColumnType: (id, body, options = {}) => requestJson(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(id)}/design/column-type-analysis`, { ...options, method: "POST", body }),
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
  getConsoleSettings: options => consoleRequest(`${API_ROOT}/schemii/console/settings`, options),
  updateConsoleSettings: body => consoleRequest(`${API_ROOT}/schemii/console/settings`, { method: "PUT", body }),
  async listConsoleHistory(workspaceId, { limit = 10, ...options } = {}) {
    const response = await consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/history?limit=${encodeURIComponent(limit)}`, options);
    return response.queries;
  },
  async listConsoleSavedQueries(workspaceId, options) {
    const response = await consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/saved-queries`, options);
    return response.queries;
  },
  createConsoleSavedQuery: (workspaceId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/saved-queries`, { ...options, method: "POST", body }),
  updateConsoleSavedQuery: (workspaceId, queryId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/saved-queries/${encodeURIComponent(queryId)}`, { ...options, method: "PATCH", body }),
  deleteConsoleSavedQuery: (workspaceId, queryId, expectedRevision, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/saved-queries/${encodeURIComponent(queryId)}?expectedRevision=${encodeURIComponent(expectedRevision)}`, { ...options, method: "DELETE" }),
  createConsoleExecution: (workspaceId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions`, { ...options, method: "POST", body }),
  getConsoleExecution: (workspaceId, executionId, options) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}`, options),
  cancelConsoleExecution: (workspaceId, executionId, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}`, { ...options, method: "DELETE" }),
  getConsoleResultPage(workspaceId, executionId, resultId, { cursor = null, ...options } = {}) {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}/results/${encodeURIComponent(resultId)}${query}`, options);
  },
  closeConsoleResult: (workspaceId, executionId, resultId, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}/results/${encodeURIComponent(resultId)}`, { ...options, method: "DELETE" }),
  consoleResultExportUrl: (workspaceId, executionId, resultId) => `${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/executions/${encodeURIComponent(executionId)}/results/${encodeURIComponent(resultId)}/export.csv`,
  createConsoleTransaction: (workspaceId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/transactions`, { ...options, method: "POST", body }),
  getConsoleTransaction: (workspaceId, transactionId, options) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/transactions/${encodeURIComponent(transactionId)}`, options),
  createConsoleTransactionExecution: (workspaceId, transactionId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/transactions/${encodeURIComponent(transactionId)}/executions`, { ...options, method: "POST", body }),
  commitConsoleTransaction: (workspaceId, transactionId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/transactions/${encodeURIComponent(transactionId)}/commit`, { ...options, method: "POST", body }),
  rollbackConsoleTransaction: (workspaceId, transactionId, body, options = {}) => consoleRequest(`${API_ROOT}/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/transactions/${encodeURIComponent(transactionId)}/rollback`, { ...options, method: "POST", body }),
});
