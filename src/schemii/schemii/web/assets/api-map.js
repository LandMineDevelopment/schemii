const HTTP_METHODS = ["get", "post", "put", "patch", "delete", "options", "head", "trace"];
const METHOD_ORDER = new Map(HTTP_METHODS.map((method, index) => [method, index]));
const CONTRACT_REFRESH_INTERVAL = 30_000;
const CONTRACT_REQUEST_TIMEOUT = 10_000;

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function localPointer(reference) {
  if (typeof reference !== "string" || !reference.startsWith("#")) return null;
  if (reference.startsWith("#/")) return reference;
  try {
    const decoded = `#${decodeURIComponent(reference.slice(1))}`;
    return decoded.startsWith("#/") ? decoded : null;
  } catch {
    return null;
  }
}

export function schemaReferenceName(reference) {
  if (typeof reference !== "string") return null;
  const prefix = "#/components/schemas/";
  if (!reference.startsWith(prefix)) return reference;
  const pointerName = reference.slice(prefix.length).replaceAll("~1", "/").replaceAll("~0", "~");
  try {
    return decodeURIComponent(pointerName);
  } catch {
    return pointerName;
  }
}

function schemaLabel(schema) {
  const value = asRecord(schema);
  const reference = schemaReferenceName(value.$ref);
  if (reference) return reference;
  if (value.type === "array") return `Array<${schemaLabel(value.items) || "value"}>`;
  if (Array.isArray(value.type)) return value.type.join(" | ");
  if (typeof value.type === "string") return value.type;
  if (value.oneOf) return "oneOf";
  if (value.anyOf) return "anyOf";
  if (value.allOf) return "allOf";
  if (value.properties) return "object";
  return null;
}

function schemaLabels(schema) {
  const value = asRecord(schema);
  const reference = schemaReferenceName(value.$ref);
  if (reference) return [reference];
  if (value.type === "array") {
    const items = schemaLabels(value.items);
    return items.length ? items.map(item => `Array<${item}>`) : ["Array<value>"];
  }
  for (const [keyword, separator] of [["oneOf", " | "], ["anyOf", " | "], ["allOf", " & "]]) {
    if (!Array.isArray(value[keyword])) continue;
    const branches = unique(value[keyword].flatMap(schemaLabels));
    return branches.length ? [`${keyword}<${branches.join(separator)}>`] : [keyword];
  }
  const label = schemaLabel(value);
  return label ? [label] : [];
}

export function collectSchemaNames(value) {
  const names = [];
  const visit = candidate => {
    if (!candidate || typeof candidate !== "object") return;
    if (Array.isArray(candidate)) {
      candidate.forEach(visit);
      return;
    }
    if (candidate.$ref) {
      const name = schemaReferenceName(candidate.$ref);
      if (name) names.push(name);
      return;
    }
    for (const [key, child] of Object.entries(candidate)) {
      if (["example", "examples", "default", "enum"].includes(key)) continue;
      visit(child);
    }
  };
  visit(value);
  return unique(names);
}

function resolveLocalReference(specification, value) {
  let source = asRecord(value);
  const visited = new Set();
  let pointer = localPointer(source.$ref);
  while (pointer && !visited.has(pointer)) {
    visited.add(pointer);
    const target = pointer.slice(2).split("/").reduce((current, token) => {
      let decoded = token;
      try {
        decoded = decodeURIComponent(token);
      } catch {
        // Leave malformed URI escapes untouched so the reference fails closed.
      }
      const key = decoded.replaceAll("~1", "/").replaceAll("~0", "~");
      return asRecord(current)[key];
    }, specification);
    if (!target || typeof target !== "object") break;
    const siblings = Object.fromEntries(Object.entries(source).filter(([key]) => key !== "$ref"));
    source = { ...asRecord(target), ...siblings };
    pointer = localPointer(source.$ref);
  }
  return source;
}

function contentSchemaLabels(content) {
  const labels = [];
  for (const mediaType of Object.values(asRecord(content))) {
    const schema = asRecord(mediaType).schema;
    labels.push(...schemaLabels(schema));
  }
  return unique(labels);
}

function parameterModel(specification, parameter) {
  const source = resolveLocalReference(specification, parameter);
  const schemas = source.schema
    ? schemaLabels(source.schema)
    : contentSchemaLabels(source.content);
  return {
    name: text(source.name, "unnamed"),
    location: text(source.in, "unknown"),
    required: Boolean(source.required),
    description: text(source.description),
    schema: schemas.join(" | ") || "value",
  };
}

function responseModel(specification, status, response) {
  const source = resolveLocalReference(specification, response);
  return {
    status,
    description: text(source.description, "Documented response"),
    schemas: contentSchemaLabels(source.content),
  };
}

function mergedParameters(specification, pathItem, operation) {
  const parameters = new Map();
  const add = parameter => {
    const source = resolveLocalReference(specification, parameter);
    const key = `${text(source.in, "unknown")}:${text(source.name, "unnamed")}`;
    parameters.set(key, parameterModel(specification, source));
  };
  if (Array.isArray(pathItem.parameters)) pathItem.parameters.forEach(add);
  if (Array.isArray(operation.parameters)) operation.parameters.forEach(add);
  return [...parameters.values()];
}

function statusRank(status) {
  if (status === "default") return [Number.POSITIVE_INFINITY, status];
  if (/^[1-5]\d\d$/.test(status)) return [Number(status), status];
  const range = /^([1-5])XX$/i.exec(status);
  if (range) return [Number(range[1]) * 100 + 99, status];
  return [900, status];
}

function statusSort([left], [right]) {
  const [leftRank, leftLabel] = statusRank(left);
  const [rightRank, rightLabel] = statusRank(right);
  return leftRank - rightRank || leftLabel.localeCompare(rightLabel);
}

function operationModel(specification, path, method, pathItem, operation) {
  const source = asRecord(operation);
  const requestBody = resolveLocalReference(specification, source.requestBody);
  const parameters = mergedParameters(specification, pathItem, source);
  const responses = Object.entries(asRecord(source.responses))
    .sort(statusSort)
    .map(([status, response]) => responseModel(specification, status, response));
  const requestSchemas = contentSchemaLabels(requestBody.content);
  const responseSchemas = unique(responses.flatMap(response => response.schemas));
  const tags = Array.isArray(source.tags) && source.tags.length
    ? source.tags.map(tag => text(tag, "ungrouped"))
    : ["ungrouped"];
  return {
    id: `${method}:${path}`,
    method,
    path,
    tags,
    primaryTag: tags[0],
    summary: text(source.summary, text(source.operationId, `${method.toUpperCase()} ${path}`)),
    description: text(source.description),
    operationId: text(source.operationId),
    deprecated: Boolean(source.deprecated),
    parameters,
    request: {
      required: Boolean(requestBody.required),
      schemas: requestSchemas,
    },
    responses,
    schemas: unique([...requestSchemas, ...responseSchemas]),
  };
}

export function buildApiMapModel(document) {
  const specification = asRecord(document);
  const paths = asRecord(specification.paths);
  const tagMetadata = new Map(
    (Array.isArray(specification.tags) ? specification.tags : [])
      .map(tag => [text(tag?.name), text(tag?.description)])
      .filter(([name]) => name),
  );
  const operations = [];
  for (const [path, rawPathItem] of Object.entries(paths)) {
    const pathItem = resolveLocalReference(specification, rawPathItem);
    for (const [method, rawOperation] of Object.entries(pathItem)) {
      if (!METHOD_ORDER.has(method.toLowerCase())) continue;
      operations.push(operationModel(specification, path, method.toLowerCase(), pathItem, rawOperation));
    }
  }
  operations.sort((left, right) => {
    const pathComparison = left.path.localeCompare(right.path);
    return pathComparison || METHOD_ORDER.get(left.method) - METHOD_ORDER.get(right.method);
  });

  const groupsByTag = new Map();
  for (const operation of operations) {
    if (!groupsByTag.has(operation.primaryTag)) {
      groupsByTag.set(operation.primaryTag, {
        name: operation.primaryTag,
        description: tagMetadata.get(operation.primaryTag) || "",
        operations: [],
      });
    }
    groupsByTag.get(operation.primaryTag).operations.push(operation);
  }

  return {
    title: text(specification.info?.title, "API"),
    version: text(specification.info?.version, "Unversioned"),
    description: text(specification.info?.description, "OpenAPI contract from the active server."),
    pathCount: Object.keys(paths).length,
    schemaCount: Object.keys(asRecord(specification.components?.schemas)).length,
    operations,
    groups: [...groupsByTag.values()],
  };
}

function element(tag, { className = "", textContent = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent) node.textContent = textContent;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function replace(target, ...children) {
  target.replaceChildren(...children.filter(Boolean));
}

function methodBadge(method) {
  return element("span", {
    className: `method-badge method-${method}`,
    textContent: method.toUpperCase(),
  });
}

function schemaChips(names) {
  const list = element("div", { className: "schema-chips" });
  if (!names.length) {
    list.append(element("span", { className: "none-chip", textContent: "No schema" }));
    return list;
  }
  for (const name of names) list.append(element("code", { textContent: name }));
  return list;
}

const uiState = {
  model: null,
  selectedId: null,
  query: "",
  contractFingerprint: null,
  loading: false,
  refreshTimer: null,
  detailSignature: null,
};

function byId(id) {
  return document.getElementById(id);
}

function operationMatches(operation, query) {
  if (!query) return true;
  return [
    operation.method,
    operation.path,
    operation.summary,
    operation.description,
    operation.operationId,
    ...operation.tags,
    ...operation.schemas,
  ].join(" ").toLowerCase().includes(query);
}

function visibleOperations() {
  return uiState.model?.operations.filter(operation => operationMatches(operation, uiState.query)) || [];
}

function setSelected(operationId, { reveal = false } = {}) {
  const operation = uiState.model?.operations.find(candidate => candidate.id === operationId);
  if (!operation) return;
  uiState.selectedId = operationId;
  document.querySelectorAll(".operation-node").forEach(button => {
    const selected = button.dataset.operationId === operationId;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  const signature = JSON.stringify(operation);
  if (signature !== uiState.detailSignature) {
    renderOperationDetail(operation);
    uiState.detailSignature = signature;
  }
  if (reveal && window.matchMedia("(max-width: 1180px)").matches) {
    const inspector = byId("operation-detail");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    inspector.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    inspector.querySelector("h2")?.focus({ preventScroll: true });
  }
}

function renderOperationDetail(operation) {
  const empty = byId("operation-empty");
  const detail = byId("operation-detail");
  const inspector = detail.closest(".operation-inspector");
  if (!operation) {
    empty.hidden = false;
    detail.hidden = true;
    inspector.setAttribute("aria-labelledby", "operation-title");
    uiState.detailSignature = null;
    replace(detail);
    return;
  }

  empty.hidden = true;
  detail.hidden = false;
  const header = element("header", { className: "detail-head" });
  const identity = element("div");
  const methodPath = element("div", { className: "detail-operation" });
  methodPath.append(methodBadge(operation.method), element("code", { textContent: operation.path }));
  identity.append(
    element("span", { className: "eyebrow", textContent: operation.primaryTag }),
    element("h2", { textContent: operation.summary, attrs: { id: "selected-operation-title", tabindex: "-1" } }),
    methodPath,
  );
  const returnButton = element("button", {
    className: "return-routes",
    textContent: "Back to routes",
    attrs: { type: "button" },
  });
  returnButton.addEventListener("click", () => {
    const selected = document.querySelector(`.operation-node[data-operation-id="${CSS.escape(operation.id)}"]`);
    selected?.scrollIntoView({ block: "center" });
    selected?.focus({ preventScroll: true });
  });
  const swaggerLink = element("a", {
    className: "swagger-link",
    textContent: "Open Swagger",
    attrs: { href: "/docs" },
  });
  const actions = element("div", { className: "detail-actions" });
  actions.append(returnButton, swaggerLink);
  header.append(identity, actions);

  const body = element("div", { className: "detail-body" });
  if (operation.deprecated) body.append(element("p", { className: "deprecated-note", textContent: "This operation is marked as deprecated." }));
  body.append(element("p", {
    className: "operation-description",
    textContent: operation.description || "No additional operation description is present in OpenAPI.",
  }));

  const metadata = element("dl", { className: "operation-metadata" });
  const metadataValues = [
    ["Operation ID", operation.operationId || "Not specified"],
    ["Tags", operation.tags.join(", ")],
  ];
  for (const [label, value] of metadataValues) {
    const wrapper = element("div");
    wrapper.append(element("dt", { textContent: label }), element("dd", { textContent: value }));
    metadata.append(wrapper);
  }
  body.append(metadata);

  const parameters = element("section", { className: "detail-section" });
  parameters.append(element("h3", { textContent: `Parameters · ${operation.parameters.length}` }));
  if (!operation.parameters.length) {
    parameters.append(element("p", { className: "none-reported", textContent: "No path, query, header, or cookie parameters." }));
  } else {
    const list = element("div", { className: "contract-list" });
    for (const parameter of operation.parameters) {
      const item = element("article", { className: "contract-item" });
      const title = element("div");
      title.append(
        element("code", { textContent: parameter.name }),
        element("span", { textContent: parameter.required ? `${parameter.location} · required` : parameter.location }),
      );
      item.append(title, element("p", { textContent: parameter.description || parameter.schema }));
      list.append(item);
    }
    parameters.append(list);
  }
  body.append(parameters);

  const request = element("section", { className: "detail-section" });
  request.append(element("h3", { textContent: `Request body${operation.request.required ? " · required" : ""}` }), schemaChips(operation.request.schemas));
  body.append(request);

  const responses = element("section", { className: "detail-section" });
  responses.append(element("h3", { textContent: `Responses · ${operation.responses.length}` }));
  const responseList = element("div", { className: "response-list" });
  for (const response of operation.responses) {
    const item = element("article", { className: "response-item" });
    const copy = element("div");
    copy.append(element("strong", { textContent: response.status }), element("p", { textContent: response.description }));
    item.append(copy, schemaChips(response.schemas));
    responseList.append(item);
  }
  responses.append(responseList);
  body.append(responses);
  replace(detail, header, body);
  inspector.setAttribute("aria-labelledby", "selected-operation-title");
}

function groupIdentifier(name, index) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "ungrouped";
  return `group-${index + 1}-${slug}`;
}

function renderMap({ restoreOperationFocus = null, restoreInspectorElement = null, restoreGroupHref = null } = {}) {
  const model = uiState.model;
  if (!model) return;
  const query = uiState.query;
  const shown = visibleOperations();
  const shownIds = new Set(shown.map(operation => operation.id));
  const groupLinks = byId("group-links");
  const routeGroups = byId("route-groups");
  groupLinks.hidden = false;
  routeGroups.hidden = false;
  replace(groupLinks);
  replace(routeGroups);

  for (const [groupIndex, group] of model.groups.entries()) {
    const operations = group.operations.filter(operation => shownIds.has(operation.id));
    if (!operations.length) continue;
    const identifier = groupIdentifier(group.name, groupIndex);
    const indexLink = element("a", { attrs: { href: `#${identifier}` } });
    indexLink.append(
      element("span", { textContent: group.name }),
      element("small", { textContent: String(operations.length) }),
    );
    groupLinks.append(indexLink);

    const section = element("section", {
      className: "route-group",
      attrs: { id: identifier, "aria-labelledby": `${identifier}-title` },
    });
    const groupNode = element("header", { className: "group-node" });
    groupNode.append(
      element("span", { className: "group-glyph", textContent: group.name.slice(0, 2).toUpperCase(), attrs: { "aria-hidden": "true" } }),
      element("span", { className: "eyebrow", textContent: "Route group" }),
      element("h3", { textContent: group.name, attrs: { id: `${identifier}-title` } }),
      element("p", { textContent: group.description || `${operations.length} operation${operations.length === 1 ? "" : "s"} in the active contract.` }),
    );
    const operationList = element("div", { className: "operation-list" });
    for (const operation of operations) {
      const button = element("button", {
        className: "operation-node",
        attrs: {
          type: "button",
          "data-operation-id": operation.id,
          "aria-pressed": operation.id === uiState.selectedId ? "true" : "false",
        },
      });
      if (operation.id === uiState.selectedId) button.classList.add("selected");
      const route = element("span", { className: "operation-route" });
      route.append(methodBadge(operation.method), element("code", { textContent: operation.path }));
      const summary = element("span", { className: "operation-copy" });
      summary.append(
        element("strong", { textContent: operation.summary }),
        element("small", { textContent: operation.schemas.length ? operation.schemas.join(" · ") : "No referenced schemas" }),
      );
      button.append(route, summary, element("span", { className: "operation-arrow", textContent: "→", attrs: { "aria-hidden": "true" } }));
      button.addEventListener("click", () => setSelected(operation.id, { reveal: true }));
      operationList.append(button);
    }
    section.append(groupNode, operationList);
    routeGroups.append(section);
  }

  byId("filter-summary").textContent = query
    ? `${shown.length} of ${model.operations.length} operations`
    : `${model.operations.length} operations`;
  byId("map-state").hidden = shown.length > 0;
  if (!shown.length) {
    const state = byId("map-state");
    replace(
      state,
      element("span", { className: "empty-mark", textContent: "0", attrs: { "aria-hidden": "true" } }),
      element("strong", { textContent: "No routes match this filter" }),
      element("p", { textContent: "Search by HTTP method, path, group, operation, or referenced schema." }),
    );
  }
  if (!shownIds.has(uiState.selectedId)) {
    uiState.selectedId = shown[0]?.id || null;
  }
  if (uiState.selectedId) setSelected(uiState.selectedId);
  else renderOperationDetail(null);
  if (restoreGroupHref) {
    const link = [...byId("group-links").querySelectorAll("a")]
      .find(candidate => candidate.getAttribute("href") === restoreGroupHref);
    (link || byId("route-search")).focus({ preventScroll: true });
  } else if (restoreOperationFocus) {
    const operation = document.querySelector(`.operation-node[data-operation-id="${CSS.escape(restoreOperationFocus)}"]`);
    (operation || byId("route-search")).focus({ preventScroll: true });
  } else if (restoreInspectorElement && !restoreInspectorElement.isConnected) {
    byId("operation-detail").querySelector("h2")?.focus({ preventScroll: true });
  }
}

function showLoadError(error) {
  uiState.model = null;
  uiState.selectedId = null;
  replace(byId("group-links"));
  replace(byId("route-groups"));
  byId("group-links").hidden = false;
  byId("route-groups").hidden = false;
  renderOperationDetail(null);
  byId("map-description").textContent = "The active OpenAPI contract is currently unavailable.";
  byId("api-root-title").textContent = "Schemii API";
  byId("api-root-version").textContent = "Contract unavailable";
  for (const id of ["path-count", "operation-count", "group-count", "schema-count"]) byId(id).textContent = "—";
  const state = byId("map-state");
  state.hidden = false;
  const retry = element("button", { textContent: "Retry contract load", attrs: { type: "button" } });
  retry.addEventListener("click", loadContract);
  replace(
    state,
    element("span", { className: "error-mark", textContent: "!", attrs: { "aria-hidden": "true" } }),
    element("strong", { textContent: "The active OpenAPI contract could not be loaded" }),
    element("p", { textContent: error instanceof Error ? error.message : "The request failed." }),
    retry,
  );
  byId("filter-summary").textContent = "Contract unavailable";
  setContractStatus("Load failed", true);
}

function setContractStatus(message, error = false) {
  const status = byId("contract-status");
  status.textContent = message;
  status.classList.toggle("error", error);
  if (error) byId("contract-alert").textContent = message;
  else byId("contract-alert").textContent = "";
}

function scheduleContractRefresh() {
  window.clearTimeout(uiState.refreshTimer);
  uiState.refreshTimer = null;
  if (document.visibilityState !== "visible") return;
  uiState.refreshTimer = window.setTimeout(loadContract, CONTRACT_REFRESH_INTERVAL);
}

function showRefreshError(error) {
  setContractStatus("Refresh failed · showing last contract", true);
  byId("contract-status").title = error instanceof Error ? error.message : "The refresh request failed";
  byId("group-links").hidden = false;
  byId("route-groups").hidden = false;
}

function applyContractMetadata(model) {
  document.title = `${model.title} API map`;
  byId("map-description").textContent = model.description;
  byId("api-root-title").textContent = model.title;
  byId("api-root-version").textContent = `Version ${model.version}`;
  byId("path-count").textContent = model.pathCount.toLocaleString();
  byId("operation-count").textContent = model.operations.length.toLocaleString();
  byId("group-count").textContent = model.groups.length.toLocaleString();
  byId("schema-count").textContent = model.schemaCount.toLocaleString();
}

async function loadContract() {
  if (uiState.loading) return;
  uiState.loading = true;
  window.clearTimeout(uiState.refreshTimer);
  uiState.refreshTimer = null;
  const refresh = byId("refresh-map");
  refresh.disabled = true;
  const initialLoad = !uiState.model;
  setContractStatus(initialLoad ? "Checking active contract" : "Checking for contract changes");
  const state = byId("map-state");
  if (initialLoad) {
    byId("filter-summary").textContent = "Loading routes";
    state.hidden = false;
    byId("group-links").hidden = true;
    byId("route-groups").hidden = true;
    replace(
      state,
      element("span", { className: "state-spinner", attrs: { "aria-hidden": "true" } }),
      element("strong", { textContent: "Loading the active API contract" }),
      element("p", { textContent: "Reading /openapi.json from this Schemii process." }),
    );
  }
  try {
    const controller = new AbortController();
    const requestTimeout = window.setTimeout(() => controller.abort(), CONTRACT_REQUEST_TIMEOUT);
    let specification;
    try {
      const response = await fetch("/openapi.json", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`OpenAPI request failed with HTTP ${response.status}`);
      specification = await response.json();
    } finally {
      window.clearTimeout(requestTimeout);
    }
    const fingerprint = JSON.stringify(specification);
    if (fingerprint !== uiState.contractFingerprint) {
      const activeElement = document.activeElement;
      const restoreOperationFocus = activeElement?.closest?.(".operation-node")?.dataset.operationId || null;
      const restoreGroupHref = activeElement?.closest?.("#group-links a")?.getAttribute("href") || null;
      const restoreInspectorElement = activeElement && byId("operation-detail").contains(activeElement)
        ? activeElement
        : null;
      const model = buildApiMapModel(specification);
      if (!model.operations.length) throw new Error("The OpenAPI document does not contain any operations");
      uiState.model = model;
      uiState.contractFingerprint = fingerprint;
      if (!model.operations.some(operation => operation.id === uiState.selectedId)) {
        uiState.selectedId = model.operations[0].id;
      }
      applyContractMetadata(model);
      renderMap({ restoreOperationFocus, restoreInspectorElement, restoreGroupHref });
    }
    const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    byId("contract-status").removeAttribute("title");
    setContractStatus(`Live · checked ${checkedAt}`);
  } catch (error) {
    const displayedError = error?.name === "AbortError"
      ? new Error("The OpenAPI request timed out after 10 seconds")
      : error;
    if (uiState.model) showRefreshError(displayedError);
    else showLoadError(displayedError);
  } finally {
    uiState.loading = false;
    refresh.disabled = false;
    scheduleContractRefresh();
  }
}

function start() {
  byId("route-search").addEventListener("input", event => {
    uiState.query = event.target.value.trim().toLowerCase();
    renderMap();
  });
  byId("refresh-map").addEventListener("click", loadContract);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadContract();
    else {
      window.clearTimeout(uiState.refreshTimer);
      uiState.refreshTimer = null;
    }
  });
  loadContract();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}
