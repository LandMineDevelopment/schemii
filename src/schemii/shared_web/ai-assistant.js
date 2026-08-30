(() => {
  const safeHttpUrl = value => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  };

  function normalizeStoredModel(value) {
    if (typeof value !== "string" || !value || value.length > 1024) return "";
    try {
      const model = JSON.parse(value);
      if (!model || typeof model !== "object" || Array.isArray(model) || Object.keys(model).sort().join(",") !== "modelId,providerId") return "";
      if (typeof model.providerId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(model.providerId)) return "";
      if (typeof model.modelId !== "string" || !model.modelId || model.modelId !== model.modelId.trim() || model.modelId.length > 256 || /[\x00-\x1f\x7f]/.test(model.modelId)) return "";
      return JSON.stringify({ providerId: model.providerId, modelId: model.modelId });
    } catch { return ""; }
  }

  function formatDuration(milliseconds) {
    const seconds = Math.max(0, milliseconds) / 1000;
    return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
  }

  function aiContextCacheKey(parts) {
    let hash = 1469598103934665603n;
    for (const character of JSON.stringify(parts)) {
      hash ^= BigInt(character.codePointAt(0));
      hash = BigInt.asUintN(64, hash * 1099511628211n);
    }
    return hash.toString(16).padStart(16, "0");
  }

  function boundedUtf8Text(value, maximum) {
    const encoder = new TextEncoder();
    let result = "";
    for (const character of String(value ?? "")) {
      if (encoder.encode(result + character).length > maximum) break;
      result += character;
    }
    return result.trim();
  }

  function boundedAiQueryResult(result, { maxRows = 50, maxColumns = 50, maxBytes = 24 * 1024, envelope = {} } = {}) {
    const columns = (result?.columns ?? []).slice(0, maxColumns).map(column => ({ name: String(column?.name ?? column) }));
    const rows = (result?.rows ?? []).slice(0, maxRows).map(row => Array.isArray(row) ? row.slice(0, columns.length) : []);
    const bounded = {
      ...envelope,
      columns,
      rows,
      rowCount: rows.length,
      truncated: Boolean(result?.truncated || (result?.rows?.length ?? 0) > rows.length || (result?.columns?.length ?? 0) > columns.length),
    };
    const encoder = new TextEncoder();
    while (bounded.rows.length && encoder.encode(JSON.stringify(bounded)).length > maxBytes) {
      bounded.rows.pop();
      bounded.rowCount = bounded.rows.length;
      bounded.truncated = true;
    }
    if (encoder.encode(JSON.stringify(bounded)).length > maxBytes) throw new Error("Query result metadata exceeds the AI disclosure limit");
    return bounded;
  }

  const AI_MODE_LABELS = {
    disabled: "Disabled", every_action: "Every action", once_per_chat: "Once per chat", automatic: "Automatic",
  };
  const AI_CAPABILITY_LABELS = {
    schema: "Schema changes", structured_read: "Structured data read", structured_write: "Structured data write",
    raw_read: "Raw SQL read", raw_write: "Raw SQL write", dashboard_read: "Dashboard read", dashboard_write: "Dashboard changes",
  };
  const AI_BOUND_FIELDS = [
    ["rowsDisclosed", "Rows disclosed", 1, 10000, "Maximum rows disclosed to the selected provider."],
    ["rowsWritten", "Rows submitted", 1, 10000, "Maximum primary rows submitted by a bounded structured operation; trigger and rule side effects are not counted."],
    ["pagesInspected", "Pages inspected", 1, 100, "Maximum catalog or result pages inspected."],
    ["rawStatements", "Raw statements", 1, 20, "Maximum statements in one raw SQL operation."],
    ["operationTimeoutMs", "Operation timeout (ms)", 1000, 300000, "Blank means Inherit PostgreSQL. PostgreSQL and connection policy remain authoritative."],
    ["agentConcurrency", "Agent concurrency", 1, 16, "Maximum concurrent agent operations."],
  ];

  function createAiAssistant(options) {
    const {
      sessionClient, root, trigger, settingsDialog, historyDialog, storageKey, getContext,
      buildMessagePayload, buildSessionPayload, contextKey = () => null, parseSession = session => ({ title: session.title || "Untitled chat", key: null }),
      buildProposalClaimPayload, buildProposalExecutionPayload, prepareProposalExecution, buildHistoryQuery, renderAction, validateAction, handleOperationResult, toolLabels = {}, skillLabels = {}, labels = {},
      onOpenChange = () => {}, onAccessChange = () => {}, onNewChat = () => {}, onPolicyChange = () => {}, state: suppliedState,
      canViewSession = () => true, extraBusyControls = [], panelModal = true,
    } = options;
    if (!sessionClient || !root || !trigger || !settingsDialog || !historyDialog || typeof getContext !== "function") throw new TypeError("AI assistant dependencies are required");
    const find = name => root.querySelector(`[data-ai="${name}"]`);
    const elements = {
      model: find("model"), access: find("access"), status: find("status"), railStatus: trigger.querySelector("[data-ai-trigger-status]"),
      messages: find("messages"), prompt: find("prompt"), form: find("form"), send: find("send"), close: find("close"),
      newChat: find("new-chat"), history: find("history"), settings: find("settings"), disclosure: find("disclosure"),
      settingsStatus: settingsDialog.querySelector("[data-ai-settings-status]"), settingsBody: settingsDialog.querySelector("[data-ai-settings-body]"),
      historyList: historyDialog.querySelector("[data-ai-history-body]"),
    };
    if (Object.entries(elements).some(([key, value]) => !value && !["railStatus", "disclosure"].includes(key))) throw new TypeError("AI assistant markup is incomplete");
    const state = suppliedState || {};
    Object.assign(state, {
      loaded: false, available: false, version: "", providers: [], default: {}, authMethods: {}, skills: [], settings: null,
      sessionId: null, contextKey: null, busy: false, requestGeneration: 0, oauth: null,
    });
    let settingsReturnFocus = null;
    let historyReturnFocus = null;
    const proposalActivities = new Set();
    let proposalActivityTimer = null;
    const allowPath = path => typeof path === "string" && path.startsWith("/api/ai/");
    const request = (path, requestOptions = {}) => sessionClient.json(path, requestOptions, { allowPath, defaultMessage: "The AI service request failed" });
    const fetchActivity = (path, requestOptions = {}) => sessionClient.fetch(path, requestOptions, { allowPath, defaultMessage: "Agent activity is unavailable" });
    const shared = window.SchemiiShared;
    shared.decorateIconControl(trigger, { icon: "assistant", label: labels.trigger || "AI assistant", placement: "bottom", className: trigger.className });
    if (root.id) trigger.setAttribute("aria-controls", root.id);
    if (elements.railStatus) trigger.append(elements.railStatus);
    shared.decorateIconControl(elements.history, { icon: "history", label: "Chat history" });
    shared.decorateIconControl(elements.newChat, { icon: "newChat", label: "New chat" });
    shared.decorateIconControl(elements.settings, { icon: "settings", label: "AI permissions and provider settings" });
    shared.decorateIconControl(elements.close, { icon: "close", label: "Close AI assistant" });
    const settingsClose = settingsDialog.querySelector("[data-ai-settings-close]");
    const historyClose = historyDialog.querySelector("[data-ai-history-close]");
    shared.decorateIconControl(settingsClose, { icon: "close", label: "Close AI settings" });
    shared.decorateIconControl(historyClose, { icon: "close", label: "Close chat history" });

    function storedModel() {
      try { return normalizeStoredModel(localStorage.getItem(storageKey)); } catch { return ""; }
    }

    function rememberModel(value = elements.model.value) {
      const normalized = normalizeStoredModel(value);
      if (!normalized) return;
      try { localStorage.setItem(storageKey, normalized); } catch { /* Model preference persistence is optional. */ }
    }

    function setOpen(open) {
      root.classList.toggle("open", open);
      root.setAttribute("aria-hidden", String(!open));
      root.inert = !open;
      trigger.classList.toggle("active", open);
      trigger.setAttribute("aria-expanded", String(open));
      onOpenChange(open);
      if (open) {
        loadStatus();
        requestAnimationFrame(() => elements.prompt.focus());
      } else {
        if (settingsDialog.open) closeSettings(false);
        if (historyDialog.open) closeHistory(false);
        requestAnimationFrame(() => trigger.focus());
      }
    }

    function setNestedDialogOpen(open) {
      root.inert = open || !root.classList.contains("open");
      root.setAttribute("aria-hidden", String(open || !root.classList.contains("open")));
    }

    function focusablePanelControls() {
      return [...root.querySelectorAll('button:not([disabled]), select:not([disabled]), textarea:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')]
        .filter(control => !control.hidden && control.getAttribute("aria-hidden") !== "true" && control.getClientRects().length);
    }

    function trapPanelFocus(event) {
      if (event.key !== "Tab" || !root.classList.contains("open") || settingsDialog.open || historyDialog.open) return;
      const controls = focusablePanelControls();
      if (!controls.length) return;
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }

    function setBusy(busy) {
      state.busy = busy;
      elements.send.disabled = busy || !state.available || !elements.model.value;
      elements.prompt.disabled = busy || !state.available || !elements.model.value;
      for (const control of [elements.newChat, elements.history, elements.settings, elements.access, elements.model]) control.disabled = busy || (control === elements.model && !elements.model.value);
      for (const control of extraBusyControls) control.disabled = busy || control.dataset.aiUnavailable === "true";
      elements.send.textContent = busy ? "Working..." : "Send";
    }

    function renderModels() {
      const previous = normalizeStoredModel(elements.model.value) || storedModel();
      elements.model.replaceChildren();
      for (const provider of state.providers.filter(item => item.connected && item.models?.length)) {
        const group = document.createElement("optgroup");
        group.label = provider.name;
        for (const model of provider.models) {
          const option = document.createElement("option");
          option.value = JSON.stringify({ providerId: provider.id, modelId: model.id });
          const active = !model.status || model.status === "active";
          option.disabled = !active;
          option.textContent = active ? model.name : `${model.name} (${model.status})`;
          group.append(option);
        }
        elements.model.append(group);
      }
      const selectable = option => option.value && !option.disabled;
      if (previous && [...elements.model.options].some(option => option.value === previous && selectable(option))) elements.model.value = previous;
      else {
        const provider = state.providers.find(item => item.connected && item.models?.some(model => model.id === state.default?.[item.id]));
        const preferred = provider && JSON.stringify({ providerId: provider.id, modelId: state.default[provider.id] });
        const defaultOption = preferred && [...elements.model.options].find(option => option.value === preferred && selectable(option));
        const fallback = [...elements.model.options].find(selectable);
        elements.model.value = (defaultOption || fallback)?.value || "";
      }
      if (!elements.model.options.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Connect a provider in settings";
        elements.model.append(option);
      }
      const hasModel = Boolean(elements.model.value);
      elements.prompt.placeholder = hasModel ? (labels.prompt || "Ask the assistant...") : "Connect a provider in settings to start chatting";
      const emptyCopy = elements.messages.querySelector("[data-ai-empty-copy]");
      if (emptyCopy && !hasModel) emptyCopy.textContent = "Connect a provider in settings to start chatting.";
      if (hasModel) rememberModel();
      setBusy(state.busy);
    }

    function authMethods(providerId) {
      const methods = state.authMethods?.[providerId] ?? [];
      if (Array.isArray(methods)) return methods.map(method => typeof method === "string" ? { id: method, name: method } : method);
      return Object.entries(methods).map(([id, method]) => typeof method === "string" ? { id, name: method } : { id, ...method });
    }

    function renderOauthCompletion(authorization) {
      const form = document.createElement("form");
      form.className = "ai-oauth-completion";
      const instructions = document.createElement("p");
      instructions.textContent = authorization.instructions || "Complete authorization in the opened page, then enter the returned code if requested.";
      const url = safeHttpUrl(authorization.url);
      if (url) {
        const link = document.createElement("a");
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "Open authorization page";
        form.append(instructions, link);
      } else form.append(instructions);
      const code = document.createElement("input");
      code.name = "code"; code.autocomplete = "off"; code.placeholder = "Callback code (if provided)";
      const finish = document.createElement("button");
      finish.className = "button button-primary"; finish.type = "submit"; finish.textContent = "Complete connection";
      form.append(code, finish);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!state.oauth) return;
        try {
          await request("/api/ai/auth/oauth/callback", { method: "POST", body: JSON.stringify({ providerId: state.oauth.providerId, method: state.oauth.method, ...(code.value.trim() ? { code: code.value.trim() } : {}) }) });
          state.oauth = null;
          await loadStatus(true);
        } catch (error) { elements.settingsStatus.textContent = error.message; }
      });
      elements.settingsStatus.replaceChildren(form);
    }

    function buildAuthForm(provider, method) {
      const form = document.createElement("form");
      form.className = "ai-auth-form";
      const methodId = Number(method.id);
      const apiKeyMethod = method.type === "api" || /api.?key/i.test(method.name ?? method.label ?? "");
      const label = document.createElement("strong");
      label.textContent = method.name ?? (apiKeyMethod ? "API key" : "OAuth");
      form.append(label);
      const helpUrl = safeHttpUrl(method.helpUrl);
      if (helpUrl) {
        const help = document.createElement("a");
        help.className = "ai-auth-help"; help.href = helpUrl; help.target = "_blank"; help.rel = "noopener noreferrer"; help.textContent = method.helpLabel || "Create provider key";
        form.append(help);
      }
      const addInputs = () => {
        for (const definition of method.inputs ?? method.prompts ?? []) {
          const name = definition.id ?? definition.key ?? definition.name;
          const input = definition.type === "select" ? document.createElement("select") : document.createElement("input");
          if (definition.type === "select") for (const item of definition.options ?? []) {
            const option = document.createElement("option"); option.value = item.value; option.textContent = item.label || item.value; input.append(option);
          }
          input.name = name; input.required = definition.required !== false; input.autocomplete = "off"; input.placeholder = definition.label ?? definition.message ?? name;
          form.append(input);
        }
      };
      if (apiKeyMethod) {
        const key = document.createElement("input");
        key.type = "password"; key.name = "key"; key.autocomplete = "off"; key.placeholder = "API key"; key.required = true;
        form.append(key);
      }
      addInputs();
      const submit = document.createElement("button");
      submit.type = "submit"; submit.className = "button button-primary"; submit.textContent = apiKeyMethod ? "Connect" : "Start authorization";
      form.append(submit);
      form.addEventListener("submit", async event => {
        event.preventDefault(); submit.disabled = true;
        try {
          if (apiKeyMethod) {
            const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name && name !== "key"));
            await request("/api/ai/auth/api", { method: "POST", body: JSON.stringify({ providerId: provider.id, key: form.elements.key.value, inputs }) });
            form.elements.key.value = "";
            await loadStatus(true);
          } else {
            const inputs = Object.fromEntries([...new FormData(form)].filter(([name]) => name));
            const authorization = await request("/api/ai/auth/oauth/authorize", { method: "POST", body: JSON.stringify({ providerId: provider.id, method: methodId, inputs }) });
            state.oauth = { providerId: provider.id, method: methodId };
            renderOauthCompletion(authorization);
            const url = safeHttpUrl(authorization.url);
            if (url) window.open(url, "_blank", "noopener,noreferrer");
          }
        } catch (error) { elements.settingsStatus.textContent = error.message; }
        finally { submit.disabled = false; }
      });
      return form;
    }

    function renderProviders(container) {
      container.replaceChildren();
      for (const provider of state.providers) {
        const card = document.createElement("article"); card.className = "ai-provider-card";
        const heading = document.createElement("div"); heading.className = "ai-provider-heading";
        const name = document.createElement("strong"); name.textContent = provider.name;
        const indicator = document.createElement("span");
        const free = provider.connected && provider.authenticated === false;
        indicator.className = provider.connected ? "connected" : ""; indicator.textContent = free ? "Free access" : provider.connected ? "Connected" : "Not connected";
        heading.append(name, indicator); card.append(heading);
        if (provider.connected && !free) {
          const disconnect = document.createElement("button"); disconnect.type = "button"; disconnect.className = "button button-ghost"; disconnect.textContent = "Disconnect";
          disconnect.addEventListener("click", async () => {
            if (!confirm(`Disconnect ${provider.name}? This affects both Schemii and Schemer.`)) return;
            await request(`/api/ai/auth/${encodeURIComponent(provider.id)}`, { method: "DELETE" }); await loadStatus(true);
          });
          card.append(disconnect);
        } else {
          const methods = authMethods(provider.id);
          for (const method of methods) card.append(buildAuthForm(provider, method));
          if (!methods.length) { const note = document.createElement("p"); note.textContent = "This provider did not advertise a supported authentication method."; card.append(note); }
        }
        container.append(card);
      }
    }

    function invalidatePendingProposals() {
      state.requestGeneration += 1;
      state.sessionId = null;
      state.contextKey = null;
      for (const card of elements.messages.querySelectorAll(".ai-action-card:not(.applied)")) {
        card.classList.add("revoked");
        card.querySelectorAll("button").forEach(button => { button.disabled = true; });
        if (!card.querySelector(".ai-action-revoked")) {
          const notice = document.createElement("p");
          notice.className = "ai-action-revoked";
          notice.textContent = "Revoked because AI permissions or limits changed. Request a new proposal.";
          card.append(notice);
        }
      }
    }

    function renderPolicyEditor() {
      elements.settingsBody.replaceChildren();
      const settings = state.settings;
      const policySection = document.createElement("section");
      policySection.className = "ai-policy-section";
      const heading = document.createElement("div");
      heading.className = "ai-settings-section-head";
      const headingCopy = document.createElement("div");
      const title = document.createElement("h3"); title.textContent = "Permissions and limits";
      const intro = document.createElement("p"); intro.textContent = "These application settings are authoritative. Chat disclosure and exact-target context can only narrow them.";
      headingCopy.append(title, intro); heading.append(headingCopy); policySection.append(heading);
      if (!settings) {
        const unavailable = document.createElement("p"); unavailable.className = "ai-settings-empty"; unavailable.textContent = "AI policy settings are unavailable.";
        policySection.append(unavailable);
      } else {
        const form = document.createElement("form"); form.className = "ai-policy-form"; form.dataset.aiPolicyForm = "";
        const capabilities = document.createElement("fieldset");
        const capabilityLegend = document.createElement("legend"); capabilityLegend.textContent = "Product-supported capabilities"; capabilities.append(capabilityLegend);
        for (const [name, authority] of Object.entries(settings.capabilities ?? {})) {
          const row = document.createElement("label"); row.className = "ai-policy-capability";
          const copy = document.createElement("span"); const label = document.createElement("strong"); label.textContent = AI_CAPABILITY_LABELS[name] || name;
          const effective = document.createElement("small");
          effective.textContent = authority.effectiveMode === authority.configuredMode
            ? `Effective: ${AI_MODE_LABELS[authority.effectiveMode]}`
            : `Configured: ${AI_MODE_LABELS[authority.configuredMode]}; effective: ${AI_MODE_LABELS[authority.effectiveMode]} because this action has a non-relaxable safety floor.`;
          copy.append(label, effective);
          const select = document.createElement("select"); select.name = name; select.setAttribute("aria-label", `${label.textContent} mode`);
          for (const mode of Object.keys(AI_MODE_LABELS)) {
            const option = document.createElement("option"); option.value = mode; option.textContent = AI_MODE_LABELS[mode]; option.selected = authority.configuredMode === mode; select.append(option);
          }
          row.append(copy, select); capabilities.append(row);
        }
        const limits = document.createElement("fieldset"); limits.className = "ai-policy-limits";
        const limitsLegend = document.createElement("legend"); limitsLegend.textContent = "User bounds"; limits.append(limitsLegend);
        const limitsHelp = document.createElement("p"); limitsHelp.textContent = "Blank values mean no user bound, except operation timeout, which inherits PostgreSQL. Transport pagination, response-size limits, and process ceilings still apply separately."; limits.append(limitsHelp);
        for (const [name, labelText, minimum, maximum, description] of AI_BOUND_FIELDS) {
          const label = document.createElement("label"); const copy = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = labelText;
          const help = document.createElement("small"); help.id = `ai-bound-${name}-help`; help.textContent = description; copy.append(strong, help);
          const input = document.createElement("input"); input.type = "number"; input.name = name; input.min = String(minimum); input.max = String(maximum); input.step = "1";
          input.value = settings.bounds?.[name] == null ? "" : String(settings.bounds[name]); input.placeholder = name === "operationTimeoutMs" ? "Inherit PostgreSQL" : "No user bound";
          input.setAttribute("aria-describedby", help.id); label.append(copy, input); limits.append(label);
        }
        const actions = document.createElement("div"); actions.className = "ai-policy-actions";
        const revision = document.createElement("span"); revision.textContent = `Revision ${settings.revision}`;
        const save = document.createElement("button"); save.type = "submit"; save.className = "button button-primary"; save.textContent = "Save permissions";
        actions.append(revision, save); form.append(capabilities, limits, actions); policySection.append(form);
        form.addEventListener("submit", async event => {
          event.preventDefault();
          if (!form.reportValidity()) return;
          save.disabled = true; elements.settingsStatus.textContent = "Saving AI permissions and limits...";
          const nextPolicy = { schemaVersion: settings.schemaVersion, capabilities: {}, bounds: {} };
          for (const name of Object.keys(settings.capabilities)) nextPolicy.capabilities[name] = form.elements[name].value;
          for (const [name] of AI_BOUND_FIELDS) nextPolicy.bounds[name] = form.elements[name].value === "" ? null : Number(form.elements[name].value);
          try {
            const updated = await request("/api/ai/settings", { method: "PUT", body: JSON.stringify({ expectedRevision: settings.revision, policy: nextPolicy }) });
            state.settings = updated; onPolicyChange(updated); invalidatePendingProposals(); renderSettings();
            elements.settingsStatus.textContent = `Permissions saved at revision ${updated.revision}. Existing pending proposals were revoked; provider connections and chat history were preserved.`;
          } catch (error) {
            if (error.code === "policy_changed") {
              await loadPolicy();
              elements.settingsStatus.textContent = "This policy changed in another tab. Current settings were refreshed; review them before saving again.";
            } else elements.settingsStatus.textContent = error.message;
          } finally { save.disabled = false; }
        });
      }
      const providerSection = document.createElement("section"); providerSection.className = "ai-provider-section";
      const providerHead = document.createElement("div"); providerHead.className = "ai-settings-section-head";
      const providerCopy = document.createElement("div"); const providerTitle = document.createElement("h3"); providerTitle.textContent = "Provider credentials";
      const providerIntro = document.createElement("p"); providerIntro.textContent = "Provider connections are shared by Schemii and Schemer and are stored separately from permissions.";
      providerCopy.append(providerTitle, providerIntro); providerHead.append(providerCopy);
      const providerBody = document.createElement("div"); providerBody.className = "ai-providers"; renderProviders(providerBody);
      providerSection.append(providerHead, providerBody); elements.settingsBody.append(policySection, providerSection);
    }

    function renderSettings() { renderPolicyEditor(); }

    async function loadPolicy(render = settingsDialog.open) {
      try {
        const settings = await request("/api/ai/settings", { method: "GET" });
        state.settings = settings; onPolicyChange(settings);
      } catch (error) {
        state.settings = null;
        if (render) elements.settingsStatus.textContent = `AI permissions unavailable: ${error.message}`;
      }
      if (render) renderSettings();
      return state.settings;
    }

    async function loadStatus(shouldRenderSettings = false) {
      elements.status.textContent = "Checking";
      await loadPolicy(false);
      try {
        const payload = await request("/api/ai/status", { method: "GET" });
        Object.assign(state, { loaded: true, available: payload.available === true || payload.healthy === true, version: payload.version ?? "", providers: payload.providers ?? [], default: payload.default ?? {}, authMethods: payload.authMethods ?? {}, skills: payload.skills ?? [] });
        const connected = state.providers.filter(provider => provider.connected).length;
        elements.status.textContent = state.available ? `${connected} AI provider${connected === 1 ? "" : "s"}` : "Unavailable";
        elements.status.classList.toggle("available", state.available);
        elements.railStatus?.classList.toggle("available", state.available);
        elements.settingsStatus.textContent = state.available ? `OpenCode ${state.version || "available"}` : "OpenCode is unavailable. The application remains usable without AI.";
      } catch (error) {
        Object.assign(state, { loaded: true, available: false, providers: [], default: {} });
        elements.status.textContent = "Offline"; elements.settingsStatus.textContent = `AI unavailable: ${error.message}`;
      }
      renderModels();
      if (shouldRenderSettings || settingsDialog.open) renderSettings();
    }

    function removeEmptyState() { elements.messages.querySelector(".ai-empty-state")?.remove(); }

    async function copyChatText(text) {
      const value = String(text ?? "");
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          return;
        }
      } catch { /* Fall back for browsers that deny the Clipboard API. */ }
      const field = document.createElement("textarea");
      field.value = value; field.readOnly = true; field.tabIndex = -1;
      field.style.position = "fixed"; field.style.opacity = "0"; field.style.pointerEvents = "none";
      document.body.append(field); field.select();
      try {
        if (!document.execCommand?.("copy")) throw new Error("Copy is unavailable");
      } finally {
        field.remove();
      }
    }

    function selectedTextWithin(surface) {
      const selection = window.getSelection?.();
      if (!selection || selection.isCollapsed || !selection.anchorNode || !selection.focusNode) return "";
      if (!surface.contains(selection.anchorNode) || !surface.contains(selection.focusNode)) return "";
      return selection.toString();
    }

    function appendCopyControl(surface, text, label) {
      surface.classList.add("ai-copy-surface");
      const button = shared.createIconButton({ icon: "copy", label, tooltip: label, className: "ai-copy-button", placement: "left" });
      let pointerSelection = "";
      button.addEventListener("pointerdown", () => { pointerSelection = selectedTextWithin(surface); });
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const selected = pointerSelection || selectedTextWithin(surface); pointerSelection = "";
          await copyChatText(selected || text); button.setAttribute("aria-label", "Copied"); button.dataset.tooltip = "Copied"; button.classList.add("copied");
        } catch {
          button.setAttribute("aria-label", "Copy failed"); button.dataset.tooltip = "Copy failed"; button.classList.add("failed");
        } finally {
          button.disabled = false;
          setTimeout(() => {
            if (!button.isConnected) return;
            button.setAttribute("aria-label", label); button.dataset.tooltip = label; button.classList.remove("copied", "failed");
          }, 1800);
        }
      });
      surface.append(button);
      return button;
    }

    function appendMessageCopyControl(body, text, role) {
      return appendCopyControl(body, text, role === "assistant" ? "Copy assistant message" : role === "tool" ? "Copy query result message" : "Copy your message");
    }

    function appendMessage(role, text) {
      removeEmptyState();
      const message = document.createElement("article"); message.className = `ai-message ${role}`;
      const label = document.createElement("span"); label.textContent = role === "assistant" ? "Assistant" : role === "tool" ? "Query result" : "You";
      const value = String(text ?? ""); const body = document.createElement("p"); body.textContent = value;
      appendMessageCopyControl(body, value, role); message.append(label, body); elements.messages.append(message); scrollToEnd();
      return message;
    }

    function hasLocalSettingsAction(error) {
      return Boolean(window.SchemiiShared.allowedLocalErrorAction?.(error));
    }

    function renderStructuredError(error) {
      if (!["capability_unavailable", "application_limitation"].includes(error?.code)) return false;
      removeEmptyState();
      const details = error.payload?.error?.details ?? {};
      const card = document.createElement("section"); card.className = "ai-error-card"; card.setAttribute("role", "alert");
      const title = document.createElement("strong"); title.textContent = error.code === "capability_unavailable" ? "Capability unavailable" : "Application limitation";
      const message = document.createElement("p"); message.textContent = window.SchemiiShared.formatApiError?.(error) || error.message;
      card.append(title, message);
      const capability = details.requiredCapability ?? details.capability;
      if (typeof capability === "string" && capability) {
        const exact = document.createElement("p"); exact.className = "ai-error-detail"; exact.textContent = `Required capability: ${AI_CAPABILITY_LABELS[capability] || capability}`; card.append(exact);
      }
      if (typeof details.guidance === "string" && details.guidance) {
        const guidance = document.createElement("p"); guidance.className = "ai-error-detail"; guidance.textContent = details.guidance; card.append(guidance);
      }
      if (typeof details.safeAlternative === "string" && details.safeAlternative && details.safeAlternative !== details.guidance) {
        const alternative = document.createElement("p"); alternative.className = "ai-error-detail"; alternative.textContent = `Alternative: ${details.safeAlternative}`; card.append(alternative);
      }
      if (hasLocalSettingsAction(error)) {
        const open = document.createElement("button"); open.type = "button"; open.className = "button button-ghost"; open.textContent = "Open AI settings";
        open.addEventListener("click", () => openSettings(open)); card.append(open);
      }
      elements.messages.append(card); scrollToEnd(); return true;
    }

    function renderProposalDiagnostic(diagnostic) {
      if (diagnostic?.code !== "proposal_validation_failed" || typeof diagnostic.message !== "string") return;
      removeEmptyState();
      const card = document.createElement("section"); card.className = "ai-error-card ai-proposal-diagnostic"; card.setAttribute("role", "alert");
      const title = document.createElement("strong"); title.textContent = "Proposal not created";
      const message = document.createElement("p"); message.textContent = diagnostic.message;
      card.append(title, message); elements.messages.append(card);
    }

    function operationError(operation, fallback = "The operation did not succeed") {
      const payload = operation?.error;
      const error = new Error(payload?.message || fallback);
      if (payload && typeof payload === "object") {
        error.code = payload.code;
        error.payload = { error: payload };
        if (window.SchemiiShared.formatApiError) error.message = window.SchemiiShared.formatApiError(error, fallback);
      }
      return error;
    }

    function isCancellationError(error) {
      return ["execution_cancelled", "proposal_cancelled"].includes(error?.code);
    }

    function appendQueryResult(result) {
      removeEmptyState();
      const columns = (result?.columns ?? []).map(column => column?.name ?? String(column));
      const rows = Array.isArray(result?.rows) ? result.rows : [];
      const message = document.createElement("article"); message.className = "ai-message tool ai-query-result";
      const label = document.createElement("span"); label.textContent = "Query result";
      const card = document.createElement("div"); card.className = "ai-query-result-card";
      const meta = document.createElement("div"); meta.className = "ai-query-result-meta";
      const count = document.createElement("strong"); count.textContent = `${rows.length} row${rows.length === 1 ? "" : "s"}`;
      const status = document.createElement("span"); status.textContent = result?.truncated ? `Truncated / ${rows.length} displayed` : "Complete result";
      meta.append(count, status); card.append(meta);
      if (columns.length) {
        const scroll = document.createElement("div"); scroll.className = "ai-query-result-scroll"; scroll.tabIndex = 0;
        scroll.setAttribute("aria-label", `Query result with ${rows.length} row${rows.length === 1 ? "" : "s"}`);
        const table = document.createElement("table"); table.className = "ai-query-result-table";
        const head = document.createElement("thead"); const headingRow = document.createElement("tr");
        for (const column of columns) { const heading = document.createElement("th"); heading.scope = "col"; heading.textContent = column; headingRow.append(heading); }
        head.append(headingRow);
        const body = document.createElement("tbody");
        for (const row of rows) {
          const tableRow = document.createElement("tr");
          columns.forEach((_column, index) => {
            const cell = document.createElement("td"); const value = Array.isArray(row) ? row[index] : null;
            if (value === null) { cell.textContent = "NULL"; cell.className = "null"; }
            else if (value === "") { cell.textContent = "empty"; cell.className = "empty"; }
            else { try { cell.textContent = typeof value === "object" ? JSON.stringify(value) : String(value); } catch { cell.textContent = "[unsupported value]"; } }
            cell.title = cell.textContent; tableRow.append(cell);
          });
          body.append(tableRow);
        }
        table.append(head, body); scroll.append(table); card.append(scroll);
      }
      if (!rows.length) { const empty = document.createElement("p"); empty.className = "ai-query-result-empty"; empty.textContent = "Query returned no rows."; card.append(empty); }
      message.append(label, card); elements.messages.append(message); scrollToEnd(); return message;
    }

    function scrollToEnd() { elements.messages.scrollTop = elements.messages.scrollHeight; }

    function tickProposalActivities() {
      for (const activity of [...proposalActivities]) {
        if (!activity.elapsed.isConnected) { proposalActivities.delete(activity); continue; }
        activity.elapsed.textContent = formatDuration(performance.now() - activity.startedAt);
      }
      if (!proposalActivities.size && proposalActivityTimer !== null) {
        clearInterval(proposalActivityTimer); proposalActivityTimer = null;
      }
    }

    function beginProposalOperation(card, labels = {}) {
      if (!(card instanceof HTMLElement)) throw new TypeError("A proposal card is required");
      card.querySelector(".ai-action-progress")?.remove();
      const startedAt = performance.now();
      const progress = document.createElement("div"); progress.className = "ai-action-progress running";
      const indicator = document.createElement("span"); indicator.className = "ai-action-progress-indicator"; indicator.setAttribute("aria-hidden", "true");
      const status = document.createElement("span"); status.className = "ai-action-progress-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); status.setAttribute("aria-atomic", "true"); status.textContent = labels.runningLabel || "Running proposal";
      const elapsed = document.createElement("time"); elapsed.className = "ai-action-progress-time"; elapsed.setAttribute("aria-hidden", "true"); elapsed.textContent = "0.0s";
      const cancel = typeof labels.onCancel === "function" ? document.createElement("button") : null;
      if (cancel) {
        cancel.type = "button"; cancel.className = "button button-ghost ai-action-cancel"; cancel.textContent = labels.cancelLabel || "Stop";
        cancel.setAttribute("aria-label", labels.cancelAriaLabel || "Cancel running query");
      }
      progress.append(indicator, status, elapsed); if (cancel) progress.append(cancel);
      card.append(progress); card.classList.add("running"); card.setAttribute("aria-busy", "true"); scrollToEnd();
      const activity = { elapsed, startedAt }; proposalActivities.add(activity);
      if (proposalActivityTimer === null) proposalActivityTimer = setInterval(tickProposalActivities, 100);
      let finished = false; let cancellationRequested = false;
      cancel?.addEventListener("click", async () => {
        if (finished || cancellationRequested) return;
        cancellationRequested = true; cancel.disabled = true; progress.classList.add("cancelling");
        status.textContent = labels.cancellingLabel || "Requesting cancellation";
        try {
          const response = await labels.onCancel();
          if (response?.cancellation?.requested === false) status.textContent = labels.alreadyFinishedLabel || "Query already finished; loading its result";
          else status.textContent = labels.cancellationRequestedLabel || "Cancelling query and waiting for rollback";
        } catch (error) {
          cancellationRequested = false; cancel.disabled = false; progress.classList.remove("cancelling");
          status.textContent = labels.cancelFailedLabel || "Cancellation failed; query is still running";
          labels.onCancelError?.(error);
        }
      });
      return {
        finish(outcome = "completed") {
          if (finished) return; finished = true;
          elapsed.textContent = formatDuration(performance.now() - startedAt); proposalActivities.delete(activity); tickProposalActivities();
          const failed = outcome === "failed"; const warning = outcome === "warning"; const cancelled = outcome === "cancelled";
          progress.className = `ai-action-progress ${failed ? "failed" : warning ? "warning" : cancelled ? "cancelled" : "completed"}`;
          const terminalLabel = failed ? (labels.failedLabel || "Proposal failed") : warning ? (labels.warningLabel || "Proposal completed; refresh failed") : cancelled ? (labels.cancelledLabel || "Query cancelled") : (labels.completedLabel || "Proposal completed");
          status.textContent = `${terminalLabel} in ${elapsed.textContent}`;
          if (cancel) cancel.hidden = true;
          card.classList.remove("running"); card.removeAttribute("aria-busy");
          elapsed.setAttribute("datetime", `PT${Math.max(0, (performance.now() - startedAt) / 1000).toFixed(3)}S`);
        }
      };
    }

    function beginActivity(modelName) {
      removeEmptyState();
      const startedAt = performance.now();
      const details = document.createElement("details"); details.className = "ai-run active"; details.open = true; details.setAttribute("role", "status");
      const summary = document.createElement("summary");
      const indicator = document.createElement("span"); indicator.className = "ai-progress-grid"; indicator.setAttribute("aria-hidden", "true");
      for (let index = 0; index < 25; index += 1) { const dot = document.createElement("i"); dot.style.setProperty("--dot-index", index); indicator.append(dot); }
      const title = document.createElement("span"); title.className = "ai-run-title shimmer"; title.textContent = "Starting assistant";
      const elapsed = document.createElement("time"); elapsed.className = "ai-run-time"; elapsed.textContent = "0.0s";
      const steps = document.createElement("div"); steps.className = "ai-run-steps";
      summary.append(indicator, title, elapsed); details.append(summary, steps); elements.messages.append(details); scrollToEnd();
      const stageElements = new Map(); let retryAt = null; let finished = false;
      const setStage = (key, label, status = "running") => {
        const safeStatus = ["running", "completed", "error"].includes(status) ? status : "running";
        let row = stageElements.get(key);
        if (!row) {
          row = document.createElement("div"); row.className = "ai-run-step";
          const marker = document.createElement("span"); marker.className = "ai-run-step-marker"; marker.setAttribute("aria-hidden", "true");
          const copy = document.createElement("span"); copy.className = "ai-run-step-copy"; row.append(marker, copy); steps.append(row); stageElements.set(key, row);
        }
        row.className = `ai-run-step ${safeStatus}`; row.querySelector(".ai-run-step-copy").textContent = label;
      };
      setStage("request", `Opening ${modelName || "selected model"}`);
      const tick = () => { elapsed.textContent = formatDuration(performance.now() - startedAt); if (retryAt) title.textContent = `Retrying in ${Math.max(0, Math.ceil((retryAt - Date.now()) / 1000))}s`; };
      const timer = setInterval(tick, 100);
      return {
        update(event) {
          if (finished || !event || typeof event !== "object") return;
          if (event.type === "part") setStage("model", "Model started", "completed");
          if (event.type === "connection") {
            if (event.state === "connected") { title.textContent = "Waiting for model"; setStage("request", `Connected to ${modelName || "selected model"}`, "completed"); }
            else { title.textContent = "Working without live updates"; setStage("stream", "Live activity disconnected", "error"); }
          } else if (event.type === "session" && event.state === "busy") { retryAt = null; title.textContent = "Agent is working"; setStage("model", "Model started"); }
          else if (event.type === "session" && event.state === "retry") { retryAt = Number.isFinite(event.retryAt) ? event.retryAt : null; title.textContent = "Retrying provider"; setStage("retry", `Provider retry ${Number.isInteger(event.attempt) ? event.attempt : ""}`.trim()); }
          else if (event.type === "session" && event.state === "error") { title.textContent = "Provider reported an issue"; setStage("provider-error", "Provider issue detected", "error"); }
          else if (event.type === "session" && event.state === "idle") { retryAt = null; title.textContent = "Finalizing response"; setStage("model", "Model finished", "completed"); }
          else if (event.type === "compaction") { title.textContent = "Compacting context"; setStage("compaction", "Context compacted", event.state === "completed" ? "completed" : "running"); }
          else if (event.type === "part" && event.kind === "reasoning") { title.textContent = event.state === "completed" ? "Preparing response" : "Reasoning"; setStage(event.key, "Reasoning", event.state); }
          else if (event.type === "part" && event.kind === "text") { title.textContent = "Writing response"; setStage(event.key, "Writing response", event.state); }
          else if (event.type === "part" && event.kind === "tool" && toolLabels[event.tool]) { title.textContent = toolLabels[event.tool]; setStage(event.key, toolLabels[event.tool], event.state); }
          else if (event.type === "part" && event.kind === "skill" && skillLabels[event.skill]) { title.textContent = `Loading ${skillLabels[event.skill]}`; setStage(event.key, skillLabels[event.skill], event.state); }
          scrollToEnd();
        },
        finish(outcome) {
          if (finished) return; clearInterval(timer); retryAt = null; tick(); finished = true;
          const failed = outcome === "error"; details.classList.remove("active"); details.classList.add(failed ? "failed" : "completed"); title.classList.remove("shimmer"); title.textContent = failed ? "Agent stopped" : "Assistant response ready";
          setStage("model", failed ? "Response failed" : "Model finished", failed ? "error" : "completed"); if (!failed) setStage("delivered", "Response delivered", "completed");
          if (!failed) setTimeout(() => { details.open = false; }, 650);
        }
      };
    }

    async function readActivity(sessionId, onEvent, signal) {
      const response = await fetchActivity(`/api/ai/sessions/${encodeURIComponent(sessionId)}/activity`, { method: "GET", signal });
      if (!response.body) throw new Error("Agent activity stream is unavailable");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n"); buffer = lines.pop() || "";
        for (const line of lines) if (line.trim()) try { onEvent(JSON.parse(line)); } catch { /* Ignore malformed records. */ }
        if (done) break;
      }
    }

    function startActivityStream(sessionId, activity) {
      const controller = new AbortController(); let resolveReady; let readyResolved = false;
      const ready = new Promise(resolve => { resolveReady = resolve; });
      const markReady = () => { if (!readyResolved) { readyResolved = true; resolveReady(); } };
      const done = readActivity(sessionId, event => { if (event?.type === "connection") markReady(); activity.update(event); }, controller.signal)
        .catch(error => { markReady(); if (error.name !== "AbortError") activity.update({ type: "connection", state: "disconnected" }); }).finally(markReady);
      return { ready, done, abort: () => controller.abort() };
    }

    function renderReasoning(part) {
      if (!part.text) return;
      const details = document.createElement("details"); details.className = "ai-reasoning";
      const summary = document.createElement("summary"); summary.textContent = `Thought${Number.isFinite(part.durationMs) ? ` / ${formatDuration(part.durationMs)}` : ""}`;
      const body = document.createElement("p"); body.textContent = part.text; details.append(summary, body); elements.messages.append(details);
    }

    function renderToolPart(part) {
      const label = part.type === "skill" ? skillLabels[part.skill] : toolLabels[part.tool];
      if (!label) return;
      const status = ["pending", "running", "completed", "error"].includes(part.status) ? part.status : "completed";
      const card = document.createElement("div"); card.className = `ai-tool-part ${status}`;
      const marker = document.createElement("span"); marker.className = "ai-tool-marker"; marker.setAttribute("aria-hidden", "true");
      const name = document.createElement("strong"); name.textContent = label;
      const statusNode = document.createElement("span"); statusNode.textContent = status;
      card.append(marker, name, statusNode); elements.messages.append(card);
    }

    async function proposalRequest(proposal, operation, body) {
      if (!proposal?.proposalId || !proposal?.sessionId) throw new Error("The proposal authority is unavailable");
      return request(`/api/ai/sessions/${encodeURIComponent(proposal.sessionId)}/proposals/${encodeURIComponent(proposal.proposalId)}/${operation}`, {
        method: "POST", body: JSON.stringify(body),
      });
    }

    async function cancelProposal(proposal) {
      if (!proposal?.proposalId || !proposal?.sessionId) throw new Error("The proposal authority is unavailable");
      return request(`/api/ai/sessions/${encodeURIComponent(proposal.sessionId)}/proposals/${encodeURIComponent(proposal.proposalId)}/execution`, {
        method: "DELETE",
      });
    }

    function proposalContextIsCurrent(capture) {
      const currentAccess = elements.access.value;
      const currentCapture = getContext(currentAccess);
      const capturedAccess = capture?.accessLevel ?? currentAccess;
      return Boolean(currentCapture && contextKey(capture, capturedAccess) === contextKey(currentCapture, currentAccess));
    }

    async function executeProposal(proposal, capture) {
      if (!proposalContextIsCurrent(capture)) {
        throw new Error("The application context changed. Start a new conversation before confirming this action.");
      }
      if (prepareProposalExecution) await prepareProposalExecution({ proposal, capture });
      if (!proposalContextIsCurrent(capture)) {
        throw new Error("The application context changed while pending edits were saved. Request a fresh proposal.");
      }
      const context = buildProposalClaimPayload ? buildProposalClaimPayload(capture, elements.access.value) : {};
      const policy = proposal.policyBinding ?? {};
      const mode = policy.effectiveMode === "once_per_chat" ? "once_per_chat" : "every_action";
      const confirmation = { accepted: true, mode };
      const body = buildProposalExecutionPayload
        ? buildProposalExecutionPayload({ proposal, confirmation })
        : { ...context, policyRevision: policy.policyRevision, confirmation };
      try {
        const response = await proposalRequest(proposal, "execute", body);
        if (response.operation?.state === "running") return waitForOperation(proposal, response.operation);
        if (response.operation?.state === "uncertain") {
          const reconciled = await proposalRequest(proposal, "reconcile", context);
          return reconciled.operation?.state === "running" ? waitForOperation(proposal, reconciled.operation) : reconciled;
        }
        return response;
      } catch (error) {
        try {
          const response = await proposalRequest(proposal, "reconcile", context);
          return response.operation?.state === "running" ? waitForOperation(proposal, response.operation) : response;
        }
        catch (reconcileError) {
          if (reconcileError.code === "operation_not_started") throw error;
          reconcileError.message = "Outcome unknown. Reconnect or reload to check this operation.";
          throw reconcileError;
        }
      }
    }

    async function waitForOperation(proposal, operation) {
      for (let attempt = 0; attempt < 360 && operation?.state === "running"; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, document.hidden ? 2000 : 500));
        const response = await request(`/api/ai/sessions/${encodeURIComponent(proposal.sessionId)}/operations/${encodeURIComponent(operation.id)}/status`, { method: "GET" });
        operation = response.operation;
      }
      if (operation?.state === "running") throw new Error("Operation is still running. Reopen this conversation to check its status.");
      return { operation };
    }

    function renderCancellationRecovery(proposal) {
      const card = document.createElement("section"); card.className = "ai-action-card";
      const title = document.createElement("strong"); title.textContent = "Read-only query";
      const detail = document.createElement("p"); detail.textContent = "Cancellation was requested. Waiting for PostgreSQL rollback and the durable operation outcome.";
      card.append(title, detail); elements.messages.append(card);
      const activity = beginProposalOperation(card, {
        runningLabel: "Cancelling query", cancelledLabel: "Query cancelled",
        warningLabel: "Cancellation outcome requires reconciliation", failedLabel: "Cancellation status failed",
      });
      void (async () => {
        try {
          const response = proposal.operation == null
            ? { operation: { state: "cancelled" } }
            : proposal.operation.state === "running"
            ? await waitForOperation(proposal, proposal.operation)
            : { operation: proposal.operation };
          if (response.operation?.state === "cancelled") activity.finish("cancelled");
          else if (response.operation?.state === "uncertain") activity.finish("warning");
          else if (response.operation?.state === "succeeded") activity.finish("completed");
          else activity.finish("failed");
        } catch (error) {
          activity.finish("failed");
          const message = document.createElement("p"); message.className = "ai-action-error"; message.textContent = error.message; card.append(message);
        }
      })();
    }

    function renderGenericAction(proposal, capture) {
      const action = proposal.action;
      let normalized;
      try { normalized = validateAction?.(action, capture); } catch { return; }
      if (!normalized) return;
      const card = document.createElement("section"); card.className = `ai-action-card${normalized.destructive ? " destructive" : ""}`;
      const title = document.createElement("strong"); title.textContent = normalized.title;
      const summary = document.createElement("p"); summary.textContent = normalized.summary;
      card.append(title, summary);
      if (normalized.review) { const review = document.createElement("pre"); review.textContent = normalized.review; appendCopyControl(review, normalized.review, "Copy proposal details"); card.append(review); }
      const button = document.createElement("button"); button.type = "button"; button.className = "button button-primary"; button.textContent = normalized.buttonLabel || (normalized.destructive ? "Review deletion" : "Review & confirm");
      button.addEventListener("click", async () => {
        const consequence = normalized.destructive ? "\n\nThis action is destructive and cannot be undone." : "";
        if (!confirm(`${normalized.summary}${consequence}\n\nConfirm this reviewed action?`)) return;
        card.querySelectorAll(".ai-action-error").forEach(error => error.remove()); button.disabled = true; button.textContent = "Running...";
        const cancellableQuery = normalized.action?.type === "read_query";
        const activity = beginProposalOperation(card, cancellableQuery ? {
          runningLabel: "Running query", completedLabel: "Query completed", failedLabel: "Query failed",
          onCancel: () => cancelProposal(proposal),
        } : {});
        let operationSucceeded = false;
        try {
          const response = await executeProposal(proposal, capture);
          const operation = response.operation;
          if (operation?.state !== "succeeded") throw operationError(operation);
          operationSucceeded = true;
          if (!proposalContextIsCurrent(capture)) { activity.finish("completed"); button.textContent = "Completed"; return; }
          const appliedLabel = await handleOperationResult?.(operation.result, capture);
          activity.finish("completed");
          button.textContent = appliedLabel || normalized.appliedLabel || "Applied"; card.classList.add("applied");
        } catch (error) {
          const cancelled = isCancellationError(error);
          activity.finish(cancelled ? "cancelled" : operationSucceeded ? "warning" : "failed");
          button.disabled = operationSucceeded || cancelled;
          button.textContent = cancelled ? "Cancelled" : operationSucceeded ? "Completed" : normalized.buttonLabel || (normalized.destructive ? "Review deletion" : "Review & confirm");
          if (!cancelled && !renderStructuredError(error)) { const detail = document.createElement("p"); detail.className = "ai-action-error"; detail.textContent = error.message; card.append(detail); }
        }
      });
      card.append(button); elements.messages.append(card);
    }

    function renderResponse(response, capture, sessionId = state.sessionId) {
      let renderedText = false;
      for (const part of response.parts ?? []) {
        if (part?.type === "text" && part.text) { appendMessage("assistant", part.text); renderedText = true; }
        else if (part?.type === "reasoning") renderReasoning(part);
        else if (part?.type === "tool" || part?.type === "skill") renderToolPart(part);
      }
      if (!renderedText && response.text) appendMessage("assistant", response.text);
      for (const diagnostic of response.proposalDiagnostics ?? []) renderProposalDiagnostic(diagnostic);
      for (const item of response.proposals ?? []) {
        const proposal = { ...item, sessionId };
        if (proposal.operation) {
          if (proposal.operation.state === "succeeded") handleOperationResult?.(proposal.operation.result, capture);
          else if (proposal.operation.error?.message && !renderStructuredError(operationError(proposal.operation))) appendMessage("assistant", `Automatic action failed: ${proposal.operation.error.message}`);
          continue;
        }
        if (renderAction) renderAction(proposal, capture, api);
        else renderGenericAction(proposal, capture);
      }
      scrollToEnd();
    }

    async function ensureSession(model, capture, key) {
      if (state.sessionId && state.contextKey === key) return state.sessionId;
      state.sessionId = null;
      if (typeof buildSessionPayload !== "function") throw new Error("The application session contract is unavailable");
      const payload = buildSessionPayload(capture, elements.access.value, model);
      const session = await request("/api/ai/sessions", { method: "POST", body: JSON.stringify(payload) });
      state.sessionId = session.id; state.contextKey = key;
      return session.id;
    }

    async function sendMessage(text, renderedRole = "user", messageOptions = {}) {
      if (!text.trim() || state.busy) return;
      let model;
      try { model = JSON.parse(elements.model.value); } catch { return; }
      const accessLevel = elements.access.value;
      const capture = messageOptions.capture ?? getContext(accessLevel);
      if (!capture) return;
      const key = contextKey(capture, accessLevel);
      const requestGeneration = ++state.requestGeneration;
      if (renderedRole === "user") appendMessage("user", text);
      const activity = beginActivity(elements.model.selectedOptions[0]?.textContent || model.modelId);
      let stream = null; setBusy(true);
      try {
        const sessionId = await ensureSession(model, capture, key);
        if (requestGeneration !== state.requestGeneration) return;
        stream = startActivityStream(sessionId, activity);
        await Promise.race([stream.ready, new Promise(resolve => setTimeout(resolve, 1500))]);
        const extras = messageOptions.extras ?? {};
        const payload = buildMessagePayload ? buildMessagePayload({ text, model, capture, accessLevel, extras }) : { text, model, accessLevel, ...extras };
        const response = await request(`/api/ai/sessions/${encodeURIComponent(sessionId)}/messages`, { method: "POST", body: JSON.stringify(payload) });
        if (requestGeneration !== state.requestGeneration) return;
        await Promise.race([stream.done, new Promise(resolve => setTimeout(resolve, 750))]);
        renderResponse(response, capture, sessionId); activity.finish("completed");
      } catch (error) {
        if (requestGeneration === state.requestGeneration) {
          activity.finish("error"); if (!renderStructuredError(error)) appendMessage("assistant", `AI unavailable: ${error.message}`);
          if (["provider_timeout", "provider_empty_response", "opencode_error"].includes(error.code)) await loadStatus();
        }
      } finally { stream?.abort(); if (requestGeneration === state.requestGeneration) setBusy(false); }
    }

    function resetConversation(copy = labels.newChatCopy || "Proposals will use the current application context.") {
      if (state.busy) return;
      proposalActivities.clear(); tickProposalActivities();
      state.requestGeneration += 1; state.sessionId = null; state.contextKey = null; elements.messages.replaceChildren(); onNewChat();
      const empty = document.createElement("div"); empty.className = "ai-empty-state";
      const title = document.createElement("strong"); title.textContent = "New conversation";
      const paragraph = document.createElement("p"); paragraph.dataset.aiEmptyCopy = ""; paragraph.textContent = copy;
      empty.append(title, paragraph); elements.messages.append(empty);
    }

    function invalidateContext(copy = "Application context changed. The next message starts a new isolated conversation.") {
      proposalActivities.clear(); tickProposalActivities();
      state.requestGeneration += 1; state.sessionId = null; state.contextKey = null; setBusy(false); elements.messages.replaceChildren(); onNewChat();
      const empty = document.createElement("div"); empty.className = "ai-empty-state";
      const title = document.createElement("strong"); title.textContent = "New conversation";
      const paragraph = document.createElement("p"); paragraph.dataset.aiEmptyCopy = ""; paragraph.textContent = copy;
      empty.append(title, paragraph); elements.messages.append(empty);
    }

    function formatHistoryDate(value) {
      if (!Number.isFinite(value)) return "Saved conversation";
      const date = new Date(value); return Number.isNaN(date.getTime()) ? "Saved conversation" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    }

    async function restoreSession(session, binding, historyQuery, historyKey) {
      try {
        const currentKey = contextKey(getContext(elements.access.value), elements.access.value);
        if (currentKey !== historyKey || !canViewSession(binding, currentKey, elements.access.value)) throw new Error("The application context changed; reopen history before continuing");
        const history = await request(`/api/ai/sessions/${encodeURIComponent(session.id)}/messages?${historyQuery}`, { method: "GET" });
        proposalActivities.clear(); tickProposalActivities();
        state.requestGeneration += 1; elements.messages.replaceChildren();
        for (const message of history.messages ?? []) {
          if (message.role === "user") appendMessage("user", message.text);
          if (message.role === "assistant") renderResponse({ parts: message.parts ?? [], text: message.text ?? "", actions: [], proposals: [] }, null, session.id);
        }
        const restoredCapture = getContext(elements.access.value);
        for (const proposal of history.pendingProposals ?? []) {
          if (proposal.cancellationRequested || proposal.operation?.cancellationRequested) renderCancellationRecovery(proposal);
          else if (renderAction) renderAction(proposal, restoredCapture, api);
          else renderGenericAction(proposal, restoredCapture);
        }
        if (!elements.messages.children.length) appendMessage("assistant", "This saved conversation has no displayable messages.");
        const resumable = binding.key == null || binding.key === currentKey;
        state.sessionId = resumable ? session.id : null; state.contextKey = resumable ? currentKey : null;
        const modelValue = normalizeStoredModel(JSON.stringify(history.model ?? {}));
        if (modelValue && [...elements.model.options].some(option => option.value === modelValue)) { elements.model.value = modelValue; rememberModel(); }
        setBusy(false); historyDialog.close(); setOpen(true); scrollToEnd(); elements.prompt.focus();
        if (!resumable) appendMessage("assistant", "This history is read-only in the current context. Sending starts a new isolated chat.");
      } catch (error) { elements.historyList.textContent = `Could not open chat: ${error.message}`; }
    }

    async function renderHistory() {
      elements.historyList.replaceChildren();
      const loading = document.createElement("p"); loading.className = "ai-history-empty"; loading.textContent = "Loading conversations..."; elements.historyList.append(loading);
      try {
        const accessLevel = elements.access.value;
        const capture = getContext(accessLevel);
        if (!capture) throw new Error("Select an application context before opening history");
        const historyKey = contextKey(capture, accessLevel);
        const historyQuery = new URLSearchParams(buildHistoryQuery ? buildHistoryQuery(capture, accessLevel) : {}).toString();
        if (!historyQuery) throw new Error("AI history context is unavailable");
        const history = await request(`/api/ai/sessions?${historyQuery}`, { method: "GET" }); elements.historyList.replaceChildren();
        if (!(history.sessions ?? []).length) { loading.textContent = "No saved conversations yet."; elements.historyList.append(loading); return; }
        for (const session of history.sessions) {
          const binding = parseSession(session);
          const item = document.createElement("article"); item.className = `ai-history-item${session.id === state.sessionId ? " current" : ""}`;
          const copy = document.createElement("div"); copy.className = "ai-history-copy";
          const title = document.createElement("strong"); title.textContent = binding.title;
          const saved = `${formatHistoryDate(session.updatedAt ?? session.createdAt)}${session.id === state.sessionId ? " / Current" : ""}`;
          const contextTitle = typeof session.contextTitle === "string" && session.contextTitle && session.contextTitle !== binding.title ? session.contextTitle : "";
          const date = document.createElement("span"); date.textContent = contextTitle ? `${contextTitle} · ${saved}` : saved;
          const open = document.createElement("button"); open.type = "button"; open.className = "button button-ghost"; open.textContent = session.id === state.sessionId ? "Reopen" : "Open"; open.addEventListener("click", () => restoreSession(session, binding, historyQuery, historyKey));
          const rename = shared.createIconButton({ icon: "edit", label: `Rename chat “${binding.title}”`, className: "ai-history-rename", placement: "left" });
          rename.addEventListener("click", () => {
            if (copy.querySelector(".ai-history-rename-form")) return;
            const form = document.createElement("form"); form.className = "ai-history-rename-form";
            const input = document.createElement("input"); input.type = "text"; input.maxLength = 80; input.value = binding.title; input.setAttribute("aria-label", "Chat title");
            const save = document.createElement("button"); save.type = "submit"; save.className = "button button-primary"; save.textContent = "Save";
            const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "button button-ghost"; cancel.textContent = "Cancel";
            const status = document.createElement("span"); status.className = "ai-history-rename-status"; status.setAttribute("role", "status");
            const finish = () => {
              form.remove(); title.hidden = false; date.hidden = false; rename.hidden = false; open.disabled = false; remove.disabled = false;
              rename.setAttribute("aria-label", `Rename chat “${binding.title}”`); rename.dataset.tooltip = rename.getAttribute("aria-label");
            };
            cancel.addEventListener("click", finish);
            form.addEventListener("submit", async event => {
              event.preventDefault(); const nextTitle = input.value.trim();
              if (!nextTitle) { status.textContent = "Enter a title."; input.focus(); return; }
              save.disabled = true; cancel.disabled = true; status.textContent = "Saving…";
              try {
                const updated = await request(`/api/ai/sessions/${encodeURIComponent(session.id)}/title`, { method: "PUT", body: JSON.stringify({ title: nextTitle }) });
                session.title = updated.title; binding.title = updated.title; title.textContent = updated.title; finish();
              } catch (error) {
                save.disabled = false; cancel.disabled = false; status.textContent = error.message; input.focus();
              }
            });
            form.append(input, save, cancel, status); title.hidden = true; date.hidden = true; rename.hidden = true; open.disabled = true; remove.disabled = true; copy.append(form); input.focus(); input.select();
          });
          const remove = document.createElement("button"); remove.type = "button"; remove.className = "button button-ghost ai-history-delete"; remove.textContent = "Delete";
          remove.addEventListener("click", async () => {
            if (!confirm(`Permanently delete chat “${binding.title}”?`)) return;
            await request(`/api/ai/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
            if (state.sessionId === session.id) resetConversation(); await renderHistory();
          });
          copy.append(title, date); item.append(copy, rename, open, remove); elements.historyList.append(item);
        }
      } catch (error) { loading.textContent = `Could not load chat history: ${error.message}`; }
    }

    const api = Object.freeze({
      appendMessage, appendQueryResult, appendCopyControl, renderResponse, sendMessage, scrollToEnd, invalidateContext,
      executeProposal, cancelProposal, beginProposalOperation, proposalContextIsCurrent, openSettings, operationError, isCancellationError, renderError: renderStructuredError,
      get accessLevel() { return elements.access.value; }, get state() { return state; }, get settings() { return state.settings; },
    });

    async function openSettings(opener = document.activeElement) {
      if (!settingsDialog.open) {
        settingsReturnFocus = opener instanceof HTMLElement ? opener : elements.settings;
        setNestedDialogOpen(true);
        settingsDialog.showModal();
      }
      await loadStatus(true);
      requestAnimationFrame(() => settingsDialog.querySelector("select, input, button")?.focus());
    }

    function closeSettings(restoreFocus = true) {
      if (settingsDialog.open) settingsDialog.close();
      const target = settingsReturnFocus; settingsReturnFocus = null;
      setNestedDialogOpen(false);
      if (restoreFocus && target?.isConnected) requestAnimationFrame(() => target.focus());
    }
    function closeHistory(restoreFocus = true) {
      if (historyDialog.open) historyDialog.close();
      const target = historyReturnFocus; historyReturnFocus = null;
      setNestedDialogOpen(false);
      if (restoreFocus && target?.isConnected) requestAnimationFrame(() => target.focus());
    }
    trigger.addEventListener("click", () => setOpen(!root.classList.contains("open")));
    elements.close.addEventListener("click", () => setOpen(false));
    elements.newChat.addEventListener("click", () => resetConversation());
    elements.history.addEventListener("click", async () => {
      if (state.busy) return;
      historyReturnFocus = elements.history;
      setNestedDialogOpen(true);
      historyDialog.showModal();
      await renderHistory();
      requestAnimationFrame(() => historyDialog.querySelector("button")?.focus());
    });
    elements.settings.addEventListener("click", () => openSettings(elements.settings));
    settingsClose.addEventListener("click", closeSettings);
    settingsDialog.addEventListener("cancel", event => { event.preventDefault(); closeSettings(); });
    historyClose.addEventListener("click", closeHistory);
    historyDialog.addEventListener("cancel", event => { event.preventDefault(); closeHistory(); });
    elements.model.addEventListener("change", () => { rememberModel(); setBusy(state.busy); });
    elements.access.addEventListener("change", () => {
      resetConversation("Access changed. The next message starts a new conversation bound to this disclosure level and target.");
      onAccessChange(elements.access.value, api);
    });
    elements.form.addEventListener("submit", event => { event.preventDefault(); const text = elements.prompt.value.trim(); if (!text) return; elements.prompt.value = ""; sendMessage(text); });
    elements.prompt.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.form.requestSubmit(); } });
    if (panelModal) root.addEventListener("keydown", trapPanelFocus);
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape" || !root.classList.contains("open") || settingsDialog.open || historyDialog.open || state.busy) return;
      if (!panelModal && !root.contains(document.activeElement)) return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    });
    root.inert = true;
    return Object.freeze({ ...api, open: () => setOpen(true), close: () => setOpen(false), refresh: loadStatus, refreshPolicy: loadPolicy, reset: resetConversation, normalizeStoredModel });
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createAiAssistant, normalizeStoredAiModel: normalizeStoredModel, aiContextCacheKey, boundedAiQueryResult });
})();
