import { populateScopedReasoningOptions, reasoningForSelection } from "./ai-reasoning.js";
import { assistantDownloadLink } from "./ai-download.js";
import { requestJson } from "./http.js";
import { element } from "./dom.js";
import { createIconButton } from "./ui.js";
import { createMessageNode, availableModels, populateModelOptions, modelValue, apiKeyProviderDetails, providerConnectionState, zenConnectionNotice, sharedCodexConnectionNotice, sharedCodexPolicy, aiStatusPath } from "./ai-presentation.js";
import { renderPermissionBundles } from "./ai-permissions.js";
import { renderAiActivity } from "./ai-activity.js";
import { enhanceModelPicker } from "./ai-model-picker.js";
import { placeTurnActivity } from "./ai-timeline.js";


const active = chat => ["working", "waiting_approval"].includes(chat?.status);
const el = (tag, text, className) => element(tag, { text, className });
function button(label, action, icon) {
  const node = icon ? createIconButton({ icon, label, className: "ui-button" }) : element("button", { type: "button", text: label, className: "ui-button" });
  node.onclick = action;
  return node;
}
function dialog(title) {
  const node = element("dialog", { className: "ui-dialog model-ai-dialog", attrs: { "aria-label": title } });
  node.append(element("header", { className: "ui-dialog__head" }, [el("h2", title), button("Close", () => node.close(), "close")]));
  document.body.append(node);
  return node;
}

/** Shared product assistant shell. Product APIs own tools and permissions. */
export function createProductAssistant({
  trigger, getSubjectId, onSubjectChanged, api, productLabel, subjectLabel,
  subjectKey, subjectQueryKey, revisionKey, title, emptyTitle, emptyDescription,
  examplePrompts, resultOperations = [], resultPrompt, getAvailableActions, permissionScopeNote,
  allowEmptySubject = false,
}) {
  let chat = null, settings = null, runtime = null, available = [], pollTimer, loginTimer, login = null;
  let busy = false, opened = false, contextId = null, generation = 0, transcriptKey = "";
  let elapsedTimer = null, activityCard = null, activityRun = null, activityChatId = null;
  const revisions = new Map(), observedMutations = new Map();
  const panel = element("aside", { className: "model-ai", attrs: { id: "model-ai", "aria-label": title, "aria-hidden": "true", inert: "" } });
  const status = element("span", { className: "model-ai-status", text: "Ready", attrs: { role: "status" } });
  const modelSelect = element("select", { attrs: { "aria-label": "Assistant model" } });
  const sourceSelect = element("select", { attrs: { "aria-label": "Source connection for the first model" } });
  const sourceField = element("label", {}, [el("span", "Source connection"), sourceSelect]);
  const reasoningSelect = element("select", { attrs: { "aria-label": "Assistant reasoning level" } });
  const settingsReasoning = element("select", { attrs: { "aria-label": "Reasoning level" } });
  const notice = element("p", { className: "model-ai-notice", hidden: true, attrs: { role: "status" } });
  const disclosure = el("p", "", "model-ai-disclosure");
  const messages = element("div", { className: "model-ai-messages", attrs: { role: "log", "aria-label": "Conversation", "aria-live": "polite" } });
  const scroll = element("div", { className: "model-ai-scroll" }, [messages]);
  const input = element("textarea", { attrs: { rows: 3, placeholder: `Ask about this ${subjectLabel} or describe a change…`, "aria-label": `Message to ${title.toLowerCase()}` } });
  const send = createIconButton({ icon: "run", label: "Send", className: "ui-button primary" });
  send.type = "submit";
  const cancel = button("Stop", () => void act("cancel", {}), "stop");
  const settingsButton = button("Assistant settings", () => void showSettings(), "settings");
  const permissionsCopy = el("strong", "No actions enabled");
  const permissions = element("button", { type: "button", className: "model-ai-permissions-summary", attrs: { "aria-label": "Assistant permissions" } }, [el("span", "Permissions"), permissionsCopy]);
  permissions.onclick = () => void showSettings();
  const history = button("Conversation history", () => void showHistory(), "history");
  const fresh = button("New conversation", () => void guard(async () => {
    if (busy) return;
    if (needsSource()) {
      generation++; clearTimeout(pollTimer); chat = null; transcriptKey = ""; input.value = "";
      sourceSelect.value = sourceSelect.options.length === 2 ? sourceSelect.options[1].value : "";
      tell("Choose a source connection, then send a message to start a new conversation.");
      render(); sourceSelect.focus();
      return;
    }
    busy = true; controls();
    try { await newChat(); input.focus(); } finally { busy = false; render(); }
  }), "new-chat");
  const retry = button("Refresh", () => void guard(async () => { if (chat) await refresh(); else await load(); }));
  const form = element("form", { className: "model-ai-composer" }, [input, element("div", {}, [el("small", "Enter to send · Shift + Enter for a new line"), cancel, send])]);
  panel.append(element("header", { className: "model-ai-head" }, [element("div", {}, [el("small", `${productLabel.toUpperCase()} / AI`), el("h2", title)]), status, history, fresh, settingsButton, button("Close assistant", close, "close")]), element("div", { className: "model-ai-context" }, [sourceField, element("label", {}, [el("span", "Model"), modelSelect]), element("label", {}, [el("span", "Reasoning"), reasoningSelect]), permissions, disclosure]), notice, scroll, form);
  document.body.append(panel);
  const modelPicker = enhanceModelPicker(modelSelect, { refresh: () => refreshProviders({ refresh: true }) });
  const settingsDialog = dialog("Assistant settings"), historyDialog = dialog("Conversation history");
  const settingsBody = el("div", "", "model-ai-settings"), settingsStatus = element("p", { attrs: { role: "status" } });
  const providers = el("div", "", "model-ai-providers"), permissionList = el("div", "", "model-ai-permissions");
  const saveSettings = button("Save settings", () => void persistSettings());
  settingsBody.append(element("label", { className: "model-ai-reasoning" }, [el("span", "Reasoning level"), settingsReasoning]), el("p", "Higher levels can take longer. Model default keeps the provider’s default behavior.", "model-ai-muted"), el("h3", "Providers"), providers, el("h3", "Action permissions"), el("p", "Permissions apply to this conversation and become defaults for new conversations.", "model-ai-muted"), permissionList, settingsStatus, saveSettings);
  settingsDialog.append(settingsBody);
  const historyBody = el("div", "", "model-ai-history"); historyDialog.append(historyBody);
  trigger.setAttribute("aria-controls", "model-ai"); trigger.setAttribute("aria-expanded", "false");
  trigger.onclick = () => opened ? close() : void open();

  function tell(text = "", error = false) { notice.replaceChildren(document.createTextNode(text)); notice.hidden = !text; notice.dataset.error = String(error); if (error) notice.append(retry); }
  async function guard(fn) {
    try { return await fn(); } catch (error) { tell(error.message, true); return null; }
  }
  function selected() { return available.find(item => modelValue(item.providerId, item.id) === modelSelect.value); }
  function hasContext() { return allowEmptySubject || Boolean(getSubjectId()); }
  function subject() { return getSubjectId() || ""; }
  function needsSource() { return allowEmptySubject && !subject(); }
  function sourceReady() { return !needsSource() || Boolean(sourceSelect.value); }
  async function loadSources() {
    if (!needsSource()) return;
    const { connections } = await requestJson("/api/v1/connections?product=schemoo");
    const previous = sourceSelect.value;
    sourceSelect.replaceChildren(element("option", { text: "Choose connection", attrs: { value: "" } }),
      ...connections.map(item => element("option", { text: `${item.name} · ${item.database}`, attrs: { value: item.id } })));
    sourceSelect.value = connections.some(item => item.id === previous) ? previous : connections.length === 1 ? connections[0].id : "";
    controls();
  }
  sourceSelect.onchange = controls;
  function populateModels() {
    const selectedProvider = chat?.providerId || settings?.providerId;
    const selectedId = chat?.aiModelId || settings?.aiModelId || settings?.modelId;
    available = availableModels(runtime);
    populateModelOptions(modelSelect, available, selectedProvider, selectedId);
    populateScopedReasoningOptions(reasoningSelect, runtime, selected(), chat?.reasoningEffort || settings?.reasoningEffort || "default", busy || active(chat));
    controls();
  }
  let permissionEditor = null;
  function controls() {
    const running = active(chat);
    status.textContent = busy ? "Loading" : ({ working: "Working", waiting_approval: "Review batch", failed: "Needs attention" }[chat?.status] || "Ready");
    status.dataset.state = chat?.status || "idle";
    sourceField.hidden = !needsSource();
    sourceSelect.disabled = busy || Boolean(chat);
    send.disabled = busy || running || !hasContext() || !sourceReady() || !selected() || !input.value.trim();
    send.hidden = running;
    cancel.hidden = !running; cancel.disabled = busy;
    modelSelect.disabled = busy;
    reasoningSelect.disabled = busy || running || reasoningSelect.options.length <= 1;
    settingsReasoning.disabled = busy || running || settingsReasoning.options.length <= 1;
    fresh.disabled = busy || running || !hasContext() || (!chat && !sourceReady()) || !selected(); history.disabled = busy || !hasContext();
    saveSettings.disabled = busy;
    permissionEditor?.setBusy(busy);
    messages.querySelectorAll(".model-ai-approval button").forEach(control => { control.disabled = busy; });
    const permitted = new Set(chat?.availableActions || getAvailableActions?.(settings?.actions || []) || (settings?.actions || []).map(action => action.id));
    const actions = (settings?.actions || []).filter(action => permitted.has(action.id));
    const modes = chat?.modes || settings?.modes || {};
    const enabled = actions.filter(action => modes[action.id] !== "disabled").length;
    permissionsCopy.textContent = enabled ? `${enabled} of ${actions.length} actions` : "No actions enabled";
    const automatic = Object.values(chat?.modes || settings?.modes || {}).includes("automatic");
    const provider = runtime?.providers?.find(item => item.id === (chat?.providerId || selected()?.providerId));
    disclosure.textContent = `${automatic ? "Automatic actions enabled. Other actions require batch approval." : "Review each permitted action batch before it runs."} ${provider?.privacy || provider?.privacyNotice || ""}`;
  }
  function details(title, value) {
    return element("details", {}, [el("summary", title), element("pre", {}, [el("code", typeof value === "string" ? value : JSON.stringify(value, null, 2))])]);
  }
  function updateActivity() {
    if (activityChatId !== chat?.id) { activityChatId = chat?.id; activityRun = null; activityCard = null; }
    const progress = chat?.progress;
    if (progress) activityRun = progress;
    else if (chat?.status === "working") {
      if (!activityRun || activityRun.state !== "working") activityRun = { turnId: chat.turnId, startedAt: Date.now(), state: "working" };
      activityRun.stages = [{ label: chat?.stream ? "Writing response" : "Waiting for model response", state: "running" }];
    } else activityRun = null;
    if (activityRun) activityCard = renderAiActivity(activityRun, { existing: activityCard, workingLabel: `Working with this ${subjectLabel}`, onStop: () => void act("cancel", {}) });
    if (opened && activityRun?.state === "working") {
      if (!elapsedTimer) elapsedTimer = setInterval(() => {
        if (activityRun) renderAiActivity(activityRun, { existing: activityCard, workingLabel: `Working with this ${subjectLabel}`, onStop: () => void act("cancel", {}) });
      }, 1000);
    } else { clearInterval(elapsedTimer); elapsedTimer = null; }
  }
  function render() {
    controls();
    updateActivity();
    const key = JSON.stringify([chat?.messages, chat?.stream, chat?.pending, chat?.activity, chat?.progress, chat?.error, chat?.status]);
    if (key === transcriptKey) return; transcriptKey = key;
    const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 90;
    const previousMessages = new Map([...messages.querySelectorAll("[data-message-id]")].map(node => [node.dataset.messageId, node]));
    const expanded = new Map([...messages.querySelectorAll("details[data-detail-key]")].map(node => [node.dataset.detailKey, node.open]));
    let content = [];
    for (const message of chat?.messages || []) {
      const article = createMessageNode(message, { assistantName: `${productLabel} AI`, onError: message => tell(message, true), existing: previousMessages.get(message.id) });
      article.classList.add("model-ai-message", `model-ai-message--${message.role}`);
      article.querySelector(".ai-message__content").classList.add("model-ai-markdown");
      content.push(article);
    }
    for (const activity of chat?.activity || []) {
      const payload = activity.payload || activity;
      const operation = payload.operation || activity.operation || activity.kind || "";
      const title = payload.label || settings?.actions?.find(action => action.id === operation)?.label || operation || "Action";
      const card = element("article", { className: "model-ai-operation" }, [el("strong", title), el("small", payload.status || activity.status || "")]);
      card.dataset.timestamp = activity.createdAt || payload.createdAt || "";
      if (payload.message && payload.message !== title) card.append(el("p", payload.message));
      const result = payload.result || activity.receipt?.result || payload.receipt?.result || activity.result || payload.summary;
      if (result) {
        card.append(details("Result summary", result));
        if (["succeeded", "completed"].includes(payload.status || activity.status)) { const download = assistantDownloadLink(result); if (download) card.append(download); }
      }
      if (resultOperations.includes(operation) && ["succeeded", "completed"].includes(payload.status || activity.status)) card.append(button("Ask about this result", () => {
        const executionId = payload.executionId || payload.action?.args?.executionId;
        input.value = resultPrompt?.({ activity, payload, title, executionId }) || `Explain the result of ${title} (action ${activity.id}${executionId ? `, execution ${executionId}` : ""}). Use its result tools; if expired, rerun the saved read and tell me the data may have changed.`;
        input.focus(); controls();
      }, "assistant"));
      if (payload.error) card.append(el("p", typeof payload.error === "string" ? payload.error : payload.error.message));
      content.push(card);
    }
    if (content.length && content.every(node => Number.isFinite(Date.parse(node.dataset.timestamp)))) content.sort((left, right) => Date.parse(left.dataset.timestamp) - Date.parse(right.dataset.timestamp));
    const streamText = typeof chat?.stream === "string" ? chat.stream : chat?.stream?.text;
    if (activityRun) content = placeTurnActivity(content, activityCard, activityRun);
    if (streamText && chat?.status === "working") {
      const stream = createMessageNode({ id: "stream", role: "assistant", text: streamText }, { assistantName: `${productLabel} AI · writing`, onError: message => tell(message, true), existing: previousMessages.get("stream") });
      stream.classList.add("model-ai-message", "model-ai-stream");
      stream.querySelector(".ai-message__content").classList.add("model-ai-markdown");
      content.push(stream);
    }
    if (chat?.pending) {
      const pending = chat.pending;
      const card = element("section", { className: "model-ai-approval", attrs: { "aria-label": "Action batch awaiting approval" } }, [el("h3", "Review action batch"), el("p", pending.summary || "Approve this batch to continue. Only the listed actions are authorized.")]);
      for (const [index, action] of (pending.actions || []).entries()) {
        const label = action.label || settings?.actions?.find(item => item.id === action.operation)?.label || action.operation || "Proposed action";
        const proposal = details(label, action.args || action.arguments || action);
        proposal.dataset.detailKey = `${pending.id}:${index}`;
        proposal.open = expanded.get(proposal.dataset.detailKey) ?? false;
        card.append(proposal);
      }
      const approve = button("Approve batch", () => void act("approval", { pendingId: pending.id, approved: true }));
      const reject = button("Reject batch", () => void act("approval", { pendingId: pending.id, approved: false }));
      approve.classList.add("primary"); approve.disabled = busy; reject.disabled = busy;
      card.append(element("div", { className: "model-ai-actions" }, [reject, approve])); content.push(card);
    }
    if (chat?.error && !activityRun?.error) content.push(element("section", { className: "model-ai-failure", attrs: { role: "alert" } }, [el("strong", "The assistant could not finish"), el("p", typeof chat.error === "string" ? chat.error : chat.error.message)]));
    if (!content.length) {
      const empty = element("section", { className: "model-ai-empty" }, [el("span", "AI"), el("h3", emptyTitle), el("p", emptyDescription)]);
      for (const prompt of examplePrompts) empty.append(button(prompt, () => { input.value = prompt; input.focus(); controls(); }));
      content.push(empty);
    }
    // Keep surviving nodes attached: polling must not restart dots, shimmer, or message animations.
    const retained = new Set(content);
    for (const child of [...messages.children]) if (!retained.has(child)) child.remove();
    content.forEach((node, index) => { if (messages.children[index] !== node) messages.insertBefore(node, messages.children[index] || null); });
    if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
  }
  async function accept(next) {
    const adopted = allowEmptySubject && !contextId && next?.[subjectKey] && next.id === chat?.id;
    if (!next || (next[subjectKey] !== contextId && !adopted)) return;
    if (adopted) contextId = next[subjectKey];
    if (next.id === chat?.id && next.revision < chat.revision) return;
    const newlyFailed = next.status === "failed" && (chat?.id !== next.id || chat?.status !== "failed");
    const previous = revisions.get(next.id);
    const mutations = (next.activity || []).filter(item => item.status === "succeeded" && (!item[subjectKey] || item[subjectKey] === contextId || adopted && item.operation === "create_model") && settings?.actions?.some(action => action.id === item.operation && action.mutates && action.group !== "Results"));
    const observed = observedMutations.get(next.id);
    const newlySaved = observed ? mutations.filter(item => !observed.has(item.id)) : [];
    observedMutations.set(next.id, new Set(mutations.map(item => item.id)));
    chat = next;
    if (needsSource() && next.sourceConnectionId) sourceSelect.value = next.sourceConnectionId;
    populateModels();
    if (next[revisionKey] !== undefined) {
      revisions.set(next.id, next[revisionKey]);
    }
    if (adopted || (previous !== undefined && next[revisionKey] !== undefined && previous !== next[revisionKey]) || newlySaved.length) {
      const message = await onSubjectChanged?.(contextId, next[revisionKey], newlySaved);
      if (message) tell(message);
    }
    render(); schedule();
    if (newlyFailed) await guard(refreshProviders);
  }
  function schedule() { clearTimeout(pollTimer); if (opened && chat?.status === "working") pollTimer = setTimeout(() => void guard(refresh), 900); }
  async function refresh() {
    if (!chat) return;
    const id = chat.id, ticket = generation;
    const next = await requestJson(`${api}/chats/${encodeURIComponent(id)}`);
    if (generation === ticket && chat?.id === id) await accept(next);
  }
  async function newChat() {
    const choice = selected(); if (!choice || !hasContext()) throw new Error(`Choose a saved ${subjectLabel} and connect an AI provider first.`);
    const next = await requestJson(`${api}/chats`, { method: "POST", body: { [subjectKey]: subject(), ...(needsSource() ? { sourceConnectionId: sourceSelect.value } : {}), providerId: choice.providerId, aiModelId: choice.id, modes: chat?.modes || settings?.modes || {}, reasoningEffort: reasoningForSelection(runtime, choice, chat?.reasoningEffort || settings?.reasoningEffort) } });
    generation++; input.value = ""; transcriptKey = ""; await accept(next); tell("Started a new conversation. Previous conversations remain in history.");
  }
  async function load() {
    const id = subject(); if (!hasContext()) { tell(`Open or create a saved ${subjectLabel} to start a conversation.`); render(); return; }
    const ticket = ++generation; contextId = id;
    await loadSources();
    const [nextSettings, nextRuntime, list] = await Promise.all([requestJson(`${api}/settings`), requestJson(aiStatusPath(productLabel.toLowerCase(), id)), requestJson(`${api}/chats?${subjectQueryKey}=${encodeURIComponent(id)}`)]);
    if (ticket !== generation || subject() !== id) return;
    settings = nextSettings; runtime = nextRuntime;
    if (chat?.[subjectKey] !== id) chat = null;
    populateModels();
    if (chat) await refresh();
    else if (list.chats?.length) await accept(await requestJson(`${api}/chats/${encodeURIComponent(list.chats[0].id)}`));
    else render();
    if (!runtime.healthy) tell(runtime.message || "The AI runtime is unavailable. Check provider settings.", true);
  }
  async function open() {
    opened = true; panel.classList.add("open"); panel.inert = false; panel.setAttribute("aria-hidden", "false"); trigger.setAttribute("aria-expanded", "true");
    busy = true; controls(); await guard(load); busy = false; render(); input.focus();
  }
  function close() {
    opened = false; clearTimeout(pollTimer); clearInterval(elapsedTimer); elapsedTimer = null; trigger.focus(); panel.inert = true; panel.classList.remove("open"); panel.setAttribute("aria-hidden", "true"); trigger.setAttribute("aria-expanded", "false");
  }
  async function act(action, body) {
    if (!chat || busy) return;
    busy = true; transcriptKey = ""; render(); tell();
    const id = chat.id, ticket = generation;
    try {
      const next = await requestJson(`${api}/chats/${encodeURIComponent(id)}/${action}`, { method: "POST", body: { ...body, expectedRevision: chat.revision } });
      if (ticket === generation && chat?.id === id) await accept(next);
    } catch (error) { tell(error.message, true); await guard(refresh); }
    finally { busy = false; transcriptKey = ""; render(); }
  }
  input.addEventListener("input", controls);
  input.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  panel.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); close(); } });
  form.onsubmit = async event => {
    event.preventDefault(); const text = input.value.trim(); if (!text || busy || active(chat) || !selected() || !hasContext() || !sourceReady()) return;
    const providerId = chat?.providerId || selected().providerId;
    const provider = runtime?.providers?.find(item => item.id === providerId);
    const acknowledgment = providerId === "opencode";
    if (acknowledgment && !confirm(`${provider?.privacy || provider?.privacyNotice || "This provider may use prompts and responses for training."}\n\nSend this message and conversation context to this provider?`)) return;
    busy = true; controls(); tell();
    try {
      if (!chat) await newChat();
      const id = chat.id, ticket = generation;
      const next = await requestJson(`${api}/chats/${encodeURIComponent(id)}/messages`, { method: "POST", body: { text, expectedRevision: chat.revision, acknowledgeProviderDataPolicy: acknowledgment } });
      if (generation === ticket && chat?.id === id) { input.value = ""; await accept(next); scroll.scrollTop = scroll.scrollHeight; }
    } catch (error) { if (!input.value) input.value = text; tell(error.message, true); await guard(refresh); }
    finally { busy = false; render(); }
  };
  modelSelect.onchange = () => void guard(async () => {
    const choice = selected(); if (!choice || busy) return;
    const wasActive = active(chat);
    busy = true; controls();
    try {
      const body = { providerId: choice.providerId, aiModelId: choice.id, modes: chat?.modes || settings?.modes || {}, reasoningEffort: reasoningForSelection(runtime, choice, chat?.reasoningEffort || settings?.reasoningEffort), expectedRevision: chat?.revision };
      if (chat) await accept(await requestJson(`${api}/chats/${encodeURIComponent(chat.id)}/preferences`, { method: "PUT", body }));
      settings = await requestJson(`${api}/settings`, { method: "PUT", body: { providerId: body.providerId, aiModelId: body.aiModelId, modes: body.modes, reasoningEffort: body.reasoningEffort } });
      tell(`Switched to ${choice.name}.${wasActive ? " The current turn was stopped. Send a follow-up to continue." : " Conversation retained."}`);
    } finally { busy = false; populateModels(); }
  });

  reasoningSelect.onchange = () => void guard(async () => {
    const reasoningEffort = reasoningSelect.value, choice = selected();
    if (!choice || busy || active(chat)) return;
    busy = true; controls();
    try {
      const body = { providerId: choice.providerId, aiModelId: choice.id, modes: chat?.modes || settings?.modes || {}, reasoningEffort, expectedRevision: chat?.revision };
      if (chat) await accept(await requestJson(`${api}/chats/${encodeURIComponent(chat.id)}/preferences`, { method: "PUT", body }));
      settings = await requestJson(`${api}/settings`, { method: "PUT", body: { providerId: body.providerId, aiModelId: body.aiModelId, modes: body.modes, reasoningEffort } });
      tell("Reasoning level saved for your next message.");
    } finally { busy = false; populateModels(); }
  });

  async function showSettings() {
    settingsDialog.showModal(); settingsStatus.textContent = "Loading settings…";
    try { if (!settings || !runtime) await load(); else runtime = await requestJson(aiStatusPath(productLabel.toLowerCase(), getSubjectId())); if (chat) await refresh(); populateModels(); populateScopedReasoningOptions(settingsReasoning, runtime, selected(), chat?.reasoningEffort || settings?.reasoningEffort || "default", active(chat)); renderProviders(); renderPermissions(); settingsStatus.textContent = active(chat) ? "Saving permissions stops the current turn and clears its pending batch. Send a follow-up to continue with the new permissions." : ""; }
    catch (error) { settingsStatus.textContent = error.message; }
  }
  function renderPermissions() {
    const availableActions = chat?.availableActions || getAvailableActions?.(settings?.actions || []);
    const allowed = availableActions ? new Set(availableActions) : null;
    const actions = (settings?.actions || []).filter(action => !allowed || allowed.has(action.id));
    permissionEditor = renderPermissionBundles(permissionList, actions, chat?.modes || settings?.modes, { attribute: "data-action" });
    if (allowed && permissionScopeNote) permissionList.prepend(el("p", permissionScopeNote, "model-ai-muted"));
    controls();
  }
  async function persistSettings() {
    if (busy) return; busy = true; controls(); settingsStatus.textContent = "Saving…";
    try {
      const values = { ...(chat?.modes || settings?.modes || {}), ...Object.fromEntries([...permissionList.querySelectorAll("select[data-action]")].map(control => [control.dataset.action, control.value])) };
      const choice = selected();
      const body = { modes: values, providerId: chat?.providerId || choice?.providerId, aiModelId: chat?.aiModelId || choice?.id, reasoningEffort: reasoningForSelection(runtime, choice, settingsReasoning.value), expectedRevision: chat?.revision };
      if (chat) await accept(await requestJson(`${api}/chats/${encodeURIComponent(chat.id)}/preferences`, { method: "PUT", body }));
      settings = await requestJson(`${api}/settings`, { method: "PUT", body: { providerId: body.providerId, aiModelId: body.aiModelId, modes: body.modes, reasoningEffort: body.reasoningEffort } });
      settingsDialog.close(); tell("Assistant settings saved.");
    } catch (error) { settingsStatus.textContent = error.message; }
    finally { busy = false; render(); }
  }
  async function refreshProviders({ refresh = false } = {}) {
    runtime = await requestJson(aiStatusPath(productLabel.toLowerCase(), getSubjectId(), refresh), { timeoutMs: refresh ? 20_000 : 10_000 });
    populateModels(); renderProviders(); modelPicker.sync();
    if (refresh && (runtime.healthy === false || runtime.providers?.some(provider => provider.catalogError))) {
      throw new Error(runtime.message || "Could not check available models. Try again.");
    }
  }
  function renderProviders() {
    providers.replaceChildren();
    for (const provider of runtime?.providers || []) {
      const card = element("details", { className: "model-ai-provider" }, [element("summary", {}, [el("strong", provider.name), el("span", providerConnectionState(provider))])]);
      card.open = !provider.available || provider.id === chat?.providerId;
      if (provider.privacy || provider.privacyNotice) card.append(el("p", provider.privacy || provider.privacyNotice));
      if (provider.id === "opencode") card.append(el("p", zenConnectionNotice, "model-ai-muted"));
      else if (provider.id === "instance-codex") card.append(el("p", `${sharedCodexConnectionNotice} ${sharedCodexPolicy(provider)}`, "model-ai-muted"));
      else if (provider.authenticated && ["openai", "openai-codex"].includes(provider.id)) card.append(button("Disconnect", async () => {
        if (!confirm(`Disconnect ${provider.name}? Other conversations using this provider will also be affected.`)) return;
        try { await requestJson(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: "DELETE" }); await refreshProviders(); } catch (error) { settingsStatus.textContent = error.message; }
      }));
      else if (apiKeyProviderDetails[provider.id]) {
        const details = apiKeyProviderDetails[provider.id];
        const key = element("input", { attrs: { type: "password", autocomplete: "off", required: "", "aria-label": details.label, placeholder: details.label } });
        const connect = element("button", { type: "submit", className: "ui-button", text: "Connect" });
        const auth = element("form", { className: "model-ai-auth" }, [key, connect]);
        auth.onsubmit = async event => { event.preventDefault(); connect.disabled = true; try { await requestJson(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: "POST", body: { apiKey: key.value } }); key.value = ""; await refreshProviders(); settingsStatus.textContent = `${provider.name} connected.`; } catch (error) { settingsStatus.textContent = error.message; connect.disabled = false; } };
        card.append(auth);
      } else if (provider.id === "openai-codex") {
        if (login) {
          card.open = true; card.append(el("p", login.userCode ? "Enter this code at OpenAI:" : "Preparing device sign-in…"), el("strong", login.userCode || ""));
          try { const url = new URL(login.verificationUrl); if (url.protocol === "https:") card.append(element("a", { text: "Continue at OpenAI", attrs: { href: url.href, target: "_blank", rel: "noopener noreferrer" } })); } catch { /* URL arrives after device sign-in starts. */ }
          const cancelLogin = button("Cancel sign-in", async () => {
            if (!login?.id) return;
            try { await requestJson(`/api/v1/ai/prototype/logins/${encodeURIComponent(login.id)}`, { method: "DELETE" }); clearTimeout(loginTimer); login = null; renderProviders(); } catch (error) { settingsStatus.textContent = error.message; }
          }); cancelLogin.disabled = !login.id; card.append(cancelLogin);
        } else card.append(button("Connect Codex", async () => {
          login = {}; renderProviders();
          try { const result = await requestJson("/api/v1/ai/prototype/login", { method: "POST" }); login = result; await pollLogin(result); }
          catch (error) { login = null; renderProviders(); settingsStatus.textContent = error.message; }
        }));
      }
      providers.append(card);
    }
    if (!providers.children.length) providers.append(el("p", runtime?.message || "No providers are configured."));
  }
  async function pollLogin(current) {
    try {
      const state = await requestJson(`/api/v1/ai/prototype/logins/${encodeURIComponent(current.id)}`);
      if (login !== current) return;
      if (state.status === "succeeded") { login = null; await refreshProviders(); settingsStatus.textContent = "Codex connected."; return; }
      if (state.status !== "pending") throw new Error(state.message || "Sign-in expired. Try connecting again.");
      Object.assign(current, state); renderProviders(); loginTimer = setTimeout(() => void pollLogin(current), 2000);
    } catch (error) { if (login === current) { login = null; renderProviders(); settingsStatus.textContent = error.message; } }
  }
  async function showHistory() {
    historyDialog.showModal(); historyBody.replaceChildren(el("p", "Loading conversations…"));
    try {
      const list = await requestJson(`${api}/chats?${subjectQueryKey}=${encodeURIComponent(subject())}`);
      historyBody.replaceChildren();
      for (const item of list.chats || []) {
        const openChat = button(item.title || "Untitled conversation", () => void guard(async () => { generation++; clearTimeout(pollTimer); if (needsSource() && item.sourceConnectionId) sourceSelect.value = item.sourceConnectionId; await accept(await requestJson(`${api}/chats/${encodeURIComponent(item.id)}`)); populateModels(); historyDialog.close(); input.value = ""; input.focus(); controls(); }));
        const remove = button(`Delete ${item.title || "conversation"}`, async () => {
          if (!confirm(`Delete “${item.title || "this conversation"}” and its saved messages? This cannot be undone.`)) return;
          remove.disabled = true;
          try { await requestJson(`${api}/chats/${encodeURIComponent(item.id)}`, { method: "DELETE" }); if (chat?.id === item.id) { generation++; chat = null; clearTimeout(pollTimer); render(); } await showHistory(); } catch (error) { historyBody.append(el("p", error.message, "model-ai-failure")); remove.disabled = false; }
        }, "delete");
        historyBody.append(element("article", {}, [element("div", {}, [openChat, el("small", `${item.aiModelId || ""}${item.id === chat?.id ? " · Current" : ""}${item.updatedAt ? ` · ${new Date(item.updatedAt).toLocaleString()}` : ""}`)]), remove]));
      }
      if (!historyBody.children.length) historyBody.append(el("p", `No saved conversations for this ${subjectLabel} yet.`));
    } catch (error) { historyBody.replaceChildren(el("p", error.message, "model-ai-failure")); }
  }
  window.addEventListener("beforeunload", () => { clearTimeout(pollTimer); clearTimeout(loginTimer); clearInterval(elapsedTimer); });
  return {
    open,
    async subjectChanged() {
      if (contextId === getSubjectId()) return;
      generation++; chat = null; transcriptKey = ""; input.value = ""; tell(); clearTimeout(pollTimer); clearInterval(elapsedTimer); elapsedTimer = null; contextId = getSubjectId();
      if (opened) { busy = true; controls(); await guard(load); busy = false; render(); }
    },
  };
}

export function createModelAssistant({ trigger, getModelId, onModelChanged, api = "/api/v1/schemoo/ai" }) {
  const assistant = createProductAssistant({
    trigger, getSubjectId: getModelId, onSubjectChanged: onModelChanged, api,
    productLabel: "Schemoo", subjectLabel: "model", subjectKey: "modelId",
    allowEmptySubject: true,
    subjectQueryKey: "model_id", revisionKey: "modelRevision", title: "Model assistant",
    emptyTitle: "Shape your semantic model",
    emptyDescription: "Explore tables and relationships, explain a filter, or describe a model change. Your saved model provides the context.",
    examplePrompts: ["Explain this model and its relationships.", "Check this model for issues.", "Help me build a useful preview query."],
    resultOperations: ["execute_model", "get_execution", "get_result_page", "parameter_values", "domain_values"],
  });
  return { ...assistant, modelChanged: assistant.subjectChanged };
}
