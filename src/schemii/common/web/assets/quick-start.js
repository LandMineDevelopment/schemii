/** Animated, read-only walkthroughs of the controls used in each product. */
import { guide as schemii } from "./quick-start-schemii.js";
import { guide as schemoo } from "./quick-start-schemoo.js";
import { guide as schemer } from "./quick-start-schemer.js";

export const QUICK_STARTS = Object.freeze({ schemii, schemoo, schemer });

const POINTER = '<svg viewBox="0 0 26 32" aria-hidden="true"><path d="M2 2v24l6.5-6.5 4.5 9 4-2-4.5-9H23Z"/></svg>';

function positionCursor(scene, cursor, target, label) {
  const bounds = scene.getBoundingClientRect();
  const control = target.getBoundingClientRect();
  const x = control.left - bounds.left + control.width / 2;
  const y = control.top - bounds.top + control.height / 2;
  cursor.classList.remove("clicking", "tooltip-left", "tooltip-above", "tooltip-high");
  cursor.classList.toggle("tooltip-left", x > bounds.width * .69);
  cursor.classList.toggle("tooltip-above", y > bounds.height * .72);
  cursor.classList.toggle("tooltip-high", target.dataset.quickStartTooltip === "high");
  cursor.querySelector("span").textContent = label;
  cursor.style.left = `${x}px`;
  cursor.style.top = `${y}px`;
  cursor.classList.add("visible");
}

function targetIsVisible(scene, target) {
  const bounds = scene.getBoundingClientRect();
  const control = target.getBoundingClientRect();
  const x = control.left + control.width / 2;
  const y = control.top + control.height / 2;
  if (!control.width || !control.height || x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return false;
  for (let element = target; element && element !== scene; element = element.parentElement) {
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) < .05) return false;
  }
  // The illustration is inert so its controls cannot be focused. Temporarily
  // lift inert only for hit testing; restore it before the browser can paint.
  const inert = scene.inert;
  scene.inert = false;
  try {
    const top = document.elementFromPoint(x, y);
    return !!top && (target.contains(top) || (top !== scene && top !== scene.firstElementChild && top.contains(target)));
  } finally {
    scene.inert = inert;
  }
}

function createPlayback(page, step) {
  const scene = page.querySelector(".quick-start-scene");
  const mock = scene.firstElementChild;
  const cursor = page.querySelector(".quick-start-cursor");
  const status = page.querySelector(".quick-start-status");
  const toggle = page.querySelector(".quick-start-toggle");
  const replay = page.querySelector(".quick-start-replay");
  let timer;
  let actionIndex = 0;
  let paused = false;
  let phase = "idle";
  let active = false;
  let currentTarget = null;
  let currentLabel = "";
  const hideCursor = () => { cursor.classList.remove("visible", "clicking"); currentTarget = null; };
  const syncCursor = () => {
    if (!active || !currentTarget || !cursor.classList.contains("visible")) return;
    if (!targetIsVisible(scene, currentTarget)) return hideCursor();
    const clicking = cursor.classList.contains("clicking");
    positionCursor(scene, cursor, currentTarget, currentLabel);
    if (clicking) cursor.classList.add("clicking");
  };
  new ResizeObserver(syncCursor).observe(scene);
  new MutationObserver(syncCursor).observe(mock, { attributes: true, childList: true, subtree: true });
  scene.addEventListener("transitionend", syncCursor);
  const clear = () => { clearTimeout(timer); timer = undefined; };
  const queue = (callback, delay) => { clear(); timer = setTimeout(callback, delay); };
  const reset = (staticState = false) => {
    clear();
    actionIndex = 0;
    phase = "idle";
    mock.classList.remove(...step.states.map(state => `demo-${state}`));
    if (staticState) mock.classList.add(...step.states.map(state => `demo-${state}`));
    cursor.classList.remove("visible", "clicking", "tooltip-left", "tooltip-above");
    currentTarget = null;
    status.textContent = staticState ? step.staticText : step.idleText;
  };
  const run = () => {
    if (!active || paused) return;
    if (actionIndex >= step.actions.length) {
      status.textContent = step.completeText;
      phase = "replay";
      hideCursor();
      queue(() => { reset(); run(); }, 1500);
      return;
    }
    const action = step.actions[actionIndex];
    const target = scene.querySelector(`[data-quick-start-target="${action.target}"]`);
    if (!target) throw new Error(`Missing quick start target: ${action.target}`);
    currentTarget = target;
    currentLabel = action.label;
    positionCursor(scene, cursor, target, action.label);
    syncCursor();
    status.textContent = `Next: ${action.caption}`;
    phase = "moving";
    queue(() => {
      cursor.classList.add("clicking");
      phase = "clicking";
      queue(() => {
        mock.classList.add(`demo-${action.state}`);
        cursor.classList.remove("clicking");
        syncCursor();
        status.textContent = action.caption;
        actionIndex += 1;
        phase = "waiting";
        queue(run, action.delay ?? 900);
      }, 700);
    }, 650);
  };
  const start = (forceMotion = false) => {
    active = true;
    paused = !forceMotion && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    reset(paused);
    toggle.textContent = paused ? "Play demo" : "Pause demo";
    toggle.setAttribute("aria-pressed", String(!paused));
    if (!paused) queue(run, 500);
  };
  const stop = () => { active = false; clear(); hideCursor(); };
  const togglePlayback = () => {
    if (paused) {
      const wasStatic = phase === "idle" && actionIndex === 0 && step.states.every(state => mock.classList.contains(`demo-${state}`));
      paused = false;
      toggle.textContent = "Pause demo";
      toggle.setAttribute("aria-pressed", "true");
      if (wasStatic) reset();
      // Resuming starts the current action again so its target and caption stay in sync.
      run();
    } else {
      paused = true;
      clear();
      hideCursor();
      toggle.textContent = "Play demo";
      toggle.setAttribute("aria-pressed", "false");
      status.textContent = "Demo paused. Play to continue.";
    }
  };
  toggle.addEventListener("click", togglePlayback);
  replay.addEventListener("click", () => start(true));
  return { start, stop };
}

export function installQuickStart(product, trigger) {
  const guide = QUICK_STARTS[product];
  if (!guide || !(trigger instanceof HTMLElement)) throw new TypeError("A known product and trigger are required");
  const menu = trigger.closest("details");
  const focusReturn = menu?.querySelector("summary") || trigger;
  const dialog = document.createElement("dialog");
  dialog.className = "ui-dialog quick-start-dialog";
  dialog.setAttribute("aria-labelledby", "quick-start-title");
  dialog.innerHTML = `<div class="quick-start-panel"><header class="quick-start-head"><div><small>Quick start</small><h2 id="quick-start-title">Welcome to ${guide.name}</h2></div><span class="quick-start-count" aria-live="polite"></span></header><div class="quick-start-pages"></div><footer class="quick-start-footer"><div class="quick-start-progress" aria-label="Guide progress"></div><div class="quick-start-actions"><button type="button" class="ui-button quick-start-skip">Skip</button><button type="button" class="ui-button quick-start-back" aria-label="Previous step">←</button><button type="button" class="ui-button primary quick-start-next">Next →</button></div></footer></div>`;
  const pages = dialog.querySelector(".quick-start-pages");
  const demos = guide.steps.map((step, index) => {
    const page = document.createElement("section");
    page.className = "quick-start-page";
    page.hidden = index !== 0;
    page.innerHTML = `<div class="quick-start-scene quick-start-scene--${product}" aria-hidden="true" inert>${step.scene}<div class="quick-start-cursor">${POINTER}<span></span></div></div><div class="quick-start-playback"><span class="quick-start-status" role="status" aria-live="polite"></span><div class="quick-start-playback-actions"><button type="button" class="ui-button quick-start-replay">Replay</button><button type="button" class="ui-button quick-start-toggle" aria-pressed="true">Pause demo</button></div></div><div class="quick-start-copy"><span>${String(index + 1).padStart(2, "0")}</span><div><h3>${step.title}</h3><p>${step.text}</p><p class="quick-start-tip">${step.tip}</p><div class="quick-start-action-summary"><strong>Actions shown</strong><ol>${step.actions.map(action => `<li>${action.caption}</li>`).join("")}</ol></div></div></div>`;
    pages.append(page);
    return createPlayback(page, step);
  });
  const count = dialog.querySelector(".quick-start-count");
  const progress = dialog.querySelector(".quick-start-progress");
  const back = dialog.querySelector(".quick-start-back");
  const next = dialog.querySelector(".quick-start-next");
  const skip = dialog.querySelector(".quick-start-skip");
  let index = 0;
  function render() {
    demos.forEach(demo => demo.stop());
    [...pages.children].forEach((page, pageIndex) => { page.hidden = pageIndex !== index; });
    pages.scrollTop = 0;
    count.textContent = `${index + 1} of ${guide.steps.length}`;
    progress.replaceChildren(...guide.steps.map((_, markerIndex) => {
      const marker = document.createElement("i");
      marker.classList.toggle("active", markerIndex === index);
      marker.setAttribute("aria-hidden", "true");
      return marker;
    }));
    back.disabled = index === 0;
    next.textContent = index === guide.steps.length - 1 ? "Finish" : "Next →";
    demos[index].start();
    next.focus();
  }
  function open() {
    index = 0;
    menu?.removeAttribute("open");
    dialog.showModal();
    render();
  }
  trigger.addEventListener("click", open);
  back.addEventListener("click", () => { if (index > 0) { index -= 1; render(); } });
  next.addEventListener("click", () => { if (index + 1 === guide.steps.length) dialog.close(); else { index += 1; render(); } });
  skip.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { demos.forEach(demo => demo.stop()); focusReturn.focus(); });
  document.body.append(dialog);
  return { open, dialog };
}
