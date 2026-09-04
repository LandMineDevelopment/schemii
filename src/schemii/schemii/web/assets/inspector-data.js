import { createSqlConsole } from "./sql-console.js";
import { renderRelationRows, pagedRowsStatus } from "./relation-browser.js";
import { appendDataGridPage, installAutoPageLoader } from "./data-grid.js";
import { createIconElement } from "/assets/common/ui.js";

/**
 * Owns table-scoped rows/SQL state, request generations, pane transitions and
 * header gestures. The app supplies selected context and the shared data source;
 * SQL execution and grid paging still use their existing implementations.
 */
export function createInspectorDataController({
  api,
  elements,
  inspectorPane,
  getContext,
  relationDataSource,
  openRelationPreview,
  notify,
  notifyError,
}) {
  const state = {
    inspectorMode: "structure",
    inspectorDataMaximized: false,
    inspectorRowsView: "table",
    inspectorDataKey: null,
    inspectorRelation: null,
    inspectorRows: null,
    inspectorRowsLoading: false,
    inspectorRowsError: null,
    inspectorRowsPagingError: null,
    inspectorRowsGeneration: 0,
  };
  let inspectorDataVisibilityTimer = null;
  let inspectorDataPaneTransitionTimer = null;

  function inspectorDataAvailable(table = getContext().table) {
    return Boolean(table && getContext().workspace?.connectionId && getContext().workspace?.namespace);
  }

  function inspectorDataKey(table) {
    if (!inspectorDataAvailable(table)) return null;
    return `${getContext().workspace.id}:${table.namespace || getContext().workspace.namespace}:${table.name}:${table.kind || "table"}`;
  }

  function renderInspectorRows({ preserveGrid = false } = {}) {
    const showingResults = state.inspectorRowsView === "results";
    elements.inspectorRowsBody.hidden = showingResults;
    elements.inspectorSqlResults.hidden = !showingResults;
    if (!showingResults && !preserveGrid) renderRelationRows(elements.inspectorRowsBody, {
      page: state.inspectorRows,
      loading: state.inspectorRowsLoading,
      error: state.inspectorRowsError,
    });
    const page = state.inspectorRows;
    elements.inspectorRowsTitle.textContent = showingResults
      ? "Query results"
      : `${getContext().table?.name || "Table"} rows`;
    elements.inspectorRowsStatus.textContent = showingResults
      ? "Read-only result"
      : pagedRowsStatus(page, {
        loading: state.inspectorRowsLoading,
        pagingError: state.inspectorRowsPagingError,
      });
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

  function renderInspectorMode(table = getContext().table) {
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
    state.inspectorRowsPagingError = null;
    state.inspectorRowsView = "table";
    inspectorSqlConsole.reset(key ? relationDataSource.statement(table.name) : "");
    if (table) elements.inspectorRowsTitle.textContent = `${table.name} rows`;
    renderInspectorMode(table);
  }

  function setInspectorMode(mode) {
    const table = getContext().table;
    if (mode !== "structure" && !inspectorDataAvailable(table)) {
      notify("Rows and the console require a PostgreSQL-backed table.", { error: true });
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
      notify("Rows and the console require a PostgreSQL-backed table.", { error: true });
      return;
    }
    if (inspectorPane.state === "minimized") {
      inspectorPane.expand();
      setInspectorMode("rows");
    } else if (dataOpen) setInspectorDataMaximized(true);
    else setInspectorMode("rows");
  }

  async function loadInspectorRows({ cursor = null } = {}) {
    const table = getContext().table;
    const key = inspectorDataKey(table);
    if (!key || state.inspectorRowsLoading) return;
    const previousPage = cursor ? state.inspectorRows : null;
    const paging = Boolean(cursor && previousPage);
    const generation = ++state.inspectorRowsGeneration;
    state.inspectorRowsLoading = true;
    if (!paging) state.inspectorRowsError = null;
    state.inspectorRowsPagingError = null;
    renderInspectorRows({ preserveGrid: paging });
    let appended = false;
    try {
      const relation = cursor && state.inspectorRelation
        ? state.inspectorRelation
        : await relationDataSource.resolve(table.name, getContext().catalogSource === "design" ? null : table.kind);
      if (generation !== state.inspectorRowsGeneration || key !== state.inspectorDataKey) return;
      if (!relation) throw new Error(`${table.name} is not present in the current PostgreSQL target.`);
      const page = await relationDataSource.page(relation, { cursor, pageSize: 100 });
      if (generation !== state.inspectorRowsGeneration || key !== state.inspectorDataKey) return;
      state.inspectorRelation = relation;
      if (paging) {
        const viewport = elements.inspectorRowsBody.querySelector(".relation-preview-viewport");
        const merged = appendDataGridPage(viewport, previousPage, page);
        state.inspectorRows = merged.page;
        appended = merged.appended;
      } else state.inspectorRows = page;
    } catch (error) {
      if (generation === state.inspectorRowsGeneration && key === state.inspectorDataKey) {
        if (paging) {
          state.inspectorRowsPagingError = error;
          notifyError(error);
        } else state.inspectorRowsError = error;
      }
    } finally {
      if (generation === state.inspectorRowsGeneration && key === state.inspectorDataKey) {
        state.inspectorRowsLoading = false;
        renderInspectorRows({ preserveGrid: paging && (appended || Boolean(state.inspectorRowsPagingError)) });
      }
    }
  }

  const inspectorSqlConsole = createSqlConsole({
    api,
    draft: elements.inspectorSqlDraft,
    runButton: elements.runInspectorSql,
    cancelButton: elements.cancelInspectorSql,
    editorStatus: elements.inspectorSqlStatus,
    results: elements.inspectorSqlResults,
    getWorkspace: () => getContext().workspace,
    showToast: notify,
    onError: notifyError,
    onRunStart: showInspectorSqlResults,
  });
  installAutoPageLoader({
    container: elements.inspectorRowsBody,
    canLoad: () => Boolean(
      state.inspectorMode === "rows"
      && state.inspectorRowsView === "table"
      && state.inspectorRows?.nextCursor
      && !state.inspectorRowsLoading
      && !state.inspectorRowsPagingError
    ),
    loadNext: () => loadInspectorRows({ cursor: state.inspectorRows?.nextCursor }),
  });
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
    state.inspectorRowsPagingError = null;
    state.inspectorRelation = null;
    void loadInspectorRows();
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
    if (!inspectorSqlConsole.clearDraft()) notify("The inspector query is already empty.");
  });

  return {
    syncContext: syncInspectorDataContext,
    close: closeInspectorDataWorkspace,
    handleHeaderGesture: handleInspectorHeaderGesture,
    syncWorkspace: workspace => inspectorSqlConsole.syncWorkspace(workspace),
  };
}
