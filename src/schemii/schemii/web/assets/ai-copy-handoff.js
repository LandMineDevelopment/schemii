import { element } from '#common/dom.js';
import { requestJson } from '#common/http.js';

export function validateCopyHandoff(receipt, workspaceId, origin = globalThis.location?.origin) {
  if (receipt?.effect !== 'browser_copy_handoff' || receipt.sqlExecuted !== false || !['upload', 'download'].includes(receipt.direction)) return null;
  if (![workspaceId, receipt.sessionId, receipt.ticketId].every(value => typeof value === 'string' && /^[a-zA-Z0-9_-]+$/.test(value))) return null;
  const base = `/api/v1/schemii/workspaces/${workspaceId}/console/sessions/${receipt.sessionId}`;
  const expected = `${base}/copy/${receipt.direction}s/${receipt.ticketId}`;
  try {
    const url = new URL(receipt.url, origin);
    if (url.origin !== origin || url.pathname !== expected || url.search || url.hash || url.username || url.password || receipt.method !== (receipt.direction === 'upload' ? 'PUT' : 'GET')) return null;
    return { ...receipt, url: expected, base };
  } catch { return null; }
}

export function openCopyHandoff(receipt, workspaceId) {
  const handoff = validateCopyHandoff(receipt, workspaceId);
  if (!handoff) throw new Error('This COPY handoff is invalid. Request a new transfer.');
  const previous = document.activeElement;
  const dialog = element('dialog', { className: 'ui-dialog console-copy-dialog', attrs: { 'aria-label': 'AI COPY transfer' } });
  const status = element('p', { attrs: { role: 'status', 'aria-live': 'polite' } });
  const error = element('p', { attrs: { role: 'alert' } });
  const file = element('input', { type: 'file', attrs: { 'aria-label': 'COPY input file' } });
  const start = element(handoff.direction === 'upload' ? 'button' : 'a', { text: handoff.direction === 'upload' ? 'Upload selected file' : 'Download COPY file', className: 'ui-button', attrs: handoff.direction === 'upload' ? { type: 'button' } : { href: handoff.url, download: 'copy-data' } });
  const stop = element('button', { text: 'Stop', type: 'button', className: 'ui-button', hidden: true });
  const close = element('button', { text: 'Close', type: 'button', className: 'ui-button' });
  const sql = element('pre', { text: handoff.sql || '' }); sql.style.cssText = 'white-space:pre-wrap;max-height:30vh;overflow:auto';
  const body = element('div', { className: 'ui-dialog__body' }, [element('p', { text: 'The SQL has not run yet. Starting this transfer runs it in the approved database session. File contents are transferred by your browser and are not sent to the model.' }), sql, ...(handoff.direction === 'upload' ? [file] : []), status, error]);
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'COPY transfer' })]), body, element('footer', { className: 'ui-dialog__actions' }, [start, stop, close]));
  let busy = false, consumed = false, xhr = null, polling = null;
  function finish(message) { busy = false; clearInterval(polling); polling = null; stop.hidden = true; close.disabled = false; status.textContent = message; }
  async function checkDownload() {
    try {
      const result = await requestJson(`${handoff.url}/status`);
      if (result.status === 'succeeded') finish('COPY completed. Check your browser downloads.');
      else if (result.status === 'failed') { error.textContent = result.errorMessage || 'COPY failed.'; finish('Transfer finished with an error.'); }
      else status.textContent = result.status === 'running' ? 'Downloading COPY data…' : 'Waiting for browser download…';
    } catch (failure) { error.textContent = failure.message; finish('Could not check transfer status. Inspect the console session before retrying.'); }
  }
  start.addEventListener('click', event => {
    if (busy || consumed) { event.preventDefault(); return; }
    if (handoff.direction === 'upload' && !file.files?.[0]) { event.preventDefault(); error.textContent = 'Choose a file first.'; file.focus(); return; }
    busy = true; consumed = true; file.disabled = true; close.disabled = true; stop.hidden = false; start.setAttribute('aria-disabled', 'true'); error.textContent = '';
    if (handoff.direction === 'download') { status.textContent = 'Starting browser download…'; polling = setInterval(checkDownload, 1000); return; }
    xhr = new XMLHttpRequest(); xhr.open('PUT', handoff.url); xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = event => { status.textContent = `Uploading: ${event.loaded.toLocaleString()}${event.lengthComputable ? ` / ${event.total.toLocaleString()}` : ''} bytes`; };
    xhr.onload = () => { if (xhr.status >= 200 && xhr.status < 300) finish('COPY upload completed.'); else { let message; try { message = JSON.parse(xhr.responseText)?.error?.message; } catch {} error.textContent = message || `COPY failed (${xhr.status}).`; finish('Transfer finished with an error.'); } };
    xhr.onerror = () => { error.textContent = 'Connection lost. Inspect the session before continuing; do not replay the upload blindly.'; finish('Upload interrupted.'); };
    xhr.onabort = () => finish('Upload stopped. Check the open transaction before continuing.');
    status.textContent = 'Uploading COPY data…'; xhr.send(file.files[0]);
  });
  stop.onclick = async () => { stop.disabled = true; try { await requestJson(`${handoff.base}/cancel`, { method: 'POST' }); xhr?.abort(); finish('Transfer stopped. Check the open transaction before continuing.'); } catch (failure) { error.textContent = failure.message; stop.disabled = false; } };
  close.onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { clearInterval(polling); dialog.remove(); previous?.focus(); });
  document.body.append(dialog); dialog.showModal();
}
