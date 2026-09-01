import { DbGraph } from "./db-graph.js";
import {
  createIconButton,
  createIconElement,
  DockPane,
  initializeUi,
  renderStatePanel,
  setControlLoading,
} from "./ui.js";
import {
  codeBlock,
  normalizeInspectionObjects,
  pythonSourceExcerpt,
  sourceDefinitionCard,
  sourceDefinitionContent,
  sourceLocation,
  temporaryIconState,
} from "./source-inspection.js";

const REFRESH_INTERVAL = 30_000;
const REQUEST_TIMEOUT = 10_000;

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function humanize(value) {
  const normalized = text(value).replace(/^schemii_/, "").replace(/_query$/, "");
  return normalized.replaceAll("_", " ").replace(/^./, character => character.toUpperCase());
}

function element(tag, { className = "", textContent = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent) node.textContent = textContent;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  return node;
}

function replace(target, ...children) {
  target.replaceChildren(...children.filter(Boolean));
}

function normalizeRouteInspection(document) {
  const source = asRecord(document);
  if (source.schemaVersion !== 1) return [];
  const objects = normalizeInspectionObjects(source.objects);
  return array(source.routes).map(rawRoute => {
    const route = asRecord(rawRoute);
    const endpoint = objects.get(text(route.endpointId));
    if (!endpoint) return null;
    return {
      id: text(route.id),
      method: text(route.method).toUpperCase(),
      path: text(route.path),
      operationId: text(route.operationId),
      endpoint,
      calls: array(route.calls).map(rawCall => {
        const call = asRecord(rawCall);
        return {
          objectId: text(call.objectId),
          expression: text(call.expression),
          line: Number.isInteger(call.line) ? call.line : null,
        };
      }),
    };
  }).filter(route => route?.id);
}

function normalizeQuery(value) {
  const source = asRecord(value);
  const location = asRecord(source.location);
  return {
    id: text(source.id),
    name: text(source.name, "SQL query"),
    marker: text(source.marker),
    statement: text(source.statement, "SQL").toUpperCase(),
    placeholderCount: Number.isInteger(source.placeholderCount) ? source.placeholderCount : 0,
    resultColumns: unique(array(source.resultColumns).map(item => text(item))),
    catalogObjects: unique(array(source.catalogObjects).map(item => text(item))),
    location: {
      path: text(location.path),
      definitionLine: Number.isInteger(location.definitionLine) ? location.definitionLine : null,
      endLine: Number.isInteger(location.endLine) ? location.endLine : null,
    },
    sha256: text(source.sha256),
    sql: typeof source.sql === "string" ? source.sql : "",
    truncated: source.truncated === true,
  };
}

function reachableFor(rootId, callables) {
  const objectIds = new Set();
  const queryIds = new Set();
  const queued = [rootId];
  while (queued.length) {
    const objectId = queued.shift();
    if (objectIds.has(objectId)) continue;
    objectIds.add(objectId);
    const callable = callables.get(objectId);
    if (!callable) continue;
    callable.queryIds.forEach(queryId => queryIds.add(queryId));
    for (const call of callable.calls) {
      call.queries.forEach(query => queryIds.add(query.id));
      queued.push(call.object.id);
    }
  }
  return { objectIds, queryIds };
}

export function buildDbMapModel(databaseDocument, routeDocument = null) {
  const source = asRecord(databaseDocument);
  if (source.schemaVersion !== 1) throw new Error("The database inspection document has an unsupported schema version");
  const objects = normalizeInspectionObjects(source.objects);
  const queryList = array(source.queries).map(normalizeQuery).filter(query => query.id);
  const queries = new Map(queryList.map(query => [query.id, query]));
  const callables = new Map();
  for (const rawCallable of array(source.callables)) {
    const callable = asRecord(rawCallable);
    const object = objects.get(text(callable.objectId));
    if (!object) continue;
    const calls = array(callable.calls).map(rawCall => {
      const call = asRecord(rawCall);
      const calledObject = objects.get(text(call.objectId));
      if (!calledObject) return null;
      return {
        sequence: Number.isInteger(call.sequence) ? call.sequence : 0,
        expression: text(call.expression),
        line: Number.isInteger(call.line) ? call.line : null,
        resolution: text(call.resolution),
        object: calledObject,
        queries: array(call.queryIds).map(queryId => queries.get(text(queryId))).filter(Boolean),
      };
    }).filter(Boolean);
    callables.set(object.id, {
      object,
      depth: Number.isInteger(callable.depth) ? callable.depth : 0,
      calls,
      queryIds: unique(array(callable.queryIds).map(queryId => text(queryId))),
      inlineStatements: array(callable.inlineStatements).map(rawStatement => {
        const statement = asRecord(rawStatement);
        return {
          id: text(statement.id),
          statement: text(statement.statement, "SQL"),
          expression: text(statement.expression),
          line: Number.isInteger(statement.line) ? statement.line : null,
          readOnly: statement.readOnly === true,
          truncated: statement.truncated === true,
        };
      }),
      truncated: callable.truncated?.calls === true,
    });
  }

  const routes = normalizeRouteInspection(routeDocument);
  const operations = array(source.operations).map(rawOperation => {
    const operation = asRecord(rawOperation);
    const contract = objects.get(text(operation.contractObjectId));
    const implementation = objects.get(text(operation.implementationObjectId));
    if (!contract || !implementation) return null;
    const reachable = reachableFor(implementation.id, callables);
    const operationQueries = [...reachable.queryIds].map(queryId => queries.get(queryId)).filter(Boolean);
    const callers = routes.filter(route => route.calls.some(call => call.objectId === implementation.id));
    const reachableObjects = [...reachable.objectIds].map(objectId => objects.get(objectId)).filter(Boolean);
    const parameters = array(operation.parameters).map(rawParameter => {
      const parameter = asRecord(rawParameter);
      return {
        name: text(parameter.name),
        kind: text(parameter.kind),
        annotation: text(parameter.annotation, "Any"),
        required: parameter.required === true,
      };
    });
    const id = text(operation.id, implementation.name);
    const returnAnnotation = text(operation.returnAnnotation, "Any");
    const searchText = [
      id,
      implementation.qualname,
      contract.qualname,
      returnAnnotation,
      ...parameters.flatMap(parameter => [parameter.name, parameter.annotation]),
      ...operationQueries.flatMap(query => [query.name, query.marker, query.statement, ...query.catalogObjects]),
      ...reachableObjects.flatMap(object => [object.name, object.qualname, object.kind]),
      ...callers.flatMap(route => [route.method, route.path, route.operationId, route.endpoint.name, route.endpoint.docstring]),
    ].join(" ").toLocaleLowerCase();
    return {
      id,
      name: text(operation.name, implementation.name),
      title: humanize(text(operation.name, implementation.name)),
      contract,
      implementation,
      parameters,
      returnAnnotation,
      implementationDigest: text(operation.implementationDigest),
      callable: callables.get(implementation.id) || null,
      callers,
      queries: operationQueries,
      reachable,
      searchText,
    };
  }).filter(Boolean);
  if (!operations.length) throw new Error("The installed PostgreSQL gateway exposes no inspectable operations");

  const graphObjectIds = new Set();
  for (const callable of callables.values()) {
    graphObjectIds.add(callable.object.id);
  }
  operations.forEach(operation => {
    graphObjectIds.add(operation.implementation.id);
    operation.callable?.calls.forEach(call => graphObjectIds.add(call.object.id));
  });
  const graphObjects = [...graphObjectIds].map(objectId => objects.get(objectId)).filter(Boolean);
  const gateway = asRecord(source.gateway);
  return {
    serviceName: text(gateway.serviceName, "postgres"),
    contract: objects.get(text(gateway.contractObjectId)) || null,
    implementation: objects.get(text(gateway.implementationObjectId)) || null,
    operations,
    callables,
    queries: queryList,
    objects,
    graphObjects,
    graphObjectIds,
    callerCount: unique(operations.flatMap(operation => operation.callers.map(route => route.id))).length,
    analysis: asRecord(source.analysis),
  };
}

function byId(id) {
  return document.getElementById(id);
}

function parameterSignature(operation) {
  const parameters = operation.parameters.map(parameter => `${parameter.name}: ${parameter.annotation}`);
  return `${operation.name}(${parameters.join(", ")}) → ${operation.returnAnnotation}`;
}

function queryLabel(query) {
  return humanize(query.marker || query.name);
}

function queryDetail(query) {
  const content = element("section", { className: "db-query-detail" });
  const facts = element("dl", { className: "db-query-facts" });
  const fact = (label, value) => {
    const wrapper = element("div");
    wrapper.append(element("dt", { textContent: label }), element("dd", { textContent: value }));
    facts.append(wrapper);
  };
  fact("Statement", query.statement);
  fact("Parameters", String(query.placeholderCount));
  fact("Catalog sources", String(query.catalogObjects.length));
  fact("Fingerprint", query.sha256.slice(0, 12));
  content.append(facts);
  if (query.catalogObjects.length) {
    const sources = element("div", { className: "db-catalog-chips" });
    query.catalogObjects.forEach(name => sources.append(element("code", {
      textContent: name,
      attrs: {
        "data-ui-tooltip": `Read from ${name}`,
        "data-ui-tooltip-touch": "true",
      },
    })));
    content.append(sources);
  }
  content.append(codeBlock({
    text: query.sql,
    tokens: [],
    label: `${query.location.path}:${query.location.definitionLine || "?"}`,
    language: "sql",
  }));
  if (query.truncated) content.append(element("p", { className: "source-truncated", textContent: "SQL excerpt truncated by the inspection limit." }));
  return content;
}

function callSiteDetail(operation, call, model) {
  const content = element("div", { className: "db-call-detail" });
  const excerpt = pythonSourceExcerpt(operation.implementation, call.line);
  content.append(excerpt
    ? codeBlock({
      text: excerpt.text,
      tokens: excerpt.tokens,
      label: `${operation.implementation.location.path}:${excerpt.startLine}–${excerpt.endLine}`,
    })
    : codeBlock({ text: call.expression, tokens: [["plain", call.expression]], label: "Call expression" }));

  for (const query of call.queries) content.append(queryDetail(query));
  const callable = model.callables.get(call.object.id);
  if (callable?.inlineStatements.length) {
    const section = element("section", { className: "db-downstream-section" });
    section.append(element("h4", { textContent: "Inline database controls" }));
    callable.inlineStatements.forEach(statement => section.append(codeBlock({
      text: statement.expression,
      tokens: [["plain", statement.expression]],
      label: `${statement.statement} · ${call.object.location.path}:${statement.line || "?"}`,
    })));
    content.append(section);
  }
  if (callable?.calls.length) {
    const section = element("section", { className: "db-downstream-section" });
    section.append(element("h4", { textContent: "Direct downstream work" }));
    const list = element("ol", { className: "db-downstream-list" });
    callable.calls.forEach(child => {
      const item = element("li");
      item.append(
        element("strong", { textContent: child.object.qualname || child.object.name }),
        element("code", { textContent: child.expression }),
      );
      if (child.queries.length) item.append(element("small", { textContent: child.queries.map(queryLabel).join(" · ") }));
      list.append(item);
    });
    section.append(list);
    content.append(section);
  }
  content.append(sourceDefinitionCard(call.object));
  return content;
}

function callersDetail(operation) {
  const content = element("div", { className: "db-caller-list" });
  for (const route of operation.callers) {
    const card = element("article", { className: "db-caller-card" });
    const head = element("header");
    const identity = element("span");
    identity.append(
      element("strong", { textContent: `${route.method} ${route.path}` }),
      element("small", { textContent: route.endpoint.docstring || route.operationId }),
    );
    const apiLink = element("a", {
      className: "ui-button compact",
      textContent: "Open in API map",
      attrs: { href: `/api-map?operation=${encodeURIComponent(route.id)}` },
    });
    head.append(identity, apiLink);
    card.append(head, sourceDefinitionCard(route.endpoint));
    content.append(card);
  }
  return content;
}

function contractDetail(operation) {
  const content = element("div", { className: "db-contract-detail" });
  const signature = element("div", { className: "db-signature" });
  signature.append(element("code", { textContent: parameterSignature(operation) }));
  const parameters = element("dl", { className: "db-parameter-list" });
  operation.parameters.forEach(parameter => {
    const row = element("div");
    row.append(
      element("dt", { textContent: parameter.name }),
      element("dd", { textContent: parameter.annotation }),
      element("small", { textContent: parameter.required ? "required" : "optional" }),
    );
    parameters.append(row);
  });
  content.append(signature, parameters, sourceDefinitionContent(operation.contract));
  return content;
}

function stageIdentifier(call, index) {
  const label = (call.object.name || `call-${index + 1}`).toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `db-stage-call-${index + 1}-${label}`;
}

function stageLinkButton(operationId, stageId) {
  const button = createIconButton({
    icon: "link",
    label: "Copy link to this stage",
    placement: "left",
    className: "compact stage-link",
  });
  button.addEventListener("click", async event => {
    event.preventDefault();
    event.stopPropagation();
    const url = new URL(window.location.href);
    url.searchParams.set("operation", operationId);
    url.hash = stageId;
    const reset = () => button.classList.remove("copied", "copy-failed");
    try {
      await navigator.clipboard.writeText(url.toString());
      temporaryIconState(button, { icon: "check", label: "Stage link copied", className: "copied" }, reset);
    } catch {
      temporaryIconState(button, { icon: "close", label: "Copy failed", className: "copy-failed" }, reset, 2_000);
    }
  });
  return button;
}

function dbStage(operation, { kind, id, title, copy, meta = "", renderDetail, open = false }) {
  const stage = element("li", { className: `route-stage stage-${kind}`, attrs: { id } });
  const marker = element("span", { className: "stage-marker", attrs: { "aria-hidden": "true" } });
  const card = element("details", { className: "route-stage-card" });
  card.open = window.location.hash === `#${id}` || open;
  const summary = element("summary", { attrs: { tabindex: "0" } });
  const summaryCopy = element("span", { className: "stage-summary-copy" });
  const heading = element("span", { className: "stage-summary-heading" });
  heading.append(element("strong", { textContent: title }));
  if (meta) heading.append(element("code", {
    textContent: meta,
    attrs: {
      "data-ui-tooltip-overflow": meta,
      "data-ui-tooltip-touch": "true",
      "data-ui-tooltip-placement": "top",
    },
  }));
  summaryCopy.append(heading, element("p", { textContent: copy }));
  const actions = element("span", { className: "stage-summary-actions" });
  const disclosure = createIconElement("expand");
  disclosure.classList.add("stage-disclosure");
  actions.append(stageLinkButton(operation.id, id), disclosure);
  summary.append(summaryCopy, actions);
  card.append(summary);
  const render = () => {
    if (!card.open || card.dataset.stageRendered === "true") return;
    card.dataset.stageRendered = "true";
    const detail = element("div", { className: "stage-detail" });
    detail.append(renderDetail());
    card.append(detail);
  };
  if (card.open) render();
  card.addEventListener("toggle", render);
  stage.append(marker, card);
  return stage;
}

function callStageKind(call) {
  if (call.object.name === "_cleanup") return "cleanup";
  if (call.queries.length) return "query";
  if (call.object.kind === "outcome") return "outcome";
  if (call.object.kind === "model") return "response";
  return "helper";
}

function renderDbStory(operation, model) {
  const panel = element("section", { className: "route-story db-story" });
  const provenance = element("div", { className: "story-provenance" });
  provenance.append(
    element("span", { textContent: "Live Python + SQL inspection" }),
    element("small", { textContent: "Runtime-bound contract · recursive calls · static SQL constants" }),
  );
  const intent = element("section", { className: "story-intent" });
  intent.append(
    element("span", { className: "eyebrow", textContent: "Interface story" }),
    element("p", { textContent: `${parameterSignature(operation)}. Follow the source-derived sequence from server caller to PostgreSQL and back.` }),
  );
  panel.append(provenance, intent);
  const flow = element("ol", { className: "route-flow", attrs: { "aria-label": "Derived database call flow" } });
  if (operation.callers.length) {
    flow.append(dbStage(operation, {
      kind: "request",
      id: "db-stage-callers",
      title: "Server callers",
      copy: operation.callers.map(route => `${route.method} ${route.path}`).join(" · "),
      meta: `${operation.callers.length} registered route${operation.callers.length === 1 ? "" : "s"}`,
      renderDetail: () => callersDetail(operation),
    }));
  }
  flow.append(dbStage(operation, {
    kind: "dependency",
    id: "db-stage-contract",
    title: operation.contract.qualname || operation.contract.name,
    copy: "Define the server-to-database contract",
    meta: parameterSignature(operation),
    renderDetail: () => contractDetail(operation),
  }));
  flow.append(dbStage(operation, {
    kind: "gateway",
    id: "db-stage-implementation",
    title: operation.implementation.qualname || operation.implementation.name,
    copy: "Enter the runtime-bound PostgreSQL implementation",
    meta: sourceLocation(operation.implementation),
    renderDetail: () => sourceDefinitionContent(operation.implementation),
  }));
  operation.callable?.calls.forEach((call, index) => {
    const queryNames = call.queries.map(queryLabel);
    const kind = callStageKind(call);
    const copy = queryNames.length
      ? `Execute ${queryNames.join(" · ")}`
      : call.object.kind === "outcome" ? "Possible guarded outcome"
        : call.object.kind === "model" ? `Construct ${call.object.name}`
          : `Direct call inferred at line ${call.line || "?"}`;
    flow.append(dbStage(operation, {
      kind,
      id: stageIdentifier(call, index),
      title: call.object.qualname || call.object.name,
      copy,
      meta: queryNames.join(" · ") || call.expression,
      renderDetail: () => callSiteDetail(operation, call, model),
    }));
  });
  flow.append(dbStage(operation, {
    kind: "response",
    id: "db-stage-result",
    title: `Return ${operation.returnAnnotation}`,
    copy: "Hand the validated result back to the server caller",
    meta: `${operation.queries.length} SQL statement${operation.queries.length === 1 ? "" : "s"} reachable`,
    renderDetail: () => {
      const content = element("div", { className: "db-result-detail" });
      content.append(
        element("p", { textContent: `The contract returns ${operation.returnAnnotation}. The implementation fingerprint includes every reachable inspected callable and SQL constant.` }),
        sourceDefinitionCard(operation.contract),
      );
      return content;
    },
  }));
  panel.append(flow);
  const notes = element("footer", { className: "story-notes" });
  notes.append(element("p", {
    className: "implementation-fingerprint",
    textContent: `Implementation fingerprint ${operation.implementationDigest.slice(0, 12)}`,
  }));
  panel.append(notes);
  return panel;
}

const uiState = {
  model: null,
  query: "",
  selectedId: null,
  view: "list",
  fingerprint: null,
  loading: false,
  refreshTimer: null,
  canvasFitted: false,
};

let operationPane = null;
let dbGraph = null;

function visibleOperations() {
  if (!uiState.model) return [];
  if (!uiState.query) return uiState.model.operations;
  return uiState.model.operations.filter(operation => operation.searchText.includes(uiState.query));
}

function updateUrlSelection(operationId) {
  const url = new URL(window.location.href);
  if (operationId) url.searchParams.set("operation", operationId);
  else url.searchParams.delete("operation");
  window.history.replaceState(null, "", url);
}

function setSelected(operationId, { reveal = false } = {}) {
  const operation = uiState.model?.operations.find(item => item.id === operationId) || null;
  uiState.selectedId = operation?.id || null;
  for (const button of document.querySelectorAll(".operation-node")) {
    const selected = button.dataset.operationId === uiState.selectedId;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  }
  dbGraph?.setSelectedOperation(uiState.selectedId);
  renderOperationDetail(operation);
  updateUrlSelection(uiState.selectedId);
  if (reveal && operation) operationPane?.reveal();
}

function renderOperationDetail(operation) {
  const empty = byId("db-operation-empty");
  const detail = byId("db-operation-detail");
  if (!operation) {
    empty.hidden = false;
    detail.hidden = true;
    byId("db-operation-pane-title").textContent = "Select an operation";
    operationPane?.setAvailable(false, { reset: true });
    replace(detail);
    return;
  }
  operationPane?.setAvailable(true);
  empty.hidden = true;
  detail.hidden = false;
  byId("db-operation-pane-title").textContent = operation.title;
  const header = element("header", { className: "detail-head" });
  const identity = element("div");
  identity.append(
    element("span", { className: "eyebrow", textContent: `${uiState.model.serviceName} gateway operation` }),
    element("h2", { textContent: operation.title, attrs: { id: "selected-db-operation-title", tabindex: "-1" } }),
    element("code", {
      className: "db-detail-signature",
      textContent: parameterSignature(operation),
      attrs: {
        "data-ui-tooltip-overflow": parameterSignature(operation),
        "data-ui-tooltip-touch": "true",
      },
    }),
  );
  const actions = element("div", { className: "detail-actions ui-action-group" });
  const returnButton = createIconButton({ icon: "earlier", label: "Back to operations", placement: "left", className: "compact return-routes" });
  returnButton.addEventListener("click", () => {
    if (uiState.view === "canvas" && dbGraph?.focusOperation(operation.id)) return;
    document.querySelector(`.operation-node[data-operation-id="${CSS.escape(operation.id)}"]`)?.focus({ preventScroll: true });
  });
  actions.append(returnButton, element("a", {
    className: "ui-button compact",
    textContent: "Inspection JSON",
    attrs: { href: "/_developer/database" },
  }));
  header.append(identity, actions);
  const body = element("div", { className: "detail-body" });
  body.append(renderDbStory(operation, uiState.model));
  replace(detail, header, body);
  window.setTimeout(() => synchronizeLinkedStage(), 0);
}

function synchronizeLinkedStage() {
  const hash = window.location.hash.startsWith("#db-stage-") ? window.location.hash : "";
  if (!hash) return;
  const stage = document.querySelector(hash);
  const card = stage?.querySelector(":scope > .route-stage-card");
  if (!card) return;
  card.open = true;
  stage.scrollIntoView({ block: "start" });
}

function setMapView(view) {
  if (!["list", "canvas"].includes(view)) return;
  uiState.view = view;
  const canvasActive = view === "canvas";
  byId("list-view-button").setAttribute("aria-pressed", canvasActive ? "false" : "true");
  byId("canvas-view-button").setAttribute("aria-pressed", canvasActive ? "true" : "false");
  byId("db-canvas-tools").hidden = !canvasActive;
  byId("db-graph-title").textContent = canvasActive ? "Python calls, SQL, and pg_catalog" : "Gateway operations";
  const hasVisible = visibleOperations().length > 0;
  byId("operation-groups").hidden = canvasActive || !hasVisible;
  byId("db-canvas").hidden = !canvasActive || !hasVisible;
  dbGraph?.setActive(canvasActive && hasVisible);
  if (canvasActive && hasVisible) window.requestAnimationFrame(() => {
    dbGraph?.refreshGeometry();
    if (!uiState.canvasFitted) uiState.canvasFitted = Boolean(dbGraph?.fitSelection() || dbGraph?.fit());
  });
}

function chooseMapView(view) {
  setMapView(view);
  const url = new URL(window.location.href);
  if (view === "canvas") url.searchParams.set("view", "canvas");
  else url.searchParams.delete("view");
  window.history.replaceState(null, "", url);
}

function renderMap() {
  if (!uiState.model) return;
  const shown = visibleOperations();
  const shownIds = new Set(shown.map(operation => operation.id));
  const groups = byId("operation-groups");
  replace(groups);
  if (shown.length) {
    const section = element("section", { className: "route-group db-interface-group" });
    const group = element("header", { className: "group-node" });
    group.append(
      element("span", { className: "group-glyph", textContent: "DB", attrs: { "aria-hidden": "true" } }),
      element("span", { className: "eyebrow", textContent: "Runtime-bound protocol" }),
      element("h3", { textContent: uiState.model.contract?.qualname || "PostgresGateway" }),
      element("p", { textContent: `${uiState.model.implementation?.qualname || "Installed implementation"} · source-derived operations and SQL.` }),
    );
    const list = element("div", { className: "operation-list" });
    for (const operation of shown) {
      const button = element("button", {
        className: `operation-node db-operation-node${operation.id === uiState.selectedId ? " selected" : ""}`,
        attrs: {
          type: "button",
          "data-operation-id": operation.id,
          "aria-pressed": operation.id === uiState.selectedId ? "true" : "false",
        },
      });
      const route = element("span", { className: "operation-route" });
      route.append(
        element("span", { className: "db-method-badge", textContent: "DB" }),
        element("code", {
          textContent: operation.name,
          attrs: {
            "data-ui-tooltip-overflow": operation.name,
            "data-ui-tooltip-touch": "true",
          },
        }),
      );
      const copy = element("span", { className: "operation-copy" });
      const resultSummary = `${operation.parameters.length} inputs → ${operation.returnAnnotation}`;
      const reachSummary = `${operation.queries.length} SQL · ${operation.callers.length} API caller${operation.callers.length === 1 ? "" : "s"}`;
      copy.append(
        element("strong", {
          textContent: resultSummary,
          attrs: {
            "data-ui-tooltip-overflow": resultSummary,
            "data-ui-tooltip-touch": "true",
          },
        }),
        element("small", {
          textContent: reachSummary,
          attrs: {
            "data-ui-tooltip-overflow": reachSummary,
            "data-ui-tooltip-touch": "true",
          },
        }),
      );
      const arrow = createIconElement("later");
      arrow.classList.add("operation-arrow");
      button.append(route, copy, arrow);
      button.dataset.uiTooltip = `Inspect ${operation.name}`;
      button.addEventListener("click", () => setSelected(operation.id, { reveal: true }));
      list.append(button);
    }
    section.append(group, list);
    groups.append(section);
  }
  const state = byId("map-state");
  state.hidden = shown.length > 0;
  if (!shown.length) renderStatePanel(state, {
    mark: "0",
    title: "No database calls match this filter",
    message: "Search by operation, route, helper, query constant, result type, or pg_catalog object.",
  });
  byId("filter-summary").textContent = uiState.query
    ? `${shown.length} of ${uiState.model.operations.length} operations`
    : `${uiState.model.operations.length} operations`;
  if (!shownIds.has(uiState.selectedId)) uiState.selectedId = shown[0]?.id || null;
  dbGraph?.setVisibleOperations(shownIds, { filtering: Boolean(uiState.query) });
  setSelected(uiState.selectedId);
  setMapView(uiState.view);
}

function applyMetadata(model) {
  document.title = `${model.implementation?.name || "PostgreSQL"} DB call map`;
  byId("map-description").textContent = `${model.contract?.qualname || "PostgresGateway"} → ${model.implementation?.qualname || "runtime implementation"}`;
  byId("operation-count").textContent = model.operations.length.toLocaleString();
  byId("callable-count").textContent = model.callables.size.toLocaleString();
  byId("query-count").textContent = model.queries.length.toLocaleString();
  byId("caller-count").textContent = model.callerCount.toLocaleString();
}

function setInspectionStatus(message, error = false) {
  const status = byId("inspection-status");
  status.textContent = message;
  status.classList.toggle("error", error);
  byId("inspection-alert").textContent = error ? message : "";
}

function showLoadError(error) {
  uiState.model = null;
  dbGraph?.clear();
  replace(byId("operation-groups"));
  byId("db-canvas").hidden = true;
  renderOperationDetail(null);
  for (const id of ["operation-count", "callable-count", "query-count", "caller-count"]) byId(id).textContent = "—";
  byId("map-description").textContent = "The installed database inspection is unavailable.";
  const state = byId("map-state");
  state.hidden = false;
  const retry = createIconButton({ icon: "refresh", label: "Retry database inspection", placement: "bottom", className: "compact" });
  retry.addEventListener("click", loadMap);
  renderStatePanel(state, {
    mark: "!",
    title: "The database call map could not be derived",
    message: error instanceof Error ? error.message : "The inspection request failed.",
    variant: "error",
    action: retry,
  });
  byId("filter-summary").textContent = "Inspection unavailable";
  setInspectionStatus("Load failed", true);
}

async function fetchInspection(path, signal, { optional = false } = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  if (optional && response.status === 404) return null;
  if (response.status === 404) throw new Error("Developer inspection is disabled in this Schemii process");
  if (!response.ok) throw new Error(`Inspection request failed with HTTP ${response.status}`);
  return response.json();
}

function scheduleRefresh() {
  window.clearTimeout(uiState.refreshTimer);
  uiState.refreshTimer = null;
  if (document.visibilityState === "visible") uiState.refreshTimer = window.setTimeout(loadMap, REFRESH_INTERVAL);
}

async function loadMap() {
  if (uiState.loading) return;
  uiState.loading = true;
  window.clearTimeout(uiState.refreshTimer);
  const refresh = byId("refresh-map");
  setControlLoading(refresh, true, { loadingLabel: "Refreshing database inspection" });
  const initial = !uiState.model;
  if (initial) {
    byId("operation-groups").hidden = true;
    const state = byId("map-state");
    state.hidden = false;
    renderStatePanel(state, {
      mark: "…",
      title: "Loading the installed database interface",
      message: "Deriving the gateway contract, runtime calls, SQL, and API callers from this Schemii process.",
      variant: "loading",
    });
  }
  setInspectionStatus(initial ? "Reading installed source" : "Checking for source changes");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    const [databaseDocument, routeDocument] = await Promise.all([
      fetchInspection("/_developer/database", controller.signal),
      fetchInspection("/_developer/routes", controller.signal, { optional: true }),
    ]);
    const fingerprint = JSON.stringify([databaseDocument, routeDocument]);
    if (fingerprint !== uiState.fingerprint) {
      const model = buildDbMapModel(databaseDocument, routeDocument);
      uiState.model = model;
      dbGraph?.setModel(model);
      const requested = new URLSearchParams(window.location.search).get("operation");
      if (!model.operations.some(operation => operation.id === uiState.selectedId)) {
        uiState.selectedId = model.operations.some(operation => operation.id === requested)
          ? requested
          : model.operations[0].id;
      }
      applyMetadata(model);
      renderMap();
      uiState.fingerprint = fingerprint;
    }
    const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    setInspectionStatus(`Live source · checked ${checkedAt}`);
  } catch (error) {
    const displayed = error?.name === "AbortError" ? new Error("The inspection request timed out after 10 seconds") : error;
    if (uiState.model) setInspectionStatus("Refresh failed · showing last inspection", true);
    else showLoadError(displayed);
  } finally {
    window.clearTimeout(timeout);
    uiState.loading = false;
    setControlLoading(refresh, false);
    scheduleRefresh();
  }
}

function start() {
  initializeUi();
  operationPane = new DockPane({
    container: byId("db-map-workspace"),
    pane: byId("db-operation-inspector"),
    body: byId("db-operation-inspector-body"),
    toggle: byId("db-operation-inspector-toggle"),
    dismiss: byId("db-operation-inspector-close"),
    side: "right",
    expandedLabel: "Minimize database call inspector",
    minimizedLabel: "Expand database call inspector",
    getRestoreFocusTarget: () => {
      const selector = uiState.view === "canvas" ? ".db-canvas-node" : ".operation-node";
      return document.querySelector(`${selector}[data-operation-id="${CSS.escape(uiState.selectedId || "")}"]`) || byId("operation-search");
    },
    onStateChange: () => dbGraph?.refreshGeometry(),
  });
  operationPane.setAvailable(false);
  uiState.view = new URLSearchParams(window.location.search).get("view") === "canvas" ? "canvas" : "list";
  dbGraph = new DbGraph({
    host: byId("db-canvas"),
    stage: byId("db-canvas-stage"),
    nodeLayer: byId("db-canvas-nodes"),
    lines: byId("db-canvas-lines"),
    zoomOutput: byId("db-canvas-zoom"),
    onSelectOperation: operationId => setSelected(operationId, { reveal: true }),
  });
  byId("operation-search").addEventListener("input", event => {
    uiState.query = event.target.value.trim().toLocaleLowerCase();
    renderMap();
  });
  byId("refresh-map").addEventListener("click", loadMap);
  byId("list-view-button").addEventListener("click", () => chooseMapView("list"));
  byId("canvas-view-button").addEventListener("click", () => chooseMapView("canvas"));
  byId("db-canvas-fit").addEventListener("click", () => {
    if (dbGraph.fit()) uiState.canvasFitted = true;
  });
  byId("db-canvas-zoom-in").addEventListener("click", () => dbGraph.zoomBy(0.1));
  byId("db-canvas-zoom-out").addEventListener("click", () => dbGraph.zoomBy(-0.1));
  window.addEventListener("resize", () => {
    if (uiState.view === "canvas") dbGraph.refreshGeometry();
  });
  window.addEventListener("hashchange", synchronizeLinkedStage);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadMap();
    else window.clearTimeout(uiState.refreshTimer);
  });
  setMapView(uiState.view);
  loadMap();
}

if (typeof document !== "undefined" && document.getElementById("db-map-workspace")) start();
