import { relative } from 'node:path';
import { attempt, hash, metadata, milliseconds } from './timing.mjs';

export default async function* timingReporter(source) {
  const meta = metadata('node');
  const encode = record => JSON.stringify({ ...meta, ...record }) + '\n';
  const planned = new Set();
  let ended = false;
  let fileFailed = false;
  yield encode({ kind: 'start', planned: null });
  for await (const event of source) {
    const data = event.data || {};
    const file = data.file ? relative(process.cwd(), data.file) : 'unknown';
    const identity = `${file}:${data.line || 0}:${data.column || 0}:${data.name}`;
    if (data.line === 1 && data.column === 1 && [file, data.file].includes(data.name)) {
      // A failed file bootstrap may have undiscovered tests. Keep it outside
      // individual test denominators but let it own the terminal error status.
      if (event.type === 'test:fail') fileFailed = true;
      continue;
    }
    if (event.type === 'test:enqueue' && data.type !== 'suite') {
      const id = hash(identity);
      if (!planned.has(id)) {
        planned.add(id);
        yield encode({ kind: 'plan', test_id: id });
      }
    }
    if (['test:pass', 'test:fail'].includes(event.type) && data.details?.type !== 'suite') {
      const id = hash(identity);
      // Parent file wrappers are not test cases. Their failures still count as
      // infrastructure failure in the terminal summary, without duplicate cases.
      if (!planned.has(id)) {
        planned.add(id);
        yield encode({ kind: 'plan', test_id: id });
      }
      const outcome = data.skip || data.todo ? 'skipped'
        : data.details?.error?.failureType === 'cancelledByParent' ? 'cancelled'
          : event.type === 'test:pass' ? 'passed' : 'failed';
      yield encode(attempt(identity, file, data.line, 0, outcome, 0, data.details?.duration_ms, 0));
    }
    if (event.type === 'test:summary' && data.file === undefined) {
      ended = true;
      yield encode({ kind: 'end', outcome: fileFailed ? 'error' : data.counts?.cancelled ? 'cancelled'
        : data.success ? 'passed' : 'failed', wall_ms: milliseconds(data.duration_ms) });
    }
  }
  // A killed process cannot emit this footer; validation reports missing evidence.
  if (!ended) yield encode({ kind: 'end', outcome: 'cancelled', wall_ms: 0 });
}
