export const K6_VERSION = '2.3.0';
export const RATES = [100, 300, 600, 1000, 3000, 6000, 12000];
export const WORKLOADS = ['cheap-read', 'catalog', 'compile', 'report-1', 'report-5', 'report-20', 'csv', 'slow-reader', 'disconnect', 'cancel'];
export const OBSERVATION_FIELDS = ['appRssBytes', 'appMemoryLimitBytes', 'appCpuPercent',
  'retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits',
  'metadataActive', 'metadataRejected', 'sourceConnections', 'ingressConnections', 'threads', 'fds', 'eventLoopLagMs', 'apiProcesses'];
export function plan({ recipe = 'smoke', workload = 'cheap-read', rate = 100, active = 2 } = {}) {
  if (!WORKLOADS.includes(workload)) throw new Error('invalid_workload');
  if (!Number.isSafeInteger(active) || active < 1 || active > 1000) throw new Error('active_identity_limit');
  if (!Number.isSafeInteger(rate) || rate < 1 || rate > 12000) throw new Error('rate_limit');
  const stage = (name, callsPerMinute, seconds, measured = true) => ({ name, callsPerMinute, seconds, measured });
  let stages;
  if (recipe === 'smoke') stages = [stage('smoke', rate, 6)];
  else if (recipe === 'ramp') stages = RATES.flatMap(r => [stage(`warmup-${r}`, r, 120, false), stage(`hold-${r}`, r, 300)]);
  else if (recipe === 'confirm') stages = Array.from({ length: 3 }, (_, i) => [stage(`warmup-${i + 1}`, rate, 120, false), stage(`repeat-${i + 1}`, rate, 300)]).flat();
  else if (recipe === 'spike') stages = [stage('baseline', rate, 120, false), stage('spike', Math.min(rate * 2, 12000), 30), stage('recovery', Math.max(1, Math.floor(rate / 10)), 60)];
  else if (recipe === 'soak') stages = [stage('warmup', rate, 120, false), stage('soak', rate, 3600)];
  else throw new Error('invalid_recipe');
  const oneCall = !['cancel'].includes(workload);
  return { version: 1, recipe, workload, activeIdentities: active, stages,
    rateUnit: oneCall ? 'actual_http_calls_per_minute' : 'journey_arrivals_per_minute',
    engine: ['cheap-read', 'catalog', 'compile'].includes(workload) ? 'k6' : 'incremental-node',
    maxConcurrent: 256, requestDeadlineMs: 30000, maxBodyBytes: 32 * 1024 * 1024,
    recoverySeconds: 10, stopRssRatio: 0.85, maxObserverAgeMs: 15000,
    budgets: ['cheap-read', 'catalog', 'compile'].includes(workload) ? { httpMs: 300 } :
      ['disconnect', 'cancel'].includes(workload) ? {} : { firstRowMs: 2000, fullDrainMs: workload === 'slow-reader' ? 30000 : 5000 },
    constraints: { maxCallsPerMinute: 12000, maxStageSeconds: 3600, paidAI: false,
      sourceWrites: false, expectedApiProcesses: 1 },
    capacityEligible: recipe !== 'smoke' && !['disconnect', 'cancel'].includes(workload) };
}

export function validatePlan(value) {
  if (value?.version !== 1 || !WORKLOADS.includes(value.workload) || !Array.isArray(value.stages) ||
      !value.stages.length || value.stages.length > 21) throw new Error('invalid_plan');
  const expected = plan({ recipe: value.recipe, workload: value.workload,
    rate: value.stages[0].callsPerMinute, active: value.activeIdentities });
  if (JSON.stringify(value) !== JSON.stringify(expected)) throw new Error('plan_changed_envelope');
  return value;
}
export function publicObservation(value, now = Date.now()) {
  if (!value || !Number.isFinite(Date.parse(value.at)) || now - Date.parse(value.at) > 15000 ||
      Date.parse(value.at) > now + 5000 || !Number.isFinite(value.appMemoryLimitBytes) || value.appMemoryLimitBytes <= 0 ||
      !Number.isFinite(value.appRssBytes) || value.appRssBytes < 0) throw new Error('observer_unavailable');
  const result = { at: value.at };
  for (const key of OBSERVATION_FIELDS) if (Number.isFinite(value[key]) && value[key] >= 0) result[key] = value[key];
  return result;
}
