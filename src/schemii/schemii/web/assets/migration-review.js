import { element, errorPanel, formatTimestamp, replace } from "./dom.js";
import { createIconButton } from "./ui.js";

const RUNNING_EXECUTION_STATUSES = new Set(["reserved", "applying"]);
const RECOVERABLE_EXECUTION_STATUSES = new Set(["uncertain", "reconciliation_required"]);

export function migrationWorkspaceEligible(workspace, design) {
  return Boolean(
    workspace
      && design
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
  unreviewedConversions = false,
}) {
  return Boolean(
    plan
      && plan.status === "reviewable"
      && plan.applyCapable
      && plan.steps.length
      && !migrationExecutionNeedsPolling(execution)
      && !migrationExecutionNeedsReconciliation(execution)
      && !busy
      && !unreviewedConversions
      && (!plan.destructive || allowDestructive)
      && (!plan.requiresExternalChangeAcknowledgement || confirmExternalChanges),
  );
}

export function clearAppliedMigrationReview(state) {
  state.plan = null;
  state.rebuildTableIds = null;
  state.columnTypeConversions?.clear();
  state.conversionDrafts?.clear();
  state.allowDestructive = false;
  state.confirmExternalChanges = false;
  state.resolutions.clear();
}

export function retainedConversionChoices(conversions, choices) {
  const available = new Set((conversions || []).map(item => item.columnId));
  return new Map([...choices].filter(([columnId]) => available.has(columnId)));
}

export function hasUnreviewedConversions(choices, drafts) {
  return [...drafts].some(([id, expression]) => {
    const choice = choices.get(id);
    return choice?.strategy !== "custom" || expression.trim() !== choice.expression;
  });
}

function renderColumnTypeConversions(plan, state, refresh, updateActions) {
  if (!plan.columnTypeConversions?.length) return null;
  const cards = plan.columnTypeConversions.map(conversion => {
    const busy = state.loading || state.submitting;
    const card = element("article", { className: "migration-conversion" });
    const help = element("div", { className: "migration-conversion-help", hidden: true, text: "Strict conversion checks that every existing value can be converted and converted back without changing its value. If any value fails, PostgreSQL rolls back the migration. A custom USING expression defines your intended transformation and may change values. Checks run again while the table is locked before applying. Large tables can take longer to scan and rewrite." });
    const info = createIconButton({ icon: "info", label: "About column type conversion", className: "compact" });
    info.setAttribute("aria-expanded", "false");
    info.addEventListener("click", () => {
      help.hidden = !help.hidden;
      info.setAttribute("aria-expanded", String(!help.hidden));
    });
    card.append(element("header", {}, [element("strong", { text: `${conversion.tableName}.${conversion.columnName}` }), info]),
      element("code", { text: `${conversion.sourceType} → ${conversion.targetType}` }),
      element("p", { text: conversion.reason }), help);
    const selectChoice = choice => {
      if (choice.strategy === "strict") state.conversionDrafts.delete(conversion.columnId);
      state.columnTypeConversions.set(conversion.columnId, { columnId: conversion.columnId, ...choice });
      state.confirmExternalChanges = false;
      void refresh();
    };
    const strict = element("button", { type: "button", className: "ui-button", text: conversion.strategy === "strict" ? "Strict conversion selected" : "Use strict conversion" });
    strict.disabled = busy;
    strict.setAttribute("aria-pressed", String(conversion.strategy === "strict"));
    strict.addEventListener("click", () => selectChoice({ strategy: "strict" }));
    card.append(element("strong", { text: "Recommended: preserve every value or stop" }),
      element("code", { className: "migration-conversion-expression", text: `USING ${conversion.defaultExpression}` }), strict);
    const custom = element("details", { className: "migration-conversion-custom" });
    custom.open = conversion.strategy === "custom" || state.conversionDrafts.has(conversion.columnId);
    const expression = element("textarea", { attrs: { rows: "3", maxlength: "8192", "aria-label": `Custom USING expression for ${conversion.tableName}.${conversion.columnName}`, spellcheck: "false" } });
    expression.value = state.conversionDrafts.get(conversion.columnId) ?? conversion.expression ?? conversion.defaultExpression;
    expression.disabled = busy;
    const useCustom = element("button", { type: "button", className: "ui-button", text: "Review custom conversion" });
    const draftNotice = element("p", { attrs: { role: "status" } });
    const updateDraftNotice = () => {
      const accepted = state.columnTypeConversions.get(conversion.columnId);
      draftNotice.textContent = state.conversionDrafts.has(conversion.columnId)
        && (accepted?.strategy !== "custom" || expression.value.trim() !== accepted.expression)
        ? "Expression changed. Review this conversion before applying the migration."
        : conversion.strategy === "custom" ? "Custom conversion selected for this review." : "";
    };
    updateDraftNotice();
    useCustom.disabled = busy || !expression.value.trim();
    expression.addEventListener("input", () => {
      state.conversionDrafts.set(conversion.columnId, expression.value);
      useCustom.disabled = busy || !expression.value.trim();
      updateDraftNotice();
      updateActions();
    });
    useCustom.addEventListener("click", () => selectChoice({ strategy: "custom", expression: expression.value.trim() }));
    custom.append(element("summary", { text: "Custom USING expression" }), expression, draftNotice,
      element("p", { text: "Use the column shown above. Enter one expression, without the USING keyword. Review any rounding, truncation, or replacement of values before applying." }), useCustom);
    card.append(custom);
    if (conversion.validationError) card.append(element("p", { className: "migration-conversion-error", attrs: { role: "alert" }, text: conversion.validationError }));
    if (!conversion.strategy) card.append(element("p", { text: "Choose a conversion explicitly to continue the migration. Your saved design is unaffected." }));
    return card;
  });
  return reviewSection("Column type conversions", String(cards.length), cards);
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

function reviewSection(title, count, children, className = "", action = null) {
  const section = element("section", { className: `migration-section${className ? ` ${className}` : ""}` });
  const header = element("header");
  header.append(element("h3", { text: title }));
  if (action) {
    const actions = element("div", { className: "migration-section-actions" });
    if (count !== null) actions.append(element("span", { text: count }));
    actions.append(action);
    header.append(actions);
  } else if (count !== null) {
    header.append(element("span", { text: count }));
  }
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
      ? plan.columnOrderRebuilds?.length
        ? "No physical reorder is selected. The saved app order remains available without changing PostgreSQL."
        : "The saved design already matches the current PostgreSQL target."
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

function renderColumnOrderRebuilds(plan, state, refresh) {
  if (!plan.columnOrderRebuilds?.length) return null;
  const choices = plan.columnOrderRebuilds.map(rebuild => {
    const input = element("input", { type: "checkbox" });
    input.checked = rebuild.selected;
    input.disabled = state.loading || state.submitting || !rebuild.eligible;
    input.addEventListener("change", () => {
      const selected = new Set(state.rebuildTableIds || []);
      if (input.checked) selected.add(rebuild.tableId);
      else selected.delete(rebuild.tableId);
      state.rebuildTableIds = selected;
      state.confirmExternalChanges = false;
      void refresh();
    });
    const item = element("label", {
      className: `migration-rebuild-choice${rebuild.containsData ? " destructive" : " empty"}${rebuild.eligible ? "" : " blocked"}`,
    });
    const comparison = element("div", { className: "migration-rebuild-orders" }, [
      element("span", {}, [
        element("small", { text: "PostgreSQL now" }),
        element("code", { text: rebuild.currentOrder.join("  →  ") }),
      ]),
      element("span", {}, [
        element("small", { text: "Saved app order" }),
        element("code", { text: rebuild.desiredOrder.join("  →  ") }),
      ]),
    ]);
    const copy = rebuild.eligible
      ? rebuild.containsData
        ? "Optional. PostgreSQL locks the table, stages and restores its rows, then automatically restores modeled constraints, indexes, foreign keys, triggers, and identities. Leaving this off does not affect the saved app order."
        : "Optional and selected by default because the live table is empty. Schemii already saved the app order; deselect this to keep PostgreSQL's current physical order. If selected, modeled constraints, indexes, foreign keys, triggers, and identities are restored automatically."
      : rebuild.blockingReasons.join(" ");
    item.append(
      input,
      element("div", { className: "migration-rebuild-content" }, [
        element("header", {}, [
          element("strong", { text: rebuild.tableName }),
          badge(rebuild.containsData ? "Contains data" : "Empty table", rebuild.containsData ? "danger" : "success"),
          !rebuild.eligible ? badge("Unavailable", "warning") : null,
        ].filter(Boolean)),
        comparison,
        element("p", { text: copy }),
      ]),
    );
    return { containsData: rebuild.containsData, item };
  });
  const groups = [
    {
      kind: "populated",
      title: "Populated tables",
      copy: "Physical reordering copies and restores rows inside PostgreSQL. Review the operational cost before selecting it.",
      label: "How populated physical column reordering preserves data",
      tooltip: "How populated tables are reordered",
      containsData: true,
    },
    {
      kind: "empty",
      title: "Empty tables",
      copy: "No row copy is required. The visual order is already saved in Schemii whether or not PostgreSQL is reordered.",
      label: "Why empty-table physical column reordering is optional",
      tooltip: "Why empty-table reordering is optional",
      containsData: false,
    },
  ];
  const groupNodes = groups.flatMap(group => {
    const groupChoices = choices
      .filter(choice => choice.containsData === group.containsData)
      .map(choice => choice.item);
    if (!groupChoices.length) return [];
    const information = createIconButton({
      icon: "info",
      label: group.label,
      tooltip: group.tooltip,
      className: "compact migration-section-help",
    });
    information.addEventListener("click", () => {
      state.openRebuildHelp(information, group.kind);
    });
    return [element("section", { className: `migration-rebuild-group ${group.kind}` }, [
      element("header", {}, [
        element("div", {}, [
          element("h4", { text: group.title }),
          element("p", { text: group.copy }),
        ]),
        information,
      ]),
      ...groupChoices,
    ])];
  });
  return reviewSection(
    "Physical column order",
    String(choices.length),
    [
      element("p", {
        className: "migration-section-copy",
        text: "Schemii always saves the app order shown below. These independent options also rebuild PostgreSQL's physical column order.",
      }),
      ...groupNodes,
    ],
    "column-order",
  );
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
  if (execution.errorMessage) summary.append(element("p", { text: execution.errorMessage }));
  if (execution.commitOutcome) summary.append(element("span", { text: `Transaction: ${sentence(execution.commitOutcome)}` }));
  if (execution.errorCode === "migration_result_mismatch") {
    summary.append(element("p", {
      text: "PostgreSQL was rolled back because its inspected result did not match the reviewed design. A fresh review is shown below.",
    }));
  }
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
    rebuildTableIds: null,
    columnTypeConversions: new Map(),
    conversionDrafts: new Map(),
    version: 0,
    controller: null,
    pollTimer: null,
    handledExecutionId: null,
    rebuildHelp: elements.rebuildHelp,
    rebuildHelpInvoker: null,
    openRebuildHelp: null,
  };

  function setReviewControlsInert(inert) {
    for (const child of state.rebuildHelp.parentElement.children) {
      if (child !== state.rebuildHelp) child.inert = inert;
    }
  }

  function openRebuildHelp(invoker, kind) {
    state.rebuildHelpInvoker = invoker;
    for (const card of state.rebuildHelp.querySelectorAll("[data-rebuild-help-kind]")) {
      card.hidden = card.dataset.rebuildHelpKind !== kind;
    }
    setReviewControlsInert(true);
    state.rebuildHelp.hidden = false;
    state.rebuildHelp.querySelector(`[data-rebuild-help-kind="${kind}"] [data-close-rebuild-help]`)?.focus();
  }

  function closeRebuildHelp({ restoreFocus = true } = {}) {
    state.rebuildHelp.hidden = true;
    setReviewControlsInert(false);
    const invoker = state.rebuildHelpInvoker;
    state.rebuildHelpInvoker = null;
    if (restoreFocus && elements.dialog.open && invoker?.isConnected) invoker.focus();
  }
  state.openRebuildHelp = openRebuildHelp;

  for (const control of state.rebuildHelp.querySelectorAll("[data-close-rebuild-help]")) {
    control.addEventListener("click", closeRebuildHelp);
  }
  state.rebuildHelp.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closeRebuildHelp();
  });
  elements.dialog.addEventListener("cancel", event => {
    if (state.rebuildHelp.hidden) return;
    event.preventDefault();
    closeRebuildHelp({ restoreFocus: false });
  });

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
      unreviewedConversions: hasUnreviewedConversions(state.columnTypeConversions, state.conversionDrafts),
    });
    elements.apply.textContent = migrationExecutionNeedsPolling(state.execution) ? "Applying…" : "Apply migration";
  }

  function renderOverview(plan) {
    const overview = element("section", { className: "migration-overview" });
    const title = plan.status === "blocked"
      ? "Resolve blockers before applying"
      : !plan.steps.length && plan.complete
        ? plan.columnOrderRebuilds?.length
          ? "App order saved; physical reorder is optional"
          : "PostgreSQL is up to date"
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
      const columnOrder = renderColumnOrderRebuilds(plan, state, refresh);
      const conversions = renderColumnTypeConversions(plan, state, refresh, updateActions);
      const confirmations = renderConfirmations(plan);
      for (const section of [conversions, blockers, external, conflicts, columnOrder, renderSteps(plan), warnings, confirmations]) {
        if (section) elements.body.append(section);
      }
    }
    updateActions();
  }

  async function finishExecution(version, workspaceId) {
    if (!current(version, workspaceId) || state.handledExecutionId === state.execution?.id) return;
    if (!state.execution || !["succeeded", "failed"].includes(state.execution.status)) return;
    state.handledExecutionId = state.execution.id;
    try {
      if (state.execution.status === "succeeded") {
        // The immutable plan is consumed as soon as PostgreSQL reports a
        // successful execution. Clear it before the workspace reload so even
        // a refresh failure cannot leave an applied plan actionable.
        clearAppliedMigrationReview(state);
        await reloadWorkspace();
        if (!current(version, workspaceId)) return;
        notify("Migration committed and the workspace baseline was refreshed.");
      } else {
        notify("Migration rolled back. A fresh review is ready to retry.");
      }
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
        columnTypeConversions: [...state.columnTypeConversions.values()],
        rebuildTableIds: state.rebuildTableIds === null
          ? null
          : [...state.rebuildTableIds],
      }, { signal: beginRequest() });
      if (!current(version, workspaceId)) return;
      state.plan = plan;
      state.columnTypeConversions = retainedConversionChoices(plan.columnTypeConversions, state.columnTypeConversions);
      state.conversionDrafts = retainedConversionChoices(plan.columnTypeConversions, state.conversionDrafts);
      state.rebuildTableIds = new Set(
        plan.columnOrderRebuilds
          .filter(rebuild => rebuild.selected)
          .map(rebuild => rebuild.tableId),
      );
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
    state.rebuildTableIds = null;
    state.columnTypeConversions.clear();
    state.conversionDrafts.clear();
    state.handledExecutionId = null;
    closeRebuildHelp();
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
      unreviewedConversions: hasUnreviewedConversions(state.columnTypeConversions, state.conversionDrafts),
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
      plan.columnOrderRebuilds?.some(rebuild => rebuild.selected)
        ? "physical column reconstruction"
        : null,
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
    close: () => {
      closeRebuildHelp({ restoreFocus: false });
      invalidate();
    },
    refresh,
    render,
    available: () => Boolean(context()),
  };
}
