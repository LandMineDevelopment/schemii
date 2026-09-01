export function reorderedValues(values, fromIndex, toIndex) {
  if (!Array.isArray(values)) throw new TypeError("Sortable values must be an array");
  if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) {
    throw new TypeError("Sortable indexes must be integers");
  }
  if (
    fromIndex < 0
    || fromIndex >= values.length
    || toIndex < 0
    || toIndex >= values.length
    || fromIndex === toIndex
  ) return [...values];
  const reordered = [...values];
  const [moved] = reordered.splice(fromIndex, 1);
  reordered.splice(toIndex, 0, moved);
  return reordered;
}

function directItems(container, selector) {
  return [...container.querySelectorAll(selector)].filter(item => item.parentElement === container);
}

function moveItem(container, item, items, toIndex) {
  const remaining = items.filter(candidate => candidate !== item);
  container.insertBefore(item, remaining[toIndex] || null);
}

export function installSortableList(container, {
  itemSelector,
  handleSelector = "[data-sort-handle]",
  onReorder = () => {},
  itemLabel = item => item.dataset.sortKey || "item",
} = {}) {
  if (!container || !itemSelector) throw new TypeError("A sortable container and item selector are required");
  let active = null;

  const items = () => directItems(container, itemSelector);
  const handleFor = item => item.querySelector(handleSelector);

  function clearDropMarkers() {
    for (const item of items()) item.classList.remove("sort-drop-before", "sort-drop-after");
  }

  function markDropPosition(before, candidates) {
    clearDropMarkers();
    const marker = before || candidates.at(-1);
    if (marker) marker.classList.add(before ? "sort-drop-before" : "sort-drop-after");
  }

  function captureVisualPositions() {
    const positions = new Map(items().map(item => [item, item.getBoundingClientRect().top]));
    for (const item of items()) item.getAnimations?.().forEach(animation => animation.cancel());
    return positions;
  }

  function prefersReducedMotion() {
    return container.ownerDocument?.defaultView
      ?.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }

  function animateFromPositions(positions) {
    if (prefersReducedMotion()) return;
    for (const item of items()) {
      // The lifted row owns its transform while it follows the pointer. Animating
      // that same property here would detach it from the user's grab point.
      if (item === active?.item) continue;
      const previousTop = positions.get(item);
      if (previousTop === undefined || typeof item.animate !== "function") continue;
      const deltaY = previousTop - item.getBoundingClientRect().top;
      if (Math.abs(deltaY) < 1) continue;
      item.animate(
        [
          { transform: `translate3d(0, ${deltaY}px, 0)` },
          { transform: "translate3d(0, 0, 0)" },
        ],
        { duration: 190, easing: "cubic-bezier(.22, 1, .36, 1)" },
      );
    }
  }

  function followPointer(clientY) {
    if (!active || !Number.isFinite(clientY)) return;
    // getBoundingClientRect includes the transform currently following the
    // pointer. Subtract it to recover the row's new layout position after a
    // reorder, then translate back to the exact point where it was grabbed.
    const currentTop = active.item.getBoundingClientRect().top;
    const layoutTop = currentTop - active.translateY;
    active.translateY = clientY - active.grabOffsetY - layoutTop;
    active.item.style.transform = `translate3d(0, ${active.translateY}px, 0)`;
  }

  function settleDraggedItem(drag) {
    const translateY = drag.translateY;
    drag.item.style.transform = "";
    if (
      Math.abs(translateY) < 1
      || prefersReducedMotion()
      || typeof drag.item.animate !== "function"
    ) return;
    drag.item.animate(
      [
        { transform: `translate3d(0, ${translateY}px, 0)` },
        { transform: "translate3d(0, 0, 0)" },
      ],
      { duration: 160, easing: "cubic-bezier(.22, 1, .36, 1)" },
    );
  }

  function updateActivePosition() {
    if (!active) return;
    active.item.dataset.sortPosition = `Position ${items().indexOf(active.item) + 1} of ${items().length}`;
  }

  function refresh() {
    const current = items();
    current.forEach((item, index) => {
      const handle = handleFor(item);
      if (!handle) return;
      const label = itemLabel(item);
      const instruction = `Reorder ${label}, position ${index + 1} of ${current.length}. Drag or use the arrow keys.`;
      handle.setAttribute("aria-label", instruction);
      handle.setAttribute("title", instruction);
    });
  }

  function restoreActiveItem() {
    if (!active) return;
    const positions = captureVisualPositions();
    const visualTop = positions.get(active.item);
    moveItem(container, active.item, items(), active.startIndex);
    if (visualTop !== undefined) {
      const layoutTop = active.item.getBoundingClientRect().top - active.translateY;
      active.translateY = visualTop - layoutTop;
      active.item.style.transform = `translate3d(0, ${active.translateY}px, 0)`;
    }
    animateFromPositions(positions);
  }

  function finish({ cancelled = false } = {}) {
    if (!active) return;
    const drag = active;
    if (cancelled) restoreActiveItem();
    settleDraggedItem(drag);
    drag.item.classList.remove("is-sorting");
    delete drag.item.dataset.sortPosition;
    container.classList.remove("is-sorting");
    clearDropMarkers();
    active = null;
    const finalIndex = items().indexOf(drag.item);
    refresh();
    if (!cancelled && finalIndex !== drag.startIndex) {
      onReorder(drag.startIndex, finalIndex, { input: "pointer", sortKey: drag.sortKey });
    }
  }

  function onPointerDown(event) {
    const handle = event.target.closest(handleSelector);
    const item = handle?.closest(itemSelector);
    if (!handle || !item || item.parentElement !== container) return;
    if (event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    const current = items();
    active = {
      item,
      pointerId: event.pointerId,
      startIndex: current.indexOf(item),
      sortKey: item.dataset.sortKey || "",
      grabOffsetY: event.clientY - item.getBoundingClientRect().top,
      translateY: 0,
    };
    item.classList.add("is-sorting");
    container.classList.add("is-sorting");
    updateActivePosition();
    // Capture on the stable container. Capturing on the handle is unreliable because
    // the handle moves with its row; mobile browsers may drop capture when that row
    // is reinserted to preview a new position.
    container.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event) {
    if (!active || event.pointerId !== active.pointerId) return;
    event.preventDefault();
    const candidates = items().filter(item => item !== active.item);
    const before = candidates.find(item => event.clientY < item.getBoundingClientRect().top + item.getBoundingClientRect().height / 2);
    markDropPosition(before, candidates);
    const alreadyInSlot = before === active.item.nextElementSibling
      || (!before && active.item === container.lastElementChild);
    if (!alreadyInSlot) {
      const positions = captureVisualPositions();
      container.insertBefore(active.item, before || null);
      updateActivePosition();
      animateFromPositions(positions);
    }
    followPointer(event.clientY);
  }

  function onPointerUp(event) {
    if (!active || event.pointerId !== active.pointerId) return;
    finish();
    if (container.hasPointerCapture?.(event.pointerId)) container.releasePointerCapture(event.pointerId);
  }

  function onPointerCancel(event) {
    if (!active || event.pointerId !== active.pointerId) return;
    finish({ cancelled: true });
    if (container.hasPointerCapture?.(event.pointerId)) container.releasePointerCapture(event.pointerId);
  }

  function onLostPointerCapture(event) {
    if (active && event.pointerId === active.pointerId) finish({ cancelled: true });
  }

  function onKeyDown(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    const handle = event.target.closest(handleSelector);
    const item = handle?.closest(itemSelector);
    if (!handle || !item || item.parentElement !== container) return;
    const current = items();
    const fromIndex = current.indexOf(item);
    const toIndex = fromIndex + (event.key === "ArrowUp" ? -1 : 1);
    if (toIndex < 0 || toIndex >= current.length) return;
    event.preventDefault();
    const sortKey = item.dataset.sortKey || "";
    moveItem(container, item, current, toIndex);
    refresh();
    onReorder(fromIndex, toIndex, { input: "keyboard", sortKey });
    const replacement = items().find(candidate => candidate.dataset.sortKey === sortKey) || item;
    handleFor(replacement)?.focus();
  }

  container.addEventListener("pointerdown", onPointerDown);
  container.addEventListener("pointermove", onPointerMove);
  container.addEventListener("pointerup", onPointerUp);
  container.addEventListener("pointercancel", onPointerCancel);
  container.addEventListener("lostpointercapture", onLostPointerCapture);
  container.addEventListener("keydown", onKeyDown);
  refresh();

  return {
    refresh,
    destroy() {
      finish({ cancelled: true });
      container.removeEventListener("pointerdown", onPointerDown);
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerup", onPointerUp);
      container.removeEventListener("pointercancel", onPointerCancel);
      container.removeEventListener("lostpointercapture", onLostPointerCapture);
      container.removeEventListener("keydown", onKeyDown);
    },
  };
}
