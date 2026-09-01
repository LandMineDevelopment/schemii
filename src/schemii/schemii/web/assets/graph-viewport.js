const DEFAULT_MIN_ZOOM = 0.25;
const DEFAULT_MAX_ZOOM = 1.7;
const MAX_COORDINATE = 1_000_000;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function finite(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
}

export class GraphViewport {
  constructor({
    host,
    stage,
    zoomOutput = null,
    initialView = { x: 0, y: 0, zoom: 1 },
    minZoom = DEFAULT_MIN_ZOOM,
    maxZoom = DEFAULT_MAX_ZOOM,
    canStartPan = () => true,
    scheduleFrame = callback => window.requestAnimationFrame(callback),
    cancelFrame = frame => window.cancelAnimationFrame(frame),
  }) {
    this.host = host;
    this.stage = stage;
    this.zoomOutput = zoomOutput;
    this.minZoom = minZoom;
    this.maxZoom = maxZoom;
    this.canStartPan = canStartPan;
    this.scheduleFrame = scheduleFrame;
    this.cancelFrame = cancelFrame;
    this.view = {
      x: finite(initialView.x, 0),
      y: finite(initialView.y, 0),
      zoom: clamp(finite(initialView.zoom, 1), minZoom, maxZoom),
    };
    this.pan = null;
    this.drag = null;
    // TODO(graph-viewport-keyboard): Add configurable Arrow-key panning when the focus target is the viewport host itself, without stealing node-drag or form-control keys, and cover both catalog and API canvas consumers with keyboard tests.
    this.listeners = {
      pointerdown: event => this.startPan(event),
      pointermove: event => this.movePan(event),
      pointerup: event => this.endPan(event),
      pointercancel: event => this.endPan(event),
      lostpointercapture: event => this.endPan(event),
      wheel: event => this.handleWheel(event),
    };
    for (const [type, listener] of Object.entries(this.listeners)) {
      this.host.addEventListener(type, listener, type === "wheel" ? { passive: false } : undefined);
    }
    this.applyView();
  }

  getView() {
    return { ...this.view };
  }

  setView(view) {
    this.view = {
      x: finite(view?.x, this.view.x),
      y: finite(view?.y, this.view.y),
      zoom: clamp(finite(view?.zoom, this.view.zoom), this.minZoom, this.maxZoom),
    };
    this.applyView();
  }

  applyView() {
    this.stage.style.transform = `translate(${this.view.x}px, ${this.view.y}px) scale(${this.view.zoom})`;
    if (!this.zoomOutput) return;
    const label = `${Math.round(this.view.zoom * 100)}%`;
    this.zoomOutput.value = label;
    this.zoomOutput.textContent = label;
  }

  startPan(event) {
    if (event.button !== 0 || this.pan || this.drag || !this.canStartPan(event)) return;
    event.preventDefault();
    this.pan = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: this.view.x,
      originY: this.view.y,
    };
    this.host.classList.add("panning");
    this.host.setPointerCapture(event.pointerId);
  }

  movePan(event) {
    if (!this.pan || event.pointerId !== this.pan.pointerId) return;
    event.preventDefault();
    this.view.x = this.pan.originX + event.clientX - this.pan.startX;
    this.view.y = this.pan.originY + event.clientY - this.pan.startY;
    this.applyView();
  }

  endPan(event) {
    if (!this.pan || event.pointerId !== this.pan.pointerId) return;
    const pointerId = this.pan.pointerId;
    this.pan = null;
    this.host.classList.remove("panning");
    if (this.host.hasPointerCapture?.(pointerId)) this.host.releasePointerCapture(pointerId);
  }

  handleWheel(event) {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      this.zoomAt(event.deltaY < 0 ? 0.1 : -0.1, event.clientX, event.clientY);
      return;
    }
    this.view.x -= event.deltaX;
    this.view.y -= event.deltaY;
    this.applyView();
  }

  screenToWorld(clientX, clientY) {
    const bounds = this.host.getBoundingClientRect();
    return {
      x: (clientX - bounds.left - this.view.x) / this.view.zoom,
      y: (clientY - bounds.top - this.view.y) / this.view.zoom,
    };
  }

  zoomAt(amount, clientX, clientY) {
    const next = clamp(this.view.zoom + amount, this.minZoom, this.maxZoom);
    if (next === this.view.zoom) return false;
    const bounds = this.host.getBoundingClientRect();
    const pointX = clientX - bounds.left;
    const pointY = clientY - bounds.top;
    const worldX = (pointX - this.view.x) / this.view.zoom;
    const worldY = (pointY - this.view.y) / this.view.zoom;
    this.view.x = pointX - worldX * next;
    this.view.y = pointY - worldY * next;
    this.view.zoom = next;
    this.applyView();
    return true;
  }

  zoomBy(amount, { x = this.host.clientWidth / 2, y = this.host.clientHeight / 2 } = {}) {
    const bounds = this.host.getBoundingClientRect();
    return this.zoomAt(amount, bounds.left + x, bounds.top + y);
  }

  fitBounds(bounds, {
    left = 20,
    top = 20,
    right = 20,
    bottom = 20,
    paddingScale = 0.9,
    maxZoom = this.maxZoom,
  } = {}) {
    if (!bounds || ![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite)) return false;
    if (bounds.maxX < bounds.minX || bounds.maxY < bounds.minY) return false;
    const width = Math.max(120, this.host.clientWidth - left - right);
    const height = Math.max(120, this.host.clientHeight - top - bottom);
    const contentWidth = Math.max(1, bounds.maxX - bounds.minX);
    const contentHeight = Math.max(1, bounds.maxY - bounds.minY);
    const zoom = clamp(
      Math.min(width / contentWidth, height / contentHeight) * paddingScale,
      this.minZoom,
      Math.min(maxZoom, this.maxZoom),
    );
    this.setView({
      x: left + (width - contentWidth * zoom) / 2 - bounds.minX * zoom,
      y: top + (height - contentHeight * zoom) / 2 - bounds.minY * zoom,
      zoom,
    });
    return true;
  }

  beginNodeDrag(event, {
    key,
    element,
    position,
    constrain = candidate => ({
      x: clamp(candidate.x, -MAX_COORDINATE, MAX_COORDINATE),
      y: clamp(candidate.y, -MAX_COORDINATE, MAX_COORDINATE),
    }),
    onStart = () => {},
    onFrame = () => {},
    onCommit = () => {},
    onCancel = () => {},
  }) {
    if (this.drag || this.pan || event.button !== 0) return false;
    event.preventDefault();
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    this.drag = {
      pointerId,
      key,
      element,
      handle,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      x: position.x,
      y: position.y,
      renderedX: position.x,
      renderedY: position.y,
      moved: false,
      frame: null,
      cleanup: null,
      constrain,
      onFrame,
      onCommit,
      onCancel,
    };
    element.classList.add("dragging");
    handle.setPointerCapture(pointerId);
    const move = moveEvent => this.moveNodeDrag(moveEvent);
    const end = endEvent => this.endNodeDrag(endEvent);
    const cleanup = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      handle.removeEventListener("lostpointercapture", end);
    };
    this.drag.cleanup = cleanup;
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    handle.addEventListener("lostpointercapture", end);
    onStart();
    return true;
  }

  moveNodeDrag(event) {
    const drag = this.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault();
    const next = drag.constrain({
      x: drag.originX + (event.clientX - drag.startX) / this.view.zoom,
      y: drag.originY + (event.clientY - drag.startY) / this.view.zoom,
    });
    if (!drag.moved && Math.abs(next.x - drag.originX) <= 1 && Math.abs(next.y - drag.originY) <= 1) return;
    drag.moved = true;
    drag.x = next.x;
    drag.y = next.y;
    this.scheduleNodeDragRender();
  }

  scheduleNodeDragRender() {
    const drag = this.drag;
    if (!drag || drag.frame !== null) return;
    drag.frame = this.scheduleFrame(() => {
      if (this.drag !== drag) return;
      drag.frame = null;
      this.renderNodeDrag(drag);
    });
  }

  renderNodeDrag(drag, { commit = false } = {}) {
    const geometryChanged = drag.x !== drag.renderedX || drag.y !== drag.renderedY;
    if (commit) {
      drag.element.style.left = `${drag.x}px`;
      drag.element.style.top = `${drag.y}px`;
      drag.element.style.transform = "";
    } else {
      drag.element.style.transform = `translate3d(${drag.x - drag.originX}px, ${drag.y - drag.originY}px, 0)`;
    }
    if (!geometryChanged) return;
    drag.onFrame({ x: drag.x, y: drag.y });
    drag.renderedX = drag.x;
    drag.renderedY = drag.y;
  }

  endNodeDrag(event) {
    const drag = this.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.cleanup?.();
    if (drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
    if (drag.frame !== null) this.cancelFrame(drag.frame);
    const changed = drag.x !== drag.originX || drag.y !== drag.originY;
    if (drag.moved) this.renderNodeDrag(drag, { commit: true });
    drag.element.classList.remove("dragging");
    this.drag = null;
    if (changed) drag.onCommit({ x: drag.x, y: drag.y });
  }

  dragPosition(key) {
    if (!this.drag || this.drag.key !== key) return null;
    return { x: this.drag.x, y: this.drag.y };
  }

  cancelNodeDrag() {
    const drag = this.drag;
    if (!drag) return;
    drag.cleanup?.();
    if (drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
    if (drag.frame !== null) this.cancelFrame(drag.frame);
    drag.element.classList.remove("dragging");
    drag.element.style.transform = "";
    this.drag = null;
    drag.onCancel();
  }

  cancelInteractions() {
    this.cancelNodeDrag();
    if (!this.pan) return;
    const pointerId = this.pan.pointerId;
    this.pan = null;
    this.host.classList.remove("panning");
    if (this.host.hasPointerCapture?.(pointerId)) this.host.releasePointerCapture(pointerId);
  }

  destroy() {
    this.cancelInteractions();
    for (const [type, listener] of Object.entries(this.listeners)) {
      this.host.removeEventListener(type, listener);
    }
  }
}
