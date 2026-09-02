const DEFAULT_DURATION_MS = 850;
const DEFAULT_MAX_ELEMENTS = 24;

function selectorValue(value) {
  if (globalThis.CSS?.escape) return CSS.escape(String(value));
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function rendered(element) {
  if (!element || element.closest?.("[hidden], dialog:not([open])")) return false;
  if (typeof element.checkVisibility === "function") {
    return element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  }
  return true;
}

function uniqueRendered(elements) {
  return [...new Set(elements)].filter(rendered);
}

function hasRoundedRadius(value) {
  const parts = String(value || "").trim().split(/\s+/).filter(Boolean);
  return parts.some(part => Number.parseFloat(part) > 0 || part.endsWith("%"));
}

function cueRadius(root, element) {
  const view = element.ownerDocument?.defaultView || root.defaultView || globalThis;
  const styleFor = node => view.getComputedStyle?.(node) || null;
  const ownRadius = styleFor(element)?.borderRadius;
  if (hasRoundedRadius(ownRadius)) return ownRadius;
  const control = element.querySelector?.(":scope > input, :scope > select, :scope > textarea, :scope > button");
  const controlRadius = control ? styleFor(control)?.borderRadius : null;
  return hasRoundedRadius(controlRadius) ? controlRadius : "5px";
}

function objectElements(root, objectId, fields = []) {
  const identity = selectorValue(objectId);
  if (fields.length) {
    const exact = uniqueRendered(fields.flatMap(field => (
      [...root.querySelectorAll(
        `[data-change-object-id="${identity}"][data-change-field~="${selectorValue(field)}"]`,
      )]
    )));
    if (exact.length) return exact;
  }
  const roots = uniqueRendered(root.querySelectorAll(
    `[data-change-object-id="${identity}"][data-change-root]`,
  ));
  if (roots.length) return roots;
  return uniqueRendered(root.querySelectorAll(`[data-change-object-id="${identity}"]`));
}

function targetElements(root, target) {
  const exact = target.objectId
    ? objectElements(root, target.objectId, target.fields || [])
    : [];
  if (exact.length) return exact;
  const fallback = target.fallbackId
    ? objectElements(root, target.fallbackId)
    : [];
  if (fallback.length) return fallback;
  if (!target.scope) return [];
  return uniqueRendered(root.querySelectorAll(
    `[data-change-scope~="${selectorValue(target.scope)}"]`,
  ));
}

export function createTransientCueManager({
  root = document,
  durationMs = DEFAULT_DURATION_MS,
  maxElements = DEFAULT_MAX_ELEMENTS,
  now = () => Date.now(),
  schedule = (callback, delay) => window.setTimeout(callback, delay),
  cancel = timer => window.clearTimeout(timer),
  MutationObserverClass = globalThis.MutationObserver,
} = {}) {
  let batch = null;
  let timer = null;
  let replayQueued = false;
  let activeElements = new Map();

  const clearElements = () => {
    for (const element of activeElements.keys()) {
      element.classList.remove("change-cue-active");
      delete element.dataset.changeCueTone;
      element.style?.removeProperty?.("--change-cue-radius");
    }
    activeElements = new Map();
  };

  const clear = () => {
    if (timer !== null) cancel(timer);
    timer = null;
    batch = null;
    clearElements();
  };

  const replay = () => {
    replayQueued = false;
    if (!batch || batch.expiresAt <= now()) {
      clear();
      return [];
    }
    const resolved = [];
    const seen = new Set();
    for (const target of batch.targets) {
      for (const element of targetElements(root, target)) {
        if (seen.has(element)) continue;
        seen.add(element);
        resolved.push({ element, tone: target.tone || "amber" });
        if (resolved.length >= maxElements) break;
      }
      if (resolved.length >= maxElements) break;
    }
    const nextElements = new Map(resolved.map(({ element, tone }) => [element, tone]));
    for (const element of activeElements.keys()) {
      if (nextElements.has(element)) continue;
      element.classList.remove("change-cue-active");
      delete element.dataset.changeCueTone;
    }
    for (const [element, tone] of nextElements) {
      if (activeElements.get(element) === tone) continue;
      element.dataset.changeCueTone = tone;
      element.style?.setProperty?.("--change-cue-radius", cueRadius(root, element));
      element.classList.add("change-cue-active");
    }
    activeElements = nextElements;
    return [...activeElements.keys()];
  };

  const queueReplay = () => {
    if (!batch || replayQueued) return;
    replayQueued = true;
    queueMicrotask(replay);
  };

  const observer = MutationObserverClass
    ? new MutationObserverClass(records => {
      if (records.some(record => record.type === "childList")) queueReplay();
    })
    : null;
  observer?.observe(root.documentElement || root, { childList: true, subtree: true });

  const show = targets => {
    clear();
    const meaningful = (targets || []).filter(target => (
      target?.objectId || target?.fallbackId || target?.scope
    ));
    if (!meaningful.length) return [];
    batch = { targets: meaningful, expiresAt: now() + durationMs };
    const elements = replay();
    timer = schedule(clear, durationMs);
    return elements;
  };

  const dispose = () => {
    observer?.disconnect();
    clear();
  };

  return { clear, dispose, replay, show };
}
