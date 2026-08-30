(function (global) {
  "use strict";

  const shared = global.SchemiiShared = global.SchemiiShared || {};
  const modes = {
    managed_read: ["Managed read", "Pages from one retained read-only snapshot, then rolls back when exhausted, closed, expired, cancelled, or shut down."],
    managed: ["Managed all-or-nothing", "All statements succeed and commit together, or PostgreSQL rolls them back."],
    explicit: ["Explicit transaction", "Keeps one PostgreSQL transaction open until you commit or roll it back."],
    autocommit: ["Autocommit / maintenance", "Commits each statement independently; later failure does not undo earlier statements."],
  };

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function consoleResultResourceParts(resource) {
    const statement = resource?.statement ?? resource;
    const target = resource?.target;
    const consoleId = resource?.consoleId;
    if (!statement?.executionId || !statement?.resultId || !target?.profileId || !target?.database || !target?.namespace || !consoleId) {
      throw new Error("The retained Console result identity is incomplete");
    }
    return { statement, target, consoleId };
  }

  function consoleResultResourceUrl(resource, { cursor = null } = {}) {
    const { statement, target, consoleId } = consoleResultResourceParts(resource);
    const query = new URLSearchParams({
      consoleId, database: target.database, namespace: target.namespace,
      statementIndex: String(statement.statementIndex), resultIndex: String(statement.resultIndex),
    });
    if (cursor) query.set("cursor", cursor);
    return `/api/postgres/profiles/${encodeURIComponent(target.profileId)}/console/executions/${encodeURIComponent(statement.executionId)}/results/${encodeURIComponent(statement.resultId)}?${query}`;
  }

  async function pageConsoleResultResource(resource, request) {
    const { statement } = consoleResultResourceParts(resource);
    if (!statement.hasMore || !statement.nextCursor) return statement;
    const rows = statement.rows;
    const page = await request(consoleResultResourceUrl(resource, { cursor: statement.nextCursor }));
    rows.push(...page.rows);
    Object.assign(statement, page, { rows });
    return statement;
  }

  async function drainConsoleResultResource(resource, request) {
    let { statement } = consoleResultResourceParts(resource);
    while (statement.hasMore) statement = await pageConsoleResultResource(resource, request);
    return statement;
  }

  async function releaseConsoleResultResource(resource, request, { keepalive = false } = {}) {
    const { statement } = consoleResultResourceParts(resource);
    if (!statement.hasMore) return statement;
    await request(consoleResultResourceUrl(resource), { method: "DELETE", ...(keepalive ? { keepalive: true } : {}) });
    Object.assign(statement, { hasMore: false, nextCursor: null, resourceState: "closed", closureEvents: ["closed"] });
    return statement;
  }

  function createPostgresConsole({ button, postgresClient, getTarget, targetControls = [], onCommittedWrite = () => {} }) {
    if (!button || !postgresClient || typeof getTarget !== "function") throw new Error("PostgreSQL Console adapter is incomplete");
    const dialog = document.createElement("dialog");
    dialog.className = "shared-postgres-console";
    dialog.id = `postgres-console-${crypto.randomUUID()}`;
    const titleId = `${dialog.id}-title`;
    dialog.setAttribute("aria-labelledby", titleId);
    button.setAttribute("aria-controls", dialog.id);
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-expanded", "false");
    dialog.innerHTML = `<div class="shared-console-shell">
      <header><div><span>Live PostgreSQL</span><h2 id="${titleId}">Console</h2></div><button type="button" data-console-close aria-label="Close Console">x</button></header>
      <dl class="shared-console-target" aria-label="Exact PostgreSQL target"><div><dt>Profile</dt><dd data-console-profile>Not selected</dd></div><div><dt>Database</dt><dd data-console-database>Not selected</dd></div><div><dt>Namespace</dt><dd data-console-namespace>Not selected</dd></div></dl>
      <section class="shared-console-settings"><label>Mode<select data-console-mode>${Object.entries(modes).map(([value, item]) => `<option value="${value}">${item[0]}</option>`).join("")}</select></label><p data-console-consequence></p><div class="shared-console-limits"><label>Statements<input type="number" min="1" max="100" data-console-statement-limit></label><label>Rows / result<input type="number" min="1" max="500" data-console-row-page-size></label></div><label class="shared-console-intent"><input type="checkbox" data-console-intent> Enable durable human write intent for this application</label><button type="button" data-console-save-settings>Save Console settings</button><span data-console-settings-status role="status"></span></section>
      <section class="shared-console-transaction" data-console-transaction hidden><strong>Explicit transaction: <span data-console-transaction-state>closed</span></strong><div><button type="button" data-console-commit>Commit</button><button type="button" data-console-rollback>Roll back</button></div></section>
      <textarea data-console-sql aria-label="PostgreSQL SQL" spellcheck="false" placeholder="SELECT current_database();"></textarea>
      <footer><span data-console-status role="status" aria-live="polite">Ready</span><div><button type="button" data-console-stop hidden>Stop</button><button type="button" data-console-run>Run</button></div></footer>
      <section class="shared-console-results" data-console-results aria-label="Console results"></section>
    </div>`;
    document.body.append(dialog);
    const element = name => dialog.querySelector(`[data-console-${name}]`);
    const state = { settings: null, targetKey: null, transactionId: null, transactionState: "closed", running: false, busyPhase: "idle", activeExecution: null, consoleId: crypto.randomUUID(), results: new Map(), closingResults: new Set() };

    function target() {
      const value = getTarget();
      return value && value.profileId && value.database && value.namespace && /^[0-9a-f]{64}$/.test(value.profileFingerprint || "") ? value : null;
    }

    function targetKey(value = target()) {
      return value ? `${value.profileId}\0${value.profileFingerprint}\0${value.database}\0${value.namespace}` : "";
    }

    function activeTransaction() {
      return Boolean(state.transactionId);
    }

    function guardTargetChange(event) {
      if (!activeTransaction() && !state.running) return true;
      event?.preventDefault();
      event?.stopImmediatePropagation();
      element("status").textContent = state.running ? "Stop or wait for the active execution before changing the target." : "Commit or roll back the active transaction before changing the target.";
      return false;
    }

    function setRunning(running, phase = running ? "preparing" : "idle") {
      state.running = running;
      state.busyPhase = phase;
      element("run").disabled = running;
      element("stop").hidden = phase !== "dispatched" || !state.activeExecution;
      element("stop").disabled = phase !== "dispatched" || !state.activeExecution || state.activeExecution.cancelRequested;
      element("sql").readOnly = running;
      for (const name of ["mode", "intent", "statement-limit", "row-page-size", "save-settings", "commit", "rollback"]) element(name).disabled = running;
      element("close").disabled = running;
    }

    function renderTarget() {
      const value = target();
      element("profile").textContent = value?.profile || value?.profileId || "Not selected";
      element("database").textContent = value?.database || "Not selected";
      element("namespace").textContent = value?.namespace || "Not selected";
      state.targetKey = targetKey(value);
    }

    function renderMode() {
      const mode = element("mode").value;
      element("consequence").textContent = modes[mode][1];
      element("transaction").hidden = mode !== "explicit" && !activeTransaction();
      element("transaction-state").textContent = state.transactionState;
    }

    async function loadSettings() {
      state.settings = await postgresClient.request("/api/postgres/console/settings");
      element("mode").value = state.settings.defaultMode;
      element("intent").checked = state.settings.writeIntent === "enabled";
      element("statement-limit").value = state.settings.statementLimit;
      element("row-page-size").value = state.settings.rowPageSize;
      element("settings-status").textContent = `Revision ${state.settings.revision}; ${state.settings.statementLimit} statements, ${state.settings.rowPageSize} rows`;
      renderMode();
    }

    async function saveSettings() {
      if (!state.settings) return;
      element("save-settings").disabled = true;
      try {
        state.settings = await postgresClient.request("/api/postgres/console/settings", {
          method: "PUT",
          body: JSON.stringify({
            expectedRevision: state.settings.revision,
            writeIntent: element("intent").checked ? "enabled" : "disabled",
            defaultMode: element("mode").value,
            statementLimit: Number(element("statement-limit").value),
            rowPageSize: Number(element("row-page-size").value),
          }),
        });
        element("settings-status").textContent = `Saved revision ${state.settings.revision}; applies to new write-capable operations`;
      } catch (error) {
        element("settings-status").textContent = error.message;
        if (error.code === "console_settings_conflict") await loadSettings();
      } finally {
        element("save-settings").disabled = false;
      }
    }

    function requestBody(value, mode, executionId) {
      return {
        executionId, consoleId: state.consoleId, database: value.database, namespace: value.namespace,
        sql: element("sql").value, mode, settingsRevision: state.settings.revision,
        profileFingerprint: value.profileFingerprint,
      };
    }

    function resultCell(value) {
      if (value === null) return "NULL";
      if (typeof value === "object") return JSON.stringify(value);
      return String(value);
    }

    function renderResults() {
      if (!state.results.size) {
        element("results").innerHTML = '<p class="shared-console-empty">No row results.</p>';
        return;
      }
      element("results").innerHTML = [...state.results.values()].map(resource => {
        const headings = resource.columns.map(column => `<th>${escapeHtml(column.name)}</th>`).join("");
        const rows = resource.rows.map(row => `<tr>${row.map(value => `<td>${escapeHtml(resultCell(value))}</td>`).join("")}</tr>`).join("");
        const warning = `${resource.truncationEvents.length ? '<p class="shared-console-result-warning">Transport limits truncated this result. The shown row count is not the complete PostgreSQL result.</p>' : ""}${resource.hasMore ? `<p class="shared-console-result-pending">More rows remain in the retained ${escapeHtml(resource.snapshotRetention.replaceAll("_", " "))}; Load more and export continue without replaying SQL.</p>` : ""}`;
        return `<article class="shared-console-result ${resource.truncationEvents.length ? "truncated" : resource.hasMore ? "incomplete" : "complete"}" data-console-result="${escapeHtml(resource.resultId)}">
          <header><strong>Statement ${resource.statementIndex + 1}</strong><span>${resource.rows.length} loaded${resource.hasMore ? ", incomplete" : ""}</span></header>
          ${warning}<div class="shared-console-table"><table><thead><tr>${headings}</tr></thead><tbody>${rows}</tbody></table></div>
          <footer>${resource.hasMore ? `<button type="button" data-console-load-more="${escapeHtml(resource.resultId)}">Load more</button>` : '<span>Result exhausted</span>'}<button type="button" data-console-export="${escapeHtml(resource.resultId)}">Export JSON</button>${resource.hasMore ? `<button type="button" data-console-close-result="${escapeHtml(resource.resultId)}">Close result</button>` : ""}</footer>
        </article>`;
      }).join("");
    }

    function rememberResults(result, value) {
      state.results.clear();
      for (const statement of result.statements || []) {
        if (!statement.resultId || !statement.columns?.length) continue;
        state.results.set(statement.resultId, {
          ...statement, target: value, consoleId: state.consoleId, rows: [...statement.rows],
          truncationEvents: [...(statement.truncationEvents || statement.limitEvents || [])],
        });
      }
      renderResults();
    }

    async function loadMore(resultId, render = true) {
      const resource = state.results.get(resultId);
      if (!resource?.hasMore || !resource.nextCursor) return resource;
      await pageConsoleResultResource(resource, postgresClient.request);
      if (render) renderResults();
      return resource;
    }

    async function closeResult(resultId, { remove = false } = {}) {
      const resource = state.results.get(resultId);
      if (!resource || state.closingResults.has(resultId)) return;
      state.closingResults.add(resultId);
      try {
        if (resource.hasMore) await releaseConsoleResultResource(resource, postgresClient.request);
      } finally {
        state.closingResults.delete(resultId);
      }
      if (remove) state.results.delete(resultId);
      renderResults();
    }

    async function closeAllResults() {
      const open = [...state.results.values()].filter(resource => resource.hasMore && !state.closingResults.has(resource.resultId));
      open.forEach(resource => state.closingResults.add(resource.resultId));
      await Promise.allSettled(open.map(resource => releaseConsoleResultResource(resource, postgresClient.request)));
      open.forEach(resource => state.closingResults.delete(resource.resultId));
      state.results.clear();
      renderResults();
    }

    async function exportResult(resultId) {
      const resource = state.results.get(resultId);
      if (!resource) return;
      await drainConsoleResultResource(resource, postgresClient.request);
      renderResults();
      shared.downloadContent(
        JSON.stringify({ columns: resource.columns, rows: resource.rows, truncationEvents: resource.truncationEvents }, null, 2),
        `console-${resource.executionId}-statement-${resource.statementIndex + 1}.json`,
        "application/json",
      );
    }

    async function recoverUnknown(value, executionId) {
      element("status").textContent = "Outcome unknown. Checking the execution status; do not replay.";
      const query = new URLSearchParams({ consoleId: state.consoleId, database: value.database, namespace: value.namespace });
      try {
        const receipt = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(value.profileId)}/console/executions/${encodeURIComponent(executionId)}?${query}`);
        element("status").textContent = `Outcome ${receipt.outcome}. Do not replay unless the receipt proves it did not start.`;
        element("results").textContent = JSON.stringify(receipt, null, 2);
        if (["committed", "partial_committed"].includes(receipt.outcome)) await onCommittedWrite(value, receipt);
      } catch {
        element("status").textContent = "Outcome unknown. Status is unavailable; inspect PostgreSQL before any retry.";
      }
    }

    async function run() {
      const value = target();
      const mode = element("mode").value;
      if (state.running) return;
      const sql = element("sql").value.trim();
      if (!value) return void (element("status").textContent = "Select an exact profile, database, and namespace before running SQL.");
      if (!state.settings) return void (element("status").textContent = "Console settings are not loaded.");
      if (!Object.hasOwn(modes, mode)) return void (element("status").textContent = "Select a supported transaction mode.");
      if (!sql) return void (element("status").textContent = "Enter at least one SQL statement.");
      if (sql.includes("\0")) return void (element("status").textContent = "SQL must not contain null bytes.");
      if (sql.length > 100000) return void (element("status").textContent = "SQL exceeds the 100000-character Console limit.");
      if (state.targetKey && state.targetKey !== targetKey(value)) return guardTargetChange();
      if (mode !== "managed_read" && state.settings.writeIntent !== "enabled") {
        element("status").textContent = "Enable and save human write intent before starting a write-capable operation.";
        return;
      }
      const executionId = crypto.randomUUID();
      state.activeExecution = null;
      setRunning(true, "preparing");
      element("status").textContent = "Preparing the Console target and releasing prior results...";
      try {
        await closeAllResults();
        let result;
        if (mode === "explicit") {
          if (!state.transactionId) {
            state.transactionId = crypto.randomUUID();
            const created = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(value.profileId)}/console/transactions`, {
              method: "POST", body: JSON.stringify({
                transactionId: state.transactionId, consoleId: state.consoleId, database: value.database,
                namespace: value.namespace, settingsRevision: state.settings.revision, profileFingerprint: value.profileFingerprint,
              }),
            });
            state.transactionState = created.state;
          }
          const pending = postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(value.profileId)}/console/transactions/${encodeURIComponent(state.transactionId)}/executions`, {
            method: "POST", body: JSON.stringify({ executionId, sql: element("sql").value }),
          });
          state.activeExecution = { executionId, profileId: value.profileId, mode, cancelRequested: false };
          setRunning(true, "dispatched");
          element("status").textContent = `Running ${modes[mode][0].toLowerCase()} execution...`;
          result = await pending;
          state.transactionState = result.transactionState;
        } else {
          const pending = postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(value.profileId)}/console/executions`, {
            method: "POST", body: JSON.stringify(requestBody(value, mode, executionId)),
          });
          state.activeExecution = { executionId, profileId: value.profileId, mode, cancelRequested: false };
          setRunning(true, "dispatched");
          element("status").textContent = `Running ${modes[mode][0].toLowerCase()} execution...`;
          result = await pending;
          if (["managed", "autocommit"].includes(mode) && result.committed) await onCommittedWrite(value, result);
        }
        element("status").textContent = mode === "explicit" ? `Transaction ${state.transactionState}` : result.outcome === "cancelled" ? "Execution cancelled by PostgreSQL." : `Outcome ${result.outcome}`;
        rememberResults(result, value);
      } catch (error) {
        const writeCapable = mode !== "managed_read";
        if (error.code === "execution_cancelled") element("status").textContent = "Execution cancelled by PostgreSQL.";
        else if (writeCapable && (!error.code || error.code === "execution_outcome_unknown" || error.code === "execution_receipt_unavailable")) await recoverUnknown(value, executionId);
        else element("status").textContent = error.message;
      } finally {
        state.activeExecution = null;
        setRunning(false);
        renderMode();
      }
    }

    async function stop() {
      const execution = state.activeExecution;
      if (!execution || execution.cancelRequested) return;
      execution.cancelRequested = true;
      element("stop").disabled = true;
      element("status").textContent = "Cancellation requested. Waiting for PostgreSQL to confirm the terminal outcome...";
      try {
        await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(execution.profileId)}/console/executions/${encodeURIComponent(execution.executionId)}`, { method: "DELETE" });
      } catch (error) {
        execution.cancelRequested = false;
        element("stop").disabled = false;
        element("status").textContent = `Cancellation request failed: ${error.message}. The execution may still be running.`;
      }
    }

    async function finish(action) {
      const value = target();
      if (!value || !state.transactionId || state.running) return;
      const executionId = crypto.randomUUID();
      setRunning(true);
      try {
        const result = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(value.profileId)}/console/transactions/${encodeURIComponent(state.transactionId)}/${action}`, {
          method: "POST", body: JSON.stringify({ executionId }),
        });
        state.transactionId = null;
        state.transactionState = "closed";
        state.results.clear();
        renderResults();
        element("status").textContent = `Transaction ${result.outcome}`;
        if (result.outcome === "committed") await onCommittedWrite(value, result);
      } catch (error) {
        if (action === "commit" && (!error.code || error.code === "execution_outcome_unknown" || error.code === "execution_receipt_unavailable")) {
          await recoverUnknown(value, executionId);
          state.transactionId = null;
          state.transactionState = "unknown";
          state.results.clear();
          renderResults();
        } else element("status").textContent = error.message;
      } finally {
        setRunning(false);
        renderMode();
      }
    }

    button.addEventListener("click", async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      renderTarget();
      dialog.showModal();
      button.setAttribute("aria-expanded", "true");
      button.classList.add("active");
      try { await loadSettings(); } catch (error) { element("status").textContent = error.message; }
    }, true);
    element("close").addEventListener("click", async () => { if (!activeTransaction() && !state.running) { await closeAllResults(); dialog.close(); } else guardTargetChange(); });
    dialog.addEventListener("close", () => {
      button.setAttribute("aria-expanded", "false");
      button.classList.remove("active");
      if (!activeTransaction() && state.results.size) void closeAllResults();
    });
    dialog.addEventListener("cancel", async event => {
      if (activeTransaction() || state.running) return guardTargetChange(event);
      event.preventDefault();
      await closeAllResults();
      dialog.close();
    });
    element("mode").addEventListener("change", () => {
      if (activeTransaction() && element("mode").value !== "explicit") {
        element("mode").value = "explicit";
        return guardTargetChange();
      }
      renderMode();
    });
    element("save-settings").addEventListener("click", saveSettings);
    element("run").addEventListener("click", run);
    element("stop").addEventListener("click", stop);
    element("commit").addEventListener("click", () => finish("commit"));
    element("rollback").addEventListener("click", () => finish("rollback"));
    element("results").addEventListener("click", async event => {
      const load = event.target.closest("[data-console-load-more]");
      const close = event.target.closest("[data-console-close-result]");
      const exportButton = event.target.closest("[data-console-export]");
      try {
        if (load) await loadMore(load.dataset.consoleLoadMore);
        else if (close) await closeResult(close.dataset.consoleCloseResult);
        else if (exportButton) await exportResult(exportButton.dataset.consoleExport);
      } catch (error) { element("status").textContent = error.message; }
    });
    targetControls.filter(Boolean).forEach(control => {
      control.addEventListener("change", guardTargetChange, true);
      control.addEventListener("change", () => { if (!activeTransaction()) void closeAllResults(); });
      control.addEventListener("click", event => { if (activeTransaction()) guardTargetChange(event); }, true);
    });
    global.addEventListener("beforeunload", event => {
      for (const resource of state.results.values()) {
        if (resource.hasMore && !state.closingResults.has(resource.resultId)) void releaseConsoleResultResource(resource, postgresClient.request, { keepalive: true }).catch(() => {});
      }
      if (activeTransaction()) { event.preventDefault(); event.returnValue = ""; }
    });
    return { hasActiveTransaction: activeTransaction, guardTargetChange, refreshTarget: renderTarget, dialog };
  }

  global.SchemiiShared = Object.freeze({
    ...shared, consoleResultResourceUrl, pageConsoleResultResource, drainConsoleResultResource,
    releaseConsoleResultResource, createPostgresConsole,
  });
})(window);
