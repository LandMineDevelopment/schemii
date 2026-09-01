import { api, ApiError } from "./api.js";
import { CatalogCanvas } from "./canvas.js";
import {
  createDesignRelationship,
  createDesignTable,
  designLayoutContent,
  designPositions,
  designToCatalog,
  updateDesignTable,
} from "./design.js";
import {
  allViews,
  renderCatalogStats,
  renderFunctions,
  renderInspector,
  renderObjects,
  renderViewDetail,
  renderViewsList,
} from "./catalog.js";
import { element, emptyPanel, errorPanel, formatTimestamp, replace } from "./dom.js";
import { assertUnavailableControls, bindUnavailableControls } from "./unavailable.js";
import { closeDetailsMenus, createStatePanel, DockPane, downloadContent, initializeUi } from "./ui.js";
import {
  readWorkspaceNavigation,
  readWorkspacePreferences,
  updateWorkspacePreferences,
  workspaceNavigationHref,
} from "./workspace-navigation.js";

const byId = id => document.getElementById(id);
const DEFAULT_CANVAS_VIEW = Object.freeze({ x: 75, y: 70, zoom: 1 });

const elements = {
  runtimeDot: byId("runtime-dot"),
  runtimeStatus: byId("runtime-status"),
  workspaceTitle: byId("workspace-title"),
  newWorkspaceButton: byId("new-workspace-button"),
  connectionsButton: byId("connections-button"),
  workspacesButton: byId("workspaces-button"),
  refreshCatalogButton: byId("refresh-catalog-button"),
  saveLayoutButton: byId("save-layout-button"),
  downloadCatalogButton: byId("download-catalog-button"),
  exportDesignSqlButton: byId("export-design-sql-button"),
  introductionButton: byId("introduction-button"),
  mainLayout: byId("main-layout"),
  canvas: byId("canvas"),
  canvasStage: byId("canvas-stage"),
  tablesLayer: byId("tables-layer"),
  relationshipLines: byId("relationship-lines"),
  catalogState: byId("catalog-state"),
  conflictBanner: byId("conflict-banner"),
  conflictMessage: byId("conflict-message"),
  applyConnectionLayoutButton: byId("apply-connection-layout-button"),
  reloadConflictButton: byId("reload-conflict-button"),
  zoomOutput: byId("zoom-output"),
  relationshipOutput: byId("relationship-output"),
  fitButton: byId("fit-button"),
  createTableButton: byId("create-table-button"),
  createRelationshipButton: byId("create-relationship-button"),
  zoomInButton: byId("zoom-in-button"),
  zoomOutButton: byId("zoom-out-button"),
  inspector: byId("inspector"),
  inspectorBody: byId("table-inspector-body"),
  inspectorToggle: byId("table-inspector-toggle"),
  inspectorClose: byId("table-inspector-close"),
  inspectorTitle: byId("table-inspector-title"),
  inspectorEyebrow: byId("table-inspector-eyebrow"),
  inspectorEmptyCopy: byId("inspector-empty-copy"),
  inspectorEmpty: byId("inspector-empty"),
  inspectorContent: byId("inspector-content"),
  editTableButton: byId("edit-table-button"),
  deleteTableButton: byId("delete-table-button"),
  catalogStats: byId("catalog-stats"),
  viewsList: byId("views-list"),
  viewsSearch: byId("views-search"),
  viewDetail: byId("view-detail"),
  refreshViewsButton: byId("refresh-views-button"),
  sqlDraft: byId("sql-draft"),
  newSqlDraftButton: byId("new-sql-draft-button"),
  sqlTargetConnection: byId("sql-target-connection"),
  sqlTargetDatabase: byId("sql-target-database"),
  sqlTargetNamespace: byId("sql-target-namespace"),
  connectionsDialog: byId("connections-dialog"),
  connectionsCount: byId("connections-count"),
  connectionsList: byId("connections-list"),
  reloadConnectionsButton: byId("reload-connections-button"),
  addConnectionButton: byId("add-connection-button"),
  connectionEditorDialog: byId("connection-editor-dialog"),
  connectionForm: byId("connection-form"),
  connectionEditorTitle: byId("connection-editor-title"),
  connectionEditorCopy: byId("connection-editor-copy"),
  connectionName: byId("connection-name"),
  connectionHost: byId("connection-host"),
  connectionPort: byId("connection-port"),
  connectionDatabase: byId("connection-database"),
  connectionUsername: byId("connection-username"),
  connectionPassword: byId("connection-password"),
  connectionSslMode: byId("connection-ssl-mode"),
  connectionTimeout: byId("connection-timeout"),
  removeCredentialRow: byId("remove-credential-row"),
  removeCredential: byId("remove-credential"),
  connectionFormStatus: byId("connection-form-status"),
  saveConnectionButton: byId("save-connection-button"),
  reloadEditorConnection: byId("reload-editor-connection"),
  workspacesDialog: byId("workspaces-dialog"),
  workspacesCount: byId("workspaces-count"),
  workspacesList: byId("workspaces-list"),
  reloadWorkspacesButton: byId("reload-workspaces-button"),
  workspaceForm: byId("workspace-form"),
  workspaceName: byId("workspace-name"),
  workspaceMode: byId("workspace-mode"),
  workspaceFormCopy: byId("workspace-form-copy"),
  workspaceTargetFields: [...document.querySelectorAll(".workspace-target-field")],
  workspaceConnection: byId("workspace-connection"),
  workspaceDatabase: byId("workspace-database"),
  workspaceNamespace: byId("workspace-namespace"),
  workspaceFormStatus: byId("workspace-form-status"),
  createWorkspaceButton: byId("create-workspace-button"),
  designTableDialog: byId("design-table-dialog"),
  designTableForm: byId("design-table-form"),
  designTableTitle: byId("design-table-title"),
  designTableCopy: byId("design-table-copy"),
  designTableName: byId("design-table-name"),
  designColumns: byId("design-columns"),
  addDesignColumnButton: byId("add-design-column-button"),
  designTableStatus: byId("design-table-status"),
  saveDesignTableButton: byId("save-design-table-button"),
  designRelationshipDialog: byId("design-relationship-dialog"),
  designRelationshipForm: byId("design-relationship-form"),
  designRelationshipName: byId("design-relationship-name"),
  designRelationshipSource: byId("design-relationship-source"),
  designRelationshipTarget: byId("design-relationship-target"),
  designRelationshipKey: byId("design-relationship-key"),
  designRelationshipMappings: byId("design-relationship-mappings"),
  designRelationshipOnUpdate: byId("design-relationship-on-update"),
  designRelationshipOnDelete: byId("design-relationship-on-delete"),
  designRelationshipDeferrable: byId("design-relationship-deferrable"),
  designRelationshipDeferred: byId("design-relationship-deferred"),
  designRelationshipStatus: byId("design-relationship-status"),
  saveDesignRelationshipButton: byId("save-design-relationship-button"),
  functionsButton: byId("functions-button"),
  functionsDialog: byId("functions-dialog"),
  functionsSearch: byId("functions-search"),
  functionsCount: byId("functions-count"),
  functionsList: byId("functions-list"),
  objectsButton: byId("objects-button"),
  objectsDialog: byId("objects-dialog"),
  objectsSearch: byId("objects-search"),
  objectsCount: byId("objects-count"),
  objectsList: byId("objects-list"),
  postgresButton: byId("postgres-button"),
  introductionDialog: byId("introduction-dialog"),
  unavailableDialog: byId("unavailable-dialog"),
  unavailableTitle: byId("unavailable-title"),
  unavailableDescription: byId("unavailable-description"),
  unavailableId: byId("unavailable-id"),
  confirmDialog: byId("confirm-dialog"),
  confirmTitle: byId("confirm-title"),
  confirmMessage: byId("confirm-message"),
  confirmAction: byId("confirm-action"),
  toast: byId("toast"),
};

initializeUi();

let inspectorPreferenceReady = false;
const inspectorPane = new DockPane({
  container: elements.mainLayout,
  pane: elements.inspector,
  body: elements.inspectorBody,
  toggle: elements.inspectorToggle,
  dismiss: elements.inspectorClose,
  side: "right",
  expandedLabel: "Minimize table inspector",
  minimizedLabel: "Expand table inspector",
  getRestoreFocusTarget: () => document.querySelector(`.table-card[data-table-name="${CSS.escape(state.selectedTableName || "")}"]`) || elements.canvas,
  onStateChange: persistInspectorState,
});
inspectorPane.setAvailable(false);

const state = {
  startupComplete: false,
  session: null,
  readiness: null,
  runtimeError: null,
  connections: [],
  connectionsLoading: true,
  connectionsLoaded: false,
  connectionsError: null,
  connectionActionError: null,
  connectionTests: new Map(),
  workspaces: [],
  workspacesLoading: true,
  workspacesLoaded: false,
  workspacesError: null,
  workspaceActionError: null,
  activeWorkspace: null,
  design: null,
  designLayout: null,
  designSubmitting: false,
  designTableEditorId: null,
  designRelationshipAutoName: null,
  catalog: null,
  catalogLoading: false,
  catalogError: null,
  catalogGeneration: 0,
  selectedTableName: null,
  selectedViewName: null,
  selectedViewKind: null,
  activeLayer: "tables",
  viewFilter: "all",
  connectionEditorId: null,
  connectionEditorSnapshot: null,
  connectionEditorGeneration: 0,
  connectionSubmitting: false,
  workspaceSubmitting: false,
  workspaceDialogGeneration: 0,
  layoutTimer: null,
  layoutSaving: false,
  layoutSavePromise: null,
  layoutSaveGeneration: 0,
  layoutDirty: false,
  layoutVersion: 0,
  layoutConflict: false,
  layoutConflictKind: null,
  layoutError: null,
  preservedLayout: null,
  confirmCallback: null,
  confirmBusy: false,
  toastTimer: null,
  canvasResizeFrame: null,
  preferenceTimer: null,
  navigationGeneration: 0,
  restoringNavigation: false,
};
inspectorPreferenceReady = true;

const canvas = new CatalogCanvas({
  canvas: elements.canvas,
  stage: elements.canvasStage,
  layer: elements.tablesLayer,
  lines: elements.relationshipLines,
  zoomOutput: elements.zoomOutput,
  getViewportInsets: () => ({
    right: inspectorPane.containerState() === "expanded" || inspectorPane.containerState() === "minimized" ? 360 : 20,
  }),
  onSelect: selectTable,
  onPositionsChanged: positionsChanged,
  onRelationshipVisibilityChanged: (shown, available) => {
    elements.relationshipOutput.hidden = shown === available;
    elements.relationshipOutput.textContent = shown === available
      ? ""
      : `${shown.toLocaleString()} of ${available.toLocaleString()} relationships shown`;
  },
});

function openDialog(dialog) {
  // TODO(ui-dialog-focus): Move modal lifecycle into a shared controller that records the invoking control and restores focus after every close path, including programmatic closes and nested editor/confirmation flows.
  if (!dialog.open) dialog.showModal();
}

function showToast(message, { error = false } = {}) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", error);
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4500);
}

function errorToast(error) {
  const suffix = error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : "";
  showToast(`${error instanceof Error ? error.message : "The request failed"}.${suffix}`, { error: true });
}

function connectionById(id) {
  return state.connections.find(connection => connection.id === id) || null;
}

function workspaceLabel(workspace) {
  return workspace.connectionId
    ? `${workspace.database} · ${workspace.namespace}`
    : workspace.name;
}

function workspaceTargetLabel(workspace) {
  if (!workspace.connectionId) return "Detached design";
  return connectionById(workspace.connectionId)?.name || workspace.connectionId;
}

function isDetachedWorkspace(workspace = state.activeWorkspace) {
  return Boolean(workspace && !workspace.connectionId);
}

function selectedDesignTable() {
  if (!isDetachedWorkspace() || !state.design || !state.selectedTableName) return null;
  return state.design.content.tables.find(table => table.name === state.selectedTableName) || null;
}

function updateDesignControls() {
  const detached = isDetachedWorkspace();
  const busy = state.catalogLoading || state.designSubmitting;
  const selected = selectedDesignTable();
  const hasTargetKey = state.design?.content.tables.some(table => (
    table.keys.some(key => key.kind === "primary" || key.kind === "unique")
  ));
  elements.createTableButton.disabled = !detached || busy;
  elements.createRelationshipButton.disabled = !detached || busy || !hasTargetKey;
  elements.editTableButton.disabled = !selected || busy;
  elements.deleteTableButton.disabled = !selected || busy;
}

function workspaceStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function currentWorkspaceNavigation() {
  return {
    workspaceId: state.activeWorkspace?.id || null,
    layer: state.activeLayer,
    table: state.activeLayer === "tables" ? state.selectedTableName : null,
    view: state.activeLayer === "views" ? state.selectedViewName : null,
    viewKind: state.activeLayer === "views" ? state.selectedViewKind : null,
  };
}

function syncWorkspaceNavigation(historyMode = "replace") {
  if (!historyMode || state.restoringNavigation) return;
  const next = workspaceNavigationHref(window.location.href, currentWorkspaceNavigation());
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  const method = historyMode === "push" ? "pushState" : "replaceState";
  window.history[method](null, "", next);
}

function persistCanvasView() {
  window.clearTimeout(state.preferenceTimer);
  state.preferenceTimer = null;
  if (!state.activeWorkspace) return;
  updateWorkspacePreferences(workspaceStorage(), state.activeWorkspace.id, {
    camera: { ...canvas.view },
  });
}

function scheduleCanvasViewPersistence() {
  if (!state.activeWorkspace || state.restoringNavigation) return;
  window.clearTimeout(state.preferenceTimer);
  state.preferenceTimer = window.setTimeout(persistCanvasView, 120);
}

function persistInspectorState(paneState) {
  if (!inspectorPreferenceReady || state.restoringNavigation || !state.activeWorkspace) return;
  if (!["expanded", "minimized", "dismissed"].includes(paneState)) return;
  updateWorkspacePreferences(workspaceStorage(), state.activeWorkspace.id, {
    inspector: paneState,
  });
}

function restoreCanvasView(workspaceId) {
  const preferences = readWorkspacePreferences(workspaceStorage(), workspaceId);
  canvas.view = preferences.camera || DEFAULT_CANVAS_VIEW;
  return preferences;
}

function applyWorkspaceNavigation(navigation) {
  const wasRestoring = state.restoringNavigation;
  state.restoringNavigation = true;
  try {
    setLayer(navigation.layer, { historyMode: null });
    if (navigation.layer === "tables") {
      const table = state.catalog?.tables.find(item => item.name === navigation.table) || null;
      if (table) canvas.select(table.name, { notify: true });
      else {
        canvas.clearSelection();
        selectTable(null, { historyMode: null });
      }
      if (table) {
        const preferences = readWorkspacePreferences(workspaceStorage(), state.activeWorkspace?.id);
        if (preferences.inspector) inspectorPane.setState(preferences.inspector);
      }
    } else if (navigation.layer === "views") {
      const view = allViews(state.catalog).find(item => (
        item.name === navigation.view && item.catalogKind === navigation.viewKind
      )) || null;
      selectView(view, { historyMode: null });
    }
  } finally {
    state.restoringNavigation = wasRestoring;
  }
}

async function restoreWorkspaceNavigation(navigation, { notifyMissing = true } = {}) {
  const generation = ++state.navigationGeneration;
  const wasRestoring = state.restoringNavigation;
  state.restoringNavigation = true;
  let restored = false;
  try {
    if (!navigation.workspaceId) {
      if (state.activeWorkspace && !await flushLayoutBeforeTransition()) return false;
      clearActiveWorkspace({ historyMode: null });
      setLayer("tables", { historyMode: null });
      restored = true;
      return true;
    }
    if (!state.workspacesLoaded) return false;
    const workspace = state.workspaces.find(item => item.id === navigation.workspaceId);
    if (!workspace) {
      if (state.activeWorkspace && !await flushLayoutBeforeTransition()) return false;
      clearActiveWorkspace({ historyMode: null });
      setLayer("tables", { historyMode: null });
      if (notifyMissing) showToast("The workspace saved in this browser URL no longer exists.", { error: true });
      restored = true;
      return true;
    }
    if (state.activeWorkspace?.id !== workspace.id || !state.catalog) {
      if (!await openWorkspace(workspace, { historyMode: null })) return false;
    }
    if (generation !== state.navigationGeneration || !state.catalog) return false;
    applyWorkspaceNavigation(navigation);
    restored = true;
    return true;
  } finally {
    state.restoringNavigation = wasRestoring;
    if (restored && generation === state.navigationGeneration) syncWorkspaceNavigation("replace");
  }
}

function updateHeader() {
  const detached = isDetachedWorkspace();
  elements.workspaceTitle.textContent = state.activeWorkspace ? workspaceLabel(state.activeWorkspace) : "No workspace open";
  elements.runtimeDot.className = "status-dot";
  if (state.runtimeError) {
    elements.runtimeDot.classList.add("error");
    elements.runtimeStatus.textContent = "Active server check failed";
  } else if (!state.readiness) {
    elements.runtimeDot.classList.add("loading");
    elements.runtimeStatus.textContent = "Checking active server";
  } else if (!state.readiness.ready) {
    elements.runtimeDot.classList.add("error");
    elements.runtimeStatus.textContent = "Server not ready";
  } else {
    elements.runtimeDot.classList.add("ready");
    if (state.layoutConflict) elements.runtimeStatus.textContent = "Layout saving stopped · conflict";
    else if (state.layoutSaving) elements.runtimeStatus.textContent = "Saving layout";
    else if (state.catalogLoading) elements.runtimeStatus.textContent = detached ? "Loading saved design" : "Loading live catalog";
    else elements.runtimeStatus.textContent = `Ready · ${state.readiness.persistence}`;
  }
  elements.saveLayoutButton.disabled = !state.catalog || state.catalogLoading || state.layoutSaving || state.layoutConflict;
  elements.refreshCatalogButton.disabled = state.catalogLoading;
  elements.refreshCatalogButton.title = detached ? "Refresh saved design" : "Refresh live catalog";
  elements.refreshCatalogButton.setAttribute("aria-label", elements.refreshCatalogButton.title);
  elements.refreshViewsButton.disabled = state.catalogLoading;
  elements.reloadConflictButton.disabled = state.catalogLoading;
  elements.applyConnectionLayoutButton.disabled = state.catalogLoading;
  updateDesignControls();
  elements.downloadCatalogButton.textContent = detached ? "Download desired design JSON" : "Download live catalog JSON";
  elements.exportDesignSqlButton.disabled = !detached || !state.design || state.catalogLoading;
  elements.inspectorEyebrow.textContent = detached ? "Desired table inspector" : "Read-only table inspector";
  elements.inspectorEmptyCopy.textContent = detached
    ? "Select a designed table to inspect its columns, constraints, indexes, and relationships."
    : "Select a live table to inspect columns, constraints, indexes, triggers, and relationships.";
  elements.canvas.setAttribute("aria-label", detached ? "Desired schema table diagram canvas" : "Live table diagram canvas");
  renderSqlTarget();
}

function stateCard(mark, title, copy, actionLabel, onAction, loading = false) {
  let action = null;
  if (actionLabel && onAction) {
    action = element("button", { className: "ui-button compact", type: "button", text: actionLabel });
    action.addEventListener("click", onAction);
  }
  return createStatePanel({
    mark,
    title,
    message: copy,
    action,
    variant: loading ? "loading" : null,
    surface: true,
    className: "state-card",
  });
}

function renderCatalogState() {
  replace(elements.catalogState);
  if (!state.startupComplete) {
    elements.catalogState.append(stateCard("…", "Loading active server state", "Waiting for the session, readiness, connections, and workspaces APIs.", null, null, true));
    return;
  }
  if (state.runtimeError) {
    elements.catalogState.append(errorPanel(state.runtimeError, { retryLabel: "Retry server checks", onRetry: bootstrap }));
    return;
  }
  if (!state.activeWorkspace) {
    const card = stateCard("WS", "Open a schema workspace", "Create a database-independent design or open a workspace attached to live PostgreSQL.", "Open workspaces", openWorkspaces);
    const connectionAction = element("button", { className: "ui-button compact", type: "button", text: "Manage connections" });
    connectionAction.addEventListener("click", openConnections);
    card.append(connectionAction);
    elements.catalogState.append(card);
    return;
  }
  if (state.catalogLoading) {
    elements.catalogState.append(stateCard("…", isDetachedWorkspace() ? "Loading saved design" : "Loading live catalog", workspaceLabel(state.activeWorkspace), null, null, true));
    return;
  }
  if (state.catalogError) {
    const panel = errorPanel(state.catalogError, { retryLabel: "Retry catalog", onRetry: refreshCatalog });
    if (state.preservedLayout?.workspaceId === state.activeWorkspace.id) {
      panel.append(element("p", { text: "Unsaved table positions remain in this browser and will be reapplied after the live catalog loads." }));
    }
    elements.catalogState.append(panel);
    return;
  }
  if (state.layoutError && !state.layoutConflict) {
    const panel = errorPanel(state.layoutError, { retryLabel: "Retry layout save", onRetry: saveLayout });
    const reload = element("button", { className: "ui-button compact", type: "button", text: isDetachedWorkspace() ? "Reload saved design and layout" : "Reload live catalog and server layout" });
    reload.addEventListener("click", () => loadActiveWorkspace());
    panel.append(reload);
    elements.catalogState.append(panel);
    return;
  }
  if (state.catalog && !state.catalog.tables.length) {
    if (isDetachedWorkspace()) {
      elements.catalogState.append(stateCard("+", "Start with a table", "This design is empty. Add a table and its initial columns; Schemii will save the desired schema independently of PostgreSQL.", "Create table", openDesignTableEditor));
    } else {
      elements.catalogState.append(stateCard("0", "No live tables", `PostgreSQL reported no tables in ${state.catalog.namespace}.`, "Refresh catalog", refreshCatalog));
    }
  }
}

function renderConflict(error = null) {
  elements.conflictBanner.hidden = !state.layoutConflict;
  const connectionChanged = state.layoutConflictKind === "connection";
  elements.conflictMessage.replaceChildren(
    element("strong", { text: connectionChanged ? "Connection changed" : "Layout conflict" }),
    document.createTextNode(connectionChanged
      ? " Local table positions are preserved. Confirm before applying them to the connection's current target."
      : " Local table positions are preserved and saving has stopped."),
  );
  elements.applyConnectionLayoutButton.hidden = !connectionChanged;
  elements.conflictBanner.querySelector(".error-panel")?.remove();
  if (state.layoutConflict && error) {
    const details = errorPanel(error);
    elements.conflictBanner.insertBefore(details, elements.applyConnectionLayoutButton);
  }
}

async function loadRuntime() {
  state.runtimeError = null;
  try {
    const [session, readiness] = await Promise.all([api.session(), api.readiness()]);
    state.session = session;
    state.readiness = readiness;
  } catch (error) {
    state.runtimeError = error;
    state.session = null;
    state.readiness = null;
  }
  updateHeader();
}

async function bootstrap() {
  const requestedNavigation = readWorkspaceNavigation(window.location.href);
  state.startupComplete = false;
  state.connectionsLoading = true;
  state.workspacesLoading = true;
  renderCatalogState();
  renderConnections();
  renderWorkspaces();
  await Promise.all([loadRuntime(), loadConnections(), loadWorkspaces()]);
  state.startupComplete = true;
  await restoreWorkspaceNavigation(requestedNavigation, { notifyMissing: true });
  renderCatalogState();
  updateHeader();
}

async function loadConnections() {
  state.connectionsLoading = true;
  state.connectionsError = null;
  renderConnections();
  try {
    const previousConnections = new Map(state.connections.map(connection => [connection.id, connection]));
    const connections = await api.listConnections();
    const activeConnectionId = state.activeWorkspace?.connectionId;
    const previousActiveConnection = activeConnectionId ? previousConnections.get(activeConnectionId) : null;
    const activeConnection = activeConnectionId ? connections.find(connection => connection.id === activeConnectionId) : null;
    state.connections = connections;
    for (const connection of connections) {
      if (previousConnections.get(connection.id)?.revision !== connection.revision) state.connectionTests.delete(connection.id);
    }
    for (const connectionId of previousConnections.keys()) {
      if (!connections.some(connection => connection.id === connectionId)) state.connectionTests.delete(connectionId);
    }
    if (previousActiveConnection && previousActiveConnection.revision !== activeConnection?.revision) {
      invalidateActiveCatalog({
        preservePendingLayout: true,
        expectedConnectionRevision: previousActiveConnection.revision,
      });
      await loadActiveCatalog();
    }
    state.connectionsLoaded = true;
  } catch (error) {
    state.connectionsError = error;
    state.connectionsLoaded = false;
  } finally {
    state.connectionsLoading = false;
    renderConnections();
    renderWorkspaceConnectionOptions();
    renderSqlTarget();
  }
}

function openConnections() {
  closeDetailsMenus();
  renderConnections();
  openDialog(elements.connectionsDialog);
}

function renderConnections() {
  replace(elements.connectionsList);
  if (state.connectionsLoading) {
    elements.connectionsCount.textContent = "Loading connections";
    elements.connectionsList.append(stateCard("…", "Loading connections", "Waiting for the active connections API.", null, null, true));
    return;
  }
  if (state.connectionsError) {
    elements.connectionsCount.textContent = "Connections unavailable";
    elements.connectionsList.append(errorPanel(state.connectionsError, { retryLabel: "Retry", onRetry: loadConnections }));
    return;
  }
  elements.connectionsCount.textContent = `${state.connections.length} ${state.connections.length === 1 ? "connection" : "connections"}`;
  if (state.connectionActionError) elements.connectionsList.append(errorPanel(state.connectionActionError, { retryLabel: "Reload connections", onRetry: loadConnections }));
  if (!state.connections.length) {
    const action = element("button", { className: "ui-button compact", type: "button", text: "Create connection" });
    action.addEventListener("click", () => openConnectionEditor());
    elements.connectionsList.append(emptyPanel("PG", "No connections", "The active server returned an empty connection list.", action));
    return;
  }
  for (const connection of state.connections) {
    const card = element("article", { className: "manager-card" });
    const copy = element("div");
    copy.append(
      element("strong", { text: connection.name }),
      element("p", { text: `${connection.username}@${connection.host}:${connection.port}/${connection.database}` }),
      element("small", { text: `${connection.sslMode} · credential ${connection.credentialStored ? "stored" : "not stored"} · revision ${connection.revision}` }),
    );
    const testState = state.connectionTests.get(connection.id);
    if (testState?.loading) copy.append(element("p", { className: "connection-test", text: "Testing this live connection…" }));
    else if (testState?.result) copy.append(element("p", { className: "connection-test", text: `Connected to ${testState.result.database} · PostgreSQL ${testState.result.serverVersion}` }));
    else if (testState?.error) copy.append(errorPanel(testState.error));
    const actions = element("div", { className: "manager-actions ui-action-group end wrap" });
    const test = element("button", { className: "ui-button compact", type: "button", text: "Test" });
    test.addEventListener("click", () => testConnection(connection));
    const edit = element("button", { className: "ui-button compact", type: "button", text: "Edit" });
    edit.addEventListener("click", () => openConnectionEditor(connection));
    const remove = element("button", { className: "ui-button compact danger-text", type: "button", text: "Delete" });
    remove.addEventListener("click", () => confirmDeleteConnection(connection));
    actions.append(test, edit, remove);
    card.append(copy, actions);
    elements.connectionsList.append(card);
  }
}

function fillConnectionForm(connection = null) {
  state.connectionEditorId = connection?.id || null;
  state.connectionEditorSnapshot = connection ? { ...connection } : null;
  elements.connectionEditorTitle.textContent = connection ? "Edit connection" : "New connection";
  elements.connectionEditorCopy.textContent = connection
    ? "Password is never returned. Leave it empty to retain the currently stored credential."
    : "Save a reusable PostgreSQL target on this server.";
  elements.connectionName.value = connection?.name || "";
  elements.connectionHost.value = connection?.host || "";
  elements.connectionPort.value = connection?.port ?? 5432;
  elements.connectionDatabase.value = connection?.database || "";
  elements.connectionUsername.value = connection?.username || "";
  elements.connectionPassword.value = "";
  elements.connectionSslMode.value = connection?.sslMode || "verify-full";
  elements.connectionTimeout.value = connection?.connectTimeout ?? 10;
  elements.removeCredentialRow.hidden = !connection?.credentialStored;
  elements.removeCredential.checked = false;
  elements.reloadEditorConnection.hidden = true;
  replace(elements.connectionFormStatus);
}

function openConnectionEditor(connection = null) {
  state.connectionEditorGeneration += 1;
  fillConnectionForm(connection);
  openDialog(elements.connectionEditorDialog);
  elements.connectionName.focus();
}

function connectionFormValues() {
  return {
    name: elements.connectionName.value,
    host: elements.connectionHost.value,
    port: Number(elements.connectionPort.value),
    database: elements.connectionDatabase.value,
    username: elements.connectionUsername.value,
    sslMode: elements.connectionSslMode.value,
    connectTimeout: Number(elements.connectionTimeout.value),
  };
}

async function submitConnection(event) {
  event.preventDefault();
  if (state.connectionSubmitting) return;
  state.connectionSubmitting = true;
  elements.saveConnectionButton.disabled = true;
  const editorSnapshot = state.connectionEditorSnapshot ? { ...state.connectionEditorSnapshot } : null;
  const editing = Boolean(editorSnapshot);
  const editorGeneration = state.connectionEditorGeneration;
  const values = connectionFormValues();
  const password = elements.connectionPassword.value;
  const removeCredential = elements.removeCredential.checked;
  elements.connectionPassword.value = "";
  elements.removeCredential.checked = false;
  replace(elements.connectionFormStatus, element("span", { text: "Saving connection with the active API…" }));
  elements.reloadEditorConnection.hidden = true;
  try {
    let saved;
    let authorityChanged = false;
    if (!editorSnapshot) {
      const body = { ...values };
      if (password) body.password = password;
      saved = await api.createConnection(body);
      state.connections.push(saved);
    } else {
      const body = { expectedRevision: editorSnapshot.revision };
      for (const [field, value] of Object.entries(values)) {
        if (value !== editorSnapshot[field]) body[field] = value;
      }
      if (password) body.password = password;
      else if (removeCredential) body.password = null;
      if (Object.keys(body).length === 1) {
        replace(elements.connectionFormStatus, element("span", { text: "No connection fields changed." }));
        return;
      }
      authorityChanged = Object.keys(body).some(field => !["expectedRevision", "name"].includes(field));
      if (authorityChanged && state.activeWorkspace?.connectionId === editorSnapshot.id && !await flushLayoutBeforeTransition()) {
        replace(elements.connectionFormStatus, element("span", { text: "The current layout could not be saved. Resolve that error before changing this workspace connection." }));
        return;
      }
      if (editorGeneration !== state.connectionEditorGeneration) return;
      saved = await api.updateConnection(editorSnapshot.id, body);
      state.connections = state.connections.map(connection => connection.id === saved.id ? saved : connection);
    }
    state.connectionTests.delete(saved.id);
    state.connectionsLoaded = true;
    state.connectionActionError = null;
    renderConnections();
    renderWorkspaceConnectionOptions();
    renderSqlTarget();
    if (editorGeneration === state.connectionEditorGeneration && elements.connectionEditorDialog.open) elements.connectionEditorDialog.close();
    if (authorityChanged && state.activeWorkspace?.connectionId === saved.id) {
      invalidateActiveCatalog();
      await loadActiveCatalog();
    }
    showToast(editing ? "Connection updated by the active server." : "Connection created by the active server.");
  } catch (error) {
    if (editorGeneration === state.connectionEditorGeneration) {
      replace(elements.connectionFormStatus, errorPanel(error));
      if (error instanceof ApiError && error.status === 409) elements.reloadEditorConnection.hidden = false;
    } else errorToast(error);
  } finally {
    state.connectionSubmitting = false;
    elements.saveConnectionButton.disabled = false;
    if (editorGeneration === state.connectionEditorGeneration) elements.connectionPassword.value = "";
  }
}

async function reloadEditorConnection() {
  if (!state.connectionEditorId) return;
  const connectionId = state.connectionEditorId;
  const editorGeneration = state.connectionEditorGeneration;
  replace(elements.connectionFormStatus, element("span", { text: "Reloading connection metadata…" }));
  try {
    const previous = connectionById(connectionId);
    const connection = await api.getConnection(connectionId);
    state.connections = state.connections.map(item => item.id === connection.id ? connection : item);
    if (previous?.revision !== connection.revision) state.connectionTests.delete(connection.id);
    if (editorGeneration === state.connectionEditorGeneration && state.connectionEditorId === connectionId) fillConnectionForm(connection);
    renderConnections();
    if (previous && previous.revision !== connection.revision && state.activeWorkspace?.connectionId === connection.id) {
      invalidateActiveCatalog({
        preservePendingLayout: true,
        expectedConnectionRevision: previous.revision,
      });
      await loadActiveCatalog();
    }
  } catch (error) {
    if (editorGeneration === state.connectionEditorGeneration && state.connectionEditorId === connectionId) {
      replace(elements.connectionFormStatus, errorPanel(error));
    } else errorToast(error);
  }
}

async function testConnection(connection) {
  const testedRevision = connection.revision;
  state.connectionTests.set(connection.id, { loading: true });
  renderConnections();
  try {
    const result = await api.testConnection(connection.id);
    if (connectionById(connection.id)?.revision === testedRevision) state.connectionTests.set(connection.id, { result });
  } catch (error) {
    if (connectionById(connection.id)?.revision === testedRevision) state.connectionTests.set(connection.id, { error });
  }
  renderConnections();
}

function askConfirmation({ title, message, label, callback }) {
  state.confirmCallback = callback;
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmAction.textContent = label;
  openDialog(elements.confirmDialog);
}

function confirmDeleteConnection(connection) {
  askConfirmation({
    title: "Delete connection",
    message: `Delete “${connection.name}” from this server? Saved workspaces using it will block this request.`,
    label: "Delete connection",
    callback: async () => {
      try {
        await api.deleteConnection(connection.id, connection.revision);
        state.connections = state.connections.filter(item => item.id !== connection.id);
        state.connectionActionError = null;
        renderConnections();
        renderWorkspaceConnectionOptions();
        showToast("Connection deleted by the active server.");
      } catch (error) {
        state.connectionActionError = error;
        renderConnections();
        errorToast(error);
      }
    },
  });
}

async function loadWorkspaces() {
  state.workspacesLoading = true;
  state.workspacesError = null;
  renderWorkspaces();
  try {
    state.workspaces = await api.listWorkspaces();
    state.workspacesLoaded = true;
  } catch (error) {
    state.workspacesError = error;
    state.workspacesLoaded = false;
  } finally {
    state.workspacesLoading = false;
    renderWorkspaces();
  }
}

function openWorkspaces() {
  state.workspaceDialogGeneration += 1;
  closeDetailsMenus();
  renderWorkspaces();
  renderWorkspaceConnectionOptions();
  updateWorkspaceMode();
  openDialog(elements.workspacesDialog);
}

function renderWorkspaceConnectionOptions() {
  const selected = elements.workspaceConnection.value;
  replace(elements.workspaceConnection, element("option", { text: "Select a live connection", attrs: { value: "" } }));
  for (const connection of state.connections) elements.workspaceConnection.append(element("option", { text: `${connection.name} · ${connection.database}`, attrs: { value: connection.id } }));
  if (state.connections.some(connection => connection.id === selected)) elements.workspaceConnection.value = selected;
  updateWorkspaceDatabase();
}

function updateWorkspaceDatabase() {
  const connection = connectionById(elements.workspaceConnection.value);
  elements.workspaceDatabase.textContent = connection ? connection.database : "Select a connection";
}

function updateWorkspaceMode() {
  const attached = elements.workspaceMode.value === "attached";
  for (const field of elements.workspaceTargetFields) field.hidden = !attached;
  elements.workspaceConnection.required = attached;
  elements.workspaceNamespace.required = attached;
  elements.workspaceFormCopy.textContent = attached
    ? "Validate and attach an exact PostgreSQL database and namespace."
    : "Start a durable database-independent schema design.";
  elements.createWorkspaceButton.textContent = attached ? "Create and inspect" : "Create and design";
}

function renderWorkspaces() {
  replace(elements.workspacesList);
  if (state.workspacesLoading) {
    elements.workspacesCount.textContent = "Loading workspaces";
    elements.workspacesList.append(stateCard("…", "Loading workspaces", "Waiting for the active workspaces API.", null, null, true));
    return;
  }
  if (state.workspacesError) {
    elements.workspacesCount.textContent = "Workspaces unavailable";
    elements.workspacesList.append(errorPanel(state.workspacesError, { retryLabel: "Retry", onRetry: loadWorkspaces }));
    return;
  }
  elements.workspacesCount.textContent = `${state.workspaces.length} ${state.workspaces.length === 1 ? "workspace" : "workspaces"}`;
  if (state.workspaceActionError) elements.workspacesList.append(errorPanel(state.workspaceActionError, { retryLabel: "Reload workspaces", onRetry: loadWorkspaces }));
  if (!state.workspaces.length) {
    elements.workspacesList.append(emptyPanel("WS", "No workspaces", "The active server returned an empty workspace list. Create one below."));
    return;
  }
  for (const workspace of state.workspaces) {
    const current = workspace.id === state.activeWorkspace?.id;
    const card = element("article", { className: `manager-card${current ? " current" : ""}` });
    const copy = element("div");
    copy.append(
      element("strong", { text: workspaceLabel(workspace) }),
      element("p", { text: workspaceTargetLabel(workspace) }),
      element("small", { text: `revision ${workspace.revision} · updated ${formatTimestamp(workspace.updatedAt)}` }),
    );
    const actions = element("div", { className: "manager-actions ui-action-group end wrap" });
    const open = element("button", { className: "ui-button compact", type: "button", text: current ? "Reload" : "Open" });
    open.addEventListener("click", async () => {
      if (await openWorkspace(workspace)) elements.workspacesDialog.close();
    });
    const remove = element("button", { className: "ui-button compact danger-text", type: "button", text: "Delete" });
    remove.addEventListener("click", () => confirmDeleteWorkspace(workspace));
    actions.append(open, remove);
    card.append(copy, actions);
    elements.workspacesList.append(card);
  }
}

async function submitWorkspace(event) {
  event.preventDefault();
  if (state.workspaceSubmitting) return;
  const attached = elements.workspaceMode.value === "attached";
  const connection = connectionById(elements.workspaceConnection.value);
  const name = elements.workspaceName.value.trim();
  const namespace = elements.workspaceNamespace.value;
  if (!name) {
    replace(elements.workspaceFormStatus, element("span", { text: "Enter a workspace name." }));
    return;
  }
  if (attached && !connection) {
    replace(elements.workspaceFormStatus, element("span", { text: "Select a connection returned by the active API." }));
    return;
  }
  state.workspaceSubmitting = true;
  const dialogGeneration = state.workspaceDialogGeneration;
  elements.createWorkspaceButton.disabled = true;
  replace(elements.workspaceFormStatus, element("span", { text: attached ? "Creating and validating the workspace target…" : "Creating the detached schema design…" }));
  try {
    if (!await flushLayoutBeforeTransition()) {
      replace(elements.workspaceFormStatus, element("span", { text: "The current layout must be saved or reloaded before opening another workspace." }));
      return;
    }
    if (dialogGeneration !== state.workspaceDialogGeneration) return;
    const workspace = await api.createWorkspace(attached ? {
      name,
      connectionId: connection.id,
      database: connection.database,
      namespace,
    } : { name });
    state.workspaces.push(workspace);
    state.workspaceActionError = null;
    renderWorkspaces();
    if (dialogGeneration === state.workspaceDialogGeneration) {
      elements.workspaceNamespace.value = "";
      elements.workspaceName.value = "";
      replace(elements.workspaceFormStatus);
      const opened = await openWorkspace(workspace);
      if (opened && dialogGeneration === state.workspaceDialogGeneration && elements.workspacesDialog.open) elements.workspacesDialog.close();
    } else showToast("Workspace created by the active server.");
  } catch (error) {
    if (dialogGeneration === state.workspaceDialogGeneration) replace(elements.workspaceFormStatus, errorPanel(error));
    else errorToast(error);
  } finally {
    state.workspaceSubmitting = false;
    elements.createWorkspaceButton.disabled = false;
  }
}

function confirmDeleteWorkspace(workspace) {
  askConfirmation({
    title: "Delete workspace",
    message: `Delete the ${workspaceLabel(workspace)} workspace and its saved layout from this server?`,
    label: "Delete workspace",
    callback: async () => {
      try {
        await api.deleteWorkspace(workspace.id, workspace.revision);
        state.workspaces = state.workspaces.filter(item => item.id !== workspace.id);
        if (state.preservedLayout?.workspaceId === workspace.id) state.preservedLayout = null;
        state.workspaceActionError = null;
        if (state.activeWorkspace?.id === workspace.id) clearActiveWorkspace();
        renderWorkspaces();
        showToast("Workspace deleted by the active server.");
      } catch (error) {
        state.workspaceActionError = error;
        renderWorkspaces();
        errorToast(error);
      }
    },
  });
}

function clearActiveWorkspace({ historyMode = "replace" } = {}) {
  const workspaceId = state.activeWorkspace?.id;
  persistCanvasView();
  state.catalogGeneration += 1;
  resetLayoutSaveState();
  state.activeWorkspace = null;
  state.design = null;
  state.designLayout = null;
  state.catalog = null;
  state.catalogError = null;
  state.catalogLoading = false;
  state.selectedTableName = null;
  state.selectedViewName = null;
  state.selectedViewKind = null;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  if (state.preservedLayout?.workspaceId === workspaceId) state.preservedLayout = null;
  canvas.clear();
  renderConflict();
  renderCatalogSurfaces();
  renderCatalogState();
  updateHeader();
  syncWorkspaceNavigation(historyMode);
}

function resetLayoutSaveState() {
  window.clearTimeout(state.layoutTimer);
  state.layoutSaveGeneration += 1;
  state.layoutSaving = false;
  state.layoutSavePromise = null;
  state.layoutDirty = false;
  state.layoutError = null;
}

function invalidateActiveCatalog({ preservePendingLayout = false, expectedConnectionRevision = null } = {}) {
  if (preservePendingLayout && state.activeWorkspace && state.catalog
      && (state.layoutDirty || state.layoutSaving || state.layoutError || state.layoutConflict)) {
    state.preservedLayout = {
      workspaceId: state.activeWorkspace.id,
      expectedRevision: state.activeWorkspace.revision,
      expectedConnectionRevision,
      hadConflict: state.layoutConflict,
      hadConflictKind: state.layoutConflictKind,
      positions: canvas.getPositions(),
    };
  }
  state.catalogGeneration += 1;
  resetLayoutSaveState();
  state.catalog = null;
  state.design = null;
  state.designLayout = null;
  state.catalogError = null;
  state.catalogLoading = false;
  state.selectedTableName = null;
  state.selectedViewName = null;
  state.selectedViewKind = null;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  canvas.clear();
  renderConflict();
  renderCatalogSurfaces();
  renderCatalogState();
  updateHeader();
}

async function openWorkspace(workspace, { historyMode = "push" } = {}) {
  if (!await flushLayoutBeforeTransition()) return false;
  persistCanvasView();
  resetLayoutSaveState();
  state.activeWorkspace = workspace;
  state.catalog = null;
  state.design = null;
  state.designLayout = null;
  state.catalogError = null;
  state.selectedTableName = null;
  state.selectedViewName = null;
  state.selectedViewKind = null;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  canvas.clear();
  renderConflict();
  renderCatalogSurfaces();
  updateHeader();
  syncWorkspaceNavigation(historyMode);
  await loadActiveWorkspace({ clearConflictOnSuccess: true });
  if (state.catalog && state.activeWorkspace?.id === workspace.id) restoreCanvasView(workspace.id);
  return true;
}

async function loadActiveWorkspace(options = {}) {
  if (isDetachedWorkspace()) return loadActiveDesign(options);
  return loadActiveCatalog(options);
}

async function loadActiveDesign({ clearConflictOnSuccess = false } = {}) {
  if (!isDetachedWorkspace()) return;
  const workspaceId = state.activeWorkspace.id;
  const generation = ++state.catalogGeneration;
  state.catalogLoading = true;
  canvas.setInteractive(false);
  state.catalogError = null;
  renderCatalogState();
  updateHeader();
  try {
    const [design, layout] = await Promise.all([
      api.getDesign(workspaceId),
      api.getDesignLayout(workspaceId),
    ]);
    if (generation !== state.catalogGeneration || state.activeWorkspace?.id !== workspaceId) return;
    state.design = design;
    state.designLayout = layout;
    state.catalog = designToCatalog(state.activeWorkspace, design);
    state.catalogError = null;
    state.layoutError = null;
    state.layoutDirty = false;
    if (clearConflictOnSuccess) {
      state.layoutConflict = false;
      state.layoutConflictKind = null;
    }
    canvas.setCatalog(state.catalog, designPositions(design, layout));
    renderConflict();
    renderCatalogSurfaces();
    const navigation = readWorkspaceNavigation(window.location.href);
    if (navigation.workspaceId === workspaceId) {
      applyWorkspaceNavigation(navigation);
      syncWorkspaceNavigation("replace");
    }
  } catch (error) {
    if (generation !== state.catalogGeneration) return;
    state.catalogError = error;
  } finally {
    if (generation === state.catalogGeneration) {
      state.catalogLoading = false;
      canvas.setInteractive(true);
      renderCatalogState();
      updateHeader();
    }
  }
}

async function loadActiveCatalog({ clearConflictOnSuccess = false } = {}) {
  if (!state.activeWorkspace) return;
  const workspaceId = state.activeWorkspace.id;
  const generation = ++state.catalogGeneration;
  state.catalogLoading = true;
  canvas.setInteractive(false);
  state.catalogError = null;
  renderCatalogState();
  updateHeader();
  try {
    const response = await api.getCatalog(workspaceId);
    if (generation !== state.catalogGeneration || state.activeWorkspace?.id !== workspaceId) return;
    state.activeWorkspace = response.workspace;
    state.workspaces = state.workspaces.map(workspace => workspace.id === response.workspace.id ? response.workspace : workspace);
    state.catalog = response.catalog;
    state.catalogError = null;
    state.layoutError = null;
    const preservedLayout = state.preservedLayout?.workspaceId === workspaceId
      ? state.preservedLayout
      : null;
    state.layoutDirty = Boolean(preservedLayout);
    const workspaceRevisionChanged = Boolean(preservedLayout
      && preservedLayout.expectedRevision !== response.workspace.revision);
    const currentConnectionRevision = connectionById(response.workspace.connectionId)?.revision;
    const connectionRevisionChanged = Boolean(preservedLayout
      && preservedLayout.expectedConnectionRevision !== null
      && preservedLayout.expectedConnectionRevision !== currentConnectionRevision);
    const preservedLayoutConflict = Boolean(preservedLayout
      && (preservedLayout.hadConflict || workspaceRevisionChanged || connectionRevisionChanged));
    if (clearConflictOnSuccess && !preservedLayoutConflict) {
      state.layoutConflict = false;
      state.layoutConflictKind = null;
    }
    if (preservedLayoutConflict) {
      state.layoutConflict = true;
      state.layoutConflictKind = workspaceRevisionChanged || preservedLayout.hadConflictKind === "workspace"
        ? "workspace"
        : "connection";
    }
    canvas.setCatalog(response.catalog, preservedLayout?.positions || response.positions);
    if (preservedLayout) {
      state.preservedLayout = null;
      state.layoutVersion += 1;
      if (!preservedLayoutConflict) state.layoutTimer = window.setTimeout(saveLayout, 550);
    }
    renderConflict();
    renderWorkspaces();
    renderCatalogSurfaces();
    const navigation = readWorkspaceNavigation(window.location.href);
    if (navigation.workspaceId === workspaceId) {
      applyWorkspaceNavigation(navigation);
      syncWorkspaceNavigation("replace");
    }
  } catch (error) {
    if (generation !== state.catalogGeneration) return;
    state.catalogError = error;
  } finally {
    if (generation === state.catalogGeneration) {
      state.catalogLoading = false;
      canvas.setInteractive(true);
      renderCatalogState();
      updateHeader();
    }
  }
}

async function refreshCatalog() {
  if (state.catalogLoading) return;
  if (!state.activeWorkspace) {
    openWorkspaces();
    return;
  }
  if (state.layoutConflict) {
    showToast("Use “Reload server layout” to resolve the layout conflict explicitly.", { error: true });
    return;
  }
  if (state.layoutSaving || state.layoutDirty) {
    showToast("Wait for the pending layout save before refreshing the catalog.");
    return;
  }
  await loadActiveWorkspace();
}

function reloadConflict() {
  if (!state.activeWorkspace || !state.layoutConflict || state.catalogLoading) return;
  loadActiveWorkspace({ clearConflictOnSuccess: true });
}

function renderActiveInspector(table) {
  const desired = state.catalog?.source === "design";
  renderInspector({
    inspector: elements.inspector,
    empty: elements.inspectorEmpty,
    content: elements.inspectorContent,
    title: elements.inspectorTitle,
    table,
    catalog: state.catalog,
    onEditTable: desired && table ? () => openDesignTableEditor(table.designId) : null,
    onAddRelationship: desired && table ? () => openDesignRelationshipEditor(table.designId) : null,
    onDeleteRelationship: desired ? confirmDeleteDesignRelationship : null,
  });
}

function selectTable(name, { historyMode = "push" } = {}) {
  state.selectedTableName = name;
  const table = state.catalog?.tables.find(item => item.name === name) || null;
  renderActiveInspector(table);
  if (table) inspectorPane.reveal();
  else inspectorPane.setAvailable(false, { reset: true });
  updateDesignControls();
  syncWorkspaceNavigation(historyMode);
}

function positionsChanged() {
  if (!state.activeWorkspace || !state.catalog || state.catalogLoading) return;
  state.layoutDirty = true;
  state.layoutError = null;
  state.layoutVersion += 1;
  renderCatalogState();
  if (state.layoutConflict) return;
  window.clearTimeout(state.layoutTimer);
  state.layoutTimer = window.setTimeout(saveLayout, 550);
}

async function saveLayout() {
  window.clearTimeout(state.layoutTimer);
  if (!state.layoutDirty || state.layoutSaving || state.layoutConflict || !state.activeWorkspace || !state.catalog) return;
  const workspaceId = state.activeWorkspace.id;
  const detached = isDetachedWorkspace();
  const connection = detached ? null : connectionById(state.activeWorkspace.connectionId);
  if (!detached && !connection) {
    state.layoutError = new Error("The workspace connection is unavailable. Reload connections before saving this layout");
    renderCatalogState();
    return;
  }
  if (detached && (!state.design || !state.designLayout)) {
    state.layoutError = new Error("The saved design layout is unavailable. Refresh the design before saving positions");
    renderCatalogState();
    return;
  }
  const version = state.layoutVersion;
  const saveGeneration = state.layoutSaveGeneration;
  const positions = canvas.getPositions();
  state.layoutSaving = true;
  state.layoutError = null;
  renderCatalogState();
  updateHeader();
  const savePromise = detached
    ? api.replaceDesignLayout(workspaceId, {
      expectedLayoutRevision: state.designLayout.revision,
      expectedDesignRevision: state.design.revision,
      content: designLayoutContent(state.design, positions, state.designLayout.content.objects),
    })
    : api.updateLayout(workspaceId, {
      expectedRevision: state.activeWorkspace.revision,
      expectedConnectionRevision: connection.revision,
      tables: positions,
    });
  state.layoutSavePromise = savePromise;
  try {
    const result = await savePromise;
    if (saveGeneration !== state.layoutSaveGeneration || state.activeWorkspace?.id !== workspaceId) return;
    if (detached) {
      state.designLayout = result;
    } else {
      state.activeWorkspace = result;
      state.workspaces = state.workspaces.map(item => item.id === result.id ? result : item);
      renderWorkspaces();
    }
    state.layoutDirty = state.layoutVersion !== version;
    state.layoutError = null;
  } catch (error) {
    if (saveGeneration !== state.layoutSaveGeneration || state.activeWorkspace?.id !== workspaceId) return;
    state.layoutDirty = true;
    state.layoutError = error;
    if (error instanceof ApiError && error.status === 409 && error.code === "design_layout_conflict") {
      state.layoutConflict = true;
      state.layoutConflictKind = "design";
      state.layoutError = null;
      renderConflict(error);
    } else if (error instanceof ApiError && error.status === 409 && error.code === "workspace_conflict") {
      state.layoutConflict = true;
      state.layoutConflictKind = "workspace";
      state.layoutError = null;
      renderConflict(error);
    } else if (error instanceof ApiError && error.status === 409 && error.code === "connection_conflict") {
      state.layoutConflict = true;
      state.layoutConflictKind = "connection";
      state.layoutError = null;
      renderConflict(error);
      await loadConnections();
    }
  } finally {
    if (saveGeneration === state.layoutSaveGeneration && state.activeWorkspace?.id === workspaceId) {
      state.layoutSaving = false;
      if (state.layoutSavePromise === savePromise) state.layoutSavePromise = null;
      renderCatalogState();
      updateHeader();
      if (state.layoutDirty && !state.layoutConflict && !state.layoutError) {
        state.layoutTimer = window.setTimeout(saveLayout, 250);
      }
    }
  }
}

function applyLayoutToCurrentConnection() {
  if (state.catalogLoading || !state.layoutConflict || state.layoutConflictKind !== "connection") return;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  state.layoutDirty = true;
  state.layoutError = null;
  renderConflict();
  renderCatalogState();
  updateHeader();
  saveLayout();
}

async function flushLayoutBeforeTransition() {
  window.clearTimeout(state.layoutTimer);
  if (state.layoutConflict) {
    showToast("Resolve the layout conflict before leaving this workspace.", { error: true });
    return false;
  }
  if (state.layoutDirty && !state.layoutSaving) await saveLayout();
  if (state.layoutSavePromise) await state.layoutSavePromise.catch(() => null);
  if (state.layoutDirty || state.layoutSaving || state.layoutError) {
    showToast("The current layout could not be saved. Retry or reload it before leaving this workspace.", { error: true });
    return false;
  }
  return true;
}

async function saveLayoutImmediately() {
  if (!state.activeWorkspace || !state.catalog || state.catalogLoading || state.layoutConflict) return;
  window.clearTimeout(state.layoutTimer);
  state.layoutDirty = true;
  state.layoutError = null;
  state.layoutVersion += 1;
  await saveLayout();
  if (!state.layoutDirty && !state.layoutError && !state.layoutConflict) showToast("Layout saved.");
}

function renderCatalogSurfaces() {
  renderCatalogStats(elements.catalogStats, state.catalog);
  const selectedTable = state.catalog?.tables.find(table => table.name === state.selectedTableName) || null;
  if (!selectedTable) state.selectedTableName = null;
  renderActiveInspector(selectedTable);
  if (selectedTable) {
    if (!inspectorPane.available) inspectorPane.reveal();
  } else inspectorPane.setAvailable(false, { reset: true });
  updateDesignControls();
  const selectedView = allViews(state.catalog).find(view => view.name === state.selectedViewName && view.catalogKind === state.selectedViewKind) || null;
  if (!selectedView) {
    state.selectedViewName = null;
    state.selectedViewKind = null;
  }
  if (state.activeLayer === "views") renderViews();
  else {
    replace(elements.viewsList);
    renderViewDetail(elements.viewDetail, null);
  }
  if (elements.functionsDialog.open) renderFunctionsBrowser();
  else {
    replace(elements.functionsList);
    elements.functionsCount.textContent = state.catalog ? `${state.catalog.functions.length} live · open to browse` : "No catalog loaded";
  }
  if (elements.objectsDialog.open) renderObjectsBrowser();
  else {
    replace(elements.objectsList);
    elements.objectsCount.textContent = state.catalog ? "Open to browse live objects" : "No catalog loaded";
  }
  renderSqlTarget();
}

function selectView(view, { historyMode = "push" } = {}) {
  state.selectedViewName = view?.name || null;
  state.selectedViewKind = view?.catalogKind || null;
  renderViews();
  syncWorkspaceNavigation(historyMode);
}

function renderViews() {
  renderViewsList(elements.viewsList, {
    catalog: state.catalog,
    query: elements.viewsSearch.value,
    filter: state.viewFilter,
    selectedName: state.selectedViewName,
    onSelect: selectView,
  });
  const view = allViews(state.catalog).find(item => item.name === state.selectedViewName && item.catalogKind === state.selectedViewKind) || null;
  renderViewDetail(elements.viewDetail, view);
}

function renderFunctionsBrowser() {
  const result = renderFunctions(elements.functionsList, state.catalog, elements.functionsSearch.value);
  const source = state.catalog?.source === "design" ? "designed" : "live";
  elements.functionsCount.textContent = state.catalog ? `${result.shown} shown · ${result.matching} matching · ${state.catalog.functions.length} ${source}` : "No catalog loaded";
}

function renderObjectsBrowser() {
  const result = renderObjects(elements.objectsList, state.catalog, elements.objectsSearch.value, object => {
    if (object.target === "table") {
      elements.objectsDialog.close();
      setLayer("tables", { historyMode: null });
      canvas.focusTable(object.table);
    } else if (object.target === "view") {
      elements.objectsDialog.close();
      setLayer("views", { historyMode: null });
      selectView(object.view);
    } else {
      elements.objectsDialog.close();
      renderFunctionsBrowser();
      openDialog(elements.functionsDialog);
    }
  });
  elements.objectsCount.textContent = state.catalog ? `${result.shown} shown · ${result.matching} matching` : "No catalog loaded";
}

function renderSqlTarget() {
  const workspace = state.activeWorkspace;
  if (isDetachedWorkspace(workspace)) {
    elements.sqlTargetConnection.textContent = "Detached design";
    elements.sqlTargetDatabase.textContent = workspace.name;
    elements.sqlTargetNamespace.textContent = "Desired schema";
    return;
  }
  const connection = workspace ? connectionById(workspace.connectionId) : null;
  elements.sqlTargetConnection.textContent = workspace ? (connection?.name || workspace.connectionId) : "No workspace open";
  elements.sqlTargetDatabase.textContent = workspace?.database || "No workspace open";
  elements.sqlTargetNamespace.textContent = workspace?.namespace || "No workspace open";
}

function appendDesignColumn({ id = null, name = "", dataType = "text", nullable = true, primary = false } = {}) {
  const row = element("div", { className: "design-column-row", dataset: { designColumnId: id || "" } });
  const nameInput = element("input", { attrs: { required: "", maxlength: "63", autocomplete: "off", value: name, placeholder: "column_name" }, dataset: { designColumnName: "" } });
  const typeInput = element("input", { attrs: { required: "", maxlength: "512", autocomplete: "off", value: dataType, placeholder: "text" }, dataset: { designColumnType: "" } });
  const nullableInput = element("input", { type: "checkbox", dataset: { designColumnNullable: "" } });
  nullableInput.checked = nullable && !primary;
  const primaryInput = element("input", { type: "checkbox", dataset: { designColumnPrimary: "" } });
  primaryInput.checked = primary;
  primaryInput.addEventListener("change", () => {
    if (primaryInput.checked) nullableInput.checked = false;
  });
  const remove = element("button", { className: "ui-button compact danger-text design-column-remove", type: "button", text: "Remove", attrs: { "aria-label": "Remove column" } });
  remove.addEventListener("click", () => {
    if (elements.designColumns.childElementCount === 1) {
      showToast("A designed table needs at least one column.");
      return;
    }
    row.remove();
  });
  row.append(
    element("label", {}, [element("span", { text: "Name" }), nameInput]),
    element("label", {}, [element("span", { text: "PostgreSQL type" }), typeInput]),
    element("label", { className: "design-column-check" }, [nullableInput, element("span", { text: "Nullable" })]),
    element("label", { className: "design-column-check" }, [primaryInput, element("span", { text: "Primary" })]),
    remove,
  );
  elements.designColumns.append(row);
  return row;
}

function openDesignTableEditor(tableId = null) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const table = tableId ? state.design.content.tables.find(item => item.id === tableId) : null;
  if (tableId && !table) {
    showToast("The selected table is no longer in this design.", { error: true });
    return;
  }
  state.designTableEditorId = table?.id || null;
  elements.designTableForm.reset();
  replace(elements.designColumns);
  replace(elements.designTableStatus);
  elements.designTableTitle.textContent = table ? `Edit ${table.name}` : "Create table";
  elements.designTableCopy.textContent = table
    ? "Change the desired table while retaining stable table and column identities. Referenced columns must be disconnected before removal."
    : "Add a table and its initial columns. Saving replaces one exact design revision and never contacts PostgreSQL.";
  elements.saveDesignTableButton.textContent = table ? "Save table" : "Create table";
  elements.designTableName.value = table?.name || "";
  if (table) {
    const primaryIds = new Set(table.keys.find(key => key.kind === "primary")?.columnIds || []);
    for (const column of table.columns) appendDesignColumn({
      ...column,
      primary: primaryIds.has(column.id),
    });
  } else {
    appendDesignColumn({ name: "id", dataType: "bigint", nullable: false, primary: true });
    appendDesignColumn({ name: "name", dataType: "text", nullable: false });
  }
  openDialog(elements.designTableDialog);
  elements.designTableName.focus();
}

function designColumnValues() {
  return [...elements.designColumns.children].map(row => ({
    id: row.dataset.designColumnId || null,
    name: row.querySelector("[data-design-column-name]").value,
    dataType: row.querySelector("[data-design-column-type]").value,
    nullable: row.querySelector("[data-design-column-nullable]").checked,
    primary: row.querySelector("[data-design-column-primary]").checked,
  }));
}

function conflictPanel(error) {
  const conflict = error instanceof ApiError && error.code === "design_conflict";
  return errorPanel(error, {
    retryLabel: conflict ? "Reload design" : null,
    onRetry: conflict ? () => loadActiveDesign({ clearConflictOnSuccess: true }) : null,
  });
}

async function replaceActiveDesign(content, { selectedTableName = state.selectedTableName } = {}) {
  const workspaceId = state.activeWorkspace.id;
  const design = await api.replaceDesign(workspaceId, {
    expectedDesignRevision: state.design.revision,
    content,
  });
  const layout = await api.getDesignLayout(workspaceId);
  if (state.activeWorkspace?.id !== workspaceId) return null;
  state.design = design;
  state.designLayout = layout;
  state.catalog = designToCatalog(state.activeWorkspace, design);
  state.selectedTableName = selectedTableName;
  state.catalogError = null;
  state.layoutError = null;
  canvas.setCatalog(state.catalog, designPositions(design, layout));
  renderCatalogSurfaces();
  renderCatalogState();
  return design;
}

async function submitDesignTable(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  const editingId = state.designTableEditorId;
  let table;
  try {
    table = editingId
      ? updateDesignTable(state.design.content, editingId, elements.designTableName.value, designColumnValues())
      : createDesignTable(elements.designTableName.value, designColumnValues());
    if (!editingId && state.design.content.tables.some(item => item.name === table.name)) {
      throw new Error("A table with this name already exists in the design.");
    }
  } catch (error) {
    replace(elements.designTableStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  const content = structuredClone(state.design.content);
  if (editingId) content.tables = content.tables.map(item => item.id === editingId ? table : item);
  else content.tables.push(table);
  state.designSubmitting = true;
  elements.saveDesignTableButton.disabled = true;
  updateDesignControls();
  replace(elements.designTableStatus, element("span", { text: "Validating and saving the desired schema…" }));
  try {
    const design = await replaceActiveDesign(content, { selectedTableName: table.name });
    if (!design) return;
    elements.designTableDialog.close();
    canvas.select(table.name, { notify: true });
    if (!editingId) {
      state.layoutDirty = true;
      state.layoutVersion += 1;
      await saveLayout();
    }
    showToast(`${editingId ? "Updated" : "Created"} ${table.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designTableStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignTableButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignTable() {
  const table = selectedDesignTable();
  if (!table || state.designSubmitting) return;
  const relationships = state.design.content.relationships.filter(item => (
    item.sourceTableId === table.id || item.targetTableId === table.id
  ));
  const relationshipCopy = relationships.length
    ? ` This also removes ${relationships.length} connected relationship${relationships.length === 1 ? "" : "s"}.`
    : "";
  askConfirmation({
    title: "Delete designed table",
    message: `Delete “${table.name}” and its columns from the desired schema?${relationshipCopy}`,
    label: "Delete table",
    callback: async () => {
      if (!await flushLayoutBeforeTransition()) return;
      const content = structuredClone(state.design.content);
      content.tables = content.tables.filter(item => item.id !== table.id);
      content.relationships = content.relationships.filter(item => (
        item.sourceTableId !== table.id && item.targetTableId !== table.id
      ));
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const design = await replaceActiveDesign(content, { selectedTableName: null });
        if (!design) return;
        canvas.clearSelection();
        syncWorkspaceNavigation("replace");
        showToast(`Deleted ${table.name} from design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
}

function designTableById(tableId) {
  return state.design?.content.tables.find(table => table.id === tableId) || null;
}

function keyLabel(table, key) {
  const columns = new Map(table.columns.map(column => [column.id, column.name]));
  const kind = key.kind === "primary" ? "Primary key" : `Unique · ${key.name}`;
  return `${kind} (${key.columnIds.map(columnId => columns.get(columnId)).join(", ")})`;
}

function replaceSelectOptions(select, values, selectedValue = null) {
  replace(select);
  for (const value of values) {
    const option = element("option", { text: value.label, attrs: { value: value.value } });
    if (value.value === selectedValue) option.selected = true;
    select.append(option);
  }
}

function generatedRelationshipName() {
  const source = designTableById(elements.designRelationshipSource.value);
  const target = designTableById(elements.designRelationshipTarget.value);
  if (!source || !target) return "";
  const stem = `${source.name}_${target.name}_fkey`;
  let value = "";
  for (const character of stem) {
    if (new TextEncoder().encode(value + character).length > 63) break;
    value += character;
  }
  return value;
}

function updateGeneratedRelationshipName() {
  const current = elements.designRelationshipName.value;
  const generated = generatedRelationshipName();
  if (!current || current === state.designRelationshipAutoName) elements.designRelationshipName.value = generated;
  state.designRelationshipAutoName = generated;
}

function targetKey() {
  const target = designTableById(elements.designRelationshipTarget.value);
  return target?.keys.find(key => key.id === elements.designRelationshipKey.value) || null;
}

function renderRelationshipMappings() {
  const source = designTableById(elements.designRelationshipSource.value);
  const target = designTableById(elements.designRelationshipTarget.value);
  const key = targetKey();
  const previous = [...elements.designRelationshipMappings.querySelectorAll("[data-relationship-source-column]")]
    .map(select => select.value);
  replace(elements.designRelationshipMappings);
  if (!source || !target || !key) return;
  const targetColumns = new Map(target.columns.map(column => [column.id, column]));
  const used = new Set();
  key.columnIds.forEach((targetColumnId, index) => {
    const targetColumn = targetColumns.get(targetColumnId);
    const preferred = previous[index]
      || source.columns.find(column => column.name === targetColumn.name && !used.has(column.id))?.id
      || source.columns.find(column => !used.has(column.id))?.id
      || source.columns[0]?.id;
    if (preferred) used.add(preferred);
    const select = element("select", {
      attrs: { required: "", "aria-label": `Source column for ${targetColumn.name}` },
      dataset: { relationshipSourceColumn: "" },
    });
    replaceSelectOptions(select, source.columns.map(column => ({ value: column.id, label: `${column.name} · ${column.dataType}` })), preferred);
    const targetValue = element("span", { className: "relationship-mapping-target" }, [
      element("small", { text: "Target" }),
      element("code", { text: `${targetColumn.name} · ${targetColumn.dataType}`, title: `${targetColumn.name} · ${targetColumn.dataType}` }),
    ]);
    elements.designRelationshipMappings.append(element("div", { className: "relationship-mapping-row" }, [
      select,
      element("span", { text: "→", attrs: { "aria-hidden": "true" } }),
      targetValue,
    ]));
  });
}

function updateRelationshipTargetKeys() {
  const target = designTableById(elements.designRelationshipTarget.value);
  const keys = target?.keys.filter(key => key.kind === "primary" || key.kind === "unique") || [];
  replaceSelectOptions(elements.designRelationshipKey, keys.map(key => ({ value: key.id, label: keyLabel(target, key) })));
  renderRelationshipMappings();
  updateGeneratedRelationshipName();
}

function openDesignRelationshipEditor(sourceTableId = null) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const targets = state.design.content.tables.filter(table => table.keys.some(key => key.kind === "primary" || key.kind === "unique"));
  if (!targets.length) {
    showToast("Add a primary or unique key to a target table first.");
    return;
  }
  elements.designRelationshipForm.reset();
  replace(elements.designRelationshipStatus);
  state.designRelationshipAutoName = null;
  const source = designTableById(sourceTableId) || state.design.content.tables[0];
  replaceSelectOptions(elements.designRelationshipSource, state.design.content.tables.map(table => ({ value: table.id, label: table.name })), source?.id);
  const target = targets.find(table => table.id !== source?.id) || targets[0];
  replaceSelectOptions(elements.designRelationshipTarget, targets.map(table => ({ value: table.id, label: table.name })), target?.id);
  updateRelationshipTargetKeys();
  elements.designRelationshipDeferrable.checked = false;
  elements.designRelationshipDeferred.checked = false;
  elements.designRelationshipDeferred.disabled = true;
  openDialog(elements.designRelationshipDialog);
  elements.designRelationshipName.focus();
}

function designRelationshipValues() {
  return {
    name: elements.designRelationshipName.value,
    sourceTableId: elements.designRelationshipSource.value,
    sourceColumnIds: [...elements.designRelationshipMappings.querySelectorAll("[data-relationship-source-column]")].map(select => select.value),
    targetTableId: elements.designRelationshipTarget.value,
    targetKeyId: elements.designRelationshipKey.value,
    onUpdate: elements.designRelationshipOnUpdate.value,
    onDelete: elements.designRelationshipOnDelete.value,
    deferrable: elements.designRelationshipDeferrable.checked,
    initiallyDeferred: elements.designRelationshipDeferred.checked,
  };
}

async function submitDesignRelationship(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  let relationship;
  try {
    relationship = createDesignRelationship(state.design.content, designRelationshipValues());
  } catch (error) {
    replace(elements.designRelationshipStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  const content = structuredClone(state.design.content);
  content.relationships.push(relationship);
  state.designSubmitting = true;
  elements.saveDesignRelationshipButton.disabled = true;
  updateDesignControls();
  replace(elements.designRelationshipStatus, element("span", { text: "Validating and saving the relationship…" }));
  try {
    const design = await replaceActiveDesign(content);
    if (!design) return;
    elements.designRelationshipDialog.close();
    showToast(`Created ${relationship.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designRelationshipStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignRelationshipButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignRelationship(relationship) {
  if (!relationship?.designId || state.designSubmitting) return;
  askConfirmation({
    title: "Delete relationship",
    message: `Delete “${relationship.name}” from the desired schema? The tables and columns remain unchanged.`,
    label: "Delete relationship",
    callback: async () => {
      if (!await flushLayoutBeforeTransition()) return;
      const content = structuredClone(state.design.content);
      content.relationships = content.relationships.filter(item => item.id !== relationship.designId);
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const design = await replaceActiveDesign(content);
        if (!design) return;
        showToast(`Deleted ${relationship.name} from design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
}

function setLayer(layer, { historyMode = "push" } = {}) {
  state.activeLayer = layer;
  for (const button of document.querySelectorAll("[data-layer]")) {
    const active = button.dataset.layer === layer;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
  for (const panel of document.querySelectorAll("[data-layer-panel]")) panel.hidden = panel.dataset.layerPanel !== layer;
  if (layer === "views") renderViews();
  if (layer === "tables") window.requestAnimationFrame(() => canvas.refreshGeometry());
  syncWorkspaceNavigation(historyMode);
}

async function downloadCatalog() {
  closeDetailsMenus();
  if (!state.catalog) {
    showToast("No schema workspace is loaded. Open a workspace first.");
    return;
  }
  if (isDetachedWorkspace()) {
    try {
      const exported = await api.exportDesign(state.activeWorkspace.id, {
        expectedDesignRevision: state.design.revision,
        format: "schemii_json",
      });
      downloadContent(exported.content, exported.fileName, exported.mediaType);
      showToast(`Desired design revision ${exported.designRevision} downloaded.`);
    } catch (error) {
      errorToast(error);
    }
    return;
  }
  const safeTarget = `${state.catalog.database}-${state.catalog.namespace}`.replace(/[^a-zA-Z0-9._-]+/g, "-");
  downloadContent(`${JSON.stringify(state.catalog, null, 2)}\n`, `${safeTarget}-catalog.json`, "application/json");
  showToast("Live catalog JSON downloaded.");
}

async function exportDesignSql() {
  closeDetailsMenus();
  if (!isDetachedWorkspace() || !state.design) return;
  try {
    const exported = await api.exportDesign(state.activeWorkspace.id, {
      expectedDesignRevision: state.design.revision,
      format: "postgresql_sql",
    });
    downloadContent(exported.content, exported.fileName, exported.mediaType);
    showToast(`PostgreSQL SQL for design revision ${exported.designRevision} downloaded.`);
  } catch (error) {
    errorToast(error);
  }
}

function bindEvents() {
  bindUnavailableControls({
    dialog: elements.unavailableDialog,
    title: elements.unavailableTitle,
    description: elements.unavailableDescription,
    identifier: elements.unavailableId,
  });
  assertUnavailableControls();

  document.addEventListener("click", event => {
    const close = event.target.closest("[data-close-dialog]");
    if (close) close.closest("dialog")?.close();
  });
  document.querySelectorAll("[data-confirm-cancel]").forEach(button => button.addEventListener("click", () => {
    state.confirmCallback = null;
    elements.confirmDialog.close();
  }));
  elements.confirmAction.addEventListener("click", async () => {
    if (state.confirmBusy || !state.confirmCallback) return;
    const callback = state.confirmCallback;
    state.confirmCallback = null;
    elements.confirmDialog.close();
    state.confirmBusy = true;
    try {
      await callback();
    } finally {
      state.confirmBusy = false;
    }
  });

  elements.newWorkspaceButton.addEventListener("click", () => {
    openWorkspaces();
    elements.workspaceName.focus();
  });
  elements.connectionsButton.addEventListener("click", openConnections);
  elements.postgresButton.addEventListener("click", openConnections);
  elements.workspacesButton.addEventListener("click", openWorkspaces);
  elements.refreshCatalogButton.addEventListener("click", refreshCatalog);
  elements.saveLayoutButton.addEventListener("click", saveLayoutImmediately);
  elements.downloadCatalogButton.addEventListener("click", downloadCatalog);
  elements.exportDesignSqlButton.addEventListener("click", exportDesignSql);
  elements.createTableButton.addEventListener("click", () => openDesignTableEditor());
  elements.createRelationshipButton.addEventListener("click", () => openDesignRelationshipEditor(selectedDesignTable()?.id || null));
  elements.editTableButton.addEventListener("click", () => {
    const table = selectedDesignTable();
    if (table) openDesignTableEditor(table.id);
  });
  elements.deleteTableButton.addEventListener("click", confirmDeleteDesignTable);
  elements.introductionButton.addEventListener("click", () => {
    closeDetailsMenus();
    openDialog(elements.introductionDialog);
  });
  elements.fitButton.addEventListener("click", () => {
    if (!canvas.fit()) showToast("No live tables are available to fit.");
    else scheduleCanvasViewPersistence();
  });
  elements.zoomInButton.addEventListener("click", () => canvas.zoomBy(0.1));
  elements.zoomOutButton.addEventListener("click", () => canvas.zoomBy(-0.1));
  elements.zoomInButton.addEventListener("click", scheduleCanvasViewPersistence);
  elements.zoomOutButton.addEventListener("click", scheduleCanvasViewPersistence);
  for (const type of ["pointerup", "pointercancel", "lostpointercapture", "wheel"]) {
    elements.canvas.addEventListener(type, scheduleCanvasViewPersistence);
  }
  elements.applyConnectionLayoutButton.addEventListener("click", applyLayoutToCurrentConnection);
  elements.reloadConflictButton.addEventListener("click", reloadConflict);
  document.querySelectorAll("[data-layer]").forEach(button => button.addEventListener("click", () => setLayer(button.dataset.layer)));

  elements.reloadConnectionsButton.addEventListener("click", loadConnections);
  elements.addConnectionButton.addEventListener("click", () => openConnectionEditor());
  elements.connectionForm.addEventListener("submit", submitConnection);
  elements.reloadEditorConnection.addEventListener("click", reloadEditorConnection);
  elements.connectionEditorDialog.addEventListener("close", () => {
    state.connectionEditorGeneration += 1;
    elements.connectionPassword.value = "";
    elements.removeCredential.checked = false;
  });
  elements.connectionPassword.addEventListener("input", () => {
    if (elements.connectionPassword.value) elements.removeCredential.checked = false;
  });
  elements.removeCredential.addEventListener("change", () => {
    if (elements.removeCredential.checked) elements.connectionPassword.value = "";
  });

  elements.reloadWorkspacesButton.addEventListener("click", loadWorkspaces);
  elements.workspaceConnection.addEventListener("change", updateWorkspaceDatabase);
  elements.workspaceMode.addEventListener("change", updateWorkspaceMode);
  elements.workspaceForm.addEventListener("submit", submitWorkspace);
  elements.workspacesDialog.addEventListener("close", () => {
    state.workspaceDialogGeneration += 1;
  });

  elements.addDesignColumnButton.addEventListener("click", () => appendDesignColumn());
  elements.designTableForm.addEventListener("submit", submitDesignTable);
  elements.designTableDialog.addEventListener("close", () => {
    state.designTableEditorId = null;
  });
  elements.designRelationshipForm.addEventListener("submit", submitDesignRelationship);
  elements.designRelationshipSource.addEventListener("change", () => {
    renderRelationshipMappings();
    updateGeneratedRelationshipName();
  });
  elements.designRelationshipTarget.addEventListener("change", updateRelationshipTargetKeys);
  elements.designRelationshipKey.addEventListener("change", renderRelationshipMappings);
  elements.designRelationshipDeferrable.addEventListener("change", () => {
    elements.designRelationshipDeferred.disabled = !elements.designRelationshipDeferrable.checked;
    if (elements.designRelationshipDeferred.disabled) elements.designRelationshipDeferred.checked = false;
  });

  elements.functionsButton.addEventListener("click", () => {
    renderFunctionsBrowser();
    openDialog(elements.functionsDialog);
  });
  elements.functionsSearch.addEventListener("input", renderFunctionsBrowser);
  elements.objectsButton.addEventListener("click", () => {
    renderObjectsBrowser();
    openDialog(elements.objectsDialog);
  });
  elements.objectsSearch.addEventListener("input", renderObjectsBrowser);

  elements.viewsSearch.addEventListener("input", renderViews);
  elements.refreshViewsButton.addEventListener("click", refreshCatalog);
  document.querySelectorAll("[data-view-filter]").forEach(button => button.addEventListener("click", () => {
    state.viewFilter = button.dataset.viewFilter;
    document.querySelectorAll("[data-view-filter]").forEach(item => {
      const active = item.dataset.viewFilter === state.viewFilter;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", active ? "true" : "false");
    });
    renderViews();
  }));
  elements.newSqlDraftButton.addEventListener("click", () => {
    if (elements.sqlDraft.value) {
      elements.sqlDraft.value = "";
      showToast("Unsaved SQL draft cleared.");
    } else {
      showToast("The SQL draft is already empty.");
    }
  });

  window.addEventListener("resize", () => {
    if (state.activeLayer !== "tables" || state.canvasResizeFrame !== null) return;
    state.canvasResizeFrame = window.requestAnimationFrame(() => {
      state.canvasResizeFrame = null;
      canvas.refreshGeometry();
    });
  });
  window.addEventListener("pagehide", persistCanvasView);
  window.addEventListener("popstate", () => {
    restoreWorkspaceNavigation(readWorkspaceNavigation(window.location.href)).catch(errorToast);
  });
}

bindEvents();
renderCatalogSurfaces();
renderCatalogState();
bootstrap();
