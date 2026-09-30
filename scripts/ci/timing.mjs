// Public evidence is built from fixed fields. Never pass errors, attachments,
// titles, annotations, fixture values or environment objects to the writer.
import { createHash } from 'node:crypto';
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const hash = value => createHash('sha256').update(String(value)).digest('hex');
export const milliseconds = value => Number.isFinite(value) && value >= 0 ? Math.round(value * 1000) / 1000 : 0;

export function metadata(lane, project = 'none', shard = 0) {
  if (!['node', 'python', 'postgres', 'browser'].includes(lane)
      || !['none', 'desktop-chromium', 'android-chromium'].includes(project)
      || ![0, 1, 2].includes(shard)) throw new Error('Invalid timing lane');
  const sha = process.env.CI_TELEMETRY_SHA || '';
  const numeric = name => /^\d{1,20}$/.test(process.env[name] || '') ? Number(process.env[name]) : 0;
  return { schema: 1, source_sha: /^[a-f0-9]{40}$/.test(sha) ? sha : null,
    run_id: numeric('CI_TELEMETRY_RUN_ID'), run_attempt: numeric('CI_TELEMETRY_RUN_ATTEMPT'),
    lane, project, shard };
}

export function writer(file, meta) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, '');
  return record => appendFileSync(file, JSON.stringify({ ...meta, ...record }) + '\n');
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
