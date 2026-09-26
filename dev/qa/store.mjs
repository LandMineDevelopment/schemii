import { readFile, writeFile, mkdir, rename, stat, appendFile } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const runRoot = join(root, 'artifacts/qa');
export const stamp = () => new Date().toISOString();
export function runPath(id) {
  if (!/^qa-[a-z0-9-]{6,80}$/.test(id || '')) throw new Error('Invalid run ID.');
  return join(runRoot, id);
}
export async function privateDir(path) { await mkdir(path, { recursive: true, mode: 0o700 }); }
export async function writeJSON(path, data) {
  await privateDir(dirname(path));
  const temp = `${path}.${randomUUID()}.tmp`;
  await writeFile(temp, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  await rename(temp, path);
}
export async function readJSON(path) { return JSON.parse(await readFile(path, 'utf8')); }
export async function privateJSON(path) {
  const s = await stat(path);
  if (!s.isFile() || (s.mode & 0o077)) throw new Error('Private JSON file must be a regular file with mode 0600.');
  return readJSON(path);
}
export async function credentials(path) {
  const data = await privateJSON(resolve(path));
  const rows = Array.isArray(data.accounts) ? data.accounts : Object.entries(data.accounts || data).map(([username, value]) => ({ username, ...(typeof value === 'string' ? { password: value } : value) }));
  const found = new Map();
  for (const row of rows) {
    if (!/^[a-zA-Z0-9_.@-]{1,64}$/.test(row.username || '') || typeof row.password !== 'string' || !row.password || found.has(row.username)) throw new Error('Credential file needs unique accounts with username and password.');
    found.set(row.username, { username: row.username, password: row.password });
  }
  return found;
}
export async function event(dir, data) { await appendFile(join(dir, 'events.jsonl'), JSON.stringify({ at: stamp(), ...data }) + '\n', { mode: 0o600 }); }
export function publicRun(run) {
  return { ...run, lanes: run.lanes.map(({ token, ...lane }) => lane) };
}
export function reportHTML(run) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  const links = paths => (paths || []).map(p => `<a href="${esc(p)}">${esc(p)}</a>`).join(' · ');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>QA ${esc(run.id)}</title><style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;color:#172635;background:#f5f7fa}table{border-collapse:collapse;width:100%;background:white}th,td{padding:.7rem;border:1px solid #cad3dd;text-align:left;vertical-align:top}h1{font-size:1.6rem}code{overflow-wrap:anywhere}a{color:#1659aa}.note{white-space:pre-wrap}</style><h1>Manual UI QA · ${esc(run.id)}</h1><p>Run: <strong>${esc(run.status)}</strong> · ${esc(run.updatedAt)}</p><p>${esc(run.summary || '')}</p><p>App: ${esc(run.baseURL)} · Build: <code>${esc(run.deployment?.identity?.commit || 'not verified')}</code></p><p>Execution: ${esc(run.headless ? 'headless' : 'visible browser processes')} · ${esc(run.parallel)} parallel slots. Controller: ${esc(run.controller || 't3')}; peak worker processes: ${esc(run.workers?.peakProcesses || 0)}; peak active agent turns: ${esc(run.workers?.peakTurns || 0)}. Browser probes verify the harness; they are not application acceptance results.</p><table><thead><tr><th>Account / track</th><th>State</th><th>Scenario</th><th>Function</th><th>Style</th><th>Notes / evidence</th></tr></thead><tbody>${run.lanes.flatMap(l => l.scenarios.map(s => `<tr><td>${esc(l.username)}<br>${esc(l.track)}</td><td>${esc(l.status)}</td><td>${esc(s.title)}</td><td>${esc(s.functional)}</td><td>${esc(s.visual)}</td><td class="note">${esc(s.note)}<br>${links(s.evidence)}</td></tr>`)).join('')}</tbody></table><h2>Setup and coverage limitations</h2><p class="note">${esc(run.error || 'No recorded startup blocker.')}</p><p>Only scenarios with explicit recorded evidence have been assessed. Retained accounts and resources are not deleted by cleanup.</p></html>`;
}
