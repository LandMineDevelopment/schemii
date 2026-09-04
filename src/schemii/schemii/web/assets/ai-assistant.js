import { requestJson } from "./api.js";
import { createIconButton } from "./ui.js";

const elements = {
  button: document.querySelector("#ai-assistant-button"),
  panel: document.querySelector("#ai-assistant"),
  close: document.querySelector("#ai-assistant-close"),
  historyButton: document.querySelector("#ai-assistant-history"),
  newButton: document.querySelector("#ai-assistant-new"),
  settingsButton: document.querySelector("#ai-assistant-settings"),
  status: document.querySelector("#ai-assistant-status"),
  model: document.querySelector("#ai-assistant-model"),
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

const CAPABILITIES = Object.freeze([
  "designChanges", "liveCatalog", "structuredDataRead", "rawSqlRead", "rawSqlWrite",
]);

let chat = null;
let chats = [];
let design = null;
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
let proposalArmTimer = null;
let proposalReviewOpenedAt = 0;

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

function formatDate(value) {
  const date = new Date(value);
  const sameDay = date.toDateString() === new Date().toDateString();
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
  ).format(date);
}

function modelValue(providerId, modelId) {
  return `${providerId}\u0000${modelId}`;
}

function selectedModel(select = elements.model) {
  return models.find(item => modelValue(item.providerId, item.id) === select.value) || null;
}

function loadModelOptions(select, selectedProvider, selectedId) {
  const current = modelValue(selectedProvider || "", selectedId || "");
  select.replaceChildren(...models.map(item => {
    const option = document.createElement("option");
    option.value = modelValue(item.providerId, item.id);
    option.textContent = `${item.name} · ${item.providerName}`;
    option.selected = option.value === current;
    return option;
  }));
  if (selectedId && !models.some(item => modelValue(item.providerId, item.id) === current)) {
    const unavailable = document.createElement("option");
    unavailable.value = current;
    unavailable.textContent = `${selectedId} · unavailable — choose another model`;
    unavailable.disabled = true;
    unavailable.selected = true;
    select.prepend(unavailable);
  } else if (!selectedId && select.options.length) {
    select.options[0].selected = true;
  }
}

function permissionSummary(capabilities = {}) {
  const enabled = CAPABILITIES.filter(name => capabilities[name]);
  if (!enabled.length) return "Explain only";
  if (enabled.length === CAPABILITIES.length) return "All proposal tools";
  return `${enabled.length} of ${CAPABILITIES.length} tools`;
}

function updateContextControls() {
  const provider = chat?.providerId || settings?.defaultProviderId;
  const model = chat?.modelId || settings?.defaultModelId;
  loadModelOptions(elements.model, provider, model);
  elements.permissionsCopy.textContent = permissionSummary(chat?.capabilities || settings?.defaultCapabilities);
  const working = chat?.status === "working";
  elements.model.disabled = !models.length || working;
  elements.newButton.disabled = !models.length || working;
  elements.settingsButton.disabled = working;
  elements.permissions.disabled = working;
  elements.disclosure.textContent = chat
    ? "This conversation uses the saved workspace design. Proposed actions always wait for your review."
    : "Start a conversation using the saved workspace design. Proposed actions always wait for your review.";
  const providerStatus = runtime?.providers?.find(item => item.id === provider);
  const privacyNotice = providerStatus?.privacy || providerStatus?.privacyNotice;
  if (privacyNotice) elements.disclosure.textContent += ` ${privacyNotice}`;
}

function appendInline(parent, source) {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\*[^*\n]+\*)/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith("**")) {
      node = document.createElement("strong"); node.textContent = token.slice(2, -2);
    } else if (token.startsWith("`")) {
      node = document.createElement("code"); node.textContent = token.slice(1, -1);
    } else if (token.startsWith("[")) {
      const boundary = token.indexOf("](");
      node = document.createElement("a"); node.textContent = token.slice(1, boundary);
      node.href = token.slice(boundary + 2, -1); node.target = "_blank"; node.rel = "noopener noreferrer";
    } else {
      node = document.createElement("em"); node.textContent = token.slice(1, -1);
    }
    parent.append(node); cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(source.slice(cursor)));
}

function tableCells(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
}

function renderMarkdown(source) {
  const fragment = document.createDocumentFragment();
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim(); const body = []; index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) { body.push(lines[index]); index += 1; }
      index += 1;
      const pre = document.createElement("pre"); const code = document.createElement("code");
      if (language) code.dataset.language = language; code.textContent = body.join("\n"); pre.append(code); fragment.append(pre); continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${Math.min(heading[1].length + 2, 6)}`);
      appendInline(node, heading[2]); fragment.append(node); index += 1; continue;
    }
    if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const table = document.createElement("table"); const head = document.createElement("thead"); const headRow = document.createElement("tr");
      for (const value of tableCells(line)) { const cell = document.createElement("th"); appendInline(cell, value); headRow.append(cell); }
      head.append(headRow); table.append(head); index += 2; const body = document.createElement("tbody");
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = document.createElement("tr");
        for (const value of tableCells(lines[index])) { const cell = document.createElement("td"); appendInline(cell, value); row.append(cell); }
        body.append(row); index += 1;
      }
      table.append(body); const scroll = document.createElement("div"); scroll.className = "ai-markdown-table"; scroll.tabIndex = 0; scroll.append(table); fragment.append(scroll); continue;
    }
    const listMatch = line.match(/^\s*(?:([-*+])|(\d+\.))\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]); const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*(?:([-*+])|(\d+\.))\s+(.+)$/);
        if (!itemMatch || Boolean(itemMatch[2]) !== ordered) break;
        const item = document.createElement("li"); appendInline(item, itemMatch[3]); list.append(item); index += 1;
      }
      fragment.append(list); continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = document.createElement("blockquote"); const values = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) { values.push(lines[index].replace(/^>\s?/, "")); index += 1; }
      appendInline(quote, values.join(" ")); fragment.append(quote); continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { fragment.append(document.createElement("hr")); index += 1; continue; }
    const paragraphLines = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+/.test(lines[index]) && !/^\s*(?:[-*+] |\d+\. |>|```)/.test(lines[index])) {
      if (lines[index].includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) break;
      paragraphLines.push(lines[index].trim()); index += 1;
    }
    const paragraph = document.createElement("p"); appendInline(paragraph, paragraphLines.join(" ")); fragment.append(paragraph);
  }
  return fragment;
}

function emptyTranscript() {
  const section = document.createElement("section"); section.className = "ai-empty-state";
  const mark = document.createElement("span"); mark.className = "ai-empty-mark"; mark.textContent = "AI";
  const title = document.createElement("strong"); title.textContent = "Ask about the current workspace";
  const copy = document.createElement("p"); copy.textContent = "Explain the design, trace a relationship, review a migration, or prepare a change for review.";
  section.append(mark, title, copy); return section;
}

function messageNode(message) {
  const article = document.createElement("article"); article.className = `ai-message ${message.role}`;
  const label = document.createElement("span"); label.textContent = message.role === "assistant" ? "Schemii AI" : message.role === "user" ? "You" : "System";
  const surface = document.createElement("div"); surface.className = "ai-message__surface";
  const content = document.createElement("div"); content.className = "ai-message__content"; content.append(renderMarkdown(message.text));
  const copy = createIconButton({ icon: "copy", label: "Copy message", placement: "left", className: "compact ai-message__copy" });
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(message.text); copy.classList.add("copied"); copy.setAttribute("aria-label", "Copied");
    globalThis.setTimeout(() => { copy.classList.remove("copied"); copy.setAttribute("aria-label", "Copy message"); }, 1200);
  });
  const time = document.createElement("time"); time.dateTime = message.createdAt; time.textContent = formatDate(message.createdAt);
  surface.append(content, copy);
  if (message.transient) {
    const privacy = document.createElement("span"); privacy.className = "ai-message__privacy"; privacy.textContent = "Temporary · not saved"; surface.append(privacy);
  }
  article.append(label, surface, time); return article;
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
  ]);
  if (nextSignature === timelineSignature) { placeActivity(); return; }
  const wasNearBottom = elements.body.scrollHeight - elements.body.scrollTop - elements.body.clientHeight < 80;
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
    const activitySlot = document.createElement("div"); activitySlot.className = "ai-turn__activity"; section.append(activitySlot);
    section.append(...responseMessages.map(messageNode));
    const actions = document.createElement("div"); actions.className = "ai-turn__actions";
    actions.innerHTML = `${renderProposalCards(group.proposals)}${renderOperationCards(group.operations, group.proposals)}`;
    if (actions.childElementCount) section.append(actions);
    return section;
  });
  elements.messages.replaceChildren(...(nodes.length ? nodes : [emptyTranscript()]));
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

function progressDots() {
  const grid = document.createElement("span"); grid.className = "ai-progress-grid"; grid.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 25; index += 1) grid.append(document.createElement("i"));
  return grid;
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
  if (event.kind === "error") {
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
  for (const [key, stage] of activityRun.stages) if (stage.state === "running") activityRun.stages.set(key, { ...stage, state });
  renderActivity(); globalThis.clearInterval(elapsedTimer); elapsedTimer = null;
  globalThis.setTimeout(() => { if (activityRun?.state !== "working") { activityRun = null; renderActivity(); } }, 2400);
}

function renderActivity() {
  if (!activityRun) { elements.activity.replaceChildren(); return; }
  const details = document.createElement("details"); details.className = `ai-run ${activityRun.state}`; details.open = true;
  const summary = document.createElement("summary"); const title = document.createElement("strong"); title.className = "ai-run-title";
  const running = activityRun.state === "working";
  title.textContent = running ? "Working with this workspace" : activityRun.state === "failed" ? "Turn failed" : activityRun.state === "cancelled" ? "Turn stopped" : "Response ready";
  if (running) title.classList.add("shimmer");
  const elapsed = document.createElement("time"); elapsed.className = "ai-run-time"; elapsed.textContent = `${Math.max(0, Math.round((Date.now() - activityRun.startedAt) / 1000))}s`;
  summary.append(progressDots(), title, elapsed); details.append(summary);
  const steps = document.createElement("div"); steps.className = "ai-run-steps"; const values = [...activityRun.stages.values()];
  if (!values.length) values.push({ label: "Starting assistant", state: "running" });
  for (const stage of values) {
    const row = document.createElement("div"); row.className = `ai-run-step ${stage.state}`;
    const marker = document.createElement("span"); marker.className = "ai-run-step-marker";
    const copy = document.createElement("span"); copy.className = "ai-run-step-copy"; copy.textContent = stage.label; row.append(marker, copy); steps.append(row);
  }
  if (activityRun.error) { const error = document.createElement("p"); error.className = "ai-run-error"; error.textContent = activityRun.error; steps.append(error); }
  if (running && activityRun.turnId) {
    const stop = createIconButton({ icon: "stop", label: "Stop assistant turn", className: "compact danger ai-run-stop" });
    stop.dataset.cancelTurn = activityRun.turnId; steps.append(stop);
  }
  details.append(steps); elements.activity.replaceChildren(details);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character]);
}

function displayCell(value) {
  if (value === null) return "NULL";
  if (typeof value !== "object") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

function renderProposalCards(items) {
  return items.filter(item => item.status === "pending").map(proposal => `
    <article class="ai-proposal${proposal.destructive ? " destructive" : ""}" data-proposal-id="${escapeHtml(proposal.id)}">
      <small>PROPOSED ${escapeHtml(proposal.actionType.replaceAll("_", " "))}</small><strong>${escapeHtml(proposal.summary)}</strong>
      ${proposal.destructive ? "<p class=\"ai-warning\">This proposal can remove data or database objects.</p>" : ""}
      <footer><button class="ui-button compact" type="button" data-dismiss>Dismiss</button><button class="ui-button compact primary" type="button" data-apply>Review &amp; apply</button></footer>
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
  if (proposal.destructive) {
    const warning = document.createElement("p"); warning.className = "ai-warning";
    warning.textContent = "This action can remove data or database objects. Review every target before applying it."; wrapper.append(warning);
  }
  const action = proposal.details || {};
  if (["data_read", "console_script"].includes(proposal.actionType) && action.sql) {
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
  } else if (Object.keys(action).length) {
    const label = document.createElement("small"); label.textContent = "SERVER-VALIDATED ACTION";
    const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = JSON.stringify(action, null, 2); pre.append(code); wrapper.append(label, pre);
  }
  return wrapper;
}

function openProposalReview(proposal) {
  pendingProposal = proposal;
  proposalReviewOpenedAt = Date.now();
  globalThis.clearTimeout(proposalArmTimer);
  elements.proposalReview.replaceChildren(proposalDetailsNode(proposal));
  elements.proposalConfirm.textContent = proposal.destructive ? "Apply destructive proposal" : "Apply proposal";
  elements.proposalConfirm.classList.toggle("danger", proposal.destructive);
  elements.proposalConfirm.classList.toggle("primary", !proposal.destructive);
  elements.proposalConfirm.disabled = true;
  openDialog(elements.proposalDialog);
  proposalArmTimer = globalThis.setTimeout(() => {
    if (elements.proposalDialog.open && pendingProposal?.id === proposal.id) elements.proposalConfirm.disabled = false;
  }, 350);
}

function closeProposalReview() {
  globalThis.clearTimeout(proposalArmTimer);
  proposalArmTimer = null;
  proposalReviewOpenedAt = 0;
  pendingProposal = null;
  elements.proposalConfirm.disabled = true;
  closeDialog(elements.proposalDialog);
}

function renderOperationCards(items, proposalItems) {
  const summaries = new Map(proposalItems.map(item => [item.id, item.summary]));
  return [...items].sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt))).map(operation => {
    const summary = summaries.get(operation.proposalId) || operation.kind.replaceAll("_", " "); let action = "";
    if (operation.kind === "data_read" && operation.status === "succeeded") action = `<button class="ui-button compact" type="button" data-show-result>Show rows</button>${chat.capabilities.structuredDataRead ? "<button class=\"ui-button compact\" type=\"button\" data-use-result>Ask about rows</button>" : ""}`;
    else if (operation.kind === "console_script" && operation.status === "succeeded") action = "<button class=\"ui-button compact\" type=\"button\" data-open-console>Open Console</button>";
    return `<article class="ai-operation${operation.status === "failed" ? " failed" : ""}" data-operation-id="${escapeHtml(operation.id)}" data-proposal-id="${escapeHtml(operation.proposalId)}"><small>${escapeHtml(operation.status.toUpperCase())}</small><strong>${escapeHtml(summary)}</strong>${operation.errorMessage ? `<p>${escapeHtml(operation.errorMessage)}</p>` : ""}${action ? `<footer>${action}</footer>` : ""}<div class="ai-operation__result"></div></article>`;
  }).join("");
}

function availableModels(status) {
  return (status?.providers || []).flatMap(provider => (provider.available ? provider.models : []).filter(model => model.status === "active").map(model => ({ ...model, providerId: provider.id, providerName: provider.name })));
}

function defaultModel() {
  return models.find(item => item.providerId === settings?.defaultProviderId && item.id === settings?.defaultModelId) || models[0] || null;
}

function defaultCapabilities() {
  return { ...Object.fromEntries(CAPABILITIES.map(name => [name, false])), ...(settings?.defaultCapabilities || {}) };
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
  const label = document.createElement("label"); const copy = document.createElement("span"); copy.textContent = "API key";
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
    state.textContent = provider.authenticated ? "Connected" : provider.available ? "Available" : "Not connected";
    const caret = document.createElement("span"); caret.className = "ai-provider-caret"; caret.textContent = "⌄";
    heading.append(name, state, caret); card.append(heading);
    if (provider.privacy || provider.privacyNotice) {
      const privacy = document.createElement("p"); privacy.className = "ai-provider-empty"; privacy.textContent = provider.privacy || provider.privacyNotice; card.append(privacy);
    }
    if (provider.authenticated && ["openai-codex", "openai"].includes(provider.id)) {
      const disconnect = document.createElement("button"); disconnect.type = "button"; disconnect.className = "ui-button compact"; disconnect.textContent = "Disconnect";
      disconnect.addEventListener("click", async () => {
        disconnect.disabled = true;
        try {
          await requestJson(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
          await refreshProviderStatus(); elements.settingsStatus.textContent = `${provider.name} disconnected.`;
        } catch (error) { elements.settingsStatus.textContent = error.message; disconnect.disabled = false; }
      }); card.append(disconnect);
    } else if (provider.id === "openai-codex") card.append(codexConnection());
    else if (provider.id === "openai") card.append(apiKeyConnection(provider));
    return card;
  }));
}

async function refreshProviderStatus() {
  runtime = await requestJson("/api/v1/ai/status"); models = availableModels(runtime);
  loadModelOptions(elements.model, chat?.providerId || settings?.defaultProviderId, chat?.modelId || settings?.defaultModelId);
  loadModelOptions(elements.settingsModel, chat?.providerId || settings?.defaultProviderId, chat?.modelId || settings?.defaultModelId);
  renderProviders(); updateContextControls();
}

async function createConversation({ model = defaultModel(), capabilities = defaultCapabilities(), title = "New conversation" } = {}) {
  if (!model) throw new Error("No AI model is configured for this deployment.");
  chat = await requestJson(`/api/v1/schemii/workspaces/${workspaceId()}/ai/chats`, { method: "POST", body: { providerId: model.providerId, modelId: model.id, title, capabilities } });
  chats = [chat, ...chats.filter(item => item.id !== chat.id)];
  activitySequence = 0; timelineSignature = ""; resultContextOperationId = null;
  updateContextControls(); renderAttachment(); renderTimeline([], [], []); return chat;
}

async function loadAssistant() {
  const workspace = workspaceId(); if (!workspace) throw new Error("Open a workspace first.");
  let list;
  [design, settings, runtime, list] = await Promise.all([
    requestJson(`/api/v1/schemii/workspaces/${workspace}/design`), requestJson("/api/v1/schemii/ai/settings"), requestJson("/api/v1/ai/status"),
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
}

function scheduleProgressPoll() {
  globalThis.clearTimeout(pollTimer);
  pollTimer = globalThis.setTimeout(() => void pollProgress().catch(showError), 700);
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
  chat = currentChat; applyActivityPage(activity, currentChat); updateContextControls();
  if (chat.status === "working") {
    streamingResponse = stream?.turnId && stream.text ? { ...stream, createdAt: streamingResponse?.createdAt || new Date().toISOString() } : null;
    renderTimeline();
    setStatus("Working"); if (!activityRun) beginActivity(); scheduleProgressPoll(); return;
  }
  if (chat.status === "failed") { setStatus("Needs attention"); finishActivity("failed"); }
  else { setStatus("Ready"); if (activityRun?.state === "working") finishActivity("completed"); else if (activityRun?.state === "cancelled") finishActivity("cancelled"); }
  await refresh({ includeChat: false });
}

async function refresh({ includeChat = true } = {}) {
  if (!chat) return;
  const chatId = chat.id;
  globalThis.clearTimeout(pollTimer); pollTimer = null;
  const initialActivityLoad = activitySequence === 0;
  const [history, proposalList, operationList, activity, transient, currentChat] = await Promise.all([
    requestJson(`/api/v1/schemii/ai/chats/${chat.id}/messages`), requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`),
    requestJson(`/api/v1/schemii/ai/chats/${chat.id}/operations`), requestJson(`/api/v1/schemii/ai/chats/${chat.id}/activity?after=${activitySequence}`),
    requestJson(`/api/v1/schemii/ai/chats/${chat.id}/transient-responses`),
    includeChat ? requestJson(`/api/v1/schemii/ai/chats/${chat.id}`) : Promise.resolve(chat),
  ]);
  if (chat?.id !== chatId) return;
  chat = currentChat;
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
  } else if (chat.status === "failed") { setStatus("Needs attention"); finishActivity("failed"); }
  else { setStatus("Ready"); if (activityRun?.state === "working") finishActivity("completed"); else if (activityRun?.state === "cancelled") finishActivity("cancelled"); }
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
    else setNotice("AI actions are proposals. Query rows are temporary and are never saved by Schemii.");
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

function fillSettings() {
  loadModelOptions(elements.settingsModel, chat?.providerId || settings?.defaultProviderId, chat?.modelId || settings?.defaultModelId);
  const capabilities = chat?.capabilities || defaultCapabilities();
  for (const name of CAPABILITIES) elements.settingsForm.elements[name].checked = Boolean(capabilities[name]);
  elements.settingsStatus.textContent = "Permission changes apply to this conversation and become the default for new ones.";
  renderProviders();
}

async function openSettings() {
  if (!settings || !runtime) { try { await loadAssistant(); } catch (error) { showError(error); return; } }
  fillSettings(); openDialog(elements.settingsDialog);
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
  target.innerHTML = "<p>Loading temporary rows…</p>";
  try {
    const page = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/operations/${operationId}/query-result`);
    target.innerHTML = `${page.freshnessNotice ? `<p class="ai-freshness">${escapeHtml(page.freshnessNotice)}</p>` : ""}<div class="ai-query-result"><table><thead><tr>${page.columns.map(column => `<th>${escapeHtml(column.name)}</th>`).join("")}</tr></thead><tbody>${page.rows.map(row => `<tr>${row.map(value => `<td>${escapeHtml(displayCell(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="ai-result-note">${page.rows.length} temporary row${page.rows.length === 1 ? "" : "s"} shown. Rows are not saved by Schemii.</p>`;
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
      const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/preferences`, { method: "PUT", body: { expectedSettingsRevision: settings.revision, expectedChatRevision: chat.revision, providerId: model.providerId, modelId: model.id, capabilities: chat.capabilities } });
      settings = result.settings; chat = result.chat;
    }
    setNotice(`Switched to ${model.name}. Conversation retained.`); await refresh(); elements.input.focus();
  }
  catch (error) { showError(error); updateContextControls(); }
});

elements.input?.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); elements.form.requestSubmit(); }
});

elements.form?.addEventListener("submit", async event => {
  event.preventDefault(); const text = elements.input.value.trim(); if (!text || !chat || chat.status === "working") return;
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
  finally { elements.send.disabled = false; }
});

elements.settingsForm?.addEventListener("submit", async event => {
  event.preventDefault(); const model = selectedModel(elements.settingsModel); if (!model) return;
  const capabilities = Object.fromEntries(CAPABILITIES.map(name => [name, elements.settingsForm.elements[name].checked])); elements.settingsStatus.textContent = "Saving…";
  try {
    const modelChanged = model.providerId !== chat.providerId || model.id !== chat.modelId;
    const result = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/preferences`, { method: "PUT", body: { expectedSettingsRevision: settings.revision, expectedChatRevision: chat.revision, providerId: model.providerId, modelId: model.id, capabilities } });
    settings = result.settings; chat = result.chat;
    updateContextControls(); closeDialog(elements.settingsDialog); setNotice(modelChanged ? `Switched to ${model.name}. Conversation retained; permissions saved.` : "Assistant permissions saved."); await refresh();
  } catch (error) { elements.settingsStatus.textContent = error.message; }
});

for (const control of document.querySelectorAll("[data-ai-settings-close]")) control.addEventListener("click", () => closeDialog(elements.settingsDialog));
for (const control of document.querySelectorAll("[data-ai-history-close]")) control.addEventListener("click", () => closeDialog(elements.historyDialog));
for (const control of document.querySelectorAll("[data-ai-delete-cancel]")) control.addEventListener("click", () => { pendingDeleteChatId = null; closeDialog(elements.deleteDialog); renderHistory(); openDialog(elements.historyDialog); });
for (const control of document.querySelectorAll("[data-ai-proposal-cancel]")) control.addEventListener("click", closeProposalReview);

elements.proposalConfirm?.addEventListener("click", async () => {
  const proposal = pendingProposal; if (!proposal || !chat) return;
  if (Date.now() - proposalReviewOpenedAt < 350) return;
  elements.proposalConfirm.disabled = true;
  try {
    const operation = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals/${proposal.id}/executions`, { method: "POST", body: { expectedChatRevision: chat.revision, expectedProposalRevision: proposal.revision, proposalDigest: proposal.digest, confirmed: true } });
    closeProposalReview();
    if (operation.kind === "design_change") { location.reload(); return; }
    await refresh(); if (operation.kind === "data_read") await showQueryResult(operation.id);
  } catch (error) { showError(error); }
  finally { if (elements.proposalDialog.open && pendingProposal) elements.proposalConfirm.disabled = false; }
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
  const proposalCard = event.target.closest("[data-proposal-id]"); const operationCard = event.target.closest("[data-operation-id]");
  const cancelTurn = event.target.closest("[data-cancel-turn]");
  if (cancelTurn) {
    cancelTurn.disabled = true;
    try { await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/turns/${cancelTurn.dataset.cancelTurn}/cancel`, { method: "POST" }); await refresh(); }
    catch (error) { showError(error); cancelTurn.disabled = false; }
    return;
  }
  if (operationCard && event.target.closest("[data-show-result]")) { await showQueryResult(operationCard.dataset.operationId); return; }
  if (operationCard && event.target.closest("[data-use-result]")) { resultContextOperationId = operationCard.dataset.operationId; renderAttachment(); setNotice("The next message will include a bounded, temporary copy of these rows. Released rows are rerun and clearly marked as refreshed."); elements.input.focus(); return; }
  if (operationCard && event.target.closest("[data-open-console]")) {
    const list = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`); const proposal = list.proposals.find(item => item.id === operationCard.dataset.proposalId); if (proposal?.details?.sql) openConsole(proposal.details.sql); return;
  }
  if (!proposalCard || operationCard) return;
  try {
    const list = await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals`); const proposal = list.proposals.find(item => item.id === proposalCard.dataset.proposalId); if (!proposal) return;
    if (event.target.closest("[data-dismiss]")) { await requestJson(`/api/v1/schemii/ai/chats/${chat.id}/proposals/${proposal.id}`, { method: "DELETE" }); await refresh(); return; }
    if (!event.target.closest("[data-apply]")) return;
    openProposalReview(proposal);
  } catch (error) { showError(error); }
});

window.addEventListener("beforeunload", () => { globalThis.clearTimeout(pollTimer); globalThis.clearTimeout(loginTimer); globalThis.clearInterval(elapsedTimer); });
