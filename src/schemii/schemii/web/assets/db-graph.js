import { GraphViewport } from "./graph-viewport.js";

const MAX_COORDINATE = 1_000_000;
const DEFAULT_WIDTH = 230;
const DEFAULT_HEIGHT = 78;

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function clamp(value) {
  return Math.min(MAX_COORDINATE, Math.max(-MAX_COORDINATE, value));
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

function objectKey(objectId) {
  return `object:${objectId}`;
}

function queryKey(queryId) {
  return `query:${queryId}`;
}

function catalogKey(name) {
  return `catalog:${name}`;
}

export function buildDbGraphModel(model) {
  const nodes = [];
  const edges = [];
  const edgeKeys = new Set();
  const operationObjectIds = new Set(model.operations.map(operation => operation.implementation.id));
  const addEdge = (kind, source, target) => {
    if (!source || !target || source === target) return;
    const key = `${kind}\u0000${source}\u0000${target}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push({ key, kind, source, target });
  };

  for (const object of model.graphObjects) {
    const operation = model.operations.find(item => item.implementation.id === object.id);
    nodes.push({
      key: objectKey(object.id),
      kind: operation ? "operation" : object.kind === "outcome" ? "outcome" : object.kind === "model" ? "model" : "callable",
      object,
      operationId: operation?.id || null,
      title: object.qualname || object.name,
      meta: operation
        ? `${operation.parameters.length} inputs → ${operation.returnAnnotation}`
        : object.kind,
    });
  }
  for (const callable of model.callables.values()) {
    if (!model.graphObjectIds.has(callable.object.id)) continue;
    for (const call of callable.calls) {
      if (!model.graphObjectIds.has(call.object.id)) continue;
      addEdge("call", objectKey(callable.object.id), objectKey(call.object.id));
      for (const query of call.queries) addEdge("query", objectKey(callable.object.id), queryKey(query.id));
    }
  }
  for (const query of model.queries) {
    nodes.push({
      key: queryKey(query.id),
      kind: "query",
      query,
      title: query.name,
      meta: `${query.statement} · ${query.placeholderCount} parameter${query.placeholderCount === 1 ? "" : "s"}`,
    });
    for (const catalogObject of query.catalogObjects) {
      addEdge("catalog", queryKey(query.id), catalogKey(catalogObject));
    }
  }
  const catalogObjects = unique(model.queries.flatMap(query => query.catalogObjects));
  for (const name of catalogObjects) {
    nodes.push({
      key: catalogKey(name),
      kind: "catalog",
      title: name,
      meta: "PostgreSQL catalog source",
    });
  }

  const nodeKeys = new Set(nodes.map(node => node.key));
  const filteredEdges = edges.filter(edge => nodeKeys.has(edge.source) && nodeKeys.has(edge.target));
  const outgoing = new Map();
  for (const edge of filteredEdges) {
    const values = outgoing.get(edge.source) || [];
    values.push(edge.target);
    outgoing.set(edge.source, values);
  }
  const operationReachable = new Map();
  for (const operationId of operationObjectIds) {
    const root = objectKey(operationId);
    const reachable = new Set([root]);
    const queued = [root];
    while (queued.length) {
      const key = queued.shift();
      for (const target of outgoing.get(key) || []) {
        if (reachable.has(target)) continue;
        reachable.add(target);
        queued.push(target);
      }
    }
    operationReachable.set(
      model.operations.find(operation => operation.implementation.id === operationId)?.id,
      reachable,
    );
  }
  return { nodes, edges: filteredEdges, operationReachable };
}

function initialPositions(graph) {
  const incoming = new Map();
  const outgoing = new Map();
  for (const edge of graph.edges) {
    (incoming.get(edge.target) || incoming.set(edge.target, []).get(edge.target)).push(edge.source);
    (outgoing.get(edge.source) || outgoing.set(edge.source, []).get(edge.source)).push(edge.target);
  }
  const columns = new Map();
  const queue = [];
  for (const node of graph.nodes.filter(item => item.kind === "operation")) {
    columns.set(node.key, 0);
    queue.push(node.key);
  }
  while (queue.length) {
    const key = queue.shift();
    const nextColumn = (columns.get(key) || 0) + 1;
    for (const target of outgoing.get(key) || []) {
      if (columns.has(target) && columns.get(target) <= nextColumn) continue;
      columns.set(target, nextColumn);
      queue.push(target);
    }
  }
  const fallbackColumn = Math.max(0, ...columns.values()) + 1;
  const grouped = new Map();
  for (const node of graph.nodes) {
    const column = columns.get(node.key) ?? fallbackColumn;
    const values = grouped.get(column) || [];
    values.push(node);
    grouped.set(column, values);
  }
  const positions = new Map();
  for (const [column, values] of [...grouped].sort(([left], [right]) => left - right)) {
    values.sort((left, right) => left.kind.localeCompare(right.kind) || left.title.localeCompare(right.title));
    values.forEach((node, index) => positions.set(node.key, {
      x: 70 + column * 285,
      y: 70 + index * 104,
    }));
  }
  return positions;
}

export class DbGraph {
  constructor({ host, stage, nodeLayer, lines, zoomOutput, onSelectOperation }) {
    this.host = host;
    this.stage = stage;
    this.nodeLayer = nodeLayer;
    this.lines = lines;
    this.onSelectOperation = onSelectOperation;
    this.graph = { nodes: [], edges: [], operationReachable: new Map() };
    this.nodeByKey = new Map();
    this.positions = new Map();
    this.elements = new Map();
    this.geometry = new Map();
    this.edgeElements = new Map();
    this.edgesByNode = new Map();
    this.visibleNodeKeys = new Set();
    this.selectedKey = null;
    this.active = false;
    this.viewport = new GraphViewport({
      host,
      stage,
      zoomOutput,
      initialView: { x: 45, y: 45, zoom: 0.8 },
      minZoom: 0.18,
      maxZoom: 1.8,
      canStartPan: event => !event.target?.closest?.(".api-canvas-node, .api-canvas-tools"),
    });
  }

  setModel(model) {
    this.graph = buildDbGraphModel(model);
    this.nodeByKey = new Map(this.graph.nodes.map(node => [node.key, node]));
    const generated = initialPositions(this.graph);
    this.positions = new Map(this.graph.nodes.map(node => [node.key, this.positions.get(node.key) || generated.get(node.key)]));
    this.visibleNodeKeys = new Set(this.graph.nodes.map(node => node.key));
    this.edgesByNode = new Map();
    for (const edge of this.graph.edges) {
      for (const key of [edge.source, edge.target]) {
        const values = this.edgesByNode.get(key) || [];
        values.push(edge);
        this.edgesByNode.set(key, values);
      }
    }
    if (this.active) this.render();
  }

  setVisibleOperations(operationIds, { filtering = false } = {}) {
    if (!filtering) this.visibleNodeKeys = new Set(this.graph.nodes.map(node => node.key));
    else {
      this.visibleNodeKeys = new Set();
      for (const operationId of operationIds) {
        for (const key of this.graph.operationReachable.get(operationId) || []) this.visibleNodeKeys.add(key);
      }
    }
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
      const card = this.renderNode(node);
      const position = this.positions.get(node.key);
      card.style.left = `${position.x}px`;
      card.style.top = `${position.y}px`;
      this.elements.set(node.key, card);
      this.nodeLayer.append(card);
    }
    for (const [key, card] of this.elements) {
      this.geometry.set(key, {
        width: card.offsetWidth || DEFAULT_WIDTH,
        height: card.offsetHeight || DEFAULT_HEIGHT,
      });
    }
    this.drawEdges();
  }

  renderNode(node) {
    const selected = node.key === this.selectedKey;
    const card = nodeElement("article", {
      className: `api-canvas-node db-canvas-node db-node-${node.kind}${selected ? " selected" : ""}`,
      attrs: {
        tabindex: "0",
        role: "button",
        "aria-pressed": selected ? "true" : "false",
        "aria-label": `${node.title}. ${node.meta}. ${this.connectionDescription(node.key)} Use arrow keys to move this node.`,
      },
      dataset: { nodeKey: node.key, operationId: node.operationId || "" },
    });
    const head = nodeElement("header", { className: "api-node-head" });
    head.append(
      nodeElement("span", { className: `db-node-glyph glyph-${node.kind}`, text: this.glyph(node.kind), attrs: { "aria-hidden": "true" } }),
      nodeElement("span", { className: "api-node-kind", text: node.kind }),
    );
    head.addEventListener("pointerdown", event => this.startNodeDrag(event, node.key, card));
    card.addEventListener("click", () => {
      this.selectKey(node.key);
      if (node.operationId) this.onSelectOperation(node.operationId);
    });
    card.addEventListener("keydown", event => this.handleNodeKeydown(event, node.key));
    card.append(
      head,
      nodeElement("strong", {
        className: "api-node-title db-node-title",
        text: node.title,
        attrs: { "data-ui-tooltip-overflow": node.title, "data-ui-tooltip-touch": "true" },
      }),
      nodeElement("small", {
        className: "api-node-meta",
        text: node.meta,
        attrs: { "data-ui-tooltip-overflow": node.meta, "data-ui-tooltip-touch": "true" },
      }),
    );
    return card;
  }

  glyph(kind) {
    return { operation: "GW", callable: "ƒ", model: "{}", outcome: "!", query: "SQL", catalog: "PG" }[kind] || "·";
  }

  connectionDescription(key) {
    const count = (this.edgesByNode.get(key) || []).length;
    return `${count} graph connection${count === 1 ? "" : "s"}.`;
  }

  startNodeDrag(event, key, card) {
    this.viewport.beginNodeDrag(event, {
      key,
      element: card,
      position: this.positions.get(key),
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
      if (node?.operationId) this.onSelectOperation(node.operationId);
      return;
    }
    const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!delta) return;
    event.preventDefault();
    const step = event.shiftKey ? 48 : 16;
    const position = this.positions.get(key);
    const next = { x: clamp(position.x + delta[0] * step), y: clamp(position.y + delta[1] * step) };
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
    const operation = this.graph.nodes.find(node => node.operationId === operationId);
    if (operation) this.selectKey(operation.key);
  }

  updateHighlight() {
    const connected = new Set();
    const activeEdges = new Set();
    if (this.selectedKey && this.visibleNodeKeys.has(this.selectedKey)) {
      const selectedNode = this.nodeByKey.get(this.selectedKey);
      const reachable = selectedNode?.operationId
        ? this.graph.operationReachable.get(selectedNode.operationId)
        : null;
      if (reachable) {
        for (const key of reachable) connected.add(key);
        for (const edge of this.graph.edges) {
          if (!this.visibleNodeKeys.has(edge.source) || !this.visibleNodeKeys.has(edge.target)) continue;
          if (!connected.has(edge.source) || !connected.has(edge.target)) continue;
          activeEdges.add(edge.key);
        }
      } else {
        connected.add(this.selectedKey);
        for (const edge of this.edgesByNode.get(this.selectedKey) || []) {
          if (!this.visibleNodeKeys.has(edge.source) || !this.visibleNodeKeys.has(edge.target)) continue;
          activeEdges.add(edge.key);
          connected.add(edge.source);
          connected.add(edge.target);
        }
      }
    }
    for (const [key, card] of this.elements) card.classList.toggle("dimmed", connected.size > 0 && !connected.has(key));
    for (const [key, entry] of this.edgeElements) {
      entry.group.classList.toggle("active", activeEdges.has(key));
      entry.group.classList.toggle("dimmed", activeEdges.size > 0 && !activeEdges.has(key));
    }
  }

  prepareMarkers() {
    const namespace = "http://www.w3.org/2000/svg";
    const definitions = document.createElementNS(namespace, "defs");
    for (const kind of ["call", "query", "catalog"]) {
      const marker = document.createElementNS(namespace, "marker");
      marker.setAttribute("id", `db-arrow-${kind}`);
      marker.setAttribute("viewBox", "0 0 10 10");
      marker.setAttribute("refX", "9");
      marker.setAttribute("refY", "5");
      marker.setAttribute("markerWidth", "6");
      marker.setAttribute("markerHeight", "6");
      marker.setAttribute("orient", "auto-start-reverse");
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
      path.setAttribute("class", `api-arrow db-arrow-${kind}`);
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
      const bend = Math.max(70, Math.abs(x2 - x1) * 0.42);
      const direction = sourceIsLeft ? 1 : -1;
      const pathData = `M ${x1} ${y1} C ${x1 + bend * direction} ${y1}, ${x2 - bend * direction} ${y2}, ${x2} ${y2}`;
      let entry = this.edgeElements.get(edge.key);
      if (!entry) {
        const namespace = "http://www.w3.org/2000/svg";
        const group = document.createElementNS(namespace, "g");
        group.setAttribute("class", `api-edge db-edge db-edge-${edge.kind}`);
        const shadow = document.createElementNS(namespace, "path");
        shadow.setAttribute("class", "api-edge-shadow");
        const line = document.createElementNS(namespace, "path");
        line.setAttribute("class", "api-edge-line");
        line.setAttribute("marker-end", `url(#db-arrow-${edge.kind})`);
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
      this.geometry.set(key, { width: card.offsetWidth || DEFAULT_WIDTH, height: card.offsetHeight || DEFAULT_HEIGHT });
    }
    this.drawEdges();
  }

  fit() {
    return this.fitKeys(this.visibleNodeKeys, 1.05);
  }

  fitSelection() {
    const selected = this.nodeByKey.get(this.selectedKey);
    const keys = selected?.operationId
      ? this.graph.operationReachable.get(selected.operationId)
      : null;
    return keys ? this.fitKeys(keys, 1) : false;
  }

  fitKeys(keys, maxZoom) {
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

  focusOperation(operationId) {
    const node = this.graph.nodes.find(candidate => candidate.operationId === operationId);
    if (!node) return false;
    const position = this.positions.get(node.key);
    const geometry = this.geometry.get(node.key);
    if (!position || !geometry || !this.elements.has(node.key)) return false;
    const view = this.viewport.getView();
    this.viewport.setView({
      x: this.host.clientWidth / 2 - (position.x + geometry.width / 2) * view.zoom,
      y: this.host.clientHeight / 2 - (position.y + geometry.height / 2) * view.zoom,
      zoom: view.zoom,
    });
    this.selectKey(node.key, { focus: true });
    return true;
  }

  clear() {
    this.viewport.cancelNodeDrag();
    this.graph = { nodes: [], edges: [], operationReachable: new Map() };
    this.nodeByKey.clear();
    this.positions.clear();
    this.elements.clear();
    this.geometry.clear();
    this.edgeElements.clear();
    this.edgesByNode.clear();
    this.visibleNodeKeys.clear();
    this.selectedKey = null;
    replace(this.nodeLayer);
    replace(this.lines);
  }
}
