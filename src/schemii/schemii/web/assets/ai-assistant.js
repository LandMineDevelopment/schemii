import { managedProviderForModel, populateScopedReasoningOptions, reasoningForSelection } from "#common/ai-reasoning.js";
import { designExportReceipt, workspaceReceiptUrl } from "./ai-app-receipts.js";
import { downloadContent, createIconElement } from "#common/ui.js";
import { validateCopyHandoff, openCopyHandoff } from "./ai-copy-handoff.js";
import { requestJson } from "#common/http.js";
import { renderMarkdown } from "#common/ai-markdown.js";
import { createMessageNode, formatDate, modelValue, availableModels, populateModelOptions, apiKeyProviderDetails, providerConnectionState, zenConnectionNotice, sharedCodexConnectionNotice, sharedCodexPolicy, aiStatusPath } from "#common/ai-presentation.js";
import { renderAiActivity } from "#common/ai-activity.js";
import { enhanceModelPicker } from "#common/ai-model-picker.js";
import { placeTurnActivity } from "#common/ai-timeline.js";
import { createIconButton } from "./ui.js";
import { permissionMode, permissionValues, permissionSummary, renderPermissionBundles, bindContextSelection } from "#common/ai-permissions.js";

const elements = {
  button: document.querySelector("#ai-assistant-button"),
  panel: document.querySelector("#ai-assistant"),
  close: document.querySelector("#ai-assistant-close"),
  historyButton: document.querySelector("#ai-assistant-history"),
  newButton: document.querySelector("#ai-assistant-new"),
  settingsButton: document.querySelector("#ai-assistant-settings"),
  status: document.querySelector("#ai-assistant-status"),
  model: document.querySelector("#ai-assistant-model"),
  reasoning: document.querySelector("#ai-assistant-reasoning"),
  settingsReasoning: document.querySelector("#ai-settings-reasoning"),
  permissions: document.querySelector("#ai-assistant-permissions"),
  permissionsCopy: document.querySelector("#ai-assistant-permissions-copy"),
  disclosure: document.querySelector("#ai-assistant-disclosure"),
  notice: document.querySelector("#ai-assistant-notice"),
  body: document.querySelector("#ai-assistant-body"),
  messages: document.querySelector("#ai-assistant-messages"),
  activity: document.querySelector("#ai-assistant-activity"),
  form: document.querySelector("#ai-assistant-form"),
  input: document.querySelector("#ai-assistant-input"),
  send: document.querySelector("#ai-assistant-form button[type='submit']"),
  attachment: document.querySelector("#ai-assistant-attachment"),
  settingsDialog: document.querySelector("#ai-settings-dialog"),
  settingsForm: document.querySelector("#ai-settings-form"),
  permissionActions: document.querySelector("#ai-permission-actions"),
  settingsModel: document.querySelector("#ai-settings-model"),
  settingsStatus: document.querySelector("#ai-settings-status"),
  providerList: document.querySelector("#ai-provider-list"),
  historyDialog: document.querySelector("#ai-history-dialog"),
  historyList: document.querySelector("#ai-history-list"),
  deleteDialog: document.querySelector("#ai-delete-chat-dialog"),
  deleteCopy: document.querySelector("#ai-delete-chat-copy"),
  deleteConfirm: document.querySelector("#ai-delete-chat-confirm"),
  proposalDialog: document.querySelector("#ai-proposal-dialog"),
  proposalReview: document.querySelector("#ai-proposal-review"),
  proposalConfirm: document.querySelector("#ai-proposal-confirm"),
};

let chat = null;
let chats = [];
let design = null;
let designSavedHandler = null;
const observedDesignOperations = new Map();

async function refreshChangedDesign(chatId, operations) {
  const completed = operations.filter(operation => operation.status === "succeeded" && ["design_change", "design_history", "migration_apply", "migration_resolve", "migration_reconcile"].includes(operation.kind)).map(operation => operation.id);
  const previous = observedDesignOperations.get(chatId);
  observedDesignOperations.set(chatId, new Set(completed));
  if (previous && completed.some(id => !previous.has(id))) {
    const workspace = chat.workspaceId;
    await designSavedHandler?.(workspace);
    const currentDesign = await requestJson(`/api/v1/schemii/workspaces/${workspace}/design`);
    if (chat?.id === chatId) design = currentDesign;
  }
}

export function setAssistantDesignSavedHandler(handler) {
  designSavedHandler = handler;
}
let settings = null;
let runtime = null;
let models = [];
let pollTimer = null;
let elapsedTimer = null;
let activitySequence = 0;
let activityRun = null;
let resultContextOperationId = null;
let timelineMessages = [];
let timelineProposals = [];
let timelineOperations = [];
let timelineSignature = "";
let pendingDeleteChatId = null;
let providerLogin = null;
let loginTimer = null;
let streamingResponse = null;
let pendingProposal = null;
let pendingProposalBatch = null;
let proposalArmTimer = null;
let proposalReviewOpenedAt = 0;
const modelPickers = [elements.model, elements.settingsModel].filter(Boolean).map(select =>
  enhanceModelPicker(select, { refresh: () => refreshProviderStatus({ refresh: true }) }));

function workspaceId() {
  return new URL(location.href).searchParams.get("workspace");
}

function setNotice(message, tone = "info") {
  elements.notice.textContent = message;
  elements.notice.dataset.tone = tone;
  elements.notice.hidden = !message;
}

function setStatus(value) {
  elements.status.textContent = value;
  elements.status.dataset.status = value.toLowerCase();
}

function selectedModel(select = elements.model) {
  return models.find(item => modelValue(item.providerId, item.id) === select.value) || null;
}

function loadModelOptions(select, selectedProvider, selectedId) {
  populateModelOptions(select, models, selectedProvider, selectedId);
}

function updateContextControls() {
  const provider = chat?.providerId || settings?.defaultProviderId;
  const model = chat?.modelId || settings?.defaultModelId;
  loadModelOptions(elements.model, provider, model);
  elements.permissionsCopy.textContent = permissionSummary(chat?.capabilities || settings?.defaultCapabilities, settings?.permissionActions || []);
  const working = chat?.status === "working";
  elements.model.disabled = working;
  populateScopedReasoningOptions(elements.reasoning, runtime, selectedModel(), chat?.reasoningEffort || settings?.defaultReasoningEffort || "default", working);
  elements.newButton.disabled = !models.length || working;
  elements.settingsButton.disabled = false;
  elements.permissions.disabled = false;
  elements.send.disabled = working || chat?.status === "waiting_approval";
  const capabilities = chat?.capabilities || settings?.defaultCapabilities || {};
  const automatic = (settings?.permissionActions || []).some(({ id }) => permissionMode(capabilities, id) === "automatic");
  elements.disclosure.textContent = automatic
    ? "Automatic actions are enabled. Other permitted actions wait for batch approval. Query rows are temporary."
    : "Actions wait for your approval as a batch. Query rows are temporary.";
  const providerStatus = runtime?.providers?.find(item => item.id === provider);
  const privacyNotice = providerStatus?.privacy || providerStatus?.privacyNotice;
  if (privacyNotice) elements.disclosure.textContent += ` ${privacyNotice}`;
}


function emptyTranscript() {
  const section = document.createElement("section"); section.className = "ai-empty-state";
  const mark = document.createElement("span"); mark.className = "ai-empty-mark"; mark.textContent = "AI";
  const title = document.createElement("strong"); title.textContent = "Ask about the current workspace";
  const copy = document.createElement("p"); copy.textContent = "Explain the design, trace a relationship, review a migration, or prepare a change for review.";
  section.append(mark, title, copy); return section;
}

function messageNode(message) {
  const existing = [...elements.messages.querySelectorAll("[data-message-id]")].find(node => node.dataset.messageId === message.id);
  return createMessageNode(message, { assistantName: "Schemii AI", onError: message => setNotice(message, "error"), existing });
}

function renderAttachment() {
  if (!elements.attachment) return;
  if (!resultContextOperationId) {
    elements.attachment.hidden = true;
    elements.attachment.replaceChildren();
    return;
  }
  const copy = document.createElement("span"); copy.textContent = "Temporary query rows attached to next message";
  const remove = createIconButton({ icon: "close", label: "Remove attached query rows", className: "compact" });
  remove.addEventListener("click", () => { resultContextOperationId = null; renderAttachment(); elements.input.focus(); });
  elements.attachment.replaceChildren(copy, remove); elements.attachment.hidden = false;
}

function turnKey(item, fallback) {
  return item.turnId || fallback;
}

function renderTimeline(messages = timelineMessages, proposals = timelineProposals, operations = timelineOperations) {
  timelineMessages = messages;
  timelineProposals = proposals;
  timelineOperations = operations;
  if (streamingResponse?.text && !messages.some(item => item.turnId === streamingResponse.turnId && item.role === "assistant")) {
    messages = [...messages, { id: `stream:${streamingResponse.turnId}`, turnId: streamingResponse.turnId, role: "assistant", text: streamingResponse.text, sequence: Number.MAX_SAFE_INTEGER, createdAt: streamingResponse.createdAt, transient: true }];
  }
  const nextSignature = JSON.stringify([
    messages.map(item => [item.id, item.sequence, item.role, item.text]),
    proposals.map(item => [item.id, item.turnId, item.revision, item.status]),
    operations.map(item => [item.id, item.proposalId, item.revision, item.status]),
    Boolean(chat?.capabilities?.structuredDataRead),
    chat?.status,
  ]);
  if (nextSignature === timelineSignature) { placeActivity(); return; }
  const wasNearBottom = elements.body.scrollHeight - elements.body.scrollTop - elements.body.clientHeight < 80;
  const loadedResults = new Map([...elements.messages.querySelectorAll("[data-operation-id]")].map(card => [card.dataset.operationId, card.querySelector(".ai-operation__result")]));
  timelineSignature = nextSignature;

  const groups = new Map();
  const ensureGroup = (key, createdAt = "") => {
    if (!groups.has(key)) groups.set(key, { key, createdAt, messages: [], proposals: [], operations: [] });
    const group = groups.get(key);
    if (!group.createdAt || (createdAt && createdAt < group.createdAt)) group.createdAt = createdAt;
    return group;
  };
  for (const message of messages) ensureGroup(turnKey(message, `message:${message.id}`), message.createdAt).messages.push(message);
  for (const proposal of proposals) ensureGroup(turnKey(proposal, `proposal:${proposal.id}`), proposal.createdAt).proposals.push(proposal);
  const proposalsById = new Map(proposals.map(item => [item.id, item]));
  for (const operation of operations.slice(-20)) {
    const proposal = proposalsById.get(operation.proposalId);
    ensureGroup(turnKey(proposal || {}, `operation:${operation.id}`), operation.createdAt).operations.push(operation);
  }
  const orderedGroups = [...groups.values()].sort((left, right) => {
    const leftSequence = Math.min(...left.messages.map(item => item.sequence), Number.MAX_SAFE_INTEGER);
    const rightSequence = Math.min(...right.messages.map(item => item.sequence), Number.MAX_SAFE_INTEGER);
    if (leftSequence !== rightSequence) return leftSequence - rightSequence;
    return String(left.createdAt).localeCompare(String(right.createdAt));
  });
  const nodes = orderedGroups.map(group => {
    const section = document.createElement("section"); section.className = "ai-turn"; section.dataset.turnId = group.key;
    const userMessages = group.messages.filter(item => item.role === "user").sort((a, b) => a.sequence - b.sequence);
    const responseMessages = group.messages.filter(item => item.role !== "user").sort((a, b) => a.sequence - b.sequence);
    section.append(...userMessages.map(messageNode));
    const activitySlot = document.createElement("div"); activitySlot.className = "ai-turn__activity";
    const pending = group.proposals.filter(item => item.status === "pending");
    if (pending.length > 1) {
      const batch = document.createElement("button"); batch.type = "button"; batch.className = "ui-button compact primary";
      batch.dataset.reviewBatch = group.key; batch.textContent = `Review pending batch (${pending.length})`;
      section.append(batch);
    }
    const entries = responseMessages.map(message => ({ createdAt: message.createdAt, node: messageNode(message) }));
    for (const proposal of group.proposals.filter(item => item.status === "pending")) {
      const actions = document.createElement("div"); actions.className = "ai-turn__actions";
      actions.innerHTML = renderProposalCards([proposal]); entries.push({ createdAt: proposal.createdAt, node: actions });
    }
    for (const operation of group.operations) {
      const actions = document.createElement("div"); actions.className = "ai-turn__actions";
      actions.innerHTML = renderOperationCards([operation], group.proposals); entries.push({ createdAt: operation.createdAt, node: actions });
    }
    entries.sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
    section.append(...entries.map(entry => entry.node));
    section.replaceChildren(...placeTurnActivity([...section.children], activitySlot, { turnId: group.key }));
    return section;
  });
  if (chat?.status === "waiting_approval" && !proposals.some(proposal => proposal.status === "pending")) {
    const recovery = document.createElement("div"); recovery.className = "ai-operation";
    const copy = document.createElement("p"); copy.textContent = "The action decision is saved. Continue to resume the answer.";
    const button = document.createElement("button"); button.type = "button"; button.className = "ui-button compact primary"; button.dataset.continueTurn = ""; button.textContent = "Continue answer";
    recovery.append(copy, button);
    if (nodes.length) nodes.at(-1).append(recovery); else nodes.push(recovery);
  }
  elements.messages.replaceChildren(...(nodes.length ? nodes : [emptyTranscript()]));
  for (const card of elements.messages.querySelectorAll("[data-operation-id]")) {
    const operation = operations.find(item => item.id === card.dataset.operationId);
    if (operation?.kind === "app_action" && operation.status === "succeeded") {
      const exported = designExportReceipt(operation.resultSummary);
      if (exported) {
        const download = createIconButton({ icon: "download", label: `Download ${exported.fileName}`, className: "compact" });
        download.addEventListener("click", () => downloadContent(exported.content, exported.fileName, exported.mediaType));
        card.append(download);
      }
      const proposal = proposals.find(item => item.id === operation.proposalId);
      const url = workspaceReceiptUrl(operation.resultSummary, proposal?.details?.operation);
      if (url) {
        const link = document.createElement("a"); link.href = url; link.className = "ui-icon-button compact";
        link.title = "Open workspace"; link.setAttribute("aria-label", "Open workspace"); link.append(createIconElement("workspaces")); card.append(link);
      }
    }
    const handoff = operation?.status === "succeeded" && validateCopyHandoff(operation.resultSummary, chat?.workspaceId);
    if (handoff) {
      const button = createIconButton({ icon: handoff.direction === "upload" ? "upload" : "download", label: handoff.direction === "upload" ? "Upload COPY file" : "Download COPY file", className: "compact" });
      button.addEventListener("click", () => openCopyHandoff(handoff, chat.workspaceId));
      const footer = document.createElement("footer"); footer.append(button); card.insertBefore(footer, card.querySelector(".ai-operation__result"));
    }
    const retained = loadedResults.get(card.dataset.operationId);
    if (retained?.childNodes.length) card.querySelector(".ai-operation__result").replaceWith(retained);
  }
  placeActivity();
  if (wasNearBottom || messages.length <= 2) requestAnimationFrame(() => { elements.body.scrollTop = elements.body.scrollHeight; });
}

function placeActivity() {
  if (!elements.activity || !elements.messages) return;
  const turnId = activityRun?.turnId;
  const groups = [...elements.messages.querySelectorAll(".ai-turn")];
  const group = groups.find(item => item.dataset.turnId === turnId) || groups.at(-1);
  const destination = group?.querySelector(".ai-turn__activity");
  if (destination && elements.activity.parentElement !== destination) destination.append(elements.activity);
}

function beginActivity(turnId = null) {
  activityRun = { turnId, startedAt: Date.now(), state: "working", stages: new Map() }; renderActivity(); placeActivity();
  globalThis.clearInterval(elapsedTimer); elapsedTimer = globalThis.setInterval(renderActivity, 1000);
}

function applyActivityEvent(event) {
  if (event.kind === "status" && event.payload?.stage) {
    if (!activityRun) beginActivity(event.payload?.turnId);
    if (event.payload?.turnId) activityRun.turnId = event.payload.turnId;
    activityRun.stages.set(event.payload.stage, { label: event.payload.label || event.payload.stage, state: event.payload.state || "running" });
    if (event.payload.state === "cancelled") activityRun.state = "cancelled";
  }
  if (event.kind === "error" && event.payload?.tool) {
    if (!activityRun) beginActivity(event.payload?.turnId);
    activityRun.stages.set("actionError", { label: "An action failed; the assistant is continuing", state: "failed" });
  } else if (event.kind === "error") {
    if (!activityRun) beginActivity(event.payload?.turnId);
    if (event.payload?.turnId) activityRun.turnId = event.payload.turnId;
    activityRun.state = "failed";
    activityRun.error = event.payload?.message || "The assistant could not finish this turn.";
  }
  placeActivity();
}

function finishActivity(state = "completed") {
  if (!activityRun) return;
  activityRun.state = state;
  activityRun.finishedAt = Date.now();
  for (const [key, stage] of activityRun.stages) if (stage.state === "running") activityRun.stages.set(key, { ...stage, state });
  renderActivity(); globalThis.clearInterval(elapsedTimer); elapsedTimer = null;
  globalThis.setTimeout(() => { if (activityRun?.state !== "working") { activityRun = null; renderActivity(); } }, 2400);
}

function renderActivity() {
  if (!activityRun) { elements.activity.replaceChildren(); return; }
  const existing = elements.activity.querySelector(".ai-run");
  const card = renderAiActivity(activityRun, { existing });
  if (!existing) elements.activity.replaceChildren(card);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character]);
}

function displayCell(value) {
  if (value === null) return "NULL";
  if (typeof value !== "object") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

const ACTION_PRESENTATION = {
  console_script: { review: "Review draft", confirm: "Open Console", completed: "DRAFT READY", scope: "This prepares SQL in the Console. No SQL is executed and no database objects are changed." },
  design_change: { review: "Review design changes", confirm: "Save to design", completed: "SAVED TO DESIGN", scope: "This saves the workspace design only. A separate migration review and approval are required to change the live database." },
  migration_review: { review: "Review migration", confirm: "Prepare review", completed: "REVIEW READY", scope: "This prepares a migration review. It does not apply a migration or change the live database." },
  data_read: { review: "Approve reads", confirm: "Approve reads", completed: "SUCCEEDED", scope: "" },
  design_history: { review: "Review history change", confirm: "Change design history", completed: "DESIGN HISTORY UPDATED" },
  migration_apply: { review: "Review migration", confirm: "Apply migration", completed: "APPLIED TO DATABASE" },
  migration_resolve: { review: "Review conflict choices", confirm: "Resolve conflicts", completed: "CONFLICTS RESOLVED", scope: "This updates the workspace baseline and design with the choices below. A fresh migration review is required before changing PostgreSQL." },
  migration_reconcile: { review: "Review outcome check", confirm: "Check migration outcome", completed: "OUTCOME CHECKED", scope: "This checks the recorded commit evidence for an uncertain migration. It does not rerun migration SQL. The result may still be uncertain." },
  sql_write: { review: "Review SQL batch", confirm: "Execute and commit", completed: "COMMITTED TO DATABASE" },
};

function actionPresentation(kind) {
  const contract = settings?.actionPolicies?.find(policy => policy.kind === kind);
  return { ...(ACTION_PRESENTATION[kind] || {}), ...(contract || {}) };
}

function proposalWarning(kind) {
  return kind === "migration_resolve"
    ? "Pulling live changes may discard conflicting saved design edits; no live data is deleted."
    : "This action can remove data or database objects. Review every target before applying it.";
}

function renderProposalCards(items) {
  return items.filter(item => item.status === "pending").map(proposal => `
    <article class="ai-proposal${proposal.destructive ? " destructive" : ""}" data-proposal-id="${escapeHtml(proposal.id)}">
      <small>PROPOSED ${escapeHtml(proposal.actionType.replaceAll("_", " "))}</small><strong>${escapeHtml(proposal.summary)}</strong>
      ${proposal.destructive ? `<p class="ai-warning">${escapeHtml(proposalWarning(proposal.actionType))}</p>` : ""}
      ${actionPresentation(proposal.actionType).scope ? `<p>${escapeHtml(actionPresentation(proposal.actionType).scope)}</p>` : ""}
      ${proposal.actionType === "data_read" ? `<details class="ai-read-sql"><summary>Read queries</summary>${(proposal.details?.queries || [{ sql: proposal.details?.sql }]).map(query => `<pre><code>${escapeHtml(query.sql)}</code></pre>`).join("")}</details>` : ""}
      <footer><button class="ui-button compact" type="button" data-dismiss>Dismiss</button><button class="ui-button compact primary" type="button" data-apply>${escapeHtml(actionPresentation(proposal.actionType).review || "Review action")}</button></footer>
    </article>`).join("");
}

function proposalDetailsNode(proposal) {
  const wrapper = document.createElement("div"); wrapper.className = "ai-proposal-review__content";
  const summary = document.createElement("strong"); summary.className = "ai-proposal-review__summary"; summary.textContent = proposal.summary;
  const metadata = document.createElement("dl"); metadata.className = "ai-proposal-review__metadata";
  const addMetadata = (label, value) => {
    const term = document.createElement("dt"); term.textContent = label;
    const description = document.createElement("dd"); description.textContent = value;
    metadata.append(term, description);
  };
  addMetadata("Action", proposal.actionType.replaceAll("_", " "));
  addMetadata("Required permission", proposal.capability.replaceAll("_", " "));
  wrapper.append(summary, metadata);
  const scope = document.createElement("p"); scope.textContent = actionPresentation(proposal.actionType).scope || ""; wrapper.append(scope);
  if (proposal.destructive) {
    const warning = document.createElement("p"); warning.className = "ai-warning";
    warning.textContent = proposalWarning(proposal.actionType); wrapper.append(warning);
  }
  const actions = proposal.details?.type === "batch" ? proposal.details.actions || [] : [proposal.details || {}];
  if (proposal.details?.type === "batch" && proposal.actionType === "design_change") {
    const note = document.createElement("p"); note.textContent = `${actions.length} changes saved together in one design revision. If validation fails, none are saved.`; wrapper.append(note);
  }
  for (const [index, action] of actions.entries()) {
    if (actions.length > 1) {
      const heading = document.createElement("h4"); heading.textContent = `${index + 1}. ${String(action.type || "Change").replaceAll("_", " ")}`; wrapper.append(heading);
    }
    if (proposal.actionType === "migration_resolve") {
      addMetadata("Migration plan", action.plan_id);
      addMetadata("Design revision", action.expected_design_revision);
      const conflicts = Array.isArray(action.reviewContext) ? action.reviewContext : action.reviewContext?.conflicts || [];
      const choices = document.createElement("dl"); choices.className = "ai-proposal-review__metadata";
      for (const resolution of action.resolutions || []) {
        const conflict = conflicts.find(item => item.id === resolution.conflict_id);
        const target = document.createElement("dt"); target.textContent = conflict?.path || resolution.conflict_id;
        const detail = document.createElement("dd");
        const choice = document.createElement("strong"); choice.textContent = resolution.resolution === "pull_live" ? "Pull live database change" : resolution.resolution === "keep_design" ? "Keep workspace design" : resolution.resolution;
        detail.append(choice);
        if (conflict?.reason) { const reason = document.createElement("p"); reason.textContent = conflict.reason; detail.append(reason); }
        const consequence = document.createElement("p"); consequence.textContent = resolution.resolution === "pull_live"
          ? "Adopt the current database definition for this conflict in the workspace."
          : "Retain the intended workspace change for the next migration review.";
        detail.append(consequence); choices.append(target, detail);
      }
      wrapper.append(choices);
    } else if (proposal.actionType === "migration_reconcile") {
      addMetadata("Migration execution", action.execution_id);
      addMetadata("Execution revision", action.expected_execution_revision);
    } else if (["data_read", "console_script"].includes(proposal.actionType) && action.sql) {
      const label = document.createElement("small"); label.textContent = proposal.actionType === "data_read" ? "READ-ONLY SQL" : "SQL CONSOLE DRAFT";
      const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = action.sql; pre.append(code); wrapper.append(label, pre);
    } else if (action.type === "add_table") {
      const heading = document.createElement("small"); heading.textContent = `TABLE ${action.name}`; wrapper.append(heading);
      const list = document.createElement("div"); list.className = "ai-proposal-review__columns";
      for (const column of action.columns || []) {
        const row = document.createElement("div");
        const name = document.createElement("strong"); name.textContent = column.name;
        const type = document.createElement("code"); type.textContent = column.data_type || column.dataType;
        const state = document.createElement("span"); state.textContent = column.nullable === false ? "required" : "nullable";
        row.append(name, type, state); list.append(row);
      }
      wrapper.append(list);
      if (action.keys?.length) {
        const keys = document.createElement("p"); keys.className = "ai-proposal-review__keys";
        keys.textContent = action.keys.map(key => `${key.kind}: ${key.columns.join(", ")}`).join(" · "); wrapper.append(keys);
      }
    } else if (action.type === "put_table_member" || action.type === "put_top_level_object") {
      const object = action.object || {};
      const table = design?.content?.tables?.find(item => item.id === action.table_id);
      const label = document.createElement("strong"); label.textContent = object.name || object.id || action.collection; wrapper.append(label);
      const target = document.createElement("p"); target.textContent = `${action.collection}${action.table_id ? ` · table ${table?.name || action.table_id}` : ""}`; wrapper.append(target);
      const fields = document.createElement("dl"); fields.className = "ai-proposal-review__metadata";
      for (const [key, value] of Object.entries(object)) {
        const term = document.createElement("dt"); term.textContent = key;
        const description = document.createElement("dd");
        description.textContent = ["columns", "columnIds", "includeColumnIds"].includes(key) && Array.isArray(value)
          ? value.map(id => table?.columns?.find(column => column.id === id)?.name || id).join(", ")
          : typeof value === "object" ? JSON.stringify(value) : String(value);
        fields.append(term, description);
      }
      wrapper.append(fields);
    } else if (Object.keys(action).length) {
      const label = document.createElement("small"); label.textContent = "SERVER-VALIDATED ACTION";
      const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = JSON.stringify(action, null, 2); pre.append(code); wrapper.append(label, pre);
    }
  }
  return wrapper;
}

function openProposalReview(proposal) {
  pendingProposalBatch = null;
  pendingProposal = proposal;
  proposalReviewOpenedAt = Date.now();
  globalThis.clearTimeout(proposalArmTimer);
  elements.proposalReview.replaceChildren(proposalDetailsNode(proposal));
  elements.proposalConfirm.textContent = actionPresentation(proposal.actionType).confirm || "Confirm action";
  elements.proposalConfirm.classList.toggle("danger", proposal.destructive);
  elements.proposalConfirm.classList.toggle("primary", !proposal.destructive);
  elements.proposalConfirm.disabled = true;
  openDialog(elements.proposalDialog);
  proposalArmTimer = globalThis.setTimeout(() => {
    if (elements.proposalDialog.open && pendingProposal?.id === proposal.id) elements.proposalConfirm.disabled = false;
  }, 350);
}

function openProposalBatchReview(proposals) {
  closeProposalReview();
  pendingProposalBatch = { chatId: chat.id, items: proposals.map(proposal => ({
    proposalId: proposal.id, expectedChatRevision: chat.revision,
    expectedProposalRevision: proposal.revision, proposalDigest: proposal.digest, confirmed: true,
  })) };
  const note = document.createElement("p"); note.className = "ai-warning";
  note.textContent = "One approval covers exactly the actions below. They execute in order and stop on failure. Separate service actions are not one transaction: completed actions remain applied if a later action fails. Each design batch is saved atomically.";
  elements.proposalReview.replaceChildren(note, ...proposals.map(proposalDetailsNode));
  elements.proposalConfirm.textContent = `Approve ${proposals.length} actions`;
  elements.proposalConfirm.classList.toggle("danger", proposals.some(proposal => proposal.destructive));
  elements.proposalConfirm.classList.toggle("primary", !proposals.some(proposal => proposal.destructive));
  elements.proposalConfirm.disabled = true;
  proposalReviewOpenedAt = Date.now();
  openDialog(elements.proposalDialog);
  proposalArmTimer = globalThis.setTimeout(() => {
    if (elements.proposalDialog.open && pendingProposalBatch) elements.proposalConfirm.disabled = false;
  }, 350);
}

function closeProposalReview() {
  globalThis.clearTimeout(proposalArmTimer);
  proposalArmTimer = null;
  proposalReviewOpenedAt = 0;
  pendingProposal = null;
  pendingProposalBatch = null;
  elements.proposalConfirm.disabled = true;
  closeDialog(elements.proposalDialog);
}

function renderOperationCards(items, proposalItems) {
  const summaries = new Map(proposalItems.map(item => [item.id, item.summary]));
  return [...items].sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt))).map(operation => {
    const summary = summaries.get(operation.proposalId) || operation.kind.replaceAll("_", " "); let action = "";
    if (operation.kind === "data_read" && operation.status === "succeeded") action = `<button class="ui-button compact" type="button" data-show-result>Show rows</button>`;
    else if (operation.kind === "console_script" && operation.status === "succeeded") action = "<button class=\"ui-button compact\" type=\"button\" data-open-console>Open Console</button>";
    const presentation = actionPresentation(operation.kind);
    const status = operation.status === "succeeded" ? presentation?.completed || "SUCCEEDED" : operation.status.toUpperCase();
    const scope = operation.status === "succeeded" ? presentation?.scope : "";
    const receipt = operation.resultSummary;
    const recovery = operation.kind === "migration_reconcile" && receipt ? `<dl class="ai-proposal-review__metadata"><dt>Database transaction</dt><dd>${escapeHtml((receipt.commitOutcome || "not confirmed").replaceAll("_", " "))}</dd><dt>Workspace synchronization</dt><dd>${escapeHtml(receipt.syncStatus || "not confirmed")}</dd></dl>${receipt.reconcileRequired ? "<p class=\"ai-warning\">Further reconciliation is required; do not assume the migration committed.</p>" : ""}` : "";
    return `<article class="ai-operation${operation.status === "failed" ? " failed" : ""}" data-operation-id="${escapeHtml(operation.id)}" data-proposal-id="${escapeHtml(operation.proposalId)}"><small>${escapeHtml(status)}</small><strong>${escapeHtml(summary)}</strong>${scope ? `<p>${escapeHtml(scope)}</p>` : ""}${recovery}${operation.errorMessage ? `<p>${escapeHtml(operation.errorMessage)}</p>` : ""}${action ? `<footer>${action}</footer>` : ""}<div class="ai-operation__result"></div></article>`;
  }).join("");
}

function defaultModel() {
  return models.find(item => item.providerId === settings?.defaultProviderId && item.id === settings?.defaultModelId) || models[0] || null;
}

function defaultCapabilities() {
  return { liveCatalog: false, structuredDataRead: false, monitorQueries: false, ...permissionValues({}, settings?.permissionActions || []), ...(settings?.defaultCapabilities || {}) };
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function clearProviderLogin() {
  globalThis.clearTimeout(loginTimer);
  providerLogin = null;
}

async function pollProviderLogin(login) {
  try {
    const state = await requestJson(`/api/v1/ai/prototype/logins/${encodeURIComponent(login.id)}`);
    if (providerLogin !== login) return;
    if (state.status === "succeeded") {
      clearProviderLogin(); await refreshProviderStatus();
      elements.settingsStatus.textContent = "Codex connected to your account."; return;
    }
    if (state.status !== "pending") throw new Error(state.message || "Sign-in expired. Connect again.");
    Object.assign(login, { userCode: state.userCode, verificationUrl: state.verificationUrl });
    renderProviders();
    loginTimer = globalThis.setTimeout(() => void pollProviderLogin(login), 2000);
  } catch (error) {
    if (providerLogin !== login) return;
    clearProviderLogin(); renderProviders(); elements.settingsStatus.textContent = error.message;
  }
}

function codexConnection() {
  const section = document.createElement("div"); section.className = "ai-auth-form";
  if (providerLogin) {
    const login = providerLogin;
    const copy = document.createElement("p");
    copy.textContent = login.userCode ? "Enter this code at OpenAI to connect your account:" : "Preparing device sign-in…";
    section.append(copy);
    if (login.userCode) {
      const code = document.createElement("strong"); code.textContent = login.userCode; section.append(code);
      const url = safeExternalUrl(login.verificationUrl);
      if (url) { const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "Continue at OpenAI"; section.append(link); }
    }
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "ui-button compact"; cancel.textContent = "Cancel sign-in";
    cancel.disabled = !login.id;
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try {
        await requestJson(`/api/v1/ai/prototype/logins/${encodeURIComponent(login.id)}`, { method: "DELETE" });
        if (providerLogin === login) { clearProviderLogin(); renderProviders(); elements.settingsStatus.textContent = "Sign-in cancelled."; }
      } catch (error) { elements.settingsStatus.textContent = error.message; cancel.disabled = false; }
    });
    section.append(cancel);
  } else {
    const connect = document.createElement("button"); connect.type = "button"; connect.className = "ui-button compact primary"; connect.textContent = "Connect Codex";
    connect.addEventListener("click", async () => {
      const login = {}; providerLogin = login; renderProviders(); elements.settingsStatus.textContent = "Preparing device sign-in…";
      try {
        const result = await requestJson("/api/v1/ai/prototype/login", { method: "POST" });
        if (providerLogin !== login) return;
        login.id = result.id; renderProviders(); void pollProviderLogin(login);
      } catch (error) { clearProviderLogin(); renderProviders(); elements.settingsStatus.textContent = error.message; }
    });
    section.append(connect);
  }
  return section;
}

function apiKeyConnection(provider) {
  const form = document.createElement("form"); form.className = "ai-auth-form";
  const details = apiKeyProviderDetails[provider.id];
  const label = document.createElement("label"); const copy = document.createElement("span"); copy.textContent = details.label;
  const key = document.createElement("input"); key.type = "password"; key.required = true; key.autocomplete = "off"; label.append(copy, key);
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "ui-button compact primary"; submit.textContent = "Connect";
  form.append(label, submit);
  form.addEventListener("submit", async event => {
    event.preventDefault(); submit.disabled = true;
    try {
      await requestJson(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: "POST", body: { apiKey: key.value } });
      key.value = ""; await refreshProviderStatus(); elements.settingsStatus.textContent = `${provider.name} connected.`;
    } catch (error) { elements.settingsStatus.textContent = error.message; submit.disabled = false; }
  });
  return form;
}

function renderProviders() {
  if (!elements.providerList) return;
  const providers = runtime?.providers || [];
  if (!providers.length) {
    const empty = document.createElement("p"); empty.className = "ai-provider-empty"; empty.textContent = runtime?.message || "No model providers are available."; elements.providerList.replaceChildren(empty); return;
  }
  elements.providerList.replaceChildren(...providers.map(provider => {
    const card = document.createElement("details"); card.className = "ai-provider-card";
    card.open = (providerLogin && provider.id === "openai-codex") || provider.id === (chat?.providerId || settings?.defaultProviderId);
    const heading = document.createElement("summary"); heading.className = "ai-provider-heading";
    const name = document.createElement("strong"); name.textContent = provider.name;
    const state = document.createElement("span"); state.className = `ai-provider-state${provider.available ? " connected" : ""}`;
    state.textContent = providerConnectionState(provider);
    const caret = document.createElement("span"); caret.className = "ai-provider-caret"; caret.textContent = "⌄";
    heading.append(name, state, caret); card.append(heading);
    if (provider.privacy || provider.privacyNotice) {
      const privacy = document.createElement("p"); privacy.className = "ai-provider-empty"; privacy.textContent = provider.privacy || provider.privacyNotice; card.append(privacy);
    }
    if (provider.id === "opencode") {
      const notice = document.createElement("p"); notice.className = "ai-provider-empty"; notice.textContent = zenConnectionNotice; card.append(notice);
    } else if (provider.id === "instance-codex") {
      const notice = document.createElement("p"); notice.className = "ai-provider-empty"; notice.textContent = `${sharedCodexConnectionNotice} ${sharedCodexPolicy(provider)}`; card.append(notice);
    } else if (provider.authenticated && ["openai-codex", "openai"].includes(provider.id)) {
      const disconnect = document.createElement("button"); disconnect.type = "button"; disconnect.className = "ui-button compact"; disconnect.textContent = "Disconnect";
      disconnect.addEventListener("click", async () => {
        disconnect.disabled = true;
        try {
          await requestJson(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
          await refreshProviderStatus(); elements.settingsStatus.textContent = `${provider.name} disconnected.`;
        } catch (error) { elements.settingsStatus.textContent = error.message; disconnect.disabled = false; }
      }); card.append(disconnect);
    } else if (provider.id === "openai-codex") card.append(codexConnection());
    else if (apiKeyProviderDetails[provider.id]) card.append(apiKeyConnection(provider));
    return card;
  }));
}

async function refreshProviderStatus({ refresh = false } = {}) {
  const settingsSelection = elements.settingsModel.value.split("\u0000");
  runtime = await requestJson(aiStatusPath("schemii", workspaceId(), refresh), { timeoutMs: refresh ? 20_000 : 10_000 }); models = availableModels(runtime);
  loadModelOptions(elements.model, chat?.providerId || settings?.defaultProviderId, chat?.modelId || settings?.defaultModelId);
  loadModelOptions(elements.settingsModel, settingsSelection[0] || chat?.providerId || settings?.defaultProviderId, settingsSelection[1] || chat?.modelId || settings?.defaultModelId);
  populateScopedReasoningOptions(elements.settingsReasoning, runtime, selectedModel(elements.settingsModel), elements.settingsReasoning.value || chat?.reasoningEffort || settings?.defaultReasoningEffort || "default", chat?.status === "working");
  renderProviders(); updateContextControls();
  modelPickers.forEach(picker => picker.sync());
  if (refresh && (runtime.healthy === false || runtime.providers?.some(provider => provider.catalogError))) {
    throw new Error(runtime.message || "Could not check available models. Try again.");
  }
}

async function createConversation({ model = defaultModel(), capabilities = defaultCapabilities(), reasoningEffort = chat?.reasoningEffort || settings?.defaultReasoningEffort || "default", title = "New conversation" } = {}) {
  if (!model) throw new Error("No AI model is configured for this deployment.");
  chat = await requestJson(`/api/v1/schemii/workspaces/${workspaceId()}/ai/chats`, { method: "POST", body: { providerId: model.providerId, modelId: model.id, title, capabilities, reasoningEffort: reasoningForSelection(runtime, model, reasoningEffort) } });
  chats = [chat, ...chats.filter(item => item.id !== chat.id)];
  activitySequence = 0; timelineSignature = ""; resultContextOperationId = null;
  updateContextControls(); renderAttachment(); renderTimeline([], [], []); return chat;
}

async function loadAssistant() {
  const workspace = workspaceId(); if (!workspace) throw new Error("Open a workspace first.");
  let list;
  [design, settings, runtime, list] = await Promise.all([
    requestJson(`/api/v1/schemii/workspaces/${workspace}/design`), requestJson("/api/v1/schemii/ai/settings"), requestJson(aiStatusPath("schemii", workspace)),
    requestJson(`/api/v1/schemii/ai/chats?workspaceId=${encodeURIComponent(workspace)}`),
  ]);
  models = availableModels(runtime);
  chats = list.chats; chat = chats[0] || null;
  if (!chat && models.length) await createConversation(); updateContextControls();
}

function applyActivityPage(activity, currentChat, { initial = false } = {}) {
  activitySequence = activity.nextSequence;
  let activityEvents = activity.events;
  if (initial && currentChat.status !== "working") activityEvents = [];
  else if (initial) {
    const currentTurnStart = activityEvents.findLastIndex(event => (
      event.kind === "status" && event.payload?.stage === "context" && event.payload?.state === "running"
    ));
    if (currentTurnStart >= 0) activityEvents = activityEvents.slice(currentTurnStart);
  }
  for (const event of activityEvents) applyActivityEvent(event);
  if (activityRun && currentChat.status === "working") {
    activityRun.state = "working";
    delete activityRun.finishedAt;
  }
}

let progressPollFailures = 0;
function scheduleProgressPoll() {
  globalThis.clearTimeout(pollTimer);
  const delay = progressPollFailures ? Math.min(10000, 1000 * 2 ** progressPollFailures) : 700;
  pollTimer = globalThis.setTimeout(() => void pollProgress().catch(error => {
    showError(error);
    updateContextControls();
    progressPollFailures += 1;
    if (chat?.status === "working" && elements.panel.classList.contains("open")) scheduleProgressPoll();
  }), delay);
}

async function pollProgress() {
  if (!chat) return;
  const chatId = chat.id;
  const [activity, currentChat, stream] = await Promise.all([
    requestJson(`/api/v1/schemii/ai/chats/${chatId}/activity?after=${activitySequence}`),
    requestJson(`/api/v1/schemii/ai/chats/${chatId}`),
    requestJson(`/api/v1/schemii/ai/chats/${chatId}/stream`),
  ]);
  if (chat?.id !== chatId) return;
  progressPollFailures = 0;
  chat = currentChat; applyActivityPage(activity, currentChat); updateContextControls();
  if (chat.status === "working") {
    streamingResponse = stream?.turnId && stream.text ? { ...stream, createdAt: streamingResponse?.createdAt || new Date().toISOString() } : null;
    if (activity.events.some(event => ["query", "analysis"].includes(event.payload?.stage))) {
      const [proposalList, operationList] = await Promise.all([
        requestJson(`/api/v1/schemii/ai/chats/${chatId}/proposals`),
        requestJson(`/api/v1/schemii/ai/chats/${chatId}/operations`),
      ]);
      if (chat?.id !== chatId) return;
      timelineProposals = proposalList.proposals; timelineOperations = operationList.operations;
    }
    renderTimeline();
    setStatus("Working"); if (!activityRun) beginActivity(); scheduleProgressPoll(); return;
  }
  if (chat.status === "waiting_approval") { setStatus("Awaiting approval"); finishActivity("waiting_approval"); }
  else if (chat.status === "failed") { setStatus("Needs attention"); finishActivity("failed"); }
  else { setStatus("Ready"); if (["working", "waiting_approval"].includes(activityRun?.state)) finishActivity("completed"); else if (activityRun?.state === "cancelled") finishActivity("cancelled"); }
  await refresh({ includeChat: false });
  if (chat?.id === chatId && chat.status === "failed") await refreshProviderStatus();
}

async function refresh({ includeChat = true } = {}) {
  if (!chat) return;
  const chatId = chat.id;
  globalThis.clearTimeout(pollTimer); pollTimer = null;
  const initialActivityLoad = activitySequence === 0;
  // Observe status before loading its transcript: terminal status guarantees the
  // final message is committed, while working status keeps polling active.
  const currentChat = includeChat ? await requestJson(`/api/v1/schemii/ai/chats/${chatId}`) : chat;
  if (chat?.id !== chatId) return;
  chat = currentChat; updateContextControls();
  if (chat.status === "working") scheduleProgressPoll();
  const [history, proposalList, operationList, activity, transient] = await Promise.all([
    requestJson(`/api/v1/schemii/ai/chats/${chatId}/messages`), requestJson(`/api/v1/schemii/ai/chats/${chatId}/proposals`),
    requestJson(`/api/v1/schemii/ai/chats/${chatId}/operations`), requestJson(`/api/v1/schemii/ai/chats/${chatId}/activity?after=${activitySequence}`),
    requestJson(`/api/v1/schemii/ai/chats/${chatId}/transient-responses`),
  ]);
  if (chat?.id !== chatId) return;
  chat = currentChat;
  updateContextControls();
  await refreshChangedDesign(chatId, operationList.operations);
  if (chat?.id !== chatId) return;
  streamingResponse = null;
  const transientByTurn = new Map((transient.responses || []).map(item => [item.turnId, item]));
  const renderedMessages = history.messages.map(message => {
    const response = message.role === "assistant" ? transientByTurn.get(message.turnId) : null;
    return response ? { ...message, text: response.text, transient: true } : message;
  });
  renderTimeline(renderedMessages, proposalList.proposals, operationList.operations);
  applyActivityPage(activity, currentChat, { initial: initialActivityLoad });
  updateContextControls();
  if (chat.status === "working") {
    setStatus("Working"); if (!activityRun) beginActivity(); scheduleProgressPoll();
  } else if (chat.status === "waiting_approval") { setStatus("Awaiting approval"); finishActivity("waiting_approval"); }
  else if (chat.status === "failed") { setStatus("Needs attention"); finishActivity("failed"); }
  else { setStatus("Ready"); if (["working", "waiting_approval"].includes(activityRun?.state)) finishActivity("completed"); else if (activityRun?.state === "cancelled") finishActivity("cancelled"); }
}

function showError(error) {
  setNotice(error.message || "The assistant request failed.", "error"); setStatus("Error");
}

async function openAssistant() {
  elements.panel.classList.add("open"); elements.panel.removeAttribute("inert"); elements.panel.setAttribute("aria-hidden", "false"); elements.button.setAttribute("aria-expanded", "true");
  setStatus("Loading"); setNotice("Loading the current workspace context…");
  try {
    await loadAssistant();
    if (!runtime?.healthy) setNotice(runtime?.message || "The AI runtime is not currently available.", "error");
    else setNotice("Query results can be analyzed in this conversation. Rows are temporary and are never saved by Schemii.");
    await refresh({ includeChat: false }); elements.input.focus();
  } catch (error) { showError(error); }
}

function closeAssistant() {
  if (elements.panel.contains(document.activeElement)) elements.button.focus({ preventScroll: true });
  elements.panel.classList.remove("open"); elements.panel.setAttribute("aria-hidden", "true"); elements.panel.setAttribute("inert", ""); elements.button.setAttribute("aria-expanded", "false");
  globalThis.clearTimeout(pollTimer); pollTimer = null;
}

function openDialog(dialog) { if (!dialog.open) dialog.showModal(); }
function closeDialog(dialog) { if (dialog.open) dialog.close(); }

let permissionEditor = null;
let settingsSaving = false;
const refreshContextSelection = bindContextSelection(elements.settingsForm.querySelector("[data-context-permissions]"));

function fillSettings() {
  loadModelOptions(elements.settingsModel, chat?.providerId || settings?.defaultProviderId, chat?.modelId || settings?.defaultModelId);
  elements.settingsModel.disabled = chat?.status === "working";
  populateScopedReasoningOptions(elements.settingsReasoning, runtime, selectedModel(elements.settingsModel), chat?.reasoningEffort || settings?.defaultReasoningEffort || "default", chat?.status === "working");
  const capabilities = chat?.capabilities || defaultCapabilities();
  permissionEditor = renderPermissionBundles(elements.permissionActions, settings?.permissionActions || [], capabilities.actionModes);
  for (const name of ["liveCatalog", "structuredDataRead", "monitorQueries"]) elements.settingsForm.elements[name].checked = Boolean(capabilities[name]);
  refreshContextSelection();
  elements.settingsStatus.textContent = "Permission changes apply to this conversation and become the default for new ones.";
  renderProviders();
}

async function openSettings() {
  let providerError = null;
  try {
    if (!settings || !runtime) await loadAssistant();
    else await refreshProviderStatus();
  } catch (error) {
    if (!settings || !runtime) { showError(error); return; }
    providerError = error;
  }
  fillSettings(); openDialog(elements.settingsDialog);
  if (providerError) elements.settingsStatus.textContent = `Provider status could not be refreshed: ${providerError.message}`;
}

function renderHistory() {
  if (!chats.length) {
    const empty = document.createElement("p"); empty.className = "ai-history-empty"; empty.textContent = "No saved conversations yet."; elements.historyList.replaceChildren(empty); return;
  }
  elements.historyList.replaceChildren(...chats.map(item => {
    const row = document.createElement("article"); row.className = `ai-history-item${item.id === chat?.id ? " current" : ""}`; row.dataset.chatId = item.id;
    const open = document.createElement("button"); open.type = "button"; open.className = "ai-history-open";
    const title = document.createElement("strong"); title.textContent = item.title;
    const meta = document.createElement("span"); meta.textContent = `${item.modelId} · ${formatDate(item.updatedAt)}`; open.append(title, meta);
    const current = document.createElement("span"); current.className = "ai-history-current"; current.textContent = item.id === chat?.id ? "CURRENT" : "";
    const rename = createIconButton({ icon: "edit", label: `Rename ${item.title}`, className: "compact" }); rename.dataset.rename = "";
    const remove = createIconButton({ icon: "delete", label: `Delete ${item.title}`, className: "compact danger" }); remove.dataset.delete = "";
    row.append(open, current, rename, remove); return row;
  }));
}

function beginHistoryRename(row, item) {
  const form = document.createElement("form"); form.className = "ai-history-rename-form";
  const input = document.createElement("input"); input.type = "text"; input.value = item.title; input.maxLength = 80; input.required = true; input.setAttribute("aria-label", "Conversation name");
  const save = createIconButton({ icon: "check", label: "Save conversation name", className: "compact primary" }); save.type = "submit";
  const cancel = createIconButton({ icon: "close", label: "Cancel rename", className: "compact" });
  form.append(input, save, cancel); row.replaceChildren(form); input.focus(); input.select();
  cancel.addEventListener("click", renderHistory);
  form.addEventListener("submit", async event => {
    event.preventDefault(); const title = input.value.trim(); if (!title || title === item.title) { renderHistory(); return; }
    save.disabled = true;
    try {
      const updated = await requestJson(`/api/v1/schemii/ai/chats/${item.id}`, { method: "PATCH", body: { expectedRevision: item.revision, title } });
      chats = chats.map(value => value.id === item.id ? updated : value); if (chat?.id === item.id) chat = updated; renderHistory();
    } catch (error) { save.disabled = false; input.setCustomValidity(error.message); input.reportValidity(); }
  });
}

function requestConversationDelete(item) {
  pendingDeleteChatId = item.id;
  elements.deleteCopy.textContent = `“${item.title}” and its saved messages will be removed. This cannot be undone.`;
  closeDialog(elements.historyDialog);
  openDialog(elements.deleteDialog);
}

async function openHistory() {
  try {
    const list = await requestJson(`/api/v1/schemii/ai/chats?workspaceId=${encodeURIComponent(workspaceId())}`); chats = list.chats; renderHistory(); openDialog(elements.historyDialog);
  } catch (error) { showError(error); }
}

async function switchConversation(id) {
  if (id === chat?.id) { closeDialog(elements.historyDialog); return; }
  chat = chats.find(item => item.id === id) || await requestJson(`/api/v1/schemii/ai/chats/${id}`);
  activitySequence = 0; activityRun = null; timelineSignature = ""; resultContextOperationId = null;
  elements.activity.replaceChildren(); renderAttachment(); updateContextControls(); closeDialog(elements.historyDialog); setNotice(`Opened “${chat.title}”.`); await refresh();
}

async function showQueryResult(operationId) {
  const card = elements.messages.querySelector(`[data-operation-id="${operationId}"]`); const target = card?.querySelector(".ai-operation__result"); if (!target) return;
  const chatId = chat.id;
  target.innerHTML = "<p>Loading temporary rows…</p>";
  try {
    const page = await requestJson(`/api/v1/schemii/ai/chats/${chatId}/operations/${operationId}/query-result`);
    if (chat?.id !== chatId) return;
    target.innerHTML = (page.results || [page]).map((result, index) => {
      const rows = result.rows || []; const columns = result.columns || [];
      const title = `<strong>${escapeHtml(result.label || `Query ${index + 1}`)}</strong>${result.executedAt ? `<time>${escapeHtml(formatDate(result.executedAt))}</time>` : ""}`;
      if (result.released) return `<section class="ai-result-set">${title}<p class="ai-freshness">${escapeHtml(result.message || "These temporary rows have been released.")}</p><p class="ai-result-note">Ask the assistant to rerun this query. The new result may differ.</p></section>`;
      if (result.errorMessage) return `<section class="ai-result-set">${title}<p class="ai-warning">${escapeHtml(result.errorMessage)}</p></section>`;
      const table = rows.length ? `<div class="ai-query-result" tabindex="0" aria-label="Scrollable query results"><table><thead><tr>${columns.map(column => `<th scope="col">${escapeHtml(column.name)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(value => `<td>${escapeHtml(displayCell(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "<p class=\"ai-result-note\">Query returned no rows.</p>";
      return `<section class="ai-result-set">${title}${result.freshnessNotice ? `<p class="ai-freshness">${escapeHtml(result.freshnessNotice)}</p>` : ""}${table}<p class="ai-result-note">${rows.length} temporary row${rows.length === 1 ? "" : "s"} shown${result.sampled || result.truncated ? " · partial result" : ""}. Rows are not saved by Schemii.</p></section>`;
    }).join("") || "<p class=\"ai-result-note\">No query results are available.</p>";
  } catch (error) { target.innerHTML = `<p class="ai-warning">${escapeHtml(error.message)}</p>`; }
}

function openConsole(sql) {
  const url = new URL(location.href); url.searchParams.set("workspace", workspaceId()); url.searchParams.set("layer", "sql");
  for (const key of ["table", "tableId", "view", "viewId", "viewKind"]) url.searchParams.delete(key);
  url.hash = new URLSearchParams({ sql }).toString(); location.href = url.toString();
}

elements.button?.addEventListener("click", () => void openAssistant());
elements.close?.addEventListener("click", closeAssistant);
elements.settingsButton?.addEventListener("click", () => void openSettings());
elements.permissions?.addEventListener("click", () => void openSettings());
elements.historyButton?.addEventListener("click", () => void openHistory());
elements.newButton?.addEventListener("click", async () => {
  try { await createConversation({ model: selectedModel() || defaultModel(), capabilities: chat?.capabilities || defaultCapabilities() }); setNotice("Started a new conversation. Your previous conversation remains in history."); await refresh(); elements.input.focus(); } catch (error) { showError(error); }
});

elements.model?.addEventListener("change", async () => {
  const model = selectedModel(); if (!model || (model.providerId === chat?.providerId && model.id === chat?.modelId)) return;
  elements.model.disabled = true;
  try {
    if (!chat) await createConversation({ model, capabilities: defaultCapabilities() });
    else {
      const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/preferences`, { method: "PUT", body: { expectedSettingsRevision: settings.revision, expectedChatRevision: chat.revision, providerId: model.providerId, modelId: model.id, capabilities: chat.capabilities, reasoningEffort: reasoningForSelection(runtime, model, chat.reasoningEffort) } });
      settings = result.settings; chat = result.chat;
    }
    setNotice(`Switched to ${model.name}. Conversation retained.`); await refresh(); elements.input.focus();
  }
  catch (error) { showError(error); updateContextControls(); }
});

elements.settingsModel?.addEventListener("change", () => {
  const model = selectedModel(elements.settingsModel);
  populateScopedReasoningOptions(elements.settingsReasoning, runtime, model, reasoningForSelection(runtime, model, elements.settingsReasoning.value));
});
elements.reasoning?.addEventListener("change", async () => {
  const reasoningEffort = elements.reasoning.value;
  const model = selectedModel(); if (!model) return;
  if (managedProviderForModel(runtime, model)) return;
  elements.reasoning.disabled = true;
  try {
    if (!chat) await createConversation({ model, reasoningEffort });
    else {
      const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/preferences`, { method: "PUT", body: { expectedSettingsRevision: settings.revision, expectedChatRevision: chat.revision, providerId: model.providerId, modelId: model.id, capabilities: chat.capabilities, reasoningEffort } });
      settings = result.settings; chat = result.chat;
    }
    setNotice("Reasoning level saved for your next message."); await refresh();
  } catch (error) { showError(error); }
  finally { updateContextControls(); }
});

elements.input?.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); elements.form.requestSubmit(); }
});

elements.form?.addEventListener("submit", async event => {
  event.preventDefault(); const text = elements.input.value.trim(); if (!text || !chat || ["working", "waiting_approval"].includes(chat.status)) return;
  const provider = runtime?.providers?.find(item => item.id === chat.providerId);
  const requiresAcknowledgment = chat.providerId === "opencode";
  if (requiresAcknowledgment && !window.confirm(`${provider?.privacy || provider?.privacyNotice || "OpenCode Zen free models may use prompts and responses for training. Do not send sensitive, private, or confidential data."}\n\nSend this message and the conversation context to this provider?`)) return;
  elements.input.value = ""; elements.send.disabled = true; beginActivity(); setStatus("Working"); setNotice("");
  try {
    design = await requestJson(`/api/v1/schemii/workspaces/${workspaceId()}/design`);
    const turn = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/messages`, { method: "POST", body: { text, expectedChatRevision: chat.revision, expectedDesignRevision: design.revision, resultContextOperationId, ...(requiresAcknowledgment ? { acknowledgeProviderDataPolicy: true } : {}) } });
    if (activityRun) { activityRun.turnId = turn.id; placeActivity(); }
    resultContextOperationId = null; renderAttachment(); await refresh();
  } catch (error) { if (!elements.input.value) elements.input.value = text; finishActivity("failed"); showError(error); }
  finally { elements.send.disabled = ["working", "waiting_approval"].includes(chat?.status); }
});

elements.settingsForm?.addEventListener("submit", async event => {
  event.preventDefault(); if (settingsSaving) return; const model = selectedModel(elements.settingsModel); if (!model) return;
  const capabilities = {
    ...Object.fromEntries(["liveCatalog", "structuredDataRead", "monitorQueries"].map(name => [name, elements.settingsForm.elements[name].checked])),
    ...permissionValues(Object.fromEntries([...elements.permissionActions.querySelectorAll("select[data-permission-action]")].map(control => [control.dataset.permissionAction, control.value])), settings?.permissionActions || []),
  };
  settingsSaving = true;
  const controls = [...elements.settingsDialog.querySelectorAll("input, select, button")];
  const disabled = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  permissionEditor?.setBusy(true);
  elements.settingsStatus.textContent = "Saving…";
  try {
    const modelChanged = model.providerId !== chat.providerId || model.id !== chat.modelId;
    const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/preferences`, { method: "PUT", body: { expectedSettingsRevision: settings.revision, expectedChatRevision: chat.revision, providerId: model.providerId, modelId: model.id, capabilities, reasoningEffort: reasoningForSelection(runtime, model, elements.settingsReasoning.value) } });
    settings = result.settings; chat = result.chat;
    updateContextControls(); closeDialog(elements.settingsDialog); setNotice(modelChanged ? `Switched to ${model.name}. Conversation retained; permissions saved.` : "Assistant permissions saved."); await refresh();
  } catch (error) { elements.settingsStatus.textContent = error.message; }
  finally { settingsSaving = false; controls.forEach((control, index) => { control.disabled = disabled[index]; }); permissionEditor?.setBusy(false); }
});

for (const control of document.querySelectorAll("[data-ai-settings-close]")) control.addEventListener("click", () => closeDialog(elements.settingsDialog));
for (const control of document.querySelectorAll("[data-ai-history-close]")) control.addEventListener("click", () => closeDialog(elements.historyDialog));
for (const control of document.querySelectorAll("[data-ai-delete-cancel]")) control.addEventListener("click", () => { pendingDeleteChatId = null; closeDialog(elements.deleteDialog); renderHistory(); openDialog(elements.historyDialog); });
for (const control of document.querySelectorAll("[data-ai-proposal-cancel]")) control.addEventListener("click", closeProposalReview);

elements.proposalConfirm?.addEventListener("click", async () => {
  const proposal = pendingProposal; const batch = pendingProposalBatch;
  if ((!proposal && !batch) || !chat) return;
  if (Date.now() - proposalReviewOpenedAt < 350) return;
  elements.proposalConfirm.disabled = true;
  try {
    if (batch) {
      const result = await requestJson(`/api/v1/schemii/ai/chats/${batch.chatId}/proposal-batch/executions`, { method: "POST", body: { items: batch.items } });
      closeProposalReview();
      setNotice(result.message || (result.status === "completed" ? "Approved batch completed." : "Batch stopped. Review completed and unattempted actions."), result.status === "completed" ? "" : "error");
      await refresh();
      return;
    }
    const operation = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals/${proposal.id}/executions`, { method: "POST", body: { expectedChatRevision: chat.revision, expectedProposalRevision: proposal.revision, proposalDigest: proposal.digest, confirmed: true } });
    closeProposalReview();
    if (operation.kind === "console_script" && operation.status === "succeeded") openConsole(proposal.details.sql);
    await refresh(); if (operation.kind === "data_read") await showQueryResult(operation.id);
  } catch (error) { showError(error); }
  finally { if (elements.proposalDialog.open && (pendingProposal || pendingProposalBatch)) elements.proposalConfirm.disabled = false; }
});

elements.deleteConfirm?.addEventListener("click", async () => {
  const item = chats.find(value => value.id === pendingDeleteChatId); if (!item) return;
  elements.deleteConfirm.disabled = true;
  try {
    await requestJson(`/api/v1/schemii/ai/chats/${item.id}`, { method: "DELETE" }); chats = chats.filter(value => value.id !== item.id);
    if (chat?.id === item.id) { chat = chats[0] || null; if (!chat) await createConversation(); await refresh(); }
    pendingDeleteChatId = null; closeDialog(elements.deleteDialog); renderHistory(); openDialog(elements.historyDialog); updateContextControls();
  } catch (error) { showError(error); }
  finally { elements.deleteConfirm.disabled = false; }
});

elements.historyList?.addEventListener("click", async event => {
  const row = event.target.closest("[data-chat-id]"); if (!row) return;
  const item = chats.find(value => value.id === row.dataset.chatId); if (!item) return;
  try {
    if (event.target.closest("[data-delete]")) {
      requestConversationDelete(item); return;
    }
    if (event.target.closest("[data-rename]")) {
      beginHistoryRename(row, item); return;
    }
    if (event.target.closest(".ai-history-open")) await switchConversation(item.id);
  } catch (error) { showError(error); }
});

elements.body?.addEventListener("click", async event => {
  const batchReview = event.target.closest("[data-review-batch]");
  if (batchReview) {
    try {
      const list = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`);
      const proposals = list.proposals.filter(item => item.status === "pending" && item.turnId === batchReview.dataset.reviewBatch);
      if (proposals.length < 2) { await refresh(); return; }
      openProposalBatchReview(proposals);
    } catch (error) { showError(error); }
    return;
  }
  const continueTurn = event.target.closest("[data-continue-turn]");
  if (continueTurn) {
    continueTurn.disabled = true;
    try {
      const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/continue`, { method: "POST" });
      await refresh();
      if (!result.resumed) setNotice("The answer has not resumed. Check its current status and try again.");
    } catch (error) { showError(error); continueTurn.disabled = false; }
    return;
  }
  const proposalCard = event.target.closest("[data-proposal-id]"); const operationCard = event.target.closest("[data-operation-id]");
  const cancelTurn = event.target.closest("[data-cancel-turn]");
  if (cancelTurn) {
    cancelTurn.disabled = true;
    try { await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/turns/${cancelTurn.dataset.cancelTurn}/cancel`, { method: "POST" }); await refresh(); }
    catch (error) { showError(error); cancelTurn.disabled = false; }
    return;
  }
  if (operationCard && event.target.closest("[data-show-result]")) { await showQueryResult(operationCard.dataset.operationId); return; }
  if (operationCard && event.target.closest("[data-open-console]")) {
    const list = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`); const proposal = list.proposals.find(item => item.id === operationCard.dataset.proposalId); if (proposal?.details?.sql) openConsole(proposal.details.sql); return;
  }
  if (!proposalCard || operationCard) return;
  try {
    const list = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`); const proposal = list.proposals.find(item => item.id === proposalCard.dataset.proposalId); if (!proposal) return;
    if (event.target.closest("[data-dismiss]")) { await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals/${proposal.id}`, { method: "DELETE" }); await refresh(); return; }
    if (!event.target.closest("[data-apply]")) return;
    if (proposal.actionType === "data_read") {
      event.target.closest("[data-apply]").disabled = true;
      await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals/${proposal.id}/executions`, { method: "POST", body: { expectedChatRevision: chat.revision, expectedProposalRevision: proposal.revision, proposalDigest: proposal.digest, confirmed: true } });
      await refresh(); return;
    }
    openProposalReview(proposal);
  } catch (error) {
    const apply = proposalCard.querySelector("[data-apply]"); if (apply) apply.disabled = false;
    showError(error);
  }
});

window.addEventListener("beforeunload", () => { globalThis.clearTimeout(pollTimer); globalThis.clearTimeout(loginTimer); globalThis.clearInterval(elapsedTimer); });
