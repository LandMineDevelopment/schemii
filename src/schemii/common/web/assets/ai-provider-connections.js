import { element } from "./dom.js";
import { requestJson } from "./http.js";
import { aiStatusPath, apiKeyProviderDetails, providerConnectionState, zenConnectionNotice, sharedCodexConnectionNotice, sharedCodexPolicy } from "./ai-presentation.js";

const LOGIN_LIFETIME = 10 * 60 * 1000;
const loginPath = id => `/api/v1/ai/prototype/logins/${encodeURIComponent(id)}`;
const scopeKey = scope => JSON.stringify([scope.product, scope.resourceId || ""]);

/** Provider connections only; conversations, preferences and permissions stay in each product. */
export function createProviderConnections({
  container, getScope, getSelectedProvider = () => null, onRuntime, onStatus,
  classes = {}, expandUnavailable = false, confirmDisconnect = () => true,
  request = requestJson,
}) {
  let runtime = null, key = null, epoch = 0, refreshSequence = 0;
  let login = null, timer = null, mutation = null, disposed = false, actionSequence = 0;
  const secretInputs = new Set();
  const css = { card: "model-ai-provider", auth: "model-ai-auth", copy: "model-ai-muted", button: "ui-button", ...classes };
  const text = (tag, value, className) => element(tag, { text: value, className });
  const button = (label, action) => {
    const node = element("button", { type: "button", text: label, className: css.button });
    node.addEventListener("click", action);
    return node;
  };
  function clearSecrets() {
    for (const input of secretInputs) input.value = "";
    secretInputs.clear();
  }
  function stopTimer() { globalThis.clearTimeout(timer); timer = null; }
  function report(message) { if (!disposed) onStatus?.(message); }
  // Cancellation always targets the captured login, never a replacement attempt.
  async function removeLogin(current) {
    if (current.id) await request(loginPath(current.id), { method: "DELETE" });
  }
  function abandonLogin() {
    const previous = login;
    login = null; stopTimer();
    if (previous?.id) void removeLogin(previous).catch(() => {});
    // A pending creation cleans up its exact returned ID in startLogin.
  }
  function syncScope() {
    const scope = getScope(), next = scopeKey(scope);
    if (next !== key) {
      epoch++; refreshSequence++; actionSequence++; key = next;
      clearSecrets(); abandonLogin(); mutation = null; runtime = null;
      container.replaceChildren();
    }
    return scope;
  }
  function owns(ticket) {
    return !disposed && ticket === epoch && key === scopeKey(getScope());
  }
  function ownsLogin(current) { return owns(current.epoch) && login === current; }
  async function refresh({ refresh = false } = {}) {
    if (disposed) return null;
    const scope = syncScope(), ticket = epoch, sequence = ++refreshSequence;
    try {
      const next = await request(aiStatusPath(scope.product, scope.resourceId, refresh), { timeoutMs: refresh ? 20_000 : 10_000 });
      if (!owns(ticket) || sequence !== refreshSequence) return null;
      runtime = next; onRuntime?.(next); render();
      if (refresh && (next.healthy === false || next.providers?.some(provider => provider.catalogError))) {
        throw new Error(next.message || "Could not check available models. Try again.");
      }
      return next;
    } catch (error) {
      if (!owns(ticket) || sequence !== refreshSequence) return null;
      throw error;
    }
  }
  async function changeCredential(provider, body = null) {
    syncScope();
    if (disposed || mutation || login) { if (body) body.apiKey = ""; return; }
    const current = { epoch, action: ++actionSequence }, connected = body !== null;
    mutation = current; render();
    try {
      await request(`/api/v1/ai/credentials/${encodeURIComponent(provider.id)}`, { method: connected ? "POST" : "DELETE", ...(connected ? { body } : {}) });
      if (!owns(current.epoch) || mutation !== current) return;
      await refresh();
      if (owns(current.epoch) && mutation === current) report(`${provider.name} ${connected ? "connected" : "disconnected"}.`);
    } catch (error) {
      if (owns(current.epoch) && mutation === current) report(error.message);
    } finally {
      if (body) body.apiKey = "";
      if (owns(current.epoch) && mutation === current) { mutation = null; render(); }
    }
  }
  async function cancelLogin(current) {
    if (!ownsLogin(current)) return;
    login = null; actionSequence++; const cancellation = actionSequence; stopTimer(); render(); report("Sign-in cancelled.");
    try { await removeLogin(current); }
    catch (error) { if (owns(current.epoch) && cancellation === actionSequence) report(`Could not cancel sign-in: ${error.message}`); }
  }
  async function pollLogin(current) {
    if (!ownsLogin(current)) return;
    stopTimer();
    if (Date.now() >= current.deadline) {
      login = null; render(); report("Sign-in expired. Connect again.");
      void removeLogin(current).catch(() => {});
      return;
    }
    try {
      const state = await request(loginPath(current.id));
      if (!ownsLogin(current)) return;
      if (state.status === "succeeded") {
        login = null; render();
        await refresh();
        if (owns(current.epoch) && current.action === actionSequence) report("Codex connected to your account.");
        return;
      }
      if (state.status !== "pending") throw new Error(state.message || "Sign-in expired. Connect again.");
      current.userCode = state.userCode;
      current.verificationUrl = state.verificationUrl;
      const expiresAt = typeof state.expiresAt === "number" ? state.expiresAt : Date.parse(state.expiresAt);
      if (Number.isFinite(expiresAt)) current.deadline = Math.min(current.deadline, expiresAt);
      render();
      timer = globalThis.setTimeout(() => void pollLogin(current), Math.max(0, Math.min(2000, current.deadline - Date.now())));
    } catch (error) {
      if (!owns(current.epoch) || current.action !== actionSequence) return;
      if (login === current) login = null;
      stopTimer(); render(); report(error.message);
    }
  }
  async function startLogin() {
    syncScope();
    if (disposed || login || mutation) return;
    const current = { epoch, action: ++actionSequence, deadline: Date.now() + LOGIN_LIFETIME };
    login = current; render(); report("Preparing device sign-in…");
    try {
      const result = await request("/api/v1/ai/prototype/login", { method: "POST" });
      current.id = result.id;
      if (!ownsLogin(current)) { await removeLogin(current); return; }
      render(); void pollLogin(current);
    } catch (error) {
      if (!ownsLogin(current)) return;
      login = null; render(); report(error.message);
    }
  }
  function authForm(provider) {
    const details = apiKeyProviderDetails[provider.id];
    const input = element("input", { attrs: { type: "password", autocomplete: "off", required: "", "aria-label": details.label } });
    secretInputs.add(input);
    const submit = element("button", { type: "submit", text: "Connect", className: css.button });
    input.disabled = submit.disabled = Boolean(mutation || login);
    const form = element("form", { className: css.auth }, [element("label", {}, [text("span", details.label), input]), submit]);
    form.addEventListener("submit", event => {
      event.preventDefault(); event.stopPropagation();
      const body = { apiKey: input.value }; input.value = "";
      if (!body.apiKey.trim()) return;
      void changeCredential(provider, body);
    });
    return form;
  }
  function loginForm() {
    const form = element("div", { className: css.auth });
    if (!login) {
      const connect = button("Connect Codex", () => void startLogin());
      connect.disabled = Boolean(mutation); form.append(connect); return form;
    }
    const current = login;
    form.append(text("p", current.userCode ? "Enter this code at OpenAI to connect your account:" : "Preparing device sign-in…"));
    if (current.userCode) form.append(text("strong", current.userCode));
    // The server allows only this exact trusted device-login destination.
    if (current.verificationUrl === "https://auth.openai.com/codex/device") {
      form.append(element("a", { text: "Continue at OpenAI", attrs: { href: current.verificationUrl, target: "_blank", rel: "noopener noreferrer" } }));
    }
    form.append(button("Cancel sign-in", () => void cancelLogin(current)));
    return form;
  }
  function render() {
    if (disposed) return;
    syncScope(); clearSecrets();
    const expanded = new Set([...container.children].filter(card => card.open).map(card => card.dataset.providerId));
    const cards = (runtime?.providers || []).map(provider => {
      const card = element("details", { className: css.card, dataset: { providerId: provider.id } });
      card.open = expanded.has(provider.id) || provider.id === getSelectedProvider() || expandUnavailable && !provider.available || provider.id === "openai-codex" && Boolean(login);
      const heading = element("summary", { className: css.heading }, [text("strong", provider.name), text("span", providerConnectionState(provider), `${css.state || ""}${provider.available ? " connected" : ""}`)]);
      if (css.caret) heading.append(text("span", "⌄", css.caret));
      card.append(heading);
      if (provider.privacy || provider.privacyNotice) card.append(text("p", provider.privacy || provider.privacyNotice, css.copy));
      if (provider.id === "opencode") card.append(text("p", zenConnectionNotice, css.copy));
      else if (provider.id === "instance-codex") card.append(text("p", `${sharedCodexConnectionNotice} ${sharedCodexPolicy(provider)}`, css.copy));
      else if (provider.authenticated && ["openai", "openai-codex"].includes(provider.id)) {
        const disconnect = button("Disconnect", () => {
          if (confirmDisconnect(provider)) void changeCredential(provider);
        });
        disconnect.disabled = Boolean(mutation || login); card.append(disconnect);
      } else if (provider.id === "openai-codex") card.append(loginForm());
      else if (apiKeyProviderDetails[provider.id]) card.append(authForm(provider));
      return card;
    });
    container.replaceChildren(...(cards.length ? cards : [text("p", runtime?.message || "No providers are configured.", css.copy)]));
  }
  return {
    refresh, render,
    scopeChanged() { if (!disposed) { syncScope(); render(); } },
    setRuntime(next) { if (!disposed) { syncScope(); refreshSequence++; runtime = next; render(); } },
    dispose() { if (disposed) return; disposed = true; epoch++; refreshSequence++; clearSecrets(); abandonLogin(); mutation = null; container.replaceChildren(); },
  };
}
