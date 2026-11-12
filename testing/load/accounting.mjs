export const REJECTIONS = new Set([429, 503, 502, 409]);
export const OPERATIONS = new Set(['http', 'auth_read', 'login', 'logout', 'catalog', 'compile', 'execution_create', 'execution_read', 'result_page', 'result_close', 'report_stream', 'metadata_read', 'metadata_write', 'control']);
export class Accounting {
  constructor({ maximumCallsPerMinute = Infinity, clock = () => performance.now() } = {}) {
    this.http = { sent: 0, admitted: 0, completed: 0, rejected: 0, failed: 0, incomplete: 0 };
    this.arrivals = { offered: 0, started: 0, dropped: 0, completed: 0, failed: 0 };
    this.latencies = { firstRowMs: [], fullDrainMs: [], httpMs: [], schedulingLagMs: [] };
    this.codes = {}; this.failureCodes = {}; this.active = 0; this.peakActive = 0;
    this.operations = {};
    this.maximumCallsPerMinute = maximumCallsPerMinute; this.callClock = clock; this.callWindow = []; this.callWindowStart = 0;
  }
  offered(started, lag = 0) {
    this.arrivals.offered++; this.arrivals[started ? 'started' : 'dropped']++;
    this.latencies.schedulingLagMs.push(lag);
  }
  sent(operation = 'http') {
    if (!OPERATIONS.has(operation)) throw new Error('invalid_operation_label');
    const now = this.callClock();
    while (this.callWindowStart < this.callWindow.length && this.callWindow[this.callWindowStart] <= now - 60000) this.callWindowStart++;
    if (this.callWindow.length - this.callWindowStart >= this.maximumCallsPerMinute) throw new Error('generator_http_budget_exhausted');
    if (this.callWindowStart > 12000) { this.callWindow = this.callWindow.slice(this.callWindowStart); this.callWindowStart = 0; }
    this.callWindow.push(now);
    this.operations[operation] ||= { sent: 0, completed: 0, failed: 0, rejected: 0, incomplete: 0 };
    this.operations[operation].sent++; this.http.sent++; this.active++; this.peakActive = Math.max(this.peakActive, this.active);
  }
  finish({ status = 0, complete = true, valid = true, failure = null, timing = {}, operation = 'http' }) {
    if (!this.active) throw new Error('http_without_send');
    this.active--;
    const outcome = !status || !complete ? 'incomplete' : REJECTIONS.has(status) ? 'rejected' : status >= 200 && status < 300 && valid ? 'completed' : 'failed';
    if (!this.operations[operation]) throw new Error('http_operation_without_send');
    this.operations[operation][outcome]++;
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
    if (Object.values(this.operations).some(value => value.sent !== value.completed + value.failed + value.rejected + value.incomplete)) throw new Error('unreconciled_operation_accounting');
    return { http: { ...this.http }, operations: structuredClone(this.operations), arrivals: { ...this.arrivals }, statusCodes: { ...this.codes },
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

export function verifyRecovery(before, after, { requireOwnedProbe = false, probe = null, ownedWork = null } = {}) {
  const fields = ['retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits'];
  if (!before || !after || fields.some(key => !Number.isSafeInteger(before[key]) || !Number.isSafeInteger(after[key])))
    return { verified: false, failures: ['missing_resource_observation'] };
  const failures = fields.filter(key => after[key] > before[key]).map(key => `${key}_leak`);
  if (requireOwnedProbe) {
    if (probe?.verified !== true || probe.authenticatedRead !== true || probe.exactSelectTerminal !== true || probe.capturedBackendReleased !== true)
      failures.push('owned_authenticated_select_recovery_unverified');
    if (ownedWork?.status !== 'available' || ownedWork.activeJobs !== 0 || ownedWork.activeExecutions !== 0 ||
        ownedWork.coverage && ownedWork.coverage.complete !== true)
      failures.push('owned_work_release_unverified');
  }
  return { verified: failures.length === 0, failures };
}
