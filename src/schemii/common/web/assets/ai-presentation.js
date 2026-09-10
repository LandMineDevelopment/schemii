import { element } from "./dom.js";
import { createIconButton } from "./ui.js";
import { renderMarkdown } from "./ai-markdown.js";

export function formatDate(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.valueOf())) return "";
  const sameDay = date.toDateString() === new Date().toDateString();
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
  ).format(date);
}

export const modelValue = (providerId, modelId) => `${providerId || ""}\u0000${modelId || ""}`;

export function availableModels(status) {
  return (status?.providers || []).flatMap(provider => (provider.available ? provider.models || [] : [])
    .filter(model => model.status === "active")
    .map(model => ({ ...model, providerId: provider.id, providerName: provider.name })));
}

export function populateModelOptions(select, models, providerId, modelId) {
  const current = modelValue(providerId, modelId);
  select.replaceChildren(...models.map(model => element("option", {
    text: `${model.name} · ${model.providerName}`, attrs: { value: modelValue(model.providerId, model.id) },
  })));
  if (modelId && !models.some(model => modelValue(model.providerId, model.id) === current)) {
    select.prepend(element("option", { text: `${modelId} · unavailable — choose another model`, attrs: { value: current, disabled: "" } }));
  }
  if (modelId) select.value = current;
}

/** One message surface for both products, including safe Markdown and clipboard feedback. */
export function createMessageNode(message, { assistantName = "Assistant", onError = () => {}, existing = null } = {}) {
  const signature = JSON.stringify([message.role, message.text, message.createdAt, message.transient, assistantName]);
  if (existing?.dataset.signature === signature) {
    existing.style.animation = "none";
    return existing;
  }
  const role = ["user", "assistant", "system"].includes(message.role) ? message.role : "system";
  const article = element("article", { className: `ai-message ${role}`, dataset: { messageId: message.id || "", messageRole: role, messageTurnId: message.turnId || "", timestamp: message.createdAt || "" } });
  article.dataset.signature = signature;
  // Streaming updates must not restart the message-arrival animation.
  if (existing) article.style.animation = "none";
  const label = element("span", { text: role === "assistant" ? assistantName : role === "user" ? "You" : "System" });
  const content = element("div", { className: "ai-message__content" }, [renderMarkdown(message.text)]);
  const copy = createIconButton({ icon: "copy", label: "Copy message", placement: "left", className: "compact ai-message__copy" });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      copy.classList.add("copied"); copy.setAttribute("aria-label", "Copied");
      globalThis.setTimeout(() => { copy.classList.remove("copied"); copy.setAttribute("aria-label", "Copy message"); }, 1200);
    } catch { onError("Could not copy the message. Select its text and copy it manually."); }
  });
  const surface = element("div", { className: "ai-message__surface" }, [content, copy]);
  if (message.transient) surface.append(element("span", { className: "ai-message__privacy", text: "Temporary · not saved" }));
  const time = element("time", { text: formatDate(message.createdAt), attrs: { datetime: message.createdAt || "" } });
  article.append(label, surface, time);
  return article;
}
