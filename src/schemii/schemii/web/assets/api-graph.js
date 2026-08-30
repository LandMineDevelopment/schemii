import { GraphViewport } from "./graph-viewport.js";

const OPERATION_WIDTH = 280;
const OPERATION_HEIGHT = 104;
const SCHEMA_WIDTH = 240;
const SCHEMA_HEIGHT = 88;
const MAX_COORDINATE = 1_000_000;

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function clamp(value) {
  return Math.min(MAX_COORDINATE, Math.max(-MAX_COORDINATE, value));
}

function dimensionsFor(node) {
  return node.kind === "operation"
    ? { width: OPERATION_WIDTH, height: OPERATION_HEIGHT }
    : { width: SCHEMA_WIDTH, height: SCHEMA_HEIGHT };
}

function nodeElement(tag, { className = "", text = "", attrs = {}, dataset = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  for (const [name, value] of Object.entries(dataset)) node.dataset[name] = String(value);
  return node;
}

function replace(target, ...children) {
  target.replaceChildren(...children.filter(Boolean));
}

export function buildApiGraphModel(apiModel) {
  const schemas = Array.isArray(apiModel?.schemas) ? apiModel.schemas : [];
  const operations = Array.isArray(apiModel?.operations) ? apiModel.operations : [];
  const schemaNames = new Set(schemas.map(schema => schema.name));
  const nodes = [
    ...operations.map(operation => ({
      key: `operation:${operation.id}`,
      kind: "operation",
      operationId: operation.id,
      method: operation.method,
      path: operation.path,
      title: operation.summary,
      group: operation.primaryTag,
      schemas: operation.schemas,
    })),
    ...schemas.map(schema => ({
      key: `schema:${schema.name}`,
      kind: "schema",
      name: schema.name,
      schemaKind: schema.kind,
      description: schema.description,
      references: schema.references,
    })),
  ];
  const edges = [];
  const edgeKeys = new Set();
  const addEdge = (kind, source, target) => {
    if (source === target) return;
    const key = `${kind}\u0000${source}\u0000${target}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push({ key, kind, source, target });
  };

  for (const operation of operations) {
    const operationKey = `operation:${operation.id}`;
    const graph = operation.graph || {};
    for (const name of unique(graph.parameterSchemas || []).filter(name => schemaNames.has(name))) {
      addEdge("parameter", `schema:${name}`, operationKey);
    }
    for (const name of unique(graph.requestSchemas || []).filter(name => schemaNames.has(name))) {
      addEdge("request", `schema:${name}`, operationKey);
    }
    for (const name of unique(graph.responseSchemas || []).filter(name => schemaNames.has(name))) {
      addEdge("response", operationKey, `schema:${name}`);
    }
  }
  for (const schema of schemas) {
    for (const reference of unique(schema.references || []).filter(name => schemaNames.has(name))) {
      addEdge("schema", `schema:${schema.name}`, `schema:${reference}`);
    }
  }
  const parallelEdges = new Map();
  for (const edge of edges) {
    const pair = [edge.source, edge.target].sort().join("\u0000");
    const siblings = parallelEdges.get(pair) || [];
    siblings.push(edge);
    parallelEdges.set(pair, siblings);
  }
  for (const siblings of parallelEdges.values()) {
    siblings.forEach((edge, index) => {
      edge.lane = index - (siblings.length - 1) / 2;
    });
  }
  return { nodes, edges };
}

function initialPositions(graph, { compact = false } = {}) {
  const positions = new Map();
  const operations = graph.nodes.filter(node => node.kind === "operation");
  const schemas = graph.nodes.filter(node => node.kind === "schema");
  const groupNames = unique(operations.map(node => node.group));
  const groups = new Map(groupNames.map(name => [name, []]));
  for (const operation of operations) groups.get(operation.group).push(operation);
  const operationColumnCount = compact ? 1 : Math.min(2, Math.max(1, Math.ceil(operations.length / 8)));
  const operationColumnHeights = Array.from({ length: operationColumnCount }, () => 90);
  groupNames.forEach(name => {
    const column = operationColumnHeights.indexOf(Math.min(...operationColumnHeights));
    groups.get(name).forEach(operation => {
      positions.set(operation.key, {
        x: 80 + column * 330,
        y: operationColumnHeights[column],
      });
      operationColumnHeights[column] += 132;
    });
    operationColumnHeights[column] += 36;
  });

  const schemaColumnCount = compact
    ? Math.min(2, Math.max(1, Math.ceil(schemas.length / 15)))
    : Math.min(3, Math.max(1, Math.ceil(schemas.length / 10)));
  const schemasPerColumn = Math.max(1, Math.ceil(schemas.length / schemaColumnCount));
  const schemaStartX = 80 + operationColumnCount * 330 + (compact ? 100 : 180);
  schemas.forEach((schema, index) => {
    positions.set(schema.key, {
      x: schemaStartX + Math.floor(index / schemasPerColumn) * 290,
      y: 90 + (index % schemasPerColumn) * 112,
    });
  });
  return positions;
}

export function reconcileApiGraphPositions(graph, previousPositions = new Map(), options = {}) {
  const generated = initialPositions(graph, options);
  const positions = new Map();
  const nodeByKey = new Map(graph.nodes.map(node => [node.key, node]));
  for (const node of graph.nodes) {
    if (previousPositions.has(node.key)) positions.set(node.key, previousPositions.get(node.key));
  }
  const overlaps = (node, candidate) => {
    const size = dimensionsFor(node);
    for (const [key, position] of positions) {
      const other = dimensionsFor(nodeByKey.get(key));
      if (
        candidate.x < position.x + other.width + 24
        && candidate.x + size.width + 24 > position.x
        && candidate.y < position.y + other.height + 24
        && candidate.y + size.height + 24 > position.y
      ) return true;
    }
    return false;
  };
  for (const node of graph.nodes) {
    if (positions.has(node.key)) continue;
    const origin = generated.get(node.key);
    const candidate = { ...origin };
    const searchLimit = positions.size + 1;
    const yDirection = origin.y + searchLimit * 280 <= MAX_COORDINATE ? 1 : -1;
    for (let offset = 1; overlaps(node, candidate) && offset <= searchLimit; offset += 1) {
      candidate.y = clamp(origin.y + yDirection * offset * 280);
    }
    const xDirection = origin.x + searchLimit * 640 <= MAX_COORDINATE ? 1 : -1;
    for (let offset = 1; overlaps(node, candidate) && offset <= searchLimit; offset += 1) {
      candidate.x = clamp(origin.x + xDirection * offset * 640);
      candidate.y = origin.y;
    }
    if (overlaps(node, candidate)) throw new Error(`Unable to place API graph node ${node.key}`);
    positions.set(node.key, candidate);
  }
  return positions;
}

export class ApiGraph {
  constructor({ host, stage, nodeLayer, lines, zoomOutput, onSelectOperation }) {
    this.host = host;
    this.stage = stage;
    this.nodeLayer = nodeLayer;
    this.lines = lines;
    this.onSelectOperation = onSelectOperation;
    this.graph = { nodes: [], edges: [] };
    this.nodeByKey = new Map();
    this.positions = new Map();
    this.elements = new Map();
    this.geometry = new Map();
    this.edgeElements = new Map();
    this.edgesByNode = new Map();
    this.visibleOperationKeys = new Set();
    this.visibleNodeKeys = new Set();
    this.filtering = false;
    this.selectedKey = null;
    this.active = false;
    this.viewport = new GraphViewport({
      host,
      stage,
      zoomOutput,
      initialView: { x: 50, y: 50, zoom: 0.8 },
      minZoom: 0.2,
      maxZoom: 1.8,
      canStartPan: event => !event.target?.closest?.(".api-canvas-node, .api-canvas-state, .api-canvas-tools"),
    });
  }

  setModel(apiModel) {
    this.viewport.cancelNodeDrag();
    const graph = buildApiGraphModel(apiModel);
    const nodeByKey = new Map(graph.nodes.map(node => [node.key, node]));
    const positions = reconcileApiGraphPositions(graph, this.positions, {
      compact: window.matchMedia("(max-width: 760px)").matches,
    });
    const visibleOperationKeys = new Set(
      graph.nodes.filter(node => node.kind === "operation").map(node => node.key),
    );
    this.graph = graph;
    this.nodeByKey = nodeByKey;
    this.positions = positions;
    this.visibleOperationKeys = visibleOperationKeys;
    if (!nodeByKey.has(this.selectedKey)) this.selectedKey = null;
    this.indexEdges();
    this.updateVisibleNodes();
  }

  indexEdges() {
    this.edgesByNode = new Map();
    for (const edge of this.graph.edges) {
      for (const key of unique([edge.source, edge.target])) {
        const edges = this.edgesByNode.get(key) || [];
        edges.push(edge);
        this.edgesByNode.set(key, edges);
      }
    }
  }

  setVisibleOperations(operationIds, { filtering = false } = {}) {
    this.visibleOperationKeys = new Set([...operationIds].map(id => `operation:${id}`));
    this.filtering = filtering;
    this.updateVisibleNodes();
    if (this.active) this.render();
  }

  setActive(active) {
    const changed = this.active !== active;
    this.active = active;
    if (!active) {
      this.viewport.cancelNodeDrag();
      return;
    }
    if (changed || !this.elements.size) this.render();
  }

  updateVisibleNodes() {
    this.visibleNodeKeys = new Set(this.visibleOperationKeys);
    if (!this.filtering) {
      for (const node of this.graph.nodes) this.visibleNodeKeys.add(node.key);
      return;
    }
    const schemaQueue = [];
    for (const edge of this.graph.edges) {
      const operationKey = edge.source.startsWith("operation:") ? edge.source
        : edge.target.startsWith("operation:") ? edge.target
          : null;
      if (!operationKey || !this.visibleOperationKeys.has(operationKey)) continue;
      const schemaKey = edge.source === operationKey ? edge.target : edge.source;
      if (!schemaKey.startsWith("schema:") || this.visibleNodeKeys.has(schemaKey)) continue;
      this.visibleNodeKeys.add(schemaKey);
      schemaQueue.push(schemaKey);
    }
    while (schemaQueue.length) {
      const key = schemaQueue.shift();
      for (const edge of this.edgesByNode.get(key) || []) {
        if (edge.kind !== "schema") continue;
        const adjacent = edge.source === key ? edge.target : edge.source;
        if (this.visibleNodeKeys.has(adjacent)) continue;
        this.visibleNodeKeys.add(adjacent);
        schemaQueue.push(adjacent);
      }
    }
  }

  render() {
    this.viewport.cancelNodeDrag();
    replace(this.nodeLayer);
    replace(this.lines);
    this.elements.clear();
    this.geometry.clear();
    this.edgeElements.clear();
    this.prepareMarkers();
    for (const node of this.graph.nodes) {
      if (!this.visibleNodeKeys.has(node.key)) continue;
      const card = node.kind === "operation"
        ? this.renderOperation(node)
        : this.renderSchema(node);
      const position = this.positions.get(node.key);
      card.style.left = `${position.x}px`;
      card.style.top = `${position.y}px`;
      this.elements.set(node.key, card);
      this.nodeLayer.append(card);
    }
    for (const [key, card] of this.elements) {
      const node = this.nodeByKey.get(key);
      this.geometry.set(key, {
        width: card.offsetWidth || (node.kind === "operation" ? OPERATION_WIDTH : SCHEMA_WIDTH),
        height: card.offsetHeight || (node.kind === "operation" ? OPERATION_HEIGHT : SCHEMA_HEIGHT),
      });
    }
    this.drawEdges();
    this.updateHighlight();
  }

  renderOperation(operation) {
    const selected = operation.key === this.selectedKey;
    const card = nodeElement("article", {
      className: `api-canvas-node api-operation-card${selected ? " selected" : ""}`,
      attrs: {
        tabindex: "0",
        role: "button",
        "aria-pressed": selected ? "true" : "false",
        "aria-label": `${operation.method.toUpperCase()} ${operation.path}. ${operation.title}. ${this.connectionDescription(operation.key)} Use arrow keys to move this operation.`,
      },
      dataset: { nodeKey: operation.key, operationId: operation.operationId },
    });
    const head = nodeElement("header", { className: "api-node-head" });
    head.append(
      nodeElement("span", { className: `method-badge method-${operation.method}`, text: operation.method.toUpperCase() }),
      nodeElement("span", { className: "api-node-kind", text: operation.group }),
    );
    const path = nodeElement("code", { className: "api-node-path", text: operation.path });
    const title = nodeElement("strong", { className: "api-node-title", text: operation.title });
    const schemaCount = operation.schemas?.length || 0;
    const footer = nodeElement("small", {
      className: "api-node-meta",
      text: `${schemaCount} referenced schema${schemaCount === 1 ? "" : "s"}`,
    });
    head.addEventListener("pointerdown", event => this.startNodeDrag(event, operation.key, card));
    card.addEventListener("click", () => {
      this.selectKey(operation.key);
      this.onSelectOperation(operation.operationId);
    });
    card.addEventListener("keydown", event => this.handleNodeKeydown(event, operation.key));
    card.append(head, path, title, footer);
    return card;
  }

  renderSchema(schema) {
    const selected = schema.key === this.selectedKey;
    const connectionCount = (this.edgesByNode.get(schema.key) || []).length;
    const card = nodeElement("article", {
      className: `api-canvas-node api-schema-card${selected ? " selected" : ""}`,
      attrs: {
        tabindex: "0",
        role: "button",
        "aria-pressed": selected ? "true" : "false",
        "aria-label": `${schema.name} schema. ${this.connectionDescription(schema.key)} Use arrow keys to move this schema.`,
      },
      dataset: { nodeKey: schema.key },
    });
    const head = nodeElement("header", { className: "api-node-head" });
    head.append(
      nodeElement("span", { className: "schema-node-glyph", text: "{}", attrs: { "aria-hidden": "true" } }),
      nodeElement("span", { className: "api-node-kind", text: schema.schemaKind }),
    );
    head.addEventListener("pointerdown", event => this.startNodeDrag(event, schema.key, card));
    card.addEventListener("click", () => this.selectKey(schema.key));
    card.addEventListener("keydown", event => this.handleNodeKeydown(event, schema.key));
    card.append(
      head,
      nodeElement("strong", { className: "api-node-title schema-title", text: schema.name }),
      nodeElement("small", {
        className: "api-node-meta",
        text: `${connectionCount} connection${connectionCount === 1 ? "" : "s"}`,
      }),
    );
    return card;
  }

  connectionDescription(key) {
    const descriptions = [];
    for (const edge of this.edgesByNode.get(key) || []) {
      const otherKey = edge.source === key ? edge.target : edge.source;
      const other = this.nodeByKey.get(otherKey);
      if (!other) continue;
      const otherName = other.kind === "operation"
        ? `${other.method.toUpperCase()} ${other.path}`
        : other.name;
      if (this.nodeByKey.get(key)?.kind === "operation") {
        descriptions.push(`${edge.kind} schema ${otherName}`);
      } else if (other.kind === "operation") {
        const relation = edge.kind === "response" ? "response from" : `${edge.kind} for`;
        descriptions.push(`${relation} ${otherName}`);
      } else {
        descriptions.push(edge.source === key ? `references ${otherName}` : `referenced by ${otherName}`);
      }
    }
    if (!descriptions.length) return "No graph connections.";
    return `${descriptions.length} graph connection${descriptions.length === 1 ? "" : "s"}: ${descriptions.join("; ")}.`;
  }

  startNodeDrag(event, key, card) {
    const position = this.positions.get(key);
    this.viewport.beginNodeDrag(event, {
      key,
      element: card,
      position,
      constrain: candidate => ({ x: clamp(candidate.x), y: clamp(candidate.y) }),
      onStart: () => this.selectKey(key),
      onFrame: () => this.drawEdges(key),
      onCommit: next => this.positions.set(key, next),
    });
  }

  handleNodeKeydown(event, key) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const node = this.nodeByKey.get(key);
      this.selectKey(key);
      if (node?.kind === "operation") this.onSelectOperation(node.operationId);
      return;
    }
    const delta = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    }[event.key];
    if (!delta) return;
    event.preventDefault();
    const step = event.shiftKey ? 48 : 16;
    const position = this.positions.get(key);
    const next = {
      x: clamp(position.x + delta[0] * step),
      y: clamp(position.y + delta[1] * step),
    };
    this.positions.set(key, next);
    const card = this.elements.get(key);
    card.style.left = `${next.x}px`;
    card.style.top = `${next.y}px`;
    this.selectKey(key);
    this.drawEdges(key);
  }

  selectKey(key, { focus = false } = {}) {
    if (!this.nodeByKey.has(key)) return;
    const previous = this.selectedKey;
    this.selectedKey = key;
    for (const candidate of unique([previous, key])) {
      const card = this.elements.get(candidate);
      if (!card) continue;
      const selected = candidate === key;
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-pressed", selected ? "true" : "false");
    }
    if (focus) this.elements.get(key)?.focus({ preventScroll: true });
    this.updateHighlight();
  }

  setSelectedOperation(operationId) {
    const key = operationId ? `operation:${operationId}` : null;
    if (key && this.nodeByKey.has(key)) this.selectKey(key);
  }

  updateHighlight() {
    const connected = new Set();
    const activeEdges = new Set();
    if (this.selectedKey && this.visibleNodeKeys.has(this.selectedKey)) {
      connected.add(this.selectedKey);
      for (const edge of this.edgesByNode.get(this.selectedKey) || []) {
        if (!this.visibleNodeKeys.has(edge.source) || !this.visibleNodeKeys.has(edge.target)) continue;
        activeEdges.add(edge.key);
        connected.add(edge.source);
        connected.add(edge.target);
      }
    }
    for (const [key, card] of this.elements) {
      card.classList.toggle("dimmed", connected.size > 0 && !connected.has(key));
    }
    for (const [key, entry] of this.edgeElements) {
      entry.group.classList.toggle("active", activeEdges.has(key));
      entry.group.classList.toggle("dimmed", activeEdges.size > 0 && !activeEdges.has(key));
    }
  }

  prepareMarkers() {
    const namespace = "http://www.w3.org/2000/svg";
    const definitions = document.createElementNS(namespace, "defs");
    for (const kind of ["parameter", "request", "response", "schema"]) {
      const marker = document.createElementNS(namespace, "marker");
      marker.setAttribute("id", `api-arrow-${kind}`);
      marker.setAttribute("viewBox", "0 0 10 10");
      marker.setAttribute("refX", "9");
      marker.setAttribute("refY", "5");
      marker.setAttribute("markerWidth", "6");
      marker.setAttribute("markerHeight", "6");
      marker.setAttribute("orient", "auto-start-reverse");
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
      path.setAttribute("class", `api-arrow api-arrow-${kind}`);
      marker.append(path);
      definitions.append(marker);
    }
    this.lines.append(definitions);
  }

  positionFor(key) {
    return this.viewport.dragPosition(key) || this.positions.get(key);
  }

  drawEdges(changedKey = null) {
    const edges = changedKey ? (this.edgesByNode.get(changedKey) || []) : this.graph.edges;
    const activeKeys = changedKey ? null : new Set();
    for (const edge of edges) {
      if (!this.visibleNodeKeys.has(edge.source) || !this.visibleNodeKeys.has(edge.target)) {
        this.edgeElements.get(edge.key)?.group.remove();
        this.edgeElements.delete(edge.key);
        continue;
      }
      activeKeys?.add(edge.key);
      const source = this.positionFor(edge.source);
      const target = this.positionFor(edge.target);
      const sourceGeometry = this.geometry.get(edge.source);
      const targetGeometry = this.geometry.get(edge.target);
      if (!source || !target || !sourceGeometry || !targetGeometry) continue;
      const sourceIsLeft = source.x + sourceGeometry.width / 2 <= target.x + targetGeometry.width / 2;
      const x1 = source.x + (sourceIsLeft ? sourceGeometry.width : 0);
      const x2 = target.x + (sourceIsLeft ? 0 : targetGeometry.width);
      const y1 = source.y + sourceGeometry.height / 2;
      const y2 = target.y + targetGeometry.height / 2;
      const laneOffset = (edge.lane || 0) * 12;
      const bend = Math.max(70, Math.abs(x2 - x1) * 0.42);
      const direction = sourceIsLeft ? 1 : -1;
      const pathData = `M ${x1} ${y1 + laneOffset} C ${x1 + bend * direction} ${y1 + laneOffset}, ${x2 - bend * direction} ${y2 + laneOffset}, ${x2} ${y2 + laneOffset}`;
      let entry = this.edgeElements.get(edge.key);
      if (!entry) {
        const namespace = "http://www.w3.org/2000/svg";
        const group = document.createElementNS(namespace, "g");
        group.setAttribute("class", `api-edge api-edge-${edge.kind}`);
        const shadow = document.createElementNS(namespace, "path");
        shadow.setAttribute("class", "api-edge-shadow");
        const line = document.createElementNS(namespace, "path");
        line.setAttribute("class", "api-edge-line");
        line.setAttribute("marker-end", `url(#api-arrow-${edge.kind})`);
        group.append(shadow, line);
        this.lines.append(group);
        entry = { group, shadow, line };
        this.edgeElements.set(edge.key, entry);
      }
      entry.shadow.setAttribute("d", pathData);
      entry.line.setAttribute("d", pathData);
    }
    if (!changedKey) {
      for (const [key, entry] of this.edgeElements) {
        if (activeKeys.has(key)) continue;
        entry.group.remove();
        this.edgeElements.delete(key);
      }
    }
    this.updateHighlight();
  }

  refreshGeometry() {
    for (const [key, card] of this.elements) {
      const node = this.nodeByKey.get(key);
      this.geometry.set(key, {
        width: card.offsetWidth || (node.kind === "operation" ? OPERATION_WIDTH : SCHEMA_WIDTH),
        height: card.offsetHeight || (node.kind === "operation" ? OPERATION_HEIGHT : SCHEMA_HEIGHT),
      });
    }
    this.drawEdges();
  }

  fit() {
    return this.fitKeys(this.visibleNodeKeys, { maxZoom: 1.15 });
  }

  fitSelection() {
    if (!this.selectedKey || !this.visibleNodeKeys.has(this.selectedKey)) return false;
    const keys = new Set([this.selectedKey]);
    for (const edge of this.edgesByNode.get(this.selectedKey) || []) {
      if (this.visibleNodeKeys.has(edge.source) && this.visibleNodeKeys.has(edge.target)) {
        keys.add(edge.source);
        keys.add(edge.target);
      }
    }
    return this.fitKeys(keys, { maxZoom: 0.95 });
  }

  fitKeys(keys, { maxZoom }) {
    const entries = [...keys].map(key => {
      const position = this.positions.get(key);
      const geometry = this.geometry.get(key);
      return position && geometry ? { ...position, ...geometry } : null;
    }).filter(Boolean);
    if (!entries.length) return false;
    return this.viewport.fitBounds({
      minX: Math.min(...entries.map(entry => entry.x)),
      minY: Math.min(...entries.map(entry => entry.y)),
      maxX: Math.max(...entries.map(entry => entry.x + entry.width)),
      maxY: Math.max(...entries.map(entry => entry.y + entry.height)),
    }, { left: 36, top: 36, right: 36, bottom: 58, maxZoom });
  }

  zoomBy(amount) {
    this.viewport.zoomBy(amount);
  }

  revealKey(key, { focus = true } = {}) {
    const position = this.positions.get(key);
    const geometry = this.geometry.get(key);
    if (!position || !geometry || !this.elements.has(key)) return false;
    const view = this.viewport.getView();
    this.viewport.setView({
      x: this.host.clientWidth / 2 - (position.x + geometry.width / 2) * view.zoom,
      y: this.host.clientHeight / 2 - (position.y + geometry.height / 2) * view.zoom,
      zoom: view.zoom,
    });
    this.selectKey(key, { focus });
    return true;
  }

  focusOperation(operationId) {
    return this.revealKey(`operation:${operationId}`);
  }

  focusGroup(groupName) {
    const operation = this.graph.nodes.find(node =>
      node.kind === "operation"
      && node.group === groupName
      && this.visibleNodeKeys.has(node.key));
    return operation ? this.revealKey(operation.key) : false;
  }

  clear() {
    this.viewport.cancelNodeDrag();
    this.graph = { nodes: [], edges: [] };
    this.nodeByKey.clear();
    this.positions.clear();
    this.elements.clear();
    this.geometry.clear();
    this.edgeElements.clear();
    this.edgesByNode.clear();
    this.visibleOperationKeys.clear();
    this.visibleNodeKeys.clear();
    this.selectedKey = null;
    replace(this.nodeLayer);
    replace(this.lines);
  }

  destroy() {
    this.viewport.destroy();
  }
}
