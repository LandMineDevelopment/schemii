import { element, errorPanel, replace } from "./dom.js";
import { updateDesignTable } from "./design.js";
import { createIconButton } from "/assets/common/ui.js";
import { createSearchableSelect } from "/assets/common/searchable-select.js";
import { installSortableList } from "/assets/common/sortable.js";
import {
  composePostgresTypeModifier,
  parsePostgresTypeModifier,
  postgresTypeModifierSummary,
  postgresTypeOptions,
} from "/assets/common/postgres-types.js";

/**
 * Owns table-column controls and the inspector's unsaved draft lifecycle.
 * Workspace persistence, history, navigation and change transitions remain app-owned.
 * Both the create-table dialog and inspector use the same column controls.
 */
export function createTableEditor({
  elements,
  getContext,
  setSubmitting,
  onDraftChange,
  onSettled,
  flushLayoutBeforeTransition,
  saveTable,
  onSaved,
  conflictPanel,
  requestDesignObjectDeletion,
  changeTransitions,
  notify,
  showExpressionHelp,
  analyzeColumnType,
}) {
  let editingId = null;
  let dirty = false;
  let populating = false;
  let designColumnDraftSequence = 0;

  function appendDesignColumn({
    id = null,
    name = "",
    dataType = "text",
    nullable = true,
    primary = false,
    defaultExpression = null,
    identity = null,
    generatedExpression = null,
  } = {}, {
    container = elements.designColumns,
    sorter = designTableColumnSorter,
    onMutate = null,
  } = {}) {
    designColumnDraftSequence += 1;
    const transitionId = id || `draft-column-${designColumnDraftSequence}`;
    const row = element("div", {
      className: "design-column-row",
      dataset: {
        designColumnId: id || "",
        sortKey: transitionId,
        changeObjectId: transitionId,
        changeRoot: "",
        changeField: "order check index relationship",
      },
    });
    const sortHandle = createIconButton({
      icon: "drag",
      label: `Reorder ${name || "new column"}`,
      tooltip: `Drag to reorder ${name || "new column"}`,
      className: "compact design-sort-handle",
    });
    sortHandle.dataset.sortHandle = "";
    const nameInput = element("input", { attrs: { required: "", maxlength: "63", autocomplete: "off", value: name, placeholder: "column_name", "aria-label": "Column name" }, dataset: { designColumnName: "" } });
    const typeSelector = createSearchableSelect({
      value: dataType,
      options: postgresTypeOptions({ customTypes: getContext().catalog?.types, currentValue: dataType }),
      label: `PostgreSQL type for ${name || "column"}`,
      placeholder: "Search types",
      required: true,
      dataset: { designColumnType: "" },
      noResultsText: "No matching PostgreSQL type",
    });
    const typeInput = typeSelector.input;
    const typeWarning = element("p", { className: "design-type-warning", hidden: true, attrs: { role: "status", "aria-live": "polite" } });
    let typeAnalysisTimer = null;
    let typeAnalysisController = null;
    let typeAnalysisVersion = 0;
    const cancelTypeAnalysis = () => {
      typeAnalysisVersion += 1;
      clearTimeout(typeAnalysisTimer);
      typeAnalysisController?.abort();
    };
    const scheduleTypeAnalysis = () => {
      cancelTypeAnalysis();
      const workspaceId = getContext().activeWorkspace?.id;
      if (!id || !getContext().activeWorkspace?.connectionId) return;
      const targetType = typeInput.value;
      const version = typeAnalysisVersion;
      typeWarning.hidden = true;
      typeAnalysisTimer = setTimeout(async () => {
        if (!row.isConnected || getContext().activeWorkspace?.id !== workspaceId) return;
        typeAnalysisController = new AbortController();
        try {
          const analysis = await analyzeColumnType(workspaceId, { columnId: id, targetType }, { signal: typeAnalysisController.signal });
          if (version !== typeAnalysisVersion || !row.isConnected || getContext().activeWorkspace?.id !== workspaceId) return;
          typeWarning.hidden = !analysis.requiresConversion;
          typeWarning.textContent = analysis.requiresConversion
            ? `${analysis.reason}. You can save this design. During migration, explicitly choose how existing values should be converted; strict conversion is the recommended default.`
            : "";
        } catch (error) {
          if (version !== typeAnalysisVersion || !row.isConnected || error?.code === "request_cancelled") return;
          typeWarning.hidden = false;
          typeWarning.textContent = "The conversion check is unavailable. You can save this design; migration review will validate the type change before applying it.";
        }
      }, 300);
    };
    row.__cancelTypeAnalysis = cancelTypeAnalysis;
    typeSelector.root.dataset.changeObjectId = transitionId;
    typeSelector.root.dataset.changeField = "dataType";
    const typeModifierCopy = element("span", { className: "design-type-modifier-summary" });
    const lengthInput = element("input", {
      type: "number",
      attrs: { min: "1", step: "1", inputmode: "numeric", placeholder: "Unlimited", "aria-label": "Maximum type length" },
      dataset: { designTypeLength: "" },
    });
    const precisionInput = element("input", {
      type: "number",
      attrs: { min: "1", max: "1000", step: "1", inputmode: "numeric", placeholder: "Any", "aria-label": "Numeric precision" },
      dataset: { designTypePrecision: "" },
    });
    const scaleInput = element("input", {
      type: "number",
      attrs: { min: "-1000", max: "1000", step: "1", inputmode: "numeric", placeholder: "0", "aria-label": "Numeric scale" },
      dataset: { designTypeScale: "" },
    });
    const fractionalInput = element("input", {
      type: "number",
      attrs: { min: "0", max: "6", step: "1", inputmode: "numeric", placeholder: "Default", "aria-label": "Fractional seconds precision" },
      dataset: { designTypeFractional: "" },
    });
    const lengthField = element("label", { className: "design-type-modifier-field" }, [
      element("span", { text: "Maximum length" }),
      lengthInput,
      element("small", { text: "Leave blank for the PostgreSQL default." }),
    ]);
    const precisionField = element("label", { className: "design-type-modifier-field" }, [
      element("span", { text: "Precision" }),
      precisionInput,
      element("small", { text: "Total significant digits · 1–1000." }),
    ]);
    const scaleField = element("label", { className: "design-type-modifier-field" }, [
      element("span", { text: "Scale" }),
      scaleInput,
      element("small", { text: "Digits after the decimal · −1000–1000." }),
    ]);
    const fractionalField = element("label", { className: "design-type-modifier-field" }, [
      element("span", { text: "Fractional seconds" }),
      fractionalInput,
      element("small", { text: "Digits after the second · 0–6." }),
    ]);
    const typeModifierDetails = element("details", { className: "design-type-modifiers" }, [
      element("summary", {}, [
        element("span", { text: "Customize type limits" }),
        typeModifierCopy,
      ]),
      element("div", { className: "design-type-modifier-fields" }, [lengthField, precisionField, scaleField, fractionalField]),
    ]);
    typeModifierDetails.dataset.changeObjectId = transitionId;
    typeModifierDetails.dataset.changeField = "dataType";

    const typeOptions = currentValue => postgresTypeOptions({ customTypes: getContext().catalog?.types, currentValue });
    const clearModifierValidity = () => {
      for (const input of [lengthInput, precisionInput, scaleInput, fractionalInput]) input.setCustomValidity("");
    };
    const syncTypeModifierEditor = ({ expand = false } = {}) => {
      const modifier = parsePostgresTypeModifier(typeInput.value);
      clearModifierValidity();
      typeModifierDetails.hidden = !modifier;
      row.classList.toggle("has-type-modifiers", Boolean(modifier));
      if (!modifier) {
        typeModifierDetails.open = false;
        return;
      }
      lengthField.hidden = modifier.kind !== "length";
      precisionField.hidden = modifier.kind !== "numeric";
      scaleField.hidden = modifier.kind !== "numeric";
      fractionalField.hidden = modifier.kind !== "fractional";
      lengthInput.max = String(modifier.maxLength || "");
      lengthInput.value = modifier.length ?? "";
      precisionInput.value = modifier.kind === "numeric" ? (modifier.precision ?? "") : "";
      scaleInput.value = modifier.kind === "numeric" ? (modifier.scale ?? "") : "";
      fractionalInput.value = modifier.kind === "fractional" ? (modifier.precision ?? "") : "";
      typeModifierCopy.textContent = postgresTypeModifierSummary(modifier);
      if (expand) typeModifierDetails.open = true;
    };
    const applyTypeModifiers = event => {
      const modifier = parsePostgresTypeModifier(typeInput.value);
      if (!modifier) return;
      clearModifierValidity();
      try {
        const revisedType = composePostgresTypeModifier(modifier, {
          length: lengthInput.value,
          precision: precisionInput.value,
          scale: scaleInput.value,
        });
        const finalType = modifier.kind === "fractional"
          ? composePostgresTypeModifier(modifier, { precision: fractionalInput.value })
          : revisedType;
        typeSelector.setOptions(typeOptions(finalType));
        typeSelector.setValue(finalType);
        typeModifierCopy.textContent = postgresTypeModifierSummary(parsePostgresTypeModifier(finalType));
        scheduleTypeAnalysis();
      } catch (error) {
        event.currentTarget.setCustomValidity(error.message);
      }
    };
    for (const input of [lengthInput, precisionInput, scaleInput, fractionalInput]) {
      input.addEventListener("input", applyTypeModifiers);
    }
    typeInput.addEventListener("change", () => {
      typeSelector.setOptions(typeOptions(typeInput.value));
      syncTypeModifierEditor({ expand: true });
      scheduleTypeAnalysis();
    });
    const nullableInput = element("input", { type: "checkbox", dataset: { designColumnNullable: "" } });
    nullableInput.checked = nullable && !primary;
    const primaryInput = element("input", { type: "checkbox", dataset: { designColumnPrimary: "" } });
    primaryInput.checked = primary;
    const behaviorSelect = element("select", { dataset: { designColumnBehavior: "" }, attrs: { "aria-label": `Value behavior for ${name || "column"}` } });
    const behaviorOptions = [
      ["none", "Entered by the application"],
      ["default", "Default expression"],
      ["identity_by_default", "Identity · by default"],
      ["identity_always", "Identity · always"],
      ["generated", "Generated from columns"],
    ];
    for (const [value, label] of behaviorOptions) behaviorSelect.append(element("option", { text: label, attrs: { value } }));
    behaviorSelect.value = generatedExpression
      ? "generated"
      : identity === "always"
        ? "identity_always"
        : identity === "by_default"
          ? "identity_by_default"
          : defaultExpression
            ? "default"
            : "none";
    const expressionInput = element("input", {
      attrs: { maxlength: "262144", autocomplete: "off", value: generatedExpression || defaultExpression || "" },
      dataset: { designColumnExpression: "" },
    });
    const expressionTitle = element("span");
    const expressionHelp = createIconButton({
      icon: "info",
      label: "Allowed calculated-column expression syntax",
      tooltip: "Allowed calculated-column expression syntax",
      className: "compact",
    });
    expressionHelp.addEventListener("click", () => showExpressionHelp());
    const expressionHeading = element("span", { className: "design-expression-heading" }, [expressionTitle, expressionHelp]);
    const expressionField = element("div", { className: "design-expression-field" }, [expressionHeading, expressionInput]);
    const cueField = (node, fields) => {
      node.dataset.changeObjectId = transitionId;
      node.dataset.changeField = fields;
    };
    cueField(nameInput, "name");
    cueField(nullableInput, "nullable");
    cueField(primaryInput, "primary unique");
    cueField(behaviorSelect, "defaultExpression identity generatedExpression");
    cueField(expressionInput, "defaultExpression generatedExpression");
    const syncBehavior = () => {
      const generated = behaviorSelect.value === "generated";
      const defaulted = behaviorSelect.value === "default";
      const identityBehavior = behaviorSelect.value.startsWith("identity_");
      expressionField.hidden = !generated && !defaulted;
      expressionTitle.textContent = generated ? "Generation expression" : "Default expression";
      expressionHelp.hidden = !generated;
      expressionInput.placeholder = generated ? "quantity * unit_price" : "now()";
      expressionInput.setAttribute("aria-label", generated ? "Generation expression" : "Default expression");
      expressionInput.required = generated || defaulted;
      nullableInput.disabled = primaryInput.checked || identityBehavior;
      if (nullableInput.disabled) nullableInput.checked = false;
    };
    primaryInput.addEventListener("change", syncBehavior);
    behaviorSelect.addEventListener("change", syncBehavior);
    nameInput.addEventListener("input", () => {
      typeSelector.setLabel(`PostgreSQL type for ${nameInput.value || "column"}`);
      sorter.refresh();
    });
    const remove = createIconButton({
      icon: "delete",
      label: `Remove ${name || "column"}`,
      tooltip: "Remove column",
      className: "compact danger design-column-remove",
    });
    nameInput.addEventListener("input", () => remove.setAttribute("aria-label", `Remove ${nameInput.value || "column"}`));
    remove.addEventListener("click", () => {
      if (container.childElementCount === 1) {
        notify("A designed table needs at least one column.");
        return;
      }
      const removeDraftRow = async () => {
        if (row.dataset.changeRemoving === "true") return;
        row.dataset.changeRemoving = "true";
        remove.disabled = true;
        const prepared = await changeTransitions.prepare([{
          objectId: transitionId,
          operation: "remove",
          tone: "amber",
        }]);
        typeSelector.destroy();
        cancelTypeAnalysis();
        row.remove();
        sorter.refresh();
        onMutate?.();
        if (container === elements.inspectorDesignColumns) updateInspectorColumnCount();
        changeTransitions.reflow(prepared);
      };
      if (!id) {
        void removeDraftRow();
        return;
      }
      requestDesignObjectDeletion(id, {
        statusTarget: container === elements.inspectorDesignColumns
          ? elements.inspectorTableStatus
          : elements.designTableStatus,
        onConfirmed: removeDraftRow,
      });
    });
    row.append(
      sortHandle,
      element("label", { className: "design-column-name" }, [element("span", { text: "Name" }), nameInput]),
      element("label", { className: "design-column-type" }, [element("span", { text: "PostgreSQL type" }), typeSelector.root]),
      element("label", { className: "design-column-check design-column-nullable" }, [nullableInput, element("span", { text: "Nullable" })]),
      element("label", { className: "design-column-check design-column-primary" }, [primaryInput, element("span", { text: "Primary" })]),
      remove,
      typeModifierDetails,
      element("div", { className: "design-column-value" }, [
        typeWarning,
        element("label", {}, [element("span", { text: "Value behavior" }), behaviorSelect]),
        expressionField,
      ]),
    );
    syncTypeModifierEditor();
    syncBehavior();
    row.__designTypeSelector = typeSelector;
    container.append(row);
    scheduleTypeAnalysis();
    sorter.refresh();
    return row;
  }

  function clearDesignColumns(container) {
    for (const row of container.children) {
      row.__cancelTypeAnalysis?.();
      row.__designTypeSelector?.destroy();
    }
    replace(container);
  }

  function designColumnValues(container = elements.designColumns) {
    return [...container.children].map(row => {
      const behavior = row.querySelector("[data-design-column-behavior]").value;
      const expression = row.querySelector("[data-design-column-expression]").value;
      return {
        id: row.dataset.designColumnId || null,
        name: row.querySelector("[data-design-column-name]").value,
        dataType: row.querySelector("[data-design-column-type]").value,
        nullable: row.querySelector("[data-design-column-nullable]").checked,
        primary: row.querySelector("[data-design-column-primary]").checked,
        defaultExpression: behavior === "default" ? expression : null,
        identity: behavior === "identity_always" ? "always" : behavior === "identity_by_default" ? "by_default" : null,
        generatedExpression: behavior === "generated" ? expression : null,
      };
    });
  }

  function inspectorTableContext() {
    return {
      container: elements.inspectorDesignColumns,
      sorter: inspectorTableColumnSorter,
      onMutate: markInspectorTableDirty,
    };
  }

  function updateInspectorColumnCount() {
    elements.inspectorColumnCount.textContent = String(elements.inspectorDesignColumns.childElementCount);
  }

  function updateInspectorTableActions() {
    const busy = getContext().designSubmitting || getContext().catalogLoading || getContext().layoutConflict;
    elements.saveInspectorTableButton.disabled = !dirty || busy;
    elements.discardInspectorTableButton.disabled = !dirty || busy;
    elements.addInspectorColumnButton.disabled = busy;
    elements.inspectorTableForm.toggleAttribute("inert", busy);
    elements.inspectorTableForm.setAttribute("aria-busy", busy ? "true" : "false");
    elements.inspector.classList.toggle("has-table-draft", dirty);
    elements.inspectorContent.toggleAttribute("inert", dirty || busy);
  }

  function markInspectorTableDirty() {
    if (populating || !editingId) return;
    dirty = true;
    updateInspectorColumnCount();
    updateInspectorTableActions();
    onDraftChange();
    replace(elements.inspectorTableStatus, element("span", { text: "Unsaved changes · save to edit related objects." }));
  }

  function populateInspectorTableEditor(table) {
    populating = true;
    editingId = table.id;
    dirty = false;
    elements.inspectorTableForm.dataset.changeObjectId = table.id;
    elements.inspectorTableForm.dataset.changeRoot = "";
    elements.inspectorTableName.dataset.changeObjectId = table.id;
    elements.inspectorTableName.dataset.changeField = "name";
    elements.inspectorTableName.value = table.name;
    clearDesignColumns(elements.inspectorDesignColumns);
    const primaryIds = new Set(table.keys.find(key => key.kind === "primary")?.columnIds || []);
    for (const column of table.columns) appendDesignColumn({
      ...column,
      primary: primaryIds.has(column.id),
    }, inspectorTableContext());
    populating = false;
    updateInspectorColumnCount();
    updateInspectorTableActions();
    onDraftChange();
    replace(elements.inspectorTableStatus, element("span", {
      text: `Saved in design revision ${getContext().design?.revision ?? "current"}.`,
    }));
  }

  function renderInspectorTableEditor(table) {
    const designedTable = table?.designId
      ? getContext().design?.content.tables.find(item => item.id === table.designId) || null
      : null;
    elements.inspector.classList.toggle("is-editable", Boolean(designedTable));
    elements.mainLayout.classList.toggle("inspector-table-editable", Boolean(designedTable));
    elements.inspectorTableForm.hidden = !designedTable;
    if (!designedTable) {
      delete elements.inspectorTableForm.dataset.changeObjectId;
      delete elements.inspectorTableForm.dataset.changeRoot;
      delete elements.inspectorTableName.dataset.changeObjectId;
      delete elements.inspectorTableName.dataset.changeField;
      editingId = null;
      dirty = false;
      elements.inspector.classList.remove("has-table-draft");
      elements.inspectorContent.removeAttribute("inert");
      clearDesignColumns(elements.inspectorDesignColumns);
      replace(elements.inspectorTableStatus);
      return;
    }
    if (editingId === designedTable.id && dirty) {
      elements.inspectorTitle.textContent = elements.inspectorTableName.value.trim() || "Untitled table";
      updateInspectorColumnCount();
      updateInspectorTableActions();
      return;
    }
    populateInspectorTableEditor(designedTable);
  }

  function discardInspectorTableChanges() {
    const table = getContext().design?.content.tables.find(item => item.id === editingId) || null;
    if (!table || getContext().designSubmitting) return;
    populateInspectorTableEditor(table);
    elements.inspectorTitle.textContent = table.name;
    elements.inspectorTableName.focus();
  }

  async function submitInspectorTable(event) {
    event.preventDefault();
    if (
      getContext().designSubmitting
      || !dirty
      || !getContext().editable
      || !getContext().design
    ) return;
    const tableId = editingId;
    let table;
    try {
      table = updateDesignTable(
        getContext().design.content,
        tableId,
        elements.inspectorTableName.value,
        designColumnValues(elements.inspectorDesignColumns),
      );
    } catch (error) {
      replace(elements.inspectorTableStatus, errorPanel(error));
      return;
    }

    setSubmitting(true);
    updateInspectorTableActions();
    onDraftChange();
    replace(elements.inspectorTableStatus, element("span", { text: "Validating and saving the table…" }));
    try {
      if (!await flushLayoutBeforeTransition()) {
        replace(elements.inspectorTableStatus, element("span", { text: "Unsaved changes · resolve the layout save before retrying." }));
        return;
      }
      dirty = false;
      const design = await saveTable(table);
      if (!design) return;
      onSaved(table, design);
    } catch (error) {
      dirty = true;
      replace(elements.inspectorTableStatus, conflictPanel(error));
    } finally {
      setSubmitting(false);
      updateInspectorTableActions();
      onSettled();
    }
  }

  function syncDraftAfterDeletedObject(result) {
    if (!dirty) return;
    if (result.kind === "column") {
      const row = elements.inspectorDesignColumns.querySelector(`[data-design-column-id="${CSS.escape(result.object.id)}"]`);
      row?.__cancelTypeAnalysis?.();
      row?.__designTypeSelector?.destroy();
      row?.remove();
      updateInspectorColumnCount();
    }
    if (result.kind === "key" && result.object.kind === "primary") {
      const ids = new Set(result.object.columnIds);
      for (const row of elements.inspectorDesignColumns.children) {
        if (ids.has(row.dataset.designColumnId)) row.querySelector("[data-design-column-primary]").checked = false;
      }
    }
  }

  const inspectorTableColumnSorter = installSortableList(elements.inspectorDesignColumns, {
    itemSelector: ".design-column-row",
    itemLabel: item => item.querySelector("[data-design-column-name]")?.value || "new column",
    onReorder: () => {
      markInspectorTableDirty();
      inspectorTableColumnSorter.refresh();
    },
  });
  const designTableColumnSorter = installSortableList(elements.designColumns, {
    itemSelector: ".design-column-row",
    itemLabel: item => item.querySelector("[data-design-column-name]")?.value || "new column",
    onReorder: () => designTableColumnSorter.refresh(),
  });

  elements.addInspectorColumnButton.addEventListener("click", () => {
    const row = appendDesignColumn({}, inspectorTableContext());
    markInspectorTableDirty();
    updateInspectorColumnCount();
    row.querySelector("[data-design-column-name]")?.focus();
  });
  elements.inspectorTableForm.addEventListener("input", event => {
    if (event.target === elements.inspectorTableName) {
      elements.inspectorTitle.textContent = event.target.value.trim() || "Untitled table";
    }
    markInspectorTableDirty();
  });
  elements.inspectorTableForm.addEventListener("change", markInspectorTableDirty);
  elements.inspectorTableForm.addEventListener("submit", submitInspectorTable);
  elements.discardInspectorTableButton.addEventListener("click", discardInspectorTableChanges);

  return {
    get hasDraft() { return dirty; },
    appendColumn: appendDesignColumn,
    clearColumns: clearDesignColumns,
    columnValues: designColumnValues,
    render: renderInspectorTableEditor,
    updateActions: updateInspectorTableActions,
    syncDeletedObject: syncDraftAfterDeletedObject,
  };
}
