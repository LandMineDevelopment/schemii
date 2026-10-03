// Whole-file scheduling preserves serialized shared-account setup/cleanup.
// Costs include first-attempt fixture setup, body and teardown, not retries.
import { isAbsolute, normalize } from 'node:path';
import { readFileSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';

export const PROJECTS = ['desktop-chromium', 'android-chromium'];
export const OBSERVATION = Object.freeze({
  runId: 36738740979,
  runAttempt: 1,
  sourceSha: '8b44cf35c5f71bf0d46dce395674e63c7c04fcfb',
  completeBrowserLanes: 4,
  wholeWorkflowOutcome: 'success',
  costBasis: 'first-attempt-setup+execution+teardown-ms',
  profiles: Object.freeze({
    'desktop-chromium': Object.freeze({
      measuredFiles: 63,
      collectedCases: 247,
      passed: 241,
      skipped: 6,
      phaseTotalMs: 614339,
      costsSha256: '71d7ce7c76f15caff2905386fb4ef6ab0dd410e44b3a9f644216b7155d28f303',
    }),
    'android-chromium': Object.freeze({
      measuredFiles: 60,
      collectedCases: 239,
      passed: 233,
      skipped: 6,
      phaseTotalMs: 719180,
      costsSha256: 'b94dd2a0249af3bb1c180dfd1c8779e046405fbf8402bbb9358919ef7bebf279',
    }),
  }),
});

export function validateFile(file) {
  if (typeof file !== 'string' || isAbsolute(file) || file.includes('\\')
      || !file.startsWith('tests/e2e/') || normalize(file) !== file
      || !/\.(spec|test)\.[cm]?[jt]sx?$/.test(file)) {
    throw new Error('Expected an exact discovered test file below tests/e2e');
  }
  return file;
}

export function manifestPattern(value) {
  if (value === undefined) return undefined;
  const files = JSON.parse(value);
  if (!Array.isArray(files) || !files.length || new Set(files).size !== files.length) {
    throw new Error('Browser file manifest must be nonempty and unique');
  }
  const escaped = files.map(file => validateFile(file).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`(?:^|/)(?:${escaped.join('|')})$`);
}

export function validateShardCount(count) {
  if (![1, 2, 3, 6].includes(count)) throw new Error('Browser shard count must be 1, 2, 3 or 6');
  return count;
}

export function balanceFiles(files, project, costs = OBSERVED_COSTS, shardCount = 2) {
  validateShardCount(shardCount);
  if (shardCount === 1) throw new Error('One browser shard is reserved for the reviewed inspection profile, not balancing; invalid shard count');
  if (!PROJECTS.includes(project)) throw new Error('Unknown browser project');
  if (!Array.isArray(files) || !files.length || new Set(files).size !== files.length) {
    throw new Error('Discovered browser files must be nonempty and unique');
  }
  if (files.length < shardCount) throw new Error('Every browser shard must own at least one file');
  const known = Object.values(costs).map(cost => cost[project]).filter(cost => cost > 0)
    .sort((a, b) => a - b);
  const fallback = known.length ? known[Math.floor(known.length / 2)] : 1000;
  const unknown = [];
  const weighted = files.map(file => {
    validateFile(file);
    const observed = costs[file]?.[project];
    if (observed === undefined) unknown.push(file);
    const cost = observed ?? fallback;
    if (!Number.isFinite(cost) || cost < 0) throw new Error('Invalid observed file cost');
    return { file, cost };
  }).sort((a, b) => b.cost - a.cost || (a.file < b.file ? -1 : 1));
  const shards = Array.from({ length: shardCount }, () => ({ files: [], estimatedMs: 0 }));
  for (const item of weighted) {
    let selected = shards[0];
    for (const candidate of shards.slice(1)) {
      if (candidate.estimatedMs < selected.estimatedMs
          || (candidate.estimatedMs === selected.estimatedMs && candidate.files.length < selected.files.length)) {
        selected = candidate;
      }
    }
    selected.files.push(item.file);
    selected.estimatedMs += item.cost;
  }
  for (const shard of shards) shard.files.sort();
  return { project, shards, unknownFiles: unknown.sort(), fallbackMs: fallback };
}

// Seed weights come from one complete natural run; skipped attempts retain
// their measured setup/teardown cost. Only actual discovered profile scopes
// receive weights. These observations do not predict future wall-clock gains.
// New files retain the profile's median positive estimate and remain scheduled.
export const OBSERVED_COSTS = Object.freeze({
  "tests/e2e/account-audit.spec.js": { 'desktop-chromium': 4398, 'android-chromium': 5130 },
  "tests/e2e/account-brand-navigation.spec.js": { 'desktop-chromium': 1495, 'android-chromium': 1429 },
  "tests/e2e/accounts.spec.js": { 'desktop-chromium': 11091, 'android-chromium': 17947 },
  "tests/e2e/ai-assistant.spec.js": { 'desktop-chromium': 10329, 'android-chromium': 15474 },
  "tests/e2e/ai-copy-handoff.spec.js": { 'desktop-chromium': 914, 'android-chromium': 1283 },
  "tests/e2e/ai-design-batch-live.spec.js": { 'desktop-chromium': 98, 'android-chromium': 151 },
  "tests/e2e/ai-diagnostic-permissions.spec.js": { 'desktop-chromium': 4630, 'android-chromium': 4782 },
  "tests/e2e/ai-failure-recovery.spec.js": { 'desktop-chromium': 5600, 'android-chromium': 6538 },
  "tests/e2e/ai-model-switch.spec.js": { 'desktop-chromium': 1684, 'android-chromium': 1817 },
  "tests/e2e/ai-provider-settings.spec.js": { 'desktop-chromium': 16485, 'android-chromium': 18862 },
  "tests/e2e/ai-read-live.spec.js": { 'desktop-chromium': 214, 'android-chromium': 281 },
  "tests/e2e/ai-structured-read-live.spec.js": { 'desktop-chromium': 95, 'android-chromium': 131 },
  "tests/e2e/bulk-jobs.spec.js": { 'desktop-chromium': 9604 },
  "tests/e2e/connection-lifecycle.spec.js": { 'desktop-chromium': 2687, 'android-chromium': 3275 },
  "tests/e2e/console-preferences.spec.js": { 'desktop-chromium': 3057, 'android-chromium': 3104 },
  "tests/e2e/console-shared-capacity.spec.js": { 'desktop-chromium': 1819 },
  "tests/e2e/console-toolbar.spec.js": { 'desktop-chromium': 1879, 'android-chromium': 3374 },
  "tests/e2e/design-editor-lifecycle.spec.js": { 'desktop-chromium': 7709, 'android-chromium': 9288 },
  "tests/e2e/inspector-data.spec.js": { 'desktop-chromium': 15122, 'android-chromium': 23740 },
  "tests/e2e/inspector-editor.spec.js": { 'desktop-chromium': 8938, 'android-chromium': 11914 },
  "tests/e2e/migration-disabled-apply.spec.js": { 'desktop-chromium': 1632, 'android-chromium': 1966 },
  "tests/e2e/migration-review-mobile.spec.js": { 'desktop-chromium': 1674, 'android-chromium': 2490 },
  "tests/e2e/permission-bundles.spec.js": { 'desktop-chromium': 1405, 'android-chromium': 2158 },
  "tests/e2e/product-navigation.spec.js": { 'desktop-chromium': 950, 'android-chromium': 2051 },
  "tests/e2e/query-diagnostics.spec.js": { 'desktop-chromium': 16283, 'android-chromium': 18541 },
  "tests/e2e/query-plan-table.spec.js": { 'desktop-chromium': 4330, 'android-chromium': 5344 },
  "tests/e2e/quick-start-schemii.spec.js": { 'desktop-chromium': 1311, 'android-chromium': 10108 },
  "tests/e2e/quick-start.spec.js": { 'desktop-chromium': 21092, 'android-chromium': 28883 },
  "tests/e2e/raw-console.spec.js": { 'desktop-chromium': 29752, 'android-chromium': 24637 },
  "tests/e2e/raw-copy-streaming.spec.js": { 'desktop-chromium': 5366 },
  "tests/e2e/schemer-ai-live.spec.js": { 'desktop-chromium': 88, 'android-chromium': 138 },
  "tests/e2e/schemer-dashboards.spec.js": { 'desktop-chromium': 40919, 'android-chromium': 42360 },
  "tests/e2e/schemoo-ai.spec.js": { 'desktop-chromium': 19176, 'android-chromium': 20982 },
  "tests/e2e/schemoo-aliases.spec.js": { 'desktop-chromium': 24374, 'android-chromium': 23581 },
  "tests/e2e/schemoo-canvas-overlap.spec.js": { 'desktop-chromium': 5846, 'android-chromium': 8694 },
  "tests/e2e/schemoo-column-comparisons.spec.js": { 'desktop-chromium': 23544, 'android-chromium': 23461 },
  "tests/e2e/schemoo-column-filters.spec.js": { 'desktop-chromium': 9899, 'android-chromium': 10077 },
  "tests/e2e/schemoo-connection-colors.spec.js": { 'desktop-chromium': 1724, 'android-chromium': 2219 },
  "tests/e2e/schemoo-derived.spec.js": { 'desktop-chromium': 22132, 'android-chromium': 30131 },
  "tests/e2e/schemoo-diagnostics.spec.js": { 'desktop-chromium': 5741, 'android-chromium': 7932 },
  "tests/e2e/schemoo-dropdowns.spec.js": { 'desktop-chromium': 6415, 'android-chromium': 6042 },
  "tests/e2e/schemoo-filter-authoring.spec.js": { 'desktop-chromium': 11766, 'android-chromium': 11821 },
  "tests/e2e/schemoo-filter-dialog.spec.js": { 'desktop-chromium': 36132, 'android-chromium': 38271 },
  "tests/e2e/schemoo-filter-navigation.spec.js": { 'desktop-chromium': 9500, 'android-chromium': 9805 },
  "tests/e2e/schemoo-help.spec.js": { 'desktop-chromium': 3016, 'android-chromium': 3402 },
  "tests/e2e/schemoo-membership.spec.js": { 'desktop-chromium': 22413, 'android-chromium': 22721 },
  "tests/e2e/schemoo-model-dependencies-live.spec.js": { 'desktop-chromium': 3275, 'android-chromium': 2380 },
  "tests/e2e/schemoo-model-editor-audit.spec.js": { 'desktop-chromium': 10057, 'android-chromium': 14708 },
  "tests/e2e/schemoo-model-library.spec.js": { 'desktop-chromium': 4641, 'android-chromium': 6193 },
  "tests/e2e/schemoo-pinch.spec.js": { 'desktop-chromium': 7, 'android-chromium': 1908 },
  "tests/e2e/schemoo-preview-fields.spec.js": { 'desktop-chromium': 14247, 'android-chromium': 15550 },
  "tests/e2e/schemoo-prototype.spec.js": { 'desktop-chromium': 14909, 'android-chromium': 20041 },
  "tests/e2e/schemoo-repetition-live.spec.js": { 'desktop-chromium': 19642, 'android-chromium': 19620 },
  "tests/e2e/schemoo-saved-previews.spec.js": { 'desktop-chromium': 17278, 'android-chromium': 17379 },
  "tests/e2e/schemoo-source-reconciliation.spec.js": { 'desktop-chromium': 8116, 'android-chromium': 10426 },
  "tests/e2e/semantic-api-owned.spec.js": { 'desktop-chromium': 6006, 'android-chromium': 6196 },
  "tests/e2e/shared-report-live.spec.js": { 'desktop-chromium': 18343, 'android-chromium': 26322 },
  "tests/e2e/shared-ui-accessibility.spec.js": { 'desktop-chromium': 2356, 'android-chromium': 1939 },
  "tests/e2e/shared-ui-audit.spec.js": { 'desktop-chromium': 10173, 'android-chromium': 17372 },
  "tests/e2e/sql-console.spec.js": { 'desktop-chromium': 36436, 'android-chromium': 54244 },
  "tests/e2e/toast-history-behavior.spec.js": { 'desktop-chromium': 4814, 'android-chromium': 5292 },
  "tests/e2e/workspace-lifecycle.spec.js": { 'desktop-chromium': 7170, 'android-chromium': 10338 },
  "tests/e2e/workspace-rename.spec.js": { 'desktop-chromium': 2541, 'android-chromium': 2937 },
});

// Loaded only for the two reviewed product owners; ordinary discovery stays unchanged.
export function coverageProfile(profile, policy = undefined) {
  if (['full', 'e2e-tests'].includes(profile)) return undefined;
  if (profile === 'developer-inspection') return inspectionCoverage(policy);
  if (profile !== 'schemer-result-cache') throw new Error('Unknown browser coverage profile');
  policy ??= JSON.parse(readFileSync(new URL('./coverage-profiles.json', import.meta.url), 'utf8'));
  if (!policy || Object.keys(policy).sort().join(',') !== 'browser,files,profile,python,schema'
      || policy.schema !== 1 || policy.profile !== profile
      || !Array.isArray(policy.files) || policy.files.length !== 8 || new Set(policy.files).size !== 8
      || Object.keys(policy.browser).sort().join(',') !== [...PROJECTS].sort().join(',')) {
    throw new Error('Invalid browser coverage policy');
  }
  policy.files.forEach(validateFile);
  for (const project of PROJECTS) {
    const inventory = policy.browser[project];
    if (!inventory || Object.keys(inventory).sort().join(',') !== 'allowed_skips,files,shards'
        || Object.keys(inventory.files).sort().join(',') !== [...policy.files].sort().join(',')) {
      throw new Error('Missing expected browser files');
    }
    const cases = Object.values(inventory.files).flat();
    if (Object.values(inventory.files).some(ids => !Array.isArray(ids) || !ids.length)
        || cases.some(id => typeof id !== 'string' || !/^[0-9a-f]{64}$/.test(id))
        || new Set(cases).size !== cases.length
        || !Array.isArray(inventory.allowed_skips) || inventory.allowed_skips.length !== 1
        || inventory.files['tests/e2e/schemer-ai-live.spec.js']?.length !== 1
        || inventory.allowed_skips[0] !== inventory.files['tests/e2e/schemer-ai-live.spec.js'][0]) {
      throw new Error('Invalid browser coverage cases');
    }
    const assigned = inventory.shards?.flat();
    if (!Array.isArray(inventory.shards) || inventory.shards.length !== 3
        || inventory.shards.some(files => !Array.isArray(files) || !files.length)
        || assigned.length !== policy.files.length || new Set(assigned).size !== policy.files.length
        || assigned.some(file => !policy.files.includes(file))) {
      throw new Error('Missing or duplicate browser shard files');
    }
  }
  return policy;
}


function inspectionCoverage(policy) {
  const files = ['tests/e2e/shared-ui-audit.spec.js', 'tests/e2e/ai-diagnostic-permissions.spec.js'];
  policy ??= JSON.parse(readFileSync(new URL('./inspection-coverage.json', import.meta.url), 'utf8'));
  if (!policy || Object.keys(policy).sort().join(',') !== 'browser,files,frozen_sha256,profile,python,schema'
      || policy.schema !== 1 || policy.profile !== 'developer-inspection'
      || JSON.stringify(policy.files) !== JSON.stringify(files)
      || Object.keys(policy.browser).sort().join(',') !== [...PROJECTS].sort().join(',')) {
    throw new Error('Invalid inspection browser policy');
  }
  for (const project of PROJECTS) {
    const inventory = policy.browser[project];
    if (!inventory || Object.keys(inventory).sort().join(',') !== 'allowed_skips,files,shards'
        || Object.keys(inventory.files).sort().join(',') !== [...files].sort().join(',')
        || JSON.stringify(inventory.allowed_skips) !== '[]'
        || JSON.stringify(inventory.shards) !== JSON.stringify([files])) {
      throw new Error('Invalid inspection browser inventory');
    }
    const cases = Object.values(inventory.files).flat();
    if (files.some((file, index) => !Array.isArray(inventory.files[file])
        || inventory.files[file].length !== [5, 2][index])
        || cases.some(id => typeof id !== 'string' || !/^[0-9a-f]{64}$/.test(id))
        || new Set(cases).size !== 7) throw new Error('Invalid inspection browser cases');
  }
  const frozen = [...files, 'tests/test_database_inspection.py', 'tests/test_developer_inspection.py',
    'tests/test_route_inspection.py', 'tests/test_system_inspection.py', 'tests/test_frontend.py',
    'tests/test_application_structure.py', 'tests/test_runtime_hardening.py'];
  if (!policy.frozen_sha256 || Object.keys(policy.frozen_sha256).sort().join(',') !== frozen.sort().join(',')) {
    throw new Error('Invalid frozen inspection files');
  }
  for (const file of frozen) {
    const path = new URL('../../' + file, import.meta.url);
    if (!lstatSync(path).isFile() || createHash('sha256').update(readFileSync(path)).digest('hex') !== policy.frozen_sha256[file]) {
      throw new Error('Changed frozen inspection source');
    }
  }
  return policy;
}
