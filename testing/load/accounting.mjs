export const REJECTIONS = new Set([429, 503, 502, 409]);
export class Accounting {
  constructor() {
    this.http = { sent: 0, admitted: 0, completed: 0, rejected: 0, failed: 0, incomplete: 0 };
    this.arrivals = { offered: 0, started: 0, dropped: 0, completed: 0, failed: 0 };
    this.latencies = { firstRowMs: [], fullDrainMs: [], httpMs: [], schedulingLagMs: [] };
    this.codes = {}; this.failureCodes = {}; this.active = 0; this.peakActive = 0;
  }
  offered(started, lag = 0) {
    this.arrivals.offered++; this.arrivals[started ? 'started' : 'dropped']++;
    this.latencies.schedulingLagMs.push(lag);
  }
  sent() { this.http.sent++; this.active++; this.peakActive = Math.max(this.peakActive, this.active); }
  finish({ status = 0, complete = true, valid = true, failure = null, timing = {} }) {
    if (!this.active) throw new Error('http_without_send');
    this.active--;
    this.codes[status] = (this.codes[status] || 0) + 1;
    if (!status || !complete) this.http.incomplete++;
    else if (REJECTIONS.has(status)) this.http.rejected++;
    else if (status < 200 || status >= 300) this.http.failed++;
    else {
      this.http.admitted++;
      this.http[valid ? 'completed' : 'failed']++;
    }
    if (status && (status < 200 || status >= 300) && !REJECTIONS.has(status)) this.http.admitted++;
    if (status >= 200 && status < 300 && !complete) this.http.admitted++;
    if (failure) this.failureCodes[failure] = (this.failureCodes[failure] || 0) + 1;
    for (const key of Object.keys(this.latencies)) if (Number.isFinite(timing[key])) this.latencies[key].push(timing[key]);
  }
  journey(valid) { this.arrivals[valid ? 'completed' : 'failed']++; }
  snapshot() {
    if (this.active || this.http.sent !== this.http.completed + this.http.rejected + this.http.failed + this.http.incomplete ||
        this.arrivals.offered !== this.arrivals.started + this.arrivals.dropped ||
        this.arrivals.started !== this.arrivals.completed + this.arrivals.failed) throw new Error('unreconciled_accounting');
    return { http: { ...this.http }, arrivals: { ...this.arrivals }, statusCodes: { ...this.codes },
      failureCodes: { ...this.failureCodes }, peakActive: this.peakActive,
      latency: Object.fromEntries(Object.entries(this.latencies).map(([key, values]) => [key, distribution(values)])) };
  }
}
export function distribution(values) {
  const ordered = [...values].sort((a, b) => a - b);
  const percentile = p => ordered.length ? ordered[Math.max(0, Math.ceil(ordered.length * p) - 1)] : null;
  return { samples: ordered.length, p50: percentile(0.5), p95: percentile(0.95), p99: percentile(0.99), max: ordered.at(-1) ?? null };
}
export function assertHealthy(summary, budgets, { allowRejections = false } = {}) {
  const failures = [];
  if (summary.arrivals.dropped) failures.push('generator_underdelivery');
  if (summary.http.incomplete || summary.http.failed || summary.arrivals.failed) failures.push('incorrect_or_incomplete_work');
  if (!allowRejections && summary.http.rejected) failures.push('capacity_rejections');
  for (const [key, budget] of Object.entries(budgets)) {
    const actual = summary.latency[key];
    if (!actual?.samples || actual.p95 > budget) failures.push(`${key}_budget`);
  }
  return failures;
}

export function verifyRecovery(before, after) {
  const fields = ['retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits'];
  if (!before || !after || fields.some(key => !Number.isSafeInteger(before[key]) || !Number.isSafeInteger(after[key])))
    return { verified: false, failures: ['missing_resource_observation'] };
  const failures = fields.filter(key => after[key] > before[key]).map(key => `${key}_leak`);
  return { verified: failures.length === 0, failures };
}
