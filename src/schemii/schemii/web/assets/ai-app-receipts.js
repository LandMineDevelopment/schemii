export function designExportReceipt(receipt) {
  if (!receipt || !/^ws_[a-f0-9]{32}$/.test(receipt.workspaceId || '') || !Number.isInteger(receipt.designRevision) || receipt.designRevision < 0 || !/^[a-f0-9]{64}$/.test(receipt.sha256 || '') || typeof receipt.content !== 'string' || receipt.content.length > 16 * 1024 * 1024) return null;
  const extension = { 'application/sql': 'sql', 'application/json': 'json' }[receipt.mediaType];
  if (!extension || typeof receipt.fileName !== 'string' || !/^[a-zA-Z0-9_.-]{1,255}$/.test(receipt.fileName) || !receipt.fileName.endsWith(`.${extension}`)) return null;
  return { content: receipt.content, fileName: receipt.fileName, mediaType: receipt.mediaType };
}

export function workspaceReceiptUrl(receipt, operation) {
  const workspace = operation === 'open_postgres_workspace' ? receipt?.workspace : receipt;
  if (!['create_workspace', 'open_postgres_workspace'].includes(operation) || !/^ws_[a-f0-9]{32}$/.test(workspace?.id || '')) return null;
  return `/?workspace=${workspace.id}`;
}
