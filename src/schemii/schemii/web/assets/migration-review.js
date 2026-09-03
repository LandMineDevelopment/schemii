import { element, errorPanel, formatTimestamp, replace } from "./dom.js";

const RUNNING_EXECUTION_STATUSES = new Set(["reserved", "applying"]);
const RECOVERABLE_EXECUTION_STATUSES = new Set(["uncertain", "reconciliation_required"]);

export function migrationWorkspaceEligible(workspace, design) {
  return Boolean(
    workspace
      && design
      && workspace.mode === "design"
      && workspace.connectionId
      && workspace.database
      && workspace.namespace,
  );
}

export function migrationStepRisk(step) {
  if (step.destructive) return "destructive";
  if (step.dataMovement) return "data";
  if (step.requiresLock) return "lock";
  return "standard";
}

export function migrationExecutionNeedsPolling(execution) {
  return Boolean(execution && RUNNING_EXECUTION_STATUSES.has(execution.status));
}

export function migrationExecutionNeedsReconciliation(execution) {
  return Boolean(execution && RECOVERABLE_EXECUTION_STATUSES.has(execution.status));
}

export function migrationConflictsResolved(plan, resolutions) {
  return Boolean(plan?.conflicts?.length) && plan.conflicts.every(conflict => (
    conflict.allowedResolutions.includes(resolutions.get(conflict.id))
  ));
}

export function migrationCanApply({
  plan,
  execution,
  busy,
  allowDestructive,
  confirmExternalChanges,
}) {
  return Boolean(
    plan
      && plan.status === "reviewable"
      && plan.applyCapable
      && plan.steps.length
      && !execution
      && !busy
      && (!plan.destructive || allowDestructive)
      && (!plan.requiresExternalChangeAcknowledgement || confirmExternalChanges),
  );
}

function displayValue(value) {
  if (value === null || value === undefined) return "Not present";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function sentence(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/^./, first => first.toUpperCase());
}

function badge(text, tone = "") {
  return element("span", { className: `migration-badge${tone ? ` ${tone}` : ""}`, text });
}

function reviewSection(title, count, children, className = "") {
  const section = element("section", { className: `migration-section${className ? ` ${className}` : ""}` });
  const header = element("header");
  header.append(element("h3", { text: title }));
  if (count !== null) header.append(element("span", { text: count }));
  section.append(header, ...children);
  return section;
}

function renderMessages(title, messages, tone = "") {
  if (!messages?.length) return null;
  const items = messages.map(message => {
    const item = element("article", { className: "migration-message" });
    item.append(
      element("strong", { text: message.message }),
      element("code", { text: message.objectPath || "schema" }),
    );
    return item;
  });
  return reviewSection(title, String(messages.length), items, tone);
}

function renderSteps(plan) {
  if (!plan.steps.length) {
    const copy = plan.complete
      ? "The saved design already matches the current PostgreSQL target."
      : plan.conflicts.length
        ? "Resolve every conflict above before the server can derive executable PostgreSQL changes."
        : "Address the blocking differences above before the server can derive executable PostgreSQL changes.";
    return reviewSection("Planned changes", "0", [
      element("p", { className: "migration-section-copy", text: copy }),
    ]);
  }
  const steps = plan.steps.map(step => {
    const risk = migrationStepRisk(step);
    const details = element("details", { className: `migration-step ${risk}` });
    const summary = element("summary");
    const identity = element("span", { className: "migration-step-identity" });
    identity.append(
      element("b", { text: String(step.index).padStart(2, "0") }),
      element("span", {}, [
        element("strong", { text: sentence(step.operation) }),
        element("code", { text: step.objectPath }),
      ]),
    );
    const risks = element("span", { className: "migration-step-risks" });
    if (step.destructive) risks.append(badge("Data loss", "danger"));
    if (step.dataMovement) risks.append(badge("Data movement", "warning"));
    if (step.requiresLock) risks.append(badge("Lock", "warning"));
    if (!risks.childElementCount) risks.append(badge(step.objectKind));
    summary.append(identity, risks);
    details.append(summary, element("pre", {}, [element("code", { text: step.sql })]));
    return details;
  });
  return reviewSection("Ordered PostgreSQL changes", String(steps.length), steps);
}

function renderExternalChanges(plan) {
  if (!plan.externalChanges.length) return null;
  const changes = plan.externalChanges.map(change => {
    const item = element("article", { className: "migration-external-change" });
    item.append(
      element("span", {}, [badge(sentence(change.operation), "external"), element("code", { text: change.objectPath })]),
      element("p", { text: change.summary }),
    );
    return item;
  });
  return reviewSection("External changes preserved", String(changes.length), changes, "external");
}

function conflictValue(label, value) {
  const details = element("details", { className: "migration-conflict-value" });
  details.append(
    element("summary", { text: label }),
    element("pre", {}, [element("code", { text: displayValue(value) })]),
  );
  return details;
}

function renderConflicts(plan, state, updateActions) {
  if (!plan.conflicts.length) return null;
  const conflicts = plan.conflicts.map(conflict => {
    const item = element("article", { className: "migration-conflict" });
    item.append(
      element("header", {}, [
        element("span", {}, [badge(sentence(conflict.category), "danger"), badge(conflict.objectKind)]),
        element("code", { text: conflict.objectPath }),
      ]),
      element("p", { text: conflict.summary }),
      element("div", { className: "migration-conflict-values" }, [
        conflictValue("Baseline", conflict.baselineValue),
        conflictValue("Saved design", conflict.designValue),
        conflictValue("Live PostgreSQL", conflict.liveValue),
      ]),
    );
    const choices = element("fieldset", { className: "migration-conflict-choices" });
    choices.append(element("legend", { text: "Choose how to reconcile this conflict" }));
    const choiceLabels = {
      pull_live: ["Pull live", "Replace the conflicting design value with PostgreSQL."],
      keep_design: ["Keep design", "Accept the new PostgreSQL baseline and retain the saved design value."],
    };
    for (const resolution of conflict.allowedResolutions) {
      const input = element("input", {
        type: "radio",
        attrs: { name: `migration-conflict-${conflict.id}`, value: resolution },
      });
      input.checked = state.resolutions.get(conflict.id) === resolution;
      input.addEventListener("change", () => {
        state.resolutions.set(conflict.id, resolution);
        updateActions();
      });
      const [label, copy] = choiceLabels[resolution];
      choices.append(element("label", {}, [input, element("span", {}, [element("strong", { text: label }), element("small", { text: copy })])]));
    }
    item.append(choices);
    return item;
  });
  return reviewSection("Conflicts require a choice", String(conflicts.length), conflicts, "conflicts");
}

function renderExecution(execution) {
  const status = sentence(execution.status);
  const tone = execution.status === "succeeded"
    ? "success"
    : execution.status === "failed"
      ? "danger"
      : migrationExecutionNeedsReconciliation(execution)
        ? "warning"
        : "active";
  const section = reviewSection("Migration execution", null, [], `execution ${tone}`);
  const summary = element("div", { className: "migration-execution-summary" });
  summary.append(
    badge(status, tone),
    element("strong", { text: `${execution.completedStepCount} completed ${execution.completedStepCount === 1 ? "step" : "steps"}` }),
    element("small", { text: `Updated ${formatTimestamp(execution.updatedAt)}` }),
  );
  if (execution.errorCode) summary.append(element("code", { text: execution.errorCode }));
  if (execution.commitOutcome) summary.append(element("span", { text: `Transaction: ${sentence(execution.commitOutcome)}` }));
  section.append(summary);
  return section;
}

export function createMigrationReviewController({
  api,
  elements,
  getContext,
  reloadWorkspace,
  confirm,
  notify,
  notifyError,
}) {
  const state = {
    plan: null,
    execution: null,
    error: null,
    loading: false,
    submitting: false,
    allowDestructive: false,
    confirmExternalChanges: false,
    resolutions: new Map(),
    version: 0,
    controller: null,
    pollTimer: null,
    handledExecutionId: null,
  };

  const context = () => {
    const value = getContext();
    return migrationWorkspaceEligible(value?.workspace, value?.design) ? value : null;
  };

  function invalidate() {
    state.version += 1;
    state.controller?.abort();
    state.controller = null;
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function current(version, workspaceId) {
    return version === state.version && context()?.workspace.id === workspaceId;
  }

  function beginRequest() {
    state.controller?.abort();
    state.controller = new AbortController();
    return state.controller.signal;
  }

  function setStatus(child = null) {
    replace(elements.status, child);
  }

  function updateActions() {
    const plan = state.plan;
    const busy = state.loading || state.submitting || migrationExecutionNeedsPolling(state.execution);
    elements.refresh.disabled = busy || migrationExecutionNeedsReconciliation(state.execution);
    elements.resolve.hidden = !plan?.conflicts?.length;
    elements.resolve.disabled = busy || !migrationConflictsResolved(plan, state.resolutions);
    if (migrationExecutionNeedsReconciliation(state.execution)) {
      elements.apply.disabled = state.submitting;
      elements.apply.textContent = "Check transaction outcome";
      return;
    }
    elements.apply.disabled = !migrationCanApply({
      plan,
      execution: state.execution,
      busy,
      allowDestructive: state.allowDestructive,
      confirmExternalChanges: state.confirmExternalChanges,
    });
    elements.apply.textContent = migrationExecutionNeedsPolling(state.execution) ? "Applying…" : "Apply migration";
  }

  function renderOverview(plan) {
    const overview = element("section", { className: "migration-overview" });
    const title = plan.status === "blocked"
      ? "Resolve blockers before applying"
      : !plan.steps.length && plan.complete
        ? "PostgreSQL is up to date"
        : `${plan.steps.length} ${plan.steps.length === 1 ? "change" : "changes"} ready for review`;
    overview.append(
      element("div", {}, [
        element("span", { className: "migration-overview-status" }, [badge(sentence(plan.status), plan.status === "reviewable" ? "success" : "warning")]),
        element("h3", { text: title }),
        element("p", { text: `Compared design revision ${plan.designRevision} with baseline ${plan.baselineRevision} and the current PostgreSQL catalog.` }),
      ]),
      element("dl", {}, [
        element("div", {}, [element("dt", { text: "Drift" }), element("dd", { text: sentence(plan.driftStatus) })]),
        element("div", {}, [element("dt", { text: "Expires" }), element("dd", { text: formatTimestamp(plan.expiresAt) })]),
      ]),
    );
    return overview;
  }

  function renderConfirmations(plan) {
    const rows = [];
    if (plan.destructive) {
      const input = element("input", { type: "checkbox" });
      input.checked = state.allowDestructive;
      input.disabled = state.loading || state.submitting;
      input.addEventListener("change", () => {
        state.allowDestructive = input.checked;
        state.confirmExternalChanges = false;
        void refresh();
      });
      rows.push(element("label", { className: "migration-confirmation danger" }, [
        input,
        element("span", {}, [
          element("strong", { text: "Include destructive changes" }),
          element("small", { text: "Review operations that may permanently remove stored data or database objects." }),
        ]),
      ]));
    }
    if (plan.requiresExternalChangeAcknowledgement) {
      const input = element("input", { type: "checkbox" });
      input.checked = state.confirmExternalChanges;
      input.addEventListener("change", () => {
        state.confirmExternalChanges = input.checked;
        updateActions();
      });
      rows.push(element("label", { className: "migration-confirmation" }, [
        input,
        element("span", {}, [
          element("strong", { text: "Acknowledge compatible external changes" }),
          element("small", { text: "These PostgreSQL changes do not collide with the design and will be preserved." }),
        ]),
      ]));
    }
    return rows.length ? reviewSection("Required confirmations", null, rows, "confirmations") : null;
  }

  function render() {
    replace(elements.body);
    setStatus(state.error ? errorPanel(state.error, { retryLabel: "Try again", onRetry: retry }) : null);
    if (state.loading && !state.plan && !state.execution) {
      elements.body.append(element("div", { className: "migration-loading" }, [
        element("span", { text: "…" }),
        element("strong", { text: "Comparing design, baseline, and live PostgreSQL" }),
        element("p", { text: "The server is deriving an immutable migration review." }),
      ]));
      updateActions();
      return;
    }
    if (state.execution) elements.body.append(renderExecution(state.execution));
    if (state.plan) {
      const plan = state.plan;
      elements.body.append(renderOverview(plan));
      const blockers = renderMessages("Blocking differences", plan.blockingDifferences, "blocking");
      const warnings = renderMessages("Review notes", plan.warnings);
      const external = renderExternalChanges(plan);
      const conflicts = renderConflicts(plan, state, updateActions);
      const confirmations = renderConfirmations(plan);
      for (const section of [blockers, external, conflicts, renderSteps(plan), warnings, confirmations]) {
        if (section) elements.body.append(section);
      }
    }
    updateActions();
  }

  async function finishExecution(version, workspaceId) {
    if (!current(version, workspaceId) || state.handledExecutionId === state.execution?.id) return;
    if (state.execution?.status !== "succeeded") return;
    state.handledExecutionId = state.execution.id;
    try {
      await reloadWorkspace();
      if (!current(version, workspaceId)) return;
      notify("Migration committed and the workspace baseline was refreshed.");
      await refresh({ preserveExecution: true });
      return;
    } catch (error) {
      if (!current(version, workspaceId)) return;
      state.error = error;
      notifyError(error);
    }
    render();
  }

  function schedulePoll(version, workspaceId, executionId) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async () => {
      if (!current(version, workspaceId)) return;
      try {
        const execution = await api.getMigrationExecution(executionId, { signal: beginRequest() });
        if (!current(version, workspaceId)) return;
        state.execution = execution;
        state.error = null;
        render();
        if (migrationExecutionNeedsPolling(execution)) schedulePoll(version, workspaceId, executionId);
        else await finishExecution(version, workspaceId);
      } catch (error) {
        if (!current(version, workspaceId) || error?.code === "request_cancelled") return;
        state.error = error;
        render();
      }
    }, 700);
  }

  function retry() {
    if (migrationExecutionNeedsPolling(state.execution)) {
      state.error = null;
      render();
      schedulePoll(state.version, context().workspace.id, state.execution.id);
      return;
    }
    if (!state.plan && !state.execution) {
      void open();
      return;
    }
    void refresh();
  }

  async function refresh({ preserveExecution = false } = {}) {
    const value = context();
    if (!value) return;
    const execution = preserveExecution ? state.execution : null;
    invalidate();
    const version = state.version;
    const workspaceId = value.workspace.id;
    state.loading = true;
    state.error = null;
    state.execution = execution;
    state.resolutions.clear();
    render();
    try {
      const plan = await api.createMigrationPlan(workspaceId, {
        expectedWorkspaceRevision: value.workspace.revision,
        expectedDesignRevision: value.design.revision,
        expectedCatalogFingerprint: null,
        allowDestructive: state.allowDestructive,
      }, { signal: beginRequest() });
      if (!current(version, workspaceId)) return;
      state.plan = plan;
      state.confirmExternalChanges = false;
    } catch (error) {
      if (!current(version, workspaceId) || error?.code === "request_cancelled") return;
      state.error = error;
    } finally {
      if (current(version, workspaceId)) {
        state.loading = false;
        render();
      }
    }
  }

  async function open() {
    const value = context();
    if (!value) return false;
    invalidate();
    state.plan = null;
    state.execution = null;
    state.error = null;
    state.loading = true;
    state.submitting = false;
    state.allowDestructive = false;
    state.confirmExternalChanges = false;
    state.resolutions.clear();
    state.handledExecutionId = null;
    if (!elements.dialog.open) elements.dialog.showModal();
    render();
    const version = state.version;
    const workspaceId = value.workspace.id;
    try {
      const executions = await api.listMigrationExecutions(workspaceId, {
        limit: 25,
        signal: beginRequest(),
      });
      if (!current(version, workspaceId)) return true;
      const active = executions.find(execution => (
        migrationExecutionNeedsPolling(execution)
          || migrationExecutionNeedsReconciliation(execution)
      ));
      if (active) {
        state.execution = active;
        state.loading = false;
        render();
        if (migrationExecutionNeedsPolling(active)) schedulePoll(version, workspaceId, active.id);
        return true;
      }
    } catch (error) {
      if (!current(version, workspaceId) || error?.code === "request_cancelled") return true;
      state.error = error;
      state.loading = false;
      render();
      return true;
    }
    state.loading = false;
    await refresh();
    return true;
  }

  async function resolve() {
    const value = context();
    const plan = state.plan;
    if (!value || !migrationConflictsResolved(plan, state.resolutions) || state.submitting) return;
    invalidate();
    const version = state.version;
    const workspaceId = value.workspace.id;
    state.submitting = true;
    state.error = null;
    setStatus(element("span", { text: "Reconciling every selected conflict on the server…" }));
    updateActions();
    try {
      await api.resolveMigrationDrift(plan.id, {
        expectedDesignRevision: plan.designRevision,
        reviewDigest: plan.reviewDigest,
        resolutions: plan.conflicts.map(conflict => ({
          conflictId: conflict.id,
          resolution: state.resolutions.get(conflict.id),
        })),
      }, { signal: beginRequest() });
      if (!current(version, workspaceId)) return;
      await reloadWorkspace();
      if (!current(version, workspaceId)) return;
      state.allowDestructive = false;
      state.confirmExternalChanges = false;
      state.plan = null;
      state.submitting = false;
      notify("Conflicts reconciled. Review the refreshed migration before applying it.");
      await refresh();
    } catch (error) {
      if (!current(version, workspaceId) || error?.code === "request_cancelled") return;
      state.error = error;
      state.submitting = false;
      render();
    }
  }

  async function execute() {
    const value = context();
    const plan = state.plan;
    if (!value || !migrationCanApply({
      plan,
      execution: state.execution,
      busy: state.loading || state.submitting,
      allowDestructive: state.allowDestructive,
      confirmExternalChanges: state.confirmExternalChanges,
    })) return;
    invalidate();
    const version = state.version;
    const workspaceId = value.workspace.id;
    state.submitting = true;
    state.error = null;
    setStatus(element("span", { text: "Queuing the reviewed migration…" }));
    updateActions();
    try {
      const execution = await api.createMigrationExecution(plan.id, {
        reviewDigest: plan.reviewDigest,
        confirmDestructive: plan.destructive && state.allowDestructive,
        confirmExternalChanges: plan.requiresExternalChangeAcknowledgement && state.confirmExternalChanges,
      }, { signal: beginRequest() });
      if (!current(version, workspaceId)) return;
      state.execution = execution;
      state.submitting = false;
      render();
      if (migrationExecutionNeedsPolling(execution)) schedulePoll(version, workspaceId, execution.id);
      else await finishExecution(version, workspaceId);
    } catch (error) {
      if (!current(version, workspaceId) || error?.code === "request_cancelled") return;
      state.error = error;
      state.submitting = false;
      render();
    }
  }

  function requestApply() {
    const plan = state.plan;
    if (!plan) return;
    const risks = [
      plan.destructive ? "destructive changes" : null,
      plan.requiresExternalChangeAcknowledgement ? "preserved external changes" : null,
    ].filter(Boolean);
    confirm({
      title: "Apply migration",
      message: `Apply ${plan.steps.length} reviewed ${plan.steps.length === 1 ? "change" : "changes"} to PostgreSQL${risks.length ? `, including ${risks.join(" and ")}` : ""}? The server will run the exact displayed SQL in one managed transaction.`,
      label: "Apply migration",
      callback: execute,
    });
  }

  async function reconcile() {
    const value = context();
    const execution = state.execution;
    if (!value || !migrationExecutionNeedsReconciliation(execution) || state.submitting) return;
    invalidate();
    const version = state.version;
    const workspaceId = value.workspace.id;
    state.submitting = true;
    state.error = null;
    render();
    try {
      state.execution = await api.reconcileMigrationExecution(execution.id, {
        expectedExecutionRevision: execution.revision,
      }, { signal: beginRequest() });
      if (!current(version, workspaceId)) return;
      state.submitting = false;
      render();
      if (migrationExecutionNeedsPolling(state.execution)) schedulePoll(version, workspaceId, state.execution.id);
      else await finishExecution(version, workspaceId);
    } catch (error) {
      if (!current(version, workspaceId) || error?.code === "request_cancelled") return;
      state.error = error;
      state.submitting = false;
      render();
    }
  }

  elements.refresh.addEventListener("click", () => refresh());
  elements.resolve.addEventListener("click", resolve);
  elements.apply.addEventListener("click", () => {
    if (migrationExecutionNeedsReconciliation(state.execution)) void reconcile();
    else requestApply();
  });

  return {
    open,
    close: invalidate,
    refresh,
    render,
    available: () => Boolean(context()),
  };
}
