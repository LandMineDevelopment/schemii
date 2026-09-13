import { element } from "#common/dom.js";
import { requestJson } from "#common/http.js";
import { confirmAction } from "#common/confirmation.js";
import { formatElapsed } from "#common/elapsed-time.js";

/** Explicit batches, never guessed transaction boundaries in a pasted script. */
export function openBulkJobs({ workspace, consoleId, initialSql = "" }) {
  const base = `/api/v1/schemii/workspaces/${encodeURIComponent(workspace.id)}/console/bulk-jobs`;
  const previousFocus = document.activeElement;
  const dialog = element("dialog", { className: "ui-dialog bulk-jobs-dialog", attrs: { "aria-label": "Bulk jobs" } });
  const error = element("p", { attrs: { role: "alert" } });
  const close = element("button", { type: "button", text: "Close", className: "ui-button" });
  const refresh = element("button", { type: "button", text: "Refresh jobs", className: "ui-button" });
  const name = element("input", { attrs: { "aria-label": "Job name", maxlength: "120", placeholder: "Load reporting table" } });
  const batches = element("div", { className: "bulk-job-batches" });
  const add = element("button", { type: "button", text: "Add batch", className: "ui-button" });
  const start = element("button", { type: "button", text: "Review & start", className: "ui-button" });
  const choices = element("select", { attrs: { "aria-label": "Saved bulk jobs" } });
  const progress = element("p", { attrs: { role: "status" } });
  const detail = element("div");
  const actions = element("div", { className: "query-plan-actions" });
  const editor = element("details", { attrs: { open: "" } }, [element("summary", { text: "New bulk job" }),
    element("p", { text: "Each batch commits separately. Earlier commits remain if a later batch fails or you stop the job. Use bounded INSERT, UPDATE or DELETE batches; do not include BEGIN, COMMIT or ROLLBACK." }),
    element("label", { text: "Job name" }, [name]), batches,
    element("div", { className: "query-plan-actions" }, [add, start])]);
  const body = element("div", { className: "bulk-jobs-body" }, [
    element("p", { text: `${workspace.database} · ${workspace.namespace}. Jobs continue when this dialog closes. Reopen it to check progress or resume.` }),
    editor, element("label", { text: "Saved jobs" }, [choices]), progress, detail, actions, error]);
  dialog.append(element("header", { className: "ui-dialog__head" }, [element("h2", { text: "Bulk jobs" })]), body,
    element("footer", { className: "ui-dialog__actions" }, [refresh, close]));
  let jobs = [], selected = null, polling = null, pending = false, disposed = false;
  let choiceKey = null;
  let selectedDocument = null, loadGeneration = 0;
  let renderedRevision = null, elapsedBase = 0, elapsedAt = performance.now(), active = false;
  const ticking = setInterval(() => {
    const job = jobs.find(item => item.id === selected);
    if (job) progress.textContent = `${job.status === "reconciliation_required" ? "Needs verification" : job.status} · ${job.completedBatches} / ${job.totalBatches} batches committed · Active time: ${formatElapsed(elapsedBase + (active ? performance.now() - elapsedAt : 0))}`;
  }, 100);
  function addBatch(sql = "") {
    const textarea = element("textarea", { attrs: { "aria-label": "Batch SQL", rows: "5", spellcheck: "false" } });
    textarea.value = sql;
    const remove = element("button", { type: "button", text: "Remove batch", className: "ui-button" });
    const field = element("fieldset", {}, [element("legend", { text: `Batch ${batches.children.length + 1}` }), textarea, remove]);
    remove.onclick = () => { field.remove(); [...batches.children].forEach((node, index) => { node.querySelector("legend").textContent = `Batch ${index + 1}`; }); };
    batches.append(field);
  }
  function render() {
    const job = jobs.find(item => item.id === selected);
    const nextChoiceKey = JSON.stringify(jobs.map(item => [item.id, item.name]));
    if (choiceKey !== nextChoiceKey) {
      choices.replaceChildren(...jobs.map(item => element("option", { text: item.name, attrs: { value: item.id } })));
      choiceKey = nextChoiceKey;
    }
    choices.value = selected || "";
    choices.disabled = !jobs.length;
    if (!job) { progress.textContent = "No bulk jobs yet."; detail.replaceChildren(); actions.replaceChildren(); return; }
    elapsedBase = job.elapsedMs || 0; elapsedAt = performance.now(); active = ["running", "cancelling"].includes(job.status);
    if (renderedRevision === `${job.id}:${job.revision}`) return;
    renderedRevision = `${job.id}:${job.revision}`;
    detail.replaceChildren(); actions.replaceChildren();
    if (job.errorMessage) detail.append(element("p", { text: job.errorMessage }));
    job.batches.forEach((batch, index) => {
      const row = element("details", {}, [element("summary", { text: `Batch ${index + 1} · ${batch.status}` }), element("pre", { text: batch.sql })]);
      if (batch.errorMessage) row.append(element("p", { text: batch.errorMessage }));
      detail.append(row);
    });
    function action(label, path, extra = {}, confirmation = null) {
      const button = element("button", { type: "button", text: label, className: "ui-button" });
      button.onclick = async () => {
        if (pending) return;
        pending = true; button.disabled = true;
        try {
          let resumeSettings = {};
          if (path === "resume") {
            const [settings, currentWorkspace] = await Promise.all([
              requestJson("/api/v1/schemii/console/settings"), requestJson(`/api/v1/schemii/workspaces/${workspace.id}`),
            ]);
            resumeSettings = { expectedSettingsRevision: settings.revision, expectedWorkspaceRevision: currentWorkspace.revision };
          }
          const perform = () => requestJson(`${base}/${job.id}/${path}`, { method: "POST", body: { expectedRevision: job.revision, ...extra, ...resumeSettings } });
          if (path === "resume") await confirmAction({ title: "Resume remaining batches?", message: `${job.completedBatches} committed batches will be skipped. Resume the remaining batches using your current console settings?`, details: "The server verifies that this is still the original database target. Each remaining batch commits independently.", confirmLabel: "Resume remaining batches", busyLabel: "Resuming…", onConfirm: perform });
          else if (confirmation) await confirmAction({ title: label, message: confirmation, details: "Verify the target database before confirming an uncertain outcome. An incorrect answer can duplicate writes or skip needed work.", confirmLabel: label, busyLabel: "Saving…", onConfirm: perform });
          else await perform();
        } catch (failure) { error.textContent = failure.message; }
        finally { pending = false; renderedRevision = null; await load(); }
      };
      actions.append(button);
    }
    if (["queued", "running", "cancelling"].includes(job.status)) action("Stop job", "cancel");
    if (["paused", "cancelled", "failed", "interrupted"].includes(job.status)) action("Resume remaining batches", "resume");
    if (job.status === "uncertain" || job.batches.some(batch => batch.status === "uncertain")) {
      action("I verified this batch committed", "reconcile", { outcome: "committed" }, "Record this batch as committed, so resuming skips it?");
      action("I verified this batch rolled back", "reconcile", { outcome: "rolled_back" }, "Record this batch as rolled back, so resuming runs it again?");
    }
    if (["completed", "paused", "failed"].includes(job.status)) {
      const remove = element("button", { type: "button", text: "Delete job record", className: "ui-button" });
      remove.onclick = async () => {
        if (pending) return;
        pending = true;
        try {
          await confirmAction({ title: "Delete job record?", message: `Delete “${job.name}” and its saved checkpoints?`, details: "Committed database changes remain. You will no longer be able to resume this job.", confirmLabel: "Delete job record",
            onConfirm: () => requestJson(`${base}/${job.id}?expectedRevision=${job.revision}`, { method: "DELETE" }) });
        } finally { pending = false; renderedRevision = null; await load(); }
      };
      actions.append(remove);
    }
  }
  async function load() {
    clearTimeout(polling);
    if (disposed) return;
    const ticket = ++loadGeneration;
    try {
      const response = await requestJson(base);
      if (disposed || ticket !== loadGeneration) return;
      jobs = response.jobs;
      if (!jobs.some(item => item.id === selected)) selected = jobs[0]?.id || null;
      const summary = jobs.find(item => item.id === selected);
      if (summary) {
        if (selectedDocument?.id !== selected || selectedDocument.revision !== summary.revision) {
          const document = await requestJson(`${base}/${selected}`);
          if (disposed || ticket !== loadGeneration) return;
          selectedDocument = document;
        }
        jobs = jobs.map(item => item.id !== selected ? item : selectedDocument.revision > item.revision
          ? selectedDocument : { ...item, batches: selectedDocument.batches });
      }
      render();
    } catch (failure) { if (!disposed) error.textContent = `${failure.message} Use Refresh jobs to reconnect.`; }
    finally { if (!disposed && ticket === loadGeneration) polling = setTimeout(load, 1500); }
  }
  choices.onchange = () => { selected = choices.value; renderedRevision = null; void load(); };
  add.onclick = () => addBatch();
  refresh.onclick = () => { error.textContent = ""; void load(); };
  start.onclick = async () => {
    if (pending) return;
    const sqlBatches = [...batches.querySelectorAll("textarea")].map(node => ({ sql: node.value.trim() }));
    if (!name.value.trim() || !sqlBatches.length || sqlBatches.some(batch => !batch.sql)) { error.textContent = "Enter a job name and SQL for every batch."; return; }
    pending = true; start.disabled = true; error.textContent = "";
    try {
      await confirmAction({ title: "Start bulk job?", message: `Run ${sqlBatches.length} batches against ${workspace.database}.${workspace.namespace}?`,
        details: "Each batch commits independently. Stopping the job does not undo earlier committed batches.", confirmLabel: "Start bulk job", busyLabel: "Starting…",
        onConfirm: async () => {
          const settings = await requestJson("/api/v1/schemii/console/settings");
          const job = await requestJson(base, { method: "POST", body: { consoleId, expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision, name: name.value.trim(), batches: sqlBatches } });
          selected = job.id; editor.open = false;
        } });
    } finally { pending = false; start.disabled = false; await load(); }
  };
  close.onclick = () => dialog.close();
  dialog.addEventListener("close", () => { disposed = true; clearTimeout(polling); clearInterval(ticking); dialog.remove(); if (previousFocus?.isConnected) previousFocus.focus(); }, { once: true });
  addBatch(initialSql); document.body.append(dialog); dialog.showModal(); void load();
}
