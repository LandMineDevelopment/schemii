import { element } from "#common/dom.js";
import { requestJson } from "#common/http.js";
import { confirmAction } from "#common/confirmation.js";
import { formatElapsed } from "#common/elapsed-time.js";

const isOpen = session => ["intrans", "inerror"].includes(session.transactionStatus) || (session.status === "running" && Boolean(session.transactionStartedAt));

/** Transaction controls act on a reviewed session revision, never on row guesses. */
export function openConsoleTransactions({ workspaceId, currentSessionId, onChange = () => {} }) {
  const base = `/api/v1/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/sessions`;
  const previousFocus = document.activeElement;
  const dialog = element("dialog", { className: "ui-dialog console-transactions-dialog", attrs: { "aria-label": "Open transactions" } });
  const list = element("div", { className: "console-transactions-list" });
  const status = element("p", { attrs: { role: "status" } });
  const error = element("p", { attrs: { role: "alert" } });
  const close = element("button", { type: "button", className: "ui-button", text: "Close" });
  const refresh = element("button", { type: "button", className: "ui-button", text: "Refresh" });
  const commitAll = element("button", { type: "button", className: "ui-button", text: "Commit all" });
  const rollbackAll = element("button", { type: "button", className: "ui-button", text: "Roll back all" });
  dialog.append(element("header", { className: "ui-dialog__head" }, [element("h2", { text: "Open transactions" })]),
    element("div", { className: "console-transactions-body" }, [
      element("p", { text: "Your console transactions in this workspace, including other tabs. Each transaction commits or rolls back as a whole." }),
      status, list, error,
    ]), element("footer", { className: "ui-dialog__actions" }, [commitAll, rollbackAll, refresh, close]));
  let sessions = [], busy = false, disposed = false, timer, requestGeneration = 0, renderKey = null;
  let resolveClosed;
  const closed = new Promise(resolve => { resolveClosed = resolve; });
  function controls() {
    commitAll.disabled = busy || !sessions.length || sessions.some(item => item.status === "running" || item.transactionStatus === "inerror");
    rollbackAll.disabled = busy || !sessions.length || sessions.some(item => item.status === "running");
    refresh.disabled = busy; close.disabled = busy;
  }
  async function runAction(action, selected) {
    if (busy) return;
    busy = true; clearTimeout(timer); controls();
    try {
      await confirmAction({ title: selected.length > 1 ? `${action === "COMMIT" ? "Commit" : "Roll back"} all transactions?` : `${action === "COMMIT" ? "Commit" : "Roll back"} transaction?`,
        message: `${action === "COMMIT" ? "Commit" : "Discard"} the uncommitted changes in ${selected.length} transaction${selected.length === 1 ? "" : "s"}?`,
        details: selected.length > 1 ? "Each transaction is handled separately. If one fails or changes while you review it, the others may already be finished; review the outcome below." : "This applies to the entire transaction, including statements run from another tab. A changed transaction requires a fresh review.",
        confirmLabel: action === "COMMIT" ? "Commit" : "Roll back", busyLabel: "Working…",
        onConfirm: async () => {
          const failures = [];
          for (const session of selected) {
            try {
              const receipt = await requestJson(`${base}/${session.id}/executions`, { method: "POST", body: { sql: action, commitMode: "manual", expectedRevision: session.revision } });
              let execution = receipt;
              const deadline = Date.now() + 15000;
              while (["reserved", "running"].includes(execution.status)) {
                if (Date.now() > deadline) throw new Error("Still running. Refresh to check the outcome; do not submit it again.");
                await new Promise(resolve => setTimeout(resolve, 250));
                execution = await requestJson(`${base}/${session.id}/executions/${receipt.id}`);
              }
              if (execution.status !== "succeeded") throw new Error(execution.errorMessage || execution.status);
            } catch (failure) { failures.push(`Session ${session.backendPid ?? session.id}: ${failure.message}`); }
          }
          await onChange();
          error.textContent = failures.join("\n");
        } });
    } finally { busy = false; renderKey = null; await load(); }
  }
  function render() {
    status.textContent = sessions.length ? `${sessions.length} open transaction${sessions.length === 1 ? "" : "s"}` : "No open transactions.";
    const key = JSON.stringify(sessions.map(session => [session.id, session.revision, session.status, session.transactionStatus, session.expiresAt]));
    if (key !== renderKey) {
      renderKey = key; list.replaceChildren();
      for (const session of sessions) {
        const article = element("article", { dataset: { sessionId: session.id } });
        const title = element("strong", { text: `${session.id === currentSessionId ? "This console" : "Another console"} · Session ${session.backendPid ?? session.id}` });
        const state = element("p", { text: session.status === "running" ? "Statement running · wait or use Stop in its console" : session.transactionStatus === "inerror" ? "Aborted · roll back, or recover using a savepoint in its console" : "Changes uncommitted" });
        const age = element("span", { dataset: { startedAt: session.transactionStartedAt || "" } });
        const expiry = element("p", { dataset: { expiresAt: session.expiresAt || "" } });
        const sql = element("details", {}, [element("summary", { text: "SQL run in this transaction" })]);
        sql.append(element("p", { text: "This is statement history, not a row-by-row change preview. Savepoint and rollback commands can change which work remains pending." }));
        const statements = session.pendingStatements || [];
        for (const statement of statements) sql.append(element("pre", { text: typeof statement === "string" ? statement : statement.sql }));
        if (!statements.length) sql.append(element("p", { text: "No statement history available." }));
        if (session.pendingStatementsTruncated) sql.append(element("p", { text: "Earlier statement history was omitted to keep this view bounded." }));
        const commit = element("button", { type: "button", className: "ui-button", text: "Commit" });
        const rollback = element("button", { type: "button", className: "ui-button", text: "Roll back" });
        commit.disabled = session.status === "running" || session.transactionStatus === "inerror";
        rollback.disabled = session.status === "running";
        commit.onclick = () => void runAction("COMMIT", [session]);
        rollback.onclick = () => void runAction("ROLLBACK", [session]);
        article.append(title, state, age, expiry, sql, element("div", { className: "query-plan-actions" }, [commit, rollback]));
        list.append(article);
      }
    }
    for (const node of list.querySelectorAll("[data-started-at]")) {
      const started = Date.parse(node.dataset.startedAt);
      node.textContent = Number.isFinite(started) ? `Open for ${formatElapsed(Date.now() - started)}` : "";
    }
    for (const node of list.querySelectorAll("[data-expires-at]")) {
      const expires = Date.parse(node.dataset.expiresAt);
      node.textContent = Number.isFinite(expires)
        ? `Idle session closes in ${formatElapsed(Math.max(0, expires - Date.now()))}; uncommitted changes then roll back.` : "";
    }
    controls();
  }
  async function load() {
    clearTimeout(timer);
    if (disposed || busy) return;
    const generation = ++requestGeneration;
    try {
      const result = await requestJson(base);
      if (disposed || busy || generation !== requestGeneration) return;
      sessions = result.sessions.filter(isOpen); render();
    } catch (failure) { if (!disposed) error.textContent = `${failure.message} Use Refresh to reconnect.`; }
    finally { if (!disposed && !busy && generation === requestGeneration) timer = setTimeout(load, 1500); }
  }
  commitAll.onclick = () => void runAction("COMMIT", [...sessions]);
  rollbackAll.onclick = () => void runAction("ROLLBACK", [...sessions]);
  refresh.onclick = () => { error.textContent = ""; void load(); };
  close.onclick = () => dialog.close();
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", () => { disposed = true; clearTimeout(timer); dialog.remove(); if (previousFocus?.isConnected) previousFocus.focus(); resolveClosed(); }, { once: true });
  document.body.append(dialog); dialog.showModal(); void load();
  return closed;
}
