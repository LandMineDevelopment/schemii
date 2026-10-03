import { createDraftAnalysisLifecycle } from "./draft-analysis.js";

export function createDesignEditorControllers({ api, state, elements, helpers }) {
  const {
    isDesignWorkspace,
    showToast,
    quoteSqlIdentifier,
    renderDesignViewStory,
    openDialog,
    replace,
    element,
    emptyPanel,
    errorPanel,
    createStatePanel,
    saveDesignView,
    saveDesignType,
    saveDesignRoutine,
    saveDesignTrigger,
    flushLayoutBeforeTransition,
    replaceActiveDesign,
    updateDesignControls,
    updateHeader,
    syncWorkspaceNavigation,
    requestDesignObjectDeletion,
    conflictPanel,
    renderTypesBrowser,
    renderFunctionsBrowser,
    selectedDesignTable,
    askConfirmation,
  } = helpers;

  const editorIds = { view: null, type: null, routine: null, trigger: null };
  const editors = {
    view: {
      dialog: elements.designViewDialog,
      read: () => [elements.designViewName.value, elements.designViewKind.value,
        elements.designViewDefinition.value,
        elements.designViewKind.value === "materialized_view" ? elements.designViewPopulate.checked : null],
    },
    type: { dialog: elements.designTypeDialog, read: () => [elements.designTypeDefinition.value] },
    routine: { dialog: elements.designRoutineDialog, read: () => [elements.designRoutineDefinition.value] },
    trigger: { dialog: elements.designTriggerDialog, read: () => [elements.designTriggerDefinition.value] },
  };
  const savedDrafts = new Map();
  let discardPending = false;
  const draftSnapshot = kind => JSON.stringify(editors[kind].read());
  const hasDraft = kind => editors[kind].dialog.open
    && savedDrafts.has(kind) && savedDrafts.get(kind) !== draftSnapshot(kind);

  function resetEditor(kind) {
    savedDrafts.delete(kind);
    draftAnalysis[kind].reset();
    editorIds[kind] = null;
  }

  function discardDrafts(kinds = Object.keys(editors)) {
    for (const kind of kinds) {
      resetEditor(kind);
      if (editors[kind].dialog.open) editors[kind].dialog.close();
    }
  }

  async function requestDiscardDrafts(kinds = Object.keys(editors)) {
    if (state.designSubmitting) {
      showToast("Wait for the design save to finish before leaving the editor.");
      return false;
    }
    if (discardPending) return false;
    const dirty = kinds.filter(hasDraft);
    if (dirty.length) {
      discardPending = true;
      const discard = await new Promise(resolve => askConfirmation({
        title: "Discard unsaved changes?",
        message: `Your unsaved ${dirty.join(", ")} ${dirty.length === 1 ? "draft has" : "drafts have"} not been saved to this design. Keep editing to preserve your changes.`,
        label: "Discard changes",
        cancelLabel: "Keep editing",
        callback: () => resolve(true),
        onCancel: () => resolve(false),
      }));
      discardPending = false;
      if (!discard) return false;
    }
    discardDrafts(kinds);
    return true;
  }

  function requestCloseDialog(dialog) {
    const kind = Object.keys(editors).find(key => editors[key].dialog === dialog);
    if (!kind) return false;
    void requestDiscardDrafts([kind]);
    return true;
  }

  function finishSavedDraft(kind, submittedDraft, objectId) {
    savedDrafts.set(kind, submittedDraft);
    // Edits made during the request belong to the next save.
    if (hasDraft(kind)) {
      editorIds[kind] = objectId;
      const label = kind[0].toUpperCase() + kind.slice(1);
      elements[`saveDesign${label}Button`].textContent = `Save ${kind}`;
      elements[`design${label}Title`].textContent = `Edit ${kind}`;
      replace(elements[`design${label}Status`], element("span", {
        text: "The submitted draft was saved. Your newer changes still need to be saved.",
      }));
      return false;
    }
    resetEditor(kind);
    editors[kind].dialog.close();
    return true;
  }

  let previewOutputOrdinal = null;
  const draftAnalysis = {
    view: createDraftAnalysisLifecycle({
      isActive: () => elements.designViewDialog.open,
      onChange: renderDesignViewPreview,
    }),
    type: createDraftAnalysisLifecycle({
      isActive: () => elements.designTypeDialog.open && Boolean(state.activeWorkspace),
      onChange: renderDesignTypePreview,
    }),
    routine: createDraftAnalysisLifecycle({
      isActive: () => elements.designRoutineDialog.open && Boolean(state.activeWorkspace),
      onChange: renderDesignRoutinePreview,
    }),
    trigger: createDraftAnalysisLifecycle({
      isActive: () => elements.designTriggerDialog.open && Boolean(state.activeWorkspace),
      onChange: renderDesignTriggerPreview,
    }),
  };

  function designViewDraft() {
    return {
      designId: editorIds.view,
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
    const analysisState = draftAnalysis.view.snapshot();
    renderDesignViewStory(elements.designViewPreview, {
      view: designViewDraft(),
      analysis: analysisState.result,
      loading: analysisState.loading,
      error: analysisState.error,
      selectedOutputOrdinal: previewOutputOrdinal,
      onSelectOutput: output => {
        previewOutputOrdinal = output.ordinal;
        renderDesignViewPreview();
      },
      compact: true,
    });
  }

  async function analyzeDesignViewDraft() {
    const definition = elements.designViewDefinition.value.trim();
    const workspaceId = state.activeWorkspace.id;
    return api.analyzeDesignView(workspaceId, {
      viewId: editorIds.view,
      name: elements.designViewName.value.trim() || "new_view",
      definition,
    });
  }

  function scheduleDesignViewPreview(delay = 280) {
    draftAnalysis.view.schedule(analyzeDesignViewDraft, {
      wait: delay,
      canAnalyze: () => Boolean(elements.designViewDefinition.value.trim()),
    });
  }

  function updateDesignViewPopulation() {
    elements.designViewPopulationRow.hidden = elements.designViewKind.value !== "materialized_view";
    renderDesignViewPreview();
  }

  async function openDesignViewEditor(viewId = null) {
    if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
    const view = viewId ? state.design.content.views.find(item => item.id === viewId) : null;
    if (viewId && !view) {
      showToast("The selected view is no longer in this design.", { error: true });
      return;
    }
    if (!await requestDiscardDrafts()) return;
    editorIds.view = view?.id || null;
    draftAnalysis.view.reset();
    previewOutputOrdinal = null;
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
      ? `SELECT\n    *\nFROM ${quoteSqlIdentifier(firstTable.name)}`
      : "SELECT\n    1 AS example");
    updateDesignViewPopulation();
    savedDrafts.set("view", draftSnapshot("view"));
    openDialog(elements.designViewDialog);
    renderDesignViewPreview();
    scheduleDesignViewPreview(0);
    elements.designViewName.focus();
  }

  async function submitDesignView(event) {
    event.preventDefault();
    if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
    const submittedDraft = draftSnapshot("view");
    const editing = Boolean(editorIds.view);
    let result;
    try {
      result = saveDesignView(state.design.content, {
        viewId: editorIds.view,
        name: elements.designViewName.value,
        kind: elements.designViewKind.value,
        populateOnCreate: elements.designViewPopulate.checked,
        definition: elements.designViewDefinition.value,
      });
    } catch (error) {
      replace(elements.designViewStatus, errorPanel(error));
      return;
    }
    state.designSubmitting = true;
    elements.saveDesignViewButton.disabled = true;
    updateDesignControls();
    replace(elements.designViewStatus, element("span", { text: "Validating the query and saving the desired view…" }));
    try {
      if (!await flushLayoutBeforeTransition()) return;
      const design = await replaceActiveDesign(result.content, {
        selectedViewId: result.view.id,
      });
      if (!design) return;
      finishSavedDraft("view", submittedDraft, result.view.id);
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

  function renderDesignTypePreview() {
    replace(elements.designTypePreview);
    const definition = elements.designTypeDefinition.value.trim();
    const analysisState = draftAnalysis.type.snapshot();
    if (!definition) {
      elements.designTypePreview.append(emptyPanel("TYPE", "Write the type source", "Its enum values or domain contract will appear here."));
      return;
    }
    if (analysisState.loading) {
      elements.designTypePreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
      return;
    }
    if (analysisState.error) {
      elements.designTypePreview.append(errorPanel(analysisState.error));
      return;
    }
    const contract = analysisState.result?.analysis;
    if (!contract || analysisState.result.definition !== definition) {
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
    const definition = elements.designTypeDefinition.value.trim();
    const analysis = await api.analyzeDesignType(state.activeWorkspace.id, { definition });
    return { analysis, definition };
  }

  function scheduleDesignTypeAnalysis(delay = 280) {
    draftAnalysis.type.schedule(analyzeDesignTypeDraft, {
      wait: delay,
      canAnalyze: () => Boolean(elements.designTypeDefinition.value.trim()),
    });
  }

  async function openDesignTypeEditor(typeId = null) {
    if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
    const designType = typeId ? (state.design.content.types || []).find(item => item.id === typeId) : null;
    if (typeId && !designType) {
      showToast("The selected type is no longer in this design.", { error: true });
      return;
    }
    if (!await requestDiscardDrafts()) return;
    editorIds.type = designType?.id || null;
    draftAnalysis.type.reset();
    elements.designTypeForm.reset();
    replace(elements.designTypeStatus);
    elements.designTypeTitle.textContent = designType ? `Edit ${designType.name}` : "Create enum or domain";
    elements.saveDesignTypeButton.textContent = designType ? "Save type" : "Create type";
    elements.designTypeDefinition.value = designType?.definition || "CREATE TYPE order_status AS ENUM (\n    'draft',\n    'submitted',\n    'fulfilled',\n    'cancelled'\n);";
    savedDrafts.set("type", draftSnapshot("type"));
    openDialog(elements.designTypeDialog);
    renderDesignTypePreview();
    scheduleDesignTypeAnalysis(0);
    elements.designTypeDefinition.focus();
  }

  async function submitDesignType(event) {
    event.preventDefault();
    if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
    const submittedDraft = draftSnapshot("type");
    const editing = Boolean(editorIds.type);
    const definition = elements.designTypeDefinition.value.trim();
    state.designSubmitting = true;
    elements.saveDesignTypeButton.disabled = true;
    updateDesignControls();
    replace(elements.designTypeStatus, element("span", { text: "Deriving the contract and saving the custom type…" }));
    try {
      const analysis = await api.analyzeDesignType(state.activeWorkspace.id, { definition });
      const result = saveDesignType(state.design.content, {
        typeId: editorIds.type,
        definition,
      });
      if (!await flushLayoutBeforeTransition()) return;
      const design = await replaceActiveDesign(result.content);
      if (!design) return;
      const closed = finishSavedDraft("type", submittedDraft, result.designType.id);
      renderTypesBrowser();
      if (closed) openDialog(elements.typesDialog);
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

  function routineSignature(contract) {
    return `${contract.name}(${contract.identityArguments})`;
  }

  function renderDesignRoutinePreview() {
    replace(elements.designRoutinePreview);
    const definition = elements.designRoutineDefinition.value.trim();
    const analysisState = draftAnalysis.routine.snapshot();
    if (!definition) {
      elements.designRoutinePreview.append(emptyPanel("FN", "Write the routine source", "Its callable signature and PostgreSQL contract will appear here."));
      return;
    }
    if (analysisState.loading) {
      elements.designRoutinePreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
      return;
    }
    if (analysisState.error) {
      elements.designRoutinePreview.append(errorPanel(analysisState.error));
      return;
    }
    const contract = analysisState.result?.analysis;
    if (!contract || analysisState.result.definition !== definition) {
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
    const definition = elements.designRoutineDefinition.value.trim();
    const analysis = await api.analyzeDesignRoutine(state.activeWorkspace.id, { definition });
    return { analysis, definition };
  }

  function scheduleDesignRoutineAnalysis(delay = 280) {
    draftAnalysis.routine.schedule(analyzeDesignRoutineDraft, {
      wait: delay,
      canAnalyze: () => Boolean(elements.designRoutineDefinition.value.trim()),
    });
  }

  async function openDesignRoutineEditor(routineId = null) {
    if (!isDesignWorkspace() || !state.design || state.catalogLoading) return;
    const routine = routineId ? state.design.content.functions.find(item => item.id === routineId) : null;
    if (routineId && !routine) {
      showToast("The selected routine is no longer in this design.", { error: true });
      return;
    }
    if (!await requestDiscardDrafts()) return;
    editorIds.routine = routine?.id || null;
    draftAnalysis.routine.reset();
    elements.designRoutineForm.reset();
    replace(elements.designRoutineStatus);
    elements.designRoutineTitle.textContent = routine ? `Edit ${routine.name}` : "Create function or procedure";
    elements.saveDesignRoutineButton.textContent = routine ? "Save routine" : "Create routine";
    elements.designRoutineDefinition.value = routine?.definition || "";
    savedDrafts.set("routine", draftSnapshot("routine"));
    openDialog(elements.designRoutineDialog);
    renderDesignRoutinePreview();
    if (routine) scheduleDesignRoutineAnalysis(0);
    elements.designRoutineDefinition.focus();
  }

  async function submitDesignRoutine(event) {
    event.preventDefault();
    if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
    const submittedDraft = draftSnapshot("routine");
    const editing = Boolean(editorIds.routine);
    const definition = elements.designRoutineDefinition.value.trim();
    state.designSubmitting = true;
    elements.saveDesignRoutineButton.disabled = true;
    updateDesignControls();
    replace(elements.designRoutineStatus, element("span", { text: "Deriving the contract and saving the routine…" }));
    try {
      const analysis = await api.analyzeDesignRoutine(state.activeWorkspace.id, { definition });
      const result = saveDesignRoutine(state.design.content, {
        routineId: editorIds.routine,
        definition,
      });
      if (!await flushLayoutBeforeTransition()) return;
      const design = await replaceActiveDesign(result.content);
      if (!design) return;
      const closed = finishSavedDraft("routine", submittedDraft, result.routine.id);
      renderFunctionsBrowser();
      if (closed) openDialog(elements.functionsDialog);
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
    const analysisState = draftAnalysis.trigger.snapshot();
    if (!definition) {
      elements.designTriggerPreview.append(emptyPanel("TRG", "Write the trigger source", "Its target, activation rules, and function call will appear here."));
      return;
    }
    if (analysisState.loading) {
      elements.designTriggerPreview.append(createStatePanel({ mark: "…", title: "Deriving contract", message: "Parsing the PostgreSQL statement without contacting a database.", surface: true }));
      return;
    }
    if (analysisState.error) {
      elements.designTriggerPreview.append(errorPanel(analysisState.error));
      return;
    }
    const contract = analysisState.result?.analysis;
    if (!contract || analysisState.result.definition !== definition) {
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
    const definition = elements.designTriggerDefinition.value.trim();
    const analysis = await api.analyzeDesignTrigger(state.activeWorkspace.id, { definition });
    return { analysis, definition };
  }

  function scheduleDesignTriggerAnalysis(delay = 280) {
    draftAnalysis.trigger.schedule(analyzeDesignTriggerDraft, {
      wait: delay,
      canAnalyze: () => Boolean(elements.designTriggerDefinition.value.trim()),
    });
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
      `CREATE TRIGGER ${quoteSqlIdentifier(`${relationName}_changed`)}`,
      `AFTER INSERT OR UPDATE OR DELETE ON ${quoteSqlIdentifier(relationName)}`,
      "FOR EACH ROW",
      `EXECUTE FUNCTION ${quoteSqlIdentifier(functionName)}();`,
    ].join("\n");
  }

  async function openDesignTriggerEditor(triggerId = null, relationName = null) {
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
    if (!await requestDiscardDrafts()) return;
    editorIds.trigger = trigger?.id || null;
    draftAnalysis.trigger.reset();
    elements.designTriggerForm.reset();
    replace(elements.designTriggerStatus);
    elements.designTriggerTitle.textContent = trigger ? `Edit ${trigger.name}` : "Create trigger";
    elements.saveDesignTriggerButton.textContent = trigger ? "Save trigger" : "Create trigger";
    elements.deleteDesignTriggerButton.hidden = !trigger;
    elements.designTriggerDefinition.value = trigger?.definition || defaultTriggerDefinition(fallbackRelation);
    savedDrafts.set("trigger", draftSnapshot("trigger"));
    openDialog(elements.designTriggerDialog);
    renderDesignTriggerPreview();
    scheduleDesignTriggerAnalysis(0);
    elements.designTriggerDefinition.focus();
  }

  async function submitDesignTrigger(event) {
    event.preventDefault();
    if (state.designSubmitting || !isDesignWorkspace() || !state.design) return;
    const submittedDraft = draftSnapshot("trigger");
    const editing = Boolean(editorIds.trigger);
    const definition = elements.designTriggerDefinition.value.trim();
    state.designSubmitting = true;
    elements.saveDesignTriggerButton.disabled = true;
    updateDesignControls();
    replace(elements.designTriggerStatus, element("span", { text: "Deriving the contract and saving the trigger…" }));
    try {
      const analysis = await api.analyzeDesignTrigger(state.activeWorkspace.id, { definition });
      const result = saveDesignTrigger(state.design.content, {
        triggerId: editorIds.trigger,
        definition,
      });
      if (!await flushLayoutBeforeTransition()) return;
      const relationTable = state.design.content.tables.find(table => table.name === analysis.relationName) || null;
      const design = await replaceActiveDesign(result.content, {
        selectedTableId: relationTable?.id || state.selectedTableId,
      });
      if (!design) return;
      finishSavedDraft("trigger", submittedDraft, result.trigger.id);
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
    const triggerId = trigger?.designId || trigger?.id || editorIds.trigger;
    requestDesignObjectDeletion(triggerId, { statusTarget: elements.designTriggerStatus });
  }

  return {
    get hasDraft() { return Object.keys(editors).some(hasDraft); },
    requestDiscardDrafts,
    discardDrafts,
    requestCloseDialog,
    openDesignViewEditor,
    submitDesignView,
    confirmDeleteDesignView,
    renderDesignViewPreview,
    scheduleDesignViewPreview,
    updateDesignViewPopulation,
    closeDesignViewEditor() {
      if (!editors.view.dialog.open) resetEditor("view");
    },
    openDesignTypeEditor,
    submitDesignType,
    confirmDeleteDesignType,
    renderDesignTypePreview,
    scheduleDesignTypeAnalysis,
    closeDesignTypeEditor() {
      if (!editors.type.dialog.open) resetEditor("type");
    },
    openDesignRoutineEditor,
    submitDesignRoutine,
    confirmDeleteDesignRoutine,
    renderDesignRoutinePreview,
    scheduleDesignRoutineAnalysis,
    closeDesignRoutineEditor() {
      if (!editors.routine.dialog.open) resetEditor("routine");
    },
    openDesignTriggerEditor,
    submitDesignTrigger,
    confirmDeleteDesignTrigger,
    renderDesignTriggerPreview,
    scheduleDesignTriggerAnalysis,
    closeDesignTriggerEditor() {
      if (!editors.trigger.dialog.open) resetEditor("trigger");
    },
    currentTriggerEditorId: () => editorIds.trigger,
  };
}
