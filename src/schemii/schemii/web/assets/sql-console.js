import { createQueryPlanView, parseQueryPlan, downloadArtifact } from "#common/query-plan.js";
import { createElapsedTimer, formatElapsed } from "#common/elapsed-time.js";
import { ApiError } from "#common/http.js";
import { element, replace } from "#common/dom.js";
import {
  appendDataGridPage,
  createDataGrid,
  formatDataCell,
  installAutoPageLoader,
} from "#common/data-grid.js";
import { sqlForRun, sqlStatementRanges } from "./sql-statements.js";
import { createIconButton } from "./ui.js";
import { openConsoleCopy } from "./console-copy.js";
import { openConsoleTransactions } from "./console-transactions.js";

const TERMINAL_EXECUTION_STATUSES = new Set(["succeeded", "failed", "cancelled", "uncertain"]);
const POLL_INTERVAL_MS = 350;

export function formatConsoleCell(value) {
  return formatDataCell(value);
}

export function formatConsoleActivitySummary(activity, execution = null, runningElapsedMs = null) {
  const rawExecution = Boolean(execution?.sessionId);
  const phase = rawExecution ? execution.status : (activity.phase ?? execution?.status ?? activity.status);
  const elapsedMs = rawExecution
    ? (TERMINAL_EXECUTION_STATUSES.has(execution.status) ? execution.elapsedMs : runningElapsedMs)
    : activity.elapsedMs;
  return [
    phase,
    typeof elapsedMs === "number" && Number.isFinite(elapsedMs) && elapsedMs >= 0
      ? formatElapsed(elapsedMs) : null,
    "Query activity",
  ].filter(Boolean).join(" · ");
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
  return Boolean(transaction && ["intrans", "inerror"].includes(transaction.transactionStatus));
}

function sessionOpen(session) { return Boolean(session && session.status !== "closed"); }

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
  let operationExecutionId = null;
  let cancelling = false;
  let exportWatch = null;
  let activityTimer = null;
  let activityGeneration = 0;
  let explainRequest = null;
  let comparisonPlan = null;
  let elapsedLabel = "Elapsed";
  const elapsedDisplay = element("p", { className: "sql-query-elapsed", hidden: true, attrs: { "aria-label": "Query duration" } });
  const elapsedTimer = createElapsedTimer({ onTick: milliseconds => {
    elapsedDisplay.textContent = `${elapsedLabel}: ${formatElapsed(milliseconds)}`;
  } });
  results.before(elapsedDisplay);
  function finishElapsed(label) {
    elapsedLabel = label;
    return elapsedTimer.stop();
  }
  const activityPanel = element("details", { className: "sql-query-activity", hidden: true, attrs: { open: "" } });
  const activitySummary = element("summary", { text: "Query activity" });
  const activityBody = element("div", { attrs: { role: "status", "aria-live": "polite" } });
  activityPanel.append(activitySummary, activityBody);
  results.before(activityPanel);
  const noticesPanel = element("details", { hidden: true, className: "sql-query-notices" });
  noticesPanel.append(element("summary", { text: "PostgreSQL notices" }));
  const noticesBody = element("pre");
  noticesPanel.append(noticesBody); results.before(noticesPanel);
  const explainButton = createIconButton({ icon: "explain", label: "Explain", tooltip: "Explain query plan", className: "compact" });
  const analyzeButton = createIconButton({ icon: "analyze", label: "Run & Analyze", tooltip: "Run and analyze query plan", className: "compact" });
  const planActions = element("div", { className: "query-plan-actions" }, [explainButton, analyzeButton]);
  const transactionCount = element("span", { className: "sql-transaction-count", hidden: true, attrs: { "aria-hidden": "true" } });
  const transactionsButton = createIconButton({ icon: "transactions", label: "Open transactions", className: "compact" });
  transactionsButton.append(transactionCount);
  if (supportsWrite) planActions.append(transactionsButton);
  async function refreshOpenTransactions() {
    const current = workspace();
    if (!supportsWrite || !available(current) || !api.listRawSessions) return;
    try {
      const response = await api.listRawSessions(current.id);
      if (workspace()?.id !== current.id) return;
      const count = response.sessions.filter(session => transactionOpen(session) || (session.status === "running" && session.transactionStartedAt)).length;
      transactionCount.textContent = count ? String(count) : "";
      transactionCount.hidden = !count;
      transactionsButton.dataset.uiTooltip = count ? `Open transactions (${count})` : "Open transactions";
      transactionsButton.classList.toggle("has-open-transactions", count > 0);
    } catch { transactionCount.textContent = "?"; transactionCount.hidden = false; }
  }
  transactionsButton.onclick = () => {
    if (!available()) return;
    void openConsoleTransactions({ workspaceId, currentSessionId: transaction?.id,
      onChange: async () => { await refreshTransaction(workspace(), generation); await refreshOpenTransactions(); updateControls(); } });
  };
  if (supportsWrite) globalThis.addEventListener("focus", () => void refreshOpenTransactions());
  const beginButton = createIconButton({ icon: "begin", label: "Begin", tooltip: "Begin transaction", className: "compact" });
  const autoCommit = element("input", { type: "checkbox", attrs: { "aria-label": "Auto-commit" } });
  const commitTiming = element("select", { attrs: { "aria-label": "Auto-commit timing" } }, [
    element("option", { text: "Each statement", attrs: { value: "each_statement" } }),
    element("option", { text: "Whole run", attrs: { value: "whole_run" } }),
  ]);
  const commitChoice = element("div", { className: "sql-commit-choice" }, [element("label", {}, [autoCommit, "Auto-commit"]), commitTiming]);
  if (supportsWrite) transactionBar?.querySelector(".sql-transaction-controls")?.append(commitChoice, beginButton, rollbackButton, commitButton);
  autoCommit.onchange = () => updateControls();
  commitTiming.onchange = () => updateControls();
  const selectedCommitMode = () => autoCommit.checked ? commitTiming.value : "manual";
  const copyButton = createIconButton({ icon: "copy-file", label: "COPY file", tooltip: "Upload or download a COPY file", className: "compact" });
  if (supportsWrite) planActions.append(copyButton);
  beginButton.onclick = () => void executeControl("BEGIN");
  copyButton.onclick = async () => {
    if (busy || operationActive() || mode !== "explicit") return;
    const current = workspace();
    busy = true; updateControls();
    try {
      const policy = await loadSettings(generation);
      const session = await ensureTransaction(current, policy, generation);
      await openConsoleCopy({ workspaceId: current.id, sessionId: session.id, sql: selectedSource(draft), commitMode: selectedCommitMode(),
        onError, onStatus: async () => { await refreshTransaction(current, generation); updateControls(); } });
    } catch (error) { onError(error); }
    finally { busy = false; updateControls(); }
  };
  if (api.explainConsoleQuery) {
    const editorActions = draft.closest(".sql-editor-panel")?.querySelector(".sql-pane-actions");
    if (editorActions) editorActions.prepend(...planActions.children);
    else draft.before(planActions);
  }

  function operationActive() { return executionActive(execution) || Boolean(operationExecutionId); }

  async function monitor(runGeneration, executionId, monitorGeneration) {
    if (!api.getConsoleActivity || (runGeneration !== generation || monitorGeneration !== activityGeneration)) return;
    activityPanel.hidden = false;
    try {
      const activity = execution?.sessionId && execution.id === executionId
        ? await api.getRawActivity(workspaceId, execution.sessionId)
        : await api.getConsoleActivity(executionId);
      if ((runGeneration !== generation || monitorGeneration !== activityGeneration)) return;
      if (exportWatch?.id === executionId) {
        if (activity.phase === "exporting") exportWatch.started = true;
        operationExecutionId = activity.cancellable ? executionId : null;
        if ((exportWatch.started && activity.phase !== "exporting") || (!exportWatch.started && Date.now() > exportWatch.deadline)) exportWatch = null;
        updateControls();
      }
      const currentExecution = execution?.id === executionId ? execution : null;
      activitySummary.textContent = formatConsoleActivitySummary(activity, currentExecution, elapsedTimer.value());
      const parts = [
        activity.statementIndex == null ? null : `Statement ${activity.statementIndex + 1}`,
        Array.isArray(activity.completedStatementIndexes)
          ? `${activity.completedStatementIndexes.length} statements completed`
          : Number.isInteger(currentExecution?.completedStatements)
            ? `${currentExecution.completedStatements} statements completed` : null,
        activity.fetchedRows == null ? null : `${activity.fetchedRows} rows fetched`,
        activity.transactionStatus ? `Transaction: ${activity.transactionStatus}` : null,
        activity.databaseState,
        activity.waitEvent ? `Waiting: ${activity.waitEventType || "database"} / ${activity.waitEvent}` : null,
        activity.blockerPids?.length ? `Blocked by PID: ${activity.blockerPids.join(", ")}` : null,
        activity.statementTimeoutMs == null
          ? (activity.configuredStatementTimeoutMs == null ? null : `Configured statement timeout: ${activity.configuredStatementTimeoutMs === 0 ? "unlimited" : `${activity.configuredStatementTimeoutMs} ms`}`)
          : `Statement timeout: ${activity.statementTimeoutMs === 0 ? "unlimited" : `${activity.statementTimeoutMs} ms`}`,
        activity.lockTimeoutMs == null
          ? (activity.configuredLockTimeoutMs == null ? null : `Configured lock timeout: ${activity.configuredLockTimeoutMs === 0 ? "unlimited" : `${activity.configuredLockTimeoutMs} ms`}`)
          : `Lock timeout: ${activity.lockTimeoutMs === 0 ? "unlimited" : `${activity.lockTimeoutMs} ms`}`,
        activity.monitoringMessage,
      ].filter(Boolean);
      activityBody.textContent = parts.join(" · ");
    } catch (error) {
      if ((runGeneration !== generation || monitorGeneration !== activityGeneration)) return;
      activityBody.textContent = `Live monitoring unavailable: ${error.message}. Query execution is independent of monitoring.`;
    }
    if (runGeneration === generation && monitorGeneration === activityGeneration && (operationActive() || exportWatch)) activityTimer = globalThis.setTimeout(() => monitor(runGeneration, executionId, monitorGeneration), 750);
  }

  function startMonitoring(executionId) {
    globalThis.clearTimeout(activityTimer);
    void monitor(generation, executionId, ++activityGeneration);
  }

  async function duringOperation(executionId, callback) {
    const operationGeneration = generation;
    operationExecutionId = executionId;
    updateControls();
    startMonitoring(executionId);
    try { return await callback(); }
    finally {
      if (operationGeneration === generation) {
        operationExecutionId = null;
        updateControls();
        startMonitoring(executionId);
      }
    }
  }

  let mode = "managed_read";
  let pendingInitialDraft = typeof initialDraft === "string" && initialDraft.trim()
    ? initialDraft
    : null;
  let hasPendingUnscopedDraft = false;

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
    globalThis.clearTimeout(activityTimer);
    elapsedTimer.stop();
  }

  function updateModeControls() {
    if (!supportsWrite) return;
    const isWrite = mode === "explicit";
    const active = operationActive();
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
      const label = isWrite ? "Read-only" : "Write";
      writeModeToggleButton.setAttribute("aria-label", label);
      writeModeToggleButton.dataset.uiTooltip = label;
    }

    if (modeRoot) modeRoot.dataset.writeMode = String(isWrite);
    if (transactionBar) transactionBar.hidden = !isWrite;

    transactionStatus.title = transaction?.idleTimeoutSeconds
      ? `Idle sessions close after ${transaction.idleTimeoutSeconds / 60} minutes; uncommitted changes roll back. See Open transactions for the remaining time.` : "";
    const open = transactionOpen(transaction);
    commitButton.hidden = !isWrite;
    rollbackButton.hidden = !isWrite;
    beginButton.hidden = !isWrite;
    commitButton.disabled = busy || active || !open;
    rollbackButton.disabled = busy || active || !open;
    beginButton.disabled = busy || active || open;
    autoCommit.disabled = busy || active || open;
    commitTiming.disabled = busy || active || open || !autoCommit.checked;
    if (!isWrite) transactionStatus.textContent = "Read-only · database changes are not permitted";
    else if (transaction?.status === "running" && transaction.transactionStartedAt) {
      transactionStatus.textContent = "Transaction open · statement running";
    } else if (transaction?.transactionStatus === "inerror") {
      transactionStatus.textContent = "Transaction aborted · use ROLLBACK or ROLLBACK TO SAVEPOINT";
    } else if (transaction?.transactionStatus === "intrans") {
      transactionStatus.textContent = "Transaction open · changes uncommitted";
    } else if (transaction?.transactionStatus === "unknown") {
      transactionStatus.textContent = "Connection state unknown · verify PostgreSQL before retrying writes";
    } else transactionStatus.textContent = autoCommit.checked
      ? `No transaction open · auto-commit ${commitTiming.value === "whole_run" ? "after the whole run" : "after each statement"}`
      : "No transaction open · next Run starts a transaction";
    transactionStatus.dataset.compact = transaction?.transactionStatus === "inerror" ? "Aborted"
      : transaction?.status === "running" ? "Running" : open ? "Open"
      : transaction?.transactionStatus === "unknown" ? "Unknown" : "Idle";
    transactionStatus.title = `${transactionStatus.textContent}${transaction?.idleTimeoutSeconds ? ` · Idle sessions close after ${transaction.idleTimeoutSeconds / 60} minutes; uncommitted changes roll back.` : ""}`;
  }

  function updateControls() {
    const current = workspace();
    const active = operationActive();
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
    cancelButton.disabled = cancelling;
    explainButton.disabled = runDisabled;
    analyzeButton.disabled = explainButton.disabled;
    copyButton.disabled = !available() || active || busy || mode !== "explicit" || !runnable;
    transactionsButton.disabled = !available();
    draft.readOnly = active;
    for (const button of saveQueryButtons) {
      if (button) button.disabled = active || !draft.value.trim();
    }
    if (editorSummary) editorSummary.textContent = targetDescription(draft);

    if (active) {
      editorStatus.textContent = `${operationExecutionId ? "Fetching / exporting" : execution.status === "reserved" ? "Queued" : "Running"} · ${mode === "explicit" ? (transactionOpen(transaction) ? "transaction" : "write session") : "read-only"}`;
    } else if (!available(current)) {
      editorStatus.textContent = "A database-backed workspace is required to run SQL.";
    } else if (transaction?.transactionStatus === "inerror") {
      editorStatus.textContent = "PostgreSQL aborted this transaction. Run ROLLBACK or recover with ROLLBACK TO SAVEPOINT.";
    } else if (mode === "explicit" && transactionOpen(transaction)) {
      editorStatus.textContent = "Changes are uncommitted. Typed transaction commands and the buttons act on this same session.";
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
    if (queryId === activeQueryId || busy || operationActive()) return;
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
    if (busy || operationActive()) return;
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
    if (busy || operationActive()) return;
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
      cancelled ? "Execution cancelled" : execution.status === "uncertain" ? "Outcome uncertain · verify before retrying" : "PostgreSQL rejected the query",
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
    setResultsSummary(tab.elapsedMs == null ? tab.meta : `${tab.meta} · First results in ${formatElapsed(tab.elapsedMs)}`);
    if (tab.kind === "error") {
      replace(results, statePanel("QUERY ERROR", "PostgreSQL rejected the query", tab.message, { error: true }));
      return;
    }
    if (tab.plan) {
      replace(results, createQueryPlanView(tab.plan, { comparison: comparisonPlan, onCompare: record => {
        comparisonPlan = record;
        showToast("Plan kept for comparison with the next plan.");
        renderResults();
      } }));
      return;
    }
    const { summary, page } = tab;
    const card = element("article", { className: "sql-result-card" });
    card.append(element("header", {}, [
      element("div", {}, [element("small", { text: `STATEMENT ${summary.statementIndex + 1}` }), element("strong", { text: summary.command })]),
      element("span", { text: summary.rowCount == null ? "Paged result" : `${summary.rowCount} ${summary.rowCount === 1 ? "row" : "rows"}` }),
    ]));
    if (tab.pageError) {
      const retry = element("button", { type: "button", text: "Retry result page" });
      retry.addEventListener("click", () => { if (!operationActive()) void loadTabFirstPage(tab, generation); });
      card.append(statePanel("FETCH ERROR", "Could not fetch result page", tab.pageError.message, { error: true }), retry);
    } else if (!page) card.append(statePanel("…", "Loading retained result", "Reading the first result page from the server."));
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
        label: tab.rawSessionId ? "Export displayed rows as CSV" : "Export complete result as CSV",
        tooltip: tab.rawSessionId ? "Export displayed rows; use COPY for a full download" : "Export CSV",
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
    if (page.nextCursor) return summary.rowCount == null ? `${count} rows loaded · scroll for more` : `${count} of ${summary.rowCount} rows loaded · scroll for more`;
    if (page.truncated) return page.rawPreview
      ? `${count} rows displayed · use COPY TO STDOUT to download the full result`
      : `${count} rows loaded · result reached the server retention limit`;
    return summary.rowCount == null ? `${count} rows loaded · end of result` : `${count} of ${summary.rowCount} rows loaded`;
  }

  function activePagedResultTab() {
    const query = activeQuery();
    if (!query) return null;
    const tab = query.resultTabs.find(item => item.id === query.activeResultTabId);
    return tab?.kind === "result" && tab.page?.nextCursor ? tab : null;
  }

  async function loadSettings(runGeneration) {
    const response = await api.getConsoleSettings();
    if (runGeneration !== generation) return null;
    settings = response;
    return response;
  }

  // The main console and inspector share this preference and component. It only
  // sizes future result pages; it never unlocks writes or changes retained pages.
  if (api.updateConsoleSettings) {
    const preferences = element("details", { className: "sql-console-preferences" });
    preferences.append(element("summary", { text: "Result preferences" }));
    const pageSize = element("input", { type: "number", attrs: { min: "1", step: "1", required: "", "aria-label": "Rows per page" } });
    const save = createIconButton({ icon: "save", label: "Save result preferences", tooltip: "Save result preferences", className: "compact" });
    save.type = "submit";
    const feedback = element("small", { attrs: { role: "status" } });
    const form = element("form", { className: "sql-console-preferences-form" }, [
      element("label", {}, ["Rows per page", pageSize]), save,
      element("small", { text: "Sets read-only page size and write-session preview size. Use COPY for full write-session output." }), feedback,
    ]);
    preferences.append(form);
    results.before(preferences);
    let preferenceRevision = null;
    let savingPreferences = false;
    async function reloadPreferences() {
      save.disabled = true;
      feedback.textContent = "Loading…";
      try {
        const current = await api.getConsoleSettings();
        preferenceRevision = current.revision;
        pageSize.value = current.rowPageSize;
        pageSize.max = current.maximumRowPageSize;
        feedback.textContent = `Administrator maximum: ${current.maximumRowPageSize} rows per page.`;
        save.disabled = false;
      } catch (error) {
        feedback.textContent = error.message;
      }
    }
    preferences.addEventListener("toggle", () => {
      if (preferences.open && !savingPreferences) void reloadPreferences();
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (savingPreferences || save.disabled || !form.reportValidity()) return;
      savingPreferences = true;
      save.disabled = true;
      try {
        settings = await api.updateConsoleSettings({ expectedRevision: preferenceRevision, rowPageSize: Number(pageSize.value) });
        preferenceRevision = settings.revision;
        feedback.textContent = "Saved for future queries. Existing results and transactions are unchanged.";
      } catch (error) {
        if (error.code === "console_settings_changed") {
          await reloadPreferences();
          feedback.textContent = "Preferences changed elsewhere. Current values loaded; review and save again.";
        } else feedback.textContent = error.message;
      } finally {
        savingPreferences = false;
        save.disabled = false;
      }
    });
  }

  async function loadFirstPages(runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation) return;
    const query = activeQuery();
    if (!query) return;
    const summaries = execution.results.map((summary, index) => ({ ...summary, id: summary.id || `${execution.id}_${index}`, statementIndex: summary.statementIndex ?? index }));
    const labels = summaries.map((_, index) => resultTabLabel(query, `Result ${index + 1}`));
    const newTabs = summaries.map((summary, index) => ({
      id: browserIdentifier("rtab"),
      label: labels[index],
      meta: `${summary.command} · ${summary.rowCount == null ? "paged result" : `${summary.rowCount} ${summary.rowCount === 1 ? "row" : "rows"}`}`,
      kind: "result",
      pinned: false,
      executionId: execution.id,
      rawSessionId: execution.sessionId || null,
      explainRequest: explainRequest ? { ...explainRequest } : null,
      summary,
      page: null,
    }));
    query.resultTabs.push(...newTabs);
    query.activeResultTabId = newTabs[0]?.id || query.activeResultTabId;
    renderResults();
    for (const tab of newTabs) await loadTabFirstPage(tab, runGeneration);
    if (runGeneration === generation) {
      const elapsed = finishElapsed(newTabs.some(tab => tab.pageError) ? "Stopped after" : newTabs.length ? "Time to first results" : "Finished in");
      for (const tab of newTabs) tab.elapsedMs = elapsed;
      renderResults();
    }
    await refreshLibrary(current.id, runGeneration);
  }

  async function loadTabFirstPage(tab, runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation) return;
    tab.pageError = null;
    renderResults();
    try {
      const page = tab.rawSessionId
        ? { columns: tab.summary.columns, rows: tab.summary.rows, truncated: tab.summary.truncated, nextCursor: null, rawPreview: true }
        : await duringOperation(tab.executionId, () => api.getConsoleResultPage(current.id, tab.executionId, tab.summary.id));
      if (runGeneration !== generation || workspace()?.id !== current.id) return;
      tab.page = page;
      if (tab.explainRequest) {
        tab.plan = { ...tab.explainRequest, sessionContext: Boolean(tab.rawSessionId), plan: parseQueryPlan(page.rows[0]?.[0]), executionId: tab.executionId, capturedAt: new Date().toISOString() };
        tab.label = tab.explainRequest.analyze ? "Analyzed plan" : "Estimated plan";
        tab.meta = tab.explainRequest.analyze ? "Measured execution plan" : "Estimated execution plan";
      }
    } catch (error) {
      if (runGeneration !== generation) return;
      tab.pageError = error;
      onError(error);
    }
    if (runGeneration === generation) renderResults();
  }

  async function refreshTransaction(current, expectedGeneration) {
    if (!transaction?.id || !current || expectedGeneration !== generation) return;
    try {
      transaction = await api.getRawSession(current.id, transaction.id);
    } catch (error) {
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) throw error;
      transaction = null;
    }
    storeValue(
      consoleStorageKey(current.id, "raw-session-id"),
      sessionOpen(transaction) ? transaction.id : null,
    );
  }

  async function poll(runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation || !execution) return;
    try {
      execution = execution.sessionId
        ? await api.getRawExecution(current.id, execution.sessionId, execution.id)
        : await api.getConsoleExecution(current.id, execution.id);
      if (runGeneration !== generation) return;
      if (mode === "explicit") await refreshTransaction(current, runGeneration);
      if (!TERMINAL_EXECUTION_STATUSES.has(execution.status)) {
        renderExecutionState();
        pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
        return;
      }

      void refreshOpenTransactions();
      noticesBody.textContent = (execution.notices || []).map(notice => typeof notice === "string" ? notice : notice.message || JSON.stringify(notice)).join("\n");
      noticesPanel.hidden = !noticesBody.textContent;
      if (execution.status === "succeeded") {
        await loadFirstPages(runGeneration);
      } else {
            if (execution.sessionId && execution.results?.length) await loadFirstPages(runGeneration);
        finishElapsed(execution.status === "cancelled" ? "Cancelled after" : "Failed after");
        renderFailure();
      }
    } catch (error) {
      if (runGeneration !== generation) return;
      setResultsSummary("Status unavailable");
      replace(results, statePanel("ERROR", "Execution status unavailable", error.message, { error: true }));
      if (executionActive(execution)) {
        const reconnect = element("button", { type: "button", text: "Reconnect to execution" });
        reconnect.addEventListener("click", () => { reconnect.disabled = true; void poll(runGeneration); });
        results.append(reconnect);
      }
      onError(error);
    } finally {
      if (runGeneration === generation) {
        busy = false;
        updateControls();
      }
    }
  }

  async function ensureTransaction(current, policy, runGeneration) {
    if (sessionOpen(transaction)) return transaction;
    const session = await api.createRawSession(current.id, {
      consoleId: consoleIdentifier,
      expectedWorkspaceRevision: current.revision,
      expectedSettingsRevision: policy.revision,
    });
    if (runGeneration !== generation) { await api.closeRawSession(current.id, session.id); return null; }
    transaction = session;
    storeValue(consoleStorageKey(current.id, "raw-session-id"), transaction.id);
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

  async function submitTransaction(current, policy, source, runGeneration, explain = null, commitMode = selectedCommitMode()) {
    const session = await ensureTransaction(current, policy, runGeneration);
    if (!session || runGeneration !== generation) return false;
    execution = explain === null
      ? await api.runRawSql(current.id, session.id, source, commitMode)
      : await api.explainRawSql(current.id, session.id, source, explain, commitMode);
    return runGeneration === generation;
  }

  async function run(all = false, explain = null, controlSql = null) {
    const current = workspace();
    const source = controlSql ?? selectedSource(draft, all);
    if (!available(current) || busy || operationActive() || !source) return;
    if (typeof onRunStart === "function") onRunStart();
    stopPolling();
    const query = activeQuery();
    const replacedTabs = query?.resultTabs.filter(tab => !tab.pinned) || [];
    if (query) {
      query.resultTabs = query.resultTabs.filter(tab => tab.pinned);
      query.activeResultTabId = query.resultTabs.at(-1)?.id || null;
    }
    const runGeneration = ++generation;
    elapsedLabel = "Elapsed";
    elapsedDisplay.hidden = false;
    elapsedTimer.start();
    busy = true;
    execution = null;
    explainRequest = explain === null ? null : { sql: source, analyze: explain };
    void releaseResultTabs(current.id, replacedTabs);
    updateControls();
    setResultsSummary("Submitting");
    replace(results, statePanel("SUBMITTING", "Validating query", "The server is binding the selected SQL to the current PostgreSQL target."));
    try {
      const policy = await loadSettings(runGeneration);
      if (!policy || runGeneration !== generation) return;
      let submitted = false;
      if (mode === "explicit") {
        submitted = await submitTransaction(current, policy, source, runGeneration, explain, controlSql ? "manual" : selectedCommitMode());
      } else if (explain !== null) {
        explainRequest.settings = policy;
        execution = await api.explainConsoleQuery(current.id, {
          consoleId: consoleIdentifier, expectedWorkspaceRevision: current.revision,
          expectedSettingsRevision: policy.revision, sql: source, analyze: explain,
        });
        submitted = true;
      } else {
        submitted = await submitRead(current, policy, source, runGeneration);
      }
      if (!submitted || runGeneration !== generation) {
        if (runGeneration === generation) finishElapsed("Finished in");
        return;
      }
      busy = false;
      startMonitoring(execution.id);
      renderExecutionState();
      updateControls();
      pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
    } catch (error) {
      if (runGeneration !== generation) return;
      if (error instanceof ApiError && error.code === "console_settings_changed") settings = null;
        busy = false;
      execution = null;
      finishElapsed("Could not start after");
      setResultsSummary("Could not start");
      replace(results, statePanel("QUERY ERROR", "Query could not start", error.message, { error: true }));
      updateControls();
      onError(error);
    }
  }

  async function cancel() {
    const current = workspace();
    if (!current || !operationActive() || cancelling) return;
    cancelling = true;
    updateControls();
    try {
      const cancellingOperation = Boolean(operationExecutionId);
      const executionId = operationExecutionId || execution.id;
      const cancelledExecution = execution?.sessionId && execution.id === executionId
        ? (await api.cancelRawSession(current.id, execution.sessionId), await api.getRawExecution(current.id, execution.sessionId, executionId))
        : await api.cancelConsoleExecution(current.id, executionId);
      if (execution?.id === executionId) execution = cancelledExecution;
      if (!cancellingOperation) {
        if (execution.status === "cancelled") renderFailure();
        else renderExecutionState();
      }
      showToast("Console cancellation requested.");
    } catch (error) {
      onError(error);
    } finally {
      cancelling = false;
      updateControls();
    }
  }

  async function executeControl(sql) {
    if (mode !== "explicit" || busy || operationActive()) return;
    await run(false, null, sql);
  }

  async function finishTransaction(action) {
    await executeControl(action.toUpperCase());
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
    if (progress) progress.textContent = `Loading rows ${page.rows.length + 1}–${tab.summary.rowCount == null ? page.rows.length + 100 : Math.min(page.rows.length + 100, tab.summary.rowCount)}…`;
    busy = true;
    updateControls();
    try {
      const next = await duringOperation(tab.executionId, () => api.getConsoleResultPage(current.id, tab.executionId, tab.summary.id, { cursor: requestedCursor }));
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
    await Promise.allSettled(tabs.filter(tab => tab.kind === "result" && !tab.rawSessionId).map(tab => (
      api.closeConsoleResult(previousWorkspaceId, tab.executionId, tab.summary.id)
    )));
  }

  function exportResult(tab) {
    const current = workspace();
    if (tab?.rawSessionId && tab.page) {
      const csvCell = value => `"${String(value == null ? "" : typeof value === "object" ? JSON.stringify(value) : value).replaceAll('"', '""')}"`;
      const lines = [tab.page.columns.map(column => csvCell(column.name)), ...tab.page.rows.map(row => row.map(csvCell))].map(row => row.join(",")).join("\r\n");
      downloadArtifact("displayed-result.csv", new Blob([lines], { type: "text/csv" }));
      return;
    }
    if (!current || !tab?.executionId || !tab?.summary?.id || busy || operationActive()) return;
    const link = element("a", { attrs: {
      href: api.consoleResultExportUrl(current.id, tab.executionId, tab.summary.id),
      download: `${tab.label.replace(/[^a-z0-9_-]+/gi, "-") || "result"}.csv`,
    } });
    exportWatch = { id: tab.executionId, started: false, deadline: Date.now() + 15000 };
    link.click();
    startMonitoring(tab.executionId);
  }

  async function releaseExecution(previousWorkspaceId, previousExecution) {
    if (!previousWorkspaceId || !previousExecution) return;
    if (previousExecution.sessionId) return;
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
    if (!previousWorkspaceId || !sessionOpen(previousTransaction)) return;
    try {
      await api.closeRawSession(previousWorkspaceId, previousTransaction.id);
      storeValue(consoleStorageKey(previousWorkspaceId, "raw-session-id"), null);
    } catch (error) {
      onError(error);
    }
  }

  async function restoreTransaction(current, expectedGeneration) {
    if (!supportsWrite || !current?.id) return;
    const transactionId = storedValue(consoleStorageKey(current.id, "raw-session-id"));
    if (!/^raw_[0-9a-f]{32}$/.test(transactionId || "")) return;
    busy = true;
    updateControls();
    try {
      const restored = await api.getRawSession(current.id, transactionId);
      if (expectedGeneration !== generation || workspace()?.id !== current.id) return;
      transaction = restored;
      if (sessionOpen(restored)) {
        mode = "explicit";
        showToast("PostgreSQL session restored for this tab.");
      } else {
        storeValue(consoleStorageKey(current.id, "raw-session-id"), null);
      }
    } catch (error) {
      if (expectedGeneration !== generation) return;
      storeValue(consoleStorageKey(current.id, "raw-session-id"), null);
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) onError(error);
    } finally {
      if (expectedGeneration === generation) {
        busy = false;
        updateControls();
      }
    }
  }

  function setMode(nextMode) {
    if (!supportsWrite || !available() || operationActive() || busy) return;
    const next = nextMode === "explicit" ? "explicit" : "managed_read";
    if (next === mode) return;
    const leave = async () => {
      busy = true; updateControls();
      try {
        if (sessionOpen(transaction)) await api.closeRawSession(workspaceId, transaction.id);
        transaction = null;
        storeValue(consoleStorageKey(workspaceId, "raw-session-id"), null);
        mode = next;
      } catch (error) { onError(error); }
      finally { busy = false; updateControls(); }
    };
    if (next === "managed_read" && transactionOpen(transaction)) {
      if (typeof confirm === "function") confirm({ title: "Leave write session", message: "Closing this session rolls back its uncommitted transaction. Previously committed changes remain.", label: "Roll back and switch", tone: "danger", callback: leave });
      return;
    }
    if (next === "managed_read") void leave();
    else { mode = next; updateControls(); }
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
        const usePendingUnscopedDraft = nextId && hasPendingUnscopedDraft;
        const initialWorkspaceDraft = usePendingUnscopedDraft
          ? draft.value
          : pendingInitialDraft;
        const fallbackDraft = nextId
          ? initialWorkspaceDraft ?? storedValue(consoleStorageKey(nextId, "draft")) ?? ""
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
        if (nextId && initialWorkspaceDraft !== null) {
          const query = activeQuery();
          if (query) query.sql = initialWorkspaceDraft;
          draft.value = initialWorkspaceDraft;
          persistQueries();
          if (usePendingUnscopedDraft) hasPendingUnscopedDraft = false;
          else pendingInitialDraft = null;
        }
      } else {
        queries = nextId
          ? [{ id: browserIdentifier("qry"), name: "Query", sql: draft.value || "", resultTabs: [], activeResultTabId: null }]
          : [];
        activeQueryId = queries[0]?.id || null;
      }
      execution = null;
      operationExecutionId = null;
      comparisonPlan = null;
      exportWatch = null;
      activityPanel.hidden = true;
      elapsedDisplay.hidden = true;
      transaction = null;
        busy = false;
      mode = "managed_read";
      void releaseExecution(previousWorkspaceId, previousExecution);
      void Promise.all(previousQueries.map(query => releaseResultTabs(previousWorkspaceId, query.resultTabs)));
      void rollbackAbandonedTransaction(previousWorkspaceId, previousTransaction);
      renderIdle();
      if (current) void restoreTransaction(current, generation);
      void refreshOpenTransactions();
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

  explainButton.addEventListener("click", () => void run(false, false));
  analyzeButton.addEventListener("click", () => {
    if (typeof confirm === "function") confirm({
      title: "Run & Analyze", message: mode === "explicit"
        ? "Execute the selected statement in your current PostgreSQL session to measure its plan? ANALYZE executes writes too, using the session's transaction state."
        : "Execute the full selected read-only statement to measure its PostgreSQL plan? This can take as long as the query itself. The analysis uses a fresh read-only snapshot.",
      label: "Run & Analyze", tone: "primary", callback: () => run(false, true),
    });
    else void run(false, true);
  });
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
  draft.addEventListener("input", () => {
    if (!workspaceId) hasPendingUnscopedDraft = true;
  });
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
