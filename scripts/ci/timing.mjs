// Public evidence is built from fixed fields. Never pass errors, attachments,
// titles, annotations, fixture values or environment objects to the writer.
import { createHash } from 'node:crypto';
import { appendFileSync, lstatSync, mkdirSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

export const hash = value => createHash('sha256').update(String(value)).digest('hex');
export const milliseconds = value => Number.isFinite(value) && value >= 0 ? Math.round(value * 1000) / 1000 : 0;

// A disposable execution cwd must never become the identity of the source.
// The override is closed to this module's actual repository, not an arbitrary
// directory provided by the caller.
export function originalSourceRoot(environment = process.env) {
  const root = environment.SCHEMII_E2E_SOURCE_ROOT;
  if (root === undefined) return undefined;
  const original = realpathSync(fileURLToPath(new URL('../../', import.meta.url)));
  if (typeof root !== 'string' || !isAbsolute(root) || root !== original) {
    throw new Error('Invalid original browser source root');
  }
  return original;
}

export function metadata(lane, project = 'none', shard = 0) {
  if (!['node', 'python', 'postgres', 'browser'].includes(lane)
      || !['none', 'desktop-chromium', 'android-chromium'].includes(project)
      || ![0, 1, 2, 3, 4, 5, 6].includes(shard)
      || (lane !== 'browser' && (project !== 'none' || shard !== 0))
      || (lane === 'browser' && project === 'none')) throw new Error('Invalid timing lane');
  const sha = process.env.CI_TELEMETRY_SHA || '';
  const numeric = name => /^\d{1,20}$/.test(process.env[name] || '')
    && Number(process.env[name]) <= 1e15 ? Number(process.env[name]) : 0;
  return { schema: 1, source_sha: /^[a-f0-9]{40}$/.test(sha) ? sha : null,
    run_id: numeric('CI_TELEMETRY_RUN_ID'), run_attempt: numeric('CI_TELEMETRY_RUN_ATTEMPT'),
    lane, project, shard };
}

export function writer(file, meta, { exclusive = false } = {}) {
  if (exclusive) {
    for (let path = dirname(file); ; path = dirname(path)) {
      let info;
      try { info = lstatSync(path); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (info && (!info.isDirectory() || info.isSymbolicLink())) throw new Error('Unsafe timing output directory');
      if (path === dirname(path)) break;
    }
  }
  mkdirSync(dirname(file), { recursive: true, ...(exclusive ? { mode: 0o700 } : {}) });
  writeFileSync(file, '', exclusive ? { flag: 'wx', mode: 0o600 } : undefined);
  let bytes = 0;
  return record => {
    const line = JSON.stringify({ ...meta, ...record }) + '\n';
    bytes += Buffer.byteLength(line);
    if (exclusive && bytes > 20 * 1024 * 1024) throw new Error('Timing evidence exceeds size budget');
    appendFileSync(file, line);
  };
}

export function attempt(id, source, line, number, outcome, setup, execution, teardown) {
  if (!['passed', 'failed', 'timed-out', 'skipped', 'cancelled', 'not-run'].includes(outcome))
    throw new Error('Invalid timing outcome');
  return { kind: 'attempt', test_id: hash(id), source_id: hash(source),
    source_line: Number.isInteger(line) && line >= 0 ? line : 0,
    attempt: Number.isInteger(number) && number >= 0 ? number : 0, outcome,
    skip: outcome === 'skipped' ? 'declared-or-runtime' : 'none',
    setup_ms: milliseconds(setup), execution_ms: milliseconds(execution),
    teardown_ms: milliseconds(teardown) };
}
