import { ApiError } from "./api.js";
import { createStatePanel } from "./ui.js";

export function element(tagName, options = {}, children = []) {
  const node = document.createElement(tagName);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.type) node.type = options.type;
  if (options.title) node.title = options.title;
  if (options.hidden) node.hidden = true;
  if (options.attrs) {
    for (const [name, value] of Object.entries(options.attrs)) {
      if (value !== undefined && value !== null) node.setAttribute(name, String(value));
    }
  }
  if (options.dataset) {
    for (const [name, value] of Object.entries(options.dataset)) node.dataset[name] = String(value);
  }
  const values = Array.isArray(children) ? children : [children];
  for (const child of values) {
    if (child instanceof Node) node.append(child);
    else if (child !== undefined && child !== null) node.append(document.createTextNode(String(child)));
  }
  return node;
}

export function replace(node, ...children) {
  node.replaceChildren(...children.filter(Boolean));
}

export function errorPanel(error, { retryLabel, onRetry } = {}) {
  const message = error instanceof Error ? error.message : "The request could not be completed";
  const panel = element("div", { className: "error-panel", attrs: { role: "alert" } });
  panel.append(element("strong", { text: "Request failed" }), element("p", { text: message }));

  if (error instanceof ApiError) {
    const details = element("details");
    details.append(element("summary", { text: "Error details" }));
    const list = element("dl", { className: "error-details" });
    list.append(element("dt", { text: "Code" }), element("dd", { text: error.code }));
    if (error.requestId) list.append(element("dt", { text: "Request ID" }), element("dd", { text: error.requestId }));
    if (error.status) list.append(element("dt", { text: "HTTP status" }), element("dd", { text: error.status }));
    details.append(list);
    panel.append(details);
  }
  if (onRetry) {
    const button = element("button", { className: "ui-button compact", type: "button", text: retryLabel || "Retry" });
    button.addEventListener("click", onRetry);
    panel.append(button);
  }
  return panel;
}

export function emptyPanel(mark, title, copy, action = null) {
  return createStatePanel({ mark, title, message: copy, action, surface: true, className: "empty-panel" });
}

export function formatTimestamp(value) {
  if (!value) return "Not reported";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function normalizedSearch(value) {
  return value.trim().toLocaleLowerCase();
}
