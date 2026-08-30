const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const styles = fs.readFileSync("src/schemii/shared_web/theme.css", "utf8") + fs.readFileSync("src/schemii/web/styles.css", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const helperStart = source.indexOf("function isAdditiveTableSelection(event)");
const helperEnd = source.indexOf("function selectTable(", helperStart);
assert.notEqual(helperStart, -1, "additive selection helper is missing");
assert.notEqual(helperEnd, -1, "additive selection helper end marker is missing");

const context = vm.createContext({ Boolean });
vm.runInContext(`${source.slice(helperStart, helperEnd)}\nglobalThis.isAdditiveTableSelection = isAdditiveTableSelection;\nglobalThis.selectionBounds = selectionBounds;\nglobalThis.rectanglesIntersect = rectanglesIntersect;\nglobalThis.inspectorHeaderGesture = inspectorHeaderGesture;`, context);

assert.equal(context.isAdditiveTableSelection({ shiftKey: true, ctrlKey: false, metaKey: false }), true);
assert.equal(context.isAdditiveTableSelection({ shiftKey: false, ctrlKey: true, metaKey: false }), true);
assert.equal(context.isAdditiveTableSelection({ shiftKey: false, ctrlKey: false, metaKey: true }), true);
assert.equal(context.isAdditiveTableSelection({ shiftKey: false, ctrlKey: false, metaKey: false }), false);
assert.deepEqual(
  JSON.parse(JSON.stringify(context.selectionBounds(80, 90, 20, 30))),
  { left: 20, top: 30, right: 80, bottom: 90 }
);
assert.equal(context.rectanglesIntersect({ left: 0, top: 0, right: 20, bottom: 20 }, { left: 15, top: 15, right: 30, bottom: 30 }), true);
assert.equal(context.rectanglesIntersect({ left: 0, top: 0, right: 20, bottom: 20 }, { left: 21, top: 21, right: 30, bottom: 30 }), false);
assert.equal(context.inspectorHeaderGesture("left", true, false), "hide-data");
assert.equal(context.inspectorHeaderGesture("left", false, false), "collapse-inspector");
assert.equal(context.inspectorHeaderGesture("left", false, true), "expand-inspector");
assert.equal(context.inspectorHeaderGesture("right", false, false), "show-data");
assert.equal(context.inspectorHeaderGesture("right", true, false), "maximize-data");
assert.equal(context.inspectorHeaderGesture("right", false, true), "expand-with-data");

const selectStart = source.indexOf("function selectTable(");
const selectEnd = source.indexOf("function setRelationMode(", selectStart);
assert.match(source.slice(selectStart, selectEnd), /!additive && openInspector/, "selection must support deferring the inspector overlay");

const pointerDownStart = source.indexOf('elements.tablesLayer.addEventListener("pointerdown"');
const pointerDownEnd = source.indexOf('elements.tablesLayer.addEventListener("pointermove"', pointerDownStart);
assert.match(source.slice(pointerDownStart, pointerDownEnd), /selectTable\(tableId, additiveSelection, false\)/, "pointer down must defer the inspector until the gesture is known to be a click");
assert.match(source.slice(pointerDownStart, pointerDownEnd), /prepareInspectorTileForTablePress\(\)/, "pointer down must prepare the inspector tile before revealing it");
assert.match(source.slice(pointerDownStart, pointerDownEnd), /openInspectorPane\(\)/, "the inspector tile must appear as soon as the table is pressed");
assert.ok(
  source.slice(pointerDownStart, pointerDownEnd).indexOf("prepareInspectorTileForTablePress()") < source.slice(pointerDownStart, pointerDownEnd).indexOf("selectTable(tableId, additiveSelection, false)"),
  "the tile must reach its compact height before table selection reveals the pane"
);
assert.match(source.slice(pointerDownStart, pointerDownEnd), /card: document\.querySelector/, "group drag must cache selected table elements before movement");

const prepareTileStart = source.indexOf("function prepareInspectorTileForTablePress");
const prepareTileEnd = source.indexOf("function setInspectorContentCollapsed", prepareTileStart);
const prepareTileSource = source.slice(prepareTileStart, prepareTileEnd);
assert.match(prepareTileSource, /inspectorContentCollapsed = true/, "tile preparation must set compact state directly");
assert.match(prepareTileSource, /void elements\.inspector\.offsetHeight/, "hidden tile dimensions must be committed before slide-in");

const pointerUpStart = source.indexOf('elements.tablesLayer.addEventListener("pointerup"');
const pointerUpEnd = source.indexOf('elements.tablesLayer.addEventListener("pointercancel"', pointerUpStart);
assert.match(source.slice(pointerUpStart, pointerUpEnd), /!tablePressState\.additive/, "additive selection must not open table data");
assert.match(source.slice(pointerUpStart, pointerUpEnd), /openInspectorPane\(\)/, "pointer up must open the inspector after a confirmed click");
assert.match(source.slice(pointerUpStart, pointerUpEnd), /setInspectorContentCollapsed\(false\)/, "pointer release must expand the inspector from its tile");
assert.doesNotMatch(source.slice(pointerUpStart, pointerUpEnd), /!tablePressState\.moved|!tableMoved/, "drag release must expand only after movement finishes");
const tablePointerMoveStart = source.indexOf('elements.tablesLayer.addEventListener("pointermove"');
const tablePointerMoveEnd = source.indexOf('elements.tablesLayer.addEventListener("pointerup"', tablePointerMoveStart);
const tablePointerMoveSource = source.slice(tablePointerMoveStart, tablePointerMoveEnd);
assert.match(tablePointerMoveSource, /style\.transform = `translate3d\(/, "table movement must use composited transforms");
assert.doesNotMatch(tablePointerMoveSource, /saveSchema\(/, "table movement must not save during pointer moves");
assert.doesNotMatch(tablePointerMoveSource, /renderConnections\(\)/, "table movement must freeze relationship rendering until release");
assert.match(source.slice(pointerUpStart, pointerUpEnd), /renderConnections\(\)/, "table release must draw final connection positions immediately");
assert.match(source.slice(pointerUpStart, pointerUpEnd), /saveSchema\(LAYOUT_SAVE_DELAY_MS\)/, "table release must defer and coalesce layout persistence");
assert.match(source, /const LAYOUT_SAVE_DELAY_MS = 750/, "layout persistence must wait for gesture idle");
assert.match(source, /const columnMetrics = new Map\(\)/, "connection rendering must collect column metrics once per frame");
assert.match(styles, /\.workspace\.table-dragging \.connections\s*\{[^}]*opacity:\s*\.16;/, "frozen relationships must fade during active dragging");
assert.match(styles, /\.workspace\.panning \.connections\s*\{[^}]*visibility:\s*hidden;/, "active panning must skip relationship-layer painting");
assert.match(styles, /\.table-card\.dragging\s*\{[^}]*will-change:\s*transform;/, "active table cards must use compositor promotion");

const workspacePointerStart = source.indexOf('elements.workspace.addEventListener("pointerdown"');
const workspacePointerEnd = source.indexOf('elements.workspace.addEventListener("pointermove"', workspacePointerStart);
const workspacePointerSource = source.slice(workspacePointerStart, workspacePointerEnd);
assert.ok(
  workspacePointerSource.indexOf("event.button === 1") < workspacePointerSource.indexOf('closest(".connection-hit")'),
  "middle-click panning must take priority over connection hit targets"
);
assert.match(workspacePointerSource, /event\.preventDefault\(\)/, "middle-click panning must suppress browser auto-scroll");
assert.match(workspacePointerSource, /middlePanPanelSnapshot = collapseWorkspacePanelsForMiddlePan\(\)/, "middle-click must snapshot and collapse open workspace panels");
assert.match(workspacePointerSource, /marqueeState = \{/, "blank-canvas left drag must initialize marquee selection");
assert.match(workspacePointerSource, /baseSelection: additive \? new Set\(selectedTableIds\) : new Set\(\)/, "modifier marquee selection must retain the existing selection");

const middlePanelStart = source.indexOf("function collapseWorkspacePanelsForMiddlePan");
const middlePanelEnd = source.indexOf("function setInspectorContentCollapsed", middlePanelStart);
const middlePanelSource = source.slice(middlePanelStart, middlePanelEnd);
assert.match(middlePanelSource, /tableDataPanelExpanded/, "middle pan must remember Data View visibility");
assert.match(middlePanelSource, /tableDataPanelMaximized/, "middle pan must remember fullscreen state");
assert.match(middlePanelSource, /tablePanelActivePane/, "middle pan must remember the active Data or Console pane");
assert.match(middlePanelSource, /prepareInspectorTileForTablePress\(\)/, "middle pan must leave only the inspector tile visible");
assert.match(middlePanelSource, /setTableDataPanelExpanded\(true\)/, "middle pan release must restore an open Data View");
assert.doesNotMatch(middlePanelSource, /setInspectorContentCollapsed\(snapshot\.inspectorContentCollapsed\)/, "middle pan release must not restore expanded inspector content");

const workspacePointerMoveEnd = source.indexOf('elements.workspace.addEventListener("pointerup"', workspacePointerEnd);
const workspacePointerMoveSource = source.slice(workspacePointerEnd, workspacePointerMoveEnd);
assert.match(workspacePointerMoveSource, /updateMarqueeSelection\(event\)/, "marquee movement must continuously update table selection");
assert.match(workspacePointerMoveSource, /prepareInspectorTileForTablePress\(\)/, "marquee selection must leave the inspector minimized");
assert.match(source, /card\.getBoundingClientRect\(\)/, "marquee selection must use rendered rectangles so zoom is accounted for");
assert.match(workspacePointerMoveSource, /applyStageTransform\(\)/, "active panning must update only the composited stage transform");
assert.doesNotMatch(workspacePointerMoveSource, /applyView\(\)/, "active panning must not repaint the grid or other view UI");

const workspacePointerUpStart = source.indexOf('elements.workspace.addEventListener("pointerup"');
const workspacePointerUpEnd = source.indexOf('elements.workspace.addEventListener("pointercancel"', workspacePointerUpStart);
assert.match(source.slice(workspacePointerUpStart, workspacePointerUpEnd), /restoreWorkspacePanelsAfterMiddlePan\(\)/, "middle-button release must restore prior panels");
assert.match(source.slice(workspacePointerUpStart, workspacePointerUpEnd), /applyView\(\)/, "pan release must synchronize the complete view once");
assert.match(source.slice(workspacePointerUpStart, workspacePointerUpEnd), /selectTable\(null, false, false\)/, "a blank-canvas click without a drag must still clear selection");
const workspacePointerCancelEnd = source.indexOf('elements.workspace.addEventListener("wheel"', workspacePointerUpEnd);
assert.match(source.slice(workspacePointerUpEnd, workspacePointerCancelEnd), /restoreWorkspacePanelsAfterMiddlePan\(\)/, "cancelled middle panning must restore prior panels");

const iconFocusStart = source.indexOf("function focusInspectorDatabaseTarget");
const iconFocusEnd = source.indexOf("function closeObjectIconMenu", iconFocusStart);
const iconFocusSource = source.slice(iconFocusStart, iconFocusEnd);
assert.match(iconFocusSource, /const preservedView = \{ \.\.\.view \}/, "icon focus must preserve the canvas view");
assert.match(iconFocusSource, /elements\.inspector\.scrollTo/, "icon focus scrolling must stay inside the inspector");
assert.doesNotMatch(iconFocusSource, /scrollIntoView/, "icon focus must not scroll the canvas or page");
assert.match(iconFocusSource, /"primary-key": "primary"/, "primary icons must target the exact column toggle");
assert.match(iconFocusSource, /inspector-object-focus-control/, "icon focus must visibly cue the exact control");
assert.match(styles, /@keyframes inspector-object-focus/, "inspector controls must have a visual focus cue");
assert.match(styles, /\.main-layout\.inspector-collapsed \.inspector\s*\{[^}]*transform:\s*translate3d\(100%, 0, 0\)/, "collapsed inspector must use an animatable transform");
assert.doesNotMatch(styles, /\.main-layout\.inspector-collapsed \.inspector\s*\{[^}]*display:\s*none/, "collapsed inspector must remain renderable during transitions");
assert.match(styles, /--pane-header-height:\s*48px/, "workspace pane headers must share a fixed height");
assert.match(styles, /\.inspector-head\s*\{[^}]*position:\s*sticky;[^}]*height:\s*var\(--pane-header-height\)/, "inspector header must remain fixed while its pane scrolls");
assert.match(styles, /\.table-data-panel-head\s*\{[^}]*height:\s*var\(--pane-header-height\)/, "data and inspector headers must use the same height");
assert.match(styles, /\.inspector-head-actions\s*\{[^}]*position:\s*relative;[^}]*z-index:\s*2;/, "inspector actions must remain above the full-header toggle");
assert.match(html, /<body class="app-hydrating">/, "initial markup must suppress pre-hydration pane animation");
assert.match(html, /id="selection-marquee"[^>]*hidden/, "workspace must include a hidden marquee overlay");
assert.match(styles, /\.selection-marquee\s*\{[^}]*pointer-events:\s*none;/, "marquee overlay must not capture pointer input");
assert.match(styles, /\.app-hydrating \.inspector\s*\{[^}]*visibility:\s*hidden;[^}]*transition:\s*none;/, "hydrating inspector must remain hidden without transitions");
assert.match(source, /document\.body\.classList\.remove\("app-hydrating"\)/, "pane animations must activate after schema hydration");
assert.match(source, /elements\.inspectorContent\.addEventListener\("contextmenu"/, "inspector header must support right-click data actions");
assert.match(source, /elements\.tableDataPanelHead\.addEventListener\("contextmenu"/, "data header must toggle fullscreen on right-click");
assert.match(source, /elements\.showSqlConsolePane\.addEventListener\("contextmenu"/, "console header must toggle fullscreen on right-click");

const closeInspectorStart = source.indexOf("function closeInspectorPane");
const closeInspectorEnd = source.indexOf("function selectTable", closeInspectorStart);
const closeInspectorSource = source.slice(closeInspectorStart, closeInspectorEnd);
assert.match(closeInspectorSource, /classList\.add\("inspector-dismissed"\)/, "inspector must begin closing before resetting its tile state");
assert.match(closeInspectorSource, /inspectorDismissTransitionTimer = setTimeout/, "collapsed inspector reset must wait for the close transition");
assert.ok(
  closeInspectorSource.indexOf('classList.add("inspector-dismissed")') < closeInspectorSource.indexOf("inspectorContentCollapsed = false"),
  "the minimized inspector must not expand before sliding out"
);

console.log("Table selection interaction tests passed");
