import { buildApiMapModel, contractJsonShape } from "./api-map.js";
import { buildDbMapModel } from "./db-map.js";
import {
  codeBlock,
  normalizeInspectionObjects,
  pythonSourceExcerpt,
  sourceDefinitionContent,
  sourceLocation,
} from "./source-inspection.js";
import {
  createIconButton,
  initializeUi,
  renderStatePanel,
  setControlLoading,
} from "./ui.js";

const REQUEST_TIMEOUT = 10_000;
const REFRESH_INTERVAL = 30_000;
const LENSES = new Set(["e2e", "api", "internals", "database"]);
const JOURNEY_STAGE_IDS = ["api", "internals", "database", "response"];
const ROUTE_OWNER_GROUPS = [
  { id: "owner:common", label: "Common", description: "schemii.common", entries: [] },
  { id: "owner:schemii", label: "Schemii", description: "schemii.schemii", entries: [] },
  { id: "owner:schemoo", label: "Schemoo", description: "schemii.schemoo", entries: [] },
  { id: "owner:schemer", label: "Schemer", description: "schemii.schemer", entries: [] },
];

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function element(tag, { className = "", textContent = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent) node.textContent = textContent;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  return node;
}

function tooltipOverflow(node, value) {
  if (!value) return node;
  node.dataset.uiTooltipOverflow = value;
  node.dataset.uiTooltipTouch = "true";
  return node;
}

function normalizeContexts(values) {
  return array(values).map(rawContext => {
    const context = asRecord(rawContext);
    return {
      kind: text(context.kind, "source"),
      label: text(context.label, "source region"),
      line: Number.isInteger(context.line) ? context.line : null,
    };
  });
}

function normalizeSignature(rawSignature, objects) {
  const signature = asRecord(rawSignature);
  return {
    available: signature.available === true,
    parameters: array(signature.parameters).map(rawParameter => {
      const parameter = asRecord(rawParameter);
      return {
        name: text(parameter.name),
        kind: text(parameter.kind),
        annotation: text(parameter.annotation, "Any"),
        required: parameter.required === true,
        objects: array(parameter.objectIds).map(value => objects.get(text(value))).filter(Boolean),
      };
    }).filter(parameter => parameter.name),
    returnAnnotation: text(signature.returnAnnotation, "Any"),
    returnObjects: array(signature.returnObjectIds).map(value => objects.get(text(value))).filter(Boolean),
  };
}

function normalizeJourney(rawJourney, objects) {
  const journey = asRecord(rawJourney);
  const nodes = array(journey.nodes).map(rawNode => {
    const node = asRecord(rawNode);
    const object = objects.get(text(node.objectId));
    const stage = text(node.stage);
    if (!object || !JOURNEY_STAGE_IDS.includes(stage)) return null;
    const evidence = asRecord(node.evidence);
    return {
      key: text(node.key),
      object,
      parentKey: text(node.parentKey) || null,
      depth: Number.isInteger(node.depth) ? node.depth : 0,
      stage,
      role: text(node.role, "source-call"),
      provenance: text(node.provenance, "derived"),
      evidence: {
        kind: text(evidence.kind, "source-call"),
        resolution: text(evidence.resolution, "source"),
        line: Number.isInteger(evidence.line) ? evidence.line : null,
      },
    };
  }).filter(node => node?.key);
  const nodeByKey = new Map(nodes.map(node => [node.key, node]));
  const transitions = array(journey.transitions).map(rawTransition => {
    const transition = asRecord(rawTransition);
    const fromKey = text(transition.fromKey);
    const toKey = text(transition.toKey);
    return fromKey && toKey && nodeByKey.has(fromKey) && nodeByKey.has(toKey) ? {
      fromKey,
      toKey,
      fromStage: text(transition.fromStage),
      toStage: text(transition.toStage),
      provenance: text(transition.provenance, "derived"),
      evidence: asRecord(transition.evidence),
    } : null;
  }).filter(Boolean);
  return {
    status: text(journey.status, "unresolved"),
    nodes,
    nodeByKey,
    transitions,
    issues: array(journey.issues).map(issue => asRecord(issue)),
  };
}

function normalizeSystemDocument(document) {
  const source = asRecord(document);
  if (source.schemaVersion !== 1) throw new Error("The system inspection document has an unsupported schema version");
  const objects = normalizeInspectionObjects(source.objects);
  const callables = new Map();
  for (const rawCallable of array(source.callables)) {
    const callable = asRecord(rawCallable);
    const objectId = text(callable.objectId);
    const object = objects.get(objectId);
    if (!object) continue;
    callables.set(objectId, {
      object,
      signature: normalizeSignature(callable.signature, objects),
      calls: array(callable.calls).map(rawCall => {
        const call = asRecord(rawCall);
        const target = objects.get(text(call.objectId));
        if (!target) return null;
        return {
          sequence: Number.isInteger(call.sequence) ? call.sequence : 0,
          expression: text(call.expression, target.name),
          object: target,
          resolution: text(call.resolution, "source"),
          line: Number.isInteger(call.line) ? call.line : null,
          endLine: Number.isInteger(call.endLine) ? call.endLine : null,
          contexts: normalizeContexts(call.contexts),
          outcome: call.outcome === true || target.kind === "outcome",
          statusCode: Number.isInteger(call.statusCode) ? call.statusCode : null,
          code: text(call.code),
          arguments: array(call.arguments).map(rawArgument => {
            const argument = asRecord(rawArgument);
            return {
              parameter: text(argument.parameter, "argument"),
              annotation: text(argument.annotation, "Any"),
              expression: text(argument.expression, "value"),
              kind: text(argument.kind, "positional"),
            };
          }),
          targetSignature: normalizeSignature(call.targetSignature, objects),
          repeatCount: 1,
          lines: Number.isInteger(call.line) ? [call.line] : [],
        };
      }).filter(Boolean),
      truncated: callable.truncated?.calls === true,
    });
  }
  const routes = array(source.routes).map(rawRoute => {
    const route = asRecord(rawRoute);
    const endpoint = objects.get(text(route.endpointObjectId));
    if (!endpoint) return null;
    return {
      id: text(route.id),
      method: text(route.method, "get"),
      path: text(route.path),
      operationId: text(route.operationId),
      endpoint,
      dependencies: array(route.dependencies).map(rawDependency => {
        const dependency = asRecord(rawDependency);
        const object = objects.get(text(dependency.objectId));
        return object ? {
          parameterName: text(dependency.parameterName),
          object,
          useCache: dependency.useCache !== false,
          resultObjects: array(dependency.resultObjectIds).map(value => objects.get(text(value))).filter(Boolean),
        } : null;
      }).filter(Boolean),
      rootObjectIds: unique(array(route.rootObjectIds).map(value => text(value))),
      request: {
        bodyObjects: array(route.request?.bodyObjectIds).map(value => objects.get(text(value))).filter(Boolean),
        parameters: array(route.request?.parameters).map(rawParameter => {
          const parameter = asRecord(rawParameter);
          return {
            name: text(parameter.name),
            location: text(parameter.location),
            required: parameter.required === true,
          };
        }).filter(parameter => parameter.name),
      },
      response: {
        statusCode: Number.isInteger(route.response?.statusCode) ? route.response.statusCode : null,
        objects: array(route.response?.objectIds).map(value => objects.get(text(value))).filter(Boolean),
      },
      journey: normalizeJourney(route.journey, objects),
      implementationDigest: text(route.implementationDigest),
    };
  }).filter(route => route?.id);
  const services = array(source.services).map(rawService => {
    const service = asRecord(rawService);
    const implementation = objects.get(text(service.implementationObjectId));
    if (!implementation) return null;
    return {
      id: implementation.id,
      name: text(service.name, implementation.name),
      implementation,
      contracts: array(service.contractObjectIds).map(value => objects.get(text(value))).filter(Boolean),
      methods: array(service.methodObjectIds).map(value => objects.get(text(value))).filter(Boolean),
    };
  }).filter(Boolean);
  const bindings = array(source.bindings).map(rawBinding => {
    const binding = asRecord(rawBinding);
    const owner = objects.get(text(binding.ownerObjectId));
    if (!owner) return null;
    return {
      owner,
      attribute: text(binding.attribute),
      path: text(binding.path),
      contracts: array(binding.contractObjectIds).map(value => objects.get(text(value))).filter(Boolean),
      implementations: array(binding.implementationObjectIds).map(value => objects.get(text(value))).filter(Boolean),
    };
  }).filter(Boolean);
  return { objects, callables, routes, services, bindings, analysis: asRecord(source.analysis) };
}

function mergeObjects(...maps) {
  const merged = new Map();
  for (const map of maps) {
    for (const [id, object] of map) merged.set(id, object);
  }
  return merged;
}

function componentEntries(system) {
  const candidates = new Map();
  for (const service of system.services) candidates.set(service.implementation.id, service);
  for (const binding of system.bindings) {
    for (const implementation of binding.implementations) {
      if (!candidates.has(implementation.id)) {
        candidates.set(implementation.id, {
          id: implementation.id,
          name: implementation.name,
          implementation,
          contracts: binding.contracts,
          methods: [],
        });
      }
    }
  }
  for (const component of candidates.values()) {
    if (component.methods.length) continue;
    const prefix = `${component.implementation.qualname}.`;
    component.methods = [...system.callables.values()]
      .map(callable => callable.object)
      .filter(object => object.qualname.startsWith(prefix) && !object.name.startsWith("_"));
  }
  return [...candidates.values()]
    .filter(component => component.methods.length)
    .sort((left, right) => left.implementation.name.localeCompare(right.implementation.name));
}

function databaseAttachments(database) {
  const byObjectId = new Map();
  for (const callable of database.callables.values()) {
    const queries = unique([
      ...callable.queryIds,
      ...callable.calls.flatMap(call => call.queries.map(query => query.id)),
    ]).map(queryId => database.queries.find(query => query.id === queryId)).filter(Boolean);
    byObjectId.set(callable.object.id, {
      queries,
      statements: callable.inlineStatements,
    });
  }
  return byObjectId;
}

function groupLabel(value) {
  return humanizeIdentifier(text(value).replace(/[-_]+/g, " "))
    .replace(/\bApi\b/g, "API")
    .replace(/\bAi\b/g, "AI")
    .replace(/\bOauth\b/g, "OAuth")
    .replace(/\bSql\b/g, "SQL")
    .replace(/\bPostgres\b/g, "PostgreSQL");
}

function sourceModuleArea(module, fallback) {
  const parts = text(module).split(".").filter(Boolean);
  const ownershipIndex = parts.findIndex((part, index) => (
    part === "common" || (part === "schemii" && index > 0)
  ));
  const area = ownershipIndex >= 0 ? parts[ownershipIndex + 1] : parts.at(-2);
  const source = text(area, fallback);
  return {
    id: source.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "other",
    label: groupLabel(source),
    description: text(module),
  };
}

function routeOwnership(route) {
  const ownerIds = new Set(ROUTE_OWNER_GROUPS.map(group => group.id.replace("owner:", "")));
  const moduleParts = text(route.endpoint.module).split(".").filter(Boolean);
  let owner = ownerIds.has(moduleParts[1]) ? moduleParts[1] : "";
  if (!owner) {
    const pathOwner = route.path.match(/^\/api\/v1\/(schemii|schemoo|schemer)(?:\/|$)/)?.[1];
    owner = pathOwner || "common";
  }
  const group = ROUTE_OWNER_GROUPS.find(candidate => candidate.id === `owner:${owner}`)
    || ROUTE_OWNER_GROUPS[0];
  return {
    id: group.id,
    label: group.label,
    description: group.description,
  };
}

function routeFeature(route, owner) {
  const tag = array(route.contract?.tags)
    .map(value => text(value).toLocaleLowerCase())
    .find(value => value && !["ungrouped", owner].includes(value));
  if (tag) {
    const normalized = tag
      .replace(new RegExp(`^${owner}-`), "")
      .replace(/-planned$/, "");
    if (normalized) return groupLabel(normalized);
  }
  const area = sourceModuleArea(route.endpoint.module, "routes");
  return area.id === owner ? "Routes" : area.label;
}

function sourceEntryGroup(object, fallback) {
  const area = sourceModuleArea(object.module, fallback);
  return { ...area, id: `module:${area.id}` };
}

export function buildSystemMapModel(
  systemDocument,
  routeDocument,
  databaseDocument,
  openapiDocument,
) {
  const system = normalizeSystemDocument(systemDocument);
  const api = buildApiMapModel(openapiDocument, routeDocument);
  const database = buildDbMapModel(databaseDocument, routeDocument);
  const routeObjects = normalizeInspectionObjects(asRecord(routeDocument).objects);
  const objects = mergeObjects(system.objects, routeObjects, database.objects);
  const apiById = new Map(api.operations.map(operation => [operation.id, operation]));
  const routes = system.routes.map(route => ({
    ...route,
    contract: apiById.get(route.id) || null,
  }));
  return {
    system,
    api,
    database,
    objects,
    routes,
    routesById: new Map(routes.map(route => [route.id, route])),
    components: componentEntries(system),
    databaseAttachments: databaseAttachments(database),
  };
}

export function entriesForLens(model, lens) {
  if (lens === "database") {
    return model.database.operations.map(operation => {
      const group = sourceEntryGroup(operation.implementation, "database");
      return {
        id: operation.id,
        type: "database",
        title: operation.title,
        subtitle: operation.implementation.qualname,
        glyph: "SQL",
        roots: [operation.implementation.id],
        operation,
        groupId: group.id,
        groupLabel: group.label,
        groupDescription: group.description,
      };
    });
  }
  if (lens === "internals") {
    return model.components.map(component => {
      const group = sourceEntryGroup(component.implementation, component.implementation.kind);
      return {
        id: component.id,
        type: "component",
        title: component.implementation.name,
        subtitle: component.contracts.map(contract => contract.name).join(" · ") || component.implementation.kind,
        glyph: component.implementation.kind.includes("gateway") ? "DB" : component.implementation.kind.includes("repository") ? "REPO" : "SVC",
        roots: component.methods.map(method => method.id),
        component,
        groupId: group.id,
        groupLabel: group.label,
        groupDescription: group.description,
      };
    });
  }
  return model.routes.map(route => {
    const group = routeOwnership(route);
    const owner = group.id.replace("owner:", "");
    return {
      id: route.id,
      type: "route",
      title: route.contract?.summary || humanizeIdentifier(route.endpoint.name),
      subtitle: `${route.method.toUpperCase()} ${route.path}`,
      path: route.path,
      method: route.method,
      lifecycle: route.contract?.lifecycle || "implemented",
      roots: route.rootObjectIds,
      route,
      groupId: group.id,
      groupLabel: group.label,
      groupDescription: group.description,
      featureLabel: routeFeature(route, owner),
    };
  });
}

function entrySearchText(entry) {
  if (entry.type === "route") {
    return [
      entry.id,
      entry.title,
      entry.subtitle,
      entry.method,
      entry.path,
      entry.lifecycle,
      entry.groupLabel,
      entry.featureLabel,
      entry.route.operationId,
      entry.route.endpoint.name,
      entry.route.endpoint.qualname,
      entry.route.endpoint.module,
      entry.route.endpoint.docstring,
      ...array(entry.route.contract?.tags),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
  }
  if (entry.type === "component") {
    return [
      entry.id,
      entry.title,
      entry.subtitle,
      entry.groupLabel,
      entry.component.implementation.qualname,
      entry.component.implementation.module,
      ...entry.component.contracts.flatMap(contract => [contract.name, contract.qualname]),
      ...entry.component.methods.flatMap(method => [method.name, method.qualname, method.docstring]),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
  }
  return [
    entry.id,
    entry.title,
    entry.subtitle,
    entry.groupLabel,
    entry.operation.contract.qualname,
    entry.operation.implementation.qualname,
    entry.operation.implementation.module,
    entry.operation.returnAnnotation,
    ...entry.operation.parameters.flatMap(parameter => [parameter.name, parameter.annotation]),
    ...entry.operation.queries.flatMap(query => [query.name, query.marker, query.statement, query.sql]),
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function buildEntryCatalog(entries, {
  query = "",
  groupId = "all",
  includeEmptyRouteOwners = false,
} = {}) {
  const allGroups = new Map();
  if (includeEmptyRouteOwners) {
    for (const group of ROUTE_OWNER_GROUPS) {
      allGroups.set(group.id, { ...group, entries: [] });
    }
  }
  for (const entry of entries) {
    if (!allGroups.has(entry.groupId)) {
      allGroups.set(entry.groupId, {
        id: entry.groupId,
        label: entry.groupLabel,
        description: entry.groupDescription,
        entries: [],
      });
    }
    allGroups.get(entry.groupId).entries.push(entry);
  }
  const ownerOrder = new Map(ROUTE_OWNER_GROUPS.map((group, index) => [group.id, index]));
  const availableGroups = [...allGroups.values()].sort((left, right) => {
    const leftOrder = ownerOrder.get(left.id);
    const rightOrder = ownerOrder.get(right.id);
    if (leftOrder !== undefined || rightOrder !== undefined) {
      return (leftOrder ?? ROUTE_OWNER_GROUPS.length) - (rightOrder ?? ROUTE_OWNER_GROUPS.length);
    }
    return left.label.localeCompare(right.label);
  });
  const normalizedQuery = text(query).toLocaleLowerCase();
  const groups = availableGroups.map(group => ({
    ...group,
    entries: group.entries.filter(entry => (
      (groupId === "all" || group.id === groupId)
      && (!normalizedQuery || entrySearchText(entry).includes(normalizedQuery))
    )),
  })).filter(group => group.entries.length);
  return {
    groups,
    availableGroups,
    totalCount: entries.length,
    visibleCount: groups.reduce((count, group) => count + group.entries.length, 0),
  };
}

export function routeFeatureGroups(entries) {
  const groups = new Map();
  for (const entry of entries) {
    const label = text(entry.featureLabel, "Other");
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(entry);
  }
  return [...groups.entries()].map(([label, featureEntries]) => ({
    id: label.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "other",
    label,
    entries: featureEntries,
  }));
}

function sqlNodes(model, objectId, parentNode, depth, order) {
  const attachment = model.databaseAttachments.get(objectId);
  if (!attachment) return [];
  const queries = attachment.queries.map(query => ({
    key: `query:${parentNode.key}:${query.id}`,
    kind: "query",
    query,
    object: null,
    callerObject: parentNode.object,
    parentKey: parentNode.key,
    depth,
    order: order + query.name,
    call: {
      expression: query.name,
      line: query.location.definitionLine,
      contexts: [],
      resolution: "static-query-constant",
    },
    journey: {
      stage: "database",
      role: "sql-statement",
      provenance: "derived",
      evidence: { kind: "referenced-static-query", resolution: "source", line: query.location.definitionLine },
    },
  }));
  const statements = attachment.statements.map(statement => ({
    key: `statement:${parentNode.key}:${objectId}:${statement.id}`,
    kind: "query",
    query: {
      id: statement.id,
      name: statement.statement,
      marker: "transaction",
      statement: statement.statement,
      placeholderCount: 0,
      catalogObjects: [],
      location: { path: parentNode.object.location.path, definitionLine: statement.line },
      sql: statement.expression,
      truncated: statement.truncated,
    },
    object: null,
    callerObject: parentNode.object,
    parentKey: parentNode.key,
    depth,
    order: order + statement.id,
    call: {
      expression: statement.expression,
      line: statement.line,
      contexts: [{ kind: "transaction", label: "read-only transaction", line: statement.line }],
      resolution: "inline-sql",
    },
    journey: {
      stage: "database",
      role: "sql-statement",
      provenance: "derived",
      evidence: { kind: "inline-sql-literal", resolution: "source", line: statement.line },
    },
  }));
  return [...queries, ...statements];
}

function matchesNode(node, query) {
  if (!query) return true;
  const values = node.query
    ? [node.query.name, node.query.marker, node.query.statement, node.query.sql, ...node.query.catalogObjects]
    : [node.object?.name, node.object?.qualname, node.object?.kind, node.object?.module, node.object?.docstring];
  values.push(node.call?.expression, node.call?.resolution);
  values.push(...array(node.call?.contexts).flatMap(context => [context.kind, context.label]));
  return values.filter(Boolean).join(" ").toLocaleLowerCase().includes(query);
}

export function buildVisibleFlow(model, {
  entry,
  lens = "e2e",
  depth = 3,
  showOutcomes = false,
  query = "",
  rootObjectId = null,
} = {}) {
  if (!entry) return { nodes: [], edgeCount: 0, sqlCount: 0, truncated: false };
  const maximumDepth = lens === "api" ? 1 : depth === "all" ? 10 : Number(depth);
  const roots = rootObjectId ? [rootObjectId] : entry.roots;
  const nodes = [];
  const queued = roots.map((objectId, index) => ({ objectId, depth: 0, parentKey: null, callerObject: null, call: null, order: `${index}` }));
  const expanded = new Set();
  let edgeCount = 0;
  while (queued.length) {
    const item = queued.shift();
    const object = model.objects.get(item.objectId);
    if (!object) continue;
    const key = item.parentKey ? `${item.parentKey}>${item.order}:${object.id}` : `root:${item.order}:${object.id}`;
    const node = {
      key,
      kind: object.kind,
      object,
      query: null,
      parentKey: item.parentKey,
      depth: item.depth,
      order: item.order,
      call: item.call,
      callerObject: item.callerObject,
      journey: entry.type === "route" ? entry.route.journey.nodeByKey.get(key) || null : null,
    };
    nodes.push(node);
    if (item.parentKey) edgeCount += 1;
    const alreadyExpanded = expanded.has(object.id);
    if (!alreadyExpanded && lens !== "api") {
      const attached = sqlNodes(model, object.id, node, item.depth + 1, `${item.order}.sql.`);
      nodes.push(...attached);
      edgeCount += attached.length;
    }
    if (item.depth >= maximumDepth || alreadyExpanded) continue;
    expanded.add(object.id);
    const callable = model.system.callables.get(object.id);
    if (!callable) continue;
    const groupedCalls = [];
    const callsByTargetAndContext = new Map();
    for (const call of callable.calls) {
      const outcomePath = call.outcome || call.contexts.some(
        context => context.kind === "except" || context.kind === "raise",
      );
      if (!showOutcomes && outcomePath) continue;
      const contextKey = call.contexts.map(context => `${context.kind}:${context.label}`).join("|");
      const groupKey = `${call.object.id}|${contextKey}`;
      const previous = callsByTargetAndContext.get(groupKey);
      if (previous) {
        previous.repeatCount += 1;
        if (Number.isInteger(call.line)) previous.lines.push(call.line);
        continue;
      }
      const grouped = { ...call, lines: [...call.lines] };
      groupedCalls.push(grouped);
      callsByTargetAndContext.set(groupKey, grouped);
    }
    for (const call of groupedCalls) {
      queued.push({
        objectId: call.object.id,
        depth: item.depth + 1,
        parentKey: key,
        callerObject: object,
        call,
        order: `${item.order}.${String(call.sequence).padStart(3, "0")}`,
      });
    }
  }
  nodes.sort((left, right) => left.order.localeCompare(right.order));
  const normalizedQuery = text(query).toLocaleLowerCase();
  if (!normalizedQuery) {
    return {
      nodes,
      edgeCount,
      sqlCount: nodes.filter(node => node.kind === "query").length,
      truncated: nodes.some(node => node.object && model.system.callables.get(node.object.id)?.truncated),
    };
  }
  const byKey = new Map(nodes.map(node => [node.key, node]));
  const kept = new Set();
  for (const node of nodes) {
    if (!matchesNode(node, normalizedQuery)) continue;
    let current = node;
    while (current) {
      kept.add(current.key);
      current = current.parentKey ? byKey.get(current.parentKey) : null;
    }
  }
  const filtered = nodes.filter(node => kept.has(node.key));
  return {
    nodes: filtered,
    edgeCount: filtered.filter(node => node.parentKey && kept.has(node.parentKey)).length,
    sqlCount: filtered.filter(node => node.kind === "query").length,
    truncated: false,
  };
}

function humanizeIdentifier(value) {
  return text(value, "Source step")
    .replace(/^_+/, "")
    .replaceAll("_", " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^./, character => character.toUpperCase());
}

function responseLabel(response) {
  const schemas = array(response.schemas);
  return `${response.status}${schemas.length ? ` · ${schemas.join(" · ")}` : ""}`;
}

function sourceOutcome(node) {
  return {
    id: node.object?.id || node.key,
    name: node.object?.name || "Error outcome",
    status: node.call?.statusCode || null,
    code: text(node.call?.code),
    label: [node.call?.statusCode, text(node.call?.code) || humanizeIdentifier(node.object?.name)].filter(Boolean).join(" · "),
    node,
  };
}

function journeyStageCopy(id, nodes, entry, responses) {
  if (id === "api") {
    const requestSchemas = unique([
      ...entry.route.request.bodyObjects.map(object => object.name),
      ...array(entry.route.contract?.request?.schemas),
    ]);
    const dependencyCopy = entry.route.dependencies.map(dependency => {
      const results = dependency.resultObjects.map(object => object.name);
      return `${dependency.object.qualname} → ${results.join(" · ") || "Any"}`;
    }).join(" · ");
    return {
      label: "API",
      eyebrow: "Request boundary",
      title: `${entry.method.toUpperCase()} ${entry.path}`,
      summary: [
        dependencyCopy,
        requestSchemas.length ? `application/json → ${requestSchemas.join(" · ")}` : "",
        entry.route.endpoint.qualname,
      ].filter(Boolean).join(" → ") || `${entry.method.toUpperCase()} ${entry.path}`,
    };
  }
  if (id === "internals") {
    const owned = nodes.filter(node => node.journey?.role === "application-call");
    const main = owned[0];
    const named = unique(nodes
      .filter(node => node.journey?.role === "application-call")
      .map(node => node.object.qualname))
      .slice(0, 3);
    return {
      label: "Internals",
      eyebrow: "Application logic",
      title: main ? main.object.qualname : "No internal source calls",
      summary: named.join(" → ") || `${nodes.length} source-derived internal step${nodes.length === 1 ? "" : "s"}`,
    };
  }
  if (id === "database") {
    const gateway = nodes.find(node => node.journey?.role === "database-call");
    const sqlCount = nodes.filter(node => node.query).length;
    return {
      label: "DB interface",
      eyebrow: "Data boundary",
      title: gateway ? gateway.object.qualname : "No PostgreSQL call in this request",
      summary: gateway
        ? `${gateway.object.qualname}${sqlCount ? ` · ${sqlCount} SQL statement${sqlCount === 1 ? "" : "s"}` : " · connection boundary"}`
        : "This path stops after application-owned metadata work.",
    };
  }
  const success = responses.success.map(responseLabel);
  const errors = responses.sourceErrors.map(outcome => outcome.label);
  return {
    label: "Response",
    eyebrow: "Return boundary",
    title: [...success, ...errors].join(" · ") || `${entry.route.response.statusCode || "HTTP"}`,
    summary: `${entry.route.endpoint.qualname} → ${[...success, ...errors].join(" · ") || "response"}`,
  };
}

function signatureFlow(signature) {
  const inputs = array(signature?.parameters)
    .map(parameter => `${parameter.name}: ${parameter.annotation}`)
    .join(", ") || "∅";
  return `${inputs} → ${text(signature?.returnAnnotation, "Any")}`;
}

function callableEvidenceDescription(object, signature) {
  const location = sourceLocation(object);
  return `${object.qualname} at ${location}: ${signatureFlow(signature)}.`;
}

function contractEvidenceDescription(entry, requestObjects, parameters, requestContract) {
  const mediaType = array(entry.route.contract?.request?.content)[0]?.mediaType || "application/json";
  const models = unique(requestObjects.map(object => object.name));
  const requiredFields = unique(requestObjects.flatMap(object => (
    array(object.dataShape?.fields).filter(field => field.required).map(field => `${object.name}.${field.name}`)
  )));
  const transport = parameters.length ? parameters.join(" · ") : "no path, query, header, or cookie parameters";
  const contractType = models.join(" · ") || text(requestContract?.type, "HTTP inputs");
  return `${entry.method.toUpperCase()} ${entry.path}: ${mediaType} → ${contractType}; ${transport}${requiredFields.length ? `; required fields ${requiredFields.join(" · ")}` : ""}.`;
}

export function buildJourneyOverview(model, entry) {
  if (!entry || entry.type !== "route") return null;
  const flow = buildVisibleFlow(model, {
    entry,
    lens: "e2e",
    depth: "all",
    showOutcomes: true,
  });
  const phases = new Map();
  const grouped = new Map(JOURNEY_STAGE_IDS.map(id => [id, []]));
  const issues = [...entry.route.journey.issues];
  for (const node of flow.nodes) {
    let phase = node.journey?.stage;
    if (!JOURNEY_STAGE_IDS.includes(phase)) {
      phase = node.parentKey ? phases.get(node.parentKey) : "api";
      issues.push({ kind: "missing-journey-classification", nodeKey: node.key });
    }
    phases.set(node.key, phase);
    grouped.get(phase).push({ ...node, phase });
  }
  const contractResponses = array(entry.route.contract?.responses);
  const responseNodes = grouped.get("response");
  const mappedResponseNodes = responseNodes.filter(node => node.call?.statusCode || node.call?.code);
  const responseCandidates = mappedResponseNodes.length ? mappedResponseNodes : responseNodes;
  const sourceErrors = responseCandidates.map(sourceOutcome);
  const responses = {
    success: contractResponses.filter(response => /^[23]/.test(response.status)),
    sourceErrors,
    documentedErrors: contractResponses.filter(response => !/^[23]/.test(response.status)),
  };
  const stages = JOURNEY_STAGE_IDS.map((id, index) => ({
    id,
    index,
    nodes: grouped.get(id),
    ...journeyStageCopy(id, grouped.get(id), entry, responses),
  }));
  const flowKeys = new Set(flow.nodes.map(node => node.key));
  const transitions = entry.route.journey.transitions.filter(
    transition => flowKeys.has(transition.fromKey) && flowKeys.has(transition.toKey),
  );
  return {
    scope: "request",
    flow,
    stages,
    responses,
    phases,
    transitions,
    issues,
    guide: {
      eyebrow: "How to read this",
      title: "Follow the request across four ownership boundaries",
      description: "Select an area to reveal only the source calls that make that part of the journey happen.",
    },
  };
}

function focusedStageCopy(entry, lens, flow) {
  if (lens === "internals") {
    const methods = entry.component.methods.map(method => method.name);
    return {
      id: "internals",
      index: 0,
      label: "Internals",
      eyebrow: "Installed component",
      title: entry.component.implementation.qualname,
      summary: methods.length
        ? `${methods.length} public method${methods.length === 1 ? "" : "s"} · ${methods.join(" · ")}`
        : entry.component.implementation.kind,
      nodes: flow.nodes,
    };
  }
  const operation = entry.operation;
  const parameters = operation.parameters
    .map(parameter => `${parameter.name}: ${parameter.annotation}`)
    .join(", ") || "∅";
  return {
    id: "database",
    index: 0,
    label: "DB interface",
    eyebrow: "Gateway operation",
    title: entry.title,
    summary: `${operation.contract.qualname} → ${operation.implementation.qualname}; ${parameters} → ${operation.returnAnnotation}`,
    nodes: flow.nodes,
  };
}

export function buildFocusedJourneyOverview(model, entry, lens) {
  if (!entry || !["api", "internals", "database"].includes(lens)) return null;
  if (lens === "api") {
    if (entry.type !== "route") return null;
    const requestOverview = buildJourneyOverview(model, entry);
    const stage = requestOverview?.stages.find(candidate => candidate.id === "api");
    if (!requestOverview || !stage) return null;
    return {
      ...requestOverview,
      scope: "focused",
      stages: [{ ...stage, index: 0 }],
      guide: {
        eyebrow: "Focused source journey",
        title: `Follow ${entry.title} from transport inputs into the handler`,
        description: "Expand the boundary, then open a step to inspect its data shape, transformation, and installed source.",
      },
    };
  }
  if ((lens === "internals" && entry.type !== "component") || (lens === "database" && entry.type !== "database")) {
    return null;
  }
  const flow = buildVisibleFlow(model, {
    entry,
    lens,
    depth: "all",
    showOutcomes: true,
  });
  const stage = focusedStageCopy(entry, lens, flow);
  return {
    scope: "focused",
    flow,
    stages: [stage],
    responses: { success: [], sourceErrors: [], documentedErrors: [] },
    phases: new Map(flow.nodes.map(node => [node.key, stage.id])),
    transitions: [],
    issues: [],
    guide: {
      eyebrow: "Focused source journey",
      title: `Follow ${entry.title} from parameters to return value`,
      description: "Expand the boundary, then open a step to inspect its data shape, transformation, and installed source.",
    },
  };
}

export function buildSelectedJourneyOverview(model, entry, lens) {
  return lens === "e2e"
    ? buildJourneyOverview(model, entry)
    : buildFocusedJourneyOverview(model, entry, lens);
}

export function buildApiJourneySteps(model, entry, overview) {
  if (!entry || entry.type !== "route" || !overview) return [];
  const apiNodes = overview.stages.find(stage => stage.id === "api")?.nodes || [];
  const handoff = overview.transitions.find(
    transition => transition.fromStage === "api" && ["internals", "database"].includes(transition.toStage),
  );
  const handoffFrom = handoff ? overview.flow.nodes.find(node => node.key === handoff.fromKey) : null;
  const handoffTo = handoff ? overview.flow.nodes.find(node => node.key === handoff.toKey) : null;
  const steps = [];
  for (const dependency of entry.route.dependencies) {
    const node = apiNodes.find(candidate => candidate.object?.id === dependency.object.id);
    const resultIds = new Set(dependency.resultObjects.map(object => object.id));
    const transformationNode = node ? apiNodes.find(candidate => (
      candidate.parentKey === node.key && resultIds.has(candidate.object?.id)
    )) : null;
    const signature = model.system.callables.get(dependency.object.id)?.signature || null;
    const returnedNames = dependency.resultObjects.map(object => object.name);
    const resultLabel = returnedNames.join(" · ") || "request dependency";
    const title = `Resolve ${resultLabel}`;
    steps.push({
      id: `dependency:${dependency.object.id}`,
      kind: "dependency",
      eyebrow: "Before the handler",
      title,
      summary: returnedNames.length
        ? `${dependency.object.qualname} → ${returnedNames.join(" · ")}`
        : signatureFlow(signature),
      explanation: callableEvidenceDescription(dependency.object, signature),
      facts: [
        ["Dependency", dependency.object.qualname],
        ["Result", returnedNames.join(" · ") || "Injected request value"],
      ],
      evidence: [node, ...dependency.resultObjects.map(object => ({
        key: `dependency-result:${object.id}`,
        kind: object.kind,
        object,
        query: null,
        call: null,
      }))].filter(Boolean),
      dataFlow: {
        signature,
        outputObjects: dependency.resultObjects,
        transformationNode,
        transformationText: signatureFlow(signature),
        transformationLabel: sourceLocation(dependency.object),
      },
    });
  }
  const contract = entry.route.contract;
  const requestObjects = unique([
    ...entry.route.request.bodyObjects,
    ...array(contract?.story?.requestObjects),
  ].map(object => object.id)).map(id => model.objects.get(id)).filter(Boolean);
  const requestSchemas = unique([
    ...entry.route.request.bodyObjects.map(object => object.name),
    ...array(contract?.request?.schemas),
  ]);
  const parameters = entry.route.request.parameters.map(parameter => `${parameter.location}:${parameter.name}`);
  if (requestSchemas.length || requestObjects.length || parameters.length) {
    const bodyLabel = unique([...requestSchemas, ...requestObjects.map(object => object.name)]).join(" · ");
    const requestContract = array(contract?.request?.content)[0]?.contract || null;
    steps.push({
      id: `contract:${entry.id}`,
      kind: "contract",
      eyebrow: "Request contract",
      title: requestSchemas.length || requestObjects.length ? "Validate the request body" : "Validate request inputs",
      summary: bodyLabel
        ? `${array(contract?.request?.content)[0]?.mediaType || "application/json"} → ${bodyLabel}`
        : parameters.join(" · "),
      explanation: contractEvidenceDescription(entry, requestObjects, parameters, requestContract),
      facts: [
        ["Body", bodyLabel || "No JSON body"],
        ["Parameters", parameters.join(" · ") || "None"],
      ],
      evidence: requestObjects.map(object => ({
        key: `contract:${object.id}`,
        kind: object.kind,
        object,
        query: null,
        call: null,
      })),
      dataFlow: {
        signature: {
          available: true,
          parameters: [{
            name: "requestBody",
            annotation: bodyLabel || "HTTP parameters",
            required: contract?.request?.required === true,
            objects: requestObjects,
          }],
          returnAnnotation: bodyLabel || "validated request values",
          returnObjects: requestObjects,
        },
        inputContract: requestContract,
        outputObjects: requestObjects,
        transformationNode: null,
        transformationText: `${entry.method.toUpperCase()} ${entry.path}\n${array(contract?.request?.content)[0]?.mediaType || "application/json"} → ${bodyLabel || parameters.join(" · ")}`,
        transformationLabel: "Registered request contract",
      },
    });
  }
  const handlerNode = apiNodes.find(node => node.object?.id === entry.route.endpoint.id);
  const handlerSignature = model.system.callables.get(entry.route.endpoint.id)?.signature || null;
  const dependencyByParameter = new Map(entry.route.dependencies.map(dependency => [dependency.parameterName, dependency]));
  const transportByParameter = new Map(entry.route.request.parameters.map(parameter => [parameter.name, parameter]));
  const requestObjectIds = new Set(requestObjects.map(object => object.id));
  const handlerArguments = array(handlerSignature?.parameters).map(parameter => {
    const dependency = dependencyByParameter.get(parameter.name);
    const transport = transportByParameter.get(parameter.name);
    const bodyObjects = parameter.objects.filter(object => requestObjectIds.has(object.id));
    return {
      parameter: parameter.name,
      annotation: parameter.annotation,
      expression: dependency
        ? `resolved ${dependency.resultObjects.map(object => object.name).join(" · ") || dependency.object.name}`
          : bodyObjects.length ? `validated ${bodyObjects.map(object => object.name).join(" · ")}`
            : transport ? `${transport.location} parameter ${transport.name}`
            : `${parameter.kind || "registered"} ${parameter.annotation}`,
      kind: "framework",
    };
  });
  const successContract = array(contract?.responses)
    .find(response => /^[23]/.test(response.status))?.content?.[0]?.contract || null;
  steps.push({
    id: `handler:${entry.route.endpoint.id}`,
    kind: "handler",
    eyebrow: "Registered route",
    title: "Run the route handler",
    summary: `${entry.method.toUpperCase()} ${entry.path} → ${entry.route.endpoint.qualname}`,
    explanation: `${entry.method.toUpperCase()} ${entry.path} → ${callableEvidenceDescription(entry.route.endpoint, handlerSignature)}`,
    facts: [
      ["Handler", entry.route.endpoint.qualname],
      ["Source", sourceLocation(entry.route.endpoint)],
    ],
    evidence: [handlerNode].filter(Boolean),
    dataFlow: {
      signature: handlerSignature,
      argumentsPassed: handlerArguments,
      inputObjects: uniqueObjects([
        ...requestObjects,
        ...entry.route.dependencies.flatMap(dependency => dependency.resultObjects),
      ]),
      outputContract: successContract,
      transformationNode: handoffTo,
    },
  });
  if (handoffTo) {
    const nextLabel = handoff.toStage === "database" ? "database interface" : "application logic";
    steps.push({
      id: `handoff:${handoffTo.object.id}`,
      kind: "handoff",
      eyebrow: "Ownership handoff",
      title: `Hand off to ${nextLabel}`,
      summary: `${handoffFrom?.object?.qualname || entry.route.endpoint.qualname} → ${handoffTo.object.qualname}`,
      explanation: sourceEvidenceDescription(handoffTo, nodeSignature(model, handoffTo)),
      facts: [
        ["From", handoffFrom?.object?.qualname || entry.route.endpoint.qualname],
        ["To", handoffTo.object.qualname],
      ],
      evidence: [handoffTo],
      nextStage: handoff.toStage,
      dataFlow: {
        signature: nodeSignature(model, handoffTo),
        argumentsPassed: handoffTo.call?.arguments || [],
        transformationNode: handoffTo,
      },
    });
  }
  return steps;
}

function sourceStepKind(node, stageId) {
  if (node.query) return "query";
  if (node.call?.outcome || node.object?.kind === "outcome") return "outcome";
  if (["model", "class"].includes(node.object?.kind)) return "value";
  if (node.object?.kind?.includes("repository")) return "repository";
  if (stageId === "database" || node.object?.kind?.includes("gateway")) return "database";
  return "application";
}

function sourceStepEyebrow(node, stageId) {
  const kind = sourceStepKind(node, stageId);
  if (kind === "query") return `${text(node.query?.statement, "SQL")} statement`;
  if (kind === "outcome") return "Mapped error path";
  if (kind === "value") return "Typed value";
  if (kind === "repository") return "Repository operation";
  if (kind === "database") return node.journey?.role === "database-call" ? "PostgreSQL boundary" : "Database implementation";
  return node.journey?.role === "application-call" ? "Application boundary" : "Internal implementation";
}

function sourceStepTitle(node, stageId) {
  if (node.query) {
    const staticName = node.query.name.replace(/_QUERY$/i, "");
    if (staticName.toLocaleUpperCase() !== text(node.query.statement).toLocaleUpperCase()) {
      return `Run ${humanizeIdentifier(staticName.toLocaleLowerCase())} query`;
    }
    const setting = node.query.sql.match(/\bSET\s+LOCAL\s+([a-z_][a-z0-9_]*)/i)?.[1];
    if (setting) return `Set ${humanizeIdentifier(setting)}`;
    if (/\bBEGIN\s+TRANSACTION\b/i.test(node.query.sql)) return "Begin read-only transaction";
    return `Run ${humanizeIdentifier(text(node.query.statement, "SQL").toLocaleLowerCase())} statement`;
  }
  if (node.call?.outcome || node.object?.kind === "outcome") {
    return [
      node.call?.statusCode ? `Return ${node.call.statusCode}` : "Raise",
      text(node.call?.code) ? humanizeIdentifier(node.call.code) : humanizeIdentifier(node.object?.name),
    ].filter(Boolean).join(" · ");
  }
  if (["model", "class"].includes(node.object?.kind)) return `Build ${humanizeIdentifier(node.object.name)}`;
  const action = humanizeIdentifier(node.object?.name || "source step");
  const parts = text(node.object?.qualname).split(".");
  const owner = parts.length > 1 ? humanizeIdentifier(parts.at(-2)) : "";
  if (!owner) return action;
  const connector = stageId === "database" || node.object?.kind?.includes("gateway") ? "through" : "in";
  return `${action} ${connector} ${owner}`;
}

function sourceStepSummary(model, node) {
  if (node.query) {
    const inputs = node.query.placeholderCount
      ? `${node.query.placeholderCount} bound parameter${node.query.placeholderCount === 1 ? "" : "s"}`
      : "No bound parameters";
    const outputs = array(node.query.resultColumns);
    const result = outputs.length
      ? outputs.join(" · ")
      : ["SELECT", "SHOW", "WITH"].includes(node.query.statement) ? "SQL result" : "No returned rows";
    return `${inputs} → ${result}`;
  }
  const mappings = array(node.call?.arguments).slice(0, 3)
    .map(argument => `${argument.parameter} ← ${argument.expression}`);
  if (mappings.length) {
    const hidden = node.call.arguments.length - mappings.length;
    return `${mappings.join(" · ")}${hidden > 0 ? ` · +${hidden} more` : ""}`;
  }
  const signature = nodeSignature(model, node);
  const inputs = array(signature?.parameters).map(parameter => parameter.name);
  return `${inputs.length ? inputs.join(" · ") : "No parameters"} → ${text(signature?.returnAnnotation, node.object?.name || "result")}`;
}

function dataFlowForNode(model, node, overrides = {}) {
  const queryOutputObjects = node.query ? queryResultObjects(node.query) : [];
  const signature = node.query ? {
    available: true,
    parameters: node.query.placeholderCount ? [{
      name: "parameters",
      annotation: `tuple[${node.query.placeholderCount}]`,
      required: true,
      objects: [],
    }] : [],
    returnAnnotation: queryOutputObjects.length ? "list[SQL row]" : "SQL result",
    returnObjects: [],
  } : nodeSignature(model, node);
  const argumentsPassed = node.query?.placeholderCount ? [{
    parameter: "parameters",
    annotation: `tuple[${node.query.placeholderCount}]`,
    expression: `${node.query.placeholderCount} bound placeholder${node.query.placeholderCount === 1 ? "" : "s"}`,
  }] : node.call?.arguments || [];
  return {
    signature,
    argumentsPassed,
    outputObjects: queryOutputObjects,
    transformationNode: node,
    ...overrides,
  };
}

function sourceNarrativeStep(model, node, stageId) {
  const signature = nodeSignature(model, node);
  return {
    id: `${stageId}:${node.key}`,
    kind: sourceStepKind(node, stageId),
    eyebrow: sourceStepEyebrow(node, stageId),
    title: sourceStepTitle(node, stageId),
    summary: sourceStepSummary(model, node),
    explanation: sourceEvidenceDescription(node, signature),
    evidence: [node],
    dataFlow: dataFlowForNode(model, node),
  };
}

function matchingContractResponse(entry, status) {
  return array(entry.route.contract?.responses)
    .find(response => response.status === String(status)) || null;
}

function returnedHandlerCall(entry, overview) {
  const handler = overview.flow.nodes.find(node => node.object?.id === entry.route.endpoint.id);
  if (!handler) return null;
  const boundary = overview.transitions.find(transition => (
    transition.fromKey === handler.key
    && transition.fromStage === "api"
    && ["internals", "database"].includes(transition.toStage)
  ));
  if (boundary) {
    const target = overview.flow.nodes.find(node => node.key === boundary.toKey);
    if (target) return target;
  }
  return overview.flow.nodes.find(node => (
    node.parentKey === handler.key
    && array(node.call?.contexts).some(context => context.kind === "return")
  )) || null;
}

function buildResponseJourneySteps(model, entry, overview) {
  const steps = [];
  const handlerSignature = model.system.callables.get(entry.route.endpoint.id)?.signature || null;
  const responseObjects = uniqueObjects([
    ...entry.route.response.objects,
    ...array(entry.route.contract?.story?.responseObjects),
  ]);
  const transformationNode = returnedHandlerCall(entry, overview);
  for (const response of overview.responses.success) {
    const content = array(response.content)[0] || null;
    const resultType = text(handlerSignature?.returnAnnotation, responseObjects[0]?.name || "handler result");
    steps.push({
      id: `response:success:${response.status}`,
      kind: "success",
      eyebrow: "Successful response",
      title: `Return HTTP ${response.status}`,
      summary: `${resultType} → ${array(response.schemas).join(" · ") || content?.mediaType || "response body"}`,
      explanation: `${entry.route.endpoint.qualname} returns ${resultType}; ${entry.method.toUpperCase()} ${entry.path} declares HTTP ${response.status}${content?.mediaType ? ` ${content.mediaType}` : ""}${array(response.schemas).length ? ` as ${array(response.schemas).join(" · ")}` : ""}.`,
      evidence: [transformationNode].filter(Boolean),
      dataFlow: {
        signature: {
          available: true,
          parameters: [{
            name: "handlerResult",
            annotation: resultType,
            required: true,
            objects: responseObjects,
          }],
          returnAnnotation: `HTTP ${response.status}`,
          returnObjects: responseObjects,
        },
        argumentsPassed: [{
          parameter: "handlerResult",
          annotation: resultType,
          expression: `returned by ${entry.route.endpoint.name}`,
          kind: "framework",
        }],
        inputObjects: responseObjects,
        outputContract: content?.contract || null,
        outputObjects: responseObjects,
        transformationNode,
      },
    });
  }
  for (const outcome of overview.responses.sourceErrors) {
    const response = matchingContractResponse(entry, outcome.status);
    const content = array(response?.content)[0] || null;
    const originalSignature = nodeSignature(model, outcome.node);
    steps.push({
      id: `response:error:${outcome.node.key}`,
      kind: "outcome",
      eyebrow: "Mapped error path",
      title: [outcome.status ? `Return HTTP ${outcome.status}` : "Raise error", text(outcome.code) ? humanizeIdentifier(outcome.code) : humanizeIdentifier(outcome.name)].join(" · "),
      summary: sourceStepSummary(model, outcome.node),
      explanation: sourceEvidenceDescription(outcome.node, originalSignature),
      evidence: [outcome.node],
      dataFlow: dataFlowForNode(model, outcome.node, {
        signature: {
          ...originalSignature,
          returnAnnotation: outcome.status ? `HTTP ${outcome.status}` : text(originalSignature?.returnAnnotation, "Error response"),
          returnObjects: [],
        },
        outputContract: content?.contract || null,
        outputObjects: [],
      }),
    });
  }
  return steps;
}

export function buildJourneyStageSteps(model, entry, overview, stageId, nodes = null) {
  if (!entry || !overview || !JOURNEY_STAGE_IDS.includes(stageId)) return [];
  if (stageId === "api") {
    const steps = entry.type === "route" ? buildApiJourneySteps(model, entry, overview) : [];
    return overview.scope === "focused" ? steps.map(step => ({ ...step, nextStage: null })) : steps;
  }
  if (stageId === "response") return entry.type === "route" ? buildResponseJourneySteps(model, entry, overview) : [];
  const stageNodes = nodes || journeyStageFlow(overview, stageId).nodes;
  const steps = stageNodes.map(node => sourceNarrativeStep(model, node, stageId));
  if (!steps.length) return steps;
  if (overview.scope === "focused") return steps;
  const nextStage = stageId === "internals"
    ? (overview.stages.find(stage => stage.id === "database")?.nodes.length ? "database" : "response")
    : stageId === "database" ? "response" : null;
  if (nextStage) steps[steps.length - 1] = { ...steps.at(-1), nextStage };
  return steps;
}

export function journeyStageFlow(overview, stageId, query = "") {
  if (!overview || !JOURNEY_STAGE_IDS.includes(stageId)) {
    return { nodes: [], edgeCount: 0, sqlCount: 0, truncated: false };
  }
  const stage = overview.stages.find(candidate => candidate.id === stageId);
  const stageKeys = new Set(stage.nodes.map(node => node.key));
  const normalizedQuery = text(query).toLocaleLowerCase();
  const kept = new Set();
  for (const node of stage.nodes) {
    if (normalizedQuery && !matchesNode(node, normalizedQuery)) continue;
    let current = node;
    while (current && stageKeys.has(current.key)) {
      kept.add(current.key);
      current = current.parentKey ? stage.nodes.find(candidate => candidate.key === current.parentKey) : null;
    }
  }
  if (!normalizedQuery) stage.nodes.forEach(node => kept.add(node.key));
  const depths = new Map();
  const nodes = stage.nodes.filter(node => kept.has(node.key)).map(node => {
    const parentKey = node.parentKey && kept.has(node.parentKey) ? node.parentKey : null;
    const depth = parentKey ? (depths.get(parentKey) || 0) + 1 : 0;
    depths.set(node.key, depth);
    return { ...node, parentKey, depth };
  });
  return {
    nodes,
    edgeCount: nodes.filter(node => node.parentKey).length,
    sqlCount: nodes.filter(node => node.query).length,
    truncated: overview.flow.truncated,
  };
}

const uiState = {
  model: null,
  lens: "e2e",
  entryId: null,
  depth: 3,
  showOutcomes: false,
  query: "",
  selectedNodeKey: null,
  expandedNodeKey: null,
  expandedNarrativeId: null,
  rootObjectId: null,
  journeyStage: null,
  entryQuery: "",
  entryGroupId: "all",
  loading: false,
  refreshTimer: null,
  fingerprint: null,
};

function byId(id) {
  return document.getElementById(id);
}

function methodBadge(method) {
  return element("span", {
    className: `method-badge method-${method}`,
    textContent: method.toUpperCase(),
  });
}

function lensCopy(lens) {
  return {
    e2e: ["End-to-end request journey", "Registered route", "Choose a journey"],
    api: ["API contract and collaborators", "Registered route", "Choose an operation"],
    internals: ["Installed internal component", "Runtime component", "Choose a component"],
    database: ["Server-to-PostgreSQL journey", "Gateway operation", "Choose an operation"],
  }[lens];
}

function currentEntries() {
  return uiState.model ? entriesForLens(uiState.model, uiState.lens) : [];
}

function currentEntry() {
  const entries = currentEntries();
  return entries.find(entry => entry.id === uiState.entryId) || entries[0] || null;
}

function entryLabel(entry) {
  if (!entry) return "";
  if (entry.type === "route") return `${entry.method.toUpperCase()} ${entry.path} — ${entry.title}`;
  return entry.title;
}

function defaultEntryId(entries, lens) {
  return entries[0]?.id || null;
}

function updateUrl() {
  const parameters = new URLSearchParams();
  parameters.set("lens", uiState.lens);
  if (uiState.entryId) parameters.set("entry", uiState.entryId);
  parameters.set("depth", String(uiState.depth));
  if (uiState.showOutcomes) parameters.set("outcomes", "1");
  if (uiState.rootObjectId) parameters.set("root", uiState.rootObjectId);
  if (uiState.journeyStage) parameters.set("stage", uiState.journeyStage);
  const next = `${window.location.pathname}?${parameters}`;
  window.history.replaceState(null, "", next);
}

function setLens(lens, { preserveEntry = false } = {}) {
  if (!LENSES.has(lens)) return;
  uiState.lens = lens;
  uiState.rootObjectId = null;
  uiState.selectedNodeKey = null;
  uiState.expandedNodeKey = null;
  uiState.expandedNarrativeId = null;
  uiState.query = "";
  uiState.entryQuery = "";
  uiState.entryGroupId = "all";
  byId("flow-search").value = "";
  uiState.journeyStage = null;
  const entries = entriesForLens(uiState.model, lens);
  if (!preserveEntry || !entries.some(entry => entry.id === uiState.entryId)) {
    uiState.entryId = defaultEntryId(entries, lens);
  }
  render();
  updateUrl();
}

function setEntry(entryId) {
  if (!currentEntries().some(entry => entry.id === entryId)) return;
  uiState.entryId = entryId;
  uiState.rootObjectId = null;
  uiState.selectedNodeKey = null;
  uiState.expandedNodeKey = null;
  uiState.expandedNarrativeId = null;
  uiState.query = "";
  byId("flow-search").value = "";
  uiState.journeyStage = null;
  if (byId("entry-dialog")?.open) byId("entry-dialog").close();
  closeInspector();
  render();
  updateUrl();
}

function setFocusObject(objectId) {
  if (!uiState.model.objects.has(objectId)) return;
  uiState.rootObjectId = objectId;
  uiState.selectedNodeKey = null;
  uiState.expandedNodeKey = null;
  uiState.expandedNarrativeId = null;
  closeInspector();
  renderFlow();
  updateUrl();
}

function setJourneyStage(stageId) {
  if (!JOURNEY_STAGE_IDS.includes(stageId)) return;
  if (uiState.journeyStage === stageId) {
    closeJourneyStage();
    return;
  }
  uiState.journeyStage = stageId;
  uiState.selectedNodeKey = null;
  uiState.expandedNodeKey = null;
  uiState.expandedNarrativeId = null;
  uiState.query = "";
  byId("flow-search").value = "";
  closeInspector();
  renderFlow();
  updateUrl();
  document.querySelector(`[data-journey-stage="${CSS.escape(stageId)}"]`)?.scrollIntoView({ block: "nearest" });
}

function closeJourneyStage() {
  uiState.journeyStage = null;
  uiState.expandedNodeKey = null;
  uiState.expandedNarrativeId = null;
  uiState.query = "";
  byId("flow-search").value = "";
  closeInspector();
  renderFlow();
  updateUrl();
}

function renderLensControls() {
  byId("browse-select").value = uiState.lens;
  for (const button of document.querySelectorAll("[data-depth]")) {
    button.setAttribute("aria-pressed", String(button.dataset.depth === String(uiState.depth)));
    button.disabled = uiState.lens === "api";
  }
  const copy = lensCopy(uiState.lens);
  byId("entry-label").textContent = copy[1];
  byId("rail-eyebrow").textContent = copy[1];
  byId("rail-title").textContent = copy[2];
  byId("entry-dialog-eyebrow").textContent = copy[1];
  byId("entry-dialog-title").textContent = copy[2];
  byId("show-outcomes").checked = uiState.showOutcomes;
  const journeyOverview = !uiState.rootObjectId;
  document.querySelector(".flow-search").hidden = journeyOverview && (!uiState.journeyStage || uiState.journeyStage === "api");
  document.querySelector(".depth-control").hidden = journeyOverview;
  document.querySelector(".outcome-control").hidden = journeyOverview;
}

function entryBadge(entry) {
  return entry.method ? methodBadge(entry.method) : element("span", {
    className: "entry-glyph",
    textContent: entry.glyph,
  });
}

function entrySecondaryLabel(entry, includeFeature = true) {
  return [includeFeature ? entry.featureLabel : "", entry.subtitle].filter(Boolean).join(" · ");
}

function entryButton(entry, { includeFeature = true } = {}) {
  const button = element("button", {
    className: `entry-button${entry.id === uiState.entryId ? " selected" : ""}`,
    attrs: {
      type: "button",
      "data-entry-id": entry.id,
      "aria-current": entry.id === uiState.entryId ? "true" : "false",
    },
  });
  button.append(entryBadge(entry));
  const copy = element("span", { className: "entry-button-copy" });
  const titleRow = element("span", { className: "entry-title-row" });
  titleRow.append(tooltipOverflow(element("strong", { textContent: entry.title }), entry.title));
  if (entry.lifecycle === "planned") {
    titleRow.append(element("span", { className: "lifecycle-badge", textContent: "Planned" }));
  }
  const secondaryLabel = entrySecondaryLabel(entry, includeFeature);
  copy.append(
    titleRow,
    tooltipOverflow(element("small", { textContent: secondaryLabel }), secondaryLabel),
  );
  button.append(copy, element("span", { className: "entry-selected-mark", textContent: "✓", attrs: { "aria-hidden": "true" } }));
  button.addEventListener("click", () => setEntry(entry.id));
  return button;
}

function entryFeatureSection(feature) {
  const section = element("section", {
    className: "entry-feature",
    attrs: { "data-entry-feature": feature.id },
  });
  const heading = element("header", { className: "entry-feature-heading" });
  heading.append(
    element("strong", { textContent: feature.label }),
    element("small", { textContent: `${feature.entries.length} ${feature.entries.length === 1 ? "route" : "routes"}` }),
  );
  const entries = element("div", { className: "entry-group-list" });
  entries.append(...feature.entries.map(entry => entryButton(entry, { includeFeature: false })));
  section.append(heading, entries);
  return section;
}

function entryGroupSection(group) {
  const section = element("section", { className: "entry-group", attrs: { "data-entry-group": group.id } });
  const heading = element("header", { className: "entry-group-heading" });
  heading.append(
    tooltipOverflow(element("strong", { textContent: group.label }), group.description || group.label),
    element("small", { textContent: String(group.entries.length) }),
  );
  const entriesAreRoutes = group.entries.every(entry => entry.type === "route");
  if (entriesAreRoutes) {
    const features = element("div", { className: "entry-feature-list" });
    features.append(...routeFeatureGroups(group.entries).map(entryFeatureSection));
    section.append(heading, features);
  } else {
    const entries = element("div", { className: "entry-group-list" });
    entries.append(...group.entries.map(entry => entryButton(entry)));
    section.append(heading, entries);
  }
  return section;
}

function renderEntryGroupFilters(container, catalog) {
  const groups = [{ id: "all", label: "All", entries: currentEntries() }, ...catalog.availableGroups];
  container.replaceChildren(...groups.map(group => {
    const selected = uiState.entryGroupId === group.id;
    const button = element("button", {
      className: `entry-group-filter${selected ? " selected" : ""}`,
      attrs: { type: "button", "aria-pressed": String(selected) },
    });
    button.append(
      element("span", { textContent: group.label }),
      element("small", { textContent: String(group.entries.length) }),
    );
    button.addEventListener("click", () => {
      uiState.entryGroupId = group.id;
      renderEntries();
    });
    return button;
  }));
}

function renderEntryPickerTrigger(entry) {
  const trigger = byId("entry-picker-trigger");
  if (!entry) {
    trigger.replaceChildren(element("span", { className: "entry-picker-value", textContent: "No entries", attrs: { id: "entry-picker-value" } }));
    trigger.disabled = true;
    return;
  }
  trigger.disabled = false;
  const value = element("span", { className: "entry-picker-value", attrs: { id: "entry-picker-value" } });
  value.append(
    tooltipOverflow(element("strong", { textContent: entry.title }), entry.title),
    tooltipOverflow(
      element("small", { textContent: [entry.groupLabel, entry.featureLabel, entry.subtitle].filter(Boolean).join(" · ") }),
      [entry.groupLabel, entry.featureLabel, entry.subtitle].filter(Boolean).join(" · "),
    ),
  );
  trigger.replaceChildren(
    entryBadge(entry),
    value,
    element("span", { className: "entry-picker-disclosure", textContent: "⌄", attrs: { "aria-hidden": "true" } }),
  );
}

function renderEntryCollection(container, catalog) {
  if (!catalog.visibleCount) {
    const empty = element("div", { className: "entry-empty" });
    empty.append(
      element("strong", { textContent: "No matching entries" }),
      element("p", { textContent: "Change the search or choose another group." }),
    );
    container.replaceChildren(empty);
    return;
  }
  container.replaceChildren(...catalog.groups.map(entryGroupSection));
}

function renderEntries() {
  const entries = currentEntries();
  const includeEmptyRouteOwners = entries.some(entry => entry.type === "route");
  const available = buildEntryCatalog(entries, { includeEmptyRouteOwners });
  if (uiState.entryGroupId !== "all" && !available.availableGroups.some(group => group.id === uiState.entryGroupId)) {
    uiState.entryGroupId = "all";
  }
  const catalog = buildEntryCatalog(entries, {
    query: uiState.entryQuery,
    groupId: uiState.entryGroupId,
    includeEmptyRouteOwners,
  });
  renderEntryPickerTrigger(currentEntry());
  byId("rail-entry-search").value = uiState.entryQuery;
  byId("dialog-entry-search").value = uiState.entryQuery;
  const count = catalog.visibleCount === catalog.totalCount
    ? String(catalog.totalCount)
    : `${catalog.visibleCount}/${catalog.totalCount}`;
  byId("entry-count").textContent = count;
  byId("entry-dialog-count").textContent = `${catalog.visibleCount} of ${catalog.totalCount} ${catalog.totalCount === 1 ? "entry" : "entries"}`;
  renderEntryGroupFilters(byId("rail-group-filters"), available);
  renderEntryGroupFilters(byId("dialog-group-filters"), available);
  renderEntryCollection(byId("entry-list"), catalog);
  renderEntryCollection(byId("entry-dialog-list"), catalog);
}

function flowDescription(entry) {
  if (uiState.rootObjectId) {
    const object = uiState.model.objects.get(uiState.rootObjectId);
    const signature = uiState.model.system.callables.get(object?.id)?.signature || null;
    return object ? callableEvidenceDescription(object, signature) : "No source object selected.";
  }
  if (entry?.type === "route") {
    const request = entry.route.request.bodyObjects.map(object => object.name).join(" · ") || "no JSON body";
    const response = entry.route.response.objects.map(object => object.name).join(" · ") || `HTTP ${entry.route.response.statusCode || "response"}`;
    return `${entry.method.toUpperCase()} ${entry.path} → ${entry.route.endpoint.qualname}; ${request} → ${response}.`;
  }
  if (entry?.type === "database") {
    return `${entry.operation.contract.qualname} → ${entry.operation.implementation.qualname}; ${entry.operation.parameters.map(parameter => `${parameter.name}: ${parameter.annotation}`).join(" · ") || "∅"} → ${entry.operation.returnAnnotation}.`;
  }
  if (entry?.type === "component") {
    return `${entry.component.implementation.qualname}: ${entry.component.methods.map(method => method.qualname).join(" · ") || "no public methods"}.`;
  }
  return "No source entry selected.";
}

function renderFlowHeading(entry, flow) {
  const object = uiState.rootObjectId ? uiState.model.objects.get(uiState.rootObjectId) : null;
  const title = object?.qualname || entry?.title || "No entry point";
  byId("flow-eyebrow").textContent = object ? "Focused internal function" : lensCopy(uiState.lens)[0];
  byId("flow-title").textContent = title;
  byId("flow-description").textContent = flowDescription(entry);
  const badge = byId("flow-method");
  if (entry?.method && !object) {
    badge.hidden = false;
    badge.className = `method-badge method-${entry.method}`;
    badge.textContent = entry.method.toUpperCase();
  } else {
    badge.hidden = true;
  }
  const facts = [
    [uiState.model.system.analysis.generation === "application-startup" ? "Startup source snapshot" : "Source-derived", "live"],
  ];
  if (entry?.type === "route" && !object) facts.push([`${entry.method.toUpperCase()} ${entry.path}`, ""]);
  if (entry?.lifecycle === "planned" && !object) facts.push(["Planned · returns 501", "planned"]);
  if (object) {
    facts.push([`Depth ${uiState.depth}`, ""]);
    facts.push([uiState.showOutcomes ? "Outcomes shown" : "Success-focused", ""]);
  }
  if (entry?.route?.implementationDigest) facts.push([`sha ${entry.route.implementationDigest.slice(0, 9)}`, ""]);
  byId("flow-facts").replaceChildren(...facts.map(([label, kind]) => element("span", {
    className: `flow-fact ${kind}`.trim(),
    textContent: label,
  })));
  byId("step-count").textContent = String(flow.nodes.length);
  byId("link-count").textContent = String(flow.edgeCount);
  byId("sql-count").textContent = String(flow.sqlCount);
}

function contextChips(contexts) {
  const wrapper = element("span", { className: "context-chips" });
  const visible = contexts.slice(-3);
  for (const context of visible) {
    const label = context.kind === "if" || context.kind === "else" ? `${context.kind} · ${context.label}` : context.kind;
    wrapper.append(tooltipOverflow(element("span", {
      className: `context-chip ${context.kind}`,
      textContent: label,
    }), `${context.kind}: ${context.label}`));
  }
  return wrapper;
}

function inspectButton(node) {
  const button = createIconButton({
    icon: node.kind === "query" ? "sql" : "expand",
    label: node.kind === "query" ? "Inspect SQL" : "Inspect source",
    placement: "left",
    className: "compact",
  });
  button.addEventListener("click", event => {
    event.stopPropagation();
    openInspector(node);
  });
  return button;
}

function flowNodeTitle(node) {
  if (node.query) return node.query.name;
  if (node.call?.outcome || node.object?.kind === "outcome") {
    return [node.call?.statusCode ? `Error ${node.call.statusCode}` : "Error", text(node.call?.code) || humanizeIdentifier(node.object?.name)]
      .filter(Boolean)
      .join(" · ");
  }
  const qualname = text(node.object?.qualname, node.object?.name);
  const parts = qualname.split(".");
  if (parts.length > 1) {
    return `${humanizeIdentifier(parts.at(-2))} · ${humanizeIdentifier(parts.at(-1))}`;
  }
  return humanizeIdentifier(node.object?.name || qualname);
}

function sourceEvidenceDescription(node, signature = null) {
  if (node.query) {
    const targets = array(node.query.catalogObjects);
    const columns = array(node.query.resultColumns);
    const location = `${node.query.location.path}:${node.query.location.definitionLine || "?"}`;
    const bindings = `${node.query.placeholderCount || 0} bound parameter${node.query.placeholderCount === 1 ? "" : "s"}`;
    const result = columns.length ? columns.join(" · ") : "no declared result columns";
    return `${node.query.name} at ${location}: ${text(node.query.statement, "SQL")}${targets.length ? ` ${targets.join(" · ")}` : ""}; ${bindings} → ${result}.`;
  }
  if (node.call) {
    const caller = text(node.callerObject?.qualname, node.callerObject?.name || "source");
    const target = text(node.object?.qualname, node.object?.name || node.call.expression);
    const location = `${text(node.callerObject?.location?.path, "source")}:${node.call.line || "?"}`;
    const mappings = array(node.call.arguments).map(argument => `${argument.parameter} ← ${argument.expression}`);
    const contexts = unique(array(node.call.contexts).map(context => `${context.kind}: ${context.label}`));
    const verb = node.call.outcome || node.object?.kind === "outcome"
      ? "raises"
      : ["model", "class"].includes(node.object?.kind) ? "constructs" : "calls";
    const response = [node.call.statusCode ? `HTTP ${node.call.statusCode}` : "", text(node.call.code)].filter(Boolean).join(" · ");
    return [
      `${caller} ${verb} ${target} at ${location}`,
      mappings.length ? `arguments ${mappings.join(" · ")}` : "no explicit arguments",
      response || `returns ${text(signature?.returnAnnotation, node.call.targetSignature?.returnAnnotation || "Any")}`,
      contexts.length ? `control ${contexts.join(" · ")}` : "",
    ].filter(Boolean).join("; ") + ".";
  }
  return callableEvidenceDescription(node.object, signature);
}

function flowDetailFact(label, value) {
  const fact = element("div", { className: "flow-detail-fact" });
  fact.append(element("span", { textContent: label }), tooltipOverflow(element("code", { textContent: value }), value));
  return fact;
}

function uniqueObjects(objects) {
  return [...new Map(array(objects).filter(Boolean).map(object => [object.id, object])).values()];
}

function objectShapeJson(object) {
  const fields = array(object?.dataShape?.fields);
  const value = {};
  for (const field of fields) {
    value[field.name] = `<${field.annotation}${field.required ? "" : "; optional"}>`;
  }
  if (object?.dataShape?.truncated) value["…"] = "<additional fields omitted>";
  return JSON.stringify(value, null, 2);
}

function queryResultObjects(query) {
  const fields = array(query?.resultColumns).filter(name => name && name !== "*");
  if (!fields.length) return [];
  return [{
    id: `query-result:${query.id}`,
    name: "SQL row",
    dataShape: {
      kind: "object",
      name: "SQL row",
      fields: fields.map(name => ({ name, attribute: name, annotation: "database value", required: true })),
      truncated: query.truncated === true,
    },
  }];
}

function nodeSignature(model, node) {
  if (node?.call?.targetSignature?.available) return node.call.targetSignature;
  return node?.object ? model.system.callables.get(node.object.id)?.signature || null : null;
}

function renderShapeBlocks(objects, label) {
  const container = element("div", { className: "data-shape-blocks" });
  for (const object of uniqueObjects(objects).filter(candidate => candidate.dataShape)) {
    container.append(codeBlock({
      text: objectShapeJson(object),
      tokens: [],
      label: `${object.name} ${label}`,
      language: "json",
    }));
  }
  return container;
}

function renderDataInputs(signature, argumentsPassed = [], {
  contract = null,
  contractLabel = "HTTP JSON shape",
  objects = [],
} = {}) {
  const section = element("section", { className: "data-flow-section data-flow-inputs" });
  section.append(element("h3", { textContent: "Inputs" }));
  const parameters = array(signature?.parameters);
  const argumentsByParameter = new Map();
  for (const argument of array(argumentsPassed)) {
    if (!argumentsByParameter.has(argument.parameter)) argumentsByParameter.set(argument.parameter, []);
    argumentsByParameter.get(argument.parameter).push(argument);
  }
  const mappings = element("div", { className: "data-mappings" });
  if (!parameters.length && !argumentsPassed.length) {
    mappings.append(element("p", { className: "data-empty", textContent: "No parameters" }));
  }
  for (const parameter of parameters) {
    const passed = argumentsByParameter.get(parameter.name)?.shift();
    const row = element("div", { className: "data-mapping" });
    const target = element("div", { className: "data-mapping-target" });
    target.append(
      tooltipOverflow(element("code", { textContent: parameter.name }), parameter.name),
      tooltipOverflow(element("span", { textContent: parameter.annotation }), parameter.annotation),
    );
    row.append(target, element("span", { className: "data-arrow", textContent: "←" }));
    const fallback = parameter.required ? "framework supplied" : "default value";
    row.append(tooltipOverflow(element("code", {
      className: `data-expression${passed ? "" : " framework"}`,
      textContent: passed?.expression || fallback,
    }), passed?.expression || (parameter.required
      ? "Supplied by the registered framework boundary"
      : "The call omits this optional parameter, so its declared default is used")));
    mappings.append(row);
  }
  for (const remaining of [...argumentsByParameter.values()].flat()) {
    const row = element("div", { className: "data-mapping" });
    const target = element("div", { className: "data-mapping-target" });
    target.append(element("code", { textContent: remaining.parameter }), element("span", { textContent: remaining.annotation }));
    row.append(target, element("span", { className: "data-arrow", textContent: "←" }), tooltipOverflow(element("code", {
      className: "data-expression",
      textContent: remaining.expression,
    }), remaining.expression));
    mappings.append(row);
  }
  section.append(mappings);
  if (contract) {
    section.append(codeBlock({
      text: contractJsonShape(contract),
      tokens: [],
      label: contractLabel,
      language: "json",
    }));
  }
  const shapeObjects = uniqueObjects([
    ...objects,
    ...parameters.flatMap(parameter => parameter.objects),
  ]);
  if (!contract && shapeObjects.some(object => object.dataShape)) section.append(renderShapeBlocks(shapeObjects, "input shape"));
  return section;
}

function transformationCode(node) {
  if (node?.query) {
    return codeBlock({
      text: node.query.sql,
      tokens: [],
      label: `${node.query.location.path}:${node.query.location.definitionLine || "?"}`,
      language: "sql",
    });
  }
  if (!node?.call) return null;
  const excerpt = pythonSourceExcerpt(
    node.callerObject,
    node.call.line,
    0,
    node.call.endLine || node.call.line,
  );
  if (excerpt) {
    return codeBlock({
      text: excerpt.text,
      tokens: excerpt.tokens,
      label: `${node.callerObject.location.path}:${excerpt.startLine}–${excerpt.endLine}`,
    });
  }
  return codeBlock({
    text: node.call.expression,
    tokens: [["plain", node.call.expression]],
    label: node.call.line ? `Call site · line ${node.call.line}` : "Call expression",
  });
}

function renderTransformation(node, { textContent = "", label = "Derived signature" } = {}) {
  const section = element("section", { className: "data-flow-section data-flow-transformation" });
  section.append(element("h3", { textContent: "Transformation" }));
  const snippet = transformationCode(node);
  if (snippet) section.append(snippet);
  else section.append(codeBlock({
    text: textContent || "∅ → Any",
    tokens: [],
    label,
  }));
  return section;
}

function renderDataOutput(signature, {
  contract = null,
  objects = [],
  label = "Return shape",
} = {}) {
  const section = element("section", { className: "data-flow-section data-flow-output" });
  section.append(element("h3", { textContent: "Output" }));
  const annotation = text(signature?.returnAnnotation, objects[0]?.name || "Any");
  section.append(tooltipOverflow(element("code", { className: "data-return-type", textContent: annotation }), annotation));
  if (contract) {
    section.append(codeBlock({
      text: contractJsonShape(contract),
      tokens: [],
      label,
      language: "json",
    }));
  }
  const shapeObjects = uniqueObjects([...objects, ...array(signature?.returnObjects)]);
  if (!contract && shapeObjects.some(object => object.dataShape)) section.append(renderShapeBlocks(shapeObjects, "return shape"));
  return section;
}

function renderDataFlow({
  signature,
  argumentsPassed = [],
  inputContract = null,
  inputObjects = [],
  outputContract = null,
  outputObjects = [],
  transformationNode = null,
  transformationText = "",
  transformationLabel = "Derived signature",
} = {}) {
  const wrapper = element("div", { className: "data-flow" });
  wrapper.append(
    renderDataInputs(signature, argumentsPassed, { contract: inputContract, objects: inputObjects }),
    element("div", { className: "data-flow-direction", textContent: "↓" }),
    renderTransformation(transformationNode, {
      textContent: transformationText || signatureFlow(signature),
      label: transformationLabel,
    }),
    element("div", { className: "data-flow-direction", textContent: "↓" }),
    renderDataOutput(signature, { contract: outputContract, objects: outputObjects }),
  );
  return wrapper;
}

function renderFlowNode(node, index, { drilldown = false } = {}) {
  const expanded = drilldown && uiState.expandedNodeKey === node.key;
  const item = element("li", {
    className: `flow-step${matchesNode(node, uiState.query) && uiState.query ? " is-match" : ""}${expanded ? " expanded" : ""}`,
    attrs: {
      "data-kind": node.kind,
      style: `--flow-depth: ${Math.min(node.depth, 4)}`,
    },
  });
  const card = element("article", {
    className: `flow-card${node.kind === "query" ? " query-card" : ""}${node.key === uiState.selectedNodeKey ? " selected" : ""}${expanded ? " expanded" : ""}`,
    attrs: {
      tabindex: "0",
      "data-node-key": node.key,
      ...(drilldown ? { "aria-expanded": String(expanded) } : {}),
    },
  });
  const main = element("div", { className: "flow-card-main" });
  const head = element("div", { className: "flow-card-head" });
  const technicalName = node.query?.name || node.object?.qualname || "Source step";
  head.append(
    element("span", { className: "flow-index", textContent: String(index + 1).padStart(2, "0") }),
    element("span", { className: "flow-kind", textContent: text(node.journey?.role, node.kind).replaceAll("-", " ") }),
    tooltipOverflow(element("strong", {
      textContent: flowNodeTitle(node),
    }), technicalName),
  );
  const meta = element("div", { className: "flow-card-meta" });
  const expression = node.call?.expression || (node.object ? sourceLocation(node.object) : "static SQL");
  meta.append(tooltipOverflow(element("code", {
    className: "expression",
    textContent: expression,
  }), expression));
  if (node.call?.line) {
    meta.append(element("span", { className: "separator" }), element("small", { textContent: `line ${node.call.line}` }));
  }
  if (node.call?.repeatCount > 1) {
    meta.append(tooltipOverflow(element("span", {
      className: "context-chip repeat",
      textContent: `×${node.call.repeatCount}`,
    }), `Repeated at lines ${node.call.lines.join(", ")}`));
  }
  if (node.call?.contexts?.length) meta.append(contextChips(node.call.contexts));
  main.append(head, meta);
  const actions = element("div", { className: "flow-card-actions" });
  if (!drilldown && node.object && uiState.rootObjectId !== node.object.id) {
    const focus = element("button", { className: "ui-button compact", textContent: "Focus", attrs: { type: "button" } });
    focus.addEventListener("click", event => {
      event.stopPropagation();
      setFocusObject(node.object.id);
    });
    actions.append(focus);
  }
  if (drilldown) {
    const source = element("button", { className: "ui-button compact source-action", textContent: "Source", attrs: { type: "button" } });
    source.addEventListener("click", event => {
      event.stopPropagation();
      openInspector(node);
    });
    actions.append(source, element("span", {
      className: "flow-disclosure",
      textContent: expanded ? "Hide" : "Explain",
      attrs: { "aria-hidden": "true" },
    }));
  } else {
    actions.append(inspectButton(node));
  }
  card.append(main, actions);
  if (expanded) {
    const detail = element("div", { className: "flow-card-detail" });
    detail.append(renderDataFlow(dataFlowForNode(uiState.model, node)));
    detail.append(element("p", {
      className: "flow-detail-explanation",
      textContent: sourceEvidenceDescription(node, nodeSignature(uiState.model, node)),
    }));
    const facts = element("div", { className: "flow-detail-facts" });
    if (node.object) facts.append(flowDetailFact("Source", sourceLocation(node.object)));
    if (node.journey?.evidence?.kind) {
      facts.append(flowDetailFact("Derived by", node.journey.evidence.kind.replaceAll("-", " ")));
    }
    detail.append(facts);
    card.append(detail);
  }
  const activate = () => {
    if (!drilldown) {
      openInspector(node);
      return;
    }
    uiState.expandedNodeKey = expanded ? null : node.key;
    renderFlow();
    document.querySelector(`[data-node-key="${CSS.escape(node.key)}"]`)?.scrollIntoView({ block: "nearest" });
  };
  card.addEventListener("click", activate);
  card.addEventListener("keydown", event => {
    if (event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      activate();
    }
  });
  item.append(card);
  return item;
}

function responseBranch(kind, title, items, empty) {
  const branch = element("div", { className: `journey-branch ${kind}` });
  branch.append(
    element("span", { className: "journey-branch-mark", textContent: kind === "success" ? "✓" : "!" }),
    element("strong", { textContent: title }),
    element("small", { textContent: items.length ? items.join(" · ") : empty }),
  );
  return branch;
}

function renderJourneyBoundaryNote() {
  const explanation = element("div", { className: "journey-boundary-note" });
  explanation.append(
    element("strong", { textContent: "This request does not open a PostgreSQL connection." }),
    element("p", { textContent: "No call in the derived source graph crosses the installed PostgreSQL gateway interface." }),
  );
  return explanation;
}

function renderNarrativeStep(step, index) {
  const expanded = uiState.expandedNarrativeId === step.id;
  const item = element("li", {
    className: `narrative-step step-${step.kind}${expanded ? " expanded" : ""}`,
    attrs: { "data-narrative-id": step.id },
  });
  const toggle = element("button", {
    className: "narrative-step-toggle",
    attrs: { type: "button", "aria-expanded": String(expanded) },
  });
  toggle.append(
    element("span", { className: "narrative-step-index", textContent: String(index + 1).padStart(2, "0") }),
    element("span", { className: "narrative-step-copy" }),
    element("span", { className: "narrative-step-action", textContent: expanded ? "Hide explanation ↑" : "Explain this step ↓" }),
  );
  const copy = toggle.querySelector(".narrative-step-copy");
  copy.append(
    element("small", { textContent: step.eyebrow }),
    tooltipOverflow(element("strong", { textContent: step.title }), step.title),
    tooltipOverflow(element("span", { textContent: step.summary }), step.summary),
  );
  toggle.addEventListener("click", () => {
    uiState.expandedNarrativeId = expanded ? null : step.id;
    renderFlow();
    document.querySelector(`[data-narrative-id="${CSS.escape(step.id)}"]`)?.scrollIntoView({ block: "nearest" });
  });
  item.append(toggle);
  if (expanded) {
    const detail = element("div", { className: "narrative-step-detail" });
    if (step.dataFlow) detail.append(renderDataFlow(step.dataFlow));
    detail.append(element("p", { className: "narrative-explanation", textContent: step.explanation }));
    const definitions = [...new Map(step.evidence.flatMap(node => {
      if (node.query) return [[`query:${node.query.id}`, node]];
      return node.object?.source?.available ? [[`object:${node.object.id}`, node]] : [];
    })).values()];
    if (definitions.length) {
      const actions = element("div", { className: "narrative-definition-actions" });
      for (const node of definitions) {
        const source = element("button", {
          className: "ui-button compact",
          textContent: node.query ? "Open SQL" : `Open ${node.object.qualname || node.object.name} source`,
          attrs: { type: "button" },
        });
        tooltipOverflow(source, node.query ? "Open the complete SQL statement" : `Open ${node.object.qualname || node.object.name} source`);
        source.addEventListener("click", () => openInspector(node));
        actions.append(source);
      }
      detail.append(actions);
    }
    if (step.nextStage) {
      const stageLabel = { internals: "Internals", database: "DB interface", response: "Response" }[step.nextStage] || step.nextStage;
      const next = element("button", { className: "ui-button primary narrative-next", textContent: `Continue into ${stageLabel}`, attrs: { type: "button" } });
      next.addEventListener("click", () => setJourneyStage(step.nextStage));
      detail.append(next);
    }
    item.append(detail);
  }
  return item;
}

function renderJourneyExpansion(stage, overview, flow, entry) {
  const expansion = element("div", { className: "journey-stage-expansion" });
  if (stage.id === "database" && !stage.nodes.length) {
    expansion.append(renderJourneyBoundaryNote());
    return expansion;
  }
  const steps = buildJourneyStageSteps(uiState.model, entry, overview, stage.id, flow.nodes);
  if (steps.length) {
    const guides = {
      api: "Follow the validated request into the registered handler. Expand a step to see its data contract and source transformation.",
      internals: "Follow the application value through services, repositories, helpers, and constructed models.",
      database: "Follow the value across the PostgreSQL boundary, transaction helpers, SQL statements, and returned rows.",
      response: "Compare the successful return with every error path proven by the inspected source.",
    };
    expansion.append(element("p", {
      className: "journey-step-guide",
      textContent: guides[stage.id],
    }));
    const list = element("ol", { className: "narrative-list", attrs: { "aria-label": `${stage.label} journey steps` } });
    list.append(...steps.map(renderNarrativeStep));
    expansion.append(list);
    if (stage.id === "response" && overview.responses.documentedErrors.length) {
      expansion.append(element("p", {
        className: "journey-contract-note",
        textContent: `Also documented at the API boundary: ${overview.responses.documentedErrors.map(response => response.status).join(" · ")}. These remain contract possibilities until a source branch proves them for this handler.`,
      }));
    }
  } else if (uiState.query && stage.nodes.length) {
    const empty = element("div", { className: "journey-inline-state" });
    empty.append(element("strong", { textContent: "No matching steps in this area" }), element("p", { textContent: "Change the filter to restore the source-derived substeps." }));
    expansion.append(empty);
  }
  return expansion;
}

function renderJourneyStageCard(stage, overview, flow, entry) {
  const selected = uiState.journeyStage === stage.id;
  const card = element("article", {
    className: `journey-stage stage-${stage.id}${stage.nodes.length ? "" : " is-empty"}${selected ? " selected" : ""}`,
    attrs: {
      "data-journey-stage": stage.id,
    },
  });
  const button = element("button", {
    className: "journey-stage-toggle",
    attrs: { type: "button", "aria-expanded": String(selected) },
  });
  const heading = element("span", { className: "journey-stage-heading" });
  heading.append(
    element("span", { className: "journey-stage-number", textContent: String(stage.index + 1).padStart(2, "0") }),
    element("span", { className: "journey-stage-label", textContent: stage.label }),
    element("span", {
      className: "journey-stage-count",
      textContent: stage.id === "response"
        ? `${overview.responses.success.length + overview.responses.sourceErrors.length} paths`
        : stage.nodes.length ? `${stage.nodes.length} steps` : "Not reached",
    }),
  );
  const copy = element("span", { className: "journey-stage-copy" });
  copy.append(
    element("small", { textContent: stage.eyebrow }),
    element("strong", { textContent: stage.title }),
    tooltipOverflow(element("span", { textContent: stage.summary }), stage.summary),
  );
  button.append(heading, copy);
  if (stage.id === "response") {
    const branches = element("div", { className: "journey-branches compact" });
    branches.append(
      responseBranch("success", "Success", overview.responses.success.slice(0, 1).map(responseLabel), "No 2xx response"),
      responseBranch("error", "Mapped error", overview.responses.sourceErrors.slice(0, 1).map(outcome => outcome.label), "No mapped error in source"),
    );
    button.append(branches);
  }
  button.append(element("span", {
    className: "journey-stage-action",
    textContent: selected ? "Collapse details ↑" : "Expand details ↓",
  }));
  button.addEventListener("click", () => setJourneyStage(stage.id));
  card.append(button);
  if (selected) card.append(renderJourneyExpansion(stage, overview, flow, entry));
  return card;
}

function renderJourneyTabs(overview) {
  if (overview.stages.length === 1) {
    byId("journey-tabs").replaceChildren();
    return;
  }
  const abbreviations = { api: "API", internals: "INT", database: "DB", response: "OUT" };
  const tabs = overview.stages.map(stage => {
    const selected = uiState.journeyStage === stage.id;
    const button = element("button", {
      className: `journey-tab stage-${stage.id}${selected ? " selected" : ""}`,
      attrs: {
        type: "button",
        "aria-label": `Jump to ${stage.label}`,
        "aria-pressed": String(selected),
        title: `Jump to ${stage.label}`,
      },
    });
    button.append(
      element("span", { textContent: String(stage.index + 1).padStart(2, "0") }),
      element("strong", { textContent: abbreviations[stage.id] }),
    );
    button.addEventListener("click", () => {
      if (!selected) setJourneyStage(stage.id);
      document.querySelector(`[data-journey-stage="${CSS.escape(stage.id)}"]`)?.scrollIntoView({ block: "start" });
    });
    return button;
  });
  byId("journey-tabs").replaceChildren(...tabs);
}

function renderJourneyPanel(overview, flow, entry) {
  const container = byId("journey-overview");
  container.hidden = false;
  container.classList.toggle("single-boundary", overview.stages.length === 1);
  byId("journey-guide-eyebrow").textContent = overview.guide.eyebrow;
  byId("journey-guide-title").textContent = overview.guide.title;
  byId("journey-guide-description").textContent = overview.guide.description;
  renderJourneyTabs(overview);
  const track = byId("journey-track");
  track.replaceChildren(...overview.stages.map(stage => renderJourneyStageCard(stage, overview, flow, entry)));
  if (overview.issues.length) {
    track.prepend(element("div", {
      className: "journey-integrity-warning",
      textContent: `${overview.issues.length} source relationship${overview.issues.length === 1 ? "" : "s"} could not be classified. Open the source graph for the unresolved evidence.`,
    }));
  }
}

function renderFlow() {
  renderLensControls();
  const entry = currentEntry();
  const journey = !uiState.rootObjectId
    ? buildSelectedJourneyOverview(uiState.model, entry, uiState.lens)
    : null;
  const flow = journey
    ? uiState.journeyStage
      ? journeyStageFlow(journey, uiState.journeyStage, uiState.query)
      : journey.flow
    : buildVisibleFlow(uiState.model, {
    entry,
    lens: uiState.lens,
    depth: uiState.depth,
    showOutcomes: uiState.showOutcomes,
    query: uiState.query,
    rootObjectId: uiState.rootObjectId,
  });
  renderFlowHeading(entry, flow);
  const list = byId("flow-list");
  const footer = byId("flow-footer");
  const state = byId("system-state");
  const journeyPanel = byId("journey-overview");
  journeyPanel.hidden = !journey;
  if (journey) {
    renderJourneyPanel(journey, flow, entry);
    state.hidden = true;
    list.hidden = true;
    footer.hidden = true;
    if (!uiState.journeyStage) {
      byId("step-stat-label").textContent = "Areas";
      byId("link-stat-label").textContent = "Source";
      byId("step-count").textContent = String(journey.stages.length);
      byId("link-count").textContent = String(journey.flow.nodes.length);
      byId("sql-count").textContent = String(journey.flow.sqlCount);
    } else {
      byId("step-stat-label").textContent = "Steps";
      byId("link-stat-label").textContent = "Links";
    }
    return;
  }
  byId("step-stat-label").textContent = "Steps";
  byId("link-stat-label").textContent = "Links";
  if (!flow.nodes.length) {
    list.hidden = true;
    footer.hidden = true;
    state.hidden = false;
    renderStatePanel(state, {
      mark: "0",
      title: uiState.query ? "No matching source steps" : "No inspectable flow",
      message: uiState.query ? "Change the filter to restore the surrounding call path." : "The selected source entry exposes no bounded first-party calls.",
    });
    return;
  }
  state.hidden = true;
  list.hidden = false;
  footer.hidden = false;
  list.replaceChildren(...flow.nodes.map(renderFlowNode));
  footer.querySelector("p").textContent = flow.truncated
    ? "This path reached an explicit inspection bound; the shown source remains complete within that bound."
    : "Branches show possible control flow, not an observed request trace.";
}

function relationshipButton(object, label) {
  const button = element("button", { className: "relationship-button", attrs: { type: "button" } });
  button.append(
    tooltipOverflow(element("code", { textContent: object.qualname }), object.qualname),
    element("small", { textContent: label }),
  );
  button.addEventListener("click", () => setFocusObject(object.id));
  return button;
}

function objectRelationships(object) {
  const outbound = uiState.model.system.callables.get(object.id)?.calls.map(call => call.object) || [];
  const inbound = [];
  for (const callable of uiState.model.system.callables.values()) {
    if (callable.calls.some(call => call.object.id === object.id)) inbound.push(callable.object);
  }
  return { outbound: unique(outbound.map(item => item.id)).map(id => uiState.model.objects.get(id)).filter(Boolean), inbound };
}

function renderObjectInspector(node) {
  const object = node.object;
  const body = byId("source-inspector-body");
  const summary = element("section", { className: "inspector-summary" });
  const row = element("div", { className: "inspector-summary-row" });
  row.append(element("h2", { textContent: object.qualname }), element("span", { className: "flow-kind", textContent: object.kind.replaceAll("-", " ") }));
  summary.append(row);
  if (object.docstring) summary.append(element("p", { textContent: object.docstring }));
  summary.append(tooltipOverflow(element("code", { className: "inspector-location", textContent: sourceLocation(object) }), sourceLocation(object)));
  const actions = element("div", { className: "inspector-actions" });
  if (uiState.rootObjectId !== object.id) {
    const focus = element("button", { className: "ui-button compact primary", textContent: "Focus here", attrs: { type: "button" } });
    focus.addEventListener("click", () => setFocusObject(object.id));
    actions.append(focus);
  }
  if (uiState.rootObjectId) {
    const reset = element("button", { className: "ui-button compact", textContent: "Return to entry", attrs: { type: "button" } });
    reset.addEventListener("click", () => {
      uiState.rootObjectId = null;
      closeInspector();
      renderFlow();
      updateUrl();
    });
    actions.append(reset);
  }
  if (actions.children.length) summary.append(actions);
  const relationships = objectRelationships(object);
  const relationshipSection = element("section", { className: "inspector-section" });
  relationshipSection.append(element("h3", { textContent: "Relationships" }));
  const relationshipList = element("div", { className: "relationship-list" });
  for (const caller of relationships.inbound.slice(0, 10)) relationshipList.append(relationshipButton(caller, "caller"));
  for (const called of relationships.outbound.slice(0, 10)) relationshipList.append(relationshipButton(called, "calls"));
  if (!relationshipList.children.length) relationshipList.append(element("p", { className: "none-reported", textContent: "No first-party relationships were derived for this definition." }));
  relationshipSection.append(relationshipList);
  const sourceSection = element("section", { className: "inspector-section source-section" });
  sourceSection.append(element("h3", { textContent: "Installed source" }), sourceDefinitionContent(object));
  body.replaceChildren(summary, relationshipSection, sourceSection);
}

function renderQueryInspector(node) {
  const query = node.query;
  const body = byId("source-inspector-body");
  const summary = element("section", { className: "inspector-summary" });
  const row = element("div", { className: "inspector-summary-row" });
  row.append(element("h2", { textContent: query.name }), element("span", { className: "flow-kind", textContent: query.statement }));
  summary.append(row, element("p", {
    textContent: `${query.placeholderCount} parameter placeholder${query.placeholderCount === 1 ? "" : "s"} · ${query.catalogObjects.length} catalog source${query.catalogObjects.length === 1 ? "" : "s"}`,
  }));
  const location = `${query.location.path}:${query.location.definitionLine || "?"}`;
  summary.append(tooltipOverflow(element("code", { className: "inspector-location", textContent: location }), location));
  const facts = element("section", { className: "inspector-section" });
  facts.append(element("h3", { textContent: "Catalog sources" }));
  const chips = element("div", { className: "schema-chips" });
  chips.append(...(query.catalogObjects.length ? query.catalogObjects : ["None"]).map(value => element("code", { textContent: value })));
  facts.append(chips);
  const source = element("section", { className: "inspector-section" });
  source.append(element("h3", { textContent: "Static SQL" }), codeBlock({
    text: query.sql,
    label: location,
    language: "sql",
  }));
  body.replaceChildren(summary, facts, source);
}

function openInspector(node) {
  uiState.selectedNodeKey = node.key;
  byId("source-inspector-title").textContent = node.query?.name || node.object?.name || "Source step";
  byId("system-workspace").dataset.inspector = "open";
  if (node.query) renderQueryInspector(node);
  else renderObjectInspector(node);
  for (const card of document.querySelectorAll(".flow-card.selected")) card.classList.remove("selected");
  document.querySelector(`[data-node-key="${CSS.escape(node.key)}"]`)?.classList.add("selected");
}

function closeInspector() {
  byId("system-workspace").dataset.inspector = "closed";
  uiState.selectedNodeKey = null;
  for (const card of document.querySelectorAll(".flow-card.selected")) card.classList.remove("selected");
}

let entryDialogRestoreFocus = null;

function openEntryDialog() {
  const dialog = byId("entry-dialog");
  if (dialog.open) return;
  entryDialogRestoreFocus = byId("entry-picker-trigger");
  dialog.showModal();
  byId("dialog-entry-search").focus({ preventScroll: true });
}

function closeEntryDialog() {
  const dialog = byId("entry-dialog");
  if (dialog.open) dialog.close();
}

function updateEntrySearch(value) {
  uiState.entryQuery = value;
  renderEntries();
}

function render() {
  if (!uiState.model) return;
  renderLensControls();
  renderEntries();
  renderFlow();
}

function applyInitialState() {
  const parameters = new URLSearchParams(window.location.search);
  const pathLens = window.location.pathname === "/api-map" ? "api" : window.location.pathname === "/db-map" ? "database" : "e2e";
  const requestedLens = text(parameters.get("lens"), pathLens);
  uiState.lens = LENSES.has(requestedLens) ? requestedLens : pathLens;
  const requestedDepth = parameters.get("depth");
  const responsiveDepth = window.matchMedia("(max-width: 720px)").matches ? 1 : 3;
  uiState.depth = requestedDepth === "all" ? "all" : [1, 3].includes(Number(requestedDepth)) ? Number(requestedDepth) : responsiveDepth;
  uiState.showOutcomes = parameters.get("outcomes") === "1";
  const entries = currentEntries();
  const requestedEntry = parameters.get("entry");
  uiState.entryId = entries.some(entry => entry.id === requestedEntry) ? requestedEntry : defaultEntryId(entries, uiState.lens);
  const requestedRoot = parameters.get("root");
  uiState.rootObjectId = uiState.model.objects.has(requestedRoot) ? requestedRoot : null;
  const requestedStage = parameters.get("stage");
  const availableStages = uiState.lens === "e2e" ? JOURNEY_STAGE_IDS : [uiState.lens];
  uiState.journeyStage = !uiState.rootObjectId && availableStages.includes(requestedStage)
    ? requestedStage
    : null;
}

async function fetchDocument(path, signal) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.status === 404 && path.startsWith("/_developer/")) {
    throw new Error("Developer inspection is disabled in this Schemii process");
  }
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

function scheduleRefresh() {
  window.clearTimeout(uiState.refreshTimer);
  uiState.refreshTimer = window.setTimeout(loadSystem, REFRESH_INTERVAL);
}

async function loadSystem() {
  if (uiState.loading || document.visibilityState === "hidden") return;
  uiState.loading = true;
  const refresh = byId("refresh-system");
  setControlLoading(refresh, true, { loadingLabel: "Refreshing system map" });
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    const documents = await Promise.all([
      fetchDocument("/_developer/system", controller.signal),
      fetchDocument("/_developer/routes", controller.signal),
      fetchDocument("/_developer/database", controller.signal),
      fetchDocument("/openapi.json", controller.signal),
    ]);
    const fingerprint = JSON.stringify(documents);
    if (!uiState.model || fingerprint !== uiState.fingerprint) {
      uiState.model = buildSystemMapModel(...documents);
      if (!uiState.fingerprint) applyInitialState();
      else {
        const entries = currentEntries();
        if (!entries.some(entry => entry.id === uiState.entryId)) uiState.entryId = defaultEntryId(entries, uiState.lens);
        if (uiState.rootObjectId && !uiState.model.objects.has(uiState.rootObjectId)) uiState.rootObjectId = null;
      }
      uiState.fingerprint = fingerprint;
      render();
    }
    const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    byId("inspection-status").classList.remove("error");
    const generation = uiState.model.system.analysis.generation === "application-startup"
      ? "Startup snapshot" : "Source-derived";
    byId("inspection-status").textContent = `${generation} · fetched ${checkedAt}`;
  } catch (error) {
    const displayed = error?.name === "AbortError" ? new Error("System inspection timed out after 10 seconds") : error;
    if (uiState.model) {
      byId("inspection-status").classList.add("error");
      byId("inspection-status").textContent = "Refresh failed · showing last snapshot";
    } else {
      const state = byId("system-state");
      state.hidden = false;
      renderStatePanel(state, {
        mark: "!",
        title: "System map unavailable",
        message: displayed instanceof Error ? displayed.message : "The inspection request failed.",
        variant: "error",
      });
    }
  } finally {
    window.clearTimeout(timeout);
    uiState.loading = false;
    setControlLoading(refresh, false);
    scheduleRefresh();
  }
}

function start() {
  initializeUi();
  byId("browse-select").addEventListener("change", event => setLens(event.target.value));
  for (const button of document.querySelectorAll("[data-depth]")) {
    button.addEventListener("click", () => {
      uiState.depth = button.dataset.depth === "all" ? "all" : Number(button.dataset.depth);
      render();
      updateUrl();
    });
  }
  byId("entry-picker-trigger").addEventListener("click", openEntryDialog);
  byId("close-entry-dialog").addEventListener("click", closeEntryDialog);
  for (const input of [byId("rail-entry-search"), byId("dialog-entry-search")]) {
    input.addEventListener("input", event => updateEntrySearch(event.target.value));
  }
  byId("entry-dialog").addEventListener("click", event => {
    if (event.target === byId("entry-dialog")) closeEntryDialog();
  });
  byId("entry-dialog").addEventListener("close", () => {
    const target = entryDialogRestoreFocus;
    entryDialogRestoreFocus = null;
    window.setTimeout(() => {
      if (target?.isConnected) target.focus({ preventScroll: true });
    }, 0);
  });
  byId("flow-search").addEventListener("input", event => {
    uiState.query = event.target.value.trim().toLocaleLowerCase();
    renderFlow();
  });
  byId("show-outcomes").addEventListener("change", event => {
    uiState.showOutcomes = event.target.checked;
    renderFlow();
    updateUrl();
  });
  byId("close-source-inspector").addEventListener("click", closeInspector);
  byId("refresh-system").addEventListener("click", loadSystem);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadSystem();
    else window.clearTimeout(uiState.refreshTimer);
  });
  loadSystem();
}

if (typeof document !== "undefined" && document.getElementById("system-shell")) start();
