const EXIT_DURATION_MS = 210;
const REFLOW_DURATION_MS = 180;
const MAX_TRACKED_ELEMENTS = 300;

const TONE_RGB = Object.freeze({
  amber: "244, 185, 66",
  purple: "155, 130, 244",
  blue: "101, 169, 255",
});

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

export function resolveChangeTargetElements(root, target) {
  if (!root || !target?.objectId) return [];
  const id = selectorValue(target.objectId);
  const fields = Array.isArray(target.fields) ? target.fields.filter(Boolean) : [];
  const unique = matches => [...new Set(matches.filter(rendered))];
  if (fields.length) {
    const exact = unique(fields.flatMap(field => [...root.querySelectorAll(
      `[data-change-object-id="${id}"][data-change-field~="${selectorValue(field)}"]`,
    )]));
    if (exact.length) return exact;
  }
  const roots = unique([...root.querySelectorAll(
    `[data-change-object-id="${id}"][data-change-root]`,
  )]);
  if (roots.length) return roots;
  return unique([...root.querySelectorAll(`[data-change-object-id="${id}"]`)]);
}

function clipsAxis(style, axis) {
  const value = style?.[axis] || style?.overflow || "visible";
  return /^(auto|scroll|hidden|clip)$/.test(value);
}

export function elementFullyVisible(element, { root = element?.ownerDocument || globalThis.document, margin = 2 } = {}) {
  if (!rendered(element) || !element?.getBoundingClientRect) return false;
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const windowRef = root?.defaultView || root?.ownerDocument?.defaultView || globalThis.window;
  const viewport = windowRef?.visualViewport;
  const viewportLeft = viewport?.offsetLeft || 0;
  const viewportTop = viewport?.offsetTop || 0;
  const viewportRight = viewportLeft + (viewport?.width || windowRef?.innerWidth || root?.documentElement?.clientWidth || 0);
  const viewportBottom = viewportTop + (viewport?.height || windowRef?.innerHeight || root?.documentElement?.clientHeight || 0);
  if (
    rect.left < viewportLeft + margin
    || rect.right > viewportRight - margin
    || rect.top < viewportTop + margin
    || rect.bottom > viewportBottom - margin
  ) return false;

  for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
    if (ancestor === root?.documentElement || ancestor === root?.body) continue;
    const style = windowRef?.getComputedStyle?.(ancestor);
    const clipsX = clipsAxis(style, "overflowX");
    const clipsY = clipsAxis(style, "overflowY");
    if (!clipsX && !clipsY) continue;
    const bounds = ancestor.getBoundingClientRect?.();
    if (!bounds) continue;
    if (clipsX && (rect.left < bounds.left + margin || rect.right > bounds.right - margin)) return false;
    if (clipsY && (rect.top < bounds.top + margin || rect.bottom > bounds.bottom - margin)) return false;
  }
  return true;
}

function rectValue(element) {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

function classValue(element) {
  const value = typeof element.className === "string"
    ? element.className
    : element.getAttribute?.("class") || "";
  return value
    .split(/\s+/)
    .filter(token => token && ![
      "active",
      "selected",
      "dragging",
      "change-cue-active",
    ].includes(token) && !token.startsWith("is-"))
    .sort()
    .join(".");
}

export function changeTransitionKey(element) {
  return [
    element.dataset?.changeObjectId || "",
    element.tagName || "",
    classValue(element),
  ].join("|");
}

function groupedRoots(root, excluded = new Set()) {
  const groups = new Map();
  const elements = [...root.querySelectorAll("[data-change-object-id][data-change-root]")]
    .filter(element => !excluded.has(element) && rendered(element))
    .slice(0, MAX_TRACKED_ELEMENTS);
  for (const element of elements) {
    const key = changeTransitionKey(element);
    const values = groups.get(key) || [];
    values.push({ element, rect: rectValue(element) });
    groups.set(key, values);
  }
  return groups;
}

function removalElements(root, targets) {
  const elements = [];
  const seen = new Set();
  for (const target of targets || []) {
    if (target?.operation !== "remove" || !target.objectId) continue;
    const id = selectorValue(target.objectId);
    let matches = [...root.querySelectorAll(
      `[data-change-object-id="${id}"][data-change-root]`,
    )].filter(rendered);
    if (!matches.length) {
      matches = [...root.querySelectorAll(`[data-change-object-id="${id}"]`)].filter(rendered);
    }
    for (const element of matches) {
      if (seen.has(element)) continue;
      seen.add(element);
      elements.push({ element, tone: target.tone || "amber" });
    }
  }
  return elements;
}

function targetElements(root, targets, operations = null) {
  const allowed = operations ? new Set(operations) : null;
  const elements = [];
  const seen = new Set();
  for (const target of targets || []) {
    if (!target?.objectId || (allowed && !allowed.has(target.operation))) continue;
    const id = selectorValue(target.objectId);
    const matches = [...root.querySelectorAll(
      `[data-change-object-id="${id}"]`,
    )].filter(rendered);
    for (const element of matches) {
      if (seen.has(element)) continue;
      seen.add(element);
      elements.push(element);
    }
  }
  return elements;
}

function reducedMotion(root) {
  return root.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function createChangeTransitionManager({
  root = document,
  exitDurationMs = EXIT_DURATION_MS,
  reflowDurationMs = REFLOW_DURATION_MS,
  schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
} = {}) {
  const visibleRemovals = targets => removalElements(root, targets);

  async function prepare(targets) {
    const removals = visibleRemovals(targets);
    const snapshot = groupedRoots(root, new Set(removals.map(item => item.element)));
    if (!removals.length || reducedMotion(root)) {
      return { snapshot, removalCount: removals.length, animations: [] };
    }
    const animations = removals.map(({ element, tone }) => {
      const rgb = TONE_RGB[tone] || TONE_RGB.amber;
      if (typeof element.animate !== "function") return null;
      return element.animate([
        {
          opacity: 1,
          offset: 0,
          outline: `2px solid rgb(${rgb})`,
          boxShadow: `inset 0 0 0 999px rgba(${rgb}, .2), 0 0 0 3px rgba(${rgb}, .28)`,
          transform: "translate3d(0, 0, 0) scale(1)",
        },
        {
          opacity: 1,
          offset: .3,
          outline: `2px solid rgba(${rgb}, .9)`,
          boxShadow: `inset 0 0 0 999px rgba(${rgb}, .12), 0 0 0 2px rgba(${rgb}, .18)`,
          transform: "translate3d(0, 0, 0) scale(1)",
        },
        {
          opacity: 0,
          offset: 1,
          outline: `1px solid rgba(${rgb}, 0)`,
          boxShadow: `inset 0 0 0 999px rgba(${rgb}, 0), 0 0 0 0 rgba(${rgb}, 0)`,
          transform: "translate3d(0, -3px, 0) scale(.985)",
        },
      ], { duration: exitDurationMs, easing: "cubic-bezier(.4, 0, .2, 1)", fill: "forwards" });
    }).filter(Boolean);
    const timeout = new Promise(resolve => schedule(resolve, exitDurationMs + 30));
    await Promise.race([
      Promise.all(animations.map(animation => animation.finished.catch(() => null))),
      timeout,
    ]);
    return { snapshot, removalCount: removals.length, animations };
  }

  function restore(prepared) {
    for (const animation of prepared?.animations || []) animation.cancel?.();
  }

  function reflow(prepared) {
    // Exit animations use fill-forwards so the removed item stays faded until
    // the mutation has replaced the rendered state. Some change targets are
    // persistent surface containers (for example the view-detail pane), not
    // disposable rows. Always release the completed exits before measuring
    // and animating the surviving layout, otherwise newly rendered content can
    // remain trapped inside an opacity: 0 animation until the page reloads.
    restore(prepared);
    const snapshot = prepared?.snapshot || prepared;
    if (!snapshot || reducedMotion(root)) return 0;
    const current = groupedRoots(root);
    let animated = 0;
    for (const [key, previousValues] of snapshot) {
      const currentValues = current.get(key) || [];
      previousValues.forEach((previous, index) => {
        const next = currentValues[index];
        if (!next || typeof next.element.animate !== "function") return;
        const deltaX = previous.rect.left - next.rect.left;
        const deltaY = previous.rect.top - next.rect.top;
        if (Math.abs(deltaX) < 1 && Math.abs(deltaY) < 1) return;
        next.element.animate([
          { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)` },
          { transform: "translate3d(0, 0, 0)" },
        ], { duration: reflowDurationMs, easing: "cubic-bezier(.22, 1, .36, 1)" });
        animated += 1;
      });
    }
    return animated;
  }

  return {
    hasVisibleTarget: (targets, { operations = null } = {}) => (
      targetElements(root, targets, operations).length > 0
    ),
    hasVisibleRemoval: targets => visibleRemovals(targets).length > 0,
    prepare,
    reflow,
    restore,
  };
}
