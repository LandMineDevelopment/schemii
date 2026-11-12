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
const requiredFindingText = (value, name, max) => {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(`Finding ${name} must be non-empty text of at most ${max} characters.`);
  return value.trim();
};
export function findingInput(req) {
  if (!['critical', 'high', 'medium', 'low'].includes(req.severity)) throw new Error('Finding severity must be critical, high, medium, or low.');
  return {
    title: requiredFindingText(req.title, 'title', 180),
    severity: req.severity,
    steps: requiredFindingText(req.steps, 'steps', 6000),
    expected: requiredFindingText(req.expected, 'expected', 3000),
    actual: requiredFindingText(req.actual, 'actual', 3000),
  };
}
export function publicRun(run) {
  return { ...run, lanes: run.lanes.map(({ token, ...lane }) => lane) };
}
export function reportHTML(run) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  const links = paths => (paths || []).map(p => `<a href="${esc(p)}">${esc(p)}</a>`).join(' · ');
  const review = run.acceptance || {status:'review-pending',reasons:['Historical execution has no independently reconciled acceptance receipt.']};
  const verifications = (run.verificationHistory || []).map(v => `<article><h3>${esc(v.phase)} verification · ${esc(v.status)}</h3><p>${esc(v.note)} ${esc(v.reason)}</p><p>Source: ${esc(v.source?.commit)} · Profile: ${esc(v.profile)}</p>${(v.commands || []).map(c => `<p><code>${esc(c.command.join(' '))}</code>: ${esc(c.status)}; collected ${esc(c.summary?.collected ?? 'not instrumented')}; observed ${esc(c.summary?.unique_observed ?? 'not instrumented')}; skipped ${esc(c.summary?.attempt_outcomes?.skipped ?? 'not instrumented')}; first-attempt failures ${esc(c.summary?.first_attempt_failures ?? 'not instrumented')}; retry recovered ${esc(c.summary?.retry_recovered ?? 'not instrumented')}</p>`).join('')}</article>`).join('');
  const resolutions = [...(run.remediations || []), ...(run.importedFindings || [])].map(r => `<article><h3>${esc(r.findingId)} · ${esc(r.disposition)}</h3><p>Original run: ${esc(r.originalRun)}; reproduction attempt: ${esc(r.reproduction?.attempt)}; independent reviewer: ${esc(r.reproduction?.reviewer)}</p><p>${esc(r.provenance)}<br>${esc(r.note)}</p><p>Source: ${esc(r.source)}. Original finding and failed attempts are preserved.</p></article>`).join('');
  const coverage = run.lanes.flatMap(lane => (lane.coverageGroups || []).map(group => {
    const d = group.coverage.declaration;
    const captures = (lane.captures || []).filter(capture => capture.coverage?.sha256 === group.coverage.sha256);
    return `<article><h3>${esc(lane.id)} · ${esc(group.status)} · ${esc(d.id)}</h3><p>Declared before capture: ${esc(d.declaredAt)}; session: ${esc(d.session)}; browser activity epoch: ${esc(d.activity?.epoch)}.</p><p>Source: <code>${esc(d.source)}</code>; route: ${esc(d.url)}; viewport: ${esc(d.viewport?.width)} × ${esc(d.viewport?.height)}; declaration: <code>${esc(group.coverage.sha256)}</code>.</p><ul>${d.members.map(member => {
      const attempt = lane.scenarios.find(s => s.id === member.scenario)?.attempts?.find(a => a.scenarioAttempt === member.attempt);
      const reviewed = attempt && (run.reviews || []).filter(r => r.targetLane === lane.id && r.scenario === member.scenario && r.attempt === attempt.id).at(-1);
      return `<li>${esc(member.scenario)} · scenario attempt ${esc(member.attempt)} · function ${esc(attempt?.functional || 'not-run')}, visual ${esc(attempt?.visual || 'not-run')} · independent review ${esc(reviewed?.verdict || 'pending')}</li>`;
    }).join('')}</ul><p>Images: ${links(captures.map(c => c.path))}</p><p>${esc(group.reason || 'Active; remaining cases still require checkpoints.')}</p></article>`;
  })).join('');
  const findings = (run.findings || []).map(f => `<article><h3>${esc(f.id)} · ${esc(f.title)}</h3><p><strong>${esc(f.severity)}</strong> · ${esc(f.verificationStatus || 'unverified')} · ${esc(f.username)} · ${esc(f.scenario)}</p><p><strong>Steps</strong><br><span class="note">${esc(f.steps)}</span></p><p><strong>Expected</strong><br><span class="note">${esc(f.expected)}</span></p><p><strong>Actual</strong><br><span class="note">${esc(f.actual)}</span></p><p>Evidence: ${links(f.evidence)}</p></article>`).join('');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>QA ${esc(run.id)}</title><style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;color:#172635;background:#f5f7fa}table{border-collapse:collapse;width:100%;background:white}th,td{padding:.7rem;border:1px solid #cad3dd;text-align:left;vertical-align:top}h1{font-size:1.6rem}code{overflow-wrap:anywhere}a{color:#1659aa}.note{white-space:pre-wrap}article{background:white;border:1px solid #cad3dd;padding:1rem;margin:1rem 0}article h3{margin-top:0}</style><h1>Manual UI QA · ${esc(run.id)}</h1><p>Run: <strong>${esc(run.status)}</strong> · Execution: ${esc(run.executionStatus || run.status)} · ${esc(run.updatedAt)}</p><p>${esc(run.summary || '')}</p><p>Independent acceptance: <strong>${esc(review.status)}</strong>. Intended: ${esc(review.intended ?? 'unknown')}; dispatched: ${esc(review.dispatched ?? 'unknown')}; started: ${esc(review.started ?? 'unknown')}; first-attempt failures: ${esc(review.firstAttemptFailures ?? 'unknown')}; completed: ${esc(review.completed ?? 'unknown')}; accepted review receipts: ${esc(review.reviewed ?? 0)}.</p><p class="note">${esc((review.reasons || []).join('\n'))}</p><p>App: ${esc(run.baseURL)} · Build: <code>${esc(run.deployment?.identity?.commit || 'not verified')}</code></p><p>Execution: ${esc(run.headless ? 'headless' : 'visible browser processes')} · ${esc(run.parallel)} parallel slots. Controller: ${esc(run.controller || 't3')}; requested model: ${esc(run.agentModel || 'installed default')}; requested reasoning: ${esc(run.agentReasoning || 'installed default')}; peak worker processes: ${esc(run.workers?.peakProcesses || 0)}; peak active agent turns: ${esc(run.workers?.peakTurns || 0)}. Native binding and image inspection notes are cooperative attestations; independent review and application acceptance remain required. Browser probes verify the harness; they are not application acceptance results.</p><table><thead><tr><th>Account / track</th><th>State</th><th>Scenario</th><th>Function</th><th>Style</th><th>Notes / evidence</th></tr></thead><tbody>${run.lanes.flatMap(l => l.scenarios.map(s => `<tr><td>${esc(l.username)}<br>${esc(l.track)}</td><td>${esc(l.status)}<br>Worker: ${esc(l.workerLifecycle?.status || 'not recorded')}<br>${esc(l.workerLifecycle?.error || '')}</td><td>${esc(s.title)}</td><td>${esc(s.functional)}<br>Initial: ${esc(s.initialResult?.functional ?? s.attempts?.[0]?.functional ?? "not recorded")}</td><td>${esc(s.visual)}<br>Initial: ${esc(s.initialResult?.visual ?? s.attempts?.[0]?.visual ?? "not recorded")}</td><td class="note">${esc(s.note)}<br>${links(s.evidence)}</td></tr>`)).join('')}</tbody></table><h2>Declared same-state capture coverage</h2>${coverage || '<p>No shared coverage declared; single-case evidence scope applies.</p>'}<h2>Draft findings (${(run.findings || []).length})</h2>${findings || '<p>No draft findings recorded.</p>'}<h2>Application verification receipts</h2>${verifications || '<p>No focused/final CI receipt imported.</p>'}<h2>Finding remediation and external review</h2>${resolutions || '<p>No independent remediation imported.</p>'}<h2>Setup and coverage limitations</h2><p class="note">${esc(run.error || 'No recorded startup blocker.')}</p><p>Only scenarios with explicit recorded evidence have been assessed. Retained accounts and resources are not deleted by cleanup.</p></html>`;
}
