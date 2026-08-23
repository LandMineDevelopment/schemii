(() => {
  const ICONS = Object.freeze({
    close: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg>',
    sql: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="3.5" width="14" height="13" rx="2"/><path d="m6.5 8 2 2-2 2M10.5 12h3"/></svg>',
    database: '<svg viewBox="0 0 20 20" aria-hidden="true"><ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5"/></svg>',
    edit: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 14.5-.5 3 3-.5L16 7.5 12.5 4Z"/><path d="m11 5.5 3.5 3.5"/></svg>',
    earlier: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m9 5-5 5 5 5M4 10h12"/></svg>',
    later: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m11 5 5 5-5 5M4 10h12"/></svg>',
    copy: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/></svg>',
    duplicate: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/></svg>',
    delete: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/></svg>',
    add: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
    refresh: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 7A6 6 0 1 0 16 12"/><path d="M15.5 3.5V7H12"/></svg>',
    search: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5"/><path d="m12.2 12.2 4.3 4.3"/></svg>',
    more: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="15" cy="10" r="1"/></svg>',
    assistant: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5.5h12v10H9l-3 3v-13Z"/><path d="M9 9h6M9 12h4"/></svg>',
    history: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 5.5h11M4.5 10h11M4.5 14.5h7"/></svg>',
    settings: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4"/></svg>',
    newChat: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
  });

  function decorateIconControl(control, { icon, label, tooltip = label, placement = "top", id = "", className = "", dataset = {}, attributes = {} }) {
    if (!ICONS[icon] || typeof label !== "string" || !label) throw new TypeError("A known icon and accessible label are required");
    control.className = `shared-icon-button${className ? ` ${className}` : ""}`;
    if (id) control.id = id;
    control.setAttribute("aria-label", label);
    if (tooltip) control.dataset.tooltip = tooltip;
    if (placement) control.dataset.tooltipPlacement = placement;
    for (const [name, value] of Object.entries(dataset)) control.dataset[name] = String(value);
    for (const [name, value] of Object.entries(attributes)) control.setAttribute(name, String(value));
    control.innerHTML = ICONS[icon];
    return control;
  }

  function createIconButton(options) {
    const button = document.createElement("button");
    button.type = "button";
    return decorateIconControl(button, options);
  }

  function createTooltipController({ element }) {
    if (!(element instanceof HTMLElement)) throw new TypeError("A tooltip element is required");
    let activeTarget = null;
    let hideTimer = null;

    function position(target) {
      const targetRect = target.getBoundingClientRect();
      const tooltipRect = element.getBoundingClientRect();
      const gap = 9;
      const margin = 8;
      let placement = target.dataset.tooltipPlacement || (target.closest(".tool-rail") ? "right" : "top");
      if (placement === "right" && targetRect.right + gap + tooltipRect.width > window.innerWidth - margin) placement = "left";
      if (placement === "left" && targetRect.left - gap - tooltipRect.width < margin) placement = "right";
      if (placement === "top" && targetRect.top - gap - tooltipRect.height < margin) placement = "bottom";
      if (placement === "bottom" && targetRect.bottom + gap + tooltipRect.height > window.innerHeight - margin) placement = "top";
      let left;
      let top;
      if (placement === "right" || placement === "left") {
        left = placement === "right" ? targetRect.right + gap : targetRect.left - tooltipRect.width - gap;
        top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
      } else {
        left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
        top = placement === "bottom" ? targetRect.bottom + gap : targetRect.top - tooltipRect.height - gap;
      }
      element.dataset.placement = placement;
      element.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin))}px`;
      element.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - tooltipRect.height - margin))}px`;
    }

    function show(target) {
      const nativeTitle = target.getAttribute("title");
      if (nativeTitle) {
        target.dataset.tooltip = nativeTitle;
        target.removeAttribute("title");
      }
      if (!target.dataset.tooltip) return;
      clearTimeout(hideTimer);
      activeTarget = target;
      element.textContent = target.dataset.tooltip;
      element.classList.remove("visible");
      element.hidden = false;
      position(target);
      requestAnimationFrame(() => {
        if (activeTarget === target) element.classList.add("visible");
      });
    }

    function hide() {
      activeTarget = null;
      element.classList.remove("visible");
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => { element.hidden = true; }, 150);
    }

    function update(target, text) {
      target.dataset.tooltip = text;
      delete target.dataset.tooltipAutomatic;
      if (activeTarget !== target) return;
      element.textContent = text;
      position(target);
    }

    return Object.freeze({ show, hide, update, get activeTarget() { return activeTarget; } });
  }

  function elementHasTruncatedText(element) {
    if (!element || element.hidden) return false;
    const style = getComputedStyle(element);
    const lineClamp = Number.parseInt(style.webkitLineClamp, 10);
    const truncates = style.textOverflow === "ellipsis" || (Number.isFinite(lineClamp) && lineClamp > 0);
    return truncates && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1);
  }

  function automaticTooltipText(element) {
    const value = typeof element?.value === "string" ? element.value : element?.textContent;
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function findTooltipTarget(start, { includeDescendants = false, automaticTruncation = false, boundary = document.body } = {}) {
    for (let target = start; target && target !== boundary; target = target.parentElement) {
      const automatic = target.dataset?.tooltipAutomatic === "true";
      if (!automatic && (target.dataset?.tooltip || target.getAttribute?.("title"))) return target;
      const truncated = automaticTruncation && elementHasTruncatedText(target);
      if (automatic) {
        if (!truncated) {
          delete target.dataset.tooltip;
          delete target.dataset.tooltipAutomatic;
        } else {
          target.dataset.tooltip = automaticTooltipText(target);
        }
      }
      if (target.dataset?.tooltip || target.getAttribute?.("title")) return target;
      if (truncated) {
        const text = automaticTooltipText(target);
        if (text) {
          target.dataset.tooltip = text;
          target.dataset.tooltipAutomatic = "true";
          return target;
        }
      }
    }
    if (includeDescendants) {
      for (const target of start?.querySelectorAll?.("*") ?? []) {
        const match = findTooltipTarget(target, { automaticTruncation, boundary });
        if (match) return match;
      }
    }
    return null;
  }

  function installTooltipDelegation({ controller, root = document, resolveTarget = target => findTooltipTarget(target), hideOnClick = false, onScroll = null } = {}) {
    if (!controller) throw new TypeError("A tooltip controller is required");
    const listeners = [];
    const listen = (type, callback, options) => {
      root.addEventListener(type, callback, options);
      listeners.push([type, callback, options]);
    };
    listen("pointerover", event => {
      const target = resolveTarget(event.target, false);
      if (target && target !== controller.activeTarget) controller.show(target);
    });
    listen("pointerout", event => {
      if (!controller.activeTarget || controller.activeTarget.contains(event.relatedTarget)) return;
      controller.hide();
    });
    listen("focusin", event => {
      const target = resolveTarget(event.target, true);
      if (target) controller.show(target);
    });
    listen("focusout", event => {
      if (!controller.activeTarget || controller.activeTarget.contains(event.relatedTarget)) return;
      controller.hide();
    });
    listen("pointerdown", () => controller.hide());
    if (hideOnClick) listen("click", () => controller.hide());
    listen("scroll", () => {
      controller.hide();
      if (typeof onScroll === "function") onScroll();
    }, true);
    return Object.freeze({
      destroy() {
        for (const [type, callback, options] of listeners) root.removeEventListener(type, callback, options);
      }
    });
  }

  function setControlStatus(element, message, { state = "info", hideWhenEmpty = false } = {}) {
    element.textContent = message;
    element.dataset.state = state;
    element.classList.toggle("error", state === "error");
    if (hideWhenEmpty) element.hidden = !message;
  }

  const loadingStates = new WeakMap();

  function setControlLoading(control, loading, { label = null, loadingLabel = "Working...", disable = true } = {}) {
    if (loading) {
      if (!loadingStates.has(control)) loadingStates.set(control, {
        disabled: control.disabled,
        ariaLabel: control.getAttribute("aria-label"),
        tooltip: control.dataset.tooltip,
      });
      control.setAttribute("aria-busy", "true");
      control.classList.add("shared-control-loading");
      if (disable) control.disabled = true;
      if (loadingLabel) {
        control.setAttribute("aria-label", loadingLabel);
        control.dataset.tooltip = loadingLabel;
      }
      return;
    }
    const state = loadingStates.get(control);
    control.removeAttribute("aria-busy");
    control.classList.remove("shared-control-loading");
    if (!state) return;
    control.disabled = state.disabled;
    const restoredLabel = label ?? state.ariaLabel;
    if (restoredLabel) control.setAttribute("aria-label", restoredLabel); else control.removeAttribute("aria-label");
    const restoredTooltip = label ?? state.tooltip;
    if (restoredTooltip) control.dataset.tooltip = restoredTooltip; else delete control.dataset.tooltip;
    loadingStates.delete(control);
  }

  async function withLoadingControl(control, options, operation) {
    setControlLoading(control, true, options);
    try {
      return await operation();
    } finally {
      setControlLoading(control, false, options);
    }
  }

  function installDetailsMenu(menu, { closeOnAction = true, closeOnOutside = true, closeOnEscape = true } = {}) {
    const onClick = event => {
      if (closeOnAction && event.target.closest?.("button, a, [role='menuitem']")) menu.removeAttribute("open");
    };
    const onDocumentClick = event => {
      if (closeOnOutside && menu.open && !menu.contains(event.target)) menu.removeAttribute("open");
    };
    const onKeydown = event => {
      if (closeOnEscape && event.key === "Escape" && menu.open) {
        menu.removeAttribute("open");
        menu.querySelector("summary")?.focus();
      }
    };
    menu.addEventListener("click", onClick);
    document.addEventListener("click", onDocumentClick);
    document.addEventListener("keydown", onKeydown);
    return Object.freeze({ destroy() {
      menu.removeEventListener("click", onClick);
      document.removeEventListener("click", onDocumentClick);
      document.removeEventListener("keydown", onKeydown);
    } });
  }

  function positionOnboardingCursor(root, cursor, target, clickLabel = "Left click") {
    const rootBounds = root.getBoundingClientRect();
    const targetBounds = target.getBoundingClientRect();
    const left = targetBounds.left - rootBounds.left + targetBounds.width / 2 - 3;
    const top = targetBounds.top - rootBounds.top + targetBounds.height / 2 - 2;
    cursor.classList.remove("clicking", "right-click", "tooltip-left", "tooltip-above");
    cursor.classList.toggle("tooltip-left", left > rootBounds.width * .7);
    cursor.classList.toggle("tooltip-above", top > rootBounds.height * .7);
    cursor.classList.toggle("right-click", clickLabel === "Right click");
    const label = cursor.querySelector("span");
    if (label) label.textContent = clickLabel;
    cursor.style.left = `${left}px`;
    cursor.style.top = `${top}px`;
    cursor.classList.add("visible");
  }

  function createOnboardingDemo({
    root, cursor, status, toggle, steps, renderState, isActive = () => true,
    idleText, staticText, completeText = "Demo complete. Replaying...", staticState = null,
    initialDelay = 500, moveDelay = 650, clickDelay = 700, stepDelay = 900, replayDelay = 1500,
  }) {
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    let timer = null;
    let stepIndex = 0;
    let paused = false;
    const queue = (callback, delay) => {
      clearTimeout(timer);
      timer = setTimeout(callback, delay);
    };
    const hideCursor = () => cursor.classList.remove("visible", "clicking", "right-click");
    const reset = (showStatic = false) => {
      clearTimeout(timer);
      timer = null;
      stepIndex = 0;
      renderState(showStatic ? staticState : null);
      cursor.classList.remove("visible", "clicking", "right-click", "tooltip-left", "tooltip-above");
      status.textContent = showStatic ? staticText : idleText;
    };
    const run = () => {
      if (paused || !isActive()) return;
      if (stepIndex >= steps.length) {
        status.textContent = completeText;
        return queue(() => { reset(); run(); }, replayDelay);
      }
      const step = steps[stepIndex];
      if (!step.target) {
        renderState(step.state);
        status.textContent = step.caption;
        stepIndex += 1;
        return queue(run, step.delay ?? stepDelay);
      }
      const target = root.querySelector(`[data-onboarding-target="${step.target}"]`);
      if (!target) {
        stepIndex += 1;
        return run();
      }
      positionOnboardingCursor(root, cursor, target, step.click ?? "Left click");
      status.textContent = `Next: ${step.caption}`;
      queue(() => {
        cursor.classList.add("clicking");
        queue(() => {
          renderState(step.state);
          status.textContent = step.caption;
          cursor.classList.remove("clicking");
          stepIndex += 1;
          queue(run, step.delay ?? stepDelay);
        }, step.clickDelay ?? clickDelay);
      }, step.moveDelay ?? moveDelay);
    };
    const start = (forceMotion = false) => {
      const reducedMotion = !forceMotion && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      paused = reducedMotion;
      reset(reducedMotion);
      toggle.textContent = reducedMotion ? "Play demo" : "Pause demo";
      if (!reducedMotion) queue(run, initialDelay);
    };
    const stop = () => {
      clearTimeout(timer);
      timer = null;
      hideCursor();
    };
    const togglePlayback = () => {
      paused = !paused;
      toggle.textContent = paused ? "Play demo" : "Pause demo";
      if (paused) {
        stop();
        status.textContent = "Demo paused.";
      } else {
        run();
      }
    };
    toggle.addEventListener("click", togglePlayback);
    return Object.freeze({ start, stop, reset, toggle: togglePlayback, destroy() { stop(); toggle.removeEventListener("click", togglePlayback); } });
  }

  function createOnboardingController({
    dialog, stepLabel, progress, backButton, nextButton, skipButton, optOut,
    storagePrefix, demos = [], pageSelector = "[data-onboarding-page]",
  }) {
    const pages = [...dialog.querySelectorAll(pageSelector)];
    const disabledKey = `${storagePrefix}.onboarding.disabled.v1`;
    const serverKey = `${storagePrefix}.onboarding.server.v1`;
    let pageIndex = 0;
    let seenServerId = null;
    const storageValue = key => { try { return localStorage.getItem(key); } catch { return null; } };
    const disabled = () => storageValue(disabledKey) === "1";
    const rememberPreference = () => {
      try {
        if (optOut.checked) localStorage.setItem(disabledKey, "1");
        else localStorage.removeItem(disabledKey);
      } catch { /* The tutorial remains usable when browser storage is unavailable. */ }
    };
    const shouldShow = serverId => {
      if (!serverId || disabled()) return false;
      if (seenServerId === serverId || storageValue(serverKey) === serverId) return false;
      seenServerId = serverId;
      try { localStorage.setItem(serverKey, serverId); } catch { /* In-memory state still prevents repeats on this page. */ }
      return true;
    };
    const stopDemos = () => demos.forEach(demo => demo?.stop());
    const render = () => {
      pageIndex = Math.max(0, Math.min(pages.length - 1, pageIndex));
      pages.forEach((page, index) => { page.hidden = index !== pageIndex; });
      stepLabel.textContent = `${pageIndex + 1} of ${pages.length}`;
      progress.replaceChildren(...pages.map((_, index) => {
        const marker = document.createElement("i");
        marker.classList.toggle("active", index === pageIndex);
        marker.setAttribute("aria-hidden", "true");
        return marker;
      }));
      backButton.disabled = pageIndex === 0;
      const nextLabel = nextButton.querySelector("[data-onboarding-next-label], span");
      if (nextLabel) nextLabel.textContent = pageIndex === pages.length - 1 ? "Finish" : "Next";
      stopDemos();
      demos[pageIndex]?.start();
    };
    const open = () => {
      pageIndex = 0;
      optOut.checked = disabled();
      if (!dialog.open) dialog.showModal();
      render();
    };
    const close = () => {
      rememberPreference();
      if (dialog.open) dialog.close();
    };
    const previous = () => { pageIndex -= 1; render(); };
    const next = () => {
      if (pageIndex >= pages.length - 1) close();
      else { pageIndex += 1; render(); }
    };
    const onClose = () => { rememberPreference(); stopDemos(); };
    backButton.addEventListener("click", previous);
    nextButton.addEventListener("click", next);
    skipButton.addEventListener("click", close);
    dialog.addEventListener("close", onClose);
    return Object.freeze({
      open, close, next, previous, render, shouldShow,
      initialize(serverId) { if (shouldShow(serverId)) open(); },
      get page() { return pageIndex; },
      get disabled() { return disabled(); },
      destroy() {
        stopDemos();
        backButton.removeEventListener("click", previous);
        nextButton.removeEventListener("click", next);
        skipButton.removeEventListener("click", close);
        dialog.removeEventListener("close", onClose);
      }
    });
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    ICONS, decorateIconControl, createIconButton, createTooltipController,
    elementHasTruncatedText, automaticTooltipText, findTooltipTarget, installTooltipDelegation,
    setControlStatus, setControlLoading, withLoadingControl, installDetailsMenu,
    positionOnboardingCursor, createOnboardingDemo, createOnboardingController,
  });
})();
