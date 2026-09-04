import { ApiError } from "./api.js";
import { element, replace } from "./dom.js";
import {
  appendDataGridPage,
  createDataGrid,
  formatDataCell,
  installAutoPageLoader,
} from "./data-grid.js";
import { sqlForRun, sqlStatementRanges, transactionTerminalAction } from "./sql-statements.js";
import { createIconButton } from "./ui.js";

const TERMINAL_EXECUTION_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const ACTIVE_TRANSACTION_STATUSES = new Set(["open", "failed"]);
const POLL_INTERVAL_MS = 350;

export function formatConsoleCell(value) {
  return formatDataCell(value);
}

function newConsoleId() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `con_${Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")}`;
}

function storedValue(key) {
  try {
    return globalThis.sessionStorage?.getItem(key) || null;
  } catch {
    return null;
  }
}

function storeValue(key, value) {
  try {
    if (value === null) globalThis.sessionStorage?.removeItem(key);
    else globalThis.sessionStorage?.setItem(key, value);
  } catch {
    // Browser storage is a convenience; transaction authority remains server-side.
  }
}

function browserIdentifier(prefix) {
  const bytes = new Uint8Array(12);
  globalThis.crypto.getRandomValues(bytes);
  return `${prefix}_${Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")}`;
}

function consoleStorageKey(workspaceId, kind) {
  return `schemii.sql.${kind}.${workspaceId}`;
}

function consoleIdForWorkspace(workspaceId) {
  const key = consoleStorageKey(workspaceId, "console-id");
  const stored = storedValue(key);
  if (/^con_[0-9a-f]{32}$/.test(stored || "")) return stored;
  const identifier = newConsoleId();
  storeValue(key, identifier);
  return identifier;
}

function statePanel(mark, title, message, { error = false } = {}) {
  return element("div", { className: `sql-results-state${error ? " error" : ""}` }, [
    element("span", { text: mark }),
    element("strong", { text: title }),
    element("p", { text: message }),
  ]);
}

function transactionOpen(transaction) {
  return Boolean(transaction && ACTIVE_TRANSACTION_STATUSES.has(transaction.status));
}

function executionActive(execution) {
  return Boolean(execution && !TERMINAL_EXECUTION_STATUSES.has(execution.status));
}

function selectedSource(draft, all = false) {
  return sqlForRun(draft.value, draft.selectionStart, draft.selectionEnd, { all });
}

function targetDescription(draft) {
  if (!draft.value.trim()) return "Empty draft";
  if (draft.selectionStart !== draft.selectionEnd) {
    return selectedSource(draft) ? "Selection ready" : "Selection is empty";
  }
  const count = sqlStatementRanges(draft.value).length;
  if (!count) return "No statement found";
  return `${count} statement${count === 1 ? "" : "s"} · cursor selects one`;
}

export function createSqlConsole({
  api,
  draft,
  runButton,
  runAllButton = null,
  cancelButton,
  writeModeToggleButton = null,
  modeRoot = null,
  transactionBar = null,
  editorStatus,
  editorSummary = null,
  results,
  resultsSummary = null,
  readModeButton = null,
  writeModeButton = null,
  transactionStatus = null,
  commitButton = null,
  rollbackButton = null,
  queryTabs = null,
  resultTabs = null,
  queryDrawer = null,
  queryDrawerToggle = null,
  queryDrawerClose = null,
  savedQueries = null,
  queryHistory = null,
  historyCount = null,
  saveQueryButtons = [],
  saveDialog = null,
  saveForm = null,
  saveName = null,
  confirm = null,
  getWorkspace,
  showToast,
  onError,
  onRunStart = null,
  onPaneRequest = null,
  onCommit = null,
  initialDraft = null,
}) {
  const supportsWrite = Boolean(
    writeModeToggleButton && transactionStatus && commitButton && rollbackButton,
  );
  let consoleIdentifier = newConsoleId();
  let settings = null;
  let workspaceId = null;
  let execution = null;
  let transaction = null;
  let queries = [];
  let activeQueryId = null;
  let savedQueryItems = [];
  let historyItems = [];
  let libraryLoading = false;
  let libraryError = null;
  let libraryBusy = false;
  let starterHydrationPending = false;
  let generation = 0;
  let pollTimer = null;
  let busy = false;
  let mode = "managed_read";
  let pendingTerminalAction = null;
  let pendingInitialDraft = typeof initialDraft === "string" && initialDraft.trim()
    ? initialDraft
    : null;

  function workspace() {
    const current = getWorkspace();
    return current?.id === workspaceId ? current : null;
  }

  function available(current = workspace()) {
    return Boolean(current?.connectionId && current?.database && current?.namespace);
  }

  function activeQuery() {
    return queries.find(item => item.id === activeQueryId) || null;
  }

  function uniqueName(requested, excludedId = null) {
    const base = String(requested || "Query").trim().slice(0, 80) || "Query";
    const occupied = new Set(queries.filter(item => item.id !== excludedId).map(item => item.name.toLocaleLowerCase()));
    if (!occupied.has(base.toLocaleLowerCase())) return base;
    let suffix = 2;
    while (occupied.has(`${base} ${suffix}`.toLocaleLowerCase())) suffix += 1;
    return `${base} ${suffix}`;
  }

  function nextQueryName() {
    let number = 1;
    const occupied = new Set(queries.map(item => item.name.toLocaleLowerCase()));
    while (occupied.has(`query ${number}`)) number += 1;
    return `Query ${number}`;
  }

  function persistQueries() {
    if (!workspaceId || !queryTabs) return;
    const serializable = queries.map(({ id, name, sql }) => ({ id, name, sql }));
    storeValue(consoleStorageKey(workspaceId, "queries"), JSON.stringify({ activeQueryId, queries: serializable }));
  }

  function loadBrowserWorkspace(nextWorkspaceId, fallbackDraft = "") {
    let stored = null;
    try {
      stored = JSON.parse(storedValue(consoleStorageKey(nextWorkspaceId, "queries")) || "null");
    } catch {
      stored = null;
    }
    const hasStoredQueries = Array.isArray(stored?.queries) && stored.queries.length > 0;
    starterHydrationPending = !hasStoredQueries && !fallbackDraft.trim();
    queries = hasStoredQueries
      ? stored.queries.filter(item => item && typeof item.sql === "string").map(item => ({
        id: String(item.id || browserIdentifier("qry")),
        name: String(item.name || "Query").slice(0, 80),
        sql: item.sql,
        resultTabs: [],
        activeResultTabId: null,
      }))
      : [{ id: browserIdentifier("qry"), name: "Query 1", sql: fallbackDraft, resultTabs: [], activeResultTabId: null }];
    activeQueryId = queries.some(item => item.id === stored?.activeQueryId)
      ? stored.activeQueryId
      : queries[0].id;
    historyItems = [];
    savedQueryItems = [];
    libraryError = null;
    draft.value = activeQuery()?.sql || "";
    persistQueries();
    renderQueryTabs();
    renderQueryDrawer();
    void refreshLibrary(nextWorkspaceId, generation);
  }

  function hydrateStarterQueries() {
    if (!starterHydrationPending) return;
    starterHydrationPending = false;
    const current = activeQuery();
    if (
      queries.length !== 1
      || !current
      || current.sql.trim()
      || current.resultTabs.length
      || draft.value.trim()
    ) return;
    const starters = savedQueryItems
      .filter(item => item.starter)
      .sort((left, right) => left.name.localeCompare(right.name));
    if (!starters.length) return;
    queries = starters.map(item => ({
      id: browserIdentifier("qry"),
      name: item.name,
      sql: item.sql,
      resultTabs: [],
      activeResultTabId: null,
    }));
    activeQueryId = queries[0].id;
    draft.value = queries[0].sql;
    persistQueries();
    renderQueryTabs();
    renderResults();
    updateControls();
  }

  function persistDraft() {
    if (!workspaceId) return;
    const query = activeQuery();
    if (query) query.sql = draft.value;
    if (queryTabs) persistQueries();
    else storeValue(consoleStorageKey(workspaceId, "draft"), draft.value || null);
  }

  function stopPolling() {
    globalThis.clearTimeout(pollTimer);
    pollTimer = null;
  }

  function updateModeControls() {
    if (!supportsWrite) return;
    const isWrite = mode === "explicit";
    const active = executionActive(execution);
    readModeButton?.classList.toggle("active", !isWrite);
    writeModeButton?.classList.toggle("active", isWrite);
    readModeButton?.setAttribute("aria-pressed", String(!isWrite));
    writeModeButton?.setAttribute("aria-pressed", String(isWrite));
    if (readModeButton) readModeButton.disabled = busy || active || !available();
    if (writeModeButton) writeModeButton.disabled = busy || active || !available();
    if (writeModeToggleButton) {
      writeModeToggleButton.disabled = busy || active || !available();
      writeModeToggleButton.classList.toggle("active", isWrite);
      writeModeToggleButton.setAttribute("aria-pressed", String(isWrite));
      const label = isWrite ? "Safe read" : "Write transaction";
      writeModeToggleButton.setAttribute("aria-label", label);
      writeModeToggleButton.dataset.uiTooltip = label;
    }

    if (modeRoot) modeRoot.dataset.writeMode = String(isWrite);
    if (transactionBar) transactionBar.hidden = !isWrite;

    const open = transactionOpen(transaction);
    commitButton.hidden = !open;
    rollbackButton.hidden = !open;
    commitButton.disabled = busy || active || transaction?.status !== "open";
    rollbackButton.disabled = busy || active;
    if (!isWrite) transactionStatus.textContent = "Safe read · every run is rolled back";
    else if (transaction?.status === "failed") {
      transactionStatus.textContent = "Transaction aborted · roll back required";
    } else if (transaction?.status === "open") {
      const count = transaction.executionIds?.length || 0;
      transactionStatus.textContent = `Open transaction · ${count} run${count === 1 ? "" : "s"} · changes uncommitted`;
    } else if (transaction?.status === "committed") {
      transactionStatus.textContent = "Last transaction committed · next run starts a new one";
    } else if (transaction?.status === "rolled_back") {
      transactionStatus.textContent = "Last transaction rolled back · next run starts a new one";
    } else if (transaction?.status === "expired") {
      transactionStatus.textContent = "Transaction expired and was rolled back";
    } else if (transaction?.status === "uncertain") {
      transactionStatus.textContent = "Commit outcome uncertain · verify PostgreSQL before continuing";
    } else transactionStatus.textContent = "A transaction starts with the first write-mode run";
  }

  function updateControls() {
    const current = workspace();
    const active = executionActive(execution);
    const runnable = Boolean(selectedSource(draft));
    const runDisabled = busy || active || !available(current) || !runnable;
    const runLabel = draft.selectionStart !== draft.selectionEnd ? "Run selection" : "Run current statement";
    runButton.hidden = active;
    runButton.disabled = runDisabled;
    runButton.setAttribute("aria-label", runLabel);
    runButton.dataset.uiTooltip = runLabel;
    if (runAllButton) {
      runAllButton.hidden = active;
      runAllButton.disabled = busy || active || !available(current) || !draft.value.trim();
    }
    cancelButton.hidden = !active;
    cancelButton.disabled = busy;
    draft.readOnly = active;
    for (const button of saveQueryButtons) {
      if (button) button.disabled = active || !draft.value.trim();
    }
    if (editorSummary) editorSummary.textContent = targetDescription(draft);

    if (active) {
      editorStatus.textContent = `${execution.status === "reserved" ? "Queued" : "Running"} · ${mode === "explicit" ? "inside the open transaction" : "read-only"}`;
    } else if (!available(current)) {
      editorStatus.textContent = "A database-backed workspace is required to run SQL.";
    } else if (transaction?.status === "failed") {
      editorStatus.textContent = "PostgreSQL aborted this transaction. Roll it back before running more SQL.";
    } else if (mode === "explicit" && transactionOpen(transaction)) {
      editorStatus.textContent = "Changes remain private to this transaction until Commit is confirmed.";
    } else {
      editorStatus.textContent = "Selection runs exactly; otherwise the statement at the cursor runs.";
    }
    updateModeControls();
  }

  function setResultsSummary(value) {
    if (resultsSummary) resultsSummary.textContent = value;
  }

  function renderQueryTabs() {
    if (!queryTabs) return;
    const fragment = document.createDocumentFragment();
    for (const query of queries) {
      const item = element("div", { className: `sql-query-tab${query.id === activeQueryId ? " active" : ""}` });
      const select = element("button", {
        type: "button",
        text: query.name,
        attrs: { role: "tab", "aria-selected": String(query.id === activeQueryId), title: "Double-click to rename" },
      });
      select.addEventListener("click", () => switchQuery(query.id));
      select.addEventListener("dblclick", () => beginQueryRename(item, query));
      const rename = createIconButton({
        icon: "edit",
        label: `Rename ${query.name}`,
        tooltip: "Rename query",
        className: "sql-tab-action",
      });
      rename.addEventListener("click", () => beginQueryRename(item, query));
      const close = createIconButton({
        icon: "close",
        label: `Close ${query.name}`,
        tooltip: "Close query",
        className: "sql-tab-action",
      });
      close.addEventListener("click", () => closeQuery(query.id));
      item.append(select, rename, close);
      fragment.append(item);
    }
    replace(queryTabs, fragment);
  }

  function beginQueryRename(item, query) {
    const input = element("input", { value: query.name, attrs: { "aria-label": "Query name", maxlength: "80" } });
    const finish = cancel => {
      if (!cancel) query.name = uniqueName(input.value, query.id);
      persistQueries();
      renderQueryTabs();
    };
    input.addEventListener("keydown", event => {
      if (!["Enter", "Escape"].includes(event.key)) return;
      event.preventDefault();
      finish(event.key === "Escape");
    });
    input.addEventListener("blur", () => finish(false), { once: true });
    item.replaceChildren(input);
    input.focus();
    input.select();
  }

  function switchQuery(queryId) {
    if (queryId === activeQueryId || busy || executionActive(execution)) return;
    persistDraft();
    activeQueryId = queryId;
    draft.value = activeQuery()?.sql || "";
    persistQueries();
    renderQueryTabs();
    renderResults();
    updateControls();
    onPaneRequest?.("editor");
  }

  function addQuery() {
    if (busy || executionActive(execution)) return;
    persistDraft();
    const query = {
      id: browserIdentifier("qry"),
      name: nextQueryName(),
      sql: "",
      resultTabs: [],
      activeResultTabId: null,
    };
    queries.push(query);
    activeQueryId = query.id;
    draft.value = "";
    persistQueries();
    renderQueryTabs();
    renderResults();
    updateControls();
    onPaneRequest?.("editor");
    requestAnimationFrame(() => draft.focus());
  }

  function closeQuery(queryId) {
    if (busy || executionActive(execution)) return;
    const index = queries.findIndex(item => item.id === queryId);
    if (index < 0) return;
    const [removed] = queries.splice(index, 1);
    void releaseResultTabs(workspaceId, removed.resultTabs);
    if (!queries.length) {
      queries.push({ id: browserIdentifier("qry"), name: "Query 1", sql: "", resultTabs: [], activeResultTabId: null });
    }
    if (activeQueryId === queryId) activeQueryId = queries[Math.min(index, queries.length - 1)].id;
    draft.value = activeQuery()?.sql || "";
    persistQueries();
    renderQueryTabs();
    renderResults();
    updateControls();
  }

  function historyLabel(source) {
    const first = source.split(/\r?\n/).map(line => line.trim()).find(Boolean) || "SQL query";
    return first.replace(/^--\s*/, "").slice(0, 80);
  }

  function historyItem(historyRecord) {
    const source = historyRecord.sql || "";
    const ranAt = new Date(historyRecord.ranAt);
    return {
      source,
      label: historyLabel(source),
      detail: Number.isNaN(ranAt.valueOf()) ? "Previously run" : ranAt.toLocaleString(),
    };
  }

  async function refreshLibrary(expectedWorkspaceId = workspaceId, expectedGeneration = generation) {
    if (!expectedWorkspaceId || !queryTabs) return;
    libraryLoading = true;
    libraryError = null;
    renderQueryDrawer();
    try {
      const [saved, history] = await Promise.all([
        api.listConsoleSavedQueries(expectedWorkspaceId),
        api.listConsoleHistory(expectedWorkspaceId, { limit: 10 }),
      ]);
      if (workspaceId !== expectedWorkspaceId || generation !== expectedGeneration) return;
      savedQueryItems = saved;
      historyItems = history.map(historyItem);
      hydrateStarterQueries();
    } catch (error) {
      if (workspaceId !== expectedWorkspaceId || generation !== expectedGeneration) return;
      libraryError = error;
      onError(error);
    } finally {
      if (workspaceId === expectedWorkspaceId && generation === expectedGeneration) {
        libraryLoading = false;
        renderQueryDrawer();
      }
    }
  }

  function renderQueryDrawer() {
    if (!savedQueries || !queryHistory) return;
    const savedFragment = document.createDocumentFragment();
    if (libraryLoading && !savedQueryItems.length) savedFragment.append(element("p", { text: "Loading saved queries…" }));
    else if (libraryError && !savedQueryItems.length) savedFragment.append(element("p", { text: "Saved queries are temporarily unavailable." }));
    else if (!savedQueryItems.length) savedFragment.append(element("p", { text: "No saved queries yet." }));
    for (const item of savedQueryItems) {
      const row = element("div", { className: "sql-query-library-row" });
      const open = element("button", { type: "button" }, [element("small", { text: "SAVED" }), element("strong", { text: item.name }), element("span", { text: historyLabel(item.sql) })]);
      open.addEventListener("click", () => openStoredQuery(item));
      const removeButton = createIconButton({
        icon: "delete",
        label: `Delete saved query ${item.name}`,
        tooltip: "Delete saved query",
        placement: "left",
        className: "compact danger sql-library-action",
      });
      removeButton.disabled = libraryBusy;
      removeButton.addEventListener("click", () => removeSavedQuery(item));
      row.append(open, removeButton);
      savedFragment.append(row);
    }
    replace(savedQueries, savedFragment);

    const historyFragment = document.createDocumentFragment();
    if (libraryLoading && !historyItems.length) historyFragment.append(element("p", { text: "Loading recent history…" }));
    else if (libraryError && !historyItems.length) historyFragment.append(element("p", { text: "Recent history is temporarily unavailable." }));
    else if (!historyItems.length) historyFragment.append(element("p", { text: "Run a query to build server history." }));
    for (const item of historyItems) {
      const button = element("button", { type: "button", className: "sql-history-item" }, [
        element("small", { text: "HISTORY" }),
        element("strong", { text: item.label }),
        element("span", { text: item.detail }),
      ]);
      button.addEventListener("click", () => openStoredQuery({ name: item.label, sql: item.source }));
      historyFragment.append(button);
    }
    replace(queryHistory, historyFragment);
    if (historyCount) historyCount.textContent = `${historyItems.length} ${historyItems.length === 1 ? "entry" : "entries"}`;
  }

  async function removeSavedQuery(item) {
    if (!workspaceId || libraryBusy) return;
    const removingWorkspace = workspaceId;
    libraryBusy = true;
    renderQueryDrawer();
    try {
      await api.deleteConsoleSavedQuery(removingWorkspace, item.id, item.revision);
      if (workspaceId !== removingWorkspace) return;
      savedQueryItems = savedQueryItems.filter(saved => saved.id !== item.id);
      showToast(`Deleted saved query ${item.name}.`);
    } catch (error) {
      onError(error);
      if (error instanceof ApiError && error.status === 409) {
        await refreshLibrary(removingWorkspace, generation);
      }
    } finally {
      libraryBusy = false;
      renderQueryDrawer();
    }
  }

  function openStoredQuery(item) {
    const query = activeQuery();
    if (!query) return;
    query.sql = item.sql;
    query.name = uniqueName(item.name, query.id);
    draft.value = item.sql;
    persistQueries();
    renderQueryTabs();
    updateControls();
    if (queryDrawer) queryDrawer.hidden = true;
    queryDrawerToggle?.setAttribute("aria-expanded", "false");
    onPaneRequest?.("editor");
  }

  function resultTabLabel(query, requested) {
    const occupied = new Set(query.resultTabs.map(item => item.label.toLocaleLowerCase()));
    if (!occupied.has(requested.toLocaleLowerCase())) return requested;
    let suffix = 2;
    while (occupied.has(`${requested} (${suffix})`.toLocaleLowerCase())) suffix += 1;
    return `${requested} (${suffix})`;
  }

  function renderResultTabs(query) {
    if (!resultTabs) return;
    const fragment = document.createDocumentFragment();
    for (const tab of query?.resultTabs || []) {
      const item = element("div", { className: `sql-result-tab${tab.id === query.activeResultTabId ? " active" : ""}${tab.pinned ? " pinned" : ""}` });
      const select = element("button", { type: "button", attrs: { role: "tab", "aria-selected": String(tab.id === query.activeResultTabId) } }, [
        element("span", { text: tab.label }),
        element("small", { text: tab.meta }),
      ]);
      select.addEventListener("click", () => { query.activeResultTabId = tab.id; renderResults(); });
      select.addEventListener("dblclick", () => beginResultRename(item, query, tab));
      const rename = createIconButton({
        icon: "edit",
        label: `Rename ${tab.label}`,
        tooltip: "Rename result",
        className: "sql-tab-action",
      });
      rename.addEventListener("click", () => beginResultRename(item, query, tab));
      const pin = createIconButton({
        icon: tab.pinned ? "pin-filled" : "pin",
        label: `${tab.pinned ? "Unpin" : "Pin"} ${tab.label}`,
        tooltip: tab.pinned ? "Unpin result" : "Pin result",
        className: "sql-tab-action",
      });
      pin.setAttribute("aria-pressed", String(tab.pinned));
      pin.addEventListener("click", () => { tab.pinned = !tab.pinned; renderResults(); });
      const close = createIconButton({
        icon: "close",
        label: `Close ${tab.label}`,
        tooltip: "Close result",
        className: "sql-tab-action",
      });
      close.addEventListener("click", () => closeResultTab(query, tab));
      item.append(select, rename, pin, close);
      fragment.append(item);
    }
    replace(resultTabs, fragment);
  }

  function beginResultRename(item, query, tab) {
    const input = element("input", { value: tab.label, attrs: { "aria-label": "Result name", maxlength: "80" } });
    const finish = cancel => {
      if (!cancel) tab.label = resultTabLabel({ resultTabs: query.resultTabs.filter(candidate => candidate.id !== tab.id) }, input.value.trim() || "Result");
      renderResults();
    };
    input.addEventListener("keydown", event => {
      if (!["Enter", "Escape"].includes(event.key)) return;
      event.preventDefault();
      finish(event.key === "Escape");
    });
    input.addEventListener("blur", () => finish(false), { once: true });
    item.replaceChildren(input);
    input.focus();
    input.select();
  }

  function closeResultTab(query, tab) {
    const index = query.resultTabs.indexOf(tab);
    if (index < 0) return;
    query.resultTabs.splice(index, 1);
    void releaseResultTabs(workspaceId, [tab]);
    if (query.activeResultTabId === tab.id) query.activeResultTabId = query.resultTabs[Math.min(index, query.resultTabs.length - 1)]?.id || null;
    renderResults();
  }

  function renderIdle() {
    if (resultTabs) replace(resultTabs);
    setResultsSummary("Nothing run yet");
    if (!workspace()) {
      replace(results, statePanel("SQL", "No workspace open", "Open a database-backed workspace to query PostgreSQL."));
    } else if (!available()) {
      replace(results, statePanel("SQL", "No PostgreSQL target", "Local designs do not have a database to query."));
    } else {
      replace(results, statePanel("RESULTS", "Ready for a query", "Select text, place the cursor in a statement, or choose Run all."));
    }
  }

  function renderExecutionState() {
    const status = execution?.status || "reserved";
    setResultsSummary(status === "running" ? "Running" : "Queued");
    replace(results, statePanel(
      status === "running" ? "RUNNING" : "QUEUED",
      status === "running" ? "PostgreSQL is executing the query" : "Waiting for execution",
      "The server owns this execution. Cancel stops the active statement without clearing the draft.",
    ));
  }

  function renderFailure() {
    const statement = Number.isInteger(execution.errorStatementIndex)
      ? `Statement ${execution.errorStatementIndex + 1}. `
      : "";
    const cancelled = execution.status === "cancelled";
    const query = activeQuery();
    if (query) {
      const tab = {
        id: browserIdentifier("rtab"),
        label: resultTabLabel(query, cancelled ? "Cancelled" : "Error"),
        meta: cancelled ? "Cancelled" : `Statement ${(execution.errorStatementIndex ?? 0) + 1}`,
        kind: "error",
        pinned: false,
        message: `${statement}${execution.errorMessage || "The query did not produce a retained result."}`,
      };
      query.resultTabs.push(tab);
      query.activeResultTabId = tab.id;
      renderResultTabs(query);
      void refreshLibrary(workspaceId, generation);
    }
    setResultsSummary(cancelled ? "Cancelled" : "Query failed");
    replace(results, statePanel(
      cancelled ? "CANCELLED" : "QUERY ERROR",
      cancelled ? "Execution cancelled" : "PostgreSQL rejected the query",
      `${statement}${execution.errorMessage || "The query did not produce a retained result."}`,
      { error: !cancelled },
    ));
  }

  function renderResults() {
    const query = activeQuery();
    renderResultTabs(query);
    if (!query?.resultTabs.length) {
      setResultsSummary("Nothing run yet");
      replace(results, statePanel("RESULTS", "Ready for a query", "Run the current statement, a selection, or the full script."));
      return;
    }
    const tab = query.resultTabs.find(item => item.id === query.activeResultTabId) || query.resultTabs.at(-1);
    query.activeResultTabId = tab.id;
    setResultsSummary(tab.meta);
    if (tab.kind === "error") {
      replace(results, statePanel("QUERY ERROR", "PostgreSQL rejected the query", tab.message, { error: true }));
      return;
    }
    const { summary, page } = tab;
    const card = element("article", { className: "sql-result-card" });
    card.append(element("header", {}, [
      element("div", {}, [element("small", { text: `STATEMENT ${summary.statementIndex + 1}` }), element("strong", { text: summary.command })]),
      element("span", { text: `${summary.rowCount ?? 0} ${summary.rowCount === 1 ? "row" : "rows"}` }),
    ]));
    if (!page) card.append(statePanel("…", "Loading retained result", "Reading the first result page from the server."));
    else if (!page.rows.length) card.append(statePanel("0", "No rows returned", "PostgreSQL returned the column shape without any records."));
    else card.append(createDataGrid({ columns: page.columns, rows: page.rows, className: "sql-result-viewport" }));
    if (page) {
      const footer = element("footer");
      footer.append(element("span", {
        text: resultProgressText(summary, page),
        attrs: { "data-result-progress": "" },
      }));
      const actions = element("div", { className: "sql-result-actions" });
      const exportButton = createIconButton({
        icon: "download",
        label: "Export complete result as CSV",
        tooltip: "Export CSV",
        className: "compact",
      });
      exportButton.addEventListener("click", () => exportResult(tab));
      actions.append(exportButton);
      footer.append(actions);
      card.append(footer);
    }
    replace(results, card);
  }

  function resultProgressText(summary, page) {
    const count = page.rows.length;
    if (page.nextCursor) return `${count} of ${summary.rowCount} rows loaded · scroll for more`;
    if (page.truncated) return `${count} rows loaded · result reached the server retention limit`;
    return `${count} of ${summary.rowCount} rows loaded`;
  }

  function activePagedResultTab() {
    const query = activeQuery();
    if (!query) return null;
    const tab = query.resultTabs.find(item => item.id === query.activeResultTabId);
    return tab?.kind === "result" && tab.page?.nextCursor ? tab : null;
  }

  async function loadSettings(runGeneration) {
    if (settings) return settings;
    const response = await api.getConsoleSettings();
    if (runGeneration !== generation) return null;
    settings = response;
    return response;
  }

  async function loadFirstPages(runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation) return;
    const query = activeQuery();
    if (!query) return;
    const labels = execution.results.map((_, index) => resultTabLabel(query, `Result ${index + 1}`));
    const newTabs = execution.results.map((summary, index) => ({
      id: browserIdentifier("rtab"),
      label: labels[index],
      meta: `${summary.command} · ${summary.rowCount ?? 0} ${summary.rowCount === 1 ? "row" : "rows"}`,
      kind: "result",
      pinned: false,
      executionId: execution.id,
      summary,
      page: null,
    }));
    query.resultTabs.push(...newTabs);
    query.activeResultTabId = newTabs[0]?.id || query.activeResultTabId;
    renderResults();
    for (const tab of newTabs) {
      const summary = tab.summary;
      const page = await api.getConsoleResultPage(current.id, execution.id, summary.id);
      if (runGeneration !== generation || workspace()?.id !== current.id) return;
      tab.page = page;
      renderResults();
    }
    await refreshLibrary(current.id, runGeneration);
  }

  async function refreshTransaction(current, expectedGeneration) {
    if (!transaction?.id || !current || expectedGeneration !== generation) return;
    try {
      transaction = await api.getConsoleTransaction(current.id, transaction.id);
    } catch (error) {
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) throw error;
      transaction = null;
    }
    storeValue(
      consoleStorageKey(current.id, "transaction-id"),
      transactionOpen(transaction) ? transaction.id : null,
    );
  }

  async function poll(runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation || !execution) return;
    try {
      execution = await api.getConsoleExecution(current.id, execution.id);
      if (runGeneration !== generation) return;
      if (!TERMINAL_EXECUTION_STATUSES.has(execution.status)) {
        renderExecutionState();
        pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
        return;
      }

      if (mode === "explicit") await refreshTransaction(current, runGeneration);
      if (execution.status === "succeeded") {
        await loadFirstPages(runGeneration);
        const terminalAction = pendingTerminalAction;
        pendingTerminalAction = null;
        if (terminalAction && runGeneration === generation) {
          await finishTransaction(terminalAction, { typed: true });
        }
      } else {
        pendingTerminalAction = null;
        renderFailure();
      }
    } catch (error) {
      if (runGeneration !== generation) return;
      setResultsSummary("Status unavailable");
      replace(results, statePanel("ERROR", "Execution status unavailable", error.message, { error: true }));
      onError(error);
    } finally {
      if (runGeneration === generation) {
        busy = false;
        updateControls();
      }
    }
  }

  async function ensureTransaction(current, policy, runGeneration) {
    if (transactionOpen(transaction)) return transaction;
    transaction = await api.createConsoleTransaction(current.id, {
      consoleId: consoleIdentifier,
      expectedWorkspaceRevision: current.revision,
      expectedSettingsRevision: policy.revision,
    });
    if (runGeneration !== generation) return null;
    storeValue(consoleStorageKey(current.id, "transaction-id"), transaction.id);
    showToast(transaction.executionIds?.length
      ? "Existing write transaction resumed."
      : "Write transaction opened. Changes are not committed yet.");
    return transaction;
  }

  async function submitRead(current, policy, source, runGeneration) {
    execution = await api.createConsoleExecution(current.id, {
      consoleId: consoleIdentifier,
      expectedWorkspaceRevision: current.revision,
      expectedSettingsRevision: policy.revision,
      mode: "managed_read",
      statements: [source],
    });
    return runGeneration === generation;
  }

  async function submitTransaction(current, policy, source, terminalAction, runGeneration) {
    if (!transactionOpen(transaction) && !source && terminalAction) {
      throw new Error(`There is no open transaction to ${terminalAction}.`);
    }
    const activeTransaction = await ensureTransaction(current, policy, runGeneration);
    if (!activeTransaction || runGeneration !== generation) return false;
    if (!source) {
      busy = false;
      updateControls();
      await finishTransaction(terminalAction, { typed: true });
      return false;
    }
    if (activeTransaction.status === "failed") {
      throw new Error("This transaction is aborted. Roll it back before running more SQL.");
    }
    pendingTerminalAction = terminalAction;
    execution = await api.createConsoleTransactionExecution(current.id, activeTransaction.id, {
      expectedRevision: activeTransaction.revision,
      statements: [source],
    });
    await refreshTransaction(current, runGeneration);
    return runGeneration === generation;
  }

  async function run(all = false) {
    const current = workspace();
    const source = selectedSource(draft, all);
    if (!available(current) || busy || executionActive(execution) || !source) return;
    if (typeof onRunStart === "function") onRunStart();
    stopPolling();
    const query = activeQuery();
    const replacedTabs = query?.resultTabs.filter(tab => !tab.pinned) || [];
    if (query) {
      query.resultTabs = query.resultTabs.filter(tab => tab.pinned);
      query.activeResultTabId = query.resultTabs.at(-1)?.id || null;
    }
    const runGeneration = ++generation;
    busy = true;
    execution = null;
    pendingTerminalAction = null;
    void releaseResultTabs(current.id, replacedTabs);
    updateControls();
    setResultsSummary("Submitting");
    replace(results, statePanel("SUBMITTING", "Validating query", "The server is binding the selected SQL to the current PostgreSQL target."));
    try {
      const policy = await loadSettings(runGeneration);
      if (!policy || runGeneration !== generation) return;
      let submitted = false;
      if (mode === "explicit") {
        const script = transactionTerminalAction(source);
        submitted = await submitTransaction(current, policy, script.sql, script.action, runGeneration);
      } else {
        const script = transactionTerminalAction(source);
        if (script.action) throw new Error("Switch to Write transaction before running COMMIT or ROLLBACK.");
        submitted = await submitRead(current, policy, source, runGeneration);
      }
      if (!submitted || runGeneration !== generation) return;
      busy = false;
      renderExecutionState();
      updateControls();
      pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
    } catch (error) {
      if (runGeneration !== generation) return;
      if (error instanceof ApiError && error.code === "console_settings_changed") settings = null;
      pendingTerminalAction = null;
      busy = false;
      execution = null;
      setResultsSummary("Could not start");
      replace(results, statePanel("QUERY ERROR", "Query could not start", error.message, { error: true }));
      updateControls();
      onError(error);
    }
  }

  async function cancel() {
    const current = workspace();
    if (!current || !executionActive(execution) || busy) return;
    busy = true;
    pendingTerminalAction = null;
    updateControls();
    try {
      execution = await api.cancelConsoleExecution(current.id, execution.id);
      if (execution.status === "cancelled") renderFailure();
      else renderExecutionState();
      showToast("Console cancellation requested.");
    } catch (error) {
      onError(error);
    } finally {
      busy = false;
      updateControls();
    }
  }

  async function finishTransaction(action, { typed = false } = {}) {
    const current = workspace();
    if (!current || !transactionOpen(transaction) || executionActive(execution) || busy) return;
    busy = true;
    updateControls();
    try {
      transaction = action === "commit"
        ? await api.commitConsoleTransaction(current.id, transaction.id, { expectedRevision: transaction.revision })
        : await api.rollbackConsoleTransaction(current.id, transaction.id, { expectedRevision: transaction.revision });
      storeValue(consoleStorageKey(current.id, "transaction-id"), null);
      showToast(action === "commit" ? "Transaction committed." : "Transaction rolled back.");
      if (action === "commit" && typeof onCommit === "function") await onCommit();
      if (typed && !execution) {
        setResultsSummary(action === "commit" ? "Committed" : "Rolled back");
        replace(results, statePanel(
          action === "commit" ? "COMMIT" : "ROLLBACK",
          action === "commit" ? "Transaction committed" : "Transaction rolled back",
          action === "commit" ? "PostgreSQL made the transaction durable." : "PostgreSQL discarded the transaction changes.",
        ));
      }
    } catch (error) {
      onError(error);
      setResultsSummary("Transaction action failed");
      replace(results, statePanel("TRANSACTION ERROR", `Could not ${action}`, error.message, { error: true }));
      try {
        await refreshTransaction(current, generation);
      } catch {
        // The original transaction error is the useful message.
      }
    } finally {
      busy = false;
      updateControls();
    }
  }

  function requestFinish(action) {
    if (!transactionOpen(transaction)) return;
    if (typeof confirm !== "function") {
      void finishTransaction(action);
      return;
    }
    const committing = action === "commit";
    confirm({
      title: committing ? "Commit write transaction" : "Roll back write transaction",
      message: committing
        ? "Make every change in this open transaction durable in PostgreSQL? This cannot be undone from the Console."
        : "Discard every uncommitted change in this open transaction?",
      label: committing ? "Commit" : "Roll back",
      tone: committing ? "primary" : "danger",
      callback: () => finishTransaction(action),
    });
  }

  async function loadMore(tab) {
    const current = workspace();
    const page = tab?.page;
    if (!current || !tab?.executionId || !page?.nextCursor || busy) return;
    const requestedGeneration = generation;
    const requestedWorkspaceId = current.id;
    const requestedQueryId = activeQueryId;
    const requestedTabId = tab.id;
    const requestedCursor = page.nextCursor;
    const progress = results.querySelector("[data-result-progress]");
    if (progress) progress.textContent = `Loading rows ${page.rows.length + 1}–${Math.min(page.rows.length + 100, tab.summary.rowCount)}…`;
    busy = true;
    updateControls();
    try {
      const next = await api.getConsoleResultPage(current.id, tab.executionId, tab.summary.id, { cursor: requestedCursor });
      if (generation !== requestedGeneration || workspace()?.id !== requestedWorkspaceId) return;
      const stillActive = activeQueryId === requestedQueryId && activeQuery()?.activeResultTabId === requestedTabId;
      const viewport = stillActive ? results.querySelector(".sql-result-viewport") : null;
      const merged = appendDataGridPage(viewport, page, next);
      tab.page = merged.page;
      if (!stillActive) return;
      if (!merged.appended) {
        renderResults();
        return;
      }
      if (progress) progress.textContent = resultProgressText(tab.summary, tab.page);
    } catch (error) {
      if (progress) progress.textContent = resultProgressText(tab.summary, page);
      onError(error);
    } finally {
      busy = false;
      updateControls();
    }
  }

  async function releaseResultTabs(previousWorkspaceId, tabs) {
    if (!previousWorkspaceId || !tabs?.length) return;
    await Promise.allSettled(tabs.filter(tab => tab.kind === "result").map(tab => (
      api.closeConsoleResult(previousWorkspaceId, tab.executionId, tab.summary.id)
    )));
  }

  function exportResult(tab) {
    const current = workspace();
    if (!current || !tab?.executionId || !tab?.summary?.id) return;
    const link = document.createElement("a");
    link.href = api.consoleResultExportUrl(current.id, tab.executionId, tab.summary.id);
    link.download = `${tab.label.replace(/[^a-z0-9_-]+/gi, "-").toLocaleLowerCase() || "result"}.csv`;
    link.click();
  }

  async function releaseExecution(previousWorkspaceId, previousExecution) {
    if (!previousWorkspaceId || !previousExecution) return;
    try {
      if (!TERMINAL_EXECUTION_STATUSES.has(previousExecution.status)) {
        await api.cancelConsoleExecution(previousWorkspaceId, previousExecution.id);
      }
      await Promise.all((previousExecution.results || []).map(summary => (
        api.closeConsoleResult(previousWorkspaceId, previousExecution.id, summary.id)
      )));
    } catch (error) {
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) return;
    }
  }

  async function rollbackAbandonedTransaction(previousWorkspaceId, previousTransaction) {
    if (!previousWorkspaceId || !transactionOpen(previousTransaction)) return;
    try {
      await api.rollbackConsoleTransaction(previousWorkspaceId, previousTransaction.id, {
        expectedRevision: previousTransaction.revision,
      });
      storeValue(consoleStorageKey(previousWorkspaceId, "transaction-id"), null);
      showToast("The open SQL transaction was rolled back when its workspace changed.");
    } catch (error) {
      onError(error);
    }
  }

  async function restoreTransaction(current, expectedGeneration) {
    if (!supportsWrite || !current?.id) return;
    const transactionId = storedValue(consoleStorageKey(current.id, "transaction-id"));
    if (!/^ctx_[0-9a-f]{32}$/.test(transactionId || "")) return;
    busy = true;
    updateControls();
    try {
      const restored = await api.getConsoleTransaction(current.id, transactionId);
      if (expectedGeneration !== generation || workspace()?.id !== current.id) return;
      transaction = restored;
      if (transactionOpen(restored)) {
        mode = "explicit";
        showToast("Open write transaction restored for this tab.");
      } else {
        storeValue(consoleStorageKey(current.id, "transaction-id"), null);
      }
    } catch (error) {
      if (expectedGeneration !== generation) return;
      storeValue(consoleStorageKey(current.id, "transaction-id"), null);
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) onError(error);
    } finally {
      if (expectedGeneration === generation) {
        busy = false;
        updateControls();
      }
    }
  }

  function setMode(nextMode) {
    if (!supportsWrite || !available() || executionActive(execution) || busy) return;
    const next = nextMode === "explicit" ? "explicit" : "managed_read";
    if (next === mode) return;
    if (next === "managed_read" && transactionOpen(transaction)) {
      if (typeof confirm === "function") {
        confirm({
          title: "Leave write transaction",
          message: "Switching to Safe read will roll back every uncommitted change in the open transaction.",
          label: "Roll back and switch",
          tone: "danger",
          callback: async () => {
            await finishTransaction("rollback");
            if (!transactionOpen(transaction)) {
              mode = "managed_read";
              updateControls();
            }
          },
        });
      }
      return;
    }
    mode = next;
    updateControls();
  }

  function syncWorkspace(current) {
    const nextId = current?.id || null;
    if (nextId !== workspaceId) {
      const previousWorkspaceId = workspaceId;
      const previousExecution = execution;
      const previousTransaction = transaction;
      const previousQueries = queries;
      generation += 1;
      stopPolling();
      workspaceId = nextId;
      consoleIdentifier = nextId && supportsWrite
        ? consoleIdForWorkspace(nextId)
        : newConsoleId();
      if (queryTabs) {
        const fallbackDraft = nextId
          ? pendingInitialDraft ?? storedValue(consoleStorageKey(nextId, "draft")) ?? ""
          : "";
        if (nextId) loadBrowserWorkspace(nextId, fallbackDraft);
        else {
          starterHydrationPending = false;
          queries = [];
          activeQueryId = null;
          draft.value = "";
          renderQueryTabs();
          renderQueryDrawer();
        }
        if (nextId && pendingInitialDraft !== null) {
          const query = activeQuery();
          if (query) query.sql = pendingInitialDraft;
          draft.value = pendingInitialDraft;
          persistQueries();
          pendingInitialDraft = null;
        }
      } else {
        queries = nextId
          ? [{ id: browserIdentifier("qry"), name: "Query", sql: draft.value || "", resultTabs: [], activeResultTabId: null }]
          : [];
        activeQueryId = queries[0]?.id || null;
      }
      execution = null;
      transaction = null;
      pendingTerminalAction = null;
      busy = false;
      mode = "managed_read";
      void releaseExecution(previousWorkspaceId, previousExecution);
      void Promise.all(previousQueries.map(query => releaseResultTabs(previousWorkspaceId, query.resultTabs)));
      void rollbackAbandonedTransaction(previousWorkspaceId, previousTransaction);
      renderIdle();
      if (current) void restoreTransaction(current, generation);
    }
    updateControls();
  }

  function clearDraft() {
    if (!draft.value) return false;
    draft.value = "";
    persistDraft();
    updateControls();
    return true;
  }

  function setDraft(value) {
    draft.value = value;
    persistDraft();
    updateControls();
  }

  function toggleQueryDrawer(force = null) {
    if (!queryDrawer) return;
    const open = force === null ? queryDrawer.hidden : Boolean(force);
    queryDrawer.hidden = !open;
    queryDrawerToggle?.setAttribute("aria-expanded", String(open));
    if (open) {
      renderQueryDrawer();
      void refreshLibrary(workspaceId, generation);
    }
  }

  function openSaveDialog() {
    if (!saveDialog || !draft.value.trim()) return;
    saveName.value = activeQuery()?.name || "Query";
    saveDialog.showModal();
    requestAnimationFrame(() => { saveName.focus(); saveName.select(); });
  }

  async function saveCurrentQuery(event) {
    event?.preventDefault();
    const name = saveName?.value.trim();
    const sql = draft.value.trim();
    if (!workspaceId || !name || !sql || libraryBusy) return;
    const savingWorkspace = workspaceId;
    libraryBusy = true;
    try {
      const saved = await api.createConsoleSavedQuery(savingWorkspace, {
        name: name.slice(0, 80),
        sql,
      });
      if (workspaceId !== savingWorkspace) return;
      savedQueryItems = [saved, ...savedQueryItems.filter(item => item.id !== saved.id)];
      saveDialog?.close();
      toggleQueryDrawer(true);
      showToast("Query saved in Schemii metadata.");
    } catch (error) {
      onError(error);
    } finally {
      libraryBusy = false;
      renderQueryDrawer();
    }
  }

  function reset(value = "") {
    const previousExecution = execution;
    const previousTransaction = transaction;
    generation += 1;
    stopPolling();
    execution = null;
    transaction = null;
    pendingTerminalAction = null;
    busy = false;
    mode = "managed_read";
    for (const query of queries) void releaseResultTabs(workspaceId, query.resultTabs);
    queries = [{ id: browserIdentifier("qry"), name: "Query 1", sql: value, resultTabs: [], activeResultTabId: null }];
    activeQueryId = queries[0].id;
    draft.value = value;
    persistDraft();
    void releaseExecution(workspaceId, previousExecution);
    void rollbackAbandonedTransaction(workspaceId, previousTransaction);
    renderIdle();
    renderQueryTabs();
    updateControls();
  }

  runButton.addEventListener("click", () => void run(false));
  runAllButton?.addEventListener("click", () => void run(true));
  cancelButton.addEventListener("click", cancel);
  readModeButton?.addEventListener("click", () => setMode("managed_read"));
  writeModeButton?.addEventListener("click", () => setMode("explicit"));
  writeModeToggleButton?.addEventListener("click", () => setMode(mode === "explicit" ? "managed_read" : "explicit"));
  commitButton?.addEventListener("click", () => requestFinish("commit"));
  rollbackButton?.addEventListener("click", () => requestFinish("rollback"));
  queryDrawerToggle?.addEventListener("click", () => toggleQueryDrawer());
  queryDrawerClose?.addEventListener("click", () => toggleQueryDrawer(false));
  for (const button of saveQueryButtons) button?.addEventListener("click", openSaveDialog);
  saveForm?.addEventListener("submit", event => void saveCurrentQuery(event));
  queryTabs?.addEventListener("keydown", event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !queries.length) return;
    event.preventDefault();
    const index = queries.findIndex(item => item.id === activeQueryId);
    const delta = event.key === 'ArrowRight' ? 1 : -1;
    switchQuery(queries[(index + delta + queries.length) % queries.length].id);
  });
  resultTabs?.addEventListener("keydown", event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const query = activeQuery();
    if (!query?.resultTabs.length) return;
    event.preventDefault();
    const index = query.resultTabs.findIndex(item => item.id === query.activeResultTabId);
    const delta = event.key === 'ArrowRight' ? 1 : -1;
    query.activeResultTabId = query.resultTabs[(index + delta + query.resultTabs.length) % query.resultTabs.length].id;
    renderResults();
  });
  installAutoPageLoader({
    container: results,
    canLoad: () => Boolean(!busy && activePagedResultTab()),
    loadNext: () => loadMore(activePagedResultTab()),
  });
  for (const eventName of ["input", "select", "click", "keyup"]) {
    draft.addEventListener(eventName, updateControls);
  }
  draft.addEventListener("input", persistDraft);
  draft.addEventListener("keydown", event => {
    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    void run(false);
  });
  updateControls();

  return Object.freeze({
    syncWorkspace,
    clearDraft,
    setDraft,
    reset,
    addQuery,
    run: () => run(false),
    runAll: () => run(true),
    cancel,
    hasOpenTransaction: () => transactionOpen(transaction),
  });
}
