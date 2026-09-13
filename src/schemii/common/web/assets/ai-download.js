import { createIconElement } from './ui.js';

export function validatedAssistantDownload(receipt, origin = globalThis.location?.origin) {
  if (receipt?.effect !== 'browser_download') return null;
  try {
    const url = new URL(receipt.url, origin);
    if (url.origin !== origin || url.username || url.password || url.search || url.hash) return null;
    const model = /^\/api\/v1\/schemoo\/models\/model_[a-f0-9]{32}$/.test(url.pathname);
    const rows = /^\/api\/v1\/common\/query-executions\/cex_[a-f0-9]{32}\/results\/res_[a-f0-9]{32}\/export\.csv$/.test(url.pathname);
    return model || rows ? { url: url.pathname, filename: model ? 'semantic-model.json' : 'query-result.csv' } : null;
  } catch { return null; }
}

export function assistantDownloadLink(receipt) {
  const download = validatedAssistantDownload(receipt);
  if (!download) return null;
  const link = document.createElement('a'); link.className = 'ui-icon-button compact'; link.href = download.url; link.download = download.filename;
  link.title = `Download ${download.filename}`; link.setAttribute('aria-label', link.title); link.append(createIconElement('download'));
  return link;
}
