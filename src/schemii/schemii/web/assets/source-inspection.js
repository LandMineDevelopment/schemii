import {
  createIconButton,
  decorateIconControl,
} from "./ui.js";

function element(tag, { className = "", textContent = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent) node.textContent = textContent;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function normalizeInspectedObject(value) {
  const source = asRecord(value?.source);
  const location = asRecord(value?.location);
  const rawShape = asRecord(value?.dataShape);
  const sourceText = typeof source.text === "string" ? source.text : "";
  const allowedTokenKinds = new Set([
    "plain", "comment", "string", "number", "operator", "keyword",
    "definition", "decorator", "builtin",
  ]);
  const tokens = (Array.isArray(source.tokens) ? source.tokens : [])
    .filter(item => Array.isArray(item) && item.length === 2 && allowedTokenKinds.has(item[0]) && typeof item[1] === "string")
    .map(([kind, valueText]) => [kind, valueText]);
  return {
    id: text(value?.id),
    name: text(value?.name, "Python object"),
    qualname: text(value?.qualname),
    module: text(value?.module),
    kind: text(value?.kind, "object"),
    dataShape: rawShape.kind === "object" ? {
      kind: "object",
      name: text(rawShape.name, text(value?.name, "Value")),
      fields: (Array.isArray(rawShape.fields) ? rawShape.fields : []).map(rawField => {
        const field = asRecord(rawField);
        return {
          name: text(field.name),
          attribute: text(field.attribute, text(field.name)),
          annotation: text(field.annotation, "Any"),
          required: field.required === true,
        };
      }).filter(field => field.name),
      truncated: rawShape.truncated === true,
    } : null,
    docstring: text(value?.docstring),
    docstringTruncated: value?.docstringTruncated === true,
    location: {
      path: text(location.path),
      sourceStartLine: Number.isInteger(location.sourceStartLine) ? location.sourceStartLine : null,
      definitionLine: Number.isInteger(location.definitionLine) ? location.definitionLine : null,
      endLine: Number.isInteger(location.endLine) ? location.endLine : null,
    },
    source: {
      available: source.available === true,
      sha256: text(source.sha256),
      text: sourceText,
      tokens: tokens.map(([, valueText]) => valueText).join("") === sourceText
        ? tokens
        : sourceText ? [["plain", sourceText]] : [],
      truncated: source.truncated === true,
    },
  };
}

export function normalizeInspectionObjects(values) {
  return new Map(
    (Array.isArray(values) ? values : [])
      .map(normalizeInspectedObject)
      .filter(item => item.id)
      .map(item => [item.id, item]),
  );
}

export function temporaryIconState(button, { icon, label, className }, reset, duration = 1_500) {
  window.clearTimeout(button.__resetIconTimer);
  button.classList.remove("copied", "copy-failed");
  if (className) button.classList.add(className);
  decorateIconControl(button, { icon, label, tooltip: label, placement: "left" });
  button.__resetIconTimer = window.setTimeout(reset, duration);
}

export function copyButton(value, label = "Copy code") {
  const button = createIconButton({
    icon: "copy",
    label,
    placement: "left",
    className: "compact copy-code",
  });
  const reset = () => {
    button.classList.remove("copied", "copy-failed");
    decorateIconControl(button, { icon: "copy", label, tooltip: label, placement: "left" });
  };
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      temporaryIconState(button, { icon: "check", label: "Copied", className: "copied" }, reset);
    } catch {
      temporaryIconState(button, { icon: "close", label: "Copy failed", className: "copy-failed" }, reset, 2_000);
    }
  });
  return button;
}

function appendTokenSegments(code, segments) {
  for (const [kind, value] of segments) {
    if (kind === "plain") code.append(document.createTextNode(value));
    else code.append(element("span", { className: `token token-${kind}`, textContent: value }));
  }
}

function sourceSegmentSlice(segments, start, end) {
  const result = [];
  let cursor = 0;
  for (const [kind, value] of segments) {
    const segmentEnd = cursor + value.length;
    const overlapStart = Math.max(start, cursor);
    const overlapEnd = Math.min(end, segmentEnd);
    if (overlapStart < overlapEnd) result.push([kind, value.slice(overlapStart - cursor, overlapEnd - cursor)]);
    cursor = segmentEnd;
    if (cursor >= end) break;
  }
  return result;
}

export function pythonSourceExcerpt(object, absoluteLine, radius = 2, absoluteEndLine = absoluteLine) {
  const source = object?.source?.text || "";
  const sourceStartLine = object?.location?.sourceStartLine;
  const lines = source.match(/[^\n]*\n|[^\n]+$/g) || [];
  if (!source || !Number.isInteger(sourceStartLine) || !Number.isInteger(absoluteLine) || !lines.length) return null;
  const relativeLine = absoluteLine - sourceStartLine;
  const relativeEndLine = Number.isInteger(absoluteEndLine)
    ? absoluteEndLine - sourceStartLine
    : relativeLine;
  if (relativeLine < 0 || relativeLine >= lines.length || relativeEndLine < relativeLine) return null;
  const first = Math.max(0, relativeLine - radius);
  const last = Math.min(lines.length, relativeEndLine + radius + 1);
  const start = lines.slice(0, first).reduce((length, line) => length + line.length, 0);
  const end = start + lines.slice(first, last).reduce((length, line) => length + line.length, 0);
  return {
    text: source.slice(start, end),
    tokens: sourceSegmentSlice(object.source.tokens, start, end),
    startLine: sourceStartLine + first,
    endLine: sourceStartLine + last - 1,
    focusLine: absoluteLine,
  };
}

function appendJsonTokens(code, sourceText) {
  const pattern = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  let cursor = 0;
  for (const match of sourceText.matchAll(pattern)) {
    code.append(document.createTextNode(sourceText.slice(cursor, match.index)));
    const value = match[0];
    const remainder = sourceText.slice(match.index + value.length);
    const kind = value.startsWith('"')
      ? /^\s*:/.test(remainder) ? "json-key" : "string"
      : value === "true" || value === "false" ? "boolean"
        : value === "null" ? "null" : "number";
    code.append(element("span", { className: `token token-${kind}`, textContent: value }));
    cursor = match.index + value.length;
  }
  code.append(document.createTextNode(sourceText.slice(cursor)));
}

function appendSqlTokens(code, sourceText) {
  const pattern = /\/\*[\s\S]*?\*\/|--[^\n]*|'(?:''|[^'])*'|%s|\b\d+(?:\.\d+)?\b|\b(?:SELECT|FROM|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|LATERAL|WHERE|AND|OR|AS|ON|IN|IS|NOT|NULL|CASE|WHEN|THEN|ELSE|END|ORDER|BY|LIMIT|EXISTS|BEGIN|TRANSACTION|ISOLATION|LEVEL|REPEATABLE|READ|ONLY|SET|LOCAL|DISTINCT|ARRAY|TRUE|FALSE)\b/gi;
  let cursor = 0;
  for (const match of sourceText.matchAll(pattern)) {
    code.append(document.createTextNode(sourceText.slice(cursor, match.index)));
    const value = match[0];
    const kind = value.startsWith("/*") || value.startsWith("--")
      ? "comment"
      : value.startsWith("'") ? "string"
        : value === "%s" ? "parameter"
          : /^\d/.test(value) ? "number" : "keyword";
    code.append(element("span", { className: `token token-${kind}`, textContent: value }));
    cursor = match.index + value.length;
  }
  code.append(document.createTextNode(sourceText.slice(cursor)));
}

export function codeBlock({ text: sourceText, tokens, label, language = "python", actions = [] }) {
  const wrapper = element("div", { className: `code-block code-${language}` });
  const toolbar = element("div", { className: "code-toolbar" });
  const toolbarActions = element("div", { className: "code-toolbar-actions ui-action-group" });
  toolbarActions.append(...actions, copyButton(sourceText));
  toolbar.append(element("span", { textContent: label }), toolbarActions);
  const pre = element("pre");
  const code = element("code", { attrs: { "data-language": language } });
  if (language === "python") appendTokenSegments(code, tokens?.length ? tokens : [["plain", sourceText]]);
  else if (language === "sql") appendSqlTokens(code, sourceText);
  else appendJsonTokens(code, sourceText);
  pre.append(code);
  wrapper.append(toolbar, pre);
  return wrapper;
}

export function sourceLocation(object) {
  return `${object.location.path}:${object.location.definitionLine || object.location.sourceStartLine || "?"}`;
}

export function sourceDefinitionContent(object, { toolbarActions = [] } = {}) {
  const content = element("div", { className: "source-definition" });
  if (object.docstring) {
    content.append(element("p", {
      className: "source-docstring",
      textContent: `${object.docstring}${object.docstringTruncated ? "\n\nIntent excerpt truncated by the inspection limit." : ""}`,
    }));
  }
  if (!object.source.available) {
    content.append(element("p", { className: "none-reported", textContent: "Installed source is unavailable for this definition." }));
    return content;
  }
  content.append(codeBlock({
    text: object.source.text,
    tokens: object.source.tokens,
    label: sourceLocation(object),
    actions: toolbarActions,
  }));
  if (object.source.truncated) content.append(element("p", { className: "source-truncated", textContent: "Source excerpt truncated by the inspection limit." }));
  return content;
}

export function sourceDefinitionCard(object, { open = false } = {}) {
  const details = element("details", { className: "source-card" });
  details.open = open;
  const summary = element("summary", { attrs: { tabindex: "0" } });
  const copy = element("span");
  copy.append(element("strong", { textContent: object.qualname || object.name }), element("small", { textContent: object.kind }));
  summary.append(copy, element("code", { textContent: sourceLocation(object) }));
  details.append(summary);
  const render = () => {
    if (details.dataset.sourceRendered === "true") return;
    details.dataset.sourceRendered = "true";
    details.append(sourceDefinitionContent(object));
  };
  if (details.open) render();
  details.addEventListener("toggle", () => {
    if (details.open) render();
  });
  return details;
}
