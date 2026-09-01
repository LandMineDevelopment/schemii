import { api, ApiError } from "./api.js";
import { CatalogCanvas } from "./canvas.js";
import {
  alignRelationshipColumnTypes,
  createDesignRelationship,
  createDesignTable,
  deleteDesignView,
  deleteDesignCheck,
  deleteDesignIndex,
  deleteDesignKey,
  designLayoutContent,
  designPositions,
  designToCatalog,
  relationshipDraftFromColumns,
  relationshipDraftFromExisting,
  expressionColumnIds,
  saveDesignCheck,
  saveDesignIndex,
  saveDesignKey,
  saveDesignView,
  suggestDesignCheckName,
  suggestDesignIndexName,
  suggestDesignKeyName,
  updateDesignRelationship,
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
import { installSortableList, reorderedValues } from "/assets/common/sortable.js";
import { assertUnavailableControls, bindUnavailableControls } from "./unavailable.js";
import { closeDetailsMenus, createIconButton, createStatePanel, DockPane, downloadContent, initializeUi } from "./ui.js";
import {
  readWorkspaceNavigation,
  readWorkspacePreferences,
  updateWorkspacePreferences,
  workspaceNavigationHref,
} from "./workspace-navigation.js";
import { renderDesignViewStory } from "./view-story.js";

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
  relationshipAuthoringBanner: byId("relationship-authoring-banner"),
  relationshipAuthoringStep: byId("relationship-authoring-step"),
  relationshipAuthoringInstruction: byId("relationship-authoring-instruction"),
  cancelRelationshipAuthoring: byId("cancel-relationship-authoring"),
  reviewKeyAuthoring: byId("review-key-authoring"),
  fitButton: byId("fit-button"),
  createTableButton: byId("create-table-button"),
  createRelationshipButton: byId("create-relationship-button"),
  createKeyButton: byId("create-key-button"),
  createIndexButton: byId("create-index-button"),
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
  createViewButton: byId("create-view-button"),
  viewsSourceLabel: byId("views-source-label"),
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
  designViewDialog: byId("design-view-dialog"),
  designViewForm: byId("design-view-form"),
  designViewTitle: byId("design-view-title"),
  designViewCopy: byId("design-view-copy"),
  designViewName: byId("design-view-name"),
  designViewKind: byId("design-view-kind"),
  designViewPopulationRow: byId("design-view-population-row"),
  designViewPopulate: byId("design-view-populate"),
  designViewDefinition: byId("design-view-definition"),
  designViewPreview: byId("design-view-preview"),
  designViewStatus: byId("design-view-status"),
  saveDesignViewButton: byId("save-design-view-button"),
  generatedExpressionHelpDialog: byId("generated-expression-help-dialog"),
  designKeyDialog: byId("design-key-dialog"),
  designKeyForm: byId("design-key-form"),
  designKeyTitle: byId("design-key-title"),
  designKeyTable: byId("design-key-table"),
  designKeyKind: byId("design-key-kind"),
  designKeyName: byId("design-key-name"),
  designKeyColumns: byId("design-key-columns"),
  designKeyStatus: byId("design-key-status"),
  saveDesignKeyButton: byId("save-design-key-button"),
  designCheckDialog: byId("design-check-dialog"),
  designCheckForm: byId("design-check-form"),
  designCheckTitle: byId("design-check-title"),
  designCheckTable: byId("design-check-table"),
  designCheckName: byId("design-check-name"),
  designCheckExpression: byId("design-check-expression"),
  designCheckDependencies: byId("design-check-dependencies"),
  designCheckStatus: byId("design-check-status"),
  saveDesignCheckButton: byId("save-design-check-button"),
  designIndexDialog: byId("design-index-dialog"),
  designIndexForm: byId("design-index-form"),
  designIndexTitle: byId("design-index-title"),
  designIndexTable: byId("design-index-table"),
  designIndexName: byId("design-index-name"),
  designIndexMethod: byId("design-index-method"),
  designIndexUnique: byId("design-index-unique"),
  designIndexColumns: byId("design-index-columns"),
  designIndexExpression: byId("design-index-expression"),
  designIndexExpressionDependencies: byId("design-index-expression-dependencies"),
  designIndexPredicate: byId("design-index-predicate"),
  designIndexPredicateDependencies: byId("design-index-predicate-dependencies"),
  designIndexStatus: byId("design-index-status"),
  saveDesignIndexButton: byId("save-design-index-button"),
  designRelationshipDialog: byId("design-relationship-dialog"),
  designRelationshipForm: byId("design-relationship-form"),
  designRelationshipTitle: byId("design-relationship-title"),
  designRelationshipCopy: byId("design-relationship-copy"),
  designRelationshipName: byId("design-relationship-name"),
  designRelationshipSource: byId("design-relationship-source"),
  designRelationshipTarget: byId("design-relationship-target"),
  designRelationshipKey: byId("design-relationship-key"),
  designRelationshipMappings: byId("design-relationship-mappings"),
  reselectDesignRelationship: byId("reselect-design-relationship"),
  designRelationshipTypeAlignment: byId("design-relationship-type-alignment"),
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

let designColumnDraftSequence = 0;

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
  designRelationshipEditorId: null,
  relationshipAuthoring: false,
  relationshipSource: null,
  relationshipAuthoringEditId: null,
  relationshipAuthoringDefaults: null,
  designRelationshipAnchor: null,
  keyAuthoring: false,
  keySelection: null,
  designKeyEditorId: null,
  designKeyTableId: null,
  designKeyColumnIds: [],
  designKeyAutoName: null,
  designCheckEditorId: null,
  designCheckTableId: null,
  designCheckAutoName: null,
  indexAuthoring: false,
  indexSelection: null,
  designIndexEditorId: null,
  designIndexTableId: null,
  designIndexColumnIds: [],
  designIndexAutoName: null,
  catalog: null,
  catalogLoading: false,
  catalogError: null,
  catalogGeneration: 0,
  selectedTableName: null,
  selectedViewName: null,
  selectedViewKind: null,
  selectedViewOutputOrdinal: null,
  viewAnalysisCache: new Map(),
  viewAnalysisLoadingKey: null,
  viewAnalysisError: null,
  viewAnalysisErrorKey: null,
  viewAnalysisGeneration: 0,
  designViewEditorId: null,
  designViewPreviewAnalysis: null,
  designViewPreviewError: null,
  designViewPreviewLoading: false,
  designViewPreviewOutputOrdinal: null,
  designViewPreviewTimer: null,
  designViewPreviewGeneration: 0,
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
  onRelationshipColumnSelect: handleRelationshipColumnSelection,
  onKeyColumnSelect: handleKeyColumnSelection,
  onIndexColumnSelect: handleIndexColumnSelection,
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
  const busy = state.catalogLoading || state.designSubmitting || state.layoutConflict;
  const selected = selectedDesignTable();
  const hasTargetKey = state.design?.content.tables.some(table => (
    table.keys.some(key => key.kind === "primary" || key.kind === "unique")
  ));
  elements.createTableButton.disabled = !detached || busy;
  elements.createRelationshipButton.disabled = !detached || busy || !hasTargetKey;
  elements.createRelationshipButton.classList.toggle("active", state.relationshipAuthoring);
  elements.createRelationshipButton.setAttribute("aria-pressed", state.relationshipAuthoring ? "true" : "false");
  elements.createRelationshipButton.title = state.relationshipAuthoring ? "Cancel relationship selection" : "Create relationship";
  elements.createKeyButton.disabled = !detached || busy || !state.design?.content.tables.length;
  elements.createKeyButton.classList.toggle("active", state.keyAuthoring);
  elements.createKeyButton.setAttribute("aria-pressed", state.keyAuthoring ? "true" : "false");
  elements.createKeyButton.title = state.keyAuthoring ? "Cancel key selection" : "Create primary or unique key";
  elements.createIndexButton.disabled = !detached || busy || !selected;
  elements.createIndexButton.classList.toggle("active", state.indexAuthoring);
  elements.createIndexButton.setAttribute("aria-pressed", state.indexAuthoring ? "true" : "false");
  elements.createIndexButton.title = state.indexAuthoring ? "Cancel index selection" : "Create index on selected table";
  elements.editTableButton.disabled = !selected || busy;
  elements.deleteTableButton.disabled = !selected || busy;
  elements.createViewButton.disabled = !detached || busy;
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
  elements.createViewButton.disabled = !detached || state.catalogLoading || state.designSubmitting || state.layoutConflict;
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
  cancelColumnAuthoring();
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
  cancelColumnAuthoring();
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
  cancelColumnAuthoring();
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
    onAddKey: desired && table ? () => startKeyAuthoring({ tableId: table.designId }) : null,
    onEditKey: desired ? editDesignKey : null,
    onDeleteKey: desired ? confirmDeleteDesignKey : null,
    onAddCheck: desired && table ? () => openDesignCheckEditor({ tableId: table.designId }) : null,
    onEditCheck: desired ? editDesignCheck : null,
    onDeleteCheck: desired ? confirmDeleteDesignCheck : null,
    onAddIndex: desired && table ? () => startIndexAuthoring({ tableId: table.designId }) : null,
    onEditIndex: desired ? editDesignIndex : null,
    onDeleteIndex: desired ? confirmDeleteDesignIndex : null,
    onAddRelationship: desired && table ? () => startRelationshipAuthoring() : null,
    onEditRelationship: desired ? editDesignRelationship : null,
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
  state.selectedViewOutputOrdinal = null;
  state.viewAnalysisError = null;
  state.viewAnalysisErrorKey = null;
  renderViews();
  syncWorkspaceNavigation(historyMode);
}

function viewAnalysisKey(view) {
  return `${state.activeWorkspace?.id || "none"}:${view.designId || view.name}:${view.queryDefinition}`;
}

async function analyzeSelectedDesignView(view, { force = false } = {}) {
  if (!isDetachedWorkspace() || !state.design || !view?.designId) return;
  const key = viewAnalysisKey(view);
  if (!force && (state.viewAnalysisCache.has(key) || state.viewAnalysisLoadingKey === key)) return;
  const generation = ++state.viewAnalysisGeneration;
  const workspaceId = state.activeWorkspace.id;
  state.viewAnalysisLoadingKey = key;
  state.viewAnalysisError = null;
  state.viewAnalysisErrorKey = null;
  renderViews();
  try {
    const analysis = await api.analyzeDesignView(workspaceId, {
      viewId: view.designId,
      name: view.name,
      definition: view.queryDefinition,
    });
    if (generation !== state.viewAnalysisGeneration || state.activeWorkspace?.id !== workspaceId) return;
    state.viewAnalysisCache.set(key, analysis);
    if (!state.selectedViewOutputOrdinal && analysis.outputs.length) {
      state.selectedViewOutputOrdinal = analysis.outputs[0].ordinal;
    }
  } catch (error) {
    if (generation !== state.viewAnalysisGeneration) return;
    state.viewAnalysisError = error;
    state.viewAnalysisErrorKey = key;
  } finally {
    if (generation === state.viewAnalysisGeneration) {
      state.viewAnalysisLoadingKey = null;
      renderViews();
    }
  }
}

function renderViews() {
  const desired = state.catalog?.source === "design";
  elements.viewsSourceLabel.textContent = desired ? "Desired schema" : "Live PostgreSQL";
  elements.createViewButton.hidden = !desired;
  renderViewsList(elements.viewsList, {
    catalog: state.catalog,
    query: elements.viewsSearch.value,
    filter: state.viewFilter,
    selectedName: state.selectedViewName,
    onSelect: selectView,
  });
  const view = allViews(state.catalog).find(item => item.name === state.selectedViewName && item.catalogKind === state.selectedViewKind) || null;
  if (!desired) {
    renderViewDetail(elements.viewDetail, view);
    return;
  }
  if (!view) {
    renderDesignViewStory(elements.viewDetail, { view: null });
    return;
  }
  const key = viewAnalysisKey(view);
  const analysis = state.viewAnalysisCache.get(key) || null;
  renderDesignViewStory(elements.viewDetail, {
    view,
    analysis,
    loading: state.viewAnalysisLoadingKey === key,
    error: state.viewAnalysisErrorKey === key ? state.viewAnalysisError : null,
    selectedOutputOrdinal: state.selectedViewOutputOrdinal,
    onSelectOutput: output => {
      state.selectedViewOutputOrdinal = output.ordinal;
      renderViews();
    },
    onEdit: () => openDesignViewEditor(view.designId),
    onDelete: () => confirmDeleteDesignView(view.designId),
    onRetry: () => analyzeSelectedDesignView(view, { force: true }),
  });
  if (!analysis && state.viewAnalysisLoadingKey !== key && state.viewAnalysisErrorKey !== key) {
    void analyzeSelectedDesignView(view);
  }
}

function selectedDesignView() {
  if (!isDetachedWorkspace() || !state.design || !state.selectedViewName) return null;
  return state.design.content.views.find(view => (
    view.name === state.selectedViewName && view.kind === state.selectedViewKind
  )) || null;
}

function quotedSqlIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function designViewDraft() {
  return {
    designId: state.designViewEditorId,
    namespace: "desired",
    name: elements.designViewName.value.trim() || "new_view",
    catalogKind: elements.designViewKind.value,
    queryDefinition: elements.designViewDefinition.value,
    populateOnCreate: elements.designViewKind.value === "materialized_view"
      ? elements.designViewPopulate.checked
      : null,
  };
}

function renderDesignViewPreview() {
  renderDesignViewStory(elements.designViewPreview, {
    view: designViewDraft(),
    analysis: state.designViewPreviewAnalysis,
    loading: state.designViewPreviewLoading,
    error: state.designViewPreviewError,
    selectedOutputOrdinal: state.designViewPreviewOutputOrdinal,
    onSelectOutput: output => {
      state.designViewPreviewOutputOrdinal = output.ordinal;
      renderDesignViewPreview();
    },
    compact: true,
  });
}

async function analyzeDesignViewDraft() {
  window.clearTimeout(state.designViewPreviewTimer);
  const definition = elements.designViewDefinition.value.trim();
  if (!elements.designViewDialog.open || !definition) {
    state.designViewPreviewLoading = false;
    state.designViewPreviewAnalysis = null;
    state.designViewPreviewError = null;
    renderDesignViewPreview();
    return;
  }
  const generation = ++state.designViewPreviewGeneration;
  const workspaceId = state.activeWorkspace.id;
  state.designViewPreviewLoading = true;
  state.designViewPreviewError = null;
  renderDesignViewPreview();
  try {
    const analysis = await api.analyzeDesignView(workspaceId, {
      viewId: state.designViewEditorId,
      name: elements.designViewName.value.trim() || "new_view",
      definition,
    });
    if (generation !== state.designViewPreviewGeneration || !elements.designViewDialog.open) return;
    state.designViewPreviewAnalysis = analysis;
    state.designViewPreviewOutputOrdinal = analysis.outputs[0]?.ordinal || null;
  } catch (error) {
    if (generation !== state.designViewPreviewGeneration || !elements.designViewDialog.open) return;
    state.designViewPreviewAnalysis = null;
    state.designViewPreviewError = error;
  } finally {
    if (generation === state.designViewPreviewGeneration) {
      state.designViewPreviewLoading = false;
      renderDesignViewPreview();
    }
  }
}

function scheduleDesignViewPreview(delay = 280) {
  window.clearTimeout(state.designViewPreviewTimer);
  state.designViewPreviewTimer = window.setTimeout(analyzeDesignViewDraft, delay);
}

function updateDesignViewPopulation() {
  elements.designViewPopulationRow.hidden = elements.designViewKind.value !== "materialized_view";
  renderDesignViewPreview();
}

function openDesignViewEditor(viewId = null) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const view = viewId ? state.design.content.views.find(item => item.id === viewId) : null;
  if (viewId && !view) {
    showToast("The selected view is no longer in this design.", { error: true });
    return;
  }
  state.designViewEditorId = view?.id || null;
  state.designViewPreviewGeneration += 1;
  state.designViewPreviewAnalysis = null;
  state.designViewPreviewError = null;
  state.designViewPreviewLoading = false;
  state.designViewPreviewOutputOrdinal = null;
  elements.designViewForm.reset();
  replace(elements.designViewStatus);
  elements.designViewTitle.textContent = view ? `Edit ${view.name}` : "Create view";
  elements.designViewCopy.textContent = view
    ? "Change the query and Schemii will re-derive its relational meaning before anything is saved."
    : "Write one SELECT query. Schemii derives its result grain, relations, rules, and column lineage without contacting PostgreSQL.";
  elements.saveDesignViewButton.textContent = view ? "Save view" : "Create view";
  elements.designViewName.value = view?.name || "";
  elements.designViewKind.value = view?.kind || "view";
  elements.designViewPopulate.checked = view?.populateOnCreate !== false;
  const firstTable = state.design.content.tables[0];
  elements.designViewDefinition.value = view?.definition || (firstTable
    ? `SELECT\n    *\nFROM ${quotedSqlIdentifier(firstTable.name)}`
    : "SELECT\n    1 AS example");
  updateDesignViewPopulation();
  openDialog(elements.designViewDialog);
  renderDesignViewPreview();
  scheduleDesignViewPreview(0);
  elements.designViewName.focus();
}

async function submitDesignView(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  const editing = Boolean(state.designViewEditorId);
  let result;
  try {
    result = saveDesignView(state.design.content, {
      viewId: state.designViewEditorId,
      name: elements.designViewName.value,
      kind: elements.designViewKind.value,
      populateOnCreate: elements.designViewPopulate.checked,
      definition: elements.designViewDefinition.value,
    });
  } catch (error) {
    replace(elements.designViewStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  state.designSubmitting = true;
  elements.saveDesignViewButton.disabled = true;
  updateDesignControls();
  replace(elements.designViewStatus, element("span", { text: "Validating the query and saving the desired view…" }));
  try {
    state.viewAnalysisCache.clear();
    const design = await replaceActiveDesign(result.content, {
      selectedViewName: result.view.name,
      selectedViewKind: result.view.kind,
    });
    if (!design) return;
    elements.designViewDialog.close();
    state.selectedViewOutputOrdinal = null;
    syncWorkspaceNavigation("replace");
    showToast(`${editing ? "Updated" : "Created"} ${result.view.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designViewStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignViewButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignView(viewId) {
  const view = state.design?.content.views.find(item => item.id === viewId);
  if (!view || state.designSubmitting) return;
  const catalogView = allViews(state.catalog).find(item => item.designId === view.id);
  const analysis = catalogView ? state.viewAnalysisCache.get(viewAnalysisKey(catalogView)) : null;
  const consumerCopy = analysis?.consumers.length
    ? ` ${analysis.consumers.length} other designed view${analysis.consumers.length === 1 ? "" : "s"} currently reference it and will become unresolved.`
    : "";
  askConfirmation({
    title: "Delete designed view",
    message: `Delete “${view.name}” from the desired schema?${consumerCopy}`,
    label: "Delete view",
    callback: async () => {
      if (!await flushLayoutBeforeTransition()) return;
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const result = deleteDesignView(state.design.content, view.id);
        state.viewAnalysisCache.clear();
        const design = await replaceActiveDesign(result.content, {
          selectedViewName: null,
          selectedViewKind: null,
        });
        if (!design) return;
        state.selectedViewOutputOrdinal = null;
        syncWorkspaceNavigation("replace");
        showToast(`Deleted ${view.name} from design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
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

function appendDesignColumn({
  id = null,
  name = "",
  dataType = "text",
  nullable = true,
  primary = false,
  defaultExpression = null,
  identity = null,
  generatedExpression = null,
} = {}) {
  designColumnDraftSequence += 1;
  const row = element("div", {
    className: "design-column-row",
    dataset: {
      designColumnId: id || "",
      sortKey: id || `draft-column-${designColumnDraftSequence}`,
    },
  });
  const sortHandle = createIconButton({
    icon: "drag",
    label: `Reorder ${name || "new column"}`,
    tooltip: `Drag to reorder ${name || "new column"}`,
    className: "compact design-sort-handle",
  });
  sortHandle.dataset.sortHandle = "";
  const nameInput = element("input", { attrs: { required: "", maxlength: "63", autocomplete: "off", value: name, placeholder: "column_name" }, dataset: { designColumnName: "" } });
  const typeInput = element("input", { attrs: { required: "", maxlength: "512", autocomplete: "off", value: dataType, placeholder: "text" }, dataset: { designColumnType: "" } });
  const nullableInput = element("input", { type: "checkbox", dataset: { designColumnNullable: "" } });
  nullableInput.checked = nullable && !primary;
  const primaryInput = element("input", { type: "checkbox", dataset: { designColumnPrimary: "" } });
  primaryInput.checked = primary;
  const behaviorSelect = element("select", { dataset: { designColumnBehavior: "" }, attrs: { "aria-label": `Value behavior for ${name || "column"}` } });
  const behaviorOptions = [
    ["none", "Entered by the application"],
    ["default", "Default expression"],
    ["identity_by_default", "Identity · by default"],
    ["identity_always", "Identity · always"],
    ["generated", "Generated from columns"],
  ];
  for (const [value, label] of behaviorOptions) behaviorSelect.append(element("option", { text: label, attrs: { value } }));
  behaviorSelect.value = generatedExpression
    ? "generated"
    : identity === "always"
      ? "identity_always"
      : identity === "by_default"
        ? "identity_by_default"
        : defaultExpression
          ? "default"
          : "none";
  const expressionInput = element("input", {
    attrs: { maxlength: "262144", autocomplete: "off", value: generatedExpression || defaultExpression || "" },
    dataset: { designColumnExpression: "" },
  });
  const expressionTitle = element("span");
  const expressionHelp = createIconButton({
    icon: "info",
    label: "Allowed calculated-column expression syntax",
    tooltip: "Allowed calculated-column expression syntax",
    className: "compact",
  });
  expressionHelp.addEventListener("click", () => openDialog(elements.generatedExpressionHelpDialog));
  const expressionHeading = element("span", { className: "design-expression-heading" }, [expressionTitle, expressionHelp]);
  const expressionField = element("div", { className: "design-expression-field" }, [expressionHeading, expressionInput]);
  const syncBehavior = () => {
    const generated = behaviorSelect.value === "generated";
    const defaulted = behaviorSelect.value === "default";
    const identityBehavior = behaviorSelect.value.startsWith("identity_");
    expressionField.hidden = !generated && !defaulted;
    expressionTitle.textContent = generated ? "Generation expression" : "Default expression";
    expressionHelp.hidden = !generated;
    expressionInput.placeholder = generated ? "quantity * unit_price" : "now()";
    expressionInput.setAttribute("aria-label", generated ? "Generation expression" : "Default expression");
    expressionInput.required = generated || defaulted;
    nullableInput.disabled = primaryInput.checked || identityBehavior;
    if (nullableInput.disabled) nullableInput.checked = false;
  };
  primaryInput.addEventListener("change", syncBehavior);
  behaviorSelect.addEventListener("change", syncBehavior);
  nameInput.addEventListener("input", () => designTableColumnSorter.refresh());
  const remove = element("button", { className: "ui-button compact danger-text design-column-remove", type: "button", text: "Remove", attrs: { "aria-label": "Remove column" } });
  remove.addEventListener("click", () => {
    if (elements.designColumns.childElementCount === 1) {
      showToast("A designed table needs at least one column.");
      return;
    }
    row.remove();
    designTableColumnSorter.refresh();
  });
  row.append(
    sortHandle,
    element("label", { className: "design-column-name" }, [element("span", { text: "Name" }), nameInput]),
    element("label", { className: "design-column-type" }, [element("span", { text: "PostgreSQL type" }), typeInput]),
    element("label", { className: "design-column-check design-column-nullable" }, [nullableInput, element("span", { text: "Nullable" })]),
    element("label", { className: "design-column-check design-column-primary" }, [primaryInput, element("span", { text: "Primary" })]),
    remove,
    element("div", { className: "design-column-value" }, [
      element("label", {}, [element("span", { text: "Value behavior" }), behaviorSelect]),
      expressionField,
    ]),
  );
  syncBehavior();
  elements.designColumns.append(row);
  designTableColumnSorter.refresh();
  return row;
}

function openDesignTableEditor(tableId = null) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  cancelColumnAuthoring();
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
  return [...elements.designColumns.children].map(row => {
    const behavior = row.querySelector("[data-design-column-behavior]").value;
    const expression = row.querySelector("[data-design-column-expression]").value;
    return {
      id: row.dataset.designColumnId || null,
      name: row.querySelector("[data-design-column-name]").value,
      dataType: row.querySelector("[data-design-column-type]").value,
      nullable: row.querySelector("[data-design-column-nullable]").checked,
      primary: row.querySelector("[data-design-column-primary]").checked,
      defaultExpression: behavior === "default" ? expression : null,
      identity: behavior === "identity_always" ? "always" : behavior === "identity_by_default" ? "by_default" : null,
      generatedExpression: behavior === "generated" ? expression : null,
    };
  });
}

function conflictPanel(error) {
  const conflict = error instanceof ApiError && error.code === "design_conflict";
  return errorPanel(error, {
    retryLabel: conflict ? "Reload design" : null,
    onRetry: conflict ? () => loadActiveDesign({ clearConflictOnSuccess: true }) : null,
  });
}

async function replaceActiveDesign(content, {
  selectedTableName = state.selectedTableName,
  selectedViewName = state.selectedViewName,
  selectedViewKind = state.selectedViewKind,
} = {}) {
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
  state.selectedViewName = selectedViewName;
  state.selectedViewKind = selectedViewKind;
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

function designKeyById(tableId, keyId) {
  return designTableById(tableId)?.keys.find(key => key.id === keyId) || null;
}

function designCheckById(tableId, checkId) {
  return designTableById(tableId)?.checks.find(check => check.id === checkId) || null;
}

function designIndexById(tableId, indexId) {
  return designTableById(tableId)?.indexes.find(index => index.id === indexId) || null;
}

function designRelationshipById(relationshipId) {
  return state.design?.content.relationships.find(relationship => relationship.id === relationshipId) || null;
}

function updateKeyAuthoringPresentation() {
  const selection = state.keySelection;
  const table = designTableById(selection?.tableId);
  const columnNames = new Map((table?.columns || []).map(column => [column.id, column.name]));
  const selectedNames = (selection?.columnIds || []).map(columnId => columnNames.get(columnId)).filter(Boolean);
  elements.relationshipAuthoringStep.textContent = String(Math.max(1, selectedNames.length));
  elements.relationshipAuthoringInstruction.textContent = table
    ? selectedNames.length
      ? `${table.name}: ${selectedNames.join(" → ")}`
      : `Select key columns on ${table.name}`
    : "Select the first key column";
  elements.reviewKeyAuthoring.hidden = !selectedNames.length;
  elements.reviewKeyAuthoring.textContent = `Review key (${selectedNames.length})`;
  canvas.setKeyMode({
    enabled: state.keyAuthoring,
    tableName: table?.name || null,
    columnNames: selectedNames,
  });
}

function setKeyAuthoring(enabled, { tableId = null, keyId = null, columnIds = null } = {}) {
  const active = Boolean(enabled && isDetachedWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
  if (active && state.relationshipAuthoring) setRelationshipAuthoring(false);
  if (active && state.indexAuthoring) setIndexAuthoring(false);
  state.keyAuthoring = active;
  if (active) {
    const table = tableId ? designTableById(tableId) : null;
    const key = table && keyId ? designKeyById(table.id, keyId) : null;
    state.keySelection = {
      tableId: table?.id || null,
      keyId: key?.id || null,
      columnIds: [...(columnIds || key?.columnIds || [])],
    };
  } else {
    state.keySelection = null;
  }
  elements.relationshipAuthoringBanner.hidden = !active;
  if (active) updateKeyAuthoringPresentation();
  else {
    elements.reviewKeyAuthoring.hidden = true;
    canvas.setKeyMode({ enabled: false });
  }
  updateDesignControls();
}

function cancelColumnAuthoring() {
  setRelationshipAuthoring(false);
  setKeyAuthoring(false);
  setIndexAuthoring(false);
}

function startKeyAuthoring({ tableId = null, keyId = null } = {}) {
  if (state.keyAuthoring) return;
  if (!state.design?.content.tables.length) {
    showToast("Add a table before creating a key.");
    return;
  }
  if (keyId && !designKeyById(tableId, keyId)) {
    showToast("The selected key is no longer in this design.", { error: true });
    return;
  }
  setKeyAuthoring(true, { tableId, keyId });
}

function toggleKeyAuthoring() {
  if (state.keyAuthoring) setKeyAuthoring(false);
  else startKeyAuthoring();
}

function handleKeyColumnSelection(table, column) {
  if (!state.keyAuthoring || !table.designId || !column.designId) return;
  const selection = state.keySelection || { tableId: null, keyId: null, columnIds: [] };
  if (selection.tableId && selection.tableId !== table.designId) {
    showToast("All key columns must come from the same table.");
    return;
  }
  const columnIds = [...selection.columnIds];
  const existingIndex = columnIds.indexOf(column.designId);
  if (existingIndex >= 0) columnIds.splice(existingIndex, 1);
  else columnIds.push(column.designId);
  state.keySelection = {
    tableId: selection.tableId || table.designId,
    keyId: selection.keyId,
    columnIds,
  };
  updateKeyAuthoringPresentation();
}

function reviewKeyAuthoring() {
  if (!state.keyAuthoring || !state.keySelection?.columnIds.length) return;
  const draft = {
    tableId: state.keySelection.tableId,
    keyId: state.keySelection.keyId,
    columnIds: [...state.keySelection.columnIds],
  };
  setKeyAuthoring(false);
  openDesignKeyEditor(draft);
}

function updateIndexAuthoringPresentation() {
  const selection = state.indexSelection;
  const table = designTableById(selection?.tableId);
  const columnNames = new Map((table?.columns || []).map(column => [column.id, column.name]));
  const selectedNames = (selection?.columnIds || []).map(columnId => columnNames.get(columnId)).filter(Boolean);
  elements.relationshipAuthoringStep.textContent = String(Math.max(1, selectedNames.length));
  elements.relationshipAuthoringInstruction.textContent = selectedNames.length
    ? `${table.name}: ${selectedNames.join(" → ")}`
    : `Select ordered columns on ${table.name}, or configure an expression index`;
  elements.reviewKeyAuthoring.hidden = false;
  elements.reviewKeyAuthoring.textContent = selectedNames.length
    ? `Configure index (${selectedNames.length})`
    : "Configure expression index";
  canvas.setIndexMode({
    enabled: state.indexAuthoring,
    tableName: table?.name || null,
    columnNames: selectedNames,
  });
}

function setIndexAuthoring(enabled, { tableId = null, indexId = null, columnIds = null } = {}) {
  const active = Boolean(enabled && isDetachedWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
  if (active && state.relationshipAuthoring) setRelationshipAuthoring(false);
  if (active && state.keyAuthoring) setKeyAuthoring(false);
  state.indexAuthoring = active;
  if (active) {
    const table = designTableById(tableId);
    const index = table && indexId ? designIndexById(table.id, indexId) : null;
    state.indexSelection = {
      tableId: table?.id || null,
      indexId: index?.id || null,
      columnIds: [...(columnIds || index?.columnIds || [])],
    };
  } else {
    state.indexSelection = null;
  }
  elements.relationshipAuthoringBanner.hidden = !active;
  if (active) updateIndexAuthoringPresentation();
  else {
    elements.reviewKeyAuthoring.hidden = true;
    canvas.setIndexMode({ enabled: false });
  }
  updateDesignControls();
}

function startIndexAuthoring({ tableId = selectedDesignTable()?.id || null, indexId = null } = {}) {
  if (state.indexAuthoring) return;
  const table = designTableById(tableId);
  if (!table) {
    showToast("Select a designed table before creating an index.");
    return;
  }
  if (indexId && !designIndexById(table.id, indexId)) {
    showToast("The selected index is no longer in this design.", { error: true });
    return;
  }
  setIndexAuthoring(true, { tableId: table.id, indexId });
}

function toggleIndexAuthoring() {
  if (state.indexAuthoring) setIndexAuthoring(false);
  else startIndexAuthoring();
}

function handleIndexColumnSelection(table, column) {
  if (!state.indexAuthoring || !table.designId || !column.designId) return;
  const selection = state.indexSelection;
  if (!selection || selection.tableId !== table.designId) {
    showToast("All index columns must come from the selected table.");
    return;
  }
  const columnIds = [...selection.columnIds];
  const existingIndex = columnIds.indexOf(column.designId);
  if (existingIndex >= 0) columnIds.splice(existingIndex, 1);
  else columnIds.push(column.designId);
  state.indexSelection = { ...selection, columnIds };
  updateIndexAuthoringPresentation();
}

function reviewIndexAuthoring() {
  if (!state.indexAuthoring || !state.indexSelection?.tableId) return;
  const draft = {
    tableId: state.indexSelection.tableId,
    indexId: state.indexSelection.indexId,
    columnIds: [...state.indexSelection.columnIds],
  };
  setIndexAuthoring(false);
  openDesignIndexEditor(draft);
}

function reviewColumnAuthoring() {
  if (state.indexAuthoring) reviewIndexAuthoring();
  else reviewKeyAuthoring();
}

function updateGeneratedKeyName() {
  const current = elements.designKeyName.value;
  const generated = suggestDesignKeyName(state.design.content, {
    tableId: state.designKeyTableId,
    keyId: state.designKeyEditorId,
    kind: elements.designKeyKind.value,
    columnIds: state.designKeyColumnIds,
  });
  if (!current || current === state.designKeyAutoName) elements.designKeyName.value = generated;
  state.designKeyAutoName = generated;
}

function moveDesignKeyColumn(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= state.designKeyColumnIds.length) return;
  reorderDesignKeyColumns(index, target);
}

function reorderDesignKeyColumns(fromIndex, toIndex) {
  state.designKeyColumnIds = reorderedValues(state.designKeyColumnIds, fromIndex, toIndex);
  renderDesignKeyColumns();
  updateGeneratedKeyName();
}

function renderOrderedDesignColumns(container, table, columnIds, onMove, emptyCopy = null) {
  const columns = new Map((table?.columns || []).map(column => [column.id, column]));
  replace(container);
  if (!columnIds.length && emptyCopy) {
    container.append(element("p", { className: "none-reported", text: emptyCopy }));
    return;
  }
  columnIds.forEach((columnId, index) => {
    const column = columns.get(columnId);
    if (!column) return;
    const sortHandle = createIconButton({
      icon: "drag",
      label: `Reorder ${column.name}`,
      tooltip: `Drag to reorder ${column.name}`,
      className: "compact design-sort-handle",
    });
    sortHandle.dataset.sortHandle = "";
    const up = element("button", {
      className: "ui-button compact",
      type: "button",
      text: "↑",
      attrs: { "aria-label": `Move ${column.name} earlier` },
    });
    const down = element("button", {
      className: "ui-button compact",
      type: "button",
      text: "↓",
      attrs: { "aria-label": `Move ${column.name} later` },
    });
    up.disabled = index === 0;
    down.disabled = index === columnIds.length - 1;
    up.addEventListener("click", () => onMove(index, -1));
    down.addEventListener("click", () => onMove(index, 1));
    container.append(element("div", { className: "design-key-column", dataset: { sortKey: column.id } }, [
      sortHandle,
      element("span", { className: "design-key-column-order", text: String(index + 1).padStart(2, "0") }),
      element("span", { className: "design-key-column-copy" }, [element("strong", { text: column.name }), element("code", { text: column.dataType })]),
      element("span", { className: "design-key-column-actions" }, [up, down]),
    ]));
  });
  if (container === elements.designKeyColumns) designKeyColumnSorter.refresh();
  else if (container === elements.designIndexColumns) designIndexColumnSorter.refresh();
}

function renderDesignKeyColumns() {
  renderOrderedDesignColumns(
    elements.designKeyColumns,
    designTableById(state.designKeyTableId),
    state.designKeyColumnIds,
    moveDesignKeyColumn,
  );
}

function openDesignKeyEditor({ tableId, keyId = null, columnIds }) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const table = designTableById(tableId);
  const key = keyId ? designKeyById(tableId, keyId) : null;
  if (!table || (keyId && !key)) {
    showToast("The selected key is no longer in this design.", { error: true });
    return;
  }
  elements.designKeyForm.reset();
  replace(elements.designKeyStatus);
  state.designKeyEditorId = key?.id || null;
  state.designKeyTableId = table.id;
  state.designKeyColumnIds = [...columnIds];
  state.designKeyAutoName = null;
  elements.designKeyTitle.textContent = key ? `Edit ${key.name}` : "Confirm key";
  elements.saveDesignKeyButton.textContent = key ? "Save key" : "Create key";
  elements.designKeyTable.textContent = table.name;
  const primaryOption = elements.designKeyKind.querySelector('option[value="primary"]');
  primaryOption.disabled = table.keys.some(item => item.kind === "primary" && item.id !== key?.id);
  elements.designKeyKind.value = key?.kind || (primaryOption.disabled ? "unique" : "primary");
  elements.designKeyName.value = key?.name || "";
  renderDesignKeyColumns();
  updateGeneratedKeyName();
  openDialog(elements.designKeyDialog);
  elements.designKeyName.focus();
}

async function submitDesignKey(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  let result;
  try {
    result = saveDesignKey(state.design.content, {
      tableId: state.designKeyTableId,
      keyId: state.designKeyEditorId,
      name: elements.designKeyName.value,
      kind: elements.designKeyKind.value,
      columnIds: state.designKeyColumnIds,
    });
  } catch (error) {
    replace(elements.designKeyStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  state.designSubmitting = true;
  elements.saveDesignKeyButton.disabled = true;
  updateDesignControls();
  replace(elements.designKeyStatus, element("span", { text: "Validating and saving the key…" }));
  try {
    const table = designTableById(state.designKeyTableId);
    const editing = Boolean(state.designKeyEditorId);
    const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
    if (!design) return;
    elements.designKeyDialog.close();
    canvas.select(table.name, { notify: true });
    showToast(`${editing ? "Updated" : "Created"} ${result.key.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designKeyStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignKeyButton.disabled = false;
    updateHeader();
  }
}

function editDesignKey(constraint) {
  const table = selectedDesignTable();
  if (!table || !constraint?.designId) return;
  startKeyAuthoring({ tableId: table.id, keyId: constraint.designId });
}

function confirmDeleteDesignKey(constraint) {
  const table = selectedDesignTable();
  if (!table || !constraint?.designId || state.designSubmitting) return;
  try {
    deleteDesignKey(state.design.content, table.id, constraint.designId);
  } catch (error) {
    showToast(error.message, { error: true });
    return;
  }
  askConfirmation({
    title: "Delete key",
    message: `Delete ${constraint.displayKind.toLocaleLowerCase()} “${constraint.name}” from ${table.name}? The columns remain in the design.`,
    label: "Delete key",
    callback: async () => {
      let result;
      try {
        result = deleteDesignKey(state.design.content, table.id, constraint.designId);
      } catch (error) {
        errorToast(error);
        return;
      }
      if (!await flushLayoutBeforeTransition()) return;
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
        if (!design) return;
        showToast(`Deleted ${result.key.name} in design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
}

function renderDesignCheckDependencies() {
  const table = designTableById(state.designCheckTableId);
  replace(elements.designCheckDependencies);
  if (!table) return;
  const columnIds = new Set(expressionColumnIds(elements.designCheckExpression.value, table.columns));
  const dependencies = table.columns.filter(column => columnIds.has(column.id));
  if (!dependencies.length) {
    elements.designCheckDependencies.append(element("span", {
      className: "design-dependency-empty",
      text: "No table columns recognized yet.",
    }));
    return;
  }
  for (const column of dependencies) {
    elements.designCheckDependencies.append(element("span", {
      className: "design-dependency-chip",
      text: `${column.name} · ${column.dataType}`,
    }));
  }
}

function updateGeneratedCheckName() {
  if (!state.design || !state.designCheckTableId) return;
  const current = elements.designCheckName.value;
  const generated = suggestDesignCheckName(state.design.content, {
    tableId: state.designCheckTableId,
    checkId: state.designCheckEditorId,
    expression: elements.designCheckExpression.value,
  });
  if (!current || current === state.designCheckAutoName) elements.designCheckName.value = generated;
  state.designCheckAutoName = generated;
}

function updateDesignCheckDraft() {
  renderDesignCheckDependencies();
  updateGeneratedCheckName();
}

function openDesignCheckEditor({ tableId, checkId = null }) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const table = designTableById(tableId);
  const check = checkId ? designCheckById(tableId, checkId) : null;
  if (!table || (checkId && !check)) {
    showToast("The selected check is no longer in this design.", { error: true });
    return;
  }
  elements.designCheckForm.reset();
  replace(elements.designCheckStatus);
  state.designCheckEditorId = check?.id || null;
  state.designCheckTableId = table.id;
  state.designCheckAutoName = null;
  elements.designCheckTitle.textContent = check ? `Edit ${check.name}` : "Create check";
  elements.saveDesignCheckButton.textContent = check ? "Save check" : "Create check";
  elements.designCheckTable.textContent = table.name;
  elements.designCheckExpression.value = check?.expression || "";
  elements.designCheckName.value = check?.name || "";
  updateDesignCheckDraft();
  if (check) state.designCheckAutoName = null;
  openDialog(elements.designCheckDialog);
  (check ? elements.designCheckExpression : elements.designCheckName).focus();
}

async function submitDesignCheck(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  let result;
  try {
    result = saveDesignCheck(state.design.content, {
      tableId: state.designCheckTableId,
      checkId: state.designCheckEditorId,
      name: elements.designCheckName.value,
      expression: elements.designCheckExpression.value,
    });
  } catch (error) {
    replace(elements.designCheckStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  state.designSubmitting = true;
  elements.saveDesignCheckButton.disabled = true;
  updateDesignControls();
  replace(elements.designCheckStatus, element("span", { text: "Validating and saving the check…" }));
  try {
    const table = designTableById(state.designCheckTableId);
    const editing = Boolean(state.designCheckEditorId);
    const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
    if (!design) return;
    elements.designCheckDialog.close();
    canvas.select(table.name, { notify: true });
    showToast(`${editing ? "Updated" : "Created"} ${result.check.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designCheckStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignCheckButton.disabled = false;
    updateHeader();
  }
}

function editDesignCheck(constraint) {
  const table = selectedDesignTable();
  if (!table || !constraint?.designId) return;
  openDesignCheckEditor({ tableId: table.id, checkId: constraint.designId });
}

function confirmDeleteDesignCheck(constraint) {
  const table = selectedDesignTable();
  if (!table || !constraint?.designId || state.designSubmitting) return;
  askConfirmation({
    title: "Delete check",
    message: `Delete check “${constraint.name}” from ${table.name}? The columns remain in the design.`,
    label: "Delete check",
    callback: async () => {
      let result;
      try {
        result = deleteDesignCheck(state.design.content, table.id, constraint.designId);
      } catch (error) {
        errorToast(error);
        return;
      }
      if (!await flushLayoutBeforeTransition()) return;
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
        if (!design) return;
        showToast(`Deleted ${result.check.name} in design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
}

function updateGeneratedIndexName() {
  if (!state.design || !state.designIndexTableId) return;
  const current = elements.designIndexName.value;
  const generated = suggestDesignIndexName(state.design.content, {
    tableId: state.designIndexTableId,
    indexId: state.designIndexEditorId,
    columnIds: state.designIndexColumnIds,
    expression: elements.designIndexExpression.value,
  });
  if (!current || current === state.designIndexAutoName) elements.designIndexName.value = generated;
  state.designIndexAutoName = generated;
}

function renderDesignIndexDependency(container, label, expression) {
  const table = designTableById(state.designIndexTableId);
  replace(container);
  if (!table || !expression.trim()) {
    container.append(element("span", { text: `${label}: none` }));
    return;
  }
  const columnIds = new Set(expressionColumnIds(expression, table.columns));
  const dependencies = table.columns.filter(column => columnIds.has(column.id));
  container.append(element("strong", { text: `${label}:` }));
  if (!dependencies.length) {
    container.append(element("span", { text: "No table columns recognized" }));
    return;
  }
  for (const column of dependencies) {
    container.append(element("span", { className: "design-dependency-chip", text: column.name }));
  }
}

function updateDesignIndexDraft() {
  renderDesignIndexDependency(
    elements.designIndexExpressionDependencies,
    "Expression uses",
    elements.designIndexExpression.value,
  );
  renderDesignIndexDependency(
    elements.designIndexPredicateDependencies,
    "Predicate uses",
    elements.designIndexPredicate.value,
  );
  updateGeneratedIndexName();
}

function moveDesignIndexColumn(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= state.designIndexColumnIds.length) return;
  reorderDesignIndexColumns(index, target);
}

function reorderDesignIndexColumns(fromIndex, toIndex) {
  state.designIndexColumnIds = reorderedValues(state.designIndexColumnIds, fromIndex, toIndex);
  renderDesignIndexColumns();
  updateGeneratedIndexName();
}

function renderDesignIndexColumns() {
  renderOrderedDesignColumns(
    elements.designIndexColumns,
    designTableById(state.designIndexTableId),
    state.designIndexColumnIds,
    moveDesignIndexColumn,
    "No plain columns selected. Enter an expression below to create an expression-only index.",
  );
}

function openDesignIndexEditor({ tableId, indexId = null, columnIds = [] }) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const table = designTableById(tableId);
  const index = indexId ? designIndexById(tableId, indexId) : null;
  if (!table || (indexId && !index)) {
    showToast("The selected index is no longer in this design.", { error: true });
    return;
  }
  elements.designIndexForm.reset();
  replace(elements.designIndexStatus);
  state.designIndexEditorId = index?.id || null;
  state.designIndexTableId = table.id;
  state.designIndexColumnIds = [...columnIds];
  state.designIndexAutoName = null;
  elements.designIndexTitle.textContent = index ? `Edit ${index.name}` : "Create index";
  elements.saveDesignIndexButton.textContent = index ? "Save index" : "Create index";
  elements.designIndexTable.textContent = table.name;
  elements.designIndexName.value = index?.name || "";
  elements.designIndexMethod.value = index?.method || "btree";
  elements.designIndexUnique.checked = index?.unique || false;
  elements.designIndexExpression.value = index?.expression || "";
  elements.designIndexPredicate.value = index?.predicate || "";
  renderDesignIndexColumns();
  updateDesignIndexDraft();
  if (index) state.designIndexAutoName = null;
  openDialog(elements.designIndexDialog);
  (index ? elements.designIndexExpression : elements.designIndexName).focus();
}

async function submitDesignIndex(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDetachedWorkspace() || !state.design) return;
  let result;
  try {
    result = saveDesignIndex(state.design.content, {
      tableId: state.designIndexTableId,
      indexId: state.designIndexEditorId,
      name: elements.designIndexName.value,
      method: elements.designIndexMethod.value,
      columnIds: state.designIndexColumnIds,
      expression: elements.designIndexExpression.value,
      predicate: elements.designIndexPredicate.value,
      unique: elements.designIndexUnique.checked,
    });
  } catch (error) {
    replace(elements.designIndexStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  state.designSubmitting = true;
  elements.saveDesignIndexButton.disabled = true;
  updateDesignControls();
  replace(elements.designIndexStatus, element("span", { text: "Validating and saving the index…" }));
  try {
    const table = designTableById(state.designIndexTableId);
    const editing = Boolean(state.designIndexEditorId);
    const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
    if (!design) return;
    elements.designIndexDialog.close();
    canvas.select(table.name, { notify: true });
    showToast(`${editing ? "Updated" : "Created"} ${result.index.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designIndexStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignIndexButton.disabled = false;
    updateHeader();
  }
}

function editDesignIndex(index) {
  const table = selectedDesignTable();
  if (!table || !index?.designId) return;
  startIndexAuthoring({ tableId: table.id, indexId: index.designId });
}

function confirmDeleteDesignIndex(index) {
  const table = selectedDesignTable();
  if (!table || !index?.designId || state.designSubmitting) return;
  askConfirmation({
    title: "Delete index",
    message: `Delete index “${index.name}” from ${table.name}? Its columns remain in the design.`,
    label: "Delete index",
    callback: async () => {
      let result;
      try {
        result = deleteDesignIndex(state.design.content, table.id, index.designId);
      } catch (error) {
        errorToast(error);
        return;
      }
      if (!await flushLayoutBeforeTransition()) return;
      state.designSubmitting = true;
      updateDesignControls();
      try {
        const design = await replaceActiveDesign(result.content, { selectedTableName: table.name });
        if (!design) return;
        showToast(`Deleted ${result.index.name} in design revision ${design.revision}.`);
      } catch (error) {
        errorToast(error);
      } finally {
        state.designSubmitting = false;
        updateHeader();
      }
    },
  });
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

function relationshipNameForTables(source, target) {
  if (!source || !target) return "";
  const stem = `${source.name}_${target.name}_fkey`;
  let value = "";
  for (const character of stem) {
    if (new TextEncoder().encode(value + character).length > 63) break;
    value += character;
  }
  return value;
}

function generatedRelationshipName() {
  return relationshipNameForTables(
    designTableById(elements.designRelationshipSource.value),
    designTableById(elements.designRelationshipTarget.value),
  );
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

function renderRelationshipMappings(preferredSourceColumnIds = null) {
  const source = designTableById(elements.designRelationshipSource.value);
  const target = designTableById(elements.designRelationshipTarget.value);
  const key = targetKey();
  const previous = preferredSourceColumnIds || [...elements.designRelationshipMappings.querySelectorAll("[data-relationship-source-column]")]
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
    const anchored = state.designRelationshipAnchor?.targetColumnId === targetColumnId;
    select.disabled = anchored;
    const targetValue = element("span", { className: "relationship-mapping-target" }, [
      element("small", { text: anchored ? "Selected target" : "Target" }),
      element("code", { text: `${targetColumn.name} · ${targetColumn.dataType}`, title: `${targetColumn.name} · ${targetColumn.dataType}` }),
    ]);
    elements.designRelationshipMappings.append(element("div", { className: "relationship-mapping-row" }, [
      select,
      element("span", { text: "→", attrs: { "aria-hidden": "true" } }),
      targetValue,
    ]));
  });
  renderRelationshipTypeAlignment();
}

function currentRelationshipMapping() {
  const key = targetKey();
  if (!key) return null;
  return {
    sourceTableId: elements.designRelationshipSource.value,
    sourceColumnIds: [...elements.designRelationshipMappings.querySelectorAll("[data-relationship-source-column]")].map(select => select.value),
    targetTableId: elements.designRelationshipTarget.value,
    targetColumnIds: [...key.columnIds],
  };
}

function renderRelationshipTypeAlignment() {
  const mapping = currentRelationshipMapping();
  replace(elements.designRelationshipTypeAlignment);
  elements.designRelationshipTypeAlignment.hidden = true;
  if (!mapping) return;
  let changes;
  try {
    ({ changes } = alignRelationshipColumnTypes(state.design.content, mapping));
  } catch {
    return;
  }
  if (!changes.length) return;
  elements.designRelationshipTypeAlignment.hidden = false;
  elements.designRelationshipTypeAlignment.append(
    element("strong", { text: "Automatic type match" }),
    element("span", {
      text: `${changes.length} foreign-key column type${changes.length === 1 ? "" : "s"} will change to match the referenced key.`,
    }),
    element("ul", {}, changes.map(change => element("li", {}, [
      element("code", { text: `${change.sourceTableName}.${change.sourceColumnName}` }),
      `: ${change.from} → ${change.to}`,
    ]))),
  );
}

function updateRelationshipTargetKeys(draft) {
  const target = designTableById(elements.designRelationshipTarget.value);
  const eligibleKeyIds = new Set(draft.eligibleTargetKeyIds);
  const keys = target?.keys.filter(key => eligibleKeyIds.has(key.id)) || [];
  replaceSelectOptions(elements.designRelationshipKey, keys.map(key => ({ value: key.id, label: keyLabel(target, key) })), draft.targetKeyId);
  renderRelationshipMappings(draft.sourceColumnIds);
  updateGeneratedRelationshipName();
}

function openDesignRelationshipEditor(draft, { relationshipId = null, defaults = null } = {}) {
  if (!isDetachedWorkspace() || !state.design || state.catalogLoading) return;
  const existing = relationshipId ? designRelationshipById(relationshipId) : null;
  if (relationshipId && !existing) {
    showToast("The selected relationship is no longer in this design.", { error: true });
    return;
  }
  const source = designTableById(draft.sourceTableId);
  const target = designTableById(draft.targetTableId);
  if (!source || !target) {
    showToast("The selected relationship references a table that is no longer in this design.", { error: true });
    return;
  }
  elements.designRelationshipForm.reset();
  replace(elements.designRelationshipStatus);
  state.designRelationshipEditorId = existing?.id || null;
  state.designRelationshipAnchor = draft;
  elements.designRelationshipTitle.textContent = existing ? `Edit ${existing.name}` : "Confirm relationship";
  elements.designRelationshipCopy.textContent = existing
    ? "Adjust the mapping and referential actions, or reselect both endpoints directly on the canvas."
    : "Review the columns selected on the canvas, then name the constraint and choose its referential actions.";
  elements.saveDesignRelationshipButton.textContent = existing ? "Save relationship" : "Create relationship";
  elements.reselectDesignRelationship.hidden = !existing;
  replaceSelectOptions(elements.designRelationshipSource, [{ value: source.id, label: source.name }], source.id);
  replaceSelectOptions(elements.designRelationshipTarget, [{ value: target.id, label: target.name }], target.id);
  elements.designRelationshipSource.disabled = true;
  elements.designRelationshipTarget.disabled = true;
  const initialName = defaults?.name ?? existing?.name ?? "";
  const existingGeneratedName = existing
    ? relationshipNameForTables(designTableById(existing.sourceTableId), designTableById(existing.targetTableId))
    : null;
  const tracksGeneratedName = defaults?.tracksGeneratedName
    ?? Boolean(existing && existing.name === existingGeneratedName);
  elements.designRelationshipName.value = initialName;
  state.designRelationshipAutoName = tracksGeneratedName ? initialName : null;
  updateRelationshipTargetKeys(draft);
  elements.designRelationshipOnUpdate.value = defaults?.onUpdate ?? existing?.onUpdate ?? "NO ACTION";
  elements.designRelationshipOnDelete.value = defaults?.onDelete ?? existing?.onDelete ?? "NO ACTION";
  elements.designRelationshipDeferrable.checked = defaults?.deferrable ?? existing?.deferrable ?? false;
  elements.designRelationshipDeferred.checked = defaults?.initiallyDeferred ?? existing?.initiallyDeferred ?? false;
  elements.designRelationshipDeferred.disabled = !elements.designRelationshipDeferrable.checked;
  openDialog(elements.designRelationshipDialog);
  elements.designRelationshipName.focus();
}

function editDesignRelationship(relationship) {
  if (!relationship?.designId || state.designSubmitting) return;
  let draft;
  try {
    draft = relationshipDraftFromExisting(state.design.content, relationship.designId);
  } catch (error) {
    showToast(error.message, { error: true });
    return;
  }
  openDesignRelationshipEditor(draft, { relationshipId: relationship.designId });
}

function setRelationshipAuthoring(enabled, { relationshipId = null, defaults = null } = {}) {
  const active = Boolean(enabled && isDetachedWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
  if (active && state.keyAuthoring) setKeyAuthoring(false);
  if (active && state.indexAuthoring) setIndexAuthoring(false);
  state.relationshipAuthoring = active;
  state.relationshipSource = null;
  state.relationshipAuthoringEditId = active ? relationshipId : null;
  state.relationshipAuthoringDefaults = active ? defaults : null;
  elements.relationshipAuthoringBanner.hidden = !active;
  elements.relationshipAuthoringStep.textContent = "1";
  elements.relationshipAuthoringInstruction.textContent = relationshipId
    ? "Select the new foreign key column"
    : "Select the foreign key column";
  elements.reviewKeyAuthoring.hidden = true;
  canvas.setRelationshipMode({ enabled: active });
  updateDesignControls();
}

function startRelationshipAuthoring({ relationshipId = null, defaults = null } = {}) {
  if (state.relationshipAuthoring) return false;
  if (relationshipId && !designRelationshipById(relationshipId)) {
    showToast("The selected relationship is no longer in this design.", { error: true });
    return false;
  }
  const hasTargetKey = state.design?.content.tables.some(table => table.keys.some(key => key.kind === "primary" || key.kind === "unique"));
  if (!hasTargetKey) {
    showToast("Add a primary or unique key to a target table first.");
    return false;
  }
  setRelationshipAuthoring(true, { relationshipId, defaults });
  return true;
}

function toggleRelationshipAuthoring() {
  if (state.relationshipAuthoring) setRelationshipAuthoring(false);
  else startRelationshipAuthoring();
}

function handleRelationshipColumnSelection(table, column) {
  if (!state.relationshipAuthoring || !table.designId || !column.designId) return;
  if (!state.relationshipSource) {
    state.relationshipSource = {
      tableId: table.designId,
      columnId: column.designId,
      tableName: table.name,
      columnName: column.name,
    };
    elements.relationshipAuthoringStep.textContent = "2";
    elements.relationshipAuthoringInstruction.textContent = `Now select the referenced key column for ${table.name}.${column.name}`;
    canvas.setRelationshipMode({ enabled: true, source: { tableName: table.name, columnName: column.name } });
    return;
  }
  let draft;
  try {
    draft = relationshipDraftFromColumns(state.design.content, {
      sourceTableId: state.relationshipSource.tableId,
      sourceColumnId: state.relationshipSource.columnId,
      targetTableId: table.designId,
      targetColumnId: column.designId,
    });
  } catch (error) {
    showToast(error.message, { error: true });
    return;
  }
  const relationshipId = state.relationshipAuthoringEditId;
  const defaults = state.relationshipAuthoringDefaults;
  setRelationshipAuthoring(false);
  openDesignRelationshipEditor(draft, { relationshipId, defaults });
}

function remapRelationshipForSelectedKey() {
  const anchor = state.designRelationshipAnchor;
  if (!anchor) return;
  try {
    let draft;
    if (anchor.targetColumnId) {
      draft = relationshipDraftFromColumns(state.design.content, {
        ...anchor,
        targetKeyId: elements.designRelationshipKey.value,
      });
    } else {
      const source = designTableById(anchor.sourceTableId);
      const target = designTableById(anchor.targetTableId);
      const key = target?.keys.find(item => item.id === elements.designRelationshipKey.value && ["primary", "unique"].includes(item.kind));
      if (!source || !target || !key) throw new Error("The selected target key is no longer in this design.");
      if (source.columns.length < key.columnIds.length) {
        throw new Error(`The source table needs at least ${key.columnIds.length} columns to reference this composite key.`);
      }
      draft = {
        ...anchor,
        targetKeyId: key.id,
        targetColumnIds: [...key.columnIds],
      };
    }
    state.designRelationshipAnchor = draft;
    renderRelationshipMappings(draft.sourceColumnIds);
    state.designRelationshipAnchor.sourceColumnIds = [...elements.designRelationshipMappings.querySelectorAll("[data-relationship-source-column]")]
      .map(select => select.value);
  } catch (error) {
    elements.designRelationshipKey.value = anchor.targetKeyId;
    replace(elements.designRelationshipStatus, errorPanel(error));
  }
}

function reselectDesignRelationship() {
  const relationshipId = state.designRelationshipEditorId;
  if (!relationshipId || !designRelationshipById(relationshipId)) {
    showToast("The selected relationship is no longer in this design.", { error: true });
    return;
  }
  const defaults = {
    name: elements.designRelationshipName.value,
    onUpdate: elements.designRelationshipOnUpdate.value,
    onDelete: elements.designRelationshipOnDelete.value,
    deferrable: elements.designRelationshipDeferrable.checked,
    initiallyDeferred: elements.designRelationshipDeferred.checked,
    tracksGeneratedName: Boolean(
      state.designRelationshipAutoName
      && elements.designRelationshipName.value === state.designRelationshipAutoName
    ),
  };
  elements.designRelationshipDialog.close();
  startRelationshipAuthoring({ relationshipId, defaults });
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
  let aligned;
  const relationshipId = state.designRelationshipEditorId;
  const editing = Boolean(relationshipId);
  try {
    relationship = editing
      ? updateDesignRelationship(state.design.content, relationshipId, designRelationshipValues())
      : createDesignRelationship(state.design.content, designRelationshipValues());
    aligned = alignRelationshipColumnTypes(state.design.content, relationship);
  } catch (error) {
    replace(elements.designRelationshipStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  const content = aligned.content;
  if (editing) {
    content.relationships = content.relationships.map(item => item.id === relationshipId ? relationship : item);
  } else {
    content.relationships.push(relationship);
  }
  state.designSubmitting = true;
  elements.saveDesignRelationshipButton.disabled = true;
  updateDesignControls();
  replace(elements.designRelationshipStatus, element("span", { text: "Validating and saving the relationship…" }));
  try {
    const design = await replaceActiveDesign(content);
    if (!design) return;
    elements.designRelationshipDialog.close();
    const typeCopy = aligned.changes.length
      ? ` Matched ${aligned.changes.length} foreign-key column type${aligned.changes.length === 1 ? "" : "s"}.`
      : "";
    showToast(`${editing ? "Updated" : "Created"} ${relationship.name} in design revision ${design.revision}.${typeCopy}`);
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
  if (layer !== "tables" && (state.relationshipAuthoring || state.keyAuthoring || state.indexAuthoring)) cancelColumnAuthoring();
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
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && (state.relationshipAuthoring || state.keyAuthoring || state.indexAuthoring)) cancelColumnAuthoring();
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
  elements.createRelationshipButton.addEventListener("click", toggleRelationshipAuthoring);
  elements.createKeyButton.addEventListener("click", toggleKeyAuthoring);
  elements.createIndexButton.addEventListener("click", toggleIndexAuthoring);
  elements.cancelRelationshipAuthoring.addEventListener("click", cancelColumnAuthoring);
  elements.reviewKeyAuthoring.addEventListener("click", reviewColumnAuthoring);
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
  elements.createViewButton.addEventListener("click", () => openDesignViewEditor());
  elements.designViewForm.addEventListener("submit", submitDesignView);
  elements.designViewName.addEventListener("input", () => scheduleDesignViewPreview());
  elements.designViewDefinition.addEventListener("input", () => scheduleDesignViewPreview());
  elements.designViewKind.addEventListener("change", updateDesignViewPopulation);
  elements.designViewPopulate.addEventListener("change", renderDesignViewPreview);
  elements.designViewDialog.addEventListener("close", () => {
    window.clearTimeout(state.designViewPreviewTimer);
    state.designViewPreviewGeneration += 1;
    state.designViewEditorId = null;
    state.designViewPreviewLoading = false;
  });
  elements.designKeyForm.addEventListener("submit", submitDesignKey);
  elements.designKeyKind.addEventListener("change", updateGeneratedKeyName);
  elements.designKeyDialog.addEventListener("close", () => {
    state.designKeyEditorId = null;
    state.designKeyTableId = null;
    state.designKeyColumnIds = [];
    state.designKeyAutoName = null;
  });
  elements.designCheckForm.addEventListener("submit", submitDesignCheck);
  elements.designCheckExpression.addEventListener("input", updateDesignCheckDraft);
  elements.designCheckDialog.addEventListener("close", () => {
    state.designCheckEditorId = null;
    state.designCheckTableId = null;
    state.designCheckAutoName = null;
  });
  elements.designIndexForm.addEventListener("submit", submitDesignIndex);
  elements.designIndexExpression.addEventListener("input", updateDesignIndexDraft);
  elements.designIndexPredicate.addEventListener("input", updateDesignIndexDraft);
  elements.designIndexDialog.addEventListener("close", () => {
    state.designIndexEditorId = null;
    state.designIndexTableId = null;
    state.designIndexColumnIds = [];
    state.designIndexAutoName = null;
  });
  elements.designRelationshipForm.addEventListener("submit", submitDesignRelationship);
  elements.reselectDesignRelationship.addEventListener("click", reselectDesignRelationship);
  elements.designRelationshipKey.addEventListener("change", remapRelationshipForSelectedKey);
  elements.designRelationshipMappings.addEventListener("change", renderRelationshipTypeAlignment);
  elements.designRelationshipDialog.addEventListener("close", () => {
    state.designRelationshipAnchor = null;
    state.designRelationshipAutoName = null;
    state.designRelationshipEditorId = null;
  });
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

const designTableColumnSorter = installSortableList(elements.designColumns, {
  itemSelector: ".design-column-row",
  itemLabel: item => item.querySelector("[data-design-column-name]")?.value || "new column",
  onReorder: () => designTableColumnSorter.refresh(),
});
const designKeyColumnSorter = installSortableList(elements.designKeyColumns, {
  itemSelector: ".design-key-column",
  itemLabel: item => item.querySelector("strong")?.textContent || "key column",
  onReorder: reorderDesignKeyColumns,
});
const designIndexColumnSorter = installSortableList(elements.designIndexColumns, {
  itemSelector: ".design-key-column",
  itemLabel: item => item.querySelector("strong")?.textContent || "index column",
  onReorder: reorderDesignIndexColumns,
});

bindEvents();
renderCatalogSurfaces();
renderCatalogState();
bootstrap();
