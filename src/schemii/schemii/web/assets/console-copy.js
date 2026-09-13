import { element } from "#common/dom.js";
import { ApiError, requestJson } from "#common/http.js";
import { createElapsedTimer, formatElapsed } from "#common/elapsed-time.js";

async function checked(response) {
  if (response.ok) return response;
  const body = await response.json().catch(() => null);
  throw new ApiError(body?.error?.message || `COPY request failed (${response.status})`, {
    status: response.status, code: body?.error?.code || "console_copy_failed",
  });
}

/** Stream bytes directly into the selected file; no retained Blob or row conversion. */
export async function streamCopyDownload(response, writable, onBytes = () => {}) {
  await checked(response);
  if (!response.body) throw new Error("The server returned no COPY data stream.");
  let received = 0;
  const counter = new TransformStream({ transform(chunk, controller) {
    received += chunk.byteLength;
    onBytes(received);
    controller.enqueue(chunk);
  } });
  await response.body.pipeThrough(counter).pipeTo(writable);
  return received;
}

/** COPY uses the current raw session and preserves the editor's exact SQL. */
export function openConsoleCopy({ workspaceId, sessionId, sql, commitMode = "manual", onStatus = () => {}, onError = () => {}, onActivity = () => {} }) {
  const base = `/api/v1/schemii/workspaces/${encodeURIComponent(workspaceId)}/console/sessions/${encodeURIComponent(sessionId)}`;
  const previousFocus = document.activeElement;
  const dialog = element("dialog", { className: "ui-dialog console-copy-dialog", attrs: { "aria-label": "COPY data" } });
  dialog.style.maxWidth = "min(46rem, calc(100vw - 2rem))";
  const details = element("pre", { text: sql });
  details.style.cssText = "white-space:pre-wrap;overflow:auto;max-height:35vh";
  const file = element("input", { type: "file", attrs: { "aria-label": "COPY input file" } });
  const upload = element("button", { type: "button", className: "ui-button", text: "Upload file · FROM STDIN" });
  const download = element("button", { type: "button", className: "ui-button", text: "Download · TO STDOUT" });
  const stop = element("button", { type: "button", className: "ui-button", text: "Stop", hidden: true });
  const close = element("button", { type: "button", className: "ui-button", text: "Close" });
  const status = element("p", { attrs: { role: "status", "aria-live": "polite" } });
  const browserLink = element("a", { text: "Open browser download", hidden: true, attrs: { download: "copy-data" } });
  const elapsed = element("span", { attrs: { "aria-label": "COPY elapsed time" } });
  const error = element("p", { attrs: { role: "alert" } });
  dialog.append(element("header", { className: "ui-dialog__head" }, [element("h2", { text: "COPY data" })]),
    element("div", { className: "ui-dialog__body" }, [
      element("p", { text: "Runs this SQL in your current database session. SQL options determine the file format. Your transaction stays under your control." }),
      details, element("label", { text: "Input file for COPY FROM STDIN" }, [file]), status, browserLink, elapsed, error,
    ]), element("footer", { className: "ui-dialog__actions" }, [upload, download, stop, close]));
  let busy = false, controller = null, transferred = 0, poll = null, polling = false;
  let resolveClosed;
  const closed = new Promise(resolve => { resolveClosed = resolve; });
  const timer = createElapsedTimer({ onTick: value => { elapsed.textContent = `${formatElapsed(value)}${transferred ? ` · ${transferred.toLocaleString()} bytes` : ""}`; } });
  function setBusy(value) {
    busy = value; file.disabled = value; upload.disabled = value; download.disabled = value;
    close.disabled = value; stop.hidden = !value; stop.disabled = false;
  }
  async function refresh() {
    if (polling) return;
    polling = true;
    try { const state = await requestJson(base); await onStatus(state); await onActivity(state); return state; }
    finally { polling = false; }
  }
  async function perform(kind) {
    if (busy) return;
    if (kind === "upload" && !file.files?.[0]) { error.textContent = "Choose the file to upload first."; file.focus(); return; }
    let writable = null;
    // The picker must be invoked during the button's user activation, before HTTP work.
    if (kind === "download" && typeof globalThis.showSaveFilePicker === "function") {
      try { const handle = await globalThis.showSaveFilePicker({ suggestedName: "copy-data" }); writable = await handle.createWritable(); }
      catch (failure) { if (failure.name !== "AbortError") error.textContent = failure.message; return; }
    }
    setBusy(true); transferred = 0; error.textContent = ""; browserLink.hidden = true;
    status.textContent = kind === "upload" ? "Uploading COPY data…" : "Downloading COPY data…";
    controller = new AbortController(); timer.start();
    poll = setInterval(() => { refresh().catch(() => {}); }, 1000);
    try {
      if (kind === "upload") {
        const ticket = await requestJson(`${base}/copy/uploads`, { method: "POST", body: { sql, commitMode }, signal: controller.signal });
        const response = await checked(await fetch(`${base}/copy/uploads/${encodeURIComponent(ticket.id)}`, {
          method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file.files[0],
          credentials: "same-origin", signal: controller.signal,
        }));
        const result = await response.json(); transferred = file.files[0].size; await onStatus(result);
        status.textContent = "COPY upload completed.";
      } else if (writable) {
        const response = await fetch(`${base}/copy/download`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql, commitMode }),
          credentials: "same-origin", signal: controller.signal,
        });
        transferred = await streamCopyDownload(response, writable, count => { transferred = count; });
        writable = null;
        status.textContent = "COPY download completed.";
      } else {
        await browserDownload();
      }
    } catch (failure) {
      if (writable) await writable.abort().catch(() => {});
      error.textContent = failure.name === "AbortError" ? "COPY transfer interrupted. Check the session status before continuing." : failure.message;
      onError(failure);
    } finally {
      clearInterval(poll); poll = null; timer.stop(); controller = null; browserLink.hidden = true; setBusy(false);
      await refresh().catch(failure => { error.textContent += ` Could not refresh session status: ${failure.message}`; });
    }
  }
  async function browserDownload() {
    const ticket = await requestJson(`${base}/copy/downloads`, { method: "POST", body: { sql, commitMode }, signal: controller.signal });
    const ticketPath = `${base}/copy/downloads/${encodeURIComponent(ticket.id)}`;
    browserLink.href = ticketPath; browserLink.hidden = false; browserLink.click();
    status.textContent = "Starting browser download… If it does not start, use Open browser download.";
    while (true) {
      const state = await requestJson(`${ticketPath}/status`, { signal: controller.signal });
      if (state.status === "succeeded") {
        browserLink.hidden = true;
        status.textContent = "COPY completed. Check your browser downloads for the file.";
        return;
      }
      if (state.status === "failed") { browserLink.hidden = true; throw new Error(state.errorMessage || "COPY download failed."); }
      if (state.status === "running") { browserLink.hidden = true; status.textContent = "Downloading COPY data…"; }
      await new Promise((resolve, reject) => {
        const signal = controller.signal;
        const cancel = () => { clearTimeout(timeout); reject(new DOMException("Aborted", "AbortError")); };
        const timeout = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, 500);
        if (signal.aborted) cancel();
        else signal.addEventListener("abort", cancel, { once: true });
      });
    }
  }
  stop.onclick = async () => {
    stop.disabled = true; status.textContent = "Stopping COPY…";
    try {
      await requestJson(`${base}/cancel`, { method: "POST" });
      controller?.abort();
    } catch (failure) { error.textContent = failure.message; stop.disabled = false; onError(failure); }
  };
  upload.onclick = () => perform("upload"); download.onclick = () => perform("download");
  close.onclick = () => dialog.close();
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", () => { clearInterval(poll); timer.stop(); dialog.remove(); previousFocus?.focus(); resolveClosed(); });
  document.body.append(dialog); dialog.showModal();
  return closed;
}
