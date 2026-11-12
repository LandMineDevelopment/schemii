export const K6_VERSION = '2.3.0';
export const RATES = [100, 300, 600, 1000, 3000, 6000, 12000];
export const WORKLOADS = ['cheap-read', 'catalog', 'compile', 'report-1', 'report-5', 'report-20', 'csv', 'slow-reader', 'disconnect', 'cancel',
  'sql-page', 'wide-rows', 'idle-expiry', 'shared-report', 'login-churn', 'metadata-write', 'mix'];
export const PROFILE_MODES = ['private-single', 'private-multiple', 'managed-shared', 'managed-distinct'];
export function fixtureTopology(spec) {
  const lanes = spec.workload === 'mix' ? spec.mix.map(value => value.workload) : [spec.workload];
  // Disposable CSV exports a saved tile; share its one-tile dashboard with
  // report-1 and count the demand before any fixture mutation.
  const dashboardCounts = [...new Set(lanes.filter(value => value === 'csv' || /^report-(1|5|20)$/.test(value))
    .map(value => value === 'csv' ? 1 : Number(value.slice(7))))].sort((a, b) => a - b);
  const models = lanes.some(value => ['compile', 'csv', 'slow-reader', 'disconnect', 'cancel'].includes(value) || value.startsWith('report-'));
  const perOwner = spec.profileMode === 'private-multiple' ? spec.profilesPerOwner : 1;
  if (dashboardCounts.length * perOwner > 100) throw new Error('fixture_dashboard_limit_reduce_profiles_or_report_variants');
  return { dashboardCounts, modelsRequired: models, profilesPerOwner: perOwner, workspacesPerOwner: perOwner,
    modelsPerOwner: models ? perOwner : 0, dashboardsPerOwner: dashboardCounts.length * perOwner,
    managedProfiles: spec.profileMode === 'managed-distinct' ? spec.activeIdentities : spec.profileMode === 'managed-shared' ? 1 : 0,
    sharedAuthorModels: lanes.includes('shared-report') ? 1 : 0, sharedAuthorDashboards: lanes.includes('shared-report') ? 1 : 0 };
}
export const DEFAULT_MIX = [{ workload: 'cheap-read', weight: 40 }, { workload: 'catalog', weight: 10 },
  { workload: 'compile', weight: 10 }, { workload: 'sql-page', weight: 20 }, { workload: 'report-5', weight: 10 },
  { workload: 'shared-report', weight: 5 }, { workload: 'metadata-write', weight: 5 }];
export function validateMix(mix) {
  const allowed = new Set(['cheap-read', 'catalog', 'compile', 'sql-page', 'wide-rows', 'report-1', 'report-5', 'report-20', 'csv', 'shared-report', 'metadata-write']);
  if (!Array.isArray(mix) || !mix.length || mix.length > allowed.size || new Set(mix.map(item => item.workload)).size !== mix.length ||
      mix.some(item => !allowed.has(item.workload) || !Number.isSafeInteger(item.weight) || item.weight < 1 || item.weight > 100 || Object.keys(item).sort().join() !== 'weight,workload') ||
      mix.reduce((sum, item) => sum + item.weight, 0) !== 100) throw new Error('invalid_weighted_mix');
  return mix;
}
export function mixedWorkload(mix, index) {
  validateMix(mix);
  const cycle = mix.flatMap((item, order) => Array.from({ length: item.weight }, (_, step) => ({
    workload: item.workload, position: (step + 0.5) / item.weight, order }))).sort((a, b) => a.position - b.position || a.order - b.order);
  return cycle[index % 100].workload;
}
export const OBSERVATION_FIELDS = ['appRssBytes', 'appMemoryLimitBytes', 'appCpuPercent',
  'retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits',
  'metadataActive', 'metadataRejected', 'sourceConnections', 'ingressConnections', 'threads', 'fds', 'eventLoopLagMs', 'apiProcesses'];
export function plan({ recipe = 'smoke', workload = 'cheap-read', rate = 100, active = 2, registered = active,
  profileMode = ['mix', 'shared-report'].includes(workload) ? 'managed-shared' : 'private-single', profiles = profileMode === 'private-multiple' ? 2 : 1,
  mix = DEFAULT_MIX, fixtureMode = 'retained', soakSeconds = 3600 } = {}) {
  if (!WORKLOADS.includes(workload)) throw new Error('invalid_workload');
  if (!Number.isSafeInteger(active) || active < 1 || active > 1000) throw new Error('active_identity_limit');
  if (!Number.isSafeInteger(rate) || rate < 1 || rate > 12000) throw new Error('rate_limit');
  if (!Number.isSafeInteger(registered) || registered < active || registered > 1000 || !PROFILE_MODES.includes(profileMode) ||
      !Number.isSafeInteger(profiles) || profiles < 1 || profiles > 100 || (profileMode !== 'private-multiple' && profiles !== 1) ||
      (profileMode === 'managed-distinct' && active > 100)) throw new Error('cardinality_or_profile_limit');
  validateMix(mix);
  if (!Number.isSafeInteger(soakSeconds) || soakSeconds < 1800 || soakSeconds > 3600 || recipe !== 'soak' && soakSeconds !== 3600) throw new Error('soak_seconds_requires_soak_1800_to_3600');
  const topology = fixtureMode === 'disposable' ? fixtureTopology({ workload, mix, profileMode, profilesPerOwner: profiles, activeIdentities: active }) : null;
  if (!['retained', 'disposable'].includes(fixtureMode) || (fixtureMode === 'retained' && (registered !== active ||
      ['sql-page', 'wide-rows', 'idle-expiry', 'shared-report', 'login-churn', 'metadata-write', 'mix'].includes(workload)))) throw new Error('disposable_fixture_required');
  if ((workload === 'shared-report' || workload === 'mix' && mix.some(item => item.workload === 'shared-report')) && !profileMode.startsWith('managed-')) throw new Error('shared_reports_require_managed_profile');
  if ((workload === 'idle-expiry') !== (recipe === 'lifecycle')) throw new Error('idle_requires_real_lifecycle_recipe');
  const stage = (name, callsPerMinute, seconds, measured = true) => ({ name, callsPerMinute, seconds, measured });
  let stages;
  if (recipe === 'smoke') stages = [stage('smoke', rate, 6)];
  else if (recipe === 'ramp') stages = RATES.flatMap(r => [stage(`warmup-${r}`, r, 120, false), stage(`hold-${r}`, r, 300)]);
  else if (recipe === 'confirm') stages = Array.from({ length: 3 }, (_, i) => [stage(`warmup-${i + 1}`, rate, 120, false), stage(`repeat-${i + 1}`, rate, 300)]).flat();
  else if (recipe === 'spike') stages = [stage('baseline', rate, 120, false), stage('spike', Math.min(rate * 2, 12000), 30), stage('recovery', Math.max(1, Math.floor(rate / 10)), 60)];
  else if (recipe === 'soak') stages = [stage('warmup', rate, 120, false), stage('soak', rate, soakSeconds)];
  else if (recipe === 'lifecycle') {
    if (active > 100) throw new Error('lifecycle_active_limit');
    stages = [stage('real-idle-expiry-1800-seconds', active * 60, 1)];
  }
  else throw new Error('invalid_recipe');
  const oneCall = !['cancel', 'sql-page', 'wide-rows', 'idle-expiry', 'login-churn', 'metadata-write', 'mix'].includes(workload);
  return { version: 1, recipe, workload, fixtureMode, activeIdentities: active, registeredIdentities: registered, profileMode, profilesPerOwner: profiles,
    mix: workload === 'mix' ? mix : [], idleSeconds: workload === 'idle-expiry' ? 1800 : null, soakSeconds: recipe === 'soak' ? soakSeconds : null, fixtureTopology: topology, stages,
    rateUnit: oneCall ? 'actual_http_calls_per_minute' : 'journey_arrivals_per_minute',
    engine: fixtureMode !== 'disposable' && ['cheap-read', 'catalog', 'compile'].includes(workload) ? 'k6' : 'incremental-node',
    maxConcurrent: 256, requestDeadlineMs: 30000, maxBodyBytes: 32 * 1024 * 1024,
    recoverySeconds: 10, stopRssRatio: 0.85, maxObserverAgeMs: 15000,
    budgets: ['cheap-read', 'catalog', 'compile'].includes(workload) ? { httpMs: 300 } :
      ['sql-page', 'wide-rows', 'idle-expiry', 'shared-report', 'login-churn', 'metadata-write', 'mix'].includes(workload) ? {} :
      ['disconnect', 'cancel'].includes(workload) ? {} : { firstRowMs: 2000, fullDrainMs: workload === 'slow-reader' ? 30000 : 5000 },
    constraints: { maxCallsPerMinute: 12000, maxStageSeconds: 3600, paidAI: false,
      sourceWrites: false, expectedApiProcesses: 1 },
    capacityEligible: false };
}

export function validatePlan(value) {
  if (value?.version !== 1 || !WORKLOADS.includes(value.workload) || !Array.isArray(value.stages) ||
      !value.stages.length || value.stages.length > 21) throw new Error('invalid_plan');
  const expected = plan({ recipe: value.recipe, workload: value.workload,
    rate: value.stages[0].callsPerMinute, active: value.activeIdentities, registered: value.registeredIdentities,
    profileMode: value.profileMode, profiles: value.profilesPerOwner, fixtureMode: value.fixtureMode, mix: value.workload === 'mix' ? value.mix : DEFAULT_MIX,
    soakSeconds: value.recipe === 'soak' ? value.soakSeconds : 3600 });
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
