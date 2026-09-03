import { ApiError } from "./api.js";
import { element, replace } from "./dom.js";
import { createDataGrid, formatDataCell } from "./data-grid.js";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const POLL_INTERVAL_MS = 350;

export function formatConsoleCell(value) {
  return formatDataCell(value);
}

function consoleId() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `con_${Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")}`;
}

function statePanel(mark, title, message, { error = false } = {}) {
  return element("div", { className: `sql-results-state${error ? " error" : ""}` }, [
    element("span", { text: mark }),
    element("strong", { text: title }),
    element("p", { text: message }),
  ]);
}

export function createSqlConsole({
  api,
  draft,
  runButton,
  cancelButton,
  editorStatus,
  results,
  getWorkspace,
  showToast,
  onError,
  onRunStart = null,
}) {
  let settings = null;
  let workspaceId = null;
  let execution = null;
  let resultPages = new Map();
  let generation = 0;
  let pollTimer = null;
  let busy = false;

  function workspace() {
    const current = getWorkspace();
    return current?.id === workspaceId ? current : null;
  }

  function available(current = workspace()) {
    return Boolean(current?.connectionId && current?.database && current?.namespace);
  }

  function stopPolling() {
    globalThis.clearTimeout(pollTimer);
    pollTimer = null;
  }

  function updateControls() {
    const current = workspace();
    const active = execution && !TERMINAL_STATUSES.has(execution.status);
    runButton.disabled = busy || active || !available(current) || !draft.value.trim();
    cancelButton.hidden = !active;
    cancelButton.disabled = busy;
    draft.readOnly = Boolean(active);
    editorStatus.textContent = active
      ? `${execution.status === "reserved" ? "Queued" : "Running"} · read-only transaction`
      : available(current)
        ? "Draft stays in this browser until Run query is selected."
        : "A database-backed workspace is required to run SQL.";
  }

  function renderIdle() {
    if (!workspace()) {
      replace(results, statePanel("SQL", "No workspace open", "Open a database-backed workspace to query PostgreSQL."));
    } else if (!available()) {
      replace(results, statePanel("SQL", "No PostgreSQL target", "Detached designs do not have a database to query."));
    } else {
      replace(results, statePanel("RESULTS", "Ready for a query", "Run SELECT, WITH, VALUES, SHOW, or a read-only EXPLAIN statement."));
    }
  }

  function renderExecutionState() {
    const status = execution?.status || "reserved";
    replace(results, statePanel(
      status === "running" ? "RUNNING" : "QUEUED",
      status === "running" ? "PostgreSQL is executing the query" : "Waiting for execution",
      "The server owns this execution. You may cancel it without losing the editor draft.",
    ));
  }

  function renderFailure() {
    const statement = Number.isInteger(execution.errorStatementIndex)
      ? `Statement ${execution.errorStatementIndex + 1}. `
      : "";
    replace(results, statePanel(
      execution.status === "cancelled" ? "CANCELLED" : "QUERY ERROR",
      execution.status === "cancelled" ? "Execution cancelled" : "PostgreSQL rejected the query",
      `${statement}${execution.errorMessage || "The query did not produce a retained result."}`,
      { error: execution.status !== "cancelled" },
    ));
  }

  function renderResults() {
    if (!execution?.results?.length) {
      replace(results, statePanel("COMPLETE", "Query completed", "The statement returned no tabular result."));
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const summary of execution.results) {
      const page = resultPages.get(summary.id);
      const card = element("article", { className: "sql-result-card" });
      const header = element("header", {}, [
        element("div", {}, [
          element("small", { text: `STATEMENT ${summary.statementIndex + 1}` }),
          element("strong", { text: summary.command }),
        ]),
        element("span", { text: `${summary.rowCount ?? 0} ${summary.rowCount === 1 ? "row" : "rows"}` }),
      ]);
      card.append(header);
      if (!page) {
        card.append(statePanel("…", "Loading retained result", "Reading the first result page from the server."));
      } else if (!page.rows.length) {
        card.append(statePanel("0", "No rows returned", "PostgreSQL returned the column shape without any records."));
      } else {
        card.append(createDataGrid({ columns: page.columns, rows: page.rows, className: "sql-result-viewport" }));
        if (page.nextCursor || page.truncated) {
          const footer = element("footer");
          const note = page.truncated
            ? "Result reached the server retention limit."
            : `${page.rows.length} of ${summary.rowCount} rows loaded.`;
          footer.append(element("span", { text: note }));
          if (page.nextCursor) {
            const more = element("button", { className: "ui-button compact", type: "button", text: "Load more" });
            more.addEventListener("click", () => loadMore(summary.id));
            footer.append(more);
          }
          card.append(footer);
        }
      }
      fragment.append(card);
    }
    replace(results, fragment);
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
    renderResults();
    for (const summary of execution.results) {
      const page = await api.getConsoleResultPage(current.id, execution.id, summary.id);
      if (runGeneration !== generation || workspace()?.id !== current.id) return;
      resultPages.set(summary.id, page);
      renderResults();
    }
  }

  async function poll(runGeneration) {
    const current = workspace();
    if (!current || runGeneration !== generation || !execution) return;
    try {
      execution = await api.getConsoleExecution(current.id, execution.id);
      if (runGeneration !== generation) return;
      updateControls();
      if (!TERMINAL_STATUSES.has(execution.status)) {
        renderExecutionState();
        pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
      } else if (execution.status === "succeeded") {
        await loadFirstPages(runGeneration);
      } else renderFailure();
    } catch (error) {
      if (runGeneration !== generation) return;
      replace(results, statePanel("ERROR", "Execution status unavailable", error.message, { error: true }));
      onError(error);
    } finally {
      if (runGeneration === generation) {
        busy = false;
        updateControls();
      }
    }
  }

  async function run() {
    const current = workspace();
    if (!available(current) || busy || !draft.value.trim()) return;
    if (typeof onRunStart === "function") onRunStart();
    stopPolling();
    const previousExecution = execution;
    const runGeneration = ++generation;
    busy = true;
    execution = null;
    resultPages = new Map();
    void release(current.id, previousExecution);
    updateControls();
    replace(results, statePanel("SUBMITTING", "Validating query", "The server is binding this draft to the current PostgreSQL target."));
    try {
      const policy = await loadSettings(runGeneration);
      if (!policy || runGeneration !== generation) return;
      execution = await api.createConsoleExecution(current.id, {
        consoleId: consoleId(),
        expectedWorkspaceRevision: current.revision,
        expectedSettingsRevision: policy.revision,
        mode: "managed_read",
        statements: [draft.value],
      });
      if (runGeneration !== generation) return;
      busy = false;
      renderExecutionState();
      updateControls();
      pollTimer = globalThis.setTimeout(() => poll(runGeneration), POLL_INTERVAL_MS);
    } catch (error) {
      if (runGeneration !== generation) return;
      if (error instanceof ApiError && error.code === "console_settings_changed") {
        settings = null;
      }
      busy = false;
      execution = null;
      replace(results, statePanel("QUERY ERROR", "Query could not start", error.message, { error: true }));
      updateControls();
      onError(error);
    }
  }

  async function cancel() {
    const current = workspace();
    if (!current || !execution || TERMINAL_STATUSES.has(execution.status) || busy) return;
    busy = true;
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

  async function loadMore(resultId) {
    const current = workspace();
    const page = resultPages.get(resultId);
    if (!current || !execution || !page?.nextCursor || busy) return;
    busy = true;
    updateControls();
    try {
      const next = await api.getConsoleResultPage(current.id, execution.id, resultId, { cursor: page.nextCursor });
      resultPages.set(resultId, { ...next, rows: [...page.rows, ...next.rows] });
      renderResults();
    } catch (error) {
      onError(error);
    } finally {
      busy = false;
      updateControls();
    }
  }

  async function release(previousWorkspaceId, previousExecution) {
    if (!previousWorkspaceId || !previousExecution) return;
    try {
      if (!TERMINAL_STATUSES.has(previousExecution.status)) {
        await api.cancelConsoleExecution(previousWorkspaceId, previousExecution.id);
      }
      await Promise.all((previousExecution.results || []).map(summary => (
        api.closeConsoleResult(previousWorkspaceId, previousExecution.id, summary.id)
      )));
    } catch (error) {
      if (!(error instanceof ApiError && [404, 410].includes(error.status))) return;
    }
  }

  function syncWorkspace(current) {
    const nextId = current?.id || null;
    if (nextId !== workspaceId) {
      const previousWorkspaceId = workspaceId;
      const previousExecution = execution;
      generation += 1;
      stopPolling();
      workspaceId = nextId;
      execution = null;
      resultPages = new Map();
      busy = false;
      void release(previousWorkspaceId, previousExecution);
      renderIdle();
    }
    updateControls();
  }

  function clearDraft() {
    if (!draft.value) return false;
    draft.value = "";
    updateControls();
    return true;
  }

  function setDraft(value) {
    draft.value = value;
    updateControls();
  }

  function reset(value = "") {
    const previousExecution = execution;
    generation += 1;
    stopPolling();
    execution = null;
    resultPages = new Map();
    busy = false;
    draft.value = value;
    void release(workspaceId, previousExecution);
    renderIdle();
    updateControls();
  }

  runButton.addEventListener("click", run);
  cancelButton.addEventListener("click", cancel);
  draft.addEventListener("input", updateControls);
  draft.addEventListener("keydown", event => {
    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    void run();
  });
  updateControls();

  return Object.freeze({ syncWorkspace, clearDraft, setDraft, reset, run, cancel });
}
