import { api, ApiError } from "./api.js";
import { CatalogCanvas } from "./canvas.js";
import {
  alignRelationshipColumnTypes,
  createDesignRelationship,
  createDesignTable,
  deleteDesignObject,
  deleteDesignRoutine,
  deleteDesignTrigger,
  deleteDesignType,
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
  saveDesignRoutine,
  saveDesignTrigger,
  saveDesignType,
  saveDesignView,
  suggestDesignCheckName,
  suggestDesignIndexName,
  suggestDesignKeyName,
  toggleDesignIndexColumn,
  updateDesignRelationship,
  updateDesignTable,
} from "./design.js";
import {
  allViews,
  renderCatalogStats,
  renderFunctions,
  renderInspector,
  renderObjects,
  renderTypes,
  renderViewDetail,
  renderViewsList,
} from "./catalog.js";
import { element, emptyPanel, errorPanel, formatTimestamp, replace } from "./dom.js";
import {
  applyColumnDisplayOrders,
  removeColumnDisplayOrder,
  replaceColumnDisplayOrder,
} from "/assets/common/column-display-order.js";
import { applyJsonDelta } from "/assets/common/json-delta.js";
import {
  createChangeTransitionManager,
  elementFullyVisible,
  resolveChangeTargetElements,
} from "/assets/common/change-transition.js";
import { createTransientCueManager } from "/assets/common/transient-cue.js";
import {
  composePostgresTypeModifier,
  parsePostgresTypeModifier,
  postgresTypeModifierSummary,
  postgresTypeOptions,
} from "/assets/common/postgres-types.js";
import { createSearchableSelect } from "/assets/common/searchable-select.js";
import { installSortableList, reorderedValues } from "/assets/common/sortable.js";
import { assertUnavailableControls, bindUnavailableControls } from "./unavailable.js";
import { closeDetailsMenus, createIconButton, createIconElement, createStatePanel, DockPane, downloadContent, initializeUi } from "./ui.js";
import {
  readWorkspaceNavigation,
  readWorkspacePreferences,
  updateWorkspacePreferences,
  workspaceNavigationHref,
} from "./workspace-navigation.js";
import { renderDesignViewStory } from "./view-story.js";
import { renderRelationBrowser, renderRelationRows } from "./relation-browser.js";
import { createRelationDataSource } from "./relation-data-source.js";
import { createViewAnalysisController } from "./view-analysis.js";
import {
  catalogTableId,
  catalogViewId,
  createWorkspaceStateCommitter,
  navigatedCatalogTable,
  navigatedCatalogView,
  selectedCatalogTable,
  selectedCatalogView,
} from "./workspace-state.js";
import {
  designChangePresentation,
  designChangeTargets,
  viewAnalysisContextSignature,
} from "./design-change.js";
import {
  createLatestRequestController,
  createWorkspaceOperationController,
} from "./request-coordinator.js";
import { createMigrationReviewController } from "./migration-review.js";
import { createSqlConsole } from "./sql-console.js";

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
  inspectorDataToolsButton: byId("table-data-tools-button"),
  inspectorStructurePanel: byId("inspector-structure-panel"),
  inspectorDataWorkspace: byId("inspector-data-workspace"),
  inspectorRowsHeader: byId("inspector-rows-header"),
  inspectorDataActions: byId("inspector-data-actions"),
  showInspectorRows: byId("show-inspector-rows"),
  showInspectorConsole: byId("show-inspector-console"),
  inspectorRowsContent: byId("inspector-rows-content"),
  inspectorConsoleContent: byId("inspector-console-content"),
  maximizeInspectorData: byId("maximize-inspector-data"),
  minimizeInspectorData: byId("minimize-inspector-data"),
  inspectorRowsTitle: byId("inspector-rows-title"),
  inspectorRowsBody: byId("inspector-rows-body"),
  inspectorRowsStatus: byId("inspector-rows-status"),
  inspectorRowsMore: byId("inspector-rows-more"),
  refreshInspectorRows: byId("refresh-inspector-rows"),
  openFullRowPreview: byId("open-full-row-preview"),
  inspectorSqlDraft: byId("inspector-sql-draft"),
  clearInspectorSql: byId("clear-inspector-sql"),
  runInspectorSql: byId("run-inspector-sql"),
  cancelInspectorSql: byId("cancel-inspector-sql"),
  inspectorSqlStatus: byId("inspector-sql-status"),
  inspectorSqlResults: byId("inspector-sql-results"),
  inspectorTitle: byId("table-inspector-title"),
  inspectorEyebrow: byId("table-inspector-eyebrow"),
  inspectorEmptyCopy: byId("inspector-empty-copy"),
  inspectorEmpty: byId("inspector-empty"),
  inspectorContent: byId("inspector-content"),
  inspectorTableForm: byId("inspector-table-form"),
  inspectorTableName: byId("inspector-table-name"),
  inspectorDesignColumns: byId("inspector-design-columns"),
  inspectorColumnCount: byId("inspector-column-count"),
  addInspectorColumnButton: byId("add-inspector-column-button"),
  discardInspectorTableButton: byId("discard-inspector-table-button"),
  saveInspectorTableButton: byId("save-inspector-table-button"),
  inspectorTableStatus: byId("inspector-table-status"),
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
  runSqlButton: byId("run-sql-button"),
  cancelSqlButton: byId("cancel-sql-button"),
  sqlEditorStatus: byId("sql-editor-status"),
  sqlResults: byId("sql-results"),
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
  functionsSource: byId("functions-source"),
  functionsCopy: byId("functions-copy"),
  functionsSearch: byId("functions-search"),
  functionsCount: byId("functions-count"),
  functionsList: byId("functions-list"),
  createFunctionButton: byId("create-function-button"),
  designRoutineDialog: byId("design-routine-dialog"),
  designRoutineForm: byId("design-routine-form"),
  designRoutineTitle: byId("design-routine-title"),
  designRoutineDefinition: byId("design-routine-definition"),
  designRoutinePreview: byId("design-routine-preview"),
  designRoutineStatus: byId("design-routine-status"),
  saveDesignRoutineButton: byId("save-design-routine-button"),
  typesButton: byId("types-button"),
  typesDialog: byId("types-dialog"),
  typesSearch: byId("types-search"),
  typesCount: byId("types-count"),
  typesList: byId("types-list"),
  createTypeButton: byId("create-type-button"),
  designTypeDialog: byId("design-type-dialog"),
  designTypeForm: byId("design-type-form"),
  designTypeTitle: byId("design-type-title"),
  designTypeDefinition: byId("design-type-definition"),
  designTypePreview: byId("design-type-preview"),
  designTypeStatus: byId("design-type-status"),
  saveDesignTypeButton: byId("save-design-type-button"),
  designTriggerDialog: byId("design-trigger-dialog"),
  designTriggerForm: byId("design-trigger-form"),
  designTriggerTitle: byId("design-trigger-title"),
  designTriggerDefinition: byId("design-trigger-definition"),
  designTriggerPreview: byId("design-trigger-preview"),
  designTriggerStatus: byId("design-trigger-status"),
  saveDesignTriggerButton: byId("save-design-trigger-button"),
  deleteDesignTriggerButton: byId("delete-design-trigger-button"),
  objectsButton: byId("objects-button"),
  objectsDialog: byId("objects-dialog"),
  objectsSource: byId("objects-source"),
  objectsCopy: byId("objects-copy"),
  objectsSearch: byId("objects-search"),
  objectsCount: byId("objects-count"),
  objectsList: byId("objects-list"),
  relationPreviewDialog: byId("relation-preview-dialog"),
  relationPreviewTitle: byId("relation-preview-title"),
  relationPreviewCopy: byId("relation-preview-copy"),
  relationPreviewBody: byId("relation-preview-body"),
  relationPreviewStatus: byId("relation-preview-status"),
  relationPreviewMore: byId("relation-preview-more"),
  createTriggerButton: byId("create-trigger-button"),
  reviewMigrationButton: byId("review-migration-button"),
  migrationDialog: byId("migration-dialog"),
  migrationReviewBody: byId("migration-review-body"),
  migrationStatus: byId("migration-status"),
  refreshMigrationButton: byId("refresh-migration-button"),
  resolveMigrationButton: byId("resolve-migration-button"),
  applyMigrationButton: byId("apply-migration-button"),
  undoDesignButton: byId("undo-design-button"),
  redoDesignButton: byId("redo-design-button"),
  resetDesignButton: byId("reset-design-button"),
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
  dependencyImpactDialog: byId("dependency-impact-dialog"),
  dependencyImpactTitle: byId("dependency-impact-title"),
  dependencyImpactCopy: byId("dependency-impact-copy"),
  dependencyImpactStatus: byId("dependency-impact-status"),
  dependencyImpactTree: byId("dependency-impact-tree"),
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
  getRestoreFocusTarget: () => document.querySelector(`.table-card[data-table-id="${CSS.escape(state.selectedTableId || "")}"]`) || elements.canvas,
  onToggleRequest: () => handleInspectorHeaderGesture("left"),
  onStateChange: paneState => {
    persistInspectorState(paneState);
    if (inspectorPreferenceReady && paneState === "dismissed") closeInspectorDataWorkspace();
  },
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
  designHistory: null,
  designSubmitting: false,
  historySubmitting: false,
  inspectorTableEditorId: null,
  inspectorTableEditorDirty: false,
  inspectorTableEditorPopulating: false,
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
  databaseCatalog: null,
  columnOrderModes: new Map(),
  catalogLoading: false,
  catalogError: null,
  liveRelations: null,
  liveRelationsLoading: false,
  liveRelationsError: null,
  liveRelationsGeneration: 0,
  liveRelationsSearchTimer: null,
  relationPreview: null,
  relationPreviewLoading: false,
  relationPreviewError: null,
  relationPreviewGeneration: 0,
  inspectorMode: "structure",
  inspectorDataMaximized: false,
  inspectorRowsView: "table",
  inspectorDataKey: null,
  inspectorRelation: null,
  inspectorRows: null,
  inspectorRowsLoading: false,
  inspectorRowsError: null,
  inspectorRowsGeneration: 0,
  liveViewLineage: null,
  liveViewLineageKey: null,
  liveViewLineageLoading: false,
  liveViewLineageError: null,
  selectedTableId: null,
  selectedViewId: null,
  selectedViewOutputOrdinal: null,
  designViewEditorId: null,
  designViewPreviewAnalysis: null,
  designViewPreviewError: null,
  designViewPreviewLoading: false,
  designViewPreviewOutputOrdinal: null,
  designViewPreviewTimer: null,
  designViewPreviewGeneration: 0,
  designRoutineEditorId: null,
  designRoutineAnalysis: null,
  designRoutineAnalysisDefinition: null,
  designRoutineAnalysisError: null,
  designRoutineAnalysisLoading: false,
  designRoutineAnalysisTimer: null,
  designRoutineAnalysisGeneration: 0,
  typeFilter: "all",
  designTypeEditorId: null,
  designTypeAnalysis: null,
  designTypeAnalysisDefinition: null,
  designTypeAnalysisError: null,
  designTypeAnalysisLoading: false,
  designTypeAnalysisTimer: null,
  designTypeAnalysisGeneration: 0,
  designTriggerEditorId: null,
  designTriggerAnalysis: null,
  designTriggerAnalysisDefinition: null,
  designTriggerAnalysisError: null,
  designTriggerAnalysisLoading: false,
  designTriggerAnalysisTimer: null,
  designTriggerAnalysisGeneration: 0,
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
  dependencyImpactRootId: null,
  dependencyImpactLoading: false,
  dependencyHistoryGroupId: null,
  pendingDesignDeletion: null,
  toastTimer: null,
  canvasResizeFrame: null,
  preferenceTimer: null,
  navigationGeneration: 0,
  restoringNavigation: false,
};
let inspectorDataVisibilityTimer = null;
let inspectorDataPaneTransitionTimer = null;
inspectorPreferenceReady = true;

const workspaceOperations = createWorkspaceOperationController();
const runtimeRequests = createLatestRequestController();
const connectionRequests = createLatestRequestController();
const workspaceListRequests = createLatestRequestController();
const changeCues = createTransientCueManager({ root: document });
const changeTransitions = createChangeTransitionManager({ root: document });
const migrationReview = createMigrationReviewController({
  api,
  elements: {
    dialog: elements.migrationDialog,
    body: elements.migrationReviewBody,
    status: elements.migrationStatus,
    refresh: elements.refreshMigrationButton,
    resolve: elements.resolveMigrationButton,
    apply: elements.applyMigrationButton,
  },
  getContext: () => ({ workspace: state.activeWorkspace, design: state.design }),
  reloadWorkspace: () => loadActiveWorkspace({ clearConflictOnSuccess: true }),
  confirm: askConfirmation,
  notify: showToast,
  notifyError: errorToast,
});
const relationDataSource = createRelationDataSource({
  api,
  getWorkspace: () => state.activeWorkspace,
});
const sqlConsole = createSqlConsole({
  api,
  draft: elements.sqlDraft,
  runButton: elements.runSqlButton,
  cancelButton: elements.cancelSqlButton,
  editorStatus: elements.sqlEditorStatus,
  results: elements.sqlResults,
  getWorkspace: () => state.activeWorkspace,
  showToast,
  onError: errorToast,
});
const inspectorSqlConsole = createSqlConsole({
  api,
  draft: elements.inspectorSqlDraft,
  runButton: elements.runInspectorSql,
  cancelButton: elements.cancelInspectorSql,
  editorStatus: elements.inspectorSqlStatus,
  results: elements.inspectorSqlResults,
  getWorkspace: () => state.activeWorkspace,
  showToast,
  onError: errorToast,
  onRunStart: showInspectorSqlResults,
});

function showDesignChangeTargets(targets) {
  changeCues.clear();
  const visibleChanges = (targets || []).filter(target => (
    target.operation === "add" || target.operation === "update"
  ));
  if (visibleChanges.length) changeCues.show(visibleChanges);
}

function showDesignChanges(beforeContent, afterContent) {
  const targets = designChangeTargets(beforeContent, afterContent);
  showDesignChangeTargets(targets);
  return targets;
}

function changeElement(target, { preferInspector = false } = {}) {
  const candidates = resolveChangeTargetElements(document, target);
  if (preferInspector) {
    const inspectorCandidates = candidates.filter(item => item.closest("#inspector, .inspector"));
    const visibleInspector = inspectorCandidates.find(item => elementFullyVisible(item, { root: document }));
    if (visibleInspector) return { element: visibleInspector, onScreen: true };
    if (inspectorCandidates.length) return { element: inspectorCandidates[0], onScreen: false };
  }
  const onScreen = candidates.find(item => elementFullyVisible(item, { root: document }));
  if (onScreen) return { element: onScreen, onScreen: true };
  return {
    element: candidates.find(item => !item.closest("#tables-layer"))
      || candidates[0]
      || null,
    onScreen: false,
  };
}

function scrollChangeElementIntoView(target, options = {}) {
  const current = changeElement(target, options).element;
  current?.scrollIntoView?.({ behavior: "auto", block: "center", inline: "nearest" });
  return current;
}

function setViewFilterState(filter) {
  state.viewFilter = filter;
  document.querySelectorAll("[data-view-filter]").forEach(item => {
    const active = item.dataset.viewFilter === filter;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function focusChangedViewState(target) {
  setLayerState("views");
  const view = allViews(state.catalog).find(item => item.designId === target.objectId) || null;
  if (!view) return null;
  elements.viewsSearch.value = "";
  setViewFilterState("all");
  commitWorkspaceState({ selectedViewId: catalogViewId(view) }, { render: false, canvas: false });
  state.selectedViewOutputOrdinal = null;
  return view;
}

function revealChangedView(target, presentation) {
  focusChangedViewState(target);
  renderViews();
  syncWorkspaceNavigation("replace");
  window.requestAnimationFrame(() => scrollChangeElementIntoView(target));
  return presentation;
}

function revealChangedSchema(target, presentation) {
  setLayer("tables", { historyMode: "replace" });
  const table = state.catalog?.tables.find(item => (
    item.designId === target.objectId
    || item.designId === target.fallbackId
    || item.name === target.relationName
    || item.columns.some(column => column.designId === target.objectId)
  )) || null;
  if (!table) return presentation;
  const wasRestoring = state.restoringNavigation;
  state.restoringNavigation = true;
  try {
    canvas.select(table, { focus: true, notify: true });
  } finally {
    state.restoringNavigation = wasRestoring;
  }
  syncWorkspaceNavigation("replace");
  scrollChangeElementIntoView(target, { preferInspector: true });
  window.requestAnimationFrame(() => {
    const current = changeElement(target, { preferInspector: true });
    if (!current.onScreen) scrollChangeElementIntoView(target, { preferInspector: true });
  });
  return presentation;
}

function revealDesignChanges(targets, { phase = "after" } = {}) {
  const presentation = designChangePresentation(targets);
  const target = designChangeRevealTarget(targets, phase);
  if (!target) return presentation;
  if (phase === "after" && target.scope === "views") {
    return revealChangedView(target, presentation);
  }
  if (
    phase === "after"
    && target.scope === "schema"
    && target.kind === "column"
    && target.fields?.includes("dataType")
  ) return revealChangedSchema(target, presentation);
  const current = changeElement(target);
  if (current.onScreen) {
    return presentation;
  }
  if (current.element && !current.element.closest("#tables-layer")) {
    scrollChangeElementIntoView(target);
    return presentation;
  }
  if (target.kind === "routine") {
    elements.functionsSearch.value = "";
    renderFunctionsBrowser();
    openDialog(elements.functionsDialog);
    scrollChangeElementIntoView(target);
    return presentation;
  }
  if (target.kind === "type") {
    elements.typesSearch.value = "";
    state.typeFilter = "all";
    renderTypesBrowser();
    openDialog(elements.typesDialog);
    scrollChangeElementIntoView(target);
    return presentation;
  }
  if (target.scope === "views") {
    return revealChangedView(target, presentation);
  }
  if (target.scope !== "schema") return presentation;
  return revealChangedSchema(target, presentation);
}

function designChangeRevealTarget(targets, phase = "after") {
  const changes = (targets || []).filter(target => target.operation !== "related");
  return phase === "before"
    ? changes.find(item => item.operation === "remove") || changes.find(item => item.operation === "update") || null
    : changes.find(item => item.operation === "add") || changes.find(item => item.operation === "update") || null;
}

async function prepareDesignChangeTransition(targets, { reveal = true } = {}) {
  changeCues.clear();
  const removals = (targets || []).filter(target => target.operation === "remove");
  if (reveal && removals.length) revealDesignChanges(removals, { phase: "before" });
  return changeTransitions.prepare(targets);
}

function completeDesignChangeTransition(targets, prepared, { reveal = true } = {}) {
  if (reveal) {
    const surviving = (targets || []).filter(target => (
      target.operation === "add" || target.operation === "update"
    ));
    if (surviving.length) revealDesignChanges(surviving, { phase: "after" });
  }
  changeTransitions.reflow(prepared);
  showDesignChangeTargets(targets);
}

const canvas = new CatalogCanvas({
  canvas: elements.canvas,
  stage: elements.canvasStage,
  layer: elements.tablesLayer,
  lines: elements.relationshipLines,
  zoomOutput: elements.zoomOutput,
  getViewportInsets: () => {
    const paneVisible = inspectorPane.containerState() === "expanded" || inspectorPane.containerState() === "minimized";
    const mobile = window.matchMedia("(max-width: 680px)").matches;
    return { right: paneVisible && !mobile ? elements.inspector.getBoundingClientRect().width + 20 : 20 };
  },
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

function selectedViewAnalysisContext(view = currentCatalogView()) {
  if (!isDesignWorkspace() || !state.activeWorkspace || !state.design || !view?.designId) return null;
  return {
    key: [
      state.activeWorkspace.id,
      view.designId,
      viewAnalysisContextSignature(state.design.content),
    ].join(":"),
    workspaceId: state.activeWorkspace.id,
    view,
  };
}

const viewAnalysis = createViewAnalysisController({
  keyOf: context => context.key,
  load: (context, { signal }) => api.analyzeDesignView(context.workspaceId, {
    viewId: context.view.designId,
    name: context.view.name,
    definition: context.view.queryDefinition,
  }, { signal }),
  onChange: () => {
    if (state.activeLayer === "views") renderSelectedViewDetail();
  },
});

function syncViewAnalysisSelection({ force = false, notify = false } = {}) {
  const context = selectedViewAnalysisContext();
  return viewAnalysis.select(context, { force, notify });
}

function workspaceCanvasPositions(metadata = {}) {
  if (metadata.canvasPositions) return metadata.canvasPositions;
  if (state.catalog?.source === "design" && state.design && state.designLayout) {
    return designPositions(state.design, state.designLayout);
  }
  return canvas.getPositions();
}

function notifyWorkspaceTransition(transition, metadata = {}) {
  if (transition.changed.selectedViewId) state.selectedViewOutputOrdinal = null;
  const catalogSurfaceChanged = transition.changed.catalog || transition.changed.designLayout;
  if (metadata.canvas !== false && (catalogSurfaceChanged || metadata.canvasPositions)) {
    if (state.catalog && metadata.canvasMode === "positions") {
      canvas.setPositions(workspaceCanvasPositions(metadata));
    } else if (state.catalog) {
      canvas.setCatalog(state.catalog, workspaceCanvasPositions(metadata), state.selectedTableId);
    } else canvas.clear();
  }
  void syncViewAnalysisSelection({ notify: false });
  if (metadata.render === false) {
    updateHeader();
    return;
  }
  renderConflict();
  renderCatalogSurfaces();
  renderCatalogState();
  updateHeader();
}

const commitWorkspaceState = createWorkspaceStateCommitter({
  state,
  projectDesign: designToCatalog,
  notify: notifyWorkspaceTransition,
});

function beginWorkspaceMutation() {
  const operation = workspaceOperations.beginMutation();
  if (operation.interruptedRead) {
    state.catalogLoading = false;
    canvas.setInteractive(true);
    renderCatalogState();
    updateHeader();
  }
  return operation;
}

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
  return workspace.name;
}

function workspaceTargetLabel(workspace) {
  if (!workspace.connectionId) return "Local design · no database";
  const connection = connectionById(workspace.connectionId)?.name || workspace.connectionId;
  return `PostgreSQL · ${connection} · ${workspace.database}.${workspace.namespace}`;
}

function isDesignWorkspace(workspace = state.activeWorkspace) {
  return Boolean(workspace);
}

function currentCatalogTable() {
  return selectedCatalogTable(state.catalog, state.selectedTableId);
}

function currentCatalogView() {
  return selectedCatalogView(state.catalog, state.selectedViewId);
}

function selectedDesignTable() {
  if (!isDesignWorkspace() || !state.design || !state.selectedTableId) return null;
  return state.design.content.tables.find(table => table.id === state.selectedTableId) || null;
}

function updateDesignControls() {
  const designWorkspace = isDesignWorkspace();
  const busy = state.catalogLoading || state.designSubmitting || state.historySubmitting || state.layoutConflict || state.inspectorTableEditorDirty;
  const selected = selectedDesignTable();
  const hasTargetKey = state.design?.content.tables.some(table => (
    table.keys.some(key => key.kind === "primary" || key.kind === "unique")
  ));
  elements.createTableButton.disabled = !designWorkspace || busy;
  elements.createRelationshipButton.disabled = !designWorkspace || busy || !hasTargetKey;
  elements.createRelationshipButton.classList.toggle("active", state.relationshipAuthoring);
  elements.createRelationshipButton.setAttribute("aria-pressed", state.relationshipAuthoring ? "true" : "false");
  elements.createRelationshipButton.title = state.relationshipAuthoring ? "Cancel relationship selection" : "Create relationship";
  elements.createKeyButton.disabled = !designWorkspace || busy || !state.design?.content.tables.length;
  elements.createKeyButton.classList.toggle("active", state.keyAuthoring);
  elements.createKeyButton.setAttribute("aria-pressed", state.keyAuthoring ? "true" : "false");
  elements.createKeyButton.title = state.keyAuthoring ? "Cancel key selection" : "Create primary or unique key";
  elements.createIndexButton.disabled = !designWorkspace || busy || !state.design?.content.tables.length;
  elements.createIndexButton.classList.toggle("active", state.indexAuthoring);
  elements.createIndexButton.setAttribute("aria-pressed", state.indexAuthoring ? "true" : "false");
  elements.createIndexButton.title = state.indexAuthoring ? "Cancel index selection" : "Create index";
  elements.deleteTableButton.disabled = !selected || busy;
  elements.createViewButton.disabled = !designWorkspace || busy;
  elements.createFunctionButton.disabled = !designWorkspace || busy;
  elements.typesButton.disabled = !designWorkspace || busy;
  elements.createTypeButton.disabled = !designWorkspace || busy;
  elements.createTriggerButton.disabled = !designWorkspace || busy || !(
    state.design?.content.tables.length || state.design?.content.views.length
  );
  elements.undoDesignButton.disabled = !designWorkspace || busy || !state.designHistory?.canUndo;
  elements.redoDesignButton.disabled = !designWorkspace || busy || !state.designHistory?.canRedo;
  elements.resetDesignButton.disabled = !designWorkspace || busy || !state.designHistory?.canResetToBaseline;
  elements.undoDesignButton.title = state.designHistory?.undo
    ? `Undo: ${state.designHistory.undo.title}${state.designHistory.undo.crossesBaseline ? " · crosses the PostgreSQL baseline" : ""}`
    : "Nothing to undo";
  elements.redoDesignButton.title = state.designHistory?.redo
    ? `Redo: ${state.designHistory.redo.title}${state.designHistory.redo.crossesBaseline ? " · crosses the PostgreSQL baseline" : ""}`
    : "Nothing to redo";
  elements.resetDesignButton.title = state.designHistory?.canResetToBaseline
    ? `Reset desired design to ${state.designHistory.baseline.label}`
    : state.designHistory?.resetBlockedReason || "Design already matches its baseline";
  elements.undoDesignButton.setAttribute("aria-label", elements.undoDesignButton.title);
  elements.redoDesignButton.setAttribute("aria-label", elements.redoDesignButton.title);
  elements.resetDesignButton.setAttribute("aria-label", elements.resetDesignButton.title);
}

function workspaceStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function currentWorkspaceNavigation() {
  const table = currentCatalogTable();
  const view = currentCatalogView();
  return {
    workspaceId: state.activeWorkspace?.id || null,
    layer: state.activeLayer,
    tableId: state.activeLayer === "tables" ? table?.designId || null : null,
    table: state.activeLayer === "tables" ? table?.name || null : null,
    viewId: state.activeLayer === "views" ? view?.designId || null : null,
    view: state.activeLayer === "views" ? view?.name || null : null,
    viewKind: state.activeLayer === "views" ? view?.catalogKind || null : null,
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
      const table = navigatedCatalogTable(state.catalog, navigation);
      if (table) canvas.select(table, { notify: true });
      else {
        canvas.clearSelection();
        selectTable(null, { historyMode: null });
      }
      if (table) {
        const preferences = readWorkspacePreferences(workspaceStorage(), state.activeWorkspace?.id);
        if (preferences.inspector) inspectorPane.setState(preferences.inspector);
      }
    } else if (navigation.layer === "views") {
      const view = navigatedCatalogView(state.catalog, navigation);
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
  const designWorkspace = isDesignWorkspace();
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
    else if (state.catalogLoading) elements.runtimeStatus.textContent = designWorkspace ? "Loading saved design" : "Loading live catalog";
    else elements.runtimeStatus.textContent = `Ready · ${state.readiness.persistence}`;
  }
  elements.saveLayoutButton.disabled = !state.catalog || state.catalogLoading || state.layoutSaving || state.layoutConflict;
  elements.refreshCatalogButton.disabled = state.catalogLoading
    || state.designSubmitting
    || state.historySubmitting
    || workspaceOperations.isMutating();
  elements.refreshCatalogButton.title = designWorkspace ? "Refresh saved design" : "Refresh live catalog";
  elements.refreshCatalogButton.setAttribute("aria-label", elements.refreshCatalogButton.title);
  elements.refreshViewsButton.disabled = state.catalogLoading;
  elements.createViewButton.disabled = !designWorkspace || state.catalogLoading || state.designSubmitting || state.layoutConflict;
  elements.reloadConflictButton.disabled = state.catalogLoading;
  elements.applyConnectionLayoutButton.disabled = state.catalogLoading;
  updateDesignControls();
  updateInspectorTableActions();
  elements.downloadCatalogButton.textContent = designWorkspace ? "Download desired design JSON" : "Download live catalog JSON";
  elements.exportDesignSqlButton.disabled = !designWorkspace || !state.design || state.catalogLoading;
  elements.reviewMigrationButton.disabled = !migrationReview.available()
    || state.catalogLoading
    || state.designSubmitting
    || state.historySubmitting
    || state.inspectorTableEditorDirty;
  elements.reviewMigrationButton.title = migrationReview.available()
    ? "Compare this saved design with its live PostgreSQL target"
    : "Migration review requires a database-backed design workspace";
  elements.inspectorEyebrow.textContent = designWorkspace ? "Edit designed table" : "Read-only table inspector";
  elements.inspectorEmptyCopy.textContent = designWorkspace
    ? "Select a designed table to inspect its columns, constraints, indexes, and relationships."
    : "Select a live table to inspect columns, constraints, indexes, triggers, and relationships.";
  elements.canvas.setAttribute("aria-label", designWorkspace ? "Desired schema table diagram canvas" : "Live table diagram canvas");
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
    const card = stateCard("WS", "Open a schema workspace", "Create a local design or create an editable workspace from PostgreSQL.", "Open workspaces", openWorkspaces);
    const connectionAction = element("button", { className: "ui-button compact", type: "button", text: "Manage connections" });
    connectionAction.addEventListener("click", openConnections);
    card.append(connectionAction);
    elements.catalogState.append(card);
    return;
  }
  if (state.catalogLoading) {
    elements.catalogState.append(stateCard("…", isDesignWorkspace() ? "Loading saved design" : "Loading live catalog", workspaceLabel(state.activeWorkspace), null, null, true));
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
    const reload = element("button", { className: "ui-button compact", type: "button", text: isDesignWorkspace() ? "Reload saved design and layout" : "Reload live catalog and server layout" });
    reload.addEventListener("click", () => loadActiveWorkspace());
    panel.append(reload);
    elements.catalogState.append(panel);
    return;
  }
  if (state.catalog && !state.catalog.tables.length) {
    if (isDesignWorkspace()) {
      elements.catalogState.append(stateCard("+", "Start with a table", "This design is empty. Add a table and its initial columns; Schemii will save the desired schema independently of PostgreSQL.", "Create table", () => openDesignTableEditor()));
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
  const request = runtimeRequests.begin();
  state.runtimeError = null;
  try {
    const [session, readiness] = await Promise.all([
      api.session({ signal: request.signal }),
      api.readiness({ signal: request.signal }),
    ]);
    if (!request.isCurrent()) return;
    state.session = session;
    state.readiness = readiness;
  } catch (error) {
    if (!request.isCurrent()) return;
    state.runtimeError = error;
    state.session = null;
    state.readiness = null;
  } finally {
    const current = request.isCurrent();
    request.finish();
    if (current) updateHeader();
  }
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
  const request = connectionRequests.begin();
  state.connectionsLoading = true;
  state.connectionsError = null;
  renderConnections();
  try {
    const previousConnections = new Map(state.connections.map(connection => [connection.id, connection]));
    const connections = await api.listConnections({ signal: request.signal });
    if (!request.isCurrent()) return;
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
      await loadActiveWorkspace();
    }
    if (!request.isCurrent()) return;
    state.connectionsLoaded = true;
  } catch (error) {
    if (!request.isCurrent()) return;
    state.connectionsError = error;
    state.connectionsLoaded = false;
  } finally {
    const current = request.isCurrent();
    request.finish();
    if (current) {
      state.connectionsLoading = false;
      renderConnections();
      renderWorkspaceConnectionOptions();
      renderSqlTarget();
    }
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
    if (testState?.loading) copy.append(element("p", { className: "connection-test", text: "Testing this PostgreSQL connection…" }));
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
      await loadActiveWorkspace();
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
      await loadActiveWorkspace();
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

function deletionLabel(node) {
  const labels = {
    type: "type",
    table: "table",
    column: "column",
    key: "key",
    check: "check",
    index: "index",
    relationship: "relationship",
    routine: "routine",
    view: "view",
    trigger: "trigger",
  };
  return labels[node.kind] || "object";
}

function deletionConfirmation(node, callback) {
  askConfirmation({
    title: `Delete ${deletionLabel(node)}`,
    message: `Remove “${node.name}” from the desired design? ${node.consequence} PostgreSQL is not changed until this design is applied as a migration.`,
    label: `Delete ${deletionLabel(node)}`,
    callback,
  });
}

function impactError(impact) {
  const count = impact.dependents.length;
  return new Error(
    `“${impact.target.name}” is still used by ${count} designed object${count === 1 ? "" : "s"}. Delete or update the affected objects shown below first.`,
  );
}

function navigateToImpactNode(node) {
  elements.dependencyImpactDialog.close();
  if (["table", "column", "key", "check", "index", "relationship", "trigger"].includes(node.kind) && node.tableName) {
    setLayer("tables", { historyMode: "push" });
    selectTable(node.tableName);
    if (node.kind === "key") {
      const key = designKeyById(node.tableId, node.objectId);
      if (key) openDesignKeyEditor({ tableId: node.tableId, keyId: node.objectId, columnIds: key.columnIds });
    }
    if (node.kind === "check") openDesignCheckEditor({ tableId: node.tableId, checkId: node.objectId });
    if (node.kind === "index") openDesignIndexEditor({ tableId: node.tableId, indexId: node.objectId });
    if (node.kind === "trigger") openDesignTriggerEditor(node.objectId, node.tableName);
    if (node.kind === "relationship") {
      const relationship = designRelationshipById(node.objectId);
      if (relationship) openDesignRelationshipEditor(relationshipDraftFromExisting(state.design.content, relationship), { relationshipId: relationship.id });
    }
    return;
  }
  if (node.kind === "view") {
    const view = allViews(state.catalog).find(item => item.designId === node.objectId);
    if (view) {
      setLayer("views", { historyMode: null });
      selectView(view);
    }
    return;
  }
  if (node.kind === "routine") openDesignRoutineEditor(node.objectId);
  if (node.kind === "type") openDesignTypeEditor(node.objectId);
}

function renderImpactBranch(node) {
  const branch = element("div", { className: "dependency-impact-branch", dataset: { impactObjectId: node.objectId } });
  const children = element("div", { className: "dependency-impact-children" });
  children.hidden = true;
  const toggle = element("button", {
    className: "dependency-impact-toggle",
    attrs: {
      type: "button",
      "aria-expanded": "false",
      "aria-label": node.children.length ? `Show objects affected by ${node.name}` : `${node.name} has no nested dependencies`,
    },
  }, [element("span", { text: node.children.length ? "›" : "·" })]);
  toggle.disabled = !node.children.length;
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(expanded));
    children.hidden = !expanded;
  });
  const select = element("button", { className: "dependency-impact-select", attrs: { type: "button" } }, [
    element("span", {}, [
      element("small", { text: node.kind }),
      element("strong", { text: node.name, attrs: { title: node.name } }),
    ]),
    element("code", { text: node.context || node.consequence, attrs: { title: node.consequence } }),
  ]);
  select.addEventListener("click", () => navigateToImpactNode(node));
  const remove = createIconButton({
    icon: "delete",
    label: `Delete ${node.kind} ${node.name}`,
    tooltip: `Delete ${node.name}`,
    className: "compact danger dependency-impact-delete",
  });
  remove.addEventListener("click", () => {
    if (node.children.length) {
      toggle.setAttribute("aria-expanded", "true");
      children.hidden = false;
      branch.classList.remove("is-blocked");
      void branch.offsetWidth;
      branch.classList.add("is-blocked");
      replace(elements.dependencyImpactStatus, errorPanel(new Error(`Resolve the nested dependencies under “${node.name}” first.`)));
      return;
    }
    requestDesignObjectDeletion(node.objectId, { dependencyLeaf: node });
  });
  const row = element("div", { className: "dependency-impact-row" }, [toggle, select, remove]);
  for (const child of node.children) children.append(renderImpactBranch(child));
  branch.append(row, children);
  return branch;
}

function renderDependencyImpact(impact) {
  elements.dependencyImpactTitle.textContent = `Resolve dependencies for ${impact.target.name}`;
  elements.dependencyImpactCopy.textContent = impactError(impact).message;
  replace(elements.dependencyImpactStatus);
  replace(elements.dependencyImpactTree, ...impact.dependents.map(renderImpactBranch));
}

function newDesignHistoryGroupId() {
  if (typeof crypto.randomUUID === "function") {
    return `dgrp_${crypto.randomUUID().replaceAll("-", "")}`;
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `dgrp_${[...bytes].map(value => value.toString(16).padStart(2, "0")).join("")}`;
}

async function loadDependencyImpact(objectId, { open = true } = {}) {
  if (!state.activeWorkspace || !state.design || state.dependencyImpactLoading) return null;
  state.dependencyImpactLoading = true;
  try {
    const impact = await api.getDesignDeletionImpact(state.activeWorkspace.id, objectId);
    if (impact.designRevision !== state.design.revision) {
      await loadActiveDesign({ clearConflictOnSuccess: true });
      throw new Error("The design changed while deletion dependencies were being checked. Review the refreshed design and try again.");
    }
    if (impact.blocked) {
      state.dependencyImpactRootId = objectId;
      state.dependencyHistoryGroupId ||= newDesignHistoryGroupId();
      renderDependencyImpact(impact);
      if (open) openDialog(elements.dependencyImpactDialog);
    }
    return impact;
  } finally {
    state.dependencyImpactLoading = false;
  }
}

function syncDraftAfterDeletedObject(result) {
  if (!state.inspectorTableEditorDirty) return;
  if (result.kind === "column") {
    const row = elements.inspectorDesignColumns.querySelector(`[data-design-column-id="${CSS.escape(result.object.id)}"]`);
    row?.__designTypeSelector?.destroy();
    row?.remove();
    updateInspectorColumnCount();
  }
  if (result.kind === "key" && result.object.kind === "primary") {
    const ids = new Set(result.object.columnIds);
    for (const row of elements.inspectorDesignColumns.children) {
      if (ids.has(row.dataset.designColumnId)) row.querySelector("[data-design-column-primary]").checked = false;
    }
  }
}

async function persistDesignObjectDeletion(node, { historyGroupId = null } = {}) {
  if (!await flushLayoutBeforeTransition()) return;
  const result = deleteDesignObject(state.design.content, node.objectId);
  state.designSubmitting = true;
  updateDesignControls();
  try {
    const selectedTableId = result.kind === "table" && state.selectedTableId === result.object.id
      ? null
      : state.selectedTableId;
    const selectedViewId = result.kind === "view" && state.selectedViewId === result.object.id
      ? null
      : state.selectedViewId;
    const design = await replaceActiveDesign(result.content, {
      selectedTableId,
      selectedViewId,
      historyGroupId,
      revealChanges: true,
    });
    if (!design) return;
    syncDraftAfterDeletedObject(result);
    showToast(`Deleted ${node.name} from design revision ${design.revision}.`);
    if (state.dependencyImpactRootId && elements.dependencyImpactDialog.open) {
      const rootId = state.dependencyImpactRootId;
      const refreshed = await loadDependencyImpact(rootId, { open: false });
      if (refreshed && !refreshed.blocked) {
        const pending = state.pendingDesignDeletion;
        elements.dependencyImpactDialog.close();
        state.dependencyImpactRootId = null;
        state.pendingDesignDeletion = null;
        requestDesignObjectDeletion(rootId, pending?.objectId === rootId ? pending.options : {});
      }
    }
  } catch (error) {
    if (elements.dependencyImpactDialog.open) replace(elements.dependencyImpactStatus, conflictPanel(error));
    else errorToast(error);
  } finally {
    state.designSubmitting = false;
    updateHeader();
  }
}

async function requestDesignObjectDeletion(objectId, {
  onConfirmed = null,
  statusTarget = null,
  dependencyLeaf = null,
  historyGroupId = state.dependencyHistoryGroupId,
} = {}) {
  if (!objectId || state.designSubmitting) return;
  try {
    const impact = await loadDependencyImpact(objectId, { open: !dependencyLeaf });
    if (!impact) return;
    if (impact.blocked) {
      if (!dependencyLeaf) {
        state.pendingDesignDeletion = {
          objectId,
          options: {
            onConfirmed,
            statusTarget,
            historyGroupId: historyGroupId || state.dependencyHistoryGroupId,
          },
        };
      }
      if (statusTarget) replace(statusTarget, errorPanel(impactError(impact)));
      if (dependencyLeaf) renderDependencyImpact(impact);
      return;
    }
    const node = impact.target;
    deletionConfirmation(node, async () => {
      if (onConfirmed) await onConfirmed(node);
      else await persistDesignObjectDeletion(node, {
        historyGroupId: historyGroupId || state.dependencyHistoryGroupId,
      });
    });
  } catch (error) {
    if (statusTarget) replace(statusTarget, errorPanel(error));
    else if (elements.dependencyImpactDialog.open) replace(elements.dependencyImpactStatus, conflictPanel(error));
    else errorToast(error);
  }
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
  const request = workspaceListRequests.begin();
  state.workspacesLoading = true;
  state.workspacesError = null;
  renderWorkspaces();
  try {
    const workspaces = await api.listWorkspaces({ signal: request.signal });
    if (!request.isCurrent()) return;
    state.workspaces = workspaces;
    state.workspacesLoaded = true;
  } catch (error) {
    if (!request.isCurrent()) return;
    state.workspacesError = error;
    state.workspacesLoaded = false;
  } finally {
    const current = request.isCurrent();
    request.finish();
    if (current) {
      state.workspacesLoading = false;
      renderWorkspaces();
    }
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
  replace(elements.workspaceConnection, element("option", { text: "Select a PostgreSQL connection", attrs: { value: "" } }));
  for (const connection of state.connections) elements.workspaceConnection.append(element("option", { text: `${connection.name} · ${connection.database}`, attrs: { value: connection.id } }));
  if (state.connections.some(connection => connection.id === selected)) elements.workspaceConnection.value = selected;
  updateWorkspaceDatabase();
}

function updateWorkspaceDatabase() {
  const connection = connectionById(elements.workspaceConnection.value);
  elements.workspaceDatabase.textContent = connection ? connection.database : "Select a connection";
}

function updateWorkspaceMode() {
  const mode = elements.workspaceMode.value;
  const targeted = mode === "import";
  for (const field of elements.workspaceTargetFields) field.hidden = !targeted;
  elements.workspaceConnection.required = targeted;
  elements.workspaceNamespace.required = targeted;
  elements.workspaceFormCopy.textContent = targeted
    ? "Create an editable workspace from the selected PostgreSQL namespace. Reads and migrations use the permissions granted to this connection."
    : "Create an editable workspace that exists only in Schemii. It does not read from or write to PostgreSQL.";
  elements.createWorkspaceButton.textContent = mode === "import"
    ? "Create database workspace"
    : "Create empty design";
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
      element("small", {
        text: workspace.importSummary
          ? `${workspace.importSummary.complete ? "Complete import" : `${workspace.importSummary.issues.length} import ${workspace.importSummary.issues.length === 1 ? "note" : "notes"}`} · revision ${workspace.revision} · updated ${formatTimestamp(workspace.updatedAt)}`
          : `revision ${workspace.revision} · updated ${formatTimestamp(workspace.updatedAt)}`,
      }),
    );
    const actions = element("div", { className: "manager-actions ui-action-group end wrap" });
    const open = element("button", { className: "ui-button compact", type: "button", text: current ? "Reload" : "Open" });
    open.addEventListener("click", async () => {
      if (await openWorkspace(workspace)) elements.workspacesDialog.close();
    });
    const remove = element("button", { className: "ui-button compact danger-text", type: "button", text: "Delete" });
    remove.addEventListener("click", () => confirmDeleteWorkspace(workspace));
    actions.append(open, remove);
    card.append(copy);
    if (workspace.importSummary?.issues?.length) {
      const review = element("details", { className: "workspace-import-review" });
      review.append(element("summary", {
        text: `Review ${workspace.importSummary.issues.length} import ${workspace.importSummary.issues.length === 1 ? "note" : "notes"}`,
      }));
      const list = element("ul");
      for (const issue of workspace.importSummary.issues) {
        const item = element("li");
        item.append(
          element("strong", { text: issue.objectName }),
          element("span", { text: issue.reason }),
        );
        list.append(item);
      }
      review.append(list);
      card.append(review);
    }
    card.append(actions);
    elements.workspacesList.append(card);
  }
}

async function submitWorkspace(event) {
  event.preventDefault();
  if (state.workspaceSubmitting) return;
  const mode = elements.workspaceMode.value;
  const targeted = mode === "import";
  const connection = connectionById(elements.workspaceConnection.value);
  const name = elements.workspaceName.value.trim();
  const namespace = elements.workspaceNamespace.value;
  if (!name) {
    replace(elements.workspaceFormStatus, element("span", { text: "Enter a workspace name." }));
    return;
  }
  if (targeted && !connection) {
    replace(elements.workspaceFormStatus, element("span", { text: "Select a connection returned by the active API." }));
    return;
  }
  state.workspaceSubmitting = true;
  const dialogGeneration = state.workspaceDialogGeneration;
  elements.createWorkspaceButton.disabled = true;
  replace(elements.workspaceFormStatus, element("span", {
    text: mode === "import"
      ? "Inspecting PostgreSQL and creating a new editable design…"
      : targeted
        ? "Creating and validating the workspace target…"
        : "Creating the local schema design…",
  }));
  try {
    if (!await flushLayoutBeforeTransition()) {
      replace(elements.workspaceFormStatus, element("span", { text: "The current layout must be saved or reloaded before opening another workspace." }));
      return;
    }
    if (dialogGeneration !== state.workspaceDialogGeneration) return;
    const target = targeted ? {
      name,
      connectionId: connection.id,
      database: connection.database,
      namespace,
    } : { name };
    const imported = mode === "import" ? await api.createWorkspaceImport(target) : null;
    const workspace = imported?.workspace || await api.createWorkspace(target);
    state.workspaces.push(workspace);
    state.workspaceActionError = null;
    renderWorkspaces();
    if (dialogGeneration === state.workspaceDialogGeneration) {
      elements.workspaceNamespace.value = "";
      elements.workspaceName.value = "";
      replace(elements.workspaceFormStatus);
      const opened = await openWorkspace(workspace);
      if (opened && dialogGeneration === state.workspaceDialogGeneration && elements.workspacesDialog.open) elements.workspacesDialog.close();
      if (opened && imported) {
        const summary = workspace.importSummary;
        const tables = summary?.importedObjects?.tables ?? 0;
        const notes = summary?.issues?.length ?? 0;
        showToast(`Imported ${tables} ${tables === 1 ? "table" : "tables"} into a new workspace${notes ? ` · ${notes} ${notes === 1 ? "note" : "notes"}` : ""}.`);
      }
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
  workspaceOperations.invalidate();
  viewAnalysis.clear();
  resetLayoutSaveState();
  state.columnOrderModes.clear();
  state.catalogError = null;
  state.catalogLoading = false;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  if (state.preservedLayout?.workspaceId === workspaceId) state.preservedLayout = null;
  cancelColumnAuthoring();
  commitWorkspaceState({
    activeWorkspace: null,
    design: null,
    designLayout: null,
    designHistory: null,
    catalog: null,
    databaseCatalog: null,
    selectedTableId: null,
    selectedViewId: null,
  });
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
      columnOrders: structuredClone(state.activeWorkspace.columnOrders || []),
    };
  }
  workspaceOperations.invalidate();
  viewAnalysis.clear();
  resetLayoutSaveState();
  state.columnOrderModes.clear();
  state.catalogError = null;
  state.catalogLoading = false;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  cancelColumnAuthoring();
  commitWorkspaceState({
    design: null,
    designLayout: null,
    designHistory: null,
    catalog: null,
    databaseCatalog: null,
    selectedTableId: null,
    selectedViewId: null,
  });
}

async function openWorkspace(workspace, { historyMode = "push" } = {}) {
  if (!await flushLayoutBeforeTransition()) return false;
  persistCanvasView();
  workspaceOperations.invalidate();
  viewAnalysis.clear();
  resetLayoutSaveState();
  state.columnOrderModes.clear();
  state.catalogError = null;
  state.layoutConflict = false;
  state.layoutConflictKind = null;
  cancelColumnAuthoring();
  commitWorkspaceState({
    activeWorkspace: workspace,
    design: null,
    designLayout: null,
    designHistory: null,
    catalog: null,
    databaseCatalog: null,
    selectedTableId: null,
    selectedViewId: null,
  });
  syncWorkspaceNavigation(historyMode);
  await loadActiveWorkspace({ clearConflictOnSuccess: true });
  if (state.catalog && state.activeWorkspace?.id === workspace.id) restoreCanvasView(workspace.id);
  return true;
}

async function loadActiveWorkspace(options = {}) {
  return loadActiveDesign(options);
}

async function loadActiveDesign({ clearConflictOnSuccess = false } = {}) {
  if (!isDesignWorkspace()) return;
  const workspaceId = state.activeWorkspace.id;
  const request = workspaceOperations.beginRead();
  state.catalogLoading = true;
  canvas.setInteractive(false);
  state.catalogError = null;
  renderCatalogState();
  updateHeader();
  try {
    const { design, layout, history } = await api.getDesignSnapshot(workspaceId, {
      signal: request.signal,
    });
    if (!request.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    state.catalogError = null;
    state.layoutError = null;
    state.layoutDirty = false;
    if (clearConflictOnSuccess) {
      state.layoutConflict = false;
      state.layoutConflictKind = null;
    }
    commitWorkspaceState({ design, designLayout: layout, designHistory: history, databaseCatalog: null }, {
      canvasPositions: designPositions(design, layout),
    });
    const navigation = readWorkspaceNavigation(window.location.href);
    if (navigation.workspaceId === workspaceId) {
      applyWorkspaceNavigation(navigation);
      syncWorkspaceNavigation("replace");
    }
  } catch (error) {
    if (!request.isCurrent()) return;
    if (error instanceof ApiError && error.code === "request_cancelled") return;
    state.catalogError = error;
  } finally {
    const current = request.isCurrent();
    request.finish();
    if (current) {
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
  const request = workspaceOperations.beginRead();
  state.catalogLoading = true;
  canvas.setInteractive(false);
  state.catalogError = null;
  renderCatalogState();
  updateHeader();
  try {
    const response = await api.getCatalog(workspaceId, { signal: request.signal });
    if (!request.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    const preservedLayout = state.preservedLayout?.workspaceId === workspaceId
      ? state.preservedLayout
      : null;
    const activeWorkspace = preservedLayout
      ? { ...response.workspace, columnOrders: preservedLayout.columnOrders }
      : response.workspace;
    state.workspaces = state.workspaces.map(workspace => (
      workspace.id === response.workspace.id ? activeWorkspace : workspace
    ));
    synchronizeColumnOrderModes(response.catalog, activeWorkspace);
    const catalog = applyColumnDisplayOrders(
      response.catalog,
      activeWorkspace.columnOrders,
      state.columnOrderModes,
    );
    state.catalogError = null;
    state.layoutError = null;
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
    commitWorkspaceState({ activeWorkspace, databaseCatalog: response.catalog, catalog }, {
      canvasPositions: preservedLayout?.positions || response.positions,
    });
    if (preservedLayout) {
      state.preservedLayout = null;
      state.layoutVersion += 1;
      if (!preservedLayoutConflict) state.layoutTimer = window.setTimeout(saveLayout, 550);
    }
    renderWorkspaces();
    const navigation = readWorkspaceNavigation(window.location.href);
    if (navigation.workspaceId === workspaceId) {
      applyWorkspaceNavigation(navigation);
      syncWorkspaceNavigation("replace");
    }
  } catch (error) {
    if (!request.isCurrent()) return;
    if (error instanceof ApiError && error.code === "request_cancelled") return;
    state.catalogError = error;
  } finally {
    const current = request.isCurrent();
    request.finish();
    if (current) {
      state.catalogLoading = false;
      canvas.setInteractive(true);
      renderCatalogState();
      updateHeader();
    }
  }
}

async function refreshCatalog() {
  if (state.catalogLoading || state.designSubmitting || state.historySubmitting || workspaceOperations.isMutating()) return;
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

function synchronizeColumnOrderModes(databaseCatalog = state.databaseCatalog, workspace = state.activeWorkspace) {
  if (!databaseCatalog) {
    state.columnOrderModes.clear();
    return;
  }
  const customTables = new Set(
    (workspace?.columnOrders || []).map(order => order.name),
  );
  const next = new Map();
  for (const table of databaseCatalog.tables) {
    next.set(
      table.name,
      state.columnOrderModes.get(table.name)
        || (customTables.has(table.name) ? "custom" : "database"),
    );
  }
  state.columnOrderModes = next;
}

function customColumnOrder(tableName) {
  return (state.activeWorkspace?.columnOrders || [])
    .find(order => order.name === tableName) || null;
}

function refreshColumnDisplay({ focusSortKey = null } = {}) {
  if (!state.databaseCatalog || !state.activeWorkspace) return;
  const positions = canvas.getPositions();
  const catalog = applyColumnDisplayOrders(
    state.databaseCatalog,
    state.activeWorkspace.columnOrders,
    state.columnOrderModes,
  );
  commitWorkspaceState({ catalog }, { canvasPositions: positions });
  if (focusSortKey) {
    window.requestAnimationFrame(() => {
      elements.inspectorContent.querySelector(
        `.live-column-order-row[data-sort-key="${CSS.escape(focusSortKey)}"] [data-sort-handle]`,
      )?.focus();
    });
  }
}

function setColumnOrderMode(tableName, mode) {
  if (!state.databaseCatalog || !["database", "custom"].includes(mode)) return;
  state.columnOrderModes.set(tableName, mode);
  refreshColumnDisplay();
}

function updateLiveColumnOrder(tableName, columnNames, details = {}) {
  if (!state.databaseCatalog || !state.activeWorkspace) return;
  const table = state.databaseCatalog.tables.find(item => item.name === tableName);
  if (!table || columnNames.length !== table.columns.length) return;
  const activeWorkspace = {
    ...state.activeWorkspace,
    columnOrders: replaceColumnDisplayOrder(
      state.activeWorkspace.columnOrders || [],
      tableName,
      columnNames,
    ),
  };
  commitWorkspaceState({ activeWorkspace }, { render: false, canvas: false });
  state.workspaces = state.workspaces.map(workspace => (
    workspace.id === activeWorkspace.id ? activeWorkspace : workspace
  ));
  state.columnOrderModes.set(tableName, "custom");
  refreshColumnDisplay({
    focusSortKey: details.input === "keyboard" ? details.sortKey : null,
  });
  positionsChanged();
}

function resetLiveColumnOrder(tableName) {
  if (!state.databaseCatalog || !state.activeWorkspace) return;
  const activeWorkspace = {
    ...state.activeWorkspace,
    columnOrders: removeColumnDisplayOrder(
      state.activeWorkspace.columnOrders || [],
      tableName,
    ),
  };
  commitWorkspaceState({ activeWorkspace }, { render: false, canvas: false });
  state.workspaces = state.workspaces.map(workspace => (
    workspace.id === activeWorkspace.id ? activeWorkspace : workspace
  ));
  state.columnOrderModes.set(tableName, "database");
  refreshColumnDisplay();
  positionsChanged();
  showToast(`${tableName} now follows PostgreSQL database order.`);
}

function inspectorDataAvailable(table = currentCatalogTable()) {
  return Boolean(table && state.activeWorkspace?.connectionId && state.activeWorkspace?.namespace);
}

function inspectorDataKey(table) {
  if (!inspectorDataAvailable(table)) return null;
  return `${state.activeWorkspace.id}:${table.namespace || state.activeWorkspace.namespace}:${table.name}:${table.kind || "table"}`;
}

function renderInspectorRows() {
  const showingResults = state.inspectorRowsView === "results";
  elements.inspectorRowsBody.hidden = showingResults;
  elements.inspectorSqlResults.hidden = !showingResults;
  if (!showingResults) renderRelationRows(elements.inspectorRowsBody, {
    page: state.inspectorRows,
    loading: state.inspectorRowsLoading,
    error: state.inspectorRowsError,
  });
  const page = state.inspectorRows;
  elements.inspectorRowsTitle.textContent = showingResults
    ? "Query results"
    : `${currentCatalogTable()?.name || "Table"} rows`;
  elements.inspectorRowsStatus.textContent = showingResults
    ? "Read-only result"
    : page
      ? `${page.rows.length} rows · ${page.columns.length} columns${page.truncated ? " · more available" : ""}`
      : "Up to 100 rows";
  elements.inspectorRowsMore.hidden = showingResults || !page?.nextCursor;
  elements.inspectorRowsMore.disabled = state.inspectorRowsLoading;
  elements.refreshInspectorRows.disabled = state.inspectorRowsLoading;
  elements.openFullRowPreview.disabled = !showingResults && (state.inspectorRowsLoading || !state.inspectorRelation);
  elements.openFullRowPreview.setAttribute("aria-label", showingResults ? "Return to table rows" : "Open full row preview");
  elements.openFullRowPreview.dataset.uiTooltip = showingResults ? "Return to table rows" : "Open full row preview";
  elements.refreshInspectorRows.setAttribute("aria-label", showingResults ? "Run query again" : "Refresh table rows");
  elements.refreshInspectorRows.dataset.uiTooltip = showingResults ? "Run query again" : "Refresh table rows";
}

function showInspectorSqlResults() {
  state.inspectorRowsView = "results";
  state.inspectorMode = "rows";
  renderInspectorMode();
}

function inspectorTransitionDelay(duration) {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : duration;
}

function settleInspectorDataPane(activePane) {
  const rowsActive = activePane === "rows";
  elements.inspectorRowsContent.hidden = !rowsActive;
  elements.inspectorConsoleContent.hidden = rowsActive;
}

function setInspectorDataWorkspaceVisible(visible) {
  if (visible) {
    window.clearTimeout(inspectorDataVisibilityTimer);
    inspectorDataVisibilityTimer = null;
    const opening = elements.inspectorDataWorkspace.hidden;
    elements.inspectorDataWorkspace.hidden = false;
    elements.inspectorDataWorkspace.inert = false;
    elements.mainLayout.classList.add("inspector-data-open");
    if (opening) void elements.inspectorDataWorkspace.offsetWidth;
    elements.inspectorDataWorkspace.classList.add("open");
    return;
  }
  if (elements.inspectorDataWorkspace.hidden) {
    elements.mainLayout.classList.remove("inspector-data-open");
    return;
  }
  if (!elements.inspectorDataWorkspace.classList.contains("open") && inspectorDataVisibilityTimer !== null) return;
  window.clearTimeout(inspectorDataVisibilityTimer);
  elements.inspectorDataWorkspace.inert = true;
  elements.inspectorDataWorkspace.classList.remove("open");
  inspectorDataVisibilityTimer = window.setTimeout(() => {
    inspectorDataVisibilityTimer = null;
    if (elements.inspectorDataWorkspace.classList.contains("open")) return;
    elements.inspectorDataWorkspace.hidden = true;
    elements.mainLayout.classList.remove("inspector-data-open");
    window.clearTimeout(inspectorDataPaneTransitionTimer);
    inspectorDataPaneTransitionTimer = null;
    elements.inspectorDataWorkspace.dataset.activePane = "rows";
    settleInspectorDataPane("rows");
  }, inspectorTransitionDelay(240));
}

function setInspectorDataActivePane(pane) {
  const activePane = pane === "console" ? "console" : "rows";
  const previousPane = elements.inspectorDataWorkspace.dataset.activePane;
  const transitionInProgress = inspectorDataPaneTransitionTimer !== null && previousPane === activePane;
  const transitioning = elements.inspectorDataWorkspace.classList.contains("open")
    && Boolean(previousPane && previousPane !== activePane);
  if (!transitionInProgress) {
    window.clearTimeout(inspectorDataPaneTransitionTimer);
    inspectorDataPaneTransitionTimer = null;
    if (transitioning) {
      elements.inspectorRowsContent.hidden = false;
      elements.inspectorConsoleContent.hidden = false;
      void elements.inspectorDataWorkspace.offsetHeight;
    }
    elements.inspectorDataWorkspace.dataset.activePane = activePane;
    if (transitioning) {
      inspectorDataPaneTransitionTimer = window.setTimeout(() => {
        inspectorDataPaneTransitionTimer = null;
        if (elements.inspectorDataWorkspace.dataset.activePane === activePane) settleInspectorDataPane(activePane);
      }, inspectorTransitionDelay(380));
    } else settleInspectorDataPane(activePane);
  }
  elements.showInspectorRows.setAttribute("aria-expanded", String(activePane === "rows"));
  elements.showInspectorConsole.setAttribute("aria-expanded", String(activePane === "console"));
}

function renderInspectorMode(table = currentCatalogTable()) {
  const available = inspectorDataAvailable(table);
  if (!available) {
    state.inspectorMode = "structure";
    state.inspectorDataMaximized = false;
  }
  elements.inspectorDataToolsButton.disabled = !available;
  const open = available && state.inspectorMode !== "structure";
  elements.inspectorDataToolsButton.title = available
    ? `${open ? "Close" : "Open"} table rows and read-only console`
    : "Rows and console require a PostgreSQL-backed table";
  elements.inspectorDataToolsButton.dataset.uiTooltip = elements.inspectorDataToolsButton.title;
  elements.inspectorDataToolsButton.setAttribute("aria-label", available
    ? `${open ? "Close" : "Open"} table rows and console`
    : "Table rows and console unavailable");
  elements.inspectorDataToolsButton.classList.toggle("active", open);
  elements.inspectorDataToolsButton.setAttribute("aria-pressed", open ? "true" : "false");
  const inspectorHeaderLabel = open
    ? "Close data tools"
    : inspectorPane.state === "expanded"
      ? "Minimize table inspector"
      : "Expand table inspector";
  elements.inspectorToggle.setAttribute("aria-label", inspectorHeaderLabel);
  elements.inspectorToggle.dataset.uiTooltip = `${inspectorHeaderLabel} · right-click ${open ? "maximizes data tools" : "opens data tools"}`;
  setInspectorDataWorkspaceVisible(open);
  if (open) setInspectorDataActivePane(state.inspectorMode);
  elements.showInspectorRows.title = "Switch between table rows and SQL console · right-click to maximize";
  elements.showInspectorConsole.title = "Switch between SQL console and table rows · right-click to maximize";
  elements.maximizeInspectorData.classList.toggle("active", state.inspectorDataMaximized);
  elements.maximizeInspectorData.setAttribute("aria-pressed", String(state.inspectorDataMaximized));
  elements.maximizeInspectorData.setAttribute("aria-label", state.inspectorDataMaximized ? "Restore split view" : "Maximize data tools");
  elements.minimizeInspectorData.setAttribute("aria-label", state.inspectorDataMaximized ? "Restore table inspector" : "Minimize data tools");
  elements.maximizeInspectorData.dataset.uiTooltip = state.inspectorDataMaximized ? "Restore split view" : "Maximize data tools";
  elements.minimizeInspectorData.dataset.uiTooltip = state.inspectorDataMaximized ? "Restore table inspector" : "Minimize data tools";
  elements.minimizeInspectorData.replaceChildren(createIconElement(state.inspectorDataMaximized ? "collapse" : "minimize"));
  elements.mainLayout.classList.toggle("inspector-data-maximized", open && state.inspectorDataMaximized);
  if (open && state.inspectorMode === "rows") {
    renderInspectorRows();
    if (!state.inspectorRowsLoading && !state.inspectorRows && !state.inspectorRowsError) void loadInspectorRows();
  }
}

function syncInspectorDataContext(table) {
  const key = inspectorDataKey(table);
  if (key === state.inspectorDataKey) {
    renderInspectorMode(table);
    return;
  }
  state.inspectorRowsGeneration += 1;
  state.inspectorDataKey = key;
  state.inspectorRelation = null;
  state.inspectorRows = null;
  state.inspectorRowsLoading = false;
  state.inspectorRowsError = null;
  state.inspectorRowsView = "table";
  inspectorSqlConsole.reset(key ? relationDataSource.statement(table.name) : "");
  if (table) elements.inspectorRowsTitle.textContent = `${table.name} rows`;
  renderInspectorMode(table);
}

function setInspectorMode(mode) {
  const table = currentCatalogTable();
  if (mode !== "structure" && !inspectorDataAvailable(table)) {
    showToast("Rows and the console require a PostgreSQL-backed table.", { error: true });
    return;
  }
  state.inspectorMode = mode;
  if (mode === "structure") state.inspectorDataMaximized = false;
  renderInspectorMode(table);
  if (mode === "console") window.requestAnimationFrame(() => elements.inspectorSqlDraft.focus());
}

function closeInspectorDataWorkspace() {
  if (state.inspectorMode === "structure" && !state.inspectorDataMaximized) return;
  state.inspectorMode = "structure";
  state.inspectorDataMaximized = false;
  renderInspectorMode();
}

function toggleInspectorDataPane(pane) {
  if (pane !== "rows" && pane !== "console") return;
  setInspectorMode(state.inspectorMode === pane ? (pane === "rows" ? "console" : "rows") : pane);
}

function setInspectorDataMaximized(maximized) {
  state.inspectorDataMaximized = Boolean(maximized && state.inspectorMode !== "structure");
  renderInspectorMode();
}

function handleInspectorHeaderGesture(button) {
  const dataOpen = state.inspectorMode !== "structure";
  if (button === "left") {
    if (dataOpen) closeInspectorDataWorkspace();
    else inspectorPane.toggleState();
    return;
  }
  if (!inspectorDataAvailable()) {
    showToast("Rows and the console require a PostgreSQL-backed table.", { error: true });
    return;
  }
  if (inspectorPane.state === "minimized") {
    inspectorPane.expand();
    setInspectorMode("rows");
  } else if (dataOpen) setInspectorDataMaximized(true);
  else setInspectorMode("rows");
}

async function loadInspectorRows({ cursor = null } = {}) {
  const table = currentCatalogTable();
  const key = inspectorDataKey(table);
  if (!key || state.inspectorRowsLoading) return;
  const generation = ++state.inspectorRowsGeneration;
  state.inspectorRowsLoading = true;
  state.inspectorRowsError = null;
  renderInspectorRows();
  try {
    const relation = cursor && state.inspectorRelation
      ? state.inspectorRelation
      : await relationDataSource.resolve(table.name, state.catalog?.source === "design" ? null : table.kind);
    if (generation !== state.inspectorRowsGeneration || key !== state.inspectorDataKey) return;
    if (!relation) throw new Error(`${table.name} is not present in the current PostgreSQL target.`);
    const page = await relationDataSource.page(relation, { cursor, pageSize: 100 });
    if (generation !== state.inspectorRowsGeneration || key !== state.inspectorDataKey) return;
    state.inspectorRelation = relation;
    state.inspectorRows = page;
  } catch (error) {
    if (generation === state.inspectorRowsGeneration && key === state.inspectorDataKey) state.inspectorRowsError = error;
  } finally {
    if (generation === state.inspectorRowsGeneration && key === state.inspectorDataKey) {
      state.inspectorRowsLoading = false;
      renderInspectorRows();
    }
  }
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
    onAddKey: desired && table ? () => startKeyAuthoring({ tableId: table.designId }) : null,
    onEditKey: desired ? editDesignKey : null,
    onDeleteKey: desired ? confirmDeleteDesignKey : null,
    onAddCheck: desired && table ? () => openDesignCheckEditor({ tableId: table.designId }) : null,
    onEditCheck: desired ? editDesignCheck : null,
    onDeleteCheck: desired ? confirmDeleteDesignCheck : null,
    onAddIndex: desired && table ? () => startIndexAuthoring({ tableId: table.designId }) : null,
    onEditIndex: desired ? editDesignIndex : null,
    onDeleteIndex: desired ? confirmDeleteDesignIndex : null,
    onAddTrigger: desired && table ? () => openDesignTriggerEditor(null, table.name) : null,
    onEditTrigger: desired ? trigger => openDesignTriggerEditor(trigger.designId) : null,
    onDeleteTrigger: desired ? confirmDeleteDesignTrigger : null,
    onAddRelationship: desired && table ? () => startRelationshipAuthoring() : null,
    onEditRelationship: desired ? editDesignRelationship : null,
    onDeleteRelationship: desired ? confirmDeleteDesignRelationship : null,
    showTableDetails: !desired,
    columnOrderMode: table
      ? state.columnOrderModes.get(table.name) || "database"
      : "database",
    hasCustomColumnOrder: Boolean(table && customColumnOrder(table.name)),
    onColumnOrderModeChange: !desired && table
      ? mode => setColumnOrderMode(table.name, mode)
      : null,
    onColumnOrderChange: !desired && table
      ? (columns, details) => updateLiveColumnOrder(table.name, columns, details)
      : null,
    onResetColumnOrder: !desired && table
      ? () => resetLiveColumnOrder(table.name)
      : null,
    onPreviewRows: !desired && table ? () => openTableRowPreview(table) : null,
  });
  renderInspectorTableEditor(desired ? table : null);
  syncInspectorDataContext(table);
}

function selectTable(value, { historyMode = "push" } = {}) {
  const table = value && typeof value === "object"
    ? state.catalog?.tables.find(item => catalogTableId(item) === catalogTableId(value)) || null
    : state.catalog?.tables.find(item => (
      catalogTableId(item) === value || item.name === value
    )) || null;
  const tableId = catalogTableId(table);
  if (state.inspectorTableEditorDirty && tableId !== state.selectedTableId) {
    const selected = selectedDesignTable();
    showToast("Save or discard the current table changes before selecting another table.");
    if (selected) canvas.select(selected.id, { focus: true });
    inspectorPane.reveal();
    elements.inspectorTableName.focus();
    return;
  }
  if (tableId !== state.selectedTableId) closeInspectorDataWorkspace();
  commitWorkspaceState({ selectedTableId: tableId }, { render: false, canvas: false });
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
  const designWorkspace = isDesignWorkspace();
  const connection = designWorkspace ? null : connectionById(state.activeWorkspace.connectionId);
  if (!designWorkspace && !connection) {
    state.layoutError = new Error("The workspace connection is unavailable. Reload connections before saving this layout");
    renderCatalogState();
    return;
  }
  if (designWorkspace && (!state.design || !state.designLayout)) {
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
  const savePromise = designWorkspace
    ? api.replaceDesignLayout(workspaceId, {
      expectedLayoutRevision: state.designLayout.revision,
      expectedDesignRevision: state.design.revision,
      content: designLayoutContent(state.design, positions, state.designLayout.content.objects),
    })
    : api.updateLayout(workspaceId, {
      expectedRevision: state.activeWorkspace.revision,
      expectedConnectionRevision: connection.revision,
      tables: positions,
      columnOrders: state.activeWorkspace.columnOrders || [],
    });
  state.layoutSavePromise = savePromise;
  try {
    const result = await savePromise;
    if (saveGeneration !== state.layoutSaveGeneration || state.activeWorkspace?.id !== workspaceId) return;
    if (designWorkspace) {
      commitWorkspaceState({ designLayout: result }, { render: false, canvas: false });
    } else {
      commitWorkspaceState({ activeWorkspace: result }, { render: false, canvas: false });
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
  const selectedTable = currentCatalogTable();
  renderActiveInspector(selectedTable);
  if (selectedTable) {
    if (!inspectorPane.available) inspectorPane.reveal();
  } else inspectorPane.setAvailable(false, { reset: true });
  updateDesignControls();
  if (state.activeLayer === "views") renderViews();
  else {
    replace(elements.viewsList);
    renderViewDetail(elements.viewDetail, null);
  }
  if (elements.functionsDialog.open) renderFunctionsBrowser();
  else {
    replace(elements.functionsList);
    const source = state.catalog?.source === "design" ? "designed" : "live";
    elements.functionsCount.textContent = state.catalog ? `${state.catalog.functions.length} ${source} · open to browse` : "No catalog loaded";
  }
  if (elements.typesDialog.open) renderTypesBrowser();
  else {
    replace(elements.typesList);
    elements.typesCount.textContent = state.catalog?.source === "design"
      ? `${state.catalog.types.length} designed · open to browse`
      : "Open a local or imported design";
  }
  if (elements.objectsDialog.open) renderObjectsBrowser();
  else {
    replace(elements.objectsList);
    elements.objectsCount.textContent = state.catalog ? "Open to browse live objects" : "No catalog loaded";
  }
  renderSqlTarget();
}

function selectView(view, { historyMode = "push" } = {}) {
  commitWorkspaceState({ selectedViewId: catalogViewId(view) }, { render: false, canvas: false });
  state.selectedViewOutputOrdinal = null;
  renderViews();
  syncWorkspaceNavigation(historyMode);
}

function renderViews() {
  const desired = state.catalog?.source === "design";
  const view = currentCatalogView();
  elements.viewsSourceLabel.textContent = desired ? "Desired schema" : "Live PostgreSQL";
  elements.createViewButton.hidden = !desired;
  renderViewsList(elements.viewsList, {
    catalog: state.catalog,
    query: elements.viewsSearch.value,
    filter: state.viewFilter,
    selectedName: view?.name || null,
    onSelect: selectView,
  });
  renderSelectedViewDetail();
}

function renderSelectedViewDetail() {
  const desired = state.catalog?.source === "design";
  const view = currentCatalogView();
  if (!desired) {
    delete elements.viewDetail.dataset.analysisKey;
    if (!view) {
      renderDesignViewStory(elements.viewDetail, { view: null, live: true });
      return;
    }
    const key = `${state.activeWorkspace?.id || ""}:${state.catalog?.fingerprint || ""}:${view.catalogKind}:${view.name}`;
    if (state.liveViewLineageKey !== key && !state.liveViewLineageLoading) {
      queueMicrotask(() => loadLiveViewLineage(view, key));
    }
    renderDesignViewStory(elements.viewDetail, {
      view,
      live: true,
      analysis: state.liveViewLineageKey === key ? state.liveViewLineage?.analysis : null,
      loading: state.liveViewLineageLoading,
      error: state.liveViewLineageKey === key ? state.liveViewLineageError : null,
      selectedOutputOrdinal: state.selectedViewOutputOrdinal,
      onSelectOutput: output => {
        state.selectedViewOutputOrdinal = output.ordinal;
        renderSelectedViewDetail();
      },
      onRetry: () => loadLiveViewLineage(view, key, { force: true }),
    });
    return;
  }
  if (!view) {
    delete elements.viewDetail.dataset.analysisKey;
    renderDesignViewStory(elements.viewDetail, { view: null });
    return;
  }
  const context = selectedViewAnalysisContext(view);
  const analysisState = viewAnalysis.snapshot();
  const selectedState = context && analysisState.key === context.key
    ? analysisState
    : { status: "idle", analysis: null, error: null };
  if (
    selectedState.status === "loading"
    && selectedState.analysis
    && elements.viewDetail.dataset.analysisKey === context.key
  ) {
    let status = elements.viewDetail.querySelector(".query-story-refreshing");
    if (!status) {
      status = element("span", {
        className: "query-story-refreshing",
        text: "Refreshing analysis…",
        attrs: { role: "status" },
      });
      elements.viewDetail.querySelector(".query-story-actions")?.prepend(status);
    }
    return;
  }
  renderDesignViewStory(elements.viewDetail, {
    view,
    analysis: selectedState.analysis,
    loading: selectedState.status === "loading",
    error: selectedState.error,
    selectedOutputOrdinal: state.selectedViewOutputOrdinal,
    onSelectOutput: output => {
      state.selectedViewOutputOrdinal = output.ordinal;
      renderSelectedViewDetail();
    },
    onEdit: () => openDesignViewEditor(view.designId),
    onDelete: () => confirmDeleteDesignView(view.designId),
    onRetry: () => syncViewAnalysisSelection({ force: true }),
  });
  if (context) elements.viewDetail.dataset.analysisKey = context.key;
  else delete elements.viewDetail.dataset.analysisKey;
}

function selectedDesignView() {
  if (!isDesignWorkspace() || !state.design || !state.selectedViewId) return null;
  return state.design.content.views.find(view => view.id === state.selectedViewId) || null;
}

async function findLiveRelation(name, kind = null) {
  return relationDataSource.resolve(name, kind);
}

async function loadLiveViewLineage(view, key, { force = false } = {}) {
  if (!state.activeWorkspace?.connectionId || (!force && state.liveViewLineageLoading)) return;
  state.liveViewLineageLoading = true;
  state.liveViewLineageKey = key;
  state.liveViewLineageError = null;
  renderSelectedViewDetail();
  try {
    const relation = await findLiveRelation(view.name, view.catalogKind);
    if (!relation) throw new Error("The selected view is no longer present in PostgreSQL.");
    const lineage = await api.getRelationLineage(state.activeWorkspace.id, relation.ref);
    if (state.liveViewLineageKey === key) state.liveViewLineage = lineage;
  } catch (error) {
    if (state.liveViewLineageKey === key) state.liveViewLineageError = error;
  } finally {
    if (state.liveViewLineageKey === key) {
      state.liveViewLineageLoading = false;
      renderSelectedViewDetail();
    }
  }
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
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
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
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
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
    const design = await replaceActiveDesign(result.content, {
      selectedViewId: result.view.id,
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
  requestDesignObjectDeletion(viewId, { statusTarget: elements.designViewStatus });
}

function renderTypesBrowser() {
  const desired = state.catalog?.source === "design";
  elements.createTypeButton.hidden = !desired;
  const result = renderTypes(
    elements.typesList,
    state.catalog,
    elements.typesSearch.value,
    state.typeFilter,
    {
      onEdit: desired ? designType => {
        elements.typesDialog.close();
        openDesignTypeEditor(designType.designId);
      } : null,
      onDelete: desired ? designType => {
        elements.typesDialog.close();
        confirmDeleteDesignType(designType.designId);
      } : null,
    },
  );
  elements.typesCount.textContent = state.catalog
    ? `${result.shown} shown · ${result.matching} matching · ${result.total} designed`
    : "No design loaded";
}

function renderDesignTypePreview() {
  replace(elements.designTypePreview);
  const definition = elements.designTypeDefinition.value.trim();
  if (!definition) {
    elements.designTypePreview.append(emptyPanel("TYPE", "Write the type source", "Its enum values or domain contract will appear here."));
    return;
  }
  if (state.designTypeAnalysisLoading) {
    elements.designTypePreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
    return;
  }
  if (state.designTypeAnalysisError) {
    elements.designTypePreview.append(errorPanel(state.designTypeAnalysisError));
    return;
  }
  const contract = state.designTypeAnalysis;
  if (!contract || state.designTypeAnalysisDefinition !== definition) {
    elements.designTypePreview.append(createStatePanel({ mark: "SQL", title: "Waiting for valid source", message: "The preview updates after the statement can be parsed.", surface: true }));
    return;
  }

  const preview = element("article", { className: "routine-contract" });
  const identity = element("div", { className: "routine-contract-signature" });
  identity.append(
    element("small", { text: contract.kind }),
    element("code", { text: contract.name }),
  );
  preview.append(identity);
  if (contract.kind === "enum") {
    preview.append(element("div", { className: "type-enum-values" }, contract.enumValues.map(value => (
      element("code", { text: value, title: value })
    ))));
  } else {
    const details = element("dl");
    for (const [label, value] of [
      ["Base type", contract.baseType],
      ["Default", contract.defaultExpression || "None"],
      ["Nullability", contract.notNull ? "NOT NULL" : "Nullable"],
      ["Collation", contract.collation || "Default"],
    ]) {
      details.append(element("dt", { text: label }), element("dd", { text: value }));
    }
    preview.append(details);
    if (contract.checks.length) {
      const checks = element("div", { className: "type-domain-checks" });
      checks.append(element("strong", { text: `Checks · ${contract.checks.length}` }));
      for (const check of contract.checks) {
        checks.append(element("code", { text: `${check.name ? `${check.name}: ` : ""}${check.expression}` }));
      }
      preview.append(checks);
    }
  }
  elements.designTypePreview.append(preview);
}

async function analyzeDesignTypeDraft() {
  window.clearTimeout(state.designTypeAnalysisTimer);
  const definition = elements.designTypeDefinition.value.trim();
  if (!elements.designTypeDialog.open || !definition || !state.activeWorkspace) {
    state.designTypeAnalysis = null;
    state.designTypeAnalysisDefinition = null;
    state.designTypeAnalysisError = null;
    state.designTypeAnalysisLoading = false;
    renderDesignTypePreview();
    return null;
  }
  const generation = ++state.designTypeAnalysisGeneration;
  const workspaceId = state.activeWorkspace.id;
  state.designTypeAnalysisLoading = true;
  state.designTypeAnalysisError = null;
  renderDesignTypePreview();
  try {
    const analysis = await api.analyzeDesignType(workspaceId, { definition });
    if (generation !== state.designTypeAnalysisGeneration || !elements.designTypeDialog.open) return null;
    state.designTypeAnalysis = analysis;
    state.designTypeAnalysisDefinition = definition;
    return analysis;
  } catch (error) {
    if (generation !== state.designTypeAnalysisGeneration || !elements.designTypeDialog.open) return null;
    state.designTypeAnalysis = null;
    state.designTypeAnalysisDefinition = null;
    state.designTypeAnalysisError = error;
    return null;
  } finally {
    if (generation === state.designTypeAnalysisGeneration) {
      state.designTypeAnalysisLoading = false;
      renderDesignTypePreview();
    }
  }
}

function scheduleDesignTypeAnalysis(delay = 280) {
  window.clearTimeout(state.designTypeAnalysisTimer);
  state.designTypeAnalysisTimer = window.setTimeout(analyzeDesignTypeDraft, delay);
}

function openDesignTypeEditor(typeId = null) {
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
  const designType = typeId ? (state.design.content.types || []).find(item => item.id === typeId) : null;
  if (typeId && !designType) {
    showToast("The selected type is no longer in this design.", { error: true });
    return;
  }
  state.designTypeEditorId = designType?.id || null;
  state.designTypeAnalysisGeneration += 1;
  state.designTypeAnalysis = null;
  state.designTypeAnalysisDefinition = null;
  state.designTypeAnalysisError = null;
  state.designTypeAnalysisLoading = false;
  elements.designTypeForm.reset();
  replace(elements.designTypeStatus);
  elements.designTypeTitle.textContent = designType ? `Edit ${designType.name}` : "Create enum or domain";
  elements.saveDesignTypeButton.textContent = designType ? "Save type" : "Create type";
  elements.designTypeDefinition.value = designType?.definition || "CREATE TYPE order_status AS ENUM (\n    'draft',\n    'submitted',\n    'fulfilled',\n    'cancelled'\n);";
  openDialog(elements.designTypeDialog);
  renderDesignTypePreview();
  scheduleDesignTypeAnalysis(0);
  elements.designTypeDefinition.focus();
}

async function submitDesignType(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
  const editing = Boolean(state.designTypeEditorId);
  const definition = elements.designTypeDefinition.value.trim();
  state.designSubmitting = true;
  elements.saveDesignTypeButton.disabled = true;
  updateDesignControls();
  replace(elements.designTypeStatus, element("span", { text: "Deriving the contract and saving the custom type…" }));
  try {
    const analysis = await api.analyzeDesignType(state.activeWorkspace.id, { definition });
    const result = saveDesignType(state.design.content, {
      typeId: state.designTypeEditorId,
      definition,
    });
    if (!await flushLayoutBeforeTransition()) return;
    const design = await replaceActiveDesign(result.content);
    if (!design) return;
    elements.designTypeDialog.close();
    renderTypesBrowser();
    openDialog(elements.typesDialog);
    showToast(`${editing ? "Updated" : "Created"} ${analysis.kind} ${analysis.name} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designTypeStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignTypeButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignType(typeId) {
  requestDesignObjectDeletion(typeId, { statusTarget: elements.designTypeStatus });
}

function renderFunctionsBrowser() {
  const desired = state.catalog?.source === "design";
  elements.functionsSource.textContent = desired ? "Desired schema" : "Live PostgreSQL catalog";
  elements.functionsCopy.textContent = desired
    ? "Create and inspect source-first routines. Every displayed contract is derived from its saved PostgreSQL statement."
    : "Definitions are read-only and come from the open workspace catalog.";
  elements.createFunctionButton.hidden = !desired;
  const result = renderFunctions(elements.functionsList, state.catalog, elements.functionsSearch.value, {
    onEdit: desired ? routine => {
      elements.functionsDialog.close();
      openDesignRoutineEditor(routine.designId);
    } : null,
    onDelete: desired ? routine => {
      elements.functionsDialog.close();
      confirmDeleteDesignRoutine(routine.designId);
    } : null,
  });
  const source = desired ? "designed" : "live";
  elements.functionsCount.textContent = state.catalog ? `${result.shown} shown · ${result.matching} matching · ${state.catalog.functions.length} ${source}` : "No catalog loaded";
}

function routineSignature(contract) {
  return `${contract.name}(${contract.identityArguments})`;
}

function renderDesignRoutinePreview() {
  replace(elements.designRoutinePreview);
  const definition = elements.designRoutineDefinition.value.trim();
  if (!definition) {
    elements.designRoutinePreview.append(emptyPanel("FN", "Write the routine source", "Its callable signature and PostgreSQL contract will appear here."));
    return;
  }
  if (state.designRoutineAnalysisLoading) {
    elements.designRoutinePreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
    return;
  }
  if (state.designRoutineAnalysisError) {
    elements.designRoutinePreview.append(errorPanel(state.designRoutineAnalysisError));
    return;
  }
  const contract = state.designRoutineAnalysis;
  if (!contract || state.designRoutineAnalysisDefinition !== definition) {
    elements.designRoutinePreview.append(createStatePanel({ mark: "SQL", title: "Waiting for valid source", message: "The preview updates after the statement can be parsed.", surface: true }));
    return;
  }
  const preview = element("article", { className: "routine-contract" });
  const signature = element("div", { className: "routine-contract-signature" });
  signature.append(
    element("small", { text: contract.kind }),
    element("code", { text: routineSignature(contract) }),
  );
  const details = element("dl");
  for (const [label, value] of [
    ["Arguments", contract.arguments || "None"],
    ["Returns", contract.returnType || "No return value"],
    ["Language", contract.language],
  ]) {
    details.append(element("dt", { text: label }), element("dd", { text: value }));
  }
  preview.append(signature, details);
  elements.designRoutinePreview.append(preview);
}

async function analyzeDesignRoutineDraft() {
  window.clearTimeout(state.designRoutineAnalysisTimer);
  const definition = elements.designRoutineDefinition.value.trim();
  if (!elements.designRoutineDialog.open || !definition || !state.activeWorkspace) {
    state.designRoutineAnalysis = null;
    state.designRoutineAnalysisDefinition = null;
    state.designRoutineAnalysisError = null;
    state.designRoutineAnalysisLoading = false;
    renderDesignRoutinePreview();
    return null;
  }
  const generation = ++state.designRoutineAnalysisGeneration;
  const workspaceId = state.activeWorkspace.id;
  state.designRoutineAnalysisLoading = true;
  state.designRoutineAnalysisError = null;
  renderDesignRoutinePreview();
  try {
    const analysis = await api.analyzeDesignRoutine(workspaceId, { definition });
    if (generation !== state.designRoutineAnalysisGeneration || !elements.designRoutineDialog.open) return null;
    state.designRoutineAnalysis = analysis;
    state.designRoutineAnalysisDefinition = definition;
    return analysis;
  } catch (error) {
    if (generation !== state.designRoutineAnalysisGeneration || !elements.designRoutineDialog.open) return null;
    state.designRoutineAnalysis = null;
    state.designRoutineAnalysisDefinition = null;
    state.designRoutineAnalysisError = error;
    return null;
  } finally {
    if (generation === state.designRoutineAnalysisGeneration) {
      state.designRoutineAnalysisLoading = false;
      renderDesignRoutinePreview();
    }
  }
}

function scheduleDesignRoutineAnalysis(delay = 280) {
  window.clearTimeout(state.designRoutineAnalysisTimer);
  state.designRoutineAnalysisTimer = window.setTimeout(analyzeDesignRoutineDraft, delay);
}

function openDesignRoutineEditor(routineId = null) {
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
  const routine = routineId ? state.design.content.functions.find(item => item.id === routineId) : null;
  if (routineId && !routine) {
    showToast("The selected routine is no longer in this design.", { error: true });
    return;
  }
  state.designRoutineEditorId = routine?.id || null;
  state.designRoutineAnalysisGeneration += 1;
  state.designRoutineAnalysis = null;
  state.designRoutineAnalysisDefinition = null;
  state.designRoutineAnalysisError = null;
  state.designRoutineAnalysisLoading = false;
  elements.designRoutineForm.reset();
  replace(elements.designRoutineStatus);
  elements.designRoutineTitle.textContent = routine ? `Edit ${routine.name}` : "Create function or procedure";
  elements.saveDesignRoutineButton.textContent = routine ? "Save routine" : "Create routine";
  elements.designRoutineDefinition.value = routine?.definition || "";
  openDialog(elements.designRoutineDialog);
  renderDesignRoutinePreview();
  if (routine) scheduleDesignRoutineAnalysis(0);
  elements.designRoutineDefinition.focus();
}

async function submitDesignRoutine(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
  const editing = Boolean(state.designRoutineEditorId);
  const definition = elements.designRoutineDefinition.value.trim();
  state.designSubmitting = true;
  elements.saveDesignRoutineButton.disabled = true;
  updateDesignControls();
  replace(elements.designRoutineStatus, element("span", { text: "Deriving the contract and saving the routine…" }));
  try {
    const analysis = await api.analyzeDesignRoutine(state.activeWorkspace.id, { definition });
    const result = saveDesignRoutine(state.design.content, {
      routineId: state.designRoutineEditorId,
      definition,
    });
    if (!await flushLayoutBeforeTransition()) return;
    const design = await replaceActiveDesign(result.content);
    if (!design) return;
    elements.designRoutineDialog.close();
    renderFunctionsBrowser();
    openDialog(elements.functionsDialog);
    showToast(`${editing ? "Updated" : "Created"} ${routineSignature(analysis)} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designRoutineStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignRoutineButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignRoutine(routineId) {
  requestDesignObjectDeletion(routineId, { statusTarget: elements.designRoutineStatus });
}

function triggerIdentity(contract) {
  return `${contract.relationName}.${contract.name}`;
}

function renderDesignTriggerPreview() {
  replace(elements.designTriggerPreview);
  const definition = elements.designTriggerDefinition.value.trim();
  if (!definition) {
    elements.designTriggerPreview.append(emptyPanel("TRG", "Write the trigger source", "Its target, activation rules, and function call will appear here."));
    return;
  }
  if (state.designTriggerAnalysisLoading) {
    elements.designTriggerPreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
    return;
  }
  if (state.designTriggerAnalysisError) {
    elements.designTriggerPreview.append(errorPanel(state.designTriggerAnalysisError));
    return;
  }
  const contract = state.designTriggerAnalysis;
  if (!contract || state.designTriggerAnalysisDefinition !== definition) {
    elements.designTriggerPreview.append(createStatePanel({ mark: "SQL", title: "Waiting for valid source", message: "The preview updates after the statement can be parsed.", surface: true }));
    return;
  }
  const knownRelation = [
    ...state.design.content.tables.map(table => table.name),
    ...state.design.content.views.map(view => view.name),
  ].includes(contract.relationName);
  const preview = element("article", { className: "routine-contract" });
  const signature = element("div", { className: "routine-contract-signature" });
  signature.append(
    element("small", { text: contract.constraint ? "constraint trigger" : "trigger" }),
    element("code", { text: triggerIdentity(contract) }),
  );
  const details = element("dl");
  for (const [label, value] of [
    ["Target", knownRelation ? `${contract.relationName} · in this design` : `${contract.relationName} · not in this design`],
    ["Activation", `${contract.timing.replaceAll("_", " ")} ${contract.events.join(" or ")}`],
    ["Scope", `For each ${contract.orientation}`],
    ["Function", `${contract.functionName}(${contract.functionArguments.join(", ")})`],
    ["Columns", contract.referencedColumns.join(", ") || "No direct OLD/NEW column references"],
    ["Condition", contract.whenExpression || "Always"],
    ["Transition relations", contract.transitionRelations.join(", ") || "None"],
    ["Deferral", contract.deferrable ? (contract.initiallyDeferred ? "Deferrable · initially deferred" : "Deferrable · initially immediate") : "Not deferrable"],
  ]) {
    details.append(element("dt", { text: label }), element("dd", { text: value }));
  }
  preview.append(signature, details);
  elements.designTriggerPreview.append(preview);
}

async function analyzeDesignTriggerDraft() {
  window.clearTimeout(state.designTriggerAnalysisTimer);
  const definition = elements.designTriggerDefinition.value.trim();
  if (!elements.designTriggerDialog.open || !definition || !state.activeWorkspace) {
    state.designTriggerAnalysis = null;
    state.designTriggerAnalysisDefinition = null;
    state.designTriggerAnalysisError = null;
    state.designTriggerAnalysisLoading = false;
    renderDesignTriggerPreview();
    return null;
  }
  const generation = ++state.designTriggerAnalysisGeneration;
  const workspaceId = state.activeWorkspace.id;
  state.designTriggerAnalysisLoading = true;
  state.designTriggerAnalysisError = null;
  renderDesignTriggerPreview();
  try {
    const analysis = await api.analyzeDesignTrigger(workspaceId, { definition });
    if (generation !== state.designTriggerAnalysisGeneration || !elements.designTriggerDialog.open) return null;
    state.designTriggerAnalysis = analysis;
    state.designTriggerAnalysisDefinition = definition;
    return analysis;
  } catch (error) {
    if (generation !== state.designTriggerAnalysisGeneration || !elements.designTriggerDialog.open) return null;
    state.designTriggerAnalysis = null;
    state.designTriggerAnalysisDefinition = null;
    state.designTriggerAnalysisError = error;
    return null;
  } finally {
    if (generation === state.designTriggerAnalysisGeneration) {
      state.designTriggerAnalysisLoading = false;
      renderDesignTriggerPreview();
    }
  }
}

function scheduleDesignTriggerAnalysis(delay = 280) {
  window.clearTimeout(state.designTriggerAnalysisTimer);
  state.designTriggerAnalysisTimer = window.setTimeout(analyzeDesignTriggerDraft, delay);
}

function quotedTriggerIdentifier(value) {
  return `"${value.replaceAll('"', '""')}"`;
}

function defaultTriggerDefinition(relationName) {
  if (!relationName) return "";
  const triggerRoutine = state.design.content.functions.find(routine => (
    routine.kind === "function"
    && routine.identityArguments === ""
    && routine.returnType?.toLocaleLowerCase() === "trigger"
  ));
  const functionName = triggerRoutine?.name || `handle_${relationName.replaceAll(/[^a-zA-Z0-9_]+/g, "_")}_change`;
  return [
    `CREATE TRIGGER ${quotedTriggerIdentifier(`${relationName}_changed`)}`,
    `AFTER INSERT OR UPDATE OR DELETE ON ${quotedTriggerIdentifier(relationName)}`,
    "FOR EACH ROW",
    `EXECUTE FUNCTION ${quotedTriggerIdentifier(functionName)}();`,
  ].join("\n");
}

function openDesignTriggerEditor(triggerId = null, relationName = null) {
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
  const trigger = triggerId ? (state.design.content.triggers || []).find(item => item.id === triggerId) : null;
  if (triggerId && !trigger) {
    showToast("The selected trigger is no longer in this design.", { error: true });
    return;
  }
  const fallbackRelation = relationName || selectedDesignTable()?.name || state.design.content.tables[0]?.name || state.design.content.views[0]?.name || null;
  if (!trigger && !fallbackRelation) {
    showToast("Create a table or view before adding a trigger.", { error: true });
    return;
  }
  state.designTriggerEditorId = trigger?.id || null;
  state.designTriggerAnalysisGeneration += 1;
  state.designTriggerAnalysis = null;
  state.designTriggerAnalysisDefinition = null;
  state.designTriggerAnalysisError = null;
  state.designTriggerAnalysisLoading = false;
  elements.designTriggerForm.reset();
  replace(elements.designTriggerStatus);
  elements.designTriggerTitle.textContent = trigger ? `Edit ${trigger.name}` : "Create trigger";
  elements.saveDesignTriggerButton.textContent = trigger ? "Save trigger" : "Create trigger";
  elements.deleteDesignTriggerButton.hidden = !trigger;
  elements.designTriggerDefinition.value = trigger?.definition || defaultTriggerDefinition(fallbackRelation);
  openDialog(elements.designTriggerDialog);
  renderDesignTriggerPreview();
  scheduleDesignTriggerAnalysis(0);
  elements.designTriggerDefinition.focus();
}

async function submitDesignTrigger(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
  const editing = Boolean(state.designTriggerEditorId);
  const definition = elements.designTriggerDefinition.value.trim();
  state.designSubmitting = true;
  elements.saveDesignTriggerButton.disabled = true;
  updateDesignControls();
  replace(elements.designTriggerStatus, element("span", { text: "Deriving the contract and saving the trigger…" }));
  try {
    const analysis = await api.analyzeDesignTrigger(state.activeWorkspace.id, { definition });
    const result = saveDesignTrigger(state.design.content, {
      triggerId: state.designTriggerEditorId,
      definition,
    });
    if (!await flushLayoutBeforeTransition()) return;
    const relationTable = state.design.content.tables.find(table => table.name === analysis.relationName) || null;
    const design = await replaceActiveDesign(result.content, {
      selectedTableId: relationTable?.id || state.selectedTableId,
    });
    if (!design) return;
    elements.designTriggerDialog.close();
    showToast(`${editing ? "Updated" : "Created"} ${triggerIdentity(analysis)} in design revision ${design.revision}.`);
  } catch (error) {
    replace(elements.designTriggerStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    elements.saveDesignTriggerButton.disabled = false;
    updateHeader();
  }
}

function confirmDeleteDesignTrigger(trigger) {
  const triggerId = trigger?.designId || trigger?.id || state.designTriggerEditorId;
  requestDesignObjectDeletion(triggerId, { statusTarget: elements.designTriggerStatus });
}

function renderObjectsBrowser() {
  const desired = state.catalog?.source === "design";
  elements.objectsSource.textContent = desired ? "Desired schema" : "Live PostgreSQL catalog";
  elements.objectsCopy.textContent = desired
    ? "Search types, relations, constraints, routines, and source-derived triggers from the same desired schema used by the canvas."
    : "Search live tables and views, open their existing inspector or query story, or preview a bounded page of rows.";
  elements.createTriggerButton.hidden = !desired;
  elements.reviewMigrationButton.disabled = !migrationReview.available()
    || state.catalogLoading
    || state.designSubmitting
    || state.historySubmitting
    || state.inspectorTableEditorDirty;
  if (!desired) {
    const result = renderRelationBrowser(elements.objectsList, {
      response: state.liveRelations,
      loading: state.liveRelationsLoading,
      error: state.liveRelationsError,
      onOpen: openLiveRelation,
      onPreview: openRelationPreview,
    });
    elements.objectsCount.textContent = state.catalog ? `${result.shown} live relations` : "No catalog loaded";
    return;
  }
  const result = renderObjects(elements.objectsList, state.catalog, elements.objectsSearch.value, object => {
    if (object.target === "table") {
      elements.objectsDialog.close();
      setLayer("tables", { historyMode: null });
      canvas.focusTable(object.table);
    } else if (object.target === "view") {
      elements.objectsDialog.close();
      setLayer("views", { historyMode: null });
      selectView(object.view);
    } else if (object.target === "trigger") {
      elements.objectsDialog.close();
      openDesignTriggerEditor(object.trigger.designId);
    } else if (object.target === "type") {
      elements.objectsDialog.close();
      openDesignTypeEditor(object.designType.designId);
    } else {
      elements.objectsDialog.close();
      renderFunctionsBrowser();
      openDialog(elements.functionsDialog);
    }
  });
  elements.objectsCount.textContent = state.catalog ? `${result.shown} shown · ${result.matching} matching` : "No catalog loaded";
}

async function loadLiveRelations({ force = false } = {}) {
  if (!state.activeWorkspace?.connectionId || state.catalog?.source === "design") return;
  if (state.liveRelationsLoading && !force) return;
  const generation = ++state.liveRelationsGeneration;
  state.liveRelationsLoading = true;
  state.liveRelationsError = null;
  renderObjectsBrowser();
  try {
    const response = await api.listRelations(state.activeWorkspace.id, {
      search: elements.objectsSearch.value.trim(),
      pageSize: 250,
    });
    if (generation === state.liveRelationsGeneration) state.liveRelations = response;
  } catch (error) {
    if (generation === state.liveRelationsGeneration) state.liveRelationsError = error;
  } finally {
    if (generation === state.liveRelationsGeneration) {
      state.liveRelationsLoading = false;
      renderObjectsBrowser();
    }
  }
}

function openLiveRelation(relation) {
  elements.objectsDialog.close();
  if (relation.kind === "view" || relation.kind === "materialized_view") {
    const view = allViews(state.catalog).find(item => item.name === relation.name && item.catalogKind === relation.kind);
    if (view) {
      setLayer("views", { historyMode: null });
      selectView(view);
    }
    return;
  }
  setLayer("tables", { historyMode: null });
  canvas.focusTable(relation.name);
}

async function openRelationPreview(relation, { page: initialPage = null } = {}) {
  if (!state.activeWorkspace?.connectionId) return;
  if (elements.objectsDialog.open) elements.objectsDialog.close();
  const generation = ++state.relationPreviewGeneration;
  state.relationPreview = { relation, page: initialPage };
  state.relationPreviewLoading = !initialPage;
  state.relationPreviewError = null;
  elements.relationPreviewTitle.textContent = `${relation.name} rows`;
  elements.relationPreviewCopy.textContent = "Read-only, bounded live PostgreSQL data. Values can change between pages.";
  elements.relationPreviewMore.hidden = true;
  renderRelationRows(elements.relationPreviewBody, initialPage ? { page: initialPage } : { loading: true });
  openDialog(elements.relationPreviewDialog);
  if (initialPage) {
    elements.relationPreviewStatus.textContent = `${initialPage.rows.length} rows · ${initialPage.columns.length} columns${initialPage.truncated ? " · more available" : ""}`;
    elements.relationPreviewMore.hidden = !initialPage.nextCursor;
    return;
  }
  try {
    const page = await relationDataSource.page(relation, { pageSize: 100 });
    if (generation !== state.relationPreviewGeneration) return;
    state.relationPreview.page = page;
    elements.relationPreviewStatus.textContent = `${page.rows.length} rows · ${page.columns.length} columns${page.truncated ? " · more available" : ""}`;
    elements.relationPreviewMore.hidden = !page.nextCursor;
  } catch (error) {
    if (generation === state.relationPreviewGeneration) state.relationPreviewError = error;
  } finally {
    if (generation === state.relationPreviewGeneration) {
      state.relationPreviewLoading = false;
      renderRelationRows(elements.relationPreviewBody, { page: state.relationPreview?.page, error: state.relationPreviewError });
    }
  }
}

async function openTableRowPreview(table) {
  try {
    const relation = await findLiveRelation(table.name, table.kind);
    if (!relation) throw new Error("The selected table is no longer present in PostgreSQL.");
    await openRelationPreview(relation);
  } catch (error) {
    showToast(error.message || "The row preview could not be opened.", { error: true });
  }
}

function renderSqlTarget() {
  const workspace = state.activeWorkspace;
  sqlConsole.syncWorkspace(workspace);
  inspectorSqlConsole.syncWorkspace(workspace);
  if (workspace && !workspace.connectionId) {
    elements.sqlTargetConnection.textContent = "Local design · no database";
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
} = {}, {
  container = elements.designColumns,
  sorter = designTableColumnSorter,
  onMutate = null,
} = {}) {
  designColumnDraftSequence += 1;
  const transitionId = id || `draft-column-${designColumnDraftSequence}`;
  const row = element("div", {
    className: "design-column-row",
    dataset: {
      designColumnId: id || "",
      sortKey: transitionId,
      changeObjectId: transitionId,
      changeRoot: "",
      changeField: "order check index relationship",
    },
  });
  const sortHandle = createIconButton({
    icon: "drag",
    label: `Reorder ${name || "new column"}`,
    tooltip: `Drag to reorder ${name || "new column"}`,
    className: "compact design-sort-handle",
  });
  sortHandle.dataset.sortHandle = "";
  const nameInput = element("input", { attrs: { required: "", maxlength: "63", autocomplete: "off", value: name, placeholder: "column_name", "aria-label": "Column name" }, dataset: { designColumnName: "" } });
  const typeSelector = createSearchableSelect({
    value: dataType,
    options: postgresTypeOptions({ customTypes: state.catalog?.types, currentValue: dataType }),
    label: `PostgreSQL type for ${name || "column"}`,
    placeholder: "Search types",
    required: true,
    dataset: { designColumnType: "" },
    noResultsText: "No matching PostgreSQL type",
  });
  const typeInput = typeSelector.input;
  typeSelector.root.dataset.changeObjectId = transitionId;
  typeSelector.root.dataset.changeField = "dataType";
  const typeModifierCopy = element("span", { className: "design-type-modifier-summary" });
  const lengthInput = element("input", {
    type: "number",
    attrs: { min: "1", step: "1", inputmode: "numeric", placeholder: "Unlimited", "aria-label": "Maximum type length" },
    dataset: { designTypeLength: "" },
  });
  const precisionInput = element("input", {
    type: "number",
    attrs: { min: "1", max: "1000", step: "1", inputmode: "numeric", placeholder: "Any", "aria-label": "Numeric precision" },
    dataset: { designTypePrecision: "" },
  });
  const scaleInput = element("input", {
    type: "number",
    attrs: { min: "-1000", max: "1000", step: "1", inputmode: "numeric", placeholder: "0", "aria-label": "Numeric scale" },
    dataset: { designTypeScale: "" },
  });
  const fractionalInput = element("input", {
    type: "number",
    attrs: { min: "0", max: "6", step: "1", inputmode: "numeric", placeholder: "Default", "aria-label": "Fractional seconds precision" },
    dataset: { designTypeFractional: "" },
  });
  const lengthField = element("label", { className: "design-type-modifier-field" }, [
    element("span", { text: "Maximum length" }),
    lengthInput,
    element("small", { text: "Leave blank for the PostgreSQL default." }),
  ]);
  const precisionField = element("label", { className: "design-type-modifier-field" }, [
    element("span", { text: "Precision" }),
    precisionInput,
    element("small", { text: "Total significant digits · 1–1000." }),
  ]);
  const scaleField = element("label", { className: "design-type-modifier-field" }, [
    element("span", { text: "Scale" }),
    scaleInput,
    element("small", { text: "Digits after the decimal · −1000–1000." }),
  ]);
  const fractionalField = element("label", { className: "design-type-modifier-field" }, [
    element("span", { text: "Fractional seconds" }),
    fractionalInput,
    element("small", { text: "Digits after the second · 0–6." }),
  ]);
  const typeModifierDetails = element("details", { className: "design-type-modifiers" }, [
    element("summary", {}, [
      element("span", { text: "Customize type limits" }),
      typeModifierCopy,
    ]),
    element("div", { className: "design-type-modifier-fields" }, [lengthField, precisionField, scaleField, fractionalField]),
  ]);
  typeModifierDetails.dataset.changeObjectId = transitionId;
  typeModifierDetails.dataset.changeField = "dataType";

  const typeOptions = currentValue => postgresTypeOptions({ customTypes: state.catalog?.types, currentValue });
  const clearModifierValidity = () => {
    for (const input of [lengthInput, precisionInput, scaleInput, fractionalInput]) input.setCustomValidity("");
  };
  const syncTypeModifierEditor = ({ expand = false } = {}) => {
    const modifier = parsePostgresTypeModifier(typeInput.value);
    clearModifierValidity();
    typeModifierDetails.hidden = !modifier;
    row.classList.toggle("has-type-modifiers", Boolean(modifier));
    if (!modifier) {
      typeModifierDetails.open = false;
      return;
    }
    lengthField.hidden = modifier.kind !== "length";
    precisionField.hidden = modifier.kind !== "numeric";
    scaleField.hidden = modifier.kind !== "numeric";
    fractionalField.hidden = modifier.kind !== "fractional";
    lengthInput.max = String(modifier.maxLength || "");
    lengthInput.value = modifier.length ?? "";
    precisionInput.value = modifier.kind === "numeric" ? (modifier.precision ?? "") : "";
    scaleInput.value = modifier.kind === "numeric" ? (modifier.scale ?? "") : "";
    fractionalInput.value = modifier.kind === "fractional" ? (modifier.precision ?? "") : "";
    typeModifierCopy.textContent = postgresTypeModifierSummary(modifier);
    if (expand) typeModifierDetails.open = true;
  };
  const applyTypeModifiers = event => {
    const modifier = parsePostgresTypeModifier(typeInput.value);
    if (!modifier) return;
    clearModifierValidity();
    try {
      const revisedType = composePostgresTypeModifier(modifier, {
        length: lengthInput.value,
        precision: precisionInput.value,
        scale: scaleInput.value,
      });
      const finalType = modifier.kind === "fractional"
        ? composePostgresTypeModifier(modifier, { precision: fractionalInput.value })
        : revisedType;
      typeSelector.setOptions(typeOptions(finalType));
      typeSelector.setValue(finalType);
      typeModifierCopy.textContent = postgresTypeModifierSummary(parsePostgresTypeModifier(finalType));
    } catch (error) {
      event.currentTarget.setCustomValidity(error.message);
    }
  };
  for (const input of [lengthInput, precisionInput, scaleInput, fractionalInput]) {
    input.addEventListener("input", applyTypeModifiers);
  }
  typeInput.addEventListener("change", () => {
    typeSelector.setOptions(typeOptions(typeInput.value));
    syncTypeModifierEditor({ expand: true });
  });
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
  const cueField = (node, fields) => {
    node.dataset.changeObjectId = transitionId;
    node.dataset.changeField = fields;
  };
  cueField(nameInput, "name");
  cueField(nullableInput, "nullable");
  cueField(primaryInput, "primary unique");
  cueField(behaviorSelect, "defaultExpression identity generatedExpression");
  cueField(expressionInput, "defaultExpression generatedExpression");
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
  nameInput.addEventListener("input", () => {
    typeSelector.setLabel(`PostgreSQL type for ${nameInput.value || "column"}`);
    sorter.refresh();
  });
  const remove = createIconButton({
    icon: "delete",
    label: `Remove ${name || "column"}`,
    tooltip: "Remove column",
    className: "compact danger design-column-remove",
  });
  nameInput.addEventListener("input", () => remove.setAttribute("aria-label", `Remove ${nameInput.value || "column"}`));
  remove.addEventListener("click", () => {
    if (container.childElementCount === 1) {
      showToast("A designed table needs at least one column.");
      return;
    }
    const removeDraftRow = async () => {
      if (row.dataset.changeRemoving === "true") return;
      row.dataset.changeRemoving = "true";
      remove.disabled = true;
      const prepared = await changeTransitions.prepare([{
        objectId: transitionId,
        operation: "remove",
        tone: "amber",
      }]);
      typeSelector.destroy();
      row.remove();
      sorter.refresh();
      onMutate?.();
      if (container === elements.inspectorDesignColumns) updateInspectorColumnCount();
      changeTransitions.reflow(prepared);
    };
    if (!id) {
      void removeDraftRow();
      return;
    }
    requestDesignObjectDeletion(id, {
      statusTarget: container === elements.inspectorDesignColumns
        ? elements.inspectorTableStatus
        : elements.designTableStatus,
      onConfirmed: removeDraftRow,
    });
  });
  row.append(
    sortHandle,
    element("label", { className: "design-column-name" }, [element("span", { text: "Name" }), nameInput]),
    element("label", { className: "design-column-type" }, [element("span", { text: "PostgreSQL type" }), typeSelector.root]),
    element("label", { className: "design-column-check design-column-nullable" }, [nullableInput, element("span", { text: "Nullable" })]),
    element("label", { className: "design-column-check design-column-primary" }, [primaryInput, element("span", { text: "Primary" })]),
    remove,
    typeModifierDetails,
    element("div", { className: "design-column-value" }, [
      element("label", {}, [element("span", { text: "Value behavior" }), behaviorSelect]),
      expressionField,
    ]),
  );
  syncTypeModifierEditor();
  syncBehavior();
  row.__designTypeSelector = typeSelector;
  container.append(row);
  sorter.refresh();
  return row;
}

function clearDesignColumns(container) {
  for (const row of container.children) row.__designTypeSelector?.destroy();
  replace(container);
}

function openDesignTableEditor(tableId = null) {
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
  cancelColumnAuthoring();
  const table = tableId ? state.design.content.tables.find(item => item.id === tableId) : null;
  if (tableId && !table) {
    showToast("The selected table is no longer in this design.", { error: true });
    return;
  }
  if (table) {
    canvas.select(table.id, { focus: true, notify: true });
    inspectorPane.reveal();
    elements.inspectorTableName.focus();
    return;
  }
  elements.designTableForm.reset();
  clearDesignColumns(elements.designColumns);
  replace(elements.designTableStatus);
  elements.designTableTitle.textContent = "Create table";
  elements.designTableCopy.textContent = "Add a table and its initial columns. Saving replaces one exact design revision and never contacts PostgreSQL.";
  elements.saveDesignTableButton.textContent = "Create table";
  elements.designTableName.value = "";
  appendDesignColumn({ name: "id", dataType: "bigint", nullable: false, primary: true });
  appendDesignColumn({ name: "name", dataType: "text", nullable: false });
  openDialog(elements.designTableDialog);
  elements.designTableName.focus();
}

function designColumnValues(container = elements.designColumns) {
  return [...container.children].map(row => {
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

function inspectorTableContext() {
  return {
    container: elements.inspectorDesignColumns,
    sorter: inspectorTableColumnSorter,
    onMutate: markInspectorTableDirty,
  };
}

function updateInspectorColumnCount() {
  elements.inspectorColumnCount.textContent = String(elements.inspectorDesignColumns.childElementCount);
}

function updateInspectorTableActions() {
  const dirty = state.inspectorTableEditorDirty;
  const busy = state.designSubmitting || state.catalogLoading || state.layoutConflict;
  elements.saveInspectorTableButton.disabled = !dirty || busy;
  elements.discardInspectorTableButton.disabled = !dirty || busy;
  elements.addInspectorColumnButton.disabled = busy;
  elements.inspectorTableForm.toggleAttribute("inert", busy);
  elements.inspectorTableForm.setAttribute("aria-busy", busy ? "true" : "false");
  elements.inspector.classList.toggle("has-table-draft", dirty);
  elements.inspectorContent.toggleAttribute("inert", dirty || busy);
}

function markInspectorTableDirty() {
  if (state.inspectorTableEditorPopulating || !state.inspectorTableEditorId) return;
  state.inspectorTableEditorDirty = true;
  updateInspectorColumnCount();
  updateInspectorTableActions();
  updateDesignControls();
  replace(elements.inspectorTableStatus, element("span", { text: "Unsaved changes · save to edit related objects." }));
}

function populateInspectorTableEditor(table) {
  state.inspectorTableEditorPopulating = true;
  state.inspectorTableEditorId = table.id;
  state.inspectorTableEditorDirty = false;
  elements.inspectorTableForm.dataset.changeObjectId = table.id;
  elements.inspectorTableForm.dataset.changeRoot = "";
  elements.inspectorTableName.dataset.changeObjectId = table.id;
  elements.inspectorTableName.dataset.changeField = "name";
  elements.inspectorTableName.value = table.name;
  clearDesignColumns(elements.inspectorDesignColumns);
  const primaryIds = new Set(table.keys.find(key => key.kind === "primary")?.columnIds || []);
  for (const column of table.columns) appendDesignColumn({
    ...column,
    primary: primaryIds.has(column.id),
  }, inspectorTableContext());
  state.inspectorTableEditorPopulating = false;
  updateInspectorColumnCount();
  updateInspectorTableActions();
  updateDesignControls();
  replace(elements.inspectorTableStatus, element("span", {
    text: `Saved in design revision ${state.design?.revision ?? "current"}.`,
  }));
}

function renderInspectorTableEditor(table) {
  const designedTable = table?.designId
    ? state.design?.content.tables.find(item => item.id === table.designId) || null
    : null;
  elements.inspector.classList.toggle("is-editable", Boolean(designedTable));
  elements.mainLayout.classList.toggle("inspector-table-editable", Boolean(designedTable));
  elements.inspectorTableForm.hidden = !designedTable;
  if (!designedTable) {
    delete elements.inspectorTableForm.dataset.changeObjectId;
    delete elements.inspectorTableForm.dataset.changeRoot;
    delete elements.inspectorTableName.dataset.changeObjectId;
    delete elements.inspectorTableName.dataset.changeField;
    state.inspectorTableEditorId = null;
    state.inspectorTableEditorDirty = false;
    elements.inspector.classList.remove("has-table-draft");
    elements.inspectorContent.removeAttribute("inert");
    clearDesignColumns(elements.inspectorDesignColumns);
    replace(elements.inspectorTableStatus);
    return;
  }
  if (state.inspectorTableEditorId === designedTable.id && state.inspectorTableEditorDirty) {
    elements.inspectorTitle.textContent = elements.inspectorTableName.value.trim() || "Untitled table";
    updateInspectorColumnCount();
    updateInspectorTableActions();
    return;
  }
  populateInspectorTableEditor(designedTable);
}

function discardInspectorTableChanges() {
  const table = state.design?.content.tables.find(item => item.id === state.inspectorTableEditorId) || null;
  if (!table || state.designSubmitting) return;
  populateInspectorTableEditor(table);
  elements.inspectorTitle.textContent = table.name;
  elements.inspectorTableName.focus();
}

async function submitInspectorTable(event) {
  event.preventDefault();
  if (
    state.designSubmitting
    || !state.inspectorTableEditorDirty
    || !isDesignWorkspace()
    || !state.design
  ) return;
  const editingId = state.inspectorTableEditorId;
  let table;
  try {
    table = updateDesignTable(
      state.design.content,
      editingId,
      elements.inspectorTableName.value,
      designColumnValues(elements.inspectorDesignColumns),
    );
  } catch (error) {
    replace(elements.inspectorTableStatus, errorPanel(error));
    return;
  }

  state.designSubmitting = true;
  updateInspectorTableActions();
  updateDesignControls();
  replace(elements.inspectorTableStatus, element("span", { text: "Validating and saving the table…" }));
  try {
    if (!await flushLayoutBeforeTransition()) {
      replace(elements.inspectorTableStatus, element("span", { text: "Unsaved changes · resolve the layout save before retrying." }));
      return;
    }
    const content = structuredClone(state.design.content);
    content.tables = content.tables.map(item => item.id === editingId ? table : item);
    state.inspectorTableEditorDirty = false;
    const design = await replaceActiveDesign(content, { selectedTableId: table.id });
    if (!design) return;
    canvas.select(table.id, { focus: true, notify: true });
    showToast(`Updated ${table.name} in design revision ${design.revision}.`);
  } catch (error) {
    state.inspectorTableEditorDirty = true;
    replace(elements.inspectorTableStatus, conflictPanel(error));
  } finally {
    state.designSubmitting = false;
    updateInspectorTableActions();
    updateHeader();
  }
}

function conflictPanel(error) {
  const conflict = error instanceof ApiError && error.code === "design_conflict";
  return errorPanel(error, {
    retryLabel: conflict ? "Reload design" : null,
    onRetry: conflict ? () => loadActiveDesign({ clearConflictOnSuccess: true }) : null,
  });
}

async function replaceActiveDesign(content, {
  selectedTableId = state.selectedTableId,
  selectedViewId = state.selectedViewId,
  historyGroupId = null,
  revealChanges = false,
} = {}) {
  const workspaceId = state.activeWorkspace.id;
  const beforeContent = state.design?.content || {};
  const operation = beginWorkspaceMutation();
  try {
    await api.replaceDesign(workspaceId, {
      expectedDesignRevision: state.design.revision,
      content,
      ...(historyGroupId ? { historyGroupId } : {}),
    }, { signal: operation.signal });
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return null;
    const { design, layout, history } = await api.getDesignSnapshot(workspaceId, {
      signal: operation.signal,
    });
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return null;
    const targets = designChangeTargets(beforeContent, design.content);
    const prepared = await prepareDesignChangeTransition(targets, { reveal: revealChanges });
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) {
      changeTransitions.restore(prepared);
      return null;
    }
    state.catalogError = null;
    state.layoutError = null;
    commitWorkspaceState({
      design,
      designLayout: layout,
      designHistory: history,
      selectedTableId,
      selectedViewId,
    }, { canvasPositions: designPositions(design, layout) });
    completeDesignChangeTransition(targets, prepared, { reveal: revealChanges });
    return design;
  } finally {
    operation.finish();
  }
}

function applyDesignHistoryMutation(mutation, { cue = true, render = true } = {}) {
  const beforeContent = state.design?.content || {};
  if (!render) {
    commitWorkspaceState({
      design: mutation.design,
      designLayout: mutation.layout,
      designHistory: mutation.history,
      catalog: state.catalog,
    }, {
      render: false,
      canvasMode: "positions",
      canvasPositions: designPositions(mutation.design, mutation.layout),
    });
    return;
  }
  cancelColumnAuthoring();
  commitWorkspaceState({
    design: mutation.design,
    designLayout: mutation.layout,
    designHistory: mutation.history,
  }, { canvasPositions: designPositions(mutation.design, mutation.layout) });
  if (cue) showDesignChanges(beforeContent, mutation.design.content);
}

async function applyDesignHistoryPreview(delta) {
  const beforeContent = state.design.content;
  const afterContent = applyJsonDelta(state.design.content, delta);
  const targets = designChangeTargets(beforeContent, afterContent);
  const prepared = await prepareDesignChangeTransition(targets, { reveal: true });
  const design = {
    ...state.design,
    content: afterContent,
  };
  commitWorkspaceState({ design }, {
    canvasPositions: designPositions(design, state.designLayout),
    render: false,
  });
  const revealTarget = designChangeRevealTarget(targets, "after");
  const focusedView = revealTarget?.scope === "views"
    ? focusChangedViewState(revealTarget)
    : null;
  cancelColumnAuthoring();
  renderCatalogSurfaces();
  renderCatalogState();
  const presentation = designChangePresentation(targets);
  completeDesignChangeTransition(targets, prepared, { reveal: !focusedView });
  syncWorkspaceNavigation("replace");
  if (focusedView) {
    window.requestAnimationFrame(() => scrollChangeElementIntoView(revealTarget));
  }
  return { presentation, targets };
}

function historyDraftBlocked() {
  if (!state.inspectorTableEditorDirty) return false;
  showToast("Save or discard the table edits before changing design history.", { error: true });
  return true;
}

async function executeDesignHistoryMove(direction) {
  if (
    !state.activeWorkspace
    || !state.design
    || state.historySubmitting
    || historyDraftBlocked()
  ) return;
  const action = state.designHistory?.[direction];
  if (!action) return;
  if (action.crossesBaseline) {
    askConfirmation({
      title: `${direction === "undo" ? "Undo" : "Redo"} across baseline?`,
      message: `${action.title}. This changes the desired design to the other side of the current PostgreSQL baseline; PostgreSQL itself will not be changed.`,
      label: direction === "undo" ? "Undo change" : "Redo change",
      callback: () => executeDesignHistoryMoveConfirmed(direction),
    });
    return;
  }
  await executeDesignHistoryMoveConfirmed(direction);
}

async function executeDesignHistoryMoveConfirmed(direction) {
  if (!state.activeWorkspace || !state.design || state.historySubmitting) return;
  if (!await flushLayoutBeforeTransition()) return;
  const workspaceId = state.activeWorkspace.id;
  const expectedDesignRevision = state.design.revision;
  const rollback = {
    design: state.design,
    layout: state.designLayout,
    history: state.designHistory,
    activeLayer: state.activeLayer,
    selectedTableId: state.selectedTableId,
    selectedViewId: state.selectedViewId,
  };
  const delta = state.designHistory?.[direction]?.delta || [];
  const action = state.designHistory?.[direction] || null;
  let previewApplied = false;
  let presentation = null;
  let reloadAfterFailure = false;
  state.historySubmitting = true;
  updateDesignControls();
  const operation = beginWorkspaceMutation();
  try {
    if (delta.length) {
      const preview = await applyDesignHistoryPreview(delta);
      presentation = preview.presentation;
      previewApplied = true;
    }
    const mutation = await api[direction === "undo" ? "undoDesign" : "redoDesign"](
      workspaceId,
      { expectedDesignRevision },
      { signal: operation.signal },
    );
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    const previewMatches = previewApplied
      && JSON.stringify(state.design.content) === JSON.stringify(mutation.design.content);
    applyDesignHistoryMutation(mutation, { cue: !previewMatches, render: !previewMatches });
    const result = presentation?.headline || action?.title || "Design changed";
    const next = state.designHistory?.[direction]?.title;
    showToast(`${result} · ${direction} complete.${next ? ` Next ${direction}: ${next}.` : ""}`);
  } catch (error) {
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    if (previewApplied) {
      changeCues.clear();
      applyDesignHistoryMutation(rollback, { cue: false });
      commitWorkspaceState({
        selectedTableId: rollback.selectedTableId,
        selectedViewId: rollback.selectedViewId,
      });
      setLayer(rollback.activeLayer, { historyMode: "replace" });
    }
    errorToast(error);
    if (error instanceof ApiError && ["design_changed", "nothing_to_undo", "nothing_to_redo"].includes(error.code)) {
      reloadAfterFailure = true;
    }
  } finally {
    operation.finish();
    state.historySubmitting = false;
    updateHeader();
  }
  if (reloadAfterFailure) await loadActiveDesign({ clearConflictOnSuccess: true });
}

function baselineResetChangeLabel(change) {
  const previous = change.previousName ? `${change.previousName} → ` : "";
  return `${change.operation} ${change.kind} ${previous}${change.name}`;
}

async function requestDesignBaselineReset() {
  if (
    !state.activeWorkspace
    || !state.design
    || state.historySubmitting
    || historyDraftBlocked()
  ) return;
  try {
    const preview = await api.previewDesignBaselineReset(state.activeWorkspace.id);
    const examples = preview.summary.changes.slice(0, 4).map(baselineResetChangeLabel).join("; ");
    const remainder = Math.max(0, preview.summary.changeCount - 4);
    askConfirmation({
      title: "Reset desired design to baseline?",
      message: `${preview.summary.title}. ${examples}${remainder ? `; plus ${remainder} more` : ""}. This restores saved design metadata to ${preview.baseline.label}; it does not run DDL and can be undone.`,
      label: "Reset design",
      callback: () => executeDesignBaselineReset(preview),
    });
  } catch (error) {
    errorToast(error);
    if (error instanceof ApiError && error.code === "design_already_at_baseline") {
      await loadActiveDesign({ clearConflictOnSuccess: true });
    }
  }
}

async function executeDesignBaselineReset(preview) {
  if (!state.activeWorkspace || state.historySubmitting) return;
  if (!await flushLayoutBeforeTransition()) return;
  const workspaceId = state.activeWorkspace.id;
  let reloadAfterFailure = false;
  state.historySubmitting = true;
  updateDesignControls();
  const operation = beginWorkspaceMutation();
  try {
    const mutation = await api.resetDesignToBaseline(workspaceId, {
      expectedDesignRevision: preview.designRevision,
      baselineId: preview.baseline.id,
      baselineRevision: preview.baseline.revision,
      reviewDigest: preview.reviewDigest,
    }, { signal: operation.signal });
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    const beforeContent = state.design.content;
    const targets = designChangeTargets(beforeContent, mutation.design.content);
    const prepared = await prepareDesignChangeTransition(targets, { reveal: true });
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) {
      changeTransitions.restore(prepared);
      return;
    }
    applyDesignHistoryMutation(mutation, { cue: false });
    completeDesignChangeTransition(targets, prepared, { reveal: true });
    showToast(`Reset desired design to ${preview.baseline.label}.`);
  } catch (error) {
    if (!operation.isCurrent() || state.activeWorkspace?.id !== workspaceId) return;
    errorToast(error);
    if (error instanceof ApiError && error.status === 409) {
      reloadAfterFailure = true;
    }
  } finally {
    operation.finish();
    state.historySubmitting = false;
    updateHeader();
  }
  if (reloadAfterFailure) await loadActiveDesign({ clearConflictOnSuccess: true });
}

async function submitDesignTable(event) {
  event.preventDefault();
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
  let table;
  try {
    table = createDesignTable(elements.designTableName.value, designColumnValues());
    if (state.design.content.tables.some(item => item.name === table.name)) {
      throw new Error("A table with this name already exists in the design.");
    }
  } catch (error) {
    replace(elements.designTableStatus, errorPanel(error));
    return;
  }
  if (!await flushLayoutBeforeTransition()) return;
  const content = structuredClone(state.design.content);
  content.tables.push(table);
  state.designSubmitting = true;
  elements.saveDesignTableButton.disabled = true;
  updateDesignControls();
  replace(elements.designTableStatus, element("span", { text: "Validating and saving the desired schema…" }));
  try {
    const design = await replaceActiveDesign(content, { selectedTableId: table.id });
    if (!design) return;
    elements.designTableDialog.close();
    canvas.select(table.id, { notify: true });
    state.layoutDirty = true;
    state.layoutVersion += 1;
    await saveLayout();
    showToast(`Created ${table.name} in design revision ${design.revision}.`);
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
  if (table) requestDesignObjectDeletion(table.id, { statusTarget: elements.inspectorTableStatus });
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
  const active = Boolean(enabled && isDesignWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
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
  const suggestedTable = designTableById(selection?.suggestedTableId);
  const columnNames = new Map((table?.columns || []).map(column => [column.id, column.name]));
  const selectedNames = (selection?.columnIds || []).map(columnId => columnNames.get(columnId)).filter(Boolean);
  elements.relationshipAuthoringStep.textContent = String(Math.max(1, selectedNames.length));
  elements.relationshipAuthoringInstruction.textContent = table
    ? selectedNames.length
      ? `${table.name}: ${selectedNames.join(" → ")}`
      : `Select ordered columns on ${table.name}, or configure an expression index`
    : suggestedTable
      ? `Select the first index column from any table, or configure an expression index on ${suggestedTable.name}`
      : "Select the first index column from any table";
  elements.reviewKeyAuthoring.hidden = !table && !suggestedTable;
  elements.reviewKeyAuthoring.textContent = selectedNames.length
    ? `Configure index (${selectedNames.length})`
    : suggestedTable && !table
      ? `Expression index on ${suggestedTable.name}`
      : "Configure expression index";
  canvas.setIndexMode({
    enabled: state.indexAuthoring,
    tableName: table?.name || null,
    columnNames: selectedNames,
  });
}

function setIndexAuthoring(enabled, {
  tableId = null,
  suggestedTableId = null,
  indexId = null,
  columnIds = null,
} = {}) {
  const active = Boolean(enabled && isDesignWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
  if (active && state.relationshipAuthoring) setRelationshipAuthoring(false);
  if (active && state.keyAuthoring) setKeyAuthoring(false);
  state.indexAuthoring = active;
  if (active) {
    const table = designTableById(tableId);
    const index = table && indexId ? designIndexById(table.id, indexId) : null;
    state.indexSelection = {
      tableId: table?.id || null,
      suggestedTableId: index?.id ? null : designTableById(suggestedTableId)?.id || null,
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

function startIndexAuthoring({ tableId = null, indexId = null } = {}) {
  if (state.indexAuthoring) return;
  if (!state.design?.content.tables.length) {
    showToast("Add a table before creating an index.");
    return;
  }
  const table = tableId ? designTableById(tableId) : null;
  if (indexId && (!table || !designIndexById(table.id, indexId))) {
    showToast("The selected index is no longer in this design.", { error: true });
    return;
  }
  setIndexAuthoring(true, {
    tableId: indexId ? table.id : null,
    suggestedTableId: indexId ? null : table?.id || selectedDesignTable()?.id || null,
    indexId,
  });
}

function toggleIndexAuthoring() {
  if (state.indexAuthoring) setIndexAuthoring(false);
  else startIndexAuthoring();
}

function handleIndexColumnSelection(table, column) {
  if (!state.indexAuthoring || !table.designId || !column.designId) return;
  const nextSelection = toggleDesignIndexColumn(
    state.indexSelection,
    table.designId,
    column.designId,
  );
  if (!nextSelection) {
    showToast("Remove the selected columns before choosing a different table.");
    return;
  }
  state.indexSelection = nextSelection;
  updateIndexAuthoringPresentation();
}

function reviewIndexAuthoring() {
  if (!state.indexAuthoring || !(state.indexSelection?.tableId || state.indexSelection?.suggestedTableId)) return;
  const draft = {
    tableId: state.indexSelection.tableId || state.indexSelection.suggestedTableId,
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
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
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
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
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
    const design = await replaceActiveDesign(result.content, { selectedTableId: table.id });
    if (!design) return;
    elements.designKeyDialog.close();
    canvas.select(table.id, { notify: true });
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
  if (constraint?.designId) requestDesignObjectDeletion(constraint.designId, { statusTarget: elements.designKeyStatus });
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
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
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
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
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
    const design = await replaceActiveDesign(result.content, { selectedTableId: table.id });
    if (!design) return;
    elements.designCheckDialog.close();
    canvas.select(table.id, { notify: true });
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
  if (constraint?.designId) requestDesignObjectDeletion(constraint.designId, { statusTarget: elements.designCheckStatus });
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
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
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
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
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
    const design = await replaceActiveDesign(result.content, { selectedTableId: table.id });
    if (!design) return;
    elements.designIndexDialog.close();
    canvas.select(table.id, { notify: true });
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
  if (index?.designId) requestDesignObjectDeletion(index.designId, { statusTarget: elements.designIndexStatus });
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
  if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
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
  const active = Boolean(enabled && isDesignWorkspace() && state.design && !state.catalogLoading && !state.designSubmitting);
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
  if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
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
  if (relationship?.designId) requestDesignObjectDeletion(relationship.designId, { statusTarget: elements.designRelationshipStatus });
}

function setLayerState(layer) {
  if (layer !== "tables" && (state.relationshipAuthoring || state.keyAuthoring || state.indexAuthoring)) cancelColumnAuthoring();
  if (layer !== "tables") closeInspectorDataWorkspace();
  state.activeLayer = layer;
  for (const button of document.querySelectorAll("[data-layer]")) {
    const active = button.dataset.layer === layer;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
  for (const panel of document.querySelectorAll("[data-layer-panel]")) panel.hidden = panel.dataset.layerPanel !== layer;
}

function setLayer(layer, { historyMode = "push" } = {}) {
  setLayerState(layer);
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
  if (isDesignWorkspace()) {
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
  const liveCatalog = state.databaseCatalog || state.catalog;
  const safeTarget = `${liveCatalog.database}-${liveCatalog.namespace}`.replace(/[^a-zA-Z0-9._-]+/g, "-");
  downloadContent(`${JSON.stringify(liveCatalog, null, 2)}\n`, `${safeTarget}-catalog.json`, "application/json");
  showToast("Live catalog JSON downloaded.");
}

async function exportDesignSql() {
  closeDetailsMenus();
  if (!isDesignWorkspace() || !state.design) return;
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
    const target = event.target;
    const nativeEditor = target instanceof HTMLElement && (
      target.matches("input, textarea, select") || target.isContentEditable
    );
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (
      !nativeEditor
      && !event.altKey
      && modifier
      && !document.querySelector("dialog[open]")
      && (key === "z" || key === "y")
    ) {
      const direction = key === "y" || (key === "z" && event.shiftKey) ? "redo" : "undo";
      event.preventDefault();
      executeDesignHistoryMove(direction);
    }
  });
  elements.dependencyImpactDialog.addEventListener("close", () => {
    state.dependencyImpactRootId = null;
    state.pendingDesignDeletion = null;
    state.dependencyHistoryGroupId = null;
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
  elements.deleteTableButton.addEventListener("click", confirmDeleteDesignTable);
  elements.inspectorDataToolsButton.addEventListener("click", () => {
    setInspectorMode(state.inspectorMode === "structure" ? "rows" : "structure");
  });
  elements.inspectorToggle.addEventListener("contextmenu", event => {
    event.preventDefault();
    handleInspectorHeaderGesture("right");
  });
  elements.inspectorRowsHeader.addEventListener("click", event => {
    if (event.composedPath().includes(elements.inspectorDataActions)) return;
    toggleInspectorDataPane("rows");
  });
  elements.showInspectorConsole.addEventListener("click", () => toggleInspectorDataPane("console"));
  for (const header of [elements.inspectorRowsHeader, elements.showInspectorConsole]) {
    header.addEventListener("contextmenu", event => {
      if (event.composedPath().includes(elements.inspectorDataActions)) return;
      event.preventDefault();
      setInspectorDataMaximized(!state.inspectorDataMaximized);
    });
  }
  elements.maximizeInspectorData.addEventListener("click", () => {
    setInspectorDataMaximized(!state.inspectorDataMaximized);
  });
  elements.minimizeInspectorData.addEventListener("click", () => {
    if (state.inspectorDataMaximized) {
      setInspectorDataMaximized(false);
      inspectorPane.expand();
    } else closeInspectorDataWorkspace();
  });
  elements.refreshInspectorRows.addEventListener("click", () => {
    if (state.inspectorRowsView === "results") {
      void inspectorSqlConsole.run();
      return;
    }
    state.inspectorRows = null;
    state.inspectorRowsError = null;
    state.inspectorRelation = null;
    void loadInspectorRows();
  });
  elements.inspectorRowsMore.addEventListener("click", () => {
    if (state.inspectorRows?.nextCursor) void loadInspectorRows({ cursor: state.inspectorRows.nextCursor });
  });
  elements.openFullRowPreview.addEventListener("click", () => {
    if (state.inspectorRowsView === "results") {
      state.inspectorRowsView = "table";
      renderInspectorRows();
      return;
    }
    if (state.inspectorRelation && state.inspectorRows) {
      void openRelationPreview(state.inspectorRelation, { page: state.inspectorRows });
    }
  });
  elements.clearInspectorSql.addEventListener("click", () => {
    if (!inspectorSqlConsole.clearDraft()) showToast("The inspector query is already empty.");
  });
  elements.undoDesignButton.addEventListener("click", () => executeDesignHistoryMove("undo"));
  elements.redoDesignButton.addEventListener("click", () => executeDesignHistoryMove("redo"));
  elements.resetDesignButton.addEventListener("click", requestDesignBaselineReset);
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
  elements.addInspectorColumnButton.addEventListener("click", () => {
    const row = appendDesignColumn({}, inspectorTableContext());
    markInspectorTableDirty();
    updateInspectorColumnCount();
    row.querySelector("[data-design-column-name]")?.focus();
  });
  elements.inspectorTableForm.addEventListener("input", event => {
    if (event.target === elements.inspectorTableName) {
      elements.inspectorTitle.textContent = event.target.value.trim() || "Untitled table";
    }
    markInspectorTableDirty();
  });
  elements.inspectorTableForm.addEventListener("change", markInspectorTableDirty);
  elements.inspectorTableForm.addEventListener("submit", submitInspectorTable);
  elements.discardInspectorTableButton.addEventListener("click", discardInspectorTableChanges);
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
  elements.createFunctionButton.addEventListener("click", () => {
    elements.functionsDialog.close();
    openDesignRoutineEditor();
  });
  elements.designRoutineForm.addEventListener("submit", submitDesignRoutine);
  elements.designRoutineDefinition.addEventListener("input", () => scheduleDesignRoutineAnalysis());
  elements.designRoutineDialog.addEventListener("close", () => {
    window.clearTimeout(state.designRoutineAnalysisTimer);
    state.designRoutineAnalysisGeneration += 1;
    state.designRoutineEditorId = null;
    state.designRoutineAnalysis = null;
    state.designRoutineAnalysisDefinition = null;
    state.designRoutineAnalysisError = null;
    state.designRoutineAnalysisLoading = false;
  });
  elements.typesButton.addEventListener("click", () => {
    renderTypesBrowser();
    openDialog(elements.typesDialog);
  });
  elements.typesSearch.addEventListener("input", renderTypesBrowser);
  document.querySelectorAll("[data-type-filter]").forEach(button => button.addEventListener("click", () => {
    state.typeFilter = button.dataset.typeFilter;
    document.querySelectorAll("[data-type-filter]").forEach(item => {
      const active = item.dataset.typeFilter === state.typeFilter;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", active ? "true" : "false");
    });
    renderTypesBrowser();
  }));
  elements.createTypeButton.addEventListener("click", () => {
    elements.typesDialog.close();
    openDesignTypeEditor();
  });
  elements.designTypeForm.addEventListener("submit", submitDesignType);
  elements.designTypeDefinition.addEventListener("input", () => scheduleDesignTypeAnalysis());
  elements.designTypeDialog.addEventListener("close", () => {
    window.clearTimeout(state.designTypeAnalysisTimer);
    state.designTypeAnalysisGeneration += 1;
    state.designTypeEditorId = null;
    state.designTypeAnalysis = null;
    state.designTypeAnalysisDefinition = null;
    state.designTypeAnalysisError = null;
    state.designTypeAnalysisLoading = false;
  });
  elements.createTriggerButton.addEventListener("click", () => {
    elements.objectsDialog.close();
    openDesignTriggerEditor();
  });
  elements.designTriggerForm.addEventListener("submit", submitDesignTrigger);
  elements.designTriggerDefinition.addEventListener("input", () => scheduleDesignTriggerAnalysis());
  elements.deleteDesignTriggerButton.addEventListener("click", () => {
    const triggerId = state.designTriggerEditorId;
    elements.designTriggerDialog.close();
    confirmDeleteDesignTrigger({ id: triggerId });
  });
  elements.designTriggerDialog.addEventListener("close", () => {
    window.clearTimeout(state.designTriggerAnalysisTimer);
    state.designTriggerAnalysisGeneration += 1;
    state.designTriggerEditorId = null;
    state.designTriggerAnalysis = null;
    state.designTriggerAnalysisDefinition = null;
    state.designTriggerAnalysisError = null;
    state.designTriggerAnalysisLoading = false;
  });
  elements.objectsButton.addEventListener("click", () => {
    if (state.catalog?.source !== "design") loadLiveRelations({ force: true });
    renderObjectsBrowser();
    openDialog(elements.objectsDialog);
  });
  elements.objectsSearch.addEventListener("input", () => {
    if (state.catalog?.source === "design") {
      renderObjectsBrowser();
      return;
    }
    window.clearTimeout(state.liveRelationsSearchTimer);
    state.liveRelationsSearchTimer = window.setTimeout(() => loadLiveRelations({ force: true }), 180);
  });
  elements.relationPreviewMore.addEventListener("click", async () => {
    const current = state.relationPreview;
    if (!current?.page?.nextCursor || state.relationPreviewLoading) return;
    const generation = ++state.relationPreviewGeneration;
    state.relationPreviewLoading = true;
    elements.relationPreviewMore.disabled = true;
    try {
      const page = await relationDataSource.page(current.relation, { cursor: current.page.nextCursor, pageSize: 100 });
      if (generation !== state.relationPreviewGeneration) return;
      state.relationPreview.page = page;
      elements.relationPreviewStatus.textContent = `${page.rows.length} rows · ${page.columns.length} columns${page.truncated ? " · more available" : ""}`;
      elements.relationPreviewMore.hidden = !page.nextCursor;
      renderRelationRows(elements.relationPreviewBody, { page });
    } catch (error) {
      if (generation === state.relationPreviewGeneration) renderRelationRows(elements.relationPreviewBody, { error });
    } finally {
      if (generation === state.relationPreviewGeneration) {
        state.relationPreviewLoading = false;
        elements.relationPreviewMore.disabled = false;
      }
    }
  });
  elements.relationPreviewDialog.addEventListener("close", () => {
    state.relationPreviewGeneration += 1;
    state.relationPreview = null;
    state.relationPreviewError = null;
  });
  elements.reviewMigrationButton.addEventListener("click", async () => {
    elements.objectsDialog.close();
    await migrationReview.open();
  });
  elements.migrationDialog.addEventListener("close", migrationReview.close);

  elements.viewsSearch.addEventListener("input", renderViews);
  elements.refreshViewsButton.addEventListener("click", refreshCatalog);
  document.querySelectorAll("[data-view-filter]").forEach(button => button.addEventListener("click", () => {
    setViewFilterState(button.dataset.viewFilter);
    renderViews();
  }));
  elements.newSqlDraftButton.addEventListener("click", () => {
    if (sqlConsole.clearDraft()) {
      changeCues.show([{
        objectId: "sql-draft",
        scope: "sql",
        fields: ["value"],
        tone: "blue",
      }]);
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
  window.addEventListener("beforeunload", event => {
    if (!state.inspectorTableEditorDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("popstate", () => {
    restoreWorkspaceNavigation(readWorkspaceNavigation(window.location.href)).catch(errorToast);
  });
}

const inspectorTableColumnSorter = installSortableList(elements.inspectorDesignColumns, {
  itemSelector: ".design-column-row",
  itemLabel: item => item.querySelector("[data-design-column-name]")?.value || "new column",
  onReorder: () => {
    markInspectorTableDirty();
    inspectorTableColumnSorter.refresh();
  },
});
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
