import { relative } from 'node:path';
import { performance } from 'node:perf_hooks';
import { attempt, hash, metadata, milliseconds, originalSourceRoot, writer } from './timing.mjs';

export default class TimingReporter {
  onError() {
    // Global setup/teardown and runner errors are outside individual attempts.
    // Retain only their presence, never the Error object or its contents.
    this.infrastructureFailed = true;
  }

  onBegin(config, suite) {
    this.sourceRoot = originalSourceRoot() || process.cwd();
    this.started = performance.now();
    this.tests = suite.allTests();
    this.completed = new Set();
    const projects = new Set(this.tests.map(test => test.parent.project().name));
    const project = projects.size === 1 ? [...projects][0] : process.env.CI_TELEMETRY_PROJECT;
    this.write = writer(process.env.CI_TELEMETRY_FILE || 'artifacts/ci-timing/browser.jsonl',
      metadata('browser', project, config.shard?.current || Number(process.env.CI_TELEMETRY_SHARD || 0)),
      { exclusive: process.env.SCHEMII_E2E_SOURCE_ROOT !== undefined });
    this.write({ kind: 'start', planned: this.tests.length });
    for (const test of this.tests) this.write({ kind: 'plan', test_id: hash(test.id) });
  }

  onTestEnd(test, result) {
    this.completed.add(test.id);
    const outcome = { passed: 'passed', failed: 'failed', timedOut: 'timed-out',
      skipped: 'skipped', interrupted: 'cancelled' }[result.status];
    // Hook steps separate fixture/setup/teardown from the test body. Nested
    // hook steps overlap their parents, so count only top-level roots.
    let setup = 0;
    let teardown = 0;
    for (const step of result.steps || []) {
      if (step.category !== 'hook') continue;
      if (step.title === 'Before Hooks') setup += step.duration;
      if (step.title === 'After Hooks') teardown += step.duration;
    }
    this.write(attempt(test.id, relative(this.sourceRoot, test.location.file),
      test.location.line, result.retry, outcome, setup,
      Math.max(0, result.duration - setup - teardown), teardown));
  }

  onEnd(result) {
    if (!this.write) return; // Global setup/collection may fail before onBegin.
    for (const test of this.tests) {
      if (!this.completed.has(test.id)) this.write(attempt(test.id,
        relative(this.sourceRoot, test.location.file), test.location.line, 0,
        result.status === 'interrupted' ? 'cancelled' : 'not-run', 0, 0, 0));
    }
    this.write({ kind: 'end', outcome: this.infrastructureFailed ? 'error' : { passed: 'passed', failed: 'failed',
      timedout: 'timed-out', interrupted: 'cancelled' }[result.status],
    wall_ms: milliseconds(performance.now() - this.started) });
  }

  printsToStdio() { return false; }
}
