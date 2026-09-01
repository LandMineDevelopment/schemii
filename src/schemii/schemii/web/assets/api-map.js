import { ApiGraph } from "./api-graph.js";
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
  normalizeInspectedObject,
  pythonSourceExcerpt,
  sourceDefinitionCard,
  sourceDefinitionContent,
  sourceLocation,
  temporaryIconState,
} from "./source-inspection.js";

export { pythonSourceExcerpt };

const HTTP_METHODS = ["get", "post", "put", "patch", "delete", "options", "head", "trace"];
const METHOD_ORDER = new Map(HTTP_METHODS.map((method, index) => [method, index]));
const CONTRACT_REFRESH_INTERVAL = 30_000;
const CONTRACT_REQUEST_TIMEOUT = 10_000;
const INSPECTION_REQUEST_TIMEOUT = 3_000;
const CONTRACT_MAX_DEPTH = 4;
const CONTRACT_MAX_PROPERTIES = 40;
const CONTRACT_MAX_NODES = 400;

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

function localReferenceValue(specification, pointer) {
  if (!pointer) return undefined;
  let current = specification;
  for (const token of pointer.slice(2).split("/")) {
    let decoded = token;
    try {
      decoded = decodeURIComponent(token);
    } catch {
      // Leave malformed URI escapes untouched so the reference fails closed.
    }
    const key = decoded.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!current || typeof current !== "object" || !Object.hasOwn(current, key)) return undefined;
    current = current[key];
  }
  return current;
}

function resolveLocalReference(specification, value) {
  let source = asRecord(value);
  const visited = new Set();
  let pointer = localPointer(source.$ref);
  while (pointer && !visited.has(pointer)) {
    visited.add(pointer);
    const target = localReferenceValue(specification, pointer);
    if (!target || typeof target !== "object") break;
    const siblings = Object.fromEntries(Object.entries(source).filter(([key]) => key !== "$ref"));
    source = { ...asRecord(target), ...siblings };
    pointer = localPointer(source.$ref);
  }
  return source;
}

function resolvedBooleanSchema(specification, value) {
  let source = value;
  const visited = new Set();
  while (source && typeof source === "object" && !Array.isArray(source)) {
    const pointer = localPointer(source.$ref);
    if (!pointer || visited.has(pointer)) return null;
    visited.add(pointer);
    source = localReferenceValue(specification, pointer);
  }
  return typeof source === "boolean" ? source : null;
}

function contentSchemaLabels(content) {
  const labels = [];
  for (const mediaType of Object.values(asRecord(content))) {
    const schema = asRecord(mediaType).schema;
    labels.push(...schemaLabels(schema));
  }
  return unique(labels);
}

function scalarDefault(value) {
  if (value === null) return "null";
  if (["string", "number", "boolean"].includes(typeof value)) return JSON.stringify(value);
  return "";
}

function contractType(source) {
  if (Array.isArray(source.type)) return source.type.join(" | ");
  if (typeof source.type === "string") return source.type;
  if (source.properties) return "object";
  if (source.oneOf) return "oneOf";
  if (source.anyOf) return "anyOf";
  if (source.allOf) return "allOf";
  return "value";
}

function contractConstraints(source) {
  const labels = {
    minLength: "min length",
    maxLength: "max length",
    minimum: "minimum",
    maximum: "maximum",
    exclusiveMinimum: "greater than",
    exclusiveMaximum: "less than",
    minItems: "min items",
    maxItems: "max items",
    pattern: "pattern",
  };
  return Object.entries(labels)
    .filter(([key]) => source[key] !== undefined)
    .map(([key, label]) => `${label}: ${source[key]}`);
}

export function buildSchemaContract(specification, schema, {
  depth = 0,
  pointers = new Set(),
  budget = { remaining: CONTRACT_MAX_NODES },
} = {}) {
  const raw = asRecord(schema);
  const pointer = localPointer(raw.$ref);
  const reference = schemaReferenceName(raw.$ref);
  const booleanSchema = resolvedBooleanSchema(specification, schema);
  if (budget.remaining <= 0) {
    return {
      label: reference || "additional shape",
      type: "value",
      format: "",
      description: "",
      reference: reference || "",
      nullable: false,
      default: "",
      constraints: [],
      enum: [],
      enumTruncated: false,
      properties: [],
      items: null,
      additionalProperties: null,
      branches: [],
      recursive: false,
      truncated: true,
    };
  }
  budget.remaining -= 1;
  if (booleanSchema !== null) {
    return {
      label: booleanSchema ? "any value" : "no values allowed",
      type: booleanSchema ? "any" : "never",
      format: "",
      description: "",
      reference: reference || "",
      nullable: booleanSchema,
      default: "",
      constraints: [],
      enum: [],
      enumTruncated: false,
      properties: [],
      items: null,
      additionalProperties: null,
      branches: [],
      recursive: false,
      truncated: false,
    };
  }
  const source = resolveLocalReference(specification, raw);
  const enumValues = Array.isArray(source.enum) ? source.enum : [];
  const contract = {
    label: reference || schemaLabel(source) || "value",
    type: contractType(source),
    format: text(source.format),
    description: text(source.description),
    reference: reference || "",
    nullable: source.nullable === true || (Array.isArray(source.type) && source.type.includes("null")),
    default: source.default === undefined ? "" : scalarDefault(source.default),
    constraints: contractConstraints(source),
    enum: enumValues.slice(0, 12).map(String),
    enumTruncated: enumValues.length > 12,
    properties: [],
    items: null,
    additionalProperties: null,
    branches: [],
    recursive: Boolean(pointer && pointers.has(pointer)),
    truncated: false,
  };
  if (contract.recursive) return contract;
  const hasChildren = source.properties || source.items || source.additionalProperties === true
    || (source.additionalProperties && typeof source.additionalProperties === "object")
    || source.oneOf || source.anyOf || source.allOf;
  if (depth >= CONTRACT_MAX_DEPTH) {
    contract.truncated = Boolean(hasChildren);
    return contract;
  }
  const nextPointers = new Set(pointers);
  if (pointer) nextPointers.add(pointer);
  const required = new Set(Array.isArray(source.required) ? source.required : []);
  const propertyEntries = Object.entries(asRecord(source.properties));
  for (const [name, property] of propertyEntries.slice(0, CONTRACT_MAX_PROPERTIES)) {
    if (budget.remaining <= 0) {
      contract.truncated = true;
      break;
    }
    contract.properties.push({
      name,
      required: required.has(name),
      contract: buildSchemaContract(specification, property, { depth: depth + 1, pointers: nextPointers, budget }),
    });
  }
  if (propertyEntries.length > CONTRACT_MAX_PROPERTIES) contract.truncated = true;
  if (source.items) {
    if (budget.remaining > 0) {
      contract.items = buildSchemaContract(specification, source.items, { depth: depth + 1, pointers: nextPointers, budget });
    } else {
      contract.truncated = true;
    }
  }
  if (source.additionalProperties === false) {
    contract.constraints.push("additional properties: not allowed");
  } else if (source.additionalProperties === true || (source.additionalProperties && typeof source.additionalProperties === "object")) {
    if (budget.remaining > 0) {
      contract.additionalProperties = buildSchemaContract(specification, source.additionalProperties, {
        depth: depth + 1,
        pointers: nextPointers,
        budget,
      });
    } else {
      contract.truncated = true;
    }
  }
  for (const keyword of ["oneOf", "anyOf", "allOf"]) {
    if (!Array.isArray(source[keyword])) continue;
    for (const [index, branch] of source[keyword].slice(0, 8).entries()) {
      if (budget.remaining <= 0) {
        contract.truncated = true;
        break;
      }
      contract.branches.push({
        label: `${keyword} ${index + 1}`,
        contract: buildSchemaContract(specification, branch, { depth: depth + 1, pointers: nextPointers, budget }),
      });
    }
    if (source[keyword].length > 8) contract.truncated = true;
  }
  return contract;
}

function contentModels(specification, content) {
  return Object.entries(asRecord(content)).map(([mediaType, value]) => ({
    mediaType,
    contract: buildSchemaContract(specification, asRecord(value).schema),
  }));
}

function parameterModel(specification, parameter) {
  const source = resolveLocalReference(specification, parameter);
  const content = contentModels(specification, source.content);
  const schemas = source.schema
    ? schemaLabels(source.schema)
    : contentSchemaLabels(source.content);
  return {
    name: text(source.name, "unnamed"),
    location: text(source.in, "unknown"),
    required: Boolean(source.required),
    description: text(source.description),
    schema: schemas.join(" | ") || "value",
    contract: source.schema ? buildSchemaContract(specification, source.schema) : content[0]?.contract || null,
  };
}

function responseModel(specification, status, response) {
  const source = resolveLocalReference(specification, response);
  return {
    status,
    description: text(source.description, "Documented response"),
    schemas: contentSchemaLabels(source.content),
    content: contentModels(specification, source.content),
  };
}

function inspectionRoutes(document) {
  const source = asRecord(document);
  if (source.schemaVersion !== 1 || !Array.isArray(source.routes) || !Array.isArray(source.objects)) {
    return { available: false, routes: new Map() };
  }
  const objects = new Map(source.objects.map(normalizeInspectedObject).filter(item => item.id).map(item => [item.id, item]));
  const routes = new Map();
  for (const rawRoute of source.routes) {
    const route = asRecord(rawRoute);
    const id = text(route.id);
    const endpoint = objects.get(route.endpointId);
    if (!id || !endpoint) continue;
    const dependencies = (Array.isArray(route.dependencies) ? route.dependencies : [])
      .map(item => ({ ...asRecord(item), object: objects.get(item?.objectId) }))
      .filter(item => item.object);
    const calls = (Array.isArray(route.calls) ? route.calls : [])
      .map(item => ({ ...asRecord(item), object: objects.get(item?.objectId) }))
      .filter(item => item.object);
    const resolveObjects = ids => (Array.isArray(ids) ? ids : []).map(objectId => objects.get(objectId)).filter(Boolean);
    routes.set(id, {
      endpoint,
      dependencies,
      calls,
      requestObjects: resolveObjects(route.requestObjectIds),
      responseObjects: resolveObjects(route.responseObjectIds),
      implementationDigest: text(route.implementationDigest),
      truncated: {
        dependencies: route.truncated?.dependencies === true,
        calls: route.truncated?.calls === true,
        requestObjects: route.truncated?.requestObjects === true,
        responseObjects: route.truncated?.responseObjects === true,
      },
    });
  }
  return { available: true, routes };
}

function mergedParameterSources(specification, pathItem, operation) {
  const parameters = new Map();
  const add = parameter => {
    const source = resolveLocalReference(specification, parameter);
    const key = `${text(source.in, "unknown")}:${text(source.name, "unnamed")}`;
    parameters.set(key, source);
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

function operationModel(specification, path, method, pathItem, operation, inspection) {
  const source = asRecord(operation);
  const requestBody = resolveLocalReference(specification, source.requestBody);
  const parameterSources = mergedParameterSources(specification, pathItem, source);
  const parameters = parameterSources.map(parameter => parameterModel(specification, parameter));
  const responses = Object.entries(asRecord(source.responses))
    .sort(statusSort)
    .map(([status, response]) => responseModel(specification, status, response));
  const requestSchemas = contentSchemaLabels(requestBody.content);
  const responseSchemas = unique(responses.flatMap(response => response.schemas));
  const parameterSchemaReferences = unique(parameterSources.flatMap(parameter => collectSchemaNames(parameter)));
  const requestSchemaReferences = collectSchemaNames(requestBody.content);
  const responseSchemaReferences = unique(Object.values(asRecord(source.responses)).flatMap(response => {
    const resolved = resolveLocalReference(specification, response);
    return collectSchemaNames(resolved.content);
  }));
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
    lifecycle: text(source["x-schemii-status"], "implemented"),
    deprecated: Boolean(source.deprecated),
    parameters,
    request: {
      required: Boolean(requestBody.required),
      schemas: requestSchemas,
      content: contentModels(specification, requestBody.content),
    },
    responses,
    schemas: unique([...parameterSchemaReferences, ...requestSchemas, ...responseSchemas]),
    graph: {
      parameterSchemas: parameterSchemaReferences,
      requestSchemas: requestSchemaReferences,
      responseSchemas: responseSchemaReferences,
    },
    story: inspection.routes.get(`${method}:${path}`) || null,
    inspectionAvailable: inspection.available,
  };
}

export function buildApiMapModel(document, inspectionDocument = null) {
  const specification = asRecord(document);
  const inspection = inspectionRoutes(inspectionDocument);
  const paths = asRecord(specification.paths);
  const componentSchemas = asRecord(specification.components?.schemas);
  const componentSchemaNames = new Set(Object.keys(componentSchemas));
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
      operations.push(operationModel(specification, path, method.toLowerCase(), pathItem, rawOperation, inspection));
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
    schemaCount: componentSchemaNames.size,
    schemas: Object.entries(componentSchemas).map(([name, schema]) => ({
      name,
      kind: schemaLabel(schema) || "schema",
      description: text(asRecord(schema).description),
      references: collectSchemaNames(schema).filter(reference => componentSchemaNames.has(reference) && reference !== name),
    })),
    operations,
    groups: [...groupsByTag.values()],
    implementationInspectionAvailable: inspection.available,
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

function compactUnique(values) {
  return [...new Set(values.filter(Boolean))];
}

function contractFacts(contract) {
  const facts = [...contract.constraints];
  if (contract.format) facts.push(`format: ${contract.format}`);
  if (contract.default) facts.push(`default: ${contract.default}`);
  if (contract.enum.length) facts.push(`values: ${contract.enum.join(", ")}${contract.enumTruncated ? ", …" : ""}`);
  if (contract.nullable) facts.push("nullable");
  if (contract.recursive) facts.push("recursive reference");
  if (contract.truncated) facts.push("additional shape omitted");
  return facts;
}

function contractNode(contract, {
  name = "",
  required = false,
  open = false,
} = {}) {
  const hasChildren = contract.properties.length || contract.items || contract.additionalProperties || contract.branches.length;
  const wrapper = element(hasChildren ? "details" : "div", { className: "shape-node" });
  if (hasChildren) wrapper.open = open;
  const heading = element(hasChildren ? "summary" : "div", { className: "shape-line" });
  heading.append(
    element("code", { textContent: name || contract.label }),
    element("span", { textContent: name ? contract.label : contract.type }),
  );
  if (required) heading.append(element("em", { textContent: "required" }));
  wrapper.append(heading);
  if (contract.description) wrapper.append(element("p", { className: "shape-description", textContent: contract.description }));
  const facts = contractFacts(contract);
  if (facts.length) wrapper.append(element("p", { className: "shape-facts", textContent: facts.join(" · ") }));
  if (!hasChildren) return wrapper;
  const renderChildren = () => {
    if (wrapper.dataset.childrenRendered === "true") return;
    wrapper.dataset.childrenRendered = "true";
    const children = element("div", { className: "shape-children" });
    for (const property of contract.properties) {
      children.append(contractNode(property.contract, {
        name: property.name,
        required: property.required,
        open: false,
      }));
    }
    if (contract.items) children.append(contractNode(contract.items, { name: "items", open: false }));
    if (contract.additionalProperties) {
      children.append(contractNode(contract.additionalProperties, { name: "[key: string]", open: false }));
    }
    for (const branch of contract.branches) children.append(contractNode(branch.contract, { name: branch.label, open: false }));
    wrapper.append(children);
  };
  if (wrapper.open) renderChildren();
  wrapper.addEventListener("toggle", () => {
    if (wrapper.open) renderChildren();
  });
  return wrapper;
}

function contractMarker(contract, { required = null } = {}) {
  const parts = [];
  if (contract.enum.length) parts.push(contract.enum.join(" | ") + (contract.enumTruncated ? " | …" : ""));
  else parts.push(contract.reference || contract.type || "value");
  if (contract.format) parts.push(contract.format);
  parts.push(...contract.constraints);
  if (contract.default) parts.push(`default ${contract.default}`);
  if (contract.nullable) parts.push("nullable");
  if (required === false) parts.push("optional");
  if (contract.recursive) parts.push("recursive");
  if (contract.truncated) parts.push("partial");
  return `<${parts.join("; ")}>`;
}

function contractShapeValue(contract, { required = null, depth = 0 } = {}) {
  if (contract.recursive || depth >= 5) return contractMarker(contract, { required });
  if (contract.branches.length) {
    const branchValues = contract.branches.map(branch => contractShapeValue(branch.contract, { depth: depth + 1 }));
    if (contract.type === "allOf" && branchValues.every(value => value && typeof value === "object" && !Array.isArray(value))) {
      return Object.assign({}, ...branchValues);
    }
    const alternatives = contract.branches.map(branch => branch.contract.reference || branch.contract.label || branch.contract.type);
    return `<${contract.type}: ${alternatives.join(" | ")}${required === false ? "; optional" : ""}>`;
  }
  if (contract.items || contract.type === "array") {
    return [contract.items
      ? contractShapeValue(contract.items, { depth: depth + 1 })
      : "<value>"];
  }
  if (contract.type === "object" || contract.properties.length || contract.additionalProperties) {
    const value = {};
    for (const property of contract.properties.slice(0, 24)) {
      value[property.name] = contractShapeValue(property.contract, {
        required: property.required,
        depth: depth + 1,
      });
    }
    if (contract.additionalProperties) {
      value["<key>"] = contractShapeValue(contract.additionalProperties, { depth: depth + 1 });
    }
    if (contract.truncated || contract.properties.length > 24) value["…"] = "<additional fields omitted>";
    return value;
  }
  return contractMarker(contract, { required });
}

export function contractJsonShape(contract) {
  return JSON.stringify(contractShapeValue(contract), null, 2);
}

function fieldBrowser(contract) {
  const details = element("details", { className: "field-browser" });
  details.append(element("summary", { textContent: "Browse fields and constraints" }));
  details.addEventListener("toggle", () => {
    if (!details.open || details.dataset.fieldsRendered === "true") return;
    details.dataset.fieldsRendered = "true";
    details.append(contractNode(contract, { open: true }));
  });
  return details;
}

function contractContent(content, {
  empty = "No documented JSON body.",
  linkedObjects = [],
} = {}) {
  const container = element("div", { className: "shape-content" });
  if (!content.length) {
    container.append(element("p", { className: "none-reported", textContent: empty }));
    return container;
  }
  for (const item of content) {
    const media = element("section", { className: "media-contract" });
    const heading = element("div", { className: "media-contract-head" });
    heading.append(
      element("span", {
        className: "media-type",
        textContent: `${item.mediaType} · contract shape`,
        attrs: {
          "data-ui-tooltip": "Derived from the OpenAPI contract; this is not example data.",
          "data-ui-tooltip-touch": "true",
        },
      }),
      element("code", { textContent: item.contract.reference || item.contract.label }),
    );
    const shape = contractJsonShape(item.contract);
    media.append(
      heading,
      codeBlock({ text: shape, tokens: [], label: "JSON shape", language: "json" }),
      fieldBrowser(item.contract),
    );
    const modelObjects = [...new Map(
      linkedObjects.filter(object => object.source.available).map(object => [object.id, object]),
    ).values()];
    if (modelObjects.length) {
      const models = element("details", { className: "linked-models" });
      models.append(element("summary", { textContent: `Python model${modelObjects.length === 1 ? "" : "s"} · ${modelObjects.length}` }));
      for (const object of modelObjects) models.append(sourceDefinitionCard(object));
      media.append(models);
    }
    container.append(media);
  }
  return container;
}

function requestDetail(operation) {
  const content = element("div", { className: "stage-sections" });
  const parameters = element("section", { className: "stage-detail-section stage-parameters-section" });
  parameters.append(element("h4", {
    textContent: operation.parameters.length
      ? `Transport parameters · ${operation.parameters.length}`
      : "Transport parameters · none",
  }));
  if (operation.parameters.length) {
    const list = element("div", { className: "contract-list" });
    for (const parameter of operation.parameters) {
      const item = element("article", { className: "contract-item" });
      const title = element("div");
      title.append(
        element("code", { textContent: parameter.name }),
        element("span", { textContent: parameter.required ? `${parameter.location} · required` : parameter.location }),
      );
      item.append(title, element("p", { textContent: parameter.description || parameter.schema }));
      if (parameter.contract) item.append(fieldBrowser(parameter.contract));
      list.append(item);
    }
    parameters.append(list);
  }
  const body = element("section", { className: "stage-detail-section stage-body-section" });
  body.append(element("h4", { textContent: `JSON body${operation.request.required ? " · required" : ""}` }));
  body.append(contractContent(operation.request.content, {
    linkedObjects: operation.story?.requestObjects || [],
  }));
  content.append(parameters, body);
  return content;
}

function dependenciesDetail(dependencies) {
  const content = element("div", { className: "definition-list" });
  for (const dependency of dependencies) content.append(sourceDefinitionCard(dependency.object));
  return content;
}

function collaboratorDetail(story, call) {
  const content = element("div", { className: "collaborator-detail" });
  content.append(element("p", {
    className: "inference-note",
    textContent: `Direct first-party call inferred from the registered handler at line ${call.line || "?"}.`,
  }));
  const controls = element("div", { className: "source-view-switch ui-segmented", attrs: { role: "group", "aria-label": "Source view" } });
  const callSiteButton = element("button", {
    textContent: "Call site",
    attrs: { type: "button", "aria-pressed": "true", "data-ui-tooltip": "Show the call in its handler context" },
  });
  const definitionButton = element("button", {
    textContent: "Definition",
    attrs: { type: "button", "aria-pressed": "false", "data-ui-tooltip": "Show the called definition" },
  });
  const callSite = element("div", { className: "source-view" });
  const definition = element("div", { className: "source-view", attrs: { hidden: "" } });
  const excerpt = pythonSourceExcerpt(story.endpoint, call.line);
  callSite.append(excerpt
    ? codeBlock({
      text: excerpt.text,
      tokens: excerpt.tokens,
      label: `${story.endpoint.location.path}:${excerpt.startLine}–${excerpt.endLine}`,
    })
    : codeBlock({ text: call.expression, tokens: [["plain", call.expression]], label: "Call expression" }));
  let definitionRendered = false;
  const activate = name => {
    const showCallSite = name === "call-site";
    callSite.hidden = !showCallSite;
    definition.hidden = showCallSite;
    callSiteButton.setAttribute("aria-pressed", showCallSite ? "true" : "false");
    definitionButton.setAttribute("aria-pressed", showCallSite ? "false" : "true");
    if (!showCallSite && !definitionRendered) {
      definitionRendered = true;
      definition.append(sourceDefinitionContent(call.object));
    }
  };
  callSiteButton.addEventListener("click", () => activate("call-site"));
  definitionButton.addEventListener("click", () => activate("definition"));
  controls.append(callSiteButton, definitionButton);
  content.append(controls, callSite, definition);
  return content;
}

function responseStatusKind(status) {
  if (status.startsWith("2")) return "success";
  if (status.startsWith("3")) return "redirect";
  if (status.startsWith("4")) return "client-error";
  if (status.startsWith("5")) return "server-error";
  return "default";
}

function responseDetail(operation) {
  const content = element("div", { className: "response-detail" });
  const heading = element("div", { className: "stage-detail-heading" });
  heading.append(element("h4", { textContent: `Documented responses · ${operation.responses.length}` }));
  const responseList = element("div", { className: "response-contracts" });
  for (const response of operation.responses) {
    const item = element("details", { className: `response-contract response-${responseStatusKind(response.status)}` });
    const summary = element("summary");
    summary.append(element("strong", { textContent: response.status }), element("span", { textContent: response.description }));
    item.append(summary);
    const render = () => {
      if (item.dataset.contractRendered === "true") return;
      item.dataset.contractRendered = "true";
      const responseObjects = operation.story?.responseObjects || [];
      const inspectedRootPresent = response.schemas.some(schema => responseObjects.some(object => object.name === schema));
      const linkedObjects = inspectedRootPresent ? responseObjects : [];
      item.append(contractContent(response.content, {
        empty: "No documented response body.",
        linkedObjects,
      }));
    };
    item.addEventListener("toggle", () => {
      if (item.open) render();
    });
    responseList.append(item);
  }
  content.append(heading, responseList);
  return content;
}

function stageLinkButton(operationId, stageId) {
  const label = "Copy link to this stage";
  const button = createIconButton({
    icon: "link",
    label,
    placement: "left",
    className: "compact stage-link",
  });
  const reset = () => {
    button.classList.remove("copied", "copy-failed");
    decorateIconControl(button, { icon: "link", label, tooltip: label, placement: "left" });
  };
  button.addEventListener("click", async () => {
    const url = new URL(window.location.href);
    url.searchParams.set("operation", operationId);
    url.hash = stageId;
    try {
      await navigator.clipboard.writeText(url.toString());
      temporaryIconState(button, { icon: "check", label: "Stage link copied", className: "copied" }, reset);
    } catch {
      temporaryIconState(button, { icon: "close", label: "Copy failed", className: "copy-failed" }, reset, 2_000);
    }
  });
  return button;
}

export function routeStageInitiallyOpen(stageId, hash = "") {
  return hash === `#${stageId}`;
}

export function collaboratorStageId(call) {
  const object = call?.object || {};
  const label = (object.name || object.qualname || object.kind || "call")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 28) || "call";
  const identity = `${object.id || object.qualname || object.name || object.kind || "call"}|${call?.expression || ""}|${call?.line || ""}`;
  let fingerprint = 2_166_136_261;
  for (let index = 0; index < identity.length; index += 1) {
    fingerprint ^= identity.charCodeAt(index);
    fingerprint = Math.imul(fingerprint, 16_777_619);
  }
  return `operation-stage-collaborator-${label}-${(fingerprint >>> 0).toString(36)}`;
}

function routeStage(operation, {
  kind,
  id,
  title,
  copy,
  meta = "",
  renderDetail,
}) {
  const stage = element("li", { className: `route-stage stage-${kind}`, attrs: { id } });
  const marker = element("span", { className: "stage-marker", attrs: { "aria-hidden": "true" } });
  const card = element("details", { className: "route-stage-card" });
  const stageHash = typeof window !== "undefined" && window.location.hash.startsWith("#operation-stage-")
    ? window.location.hash
    : "";
  card.open = routeStageInitiallyOpen(id, stageHash);
  const summary = element("summary", { attrs: { tabindex: "0" } });
  const summaryCopy = element("span", { className: "stage-summary-copy" });
  const summaryHeading = element("span", { className: "stage-summary-heading" });
  summaryHeading.append(element("strong", { textContent: title }));
  if (meta) {
    summaryHeading.append(element("code", {
      textContent: meta,
      attrs: {
        "data-ui-tooltip-overflow": meta,
        "data-ui-tooltip-touch": "true",
        "data-ui-tooltip-placement": "top",
      },
    }));
  }
  summaryCopy.append(summaryHeading, element("p", { textContent: copy }));
  const disclosure = createIconElement("expand");
  disclosure.classList.add("stage-disclosure");
  const summaryActions = element("span", { className: "stage-summary-actions" });
  const stageLink = stageLinkButton(operation.id, id);
  stageLink.addEventListener("click", event => event.stopPropagation());
  summaryActions.append(stageLink, disclosure);
  summary.append(summaryCopy, summaryActions);
  card.append(summary);
  const render = () => {
    if (!card.open || card.dataset.stageRendered === "true") return;
    card.dataset.stageRendered = "true";
    const detail = element("div", { className: "stage-detail" });
    detail.append(renderDetail());
    card.append(detail);
  };
  if (card.open) render();
  card.addEventListener("toggle", () => {
    render();
  });
  stage.append(marker, card);
  return stage;
}

function synchronizeLinkedStage({ focus = false, closeWithoutLink = false } = {}) {
  const stageHash = window.location.hash.startsWith("#operation-stage-") ? window.location.hash : "";
  if (!stageHash && !closeWithoutLink) return;
  let linkedStage = null;
  let linkedCard = null;
  for (const stage of document.querySelectorAll(".route-stage")) {
    const card = stage.querySelector(":scope > .route-stage-card");
    const linked = routeStageInitiallyOpen(stage.id, stageHash);
    if (card.open !== linked) card.open = linked;
    if (linked) {
      linkedStage = stage;
      linkedCard = card;
    }
  }
  if (!linkedStage || !linkedCard) return;
  window.setTimeout(() => {
    if (!linkedStage.isConnected) return;
    linkedStage.scrollIntoView({ block: "start" });
    if (focus) linkedCard.querySelector(":scope > summary")?.focus({ preventScroll: true });
  }, 0);
}

function renderRouteStory(operation) {
  const panel = element("section", { className: "route-story" });
  const story = operation.story;
  const planned = operation.lifecycle === "planned";
  const provenance = element("div", { className: `story-provenance${planned ? " planned" : ""}` });
  provenance.append(
    element("span", { textContent: planned ? "Planned contract" : story ? "Live Python inspection" : "OpenAPI contract only" }),
    element("small", {
      textContent: story
        ? planned
          ? "Typed route + bounded TODO source analysis"
          : "Registered route + bounded source analysis"
        : operation.inspectionAvailable
          ? "No implementation metadata matched this route"
          : "Developer inspection is disabled",
    }),
  );
  const intent = element("section", { className: "story-intent" });
  intent.append(
    element("span", { className: "eyebrow", textContent: "Intent" }),
    element("p", { textContent: story?.endpoint.docstring || operation.description || operation.summary }),
  );
  panel.append(provenance, intent);

  const flow = element("ol", { className: "route-flow", attrs: { "aria-label": "Derived route flow" } });
  const requestNames = compactUnique([
    ...operation.parameters.map(parameter => `${parameter.location}:${parameter.name}`),
    ...(story?.requestObjects.map(item => item.name) || []),
    ...operation.request.schemas,
  ]);
  flow.append(routeStage(operation, {
    kind: "request",
    id: "operation-stage-request",
    title: "Incoming request",
    copy: requestNames.length ? requestNames.join(" · ") : "No body or explicit contract parameters",
    meta: `${operation.method.toUpperCase()} ${operation.path}`,
    renderDetail: () => requestDetail(operation),
  }));

  if (story?.dependencies.length) {
    flow.append(routeStage(operation, {
      kind: "dependency",
      id: "operation-stage-dependencies",
      title: "Resolve dependencies",
      copy: story.dependencies.map(item => item.object.name).join(" · "),
      meta: story.dependencies.map(item => item.parameterName).filter(Boolean).join(" · "),
      renderDetail: () => dependenciesDetail(story.dependencies),
    }));
  }

  flow.append(routeStage(operation, {
    kind: "handler",
    id: "operation-stage-handler",
    title: story?.endpoint.name || operation.operationId || operation.summary,
    copy: story ? "Execute the registered FastAPI handler" : "Handler source is unavailable in contract-only mode",
    meta: story ? sourceLocation(story.endpoint) : operation.operationId,
    renderDetail: () => story
      ? sourceDefinitionContent(story.endpoint)
      : element("p", { className: "none-reported", textContent: "Enable developer inspection to view installed handler source." }),
  }));

  const collaborators = story?.calls.filter(call => ["service", "repository", "gateway"].includes(call.object.kind)) || [];
  collaborators.forEach(call => {
    flow.append(routeStage(operation, {
      kind: call.object.kind,
      id: collaboratorStageId(call),
      title: call.object.qualname || call.object.name,
      copy: `Direct call inferred at line ${call.line || "?"}`,
      meta: call.expression,
      renderDetail: () => collaboratorDetail(story, call),
    }));
  });

  const helpers = story?.calls.filter(call => ["outcome", "helper", "function"].includes(call.object.kind)) || [];
  if (helpers.length) {
    const helperObjects = [...new Map(helpers.map(call => [call.object.id, call.object])).values()];
    flow.append(routeStage(operation, {
      kind: "helper",
      id: "operation-stage-helpers",
      title: "Helpers and outcomes",
      copy: helperObjects.map(object => object.name).join(" · "),
      meta: `${helperObjects.length} linked definition${helperObjects.length === 1 ? "" : "s"}`,
      renderDetail: () => {
        const content = element("div", { className: "definition-list" });
        helperObjects.forEach(object => content.append(sourceDefinitionCard(object)));
        return content;
      },
    }));
  }

  const responseNames = compactUnique([
    ...(story?.responseObjects.map(item => item.name) || []),
    ...operation.responses.flatMap(response => response.schemas),
  ]);
  flow.append(routeStage(operation, {
    kind: "response",
    id: "operation-stage-response",
    title: "Shape response",
    copy: responseNames.length ? responseNames.join(" · ") : "Response without a JSON schema",
    meta: operation.responses.map(response => response.status).join(" · "),
    renderDetail: () => responseDetail(operation),
  }));
  panel.append(flow);

  if (story) {
    const notes = element("footer", { className: "story-notes" });
    const truncatedSections = Object.entries(story.truncated)
      .filter(([, truncated]) => truncated)
      .map(([name]) => name);
    if (truncatedSections.length) {
      notes.append(element("p", {
        className: "analysis-limit-note",
        textContent: `Inspection limit reached for ${truncatedSections.join(", ")}; this route story is partial.`,
      }));
    }
    if (story.implementationDigest) {
      notes.append(element("p", {
        className: "implementation-fingerprint",
        textContent: `Implementation fingerprint ${story.implementationDigest.slice(0, 12)}`,
      }));
    }
    panel.append(notes);
  }
  return panel;
}

const uiState = {
  model: null,
  selectedId: null,
  query: "",
  contractFingerprint: null,
  loading: false,
  refreshTimer: null,
  detailSignature: null,
  view: "list",
  canvasFitted: false,
};

let apiGraph = null;
let operationPane = null;

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
    operation.lifecycle,
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
  if (reveal) {
    const url = new URL(window.location.href);
    url.searchParams.set("operation", operationId);
    url.hash = "";
    window.history.replaceState(null, "", url);
  }
  document.querySelectorAll(".operation-node").forEach(button => {
    const selected = button.dataset.operationId === operationId;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  apiGraph?.setSelectedOperation(operationId);
  const groupMenuTrigger = byId("group-menu-trigger");
  const groupMenuLabel = `Browse route groups. Current group: ${operation.primaryTag}`;
  groupMenuTrigger.setAttribute("aria-label", groupMenuLabel);
  groupMenuTrigger.dataset.uiTooltip = groupMenuLabel;
  for (const link of byId("group-links").querySelectorAll("a")) {
    const current = link.dataset.groupName === operation.primaryTag;
    link.classList.toggle("active", current);
    if (current) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
  const signature = JSON.stringify(operation);
  if (signature !== uiState.detailSignature) {
    renderOperationDetail(operation);
    uiState.detailSignature = signature;
  }
  if (reveal || operationPane?.available === false) operationPane?.reveal();
  synchronizeLinkedStage({ closeWithoutLink: reveal });
  if (reveal && window.matchMedia("(max-width: 1180px)").matches) {
    const inspector = byId("operation-inspector");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    inspector.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    byId("selected-operation-title")?.focus({ preventScroll: true });
  }
}

function renderOperationDetail(operation) {
  const empty = byId("operation-empty");
  const detail = byId("operation-detail");
  if (!operation) {
    empty.hidden = false;
    detail.hidden = true;
    byId("operation-pane-title").textContent = "Select an operation";
    operationPane?.setAvailable(false, { reset: true });
    uiState.detailSignature = null;
    replace(detail);
    return;
  }

  empty.hidden = true;
  detail.hidden = false;
  byId("operation-pane-title").textContent = operation.summary;
  const header = element("header", { className: "detail-head" });
  const identity = element("div");
  const methodPath = element("div", { className: "detail-operation" });
  methodPath.append(methodBadge(operation.method), element("code", { textContent: operation.path }));
  identity.append(
    element("span", { className: "eyebrow", textContent: operation.primaryTag }),
    element("h2", { textContent: operation.summary, attrs: { id: "selected-operation-title", tabindex: "-1" } }),
    methodPath,
  );
  if (operation.lifecycle === "planned") {
    identity.querySelector(".eyebrow").append(
      element("span", { className: "lifecycle-badge", textContent: "Planned" }),
    );
  }
  const returnButton = createIconButton({
    icon: "earlier",
    label: "Back to routes",
    placement: "left",
    className: "compact return-routes",
  });
  returnButton.addEventListener("click", () => {
    if (uiState.view === "canvas" && apiGraph?.focusOperation(operation.id)) {
      if (window.matchMedia("(max-width: 1180px)").matches) scrollCanvasIntoView();
      return;
    }
    const selected = document.querySelector(`.operation-node[data-operation-id="${CSS.escape(operation.id)}"]`);
    selected?.scrollIntoView({ block: "center" });
    selected?.focus({ preventScroll: true });
  });
  const swaggerLink = element("a", {
    className: "swagger-link ui-button compact",
    textContent: "Open Swagger",
    attrs: { href: "/docs" },
  });
  const actions = element("div", { className: "detail-actions ui-action-group" });
  actions.append(returnButton, swaggerLink);
  header.append(identity, actions);

  const body = element("div", { className: "detail-body" });
  if (operation.deprecated) body.append(element("p", { className: "deprecated-note", textContent: "This operation is marked as deprecated." }));
  body.append(renderRouteStory(operation));
  replace(detail, header, body);
}

function groupIdentifier(name, index) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "ungrouped";
  return `group-${index + 1}-${slug}`;
}

function setMapView(view) {
  if (view !== "list" && view !== "canvas") return;
  uiState.view = view;
  const canvasActive = view === "canvas";
  byId("list-view-button").setAttribute("aria-pressed", canvasActive ? "false" : "true");
  byId("canvas-view-button").setAttribute("aria-pressed", canvasActive ? "true" : "false");
  byId("api-canvas-tools").hidden = !canvasActive;
  byId("route-graph-title").textContent = canvasActive ? "Operations and schemas" : "Groups and operations";
  const hasVisibleOperations = visibleOperations().length > 0;
  byId("route-groups").hidden = canvasActive || !hasVisibleOperations;
  byId("api-canvas").hidden = !canvasActive || !hasVisibleOperations;
  apiGraph?.setActive(canvasActive && hasVisibleOperations);
  if (!canvasActive || !hasVisibleOperations) return;
  window.requestAnimationFrame(() => {
    apiGraph?.refreshGeometry();
    if (uiState.canvasFitted) return;
    uiState.canvasFitted = Boolean(apiGraph?.fitSelection() || apiGraph?.fit());
  });
}

function scrollCanvasIntoView() {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  byId("api-canvas").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
}

function chooseMapView(view) {
  setMapView(view);
  const url = new URL(window.location.href);
  if (view === "canvas") url.searchParams.set("view", "canvas");
  else url.searchParams.delete("view");
  window.history.replaceState(null, "", url);
}

function renderMap({
  restoreOperationFocus = null,
  restoreInspectorElement = null,
  restoreGroupHref = null,
  restoreCanvasKey = null,
} = {}) {
  const model = uiState.model;
  if (!model) return;
  const query = uiState.query;
  const shown = visibleOperations();
  const shownIds = new Set(shown.map(operation => operation.id));
  const groupLinks = byId("group-links");
  const groupMenu = byId("group-menu");
  const routeGroups = byId("route-groups");
  groupMenu.hidden = shown.length === 0;
  if (groupMenu.hidden) groupMenu.removeAttribute("open");
  routeGroups.hidden = false;
  replace(groupLinks);
  replace(routeGroups);

  for (const [groupIndex, group] of model.groups.entries()) {
    const operations = group.operations.filter(operation => shownIds.has(operation.id));
    if (!operations.length) continue;
    const identifier = groupIdentifier(group.name, groupIndex);
    const indexLink = element("a", { attrs: { href: `#${identifier}`, "data-group-name": group.name } });
    indexLink.append(
      element("span", { textContent: group.name }),
      element("small", { textContent: String(operations.length) }),
    );
    indexLink.addEventListener("click", event => {
      if (uiState.view !== "canvas") return;
      event.preventDefault();
      if (apiGraph?.focusGroup(group.name) && window.matchMedia("(max-width: 760px)").matches) {
        scrollCanvasIntoView();
      }
    });
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
      const titleRow = element("span", { className: "operation-title-row" });
      titleRow.append(element("strong", { textContent: operation.summary }));
      if (operation.lifecycle === "planned") {
        titleRow.append(element("span", { className: "lifecycle-badge", textContent: "Planned" }));
      }
      summary.append(
        titleRow,
        element("small", { textContent: operation.schemas.length ? operation.schemas.join(" · ") : "No referenced schemas" }),
      );
      button.dataset.uiTooltip = `Inspect ${operation.method.toUpperCase()} ${operation.path}`;
      const operationArrow = createIconElement("later");
      operationArrow.classList.add("operation-arrow");
      button.append(route, summary, operationArrow);
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
    renderStatePanel(state, {
      mark: "0",
      title: "No routes match this filter",
      message: "Search by HTTP method, path, group, operation, or referenced schema.",
    });
  }
  if (!shownIds.has(uiState.selectedId)) {
    uiState.selectedId = shown[0]?.id || null;
    if (window.location.hash.startsWith("#operation-stage-")) {
      const url = new URL(window.location.href);
      url.hash = "";
      window.history.replaceState(null, "", url);
    }
  }
  apiGraph?.setVisibleOperations(shownIds, { filtering: Boolean(query) });
  if (uiState.selectedId) setSelected(uiState.selectedId);
  else renderOperationDetail(null);
  setMapView(uiState.view);
  if (restoreGroupHref) {
    const link = [...byId("group-links").querySelectorAll("a")]
      .find(candidate => candidate.getAttribute("href") === restoreGroupHref);
    (link || byId("route-search")).focus({ preventScroll: true });
  } else if (restoreOperationFocus) {
    const selector = uiState.view === "canvas" ? ".api-operation-card" : ".operation-node";
    const operation = document.querySelector(`${selector}[data-operation-id="${CSS.escape(restoreOperationFocus)}"]`);
    (operation || byId("route-search")).focus({ preventScroll: true });
  } else if (restoreCanvasKey) {
    const node = document.querySelector(`.api-canvas-node[data-node-key="${CSS.escape(restoreCanvasKey)}"]`);
    (node || byId("route-search")).focus({ preventScroll: true });
  } else if (restoreInspectorElement && !restoreInspectorElement.isConnected) {
    byId("selected-operation-title")?.focus({ preventScroll: true });
  }
}

function showLoadError(error) {
  uiState.model = null;
  uiState.selectedId = null;
  uiState.canvasFitted = false;
  apiGraph?.clear();
  replace(byId("group-links"));
  replace(byId("route-groups"));
  byId("group-menu").hidden = true;
  byId("group-menu").removeAttribute("open");
  byId("route-groups").hidden = false;
  byId("api-canvas").hidden = true;
  renderOperationDetail(null);
  byId("map-description").textContent = "The active OpenAPI contract is currently unavailable.";
  byId("api-root-title").textContent = "Schemii API";
  byId("api-root-version").textContent = "Contract unavailable";
  for (const id of ["path-count", "operation-count", "group-count", "schema-count"]) byId(id).textContent = "—";
  const state = byId("map-state");
  state.hidden = false;
  const retry = createIconButton({
    icon: "refresh",
    label: "Retry contract load",
    placement: "bottom",
    className: "compact",
  });
  retry.addEventListener("click", loadContract);
  renderStatePanel(state, {
    mark: "!",
    title: "The active OpenAPI contract could not be loaded",
    message: error instanceof Error ? error.message : "The request failed.",
    variant: "error",
    action: retry,
  });
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
  byId("group-menu").hidden = visibleOperations().length === 0;
  setMapView(uiState.view);
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

async function loadDeveloperInspection() {
  const controller = new AbortController();
  const requestTimeout = window.setTimeout(() => controller.abort(), INSPECTION_REQUEST_TIMEOUT);
  try {
    const response = await fetch("/_developer/routes", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (response.status === 404) return null;
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  } finally {
    window.clearTimeout(requestTimeout);
  }
}

async function loadContract() {
  if (uiState.loading) return;
  uiState.loading = true;
  window.clearTimeout(uiState.refreshTimer);
  uiState.refreshTimer = null;
  const refresh = byId("refresh-map");
  setControlLoading(refresh, true, { loadingLabel: "Refreshing contract" });
  const initialLoad = !uiState.model;
  setContractStatus(initialLoad ? "Checking active contract" : "Checking for contract changes");
  const state = byId("map-state");
  if (initialLoad) {
    byId("filter-summary").textContent = "Loading routes";
    state.hidden = false;
    byId("group-menu").hidden = true;
    byId("route-groups").hidden = true;
    if (state.contains(document.activeElement)) refresh.focus({ preventScroll: true });
    renderStatePanel(state, {
      mark: "…",
      title: "Loading the active API contract",
      message: "Reading the contract and optional developer metadata from this Schemii process.",
      variant: "loading",
    });
  }
  try {
    const controller = new AbortController();
    const requestTimeout = window.setTimeout(() => controller.abort(), CONTRACT_REQUEST_TIMEOUT);
    let specification;
    let inspectionDocument;
    const inspectionRequest = loadDeveloperInspection();
    try {
      const response = await fetch("/openapi.json", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`OpenAPI request failed with HTTP ${response.status}`);
      specification = await response.json();
      inspectionDocument = await inspectionRequest;
    } finally {
      window.clearTimeout(requestTimeout);
    }
    const fingerprint = JSON.stringify([specification, inspectionDocument]);
    if (fingerprint !== uiState.contractFingerprint) {
      const activeElement = document.activeElement;
      const restoreOperationFocus = activeElement?.closest?.(".operation-node")?.dataset.operationId || null;
      const restoreGroupHref = activeElement?.closest?.("#group-links a")?.getAttribute("href") || null;
      const restoreCanvasKey = activeElement?.closest?.(".api-canvas-node")?.dataset.nodeKey || null;
      const restoreInspectorElement = activeElement && byId("operation-detail").contains(activeElement)
        ? activeElement
        : null;
      const model = buildApiMapModel(specification, inspectionDocument);
      if (!model.operations.length) throw new Error("The OpenAPI document does not contain any operations");
      apiGraph?.setModel(model);
      uiState.model = model;
      if (!model.operations.some(operation => operation.id === uiState.selectedId)) {
        const requestedOperation = new URLSearchParams(window.location.search).get("operation");
        uiState.selectedId = model.operations.some(operation => operation.id === requestedOperation)
          ? requestedOperation
          : model.operations[0].id;
      }
      applyContractMetadata(model);
      renderMap({ restoreOperationFocus, restoreInspectorElement, restoreGroupHref, restoreCanvasKey });
      uiState.contractFingerprint = fingerprint;
    }
    const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    byId("contract-status").removeAttribute("title");
    setContractStatus(`${uiState.model?.implementationInspectionAvailable ? "Live + code" : "Live"} · checked ${checkedAt}`);
  } catch (error) {
    const displayedError = error?.name === "AbortError"
      ? new Error("The OpenAPI request timed out after 10 seconds")
      : error;
    if (uiState.model) showRefreshError(displayedError);
    else showLoadError(displayedError);
  } finally {
    uiState.loading = false;
    setControlLoading(refresh, false);
    scheduleContractRefresh();
  }
}

function start() {
  initializeUi();
  operationPane = new DockPane({
    container: byId("api-map-workspace"),
    pane: byId("operation-inspector"),
    body: byId("operation-inspector-body"),
    toggle: byId("operation-inspector-toggle"),
    dismiss: byId("operation-inspector-close"),
    side: "right",
    expandedLabel: "Minimize operation inspector",
    minimizedLabel: "Expand operation inspector",
    getRestoreFocusTarget: () => {
      const selector = uiState.view === "canvas" ? ".api-operation-card" : ".operation-node";
      return document.querySelector(`${selector}[data-operation-id="${CSS.escape(uiState.selectedId || "")}"]`) || byId("route-search");
    },
    onStateChange: () => apiGraph?.refreshGeometry(),
  });
  operationPane.setAvailable(false);
  uiState.view = new URLSearchParams(window.location.search).get("view") === "canvas" ? "canvas" : "list";
  apiGraph = new ApiGraph({
    host: byId("api-canvas"),
    stage: byId("api-canvas-stage"),
    nodeLayer: byId("api-canvas-nodes"),
    lines: byId("api-canvas-lines"),
    zoomOutput: byId("api-canvas-zoom"),
    onSelectOperation: operationId => setSelected(operationId, { reveal: true }),
  });
  byId("route-search").addEventListener("input", event => {
    uiState.query = event.target.value.trim().toLowerCase();
    renderMap();
  });
  byId("refresh-map").addEventListener("click", loadContract);
  byId("list-view-button").addEventListener("click", () => chooseMapView("list"));
  byId("canvas-view-button").addEventListener("click", () => chooseMapView("canvas"));
  byId("api-canvas-fit").addEventListener("click", () => {
    if (apiGraph.fit()) uiState.canvasFitted = true;
  });
  byId("api-canvas-zoom-in").addEventListener("click", () => apiGraph.zoomBy(0.1));
  byId("api-canvas-zoom-out").addEventListener("click", () => apiGraph.zoomBy(-0.1));
  window.addEventListener("resize", () => {
    if (uiState.view === "canvas") apiGraph.refreshGeometry();
  });
  window.addEventListener("hashchange", () => synchronizeLinkedStage({ focus: true, closeWithoutLink: true }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadContract();
    else {
      window.clearTimeout(uiState.refreshTimer);
      uiState.refreshTimer = null;
    }
  });
  setMapView(uiState.view);
  loadContract();
}

if (typeof document !== "undefined" && document.getElementById("api-map-workspace")) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}
