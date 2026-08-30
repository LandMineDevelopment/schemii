(() => {
  class ApiContractError extends Error {
    constructor(message, { contract = null, payload = null } = {}) {
      super(message);
      this.name = "ApiContractError";
      this.code = "invalid_api_response";
      this.contract = contract;
      this.payload = payload;
    }
  }

  const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const nonEmptyString = value => typeof value === "string" && value.length > 0;

  function requireObject(payload, contract) {
    if (!isObject(payload)) throw new ApiContractError(`The ${contract} response must be an object`, { contract, payload });
    return payload;
  }

  function requireArray(payload, field, contract) {
    requireObject(payload, contract);
    if (!Array.isArray(payload[field])) throw new ApiContractError(`The ${contract} response must include a ${field} array`, { contract, payload });
    return payload;
  }

  function validateSessionResponse(payload) {
    requireObject(payload, "session");
    if (!nonEmptyString(payload.token) || !nonEmptyString(payload.serverId)) throw new ApiContractError("The session response must include a token and server ID", { contract: "session", payload });
    return payload;
  }

  function validateProfilesResponse(payload) {
    requireArray(payload, "profiles", "profiles");
    if (payload.profiles.some(profile => !isObject(profile) || !nonEmptyString(profile.id))) {
      throw new ApiContractError("The profiles response contains an invalid profile", { contract: "profiles", payload });
    }
    return payload;
  }

  function validateCatalogResponse(payload, kind = null) {
    requireObject(payload, "catalog");
    const fingerprint = /^[0-9a-f]{64}$/;
    if (!nonEmptyString(payload.profileId) || !nonEmptyString(payload.profileFingerprint) || !nonEmptyString(payload.database)
        || !fingerprint.test(payload.profileFingerprint) || !fingerprint.test(payload.catalogFingerprint) || !isObject(payload.page)
        || !Number.isInteger(payload.page.pageSize) || !Number.isInteger(payload.page.returned) || typeof payload.page.hasMore !== "boolean"
        || (payload.page.hasMore ? !nonEmptyString(payload.page.nextCursor) : payload.page.nextCursor !== null)) {
      throw new ApiContractError("The catalog response has invalid target or page metadata", { contract: "catalog", payload });
    }
    if (kind === "namespaces" || Object.hasOwn(payload, "namespaces")) {
      requireArray(payload, "namespaces", "catalog");
      requireArray(payload, "entries", "catalog");
      const classifications = ["user", "pg_catalog", "information_schema", "temporary", "toast", "other_system"];
      if (!["user", "all"].includes(payload.scope) || payload.namespaces.some(namespace => !nonEmptyString(namespace))
          || payload.entries.some(entry => !isObject(entry) || !nonEmptyString(entry.name) || !classifications.includes(entry.classification) || typeof entry.system !== "boolean")) {
        throw new ApiContractError("The catalog response contains an invalid namespace", { contract: "catalog", payload });
      }
    } else if (kind === "relations" || Object.hasOwn(payload, "relations")) {
      requireArray(payload, "relations", "catalog");
      requireArray(payload, "entries", "catalog");
      const kinds = ["table", "partitioned_table", "view", "materialized_view", "foreign_table"];
      if (!nonEmptyString(payload.namespace) || payload.relations.some(relation => !isObject(relation) || !nonEmptyString(relation.name) || !kinds.includes(relation.kind))
          || payload.entries.some(relation => !isObject(relation) || relation.relation !== relation.name || relation.profileId !== payload.profileId
          || relation.database !== payload.database || relation.namespace !== payload.namespace || !kinds.includes(relation.kind))) {
        throw new ApiContractError("The catalog response contains an invalid relation", { contract: "catalog", payload });
      }
    } else if (!nonEmptyString(payload.relation) && !nonEmptyString(payload.fingerprint)) {
      throw new ApiContractError("The catalog response has no recognized catalog data", { contract: "catalog", payload });
    }
    return payload;
  }

  function validatePlanResponse(payload) {
    requireObject(payload, "plan");
    const plan = isObject(payload.plan) ? payload.plan : payload;
    const durablePlan = nonEmptyString(plan.id);
    const boundedPreview = plan.id === null && plan.previewOnly === true && plan.applyCapable === false;
    const authorizedAiPreview = plan.id === null && plan.previewOnly === true && plan.applyCapable === true && nonEmptyString(plan.applyPlanId);
    if ((!durablePlan && !boundedPreview && !authorizedAiPreview) || !Array.isArray(plan.steps) || !Array.isArray(plan.warnings) || typeof plan.destructive !== "boolean") {
      throw new ApiContractError("The plan response is invalid", { contract: "plan", payload });
    }
    return payload;
  }

  function validateOperationResponse(payload) {
    requireObject(payload, "operation");
    const operation = isObject(payload.operation) ? payload.operation : payload;
    if (!nonEmptyString(operation.id) || !nonEmptyString(operation.state)) {
      throw new ApiContractError("The operation response is invalid", { contract: "operation", payload });
    }
    return payload;
  }

  function validateResourceSummariesResponse(payload) {
    requireObject(payload, "resource summaries");
    const summaries = Array.isArray(payload.resources) ? payload.resources : payload.summaries;
    if (!Array.isArray(summaries)) {
      throw new ApiContractError("The resource summaries response must include a resources or summaries array", { contract: "resource summaries", payload });
    }
    if (summaries.some(resource => !isObject(resource) || !nonEmptyString(resource.id))) {
      throw new ApiContractError("The resource summaries response contains an invalid resource", { contract: "resource summaries", payload });
    }
    return payload;
  }

  function validateDashboardSummariesResponse(payload) {
    validateResourceSummariesResponse(payload);
    if (payload.summaries.some(item => !nonEmptyString(item.title) || !Number.isInteger(item.revision) || typeof item.archived !== "boolean")) {
      throw new ApiContractError("The dashboard summaries response contains an invalid dashboard", { contract: "dashboard summaries", payload });
    }
    validateOptionalPage(payload, "dashboard summaries");
    return payload;
  }

  function validateOptionalPage(payload, contract) {
    if (payload.page === undefined) return;
    const page = payload.page;
    if (!isObject(page) || !Number.isInteger(page.pageSize) || page.pageSize < 1 || !Number.isInteger(page.returned) || page.returned < 0 || typeof page.hasMore !== "boolean" || page.nextCursor !== null && !nonEmptyString(page.nextCursor)) {
      throw new ApiContractError(`The ${contract} page is invalid`, { contract, payload });
    }
  }

  function validateSchemaRecord(payload) {
    requireObject(payload, "schema");
    if (!nonEmptyString(payload.id) || !Number.isInteger(payload.revision) || payload.revision < 1 || !/^[0-9a-f]{64}$/.test(payload.layoutToken) || !isObject(payload.schema)) {
      throw new ApiContractError("The schema response is invalid", { contract: "schema", payload });
    }
    return payload;
  }

  function validateDashboardRecord(payload) {
    requireObject(payload, "dashboard");
    const dashboard = payload.dashboard;
    const recordFields = Object.keys(payload);
    const allowedRecordFields = new Set(["id", "version", "revision", "updatedAt", "dashboard", "aiOperationReceipts"]);
    if (!nonEmptyString(payload.id) || payload.version !== 3 || !Number.isInteger(payload.revision) || payload.revision < 1
        || recordFields.some(field => !allowedRecordFields.has(field)) || !isObject(dashboard)
        || Object.keys(dashboard).sort().join(",") !== "archived,slicers,title,viewport,widgets"
        || !nonEmptyString(dashboard.title) || typeof dashboard.archived !== "boolean"
        || !Array.isArray(dashboard.widgets) || !Array.isArray(dashboard.slicers)
        || !isObject(dashboard.viewport) || Object.keys(dashboard.viewport).sort().join(",") !== "desktop,mobile"
        || [dashboard.viewport.desktop, dashboard.viewport.mobile].some(viewport => !isObject(viewport) || Object.keys(viewport).join(",") !== "y" || !Number.isInteger(viewport.y) || viewport.y < 0)
        || dashboard.widgets.some(widget => !isObject(widget) || Object.keys(widget).sort().join(",") !== "configuration,id,kind,title"
          || !nonEmptyString(widget.id) || !nonEmptyString(widget.kind) || !nonEmptyString(widget.title) || !isObject(widget.configuration))) {
      throw new ApiContractError("The dashboard response is invalid", { contract: "dashboard", payload });
    }
    return payload;
  }

  function validateLegacySourcePreviewResponse(payload) {
    requireObject(payload, "legacy source preview");
    const fingerprint = /^[0-9a-f]{64}$/;
    const responseFields = ["dashboardId", "expectedRevision", "widgetIds", "deferredWidgetIds", "maximumUniqueProfileDatabases", "maximumDigestLength", "results", "compatibleWidgetIds", "incompatibleWidgetIds", "digest", "expiresAt"];
    const widgetIds = Array.isArray(payload.widgetIds) ? payload.widgetIds : [];
    const deferredIds = Array.isArray(payload.deferredWidgetIds) ? payload.deferredWidgetIds : [];
    const compatibleIds = Array.isArray(payload.compatibleWidgetIds) ? payload.compatibleWidgetIds : [];
    const incompatibleIds = Array.isArray(payload.incompatibleWidgetIds) ? payload.incompatibleWidgetIds : [];
    const resultIds = Array.isArray(payload.results) ? payload.results.map(result => result?.widgetId) : [];
    if (Object.keys(payload).sort().join(",") !== responseFields.sort().join(",")
        || !nonEmptyString(payload.dashboardId) || !Number.isInteger(payload.expectedRevision) || payload.expectedRevision < 1 || !widgetIds.length || widgetIds.length + deferredIds.length > 100
        || widgetIds.some(id => !nonEmptyString(id)) || new Set(widgetIds).size !== widgetIds.length
        || !Array.isArray(payload.deferredWidgetIds) || deferredIds.some(id => !nonEmptyString(id))
        || new Set([...widgetIds, ...deferredIds]).size !== widgetIds.length + deferredIds.length
        || !Number.isInteger(payload.maximumUniqueProfileDatabases) || payload.maximumUniqueProfileDatabases < 1 || payload.maximumUniqueProfileDatabases > 100
        || !Number.isInteger(payload.maximumDigestLength) || payload.maximumDigestLength < 1 || payload.maximumDigestLength > 1024 * 1024
        || !Array.isArray(payload.results) || !Array.isArray(payload.compatibleWidgetIds) || !Array.isArray(payload.incompatibleWidgetIds)
        || resultIds.length !== widgetIds.length || resultIds.some((id, index) => id !== widgetIds[index])
        || [...compatibleIds, ...incompatibleIds].some(id => !nonEmptyString(id))
        || new Set([...compatibleIds, ...incompatibleIds]).size !== widgetIds.length
        || widgetIds.some(id => !compatibleIds.includes(id) && !incompatibleIds.includes(id))
        || !nonEmptyString(payload.digest) || payload.digest.length > payload.maximumDigestLength
        || !nonEmptyString(payload.expiresAt) || !Number.isFinite(Date.parse(payload.expiresAt))
        || payload.results.some(result => !isObject(result) || !nonEmptyString(result.widgetId) || !nonEmptyString(result.title)
          || !["compatible", "incompatible"].includes(result.status)
          || result.status === "compatible" && (
            Object.keys(result).sort().join(",") !== "columnCount,columns,currentFingerprint,currentLegacyFingerprint,profileFingerprint,query,savedLegacyFingerprint,source,status,title,widgetId"
            || !compatibleIds.includes(result.widgetId) || !isObject(result.source)
            || Object.keys(result.source).sort().join(",") !== "database,kind,namespace,profileId,relation"
            || Object.values(result.source).some(value => !nonEmptyString(value)) || !["table", "view", "materialized_view", "foreign_table"].includes(result.source.kind)
            || !fingerprint.test(result.profileFingerprint)
            || !fingerprint.test(result.savedLegacyFingerprint) || !fingerprint.test(result.currentLegacyFingerprint)
            || result.savedLegacyFingerprint !== result.currentLegacyFingerprint
            || !fingerprint.test(result.currentFingerprint) || !Number.isInteger(result.columnCount) || result.columnCount < 0
            || result.columns !== "exact" || !["valid", "not_configured"].includes(result.query)
          )
          || result.status === "incompatible" && (
            Object.keys(result).sort().join(",") !== "error,status,title,widgetId" || !incompatibleIds.includes(result.widgetId)
            || !isObject(result.error) || Object.keys(result.error).sort().join(",") !== "code,message"
            || !nonEmptyString(result.error.code) || !nonEmptyString(result.error.message)
          ))) {
      throw new ApiContractError("The legacy source preview response is invalid", { contract: "legacy source preview", payload });
    }
    return payload;
  }

  function validateLegacySourceApplyResponse(payload) {
    requireObject(payload, "legacy source apply");
    const expectedFields = "dashboardId,incompatibleWidgetIds,postWriteVerification,previousRevision,revision,upgradedWidgetIds";
    const verification = payload.postWriteVerification;
    const changedIds = Array.isArray(verification?.changedWidgetIds) ? verification.changedWidgetIds : [];
    const unavailableIds = Array.isArray(verification?.unavailableWidgetIds) ? verification.unavailableWidgetIds : [];
    if (Object.keys(payload).sort().join(",") !== expectedFields
        || !nonEmptyString(payload.dashboardId) || !Number.isInteger(payload.previousRevision) || payload.previousRevision < 1 || !Number.isInteger(payload.revision)
        || payload.revision !== payload.previousRevision + 1 || !Array.isArray(payload.upgradedWidgetIds) || !payload.upgradedWidgetIds.length
        || !Array.isArray(payload.incompatibleWidgetIds)
        || [...payload.upgradedWidgetIds, ...payload.incompatibleWidgetIds].some(id => !nonEmptyString(id))
        || new Set([...payload.upgradedWidgetIds, ...payload.incompatibleWidgetIds]).size !== payload.upgradedWidgetIds.length + payload.incompatibleWidgetIds.length
        || !isObject(verification) || Object.keys(verification).sort().join(",") !== "changedWidgetIds,status,unavailableWidgetIds"
        || !["current", "changed", "unavailable"].includes(verification.status)
        || !Array.isArray(verification.changedWidgetIds) || !Array.isArray(verification.unavailableWidgetIds)
        || [...changedIds, ...unavailableIds].some(id => !nonEmptyString(id) || !payload.upgradedWidgetIds.includes(id))
        || new Set([...changedIds, ...unavailableIds]).size !== changedIds.length + unavailableIds.length
        || verification.status === "current" && (changedIds.length || unavailableIds.length)
        || verification.status === "changed" && !changedIds.length
        || verification.status === "unavailable" && (changedIds.length || !unavailableIds.length)) {
      throw new ApiContractError("The legacy source apply response is invalid", { contract: "legacy source apply", payload });
    }
    return payload;
  }

  function validateSchemasResponse(payload) {
    requireArray(payload, "schemas", "schemas");
    payload.schemas.forEach(validateSchemaRecord);
    return payload;
  }

  function validateDashboardsResponse(payload) {
    requireArray(payload, "dashboards", "dashboards");
    payload.dashboards.forEach(validateDashboardRecord);
    validateOptionalPage(payload, "dashboards");
    return payload;
  }

  function validateSchemaSaveResponse(payload) {
    requireObject(payload, "schema save");
    if (!nonEmptyString(payload.saved) || !Number.isInteger(payload.revision) || payload.revision < 1 || !nonEmptyString(payload.updatedAt) || !/^[0-9a-f]{64}$/.test(payload.layoutToken)) {
      throw new ApiContractError("The schema save response is invalid", { contract: "schema save", payload });
    }
    return payload;
  }

  function validateDeleteResponse(payload) {
    requireObject(payload, "delete");
    if (!nonEmptyString(payload.deleted)) throw new ApiContractError("The delete response is invalid", { contract: "delete", payload });
    return payload;
  }

  function validateShutdownResponse(payload) {
    requireObject(payload, "shutdown");
    if (payload.shuttingDown !== true) throw new ApiContractError("The shutdown response is invalid", { contract: "shutdown", payload });
    return payload;
  }

  function validateDeletionImpactResponse(payload) {
    requireObject(payload, "profile deletion impact");
    if (!nonEmptyString(payload.profileId) || !nonEmptyString(payload.profileFingerprint) || !/^[0-9a-f]{64}$/.test(payload.impactFingerprint) || !isObject(payload.impact)) {
      throw new ApiContractError("The profile deletion impact response is invalid", { contract: "profile deletion impact", payload });
    }
    for (const field of ["schemas", "dashboards", "activeChats", "plans", "operations"]) {
      if (!Array.isArray(payload.impact[field])) throw new ApiContractError("The profile deletion impact response is incomplete", { contract: "profile deletion impact", payload });
    }
    return payload;
  }

  function validateResultResource(resource, kind = null) {
    if (!isObject(resource) || resource.version !== 1 || !nonEmptyString(resource.id) || !nonEmptyString(resource.binding)
        || !["aggregate", "detail"].includes(resource.kind) || kind && resource.kind !== kind
        || resource.state !== "retained" || resource.processLocal !== true || !nonEmptyString(resource.expiresAt)
        || !isObject(resource.page) || !Number.isInteger(resource.page.offset) || resource.page.offset < 0
        || !Number.isInteger(resource.page.returnedRows) || resource.page.returnedRows < 0
        || typeof resource.page.hasNext !== "boolean" || typeof resource.page.hasPrevious !== "boolean"
        || resource.page.nextCursor !== null && !nonEmptyString(resource.page.nextCursor)
        || resource.page.previousCursor !== null && !nonEmptyString(resource.page.previousCursor)
        || resource.page.hasNext && !nonEmptyString(resource.page.nextCursor)
        || resource.page.hasPrevious && !nonEmptyString(resource.page.previousCursor)
        || !isObject(resource.export) || !Array.isArray(resource.export.formats)
        || !resource.export.formats.includes("json") || !resource.export.formats.includes("csv")) {
      throw new ApiContractError("The retained result resource is invalid", { contract: "result resource", resource });
    }
    return resource;
  }

  function validateQueryResultResponse(payload) {
    requireObject(payload, "query result");
    if (!Array.isArray(payload.columns) || !Array.isArray(payload.rows) || !nonEmptyString(payload.sql) || !Array.isArray(payload.parameters) || !isObject(payload.effectiveQuery) || !Array.isArray(payload.slicerLineage)) {
      throw new ApiContractError("The query result response is invalid", { contract: "query result", payload });
    }
    if (payload.resultResource !== undefined) validateResultResource(payload.resultResource, "aggregate");
    else if (payload.truncated !== false) throw new ApiContractError("A continued query result requires a retained resource", { contract: "query result", payload });
    return payload;
  }

  function validateDetailResultResponse(payload) {
    requireObject(payload, "detail result");
    if (!Array.isArray(payload.columns) || !Array.isArray(payload.rows) || !nonEmptyString(payload.sql) || !Array.isArray(payload.parameters) || !isObject(payload.effectiveQuery) || !Array.isArray(payload.slicerLineage)) {
      throw new ApiContractError("The detail result response is invalid", { contract: "detail result", payload });
    }
    validateResultResource(payload.resultResource, "detail");
    if (!Number.isInteger(payload.matchingRowCount) || !Number.isInteger(payload.nextOffset) || payload.nextOffset < payload.offset || typeof payload.hasMore !== "boolean") {
      throw new ApiContractError("The detail result response is invalid", { contract: "detail result", payload });
    }
    return payload;
  }

  function createApiPathPredicate(prefix) {
    if (typeof prefix !== "string" || !prefix.startsWith("/") || prefix.endsWith("/")) throw new TypeError("An absolute API path prefix is required");
    return value => {
      if (typeof value !== "string") return false;
      const path = value.split(/[?#]/, 1)[0];
      return path === prefix || path.startsWith(`${prefix}/`);
    };
  }

  function postgresResponseValidator(path, method = "GET") {
    if (typeof path !== "string") return null;
    const pathname = path.split(/[?#]/, 1)[0];
    const requestMethod = String(method).toUpperCase();
    if (pathname === "/api/postgres/profiles" && requestMethod === "GET") return validateProfilesResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/deletion-impact$/.test(pathname)) return validateDeletionImpactResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/namespaces$/.test(pathname)) return payload => validateCatalogResponse(payload, "namespaces");
    if (/^\/api\/postgres\/profiles\/[^/]+\/relations$/.test(pathname)) return payload => validateCatalogResponse(payload, "relations");
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:preview|views\/preview)$/.test(pathname)) return validatePlanResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:relation\/query|saved-widgets\/aggregate|dashboard-widgets\/preview)$/.test(pathname)) return validateQueryResultResponse;
    if (/^\/api\/postgres\/profiles\/[^/]+\/(?:relation\/detail|saved-widgets\/detail)$/.test(pathname)) return validateDetailResultResponse;
    if (requestMethod === "GET" && /^\/api\/postgres\/profiles\/[^/]+\/structured-results\/[^/]+$/.test(pathname)) return payload => {
      const kind = payload?.resultResource?.kind;
      return kind === "detail" ? validateDetailResultResponse(payload) : validateQueryResultResponse(payload);
    };
    return null;
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    ApiContractError,
    createApiPathPredicate,
    postgresResponseValidator,
    validateCatalogResponse,
    validateDashboardRecord,
    validateLegacySourceApplyResponse,
    validateLegacySourcePreviewResponse,
    validateDashboardsResponse,
    validateDeleteResponse,
    validateDeletionImpactResponse,
    validateDetailResultResponse,
    validateOperationResponse,
    validatePlanResponse,
    validateProfilesResponse,
    validateQueryResultResponse,
    validateResultResource,
    validateResourceSummariesResponse,
    validateDashboardSummariesResponse,
    validateSchemaRecord,
    validateSchemasResponse,
    validateSchemaSaveResponse,
    validateShutdownResponse,
    validateSessionResponse,
  });
})();
