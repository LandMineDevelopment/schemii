import { edgeRelationship, comparableTypes, validateLogicalRelationship } from "./logical-relationships.js";
import { GraphViewport } from "#common/graph-viewport.js";
import { isAlias } from "./alias-model.js";
import { exposedFields, setFieldExposure } from "./model-state.js";
import { nodeColumns } from "./model-columns.js";
import { columnFilterBindings } from "./model-filter-links.js";
import { createIconElement } from "/assets/common/ui.js";

const WIDTH = 286, HEADER = 60, ROW = 30, VISIBLE_ROWS = 10;
const svgNode = (tag, attrs = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
};
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** A model-specific rendering surface backed by the shared product viewport. */
export function createModelCanvas({ host, catalog, getDraft, onChange, onSelectNode, onSelectEdge, onCreateConnection, connectionButton }) {
  host.classList.add("sc-canvas");
  host.tabIndex = 0;
  host.setAttribute("aria-label", "Semantic model canvas. Drag background to pan; scroll to zoom.");
  const stage = el("div", "sc-stage"), cards = el("div", "sc-cards");
  const edges = svgNode("svg", { class: "sc-edges", "aria-label": "Model relationships" });
  stage.append(edges, cards);
  host.append(stage);
  let connecting=false, connectionSource=null;
  const controls=el("div","sc-connection-tools"), connect=connectionButton, notice=el("span","sc-connection-status");
  connect.disabled=false;notice.setAttribute("role","status");
  controls.append(notice);host.append(controls);
  function connectionMode(enabled) {connecting=enabled;connectionSource=null;connect.title=enabled?"Cancel connection":"Draw connection";connect.setAttribute("aria-label",connect.title);connect.setAttribute("aria-pressed",String(enabled));notice.textContent=enabled?"Choose the first column, then a compatible column. Query direction follows the starting object.":"";render();}
  connect.onclick=()=>connectionMode(!connecting);
  function chooseColumn(node,column) {
    if(!connectionSource){connectionSource={node,column};notice.textContent=`${node.label}.${column.name} selected. Choose a highlighted column.`;render();return;}
    const edge={id:`logical_${crypto.randomUUID().replaceAll("-","")}`,kind:"logical",source:connectionSource.node.id,sourceColumn:connectionSource.column.name,target:node.id,targetColumn:column.name,enabled:true};
    const error=validateLogicalRelationship(edge,getDraft(),catalog);
    if(error){notice.textContent=error;return;}
    connectionMode(false);onCreateConnection?.(edge);
  }
  const viewport = new GraphViewport({ host, stage, minZoom: 0.08, maxZoom: 1.8,
    canStartPan: event => !event.target.closest(".sc-node, .sc-edge, .sc-connection-tools") });
  viewport.handleWheel = event => {
    if (event.target.closest(".sc-node-fields") && !event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const zoom = viewport.getView().zoom;
    viewport.zoomAt(zoom * (Math.exp(-event.deltaY * 0.0015) - 1), event.clientX, event.clientY);
  };
  let cycleEdges = new Set(), usedEdges = new Set(), selectedNode = null, selectedEdge = null;
  let filterNodes = new Set(), sourceIssueNodes = new Set(), sourceIssueEdges = new Set();
  let destroyed = false, initialFit = true, fitFrame;
  const columns = node => nodeColumns(getDraft(), catalog, node);
  const height = node => HEADER + Math.min(columns(node).length, VISIBLE_ROWS) * ROW + 2;
  const fieldLists = new Map();
  const port = (node, column) => {
    const index = columns(node).findIndex(candidate => candidate.name === column);
    if (index < 0) return HEADER / 2;
    const offset = fieldLists.get(node.id)?.scrollTop || 0;
    return Math.max(HEADER + ROW / 2, Math.min(height(node) - ROW / 2, HEADER + (index + .5) * ROW - offset));
  };
  const position = node => viewport.dragPosition(node.id) || { x: node.x, y: node.y };

  function ensureLayout() {
    const nodes = getDraft().nodes;
    const count = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
    const bottoms = Array(count).fill(40);
    nodes.forEach((node, index) => {
      const col = index % count;
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) {
        node.x = 50 + col * (WIDTH + 130);
        node.y = bottoms[col];
      }
      bottoms[col] = Math.max(bottoms[col], node.y + height(node) + 110);
    });
  }

  function drawEdges() {
    edges.replaceChildren();
    const nodes = new Map(getDraft().nodes.map(node => [node.id, node]));
    for(const target of nodes.values()) {
      const source=nodes.get(target.derivation?.connection?.target || target.derivation?.source);if(!source)continue;
      const a=position(source),b=position(target),right=b.x>=a.x;
      const pair=target.derivation.connection?.columns[0];
      const x1=a.x+(right?WIDTH:0),x2=b.x+(right?0:WIDTH),y1=a.y+port(source,pair?.target),y2=b.y+port(target,pair?.source);
      const d=`M ${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}`;
      const group=svgNode("g",{class:"sc-edge sc-derived-edge",tabindex:"0",role:"button","aria-label":pair ? `${target.label}.${pair.source} connects to ${source.label}.${pair.target}` : `${target.label} calculated from ${source.label}`});
      group.append(svgNode("path",{d,class:"sc-edge-hit"}),svgNode("path",{d,class:"sc-edge-line"}));
      const select=()=>{selectedNode=target.id;selectedEdge=null;onSelectNode?.(target.id);};
      group.addEventListener("click",select);
      group.addEventListener("keydown",event=>{if(["Enter"," "].includes(event.key)){event.preventDefault();select();}});
      edges.append(group);
    }
    for (const edge of getDraft().edges) {
      const source = nodes.get(edge.source), target = nodes.get(edge.target);
      if (!source || !target) continue;
      const relation = edgeRelationship(edge, catalog);
      if (!relation) continue;
      const a = position(source), b = position(target);
      const self = source.id === target.id;
      const right = self || b.x >= a.x;
      const x1 = a.x + (right ? WIDTH : 0), x2 = b.x + (right && !self ? 0 : WIDTH);
      const y1 = a.y + port(source, relation.sourceColumn), y2 = b.y + port(target, relation.targetColumn);
      const bend = Math.max(65, Math.abs(x2 - x1) * 0.45);
      const d = self
        ? `M ${x1} ${y1} C ${x1 + 115} ${y1 - 65}, ${x2 + 115} ${y2 + 65}, ${x2} ${y2}`
        : `M ${x1} ${y1} C ${x1 + (right ? bend : -bend)} ${y1}, ${x2 + (right ? -bend : bend)} ${y2}, ${x2} ${y2}`;
      const cycle = edge.enabled && cycleEdges.has(edge.id), sourceChanged=sourceIssueEdges.has(edge.id);
      const state = !edge.enabled ? "disabled" : cycle ? "cycle" : usedEdges.has(edge.id) ? "current preview path" : "enabled · not in current preview";
      const label = `${source.label || source.table}.${relation.sourceColumn} to ${target.label || target.table}.${relation.targetColumn}${edge.kind === "logical" ? " · logical relationship" : ""} · ${state}`;
      const group = svgNode("g", { class: `sc-edge${!edge.enabled ? " sc-disabled" : ""}${cycle ? " sc-cycle" : ""}${sourceChanged ? " sc-source-changed" : ""}${usedEdges.has(edge.id) ? " sc-used" : ""}${selectedEdge === edge.id ? " sc-selected" : ""}`, tabindex: "0", role: "button", "aria-label": label, "data-edge-id": edge.id });
      const title = svgNode("title"); title.textContent = label;
      group.append(title, svgNode("path", { d, class: "sc-edge-hit" }), svgNode("path", { d, class: "sc-edge-line" }));
      group.append(svgNode("circle", { cx: x1, cy: y1, r: 4 }), svgNode("circle", { cx: x2, cy: y2, r: 4 }));
      if (cycle) {
        const text = svgNode("text", { x: self ? x1 + 76 : (x1 + x2) / 2, y: (y1 + y2) / 2 - 7, class: "sc-edge-label" });
        text.textContent = "CYCLE"; group.append(text);
      }
      const select = () => { selectedEdge = edge.id; selectedNode = null; drawEdges(); onSelectEdge?.(edge.id); };
      group.addEventListener("click", select);
      group.addEventListener("keydown", event => {
        if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); }
      });
      edges.append(group);
    }
  }

  function render(options = {}) {
    if (destroyed) return;
    if (options.cycleEdges !== undefined) cycleEdges = new Set(options.cycleEdges);
    if (options.usedEdges !== undefined) usedEdges = new Set(options.usedEdges);
    if (options.sourceIssues !== undefined) {
      sourceIssueNodes=new Set();sourceIssueEdges=new Set();
      for(const issue of options.sourceIssues) {
        if(issue.nodeId)sourceIssueNodes.add(issue.nodeId);
        for(const id of issue.nodeIds || [])sourceIssueNodes.add(id);
        for(const id of issue.edgeIds || [])sourceIssueEdges.add(id);
        if(issue.table)for(const node of getDraft().nodes)if(node.table===issue.table)sourceIssueNodes.add(node.id);
      }
    }
    viewport.cancelInteractions();
    ensureLayout();
    const scrollPositions = new Map([...fieldLists].map(([id, list]) => [id, list.scrollTop]));
    const focusedRow = document.activeElement?.closest?.(".sc-column");
    const focusKey = focusedRow && [focusedRow.closest(".sc-node")?.dataset.nodeId, focusedRow.dataset.columnName];
    fieldLists.clear();
    cards.replaceChildren();
    const draft = getDraft();
    const exposed = new Set(exposedFields(draft, catalog).map(field => JSON.stringify([field.table, field.column])));
    for (const node of draft.nodes) {
      const card = el("section", `sc-node${isAlias(node) ? " sc-alias" : ""}${node.derivation ? " sc-derived" : ""}${node.id === draft.root ? " sc-root" : ""}${selectedNode === node.id ? " sc-selected" : ""}${filterNodes.has(node.id) ? " sc-filter-bound" : ""}${sourceIssueNodes.has(node.id) ? " sc-source-changed" : ""}`);
      card.dataset.nodeId = node.id;
      card.style.left = `${node.x}px`; card.style.top = `${node.y}px`;
      card.setAttribute("aria-label", `${node.label || node.table} model source`);
      const header = el("button", "sc-node-header"); header.type = "button";
      header.title = "Select source to inspect. Drag to move; arrow keys move the source.";
      header.append(el("strong", "sc-node-name", node.label || node.table), el("small", "sc-node-detail", `${node.table}${node.id === draft.root ? " · ROOT" : ""}`));
      if(isAlias(node)) header.append(el("span","sc-alias-badge","ALIAS"));
      if(sourceIssueNodes.has(node.id))header.append(el("span","sc-source-badge","SOURCE CHANGED"));
      if(node.derivation) {
        header.querySelector("small").textContent=`${draft.nodes.find(n=>n.id===node.derivation.source)?.label || node.table} · virtual`;
        header.append(el("span","sc-alias-badge",node.derivation.kind==="row" ? "CALC" : "SUMMARY"));
      }
      let moved = false;
      header.addEventListener("pointerdown", event => {
        event.stopPropagation(); moved = false;
        viewport.beginNodeDrag(event, { key: node.id, element: card, position: node,
          onFrame: () => { moved = true; drawEdges(); },
          onCommit: next => { Object.assign(node, next); onChange?.({ layoutOnly: true }); drawEdges(); },
          onCancel: drawEdges,
        });
      });
      header.addEventListener("click", () => {
        if (moved) { moved = false; return; }
        selectedNode = node.id; selectedEdge = null;
        for (const item of cards.children) item.classList.toggle("sc-selected", item.dataset.nodeId === node.id);
        drawEdges(); onSelectNode?.(node.id);
      });
      header.addEventListener("keydown", event => {
        const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        if (!delta) return;
        event.preventDefault(); const distance = event.shiftKey ? 50 : 10;
        node.x += delta[0] * distance; node.y += delta[1] * distance;
        card.style.left = `${node.x}px`; card.style.top = `${node.y}px`;
        drawEdges(); onChange?.({ layoutOnly: true });
      });
      card.append(header);
      const fields = el("div", "sc-node-fields");
      fields.tabIndex = 0;
      fields.setAttribute("role", "group");
      fields.setAttribute("aria-label", `${node.label || node.table} fields; scroll to see all ${columns(node).length} fields`);
      fields.addEventListener("scroll", drawEdges, { passive: true });
      for (const column of columns(node)) {
        const row = el(connecting ? "div" : "label", "sc-column"), input = document.createElement("input");
        row.dataset.columnName = column.name;
        input.type = "checkbox";
        if(connecting) {
          input.disabled=true;
          const chosen=connectionSource?.node.id===node.id && connectionSource?.column.name===column.name;
          const compatible=!node.derivation && (connectionSource ? comparableTypes(connectionSource.column.dataType,column.dataType) : comparableTypes(column.dataType,column.dataType));
          row.classList.toggle("sc-connection-source",Boolean(chosen));row.classList.toggle("sc-connection-target",Boolean(connectionSource && compatible && !chosen));row.classList.toggle("sc-connection-invalid",!compatible);
          row.tabIndex=0;row.setAttribute("role","button");row.setAttribute("aria-disabled",String(!compatible));row.setAttribute("aria-pressed",String(Boolean(chosen)));row.setAttribute("aria-label",`Connect ${node.label}.${column.name}${compatible ? "" : " · incompatible type"}`);
          const choose=event=>{event.preventDefault();event.stopPropagation();if(compatible)chooseColumn(node,column);};
          row.addEventListener("click",choose);row.addEventListener("keydown",event=>{if(["Enter"," "].includes(event.key))choose(event);});
        }
        input.checked = exposed.has(JSON.stringify([node.id, column.name]));
        input.setAttribute("aria-label", `Expose ${node.label || node.table}.${column.name}`);
        input.addEventListener("change", () => {
          const current = getDraft();
          setFieldExposure(current, catalog, node.id, column.name, input.checked);
          onChange?.();
        });
        const name = el("span", "sc-column-name", column.label || column.name), type = el("small", "sc-column-type", column.dataType);
        name.title = column.label || column.name; type.title = column.dataType;
        const bindings = columnFilterBindings(draft, node.id, column.name);
        const badges = el("span", "sc-column-badges");
        if (bindings.length) {
          const marker = el("span", "sc-column-filter");
          const description = `Model filters: ${[...new Set(bindings.map(binding => binding.scope.label))].join(", ")}`;
          marker.title = description;
          marker.setAttribute("aria-label", description);
          marker.append(createIconElement("filter"));
          badges.append(marker);
        }
        row.append(input, badges, name, type); fields.append(row);
      }
      card.append(fields);
      fieldLists.set(node.id, fields);
      cards.append(card);
      fields.scrollTop = scrollPositions.get(node.id) || 0;
    }
    if (focusKey) {
      const row = [...fieldLists.get(focusKey[0])?.children || []].find(candidate => candidate.dataset.columnName === focusKey[1]);
      (connecting ? row : row?.querySelector("input"))?.focus({ preventScroll: true });
    }
    drawEdges();
    if (initialFit) { initialFit = false; fitFrame = requestAnimationFrame(fitInitial); }
  }

  function fit() {
    if (destroyed) return;
    ensureLayout(); const nodes = getDraft().nodes;
    if (!nodes.length) return;
    viewport.fitBounds({ minX: Math.min(...nodes.map(n => n.x)) - 20, minY: Math.min(...nodes.map(n => n.y)),
      maxX: Math.max(...nodes.map(n => n.x + WIDTH)) + 120, maxY: Math.max(...nodes.map(n => n.y + height(n))) }, { maxZoom: 1 });
  }
  function fitInitial() {
    fit();
    if (viewport.getView().zoom >= .95) return;
    const draft = getDraft();
    const root = draft.nodes.find(node => node.id === (draft.defaultRoot || draft.root)) || draft.nodes[0];
    if (!root) return;
    viewport.setView({
      x: host.clientWidth / 2 - root.x - WIDTH / 2,
      y: host.clientHeight / 2 - root.y - height(root) / 2,
      zoom: 1,
    });
  }
  const keyboard = event => {
    if(event.key === "Escape" && connecting){event.preventDefault();connectionMode(false);return;}
    if (event.target === host && event.key.toLowerCase() === "f") { event.preventDefault(); fit(); }
  };
  host.addEventListener("keydown", keyboard);
  return { render, fit, startConnection:()=>connectionMode(true), highlightFilters(ids) {
      filterNodes=new Set(ids);
      for(const card of cards.children)card.classList.toggle("sc-filter-bound",filterNodes.has(card.dataset.nodeId));
    }, zoomBy: factor => viewport.zoomBy(viewport.getView().zoom * (factor - 1)),
    destroy() { connect.onclick=null;connect.disabled=true;connect.setAttribute("aria-pressed","false");connect.title="Draw connection";connect.setAttribute("aria-label","Draw connection"); destroyed = true; cancelAnimationFrame(fitFrame); viewport.destroy(); host.removeEventListener("keydown", keyboard); stage.remove(); controls.remove(); } };
}
