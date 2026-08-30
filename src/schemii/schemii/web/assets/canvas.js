import { element, replace } from "./dom.js";

const CARD_WIDTH = 270;
const HEADER_HEIGHT = 48;
const COLUMN_HEIGHT = 35;
const MAX_CARD_COLUMNS = 40;
const MAX_DIAGRAM_RELATIONSHIPS = 1000;
const MAX_COORDINATE = 1_000_000;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 1.7;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function columnBadges(table, foreignColumns, columnName) {
  const badges = element("span", { className: "column-badges", attrs: { "aria-hidden": "true" } });
  if (table.primaryKey?.columns?.includes(columnName)) badges.append(element("span", { className: "pk", text: "PK" }));
  if (foreignColumns.has(`${table.name}\u0000${columnName}`)) badges.append(element("span", { className: "fk", text: "FK" }));
  if (!badges.childElementCount) badges.append(element("span", { text: "·" }));
  return badges;
}

function initialPositions(tables) {
  const positions = new Map();
  const columnCount = Math.min(10, Math.max(3, Math.ceil(Math.sqrt(tables.length))));
  const columnHeights = Array.from({ length: columnCount }, () => 90);
  for (const table of tables) {
    const column = columnHeights.indexOf(Math.min(...columnHeights));
    const y = clamp(columnHeights[column], -MAX_COORDINATE, MAX_COORDINATE);
    positions.set(table.name, { x: clamp(90 + column * 350, -MAX_COORDINATE, MAX_COORDINATE), y });
    const visibleRows = Math.min(table.columns.length, MAX_CARD_COLUMNS)
      + (table.columns.length > MAX_CARD_COLUMNS ? 1 : 0);
    columnHeights[column] += HEADER_HEIGHT + visibleRows * COLUMN_HEIGHT + 80;
  }
  return positions;
}

function cardHeight(table) {
  const visibleRows = Math.min(table.columns.length, MAX_CARD_COLUMNS)
    + (table.columns.length > MAX_CARD_COLUMNS ? 1 : 0);
  return HEADER_HEIGHT + visibleRows * COLUMN_HEIGHT + 2;
}

export class CatalogCanvas {
  constructor({
    canvas,
    stage,
    layer,
    lines,
    zoomOutput,
    onSelect,
    onPositionsChanged,
    onRelationshipVisibilityChanged,
    scheduleFrame = callback => window.requestAnimationFrame(callback),
    cancelFrame = frame => window.cancelAnimationFrame(frame),
  }) {
    this.canvas = canvas;
    this.stage = stage;
    this.layer = layer;
    this.lines = lines;
    this.zoomOutput = zoomOutput;
    this.onSelect = onSelect;
    this.onPositionsChanged = onPositionsChanged;
    this.onRelationshipVisibilityChanged = onRelationshipVisibilityChanged;
    this.scheduleFrame = scheduleFrame;
    this.cancelFrame = cancelFrame;
    this.catalog = null;
    this.positions = new Map();
    this.cards = new Map();
    this.tableByName = new Map();
    this.columnIndexes = new Map();
    this.relationshipElements = new Map();
    this.diagramRelationships = [];
    this.relationshipsByTable = new Map();
    this.measuredCardWidth = CARD_WIDTH;
    this.selectedName = null;
    this.interactive = true;
    this.view = { x: 75, y: 70, zoom: 1 };
    this.drag = null;
    this.pan = null;
    this.canvas.addEventListener("pointerdown", event => this.startPan(event));
    this.canvas.addEventListener("pointermove", event => this.movePan(event));
    this.canvas.addEventListener("pointerup", event => this.endPan(event));
    this.canvas.addEventListener("pointercancel", event => this.endPan(event));
    this.canvas.addEventListener("wheel", event => this.handleWheel(event), { passive: false });
    this.applyView();
  }

  clear() {
    this.discardDrag();
    this.catalog = null;
    this.positions.clear();
    this.cards.clear();
    this.tableByName.clear();
    this.columnIndexes.clear();
    this.relationshipElements.clear();
    this.diagramRelationships = [];
    this.relationshipsByTable.clear();
    this.onRelationshipVisibilityChanged(0, 0);
    this.selectedName = null;
    replace(this.layer);
    replace(this.lines);
  }

  setCatalog(catalog, serverPositions = []) {
    this.discardDrag();
    this.catalog = catalog;
    this.tableByName = new Map(catalog.tables.map(table => [table.name, table]));
    this.columnIndexes = new Map(catalog.tables.map(table => [
      table.name,
      new Map(table.columns.map((column, index) => [column.name, Math.min(index, MAX_CARD_COLUMNS)])),
    ]));
    const saved = new Map(serverPositions.map(position => [position.name, { x: position.x, y: position.y }]));
    const generated = initialPositions(catalog.tables);
    this.positions = new Map(catalog.tables.map(table => [table.name, saved.get(table.name) || generated.get(table.name)]));
    if (!this.tableByName.has(this.selectedName)) this.selectedName = null;
    this.updateDiagramRelationships();
    this.render();
  }

  render() {
    replace(this.layer);
    this.cards.clear();
    if (!this.catalog) return;
    const foreignColumns = new Set(this.catalog.relationships.flatMap(relationship =>
      relationship.sourceColumns.map(column => `${relationship.sourceTable}\u0000${column}`)));
    for (const table of this.catalog.tables) {
      const card = element("article", {
        className: `table-card${table.name === this.selectedName ? " selected" : ""}`,
        attrs: {
          tabindex: "0",
          role: "button",
          "aria-label": `${table.name}, ${table.columns.length} columns. Use arrow keys to move this table.`,
          "aria-pressed": table.name === this.selectedName ? "true" : "false",
          "aria-disabled": this.interactive ? "false" : "true",
        },
        dataset: { tableName: table.name },
      });
      const position = this.positions.get(table.name);
      card.style.left = `${position.x}px`;
      card.style.top = `${position.y}px`;

      const head = element("header", { className: "table-head" });
      head.append(
        element("span", { className: "table-accent", attrs: { "aria-hidden": "true" } }),
        element("strong", { text: table.name }),
        element("small", { text: `${table.columns.length} ${table.columns.length === 1 ? "column" : "columns"}` }),
      );
      head.addEventListener("pointerdown", event => this.startDrag(event, table.name, card));
      card.append(head);

      for (const column of table.columns.slice(0, MAX_CARD_COLUMNS)) {
        const row = element("div", { className: "table-column" });
        row.append(
          columnBadges(table, foreignColumns, column.name),
          element("span", { text: column.name }),
          element("code", { text: column.dataType }),
        );
        card.append(row);
      }
      if (table.columns.length > MAX_CARD_COLUMNS) {
        const remaining = table.columns.length - MAX_CARD_COLUMNS;
        const row = element("div", { className: "table-column" });
        row.append(
          element("span", { className: "column-badges", text: "…", attrs: { "aria-hidden": "true" } }),
          element("span", { text: `${remaining} more ${remaining === 1 ? "column" : "columns"}` }),
          element("code", { text: "INSPECT" }),
        );
        card.append(row);
      }
      card.addEventListener("click", () => this.select(table.name));
      card.addEventListener("keydown", event => this.handleCardKeydown(event, table.name));
      this.cards.set(table.name, card);
      this.layer.append(card);
    }
    this.measureCardWidth();
    this.drawRelationships();
  }

  select(name, { focus = false } = {}) {
    if (!this.tableByName.has(name)) return;
    const selectionChanged = this.selectedName !== name;
    if (!selectionChanged) {
      if (focus) this.cards.get(name)?.focus();
      return;
    }
    const previousName = this.selectedName;
    this.selectedName = name;
    for (const tableName of [previousName, name]) {
      if (!tableName) continue;
      const card = this.cards.get(tableName);
      if (!card) continue;
      const selected = tableName === name;
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-pressed", selected ? "true" : "false");
    }
    if (focus) this.cards.get(name)?.focus();
    if (selectionChanged && this.catalog.relationships.length > MAX_DIAGRAM_RELATIONSHIPS) {
      this.updateDiagramRelationships();
      this.drawRelationships();
    }
    this.onSelect(name);
  }

  updateDiagramRelationships() {
    if (!this.catalog) {
      this.diagramRelationships = [];
      this.relationshipsByTable.clear();
      this.onRelationshipVisibilityChanged(0, 0);
      return;
    }
    const namespace = this.catalog.namespace;
    const eligible = this.catalog.relationships.filter(relationship =>
      relationship.sourceNamespace === namespace && relationship.targetNamespace === namespace);
    if (eligible.length <= MAX_DIAGRAM_RELATIONSHIPS) this.diagramRelationships = eligible;
    else {
      const connected = [];
      const remaining = [];
      for (const relationship of eligible) {
        if (relationship.sourceTable === this.selectedName || relationship.targetTable === this.selectedName) connected.push(relationship);
        else remaining.push(relationship);
      }
      this.diagramRelationships = connected.concat(remaining).slice(0, MAX_DIAGRAM_RELATIONSHIPS);
    }
    this.relationshipsByTable = new Map();
    for (const relationship of this.diagramRelationships) {
      const source = this.relationshipsByTable.get(relationship.sourceTable) || [];
      source.push(relationship);
      this.relationshipsByTable.set(relationship.sourceTable, source);
      if (relationship.targetTable === relationship.sourceTable) continue;
      const target = this.relationshipsByTable.get(relationship.targetTable) || [];
      target.push(relationship);
      this.relationshipsByTable.set(relationship.targetTable, target);
    }
    this.onRelationshipVisibilityChanged(this.diagramRelationships.length, eligible.length);
  }

  focusTable(name) {
    this.select(name, { focus: true });
  }

  startDrag(event, name, card) {
    if (!this.interactive || this.drag || event.button !== 0) return;
    event.preventDefault();
    const position = this.positions.get(name);
    const dragHandle = event.currentTarget;
    const pointerId = event.pointerId;
    this.drag = {
      pointerId,
      name,
      card,
      handle: dragHandle,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      x: position.x,
      y: position.y,
      renderedX: position.x,
      renderedY: position.y,
      moved: false,
      frame: null,
      cleanup: null,
    };
    card.classList.add("dragging");
    dragHandle.setPointerCapture(pointerId);
    const move = moveEvent => this.moveDrag(moveEvent);
    const cleanup = () => {
      dragHandle.removeEventListener("pointermove", move);
      dragHandle.removeEventListener("pointerup", end);
      dragHandle.removeEventListener("pointercancel", end);
      dragHandle.removeEventListener("lostpointercapture", end);
    };
    const end = endEvent => this.endDrag(endEvent);
    this.drag.cleanup = cleanup;
    dragHandle.addEventListener("pointermove", move);
    dragHandle.addEventListener("pointerup", end);
    dragHandle.addEventListener("pointercancel", end);
    dragHandle.addEventListener("lostpointercapture", end);
    this.select(name);
  }

  moveDrag(event) {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    event.preventDefault();
    const x = clamp(this.drag.originX + (event.clientX - this.drag.startX) / this.view.zoom, -MAX_COORDINATE, MAX_COORDINATE);
    const y = clamp(this.drag.originY + (event.clientY - this.drag.startY) / this.view.zoom, -MAX_COORDINATE, MAX_COORDINATE);
    if (!this.drag.moved && Math.abs(x - this.drag.originX) <= 1 && Math.abs(y - this.drag.originY) <= 1) return;
    this.drag.moved = true;
    this.drag.x = x;
    this.drag.y = y;
    this.scheduleDragRender();
  }

  scheduleDragRender() {
    const drag = this.drag;
    if (!drag || drag.frame !== null) return;
    drag.frame = this.scheduleFrame(() => {
      if (this.drag !== drag) return;
      drag.frame = null;
      this.renderDrag(drag);
    });
  }

  renderDrag(drag, { commit = false } = {}) {
    const geometryChanged = drag.x !== drag.renderedX || drag.y !== drag.renderedY;
    if (commit) {
      drag.card.style.left = `${drag.x}px`;
      drag.card.style.top = `${drag.y}px`;
      drag.card.style.transform = "";
    } else {
      drag.card.style.transform = `translate3d(${drag.x - drag.originX}px, ${drag.y - drag.originY}px, 0)`;
    }
    if (geometryChanged) {
      this.drawRelationships(drag.name);
      drag.renderedX = drag.x;
      drag.renderedY = drag.y;
    }
  }

  endDrag(event) {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    const drag = this.drag;
    drag.cleanup?.();
    if (drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
    if (drag.frame !== null) this.cancelFrame(drag.frame);
    const changed = drag.x !== drag.originX || drag.y !== drag.originY;
    if (drag.moved) {
      if (changed) this.positions.set(drag.name, { x: drag.x, y: drag.y });
      this.renderDrag(drag, { commit: true });
    }
    drag.card.classList.remove("dragging");
    this.drag = null;
    if (changed) this.onPositionsChanged();
  }

  discardDrag() {
    if (!this.drag) return;
    const drag = this.drag;
    drag.cleanup?.();
    if (drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
    if (drag.frame !== null) this.cancelFrame(drag.frame);
    drag.card.classList.remove("dragging");
    drag.card.style.transform = "";
    this.drag = null;
  }

  handleCardKeydown(event, name) {
    if (!this.interactive) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      this.select(name);
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
    this.select(name);
    const step = event.shiftKey ? 32 : 8;
    const position = this.positions.get(name);
    const next = {
      x: clamp(position.x + delta[0] * step, -MAX_COORDINATE, MAX_COORDINATE),
      y: clamp(position.y + delta[1] * step, -MAX_COORDINATE, MAX_COORDINATE),
    };
    if (next.x === position.x && next.y === position.y) return;
    this.positions.set(name, next);
    const card = this.cards.get(name);
    card.style.left = `${next.x}px`;
    card.style.top = `${next.y}px`;
    this.drawRelationships(name);
    this.onPositionsChanged();
  }

  getPositions() {
    if (!this.catalog) return [];
    return this.catalog.tables.map(table => {
      const position = this.positions.get(table.name);
      return { name: table.name, x: position.x, y: position.y };
    });
  }

  measureCardWidth() {
    const card = this.cards.values().next().value;
    this.measuredCardWidth = card?.offsetWidth || Number.parseFloat(card ? getComputedStyle(card).width : "") || CARD_WIDTH;
  }

  refreshGeometry() {
    this.measureCardWidth();
    this.drawRelationships();
  }

  positionFor(name) {
    if (this.drag?.name === name) return { x: this.drag.x, y: this.drag.y };
    return this.positions.get(name);
  }

  drawRelationships(changedTable = null) {
    if (!this.catalog) {
      replace(this.lines);
      this.relationshipElements.clear();
      return;
    }
    const relationships = changedTable
      ? (this.relationshipsByTable.get(changedTable) || [])
      : this.diagramRelationships;
    const activeKeys = changedTable ? null : new Set();
    for (const relationship of relationships) {
      const key = `${relationship.sourceNamespace}\u0000${relationship.sourceTable}\u0000${relationship.name}`;
      activeKeys?.add(key);
      const source = this.positionFor(relationship.sourceTable);
      const target = this.positionFor(relationship.targetTable);
      if (!source || !target) {
        this.relationshipElements.get(key)?.group.remove();
        this.relationshipElements.delete(key);
        continue;
      }
      const sourceIndex = this.columnIndexes.get(relationship.sourceTable)?.get(relationship.sourceColumns[0]) ?? 0;
      const targetIndex = this.columnIndexes.get(relationship.targetTable)?.get(relationship.targetColumns[0]) ?? 0;
      const sourceIsLeft = source.x <= target.x;
      const x1 = source.x + (sourceIsLeft ? this.measuredCardWidth : 0);
      const x2 = target.x + (sourceIsLeft ? 0 : this.measuredCardWidth);
      const y1 = source.y + HEADER_HEIGHT + sourceIndex * COLUMN_HEIGHT + COLUMN_HEIGHT / 2;
      const y2 = target.y + HEADER_HEIGHT + targetIndex * COLUMN_HEIGHT + COLUMN_HEIGHT / 2;
      const bend = Math.max(70, Math.abs(x2 - x1) * 0.45);
      const direction = sourceIsLeft ? 1 : -1;
      const data = `M ${x1} ${y1} C ${x1 + bend * direction} ${y1}, ${x2 - bend * direction} ${y2}, ${x2} ${y2}`;
      let entry = this.relationshipElements.get(key);
      if (!entry) {
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const shadow = document.createElementNS("http://www.w3.org/2000/svg", "path");
        shadow.setAttribute("class", "relationship-shadow");
        const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
        line.setAttribute("class", "relationship-line");
        const sourceEnd = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        sourceEnd.setAttribute("class", "relationship-end");
        sourceEnd.setAttribute("r", "4");
        const targetEnd = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        targetEnd.setAttribute("class", "relationship-end");
        targetEnd.setAttribute("r", "4");
        group.append(shadow, line, sourceEnd, targetEnd);
        this.lines.append(group);
        entry = { group, shadow, line, sourceEnd, targetEnd };
        this.relationshipElements.set(key, entry);
      }
      const { shadow, line, sourceEnd, targetEnd } = entry;
      shadow.setAttribute("d", data);
      line.setAttribute("d", data);
      sourceEnd.setAttribute("cx", String(x1));
      sourceEnd.setAttribute("cy", String(y1));
      targetEnd.setAttribute("cx", String(x2));
      targetEnd.setAttribute("cy", String(y2));
    }
    if (!changedTable) {
      for (const [key, entry] of this.relationshipElements) {
        if (activeKeys.has(key)) continue;
        entry.group.remove();
        this.relationshipElements.delete(key);
      }
    }
  }

  applyView() {
    this.stage.style.transform = `translate(${this.view.x}px, ${this.view.y}px) scale(${this.view.zoom})`;
    this.zoomOutput.value = `${Math.round(this.view.zoom * 100)}%`;
    this.zoomOutput.textContent = `${Math.round(this.view.zoom * 100)}%`;
  }

  setInteractive(interactive) {
    this.interactive = interactive;
    this.canvas.classList.toggle("is-loading", !interactive);
    for (const card of this.cards.values()) card.setAttribute("aria-disabled", interactive ? "false" : "true");
  }

  startPan(event) {
    if (event.button !== 0 || event.target.closest(".table-card, .catalog-state, .conflict-banner")) return;
    event.preventDefault();
    this.pan = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: this.view.x,
      originY: this.view.y,
    };
    this.canvas.classList.add("panning");
    this.canvas.setPointerCapture(event.pointerId);
  }

  movePan(event) {
    if (!this.pan || event.pointerId !== this.pan.pointerId) return;
    this.view.x = this.pan.originX + event.clientX - this.pan.startX;
    this.view.y = this.pan.originY + event.clientY - this.pan.startY;
    this.applyView();
  }

  endPan(event) {
    if (!this.pan || event.pointerId !== this.pan.pointerId) return;
    this.pan = null;
    this.canvas.classList.remove("panning");
  }

  handleWheel(event) {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      this.zoomAt(event.deltaY < 0 ? 0.1 : -0.1, event.clientX, event.clientY);
      return;
    }
    this.view.x -= event.deltaX;
    this.view.y -= event.deltaY;
    this.applyView();
  }

  zoomBy(amount) {
    const reserved = window.matchMedia("(max-width: 680px)").matches ? 0 : 340;
    const centerX = Math.max(120, (this.canvas.clientWidth - reserved) / 2);
    const centerY = this.canvas.clientHeight / 2;
    const bounds = this.canvas.getBoundingClientRect();
    this.zoomAt(amount, bounds.left + centerX, bounds.top + centerY);
  }

  zoomAt(amount, clientX, clientY) {
    const next = clamp(this.view.zoom + amount, MIN_ZOOM, MAX_ZOOM);
    const bounds = this.canvas.getBoundingClientRect();
    const pointX = clientX - bounds.left;
    const pointY = clientY - bounds.top;
    const worldX = (pointX - this.view.x) / this.view.zoom;
    const worldY = (pointY - this.view.y) / this.view.zoom;
    this.view.x = pointX - worldX * next;
    this.view.y = pointY - worldY * next;
    this.view.zoom = next;
    this.applyView();
  }

  fit() {
    if (!this.catalog?.tables.length) return false;
    const entries = this.catalog.tables.map(table => {
      const position = this.positions.get(table.name);
      return { ...position, width: this.measuredCardWidth, height: cardHeight(table) };
    });
    const minX = Math.min(...entries.map(item => item.x));
    const minY = Math.min(...entries.map(item => item.y));
    const maxX = Math.max(...entries.map(item => item.x + item.width));
    const maxY = Math.max(...entries.map(item => item.y + item.height));
    const mobile = window.matchMedia("(max-width: 680px)").matches;
    const left = mobile ? 18 : 75;
    const top = 65;
    const right = mobile ? 18 : 360;
    const bottom = mobile ? 75 : 35;
    const width = Math.max(120, this.canvas.clientWidth - left - right);
    const height = Math.max(120, this.canvas.clientHeight - top - bottom);
    const zoom = clamp(Math.min(width / Math.max(1, maxX - minX), height / Math.max(1, maxY - minY)) * 0.9, MIN_ZOOM, 1.25);
    this.view = { x: left + (width - (maxX - minX) * zoom) / 2 - minX * zoom, y: top + (height - (maxY - minY) * zoom) / 2 - minY * zoom, zoom };
    this.applyView();
    return true;
  }
}
