// Whole-file scheduling preserves serialized shared-account setup/cleanup.
// Costs include first-attempt fixture setup, body and teardown, not retries.
import { isAbsolute, normalize } from 'node:path';

export const PROJECTS = ['desktop-chromium', 'android-chromium'];
export const OBSERVATION = Object.freeze({
  runId: 36726522134,
  runAttempt: 1,
  sourceSha: '0da5780f3b9eeeab5d1396992805bef8c6d36e4f',
  completeBrowserLanes: 4,
  wholeWorkflowOutcome: 'success',
  costBasis: 'first-attempt-setup+execution+teardown-ms',
  profiles: Object.freeze({
    'desktop-chromium': Object.freeze({
      measuredFiles: 63,
      collectedCases: 247,
      passed: 241,
      skipped: 6,
      phaseTotalMs: 588976,
      costsSha256: '3a89a945fb695776bb379252a6cbae377f5b5ff7e5ad0d6d19694ed2230de2e6',
    }),
    'android-chromium': Object.freeze({
      measuredFiles: 60,
      collectedCases: 239,
      passed: 233,
      skipped: 6,
      phaseTotalMs: 635602,
      costsSha256: '65da840dcc8a1ca7f31b17abde528c338ff48b2e487dfd514633f86db4ccb9bf',
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

export function balanceFiles(files, project, costs = OBSERVED_COSTS) {
  if (!PROJECTS.includes(project)) throw new Error('Unknown browser project');
  if (!Array.isArray(files) || !files.length || new Set(files).size !== files.length) {
    throw new Error('Discovered browser files must be nonempty and unique');
  }
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
  const shards = [{ files: [], estimatedMs: 0 }, { files: [], estimatedMs: 0 }];
  for (const item of weighted) {
    const [first, second] = shards;
    const selected = first.estimatedMs < second.estimatedMs
      || (first.estimatedMs === second.estimatedMs && first.files.length <= second.files.length)
      ? first : second;
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
  "tests/e2e/account-audit.spec.js": { 'desktop-chromium': 4929, 'android-chromium': 3737 },
  "tests/e2e/account-brand-navigation.spec.js": { 'desktop-chromium': 891, 'android-chromium': 1146 },
  "tests/e2e/accounts.spec.js": { 'desktop-chromium': 11552, 'android-chromium': 15006 },
  "tests/e2e/ai-assistant.spec.js": { 'desktop-chromium': 13807, 'android-chromium': 11187 },
  "tests/e2e/ai-copy-handoff.spec.js": { 'desktop-chromium': 1248, 'android-chromium': 1699 },
  "tests/e2e/ai-design-batch-live.spec.js": { 'desktop-chromium': 81, 'android-chromium': 91 },
  "tests/e2e/ai-diagnostic-permissions.spec.js": { 'desktop-chromium': 3189, 'android-chromium': 4921 },
  "tests/e2e/ai-failure-recovery.spec.js": { 'desktop-chromium': 6067, 'android-chromium': 5774 },
  "tests/e2e/ai-model-switch.spec.js": { 'desktop-chromium': 1069, 'android-chromium': 1391 },
  "tests/e2e/ai-provider-settings.spec.js": { 'desktop-chromium': 12732, 'android-chromium': 18198 },
  "tests/e2e/ai-read-live.spec.js": { 'desktop-chromium': 195, 'android-chromium': 267 },
  "tests/e2e/ai-structured-read-live.spec.js": { 'desktop-chromium': 94, 'android-chromium': 51 },
  "tests/e2e/bulk-jobs.spec.js": { 'desktop-chromium': 8310 },
  "tests/e2e/connection-lifecycle.spec.js": { 'desktop-chromium': 2204, 'android-chromium': 3435 },
  "tests/e2e/console-preferences.spec.js": { 'desktop-chromium': 3136, 'android-chromium': 2489 },
  "tests/e2e/console-shared-capacity.spec.js": { 'desktop-chromium': 1745 },
  "tests/e2e/console-toolbar.spec.js": { 'desktop-chromium': 2755, 'android-chromium': 2204 },
  "tests/e2e/design-editor-lifecycle.spec.js": { 'desktop-chromium': 9591, 'android-chromium': 9578 },
  "tests/e2e/inspector-data.spec.js": { 'desktop-chromium': 21769, 'android-chromium': 24002 },
  "tests/e2e/inspector-editor.spec.js": { 'desktop-chromium': 11431, 'android-chromium': 9555 },
  "tests/e2e/migration-disabled-apply.spec.js": { 'desktop-chromium': 1734, 'android-chromium': 1988 },
  "tests/e2e/migration-review-mobile.spec.js": { 'desktop-chromium': 1765, 'android-chromium': 1438 },
  "tests/e2e/permission-bundles.spec.js": { 'desktop-chromium': 2088, 'android-chromium': 2286 },
  "tests/e2e/product-navigation.spec.js": { 'desktop-chromium': 827, 'android-chromium': 2067 },
  "tests/e2e/query-diagnostics.spec.js": { 'desktop-chromium': 12419, 'android-chromium': 19440 },
  "tests/e2e/query-plan-table.spec.js": { 'desktop-chromium': 4508, 'android-chromium': 5401 },
  "tests/e2e/quick-start-schemii.spec.js": { 'desktop-chromium': 1832, 'android-chromium': 9523 },
  "tests/e2e/quick-start.spec.js": { 'desktop-chromium': 28216, 'android-chromium': 32427 },
  "tests/e2e/raw-console.spec.js": { 'desktop-chromium': 19317, 'android-chromium': 24050 },
  "tests/e2e/raw-copy-streaming.spec.js": { 'desktop-chromium': 4036 },
  "tests/e2e/schemer-ai-live.spec.js": { 'desktop-chromium': 73, 'android-chromium': 144 },
  "tests/e2e/schemer-dashboards.spec.js": { 'desktop-chromium': 28497, 'android-chromium': 31938 },
  "tests/e2e/schemoo-ai.spec.js": { 'desktop-chromium': 15797, 'android-chromium': 18331 },
  "tests/e2e/schemoo-aliases.spec.js": { 'desktop-chromium': 23867, 'android-chromium': 23981 },
  "tests/e2e/schemoo-canvas-overlap.spec.js": { 'desktop-chromium': 4744, 'android-chromium': 5916 },
  "tests/e2e/schemoo-column-comparisons.spec.js": { 'desktop-chromium': 23329, 'android-chromium': 16316 },
  "tests/e2e/schemoo-column-filters.spec.js": { 'desktop-chromium': 9503, 'android-chromium': 9673 },
  "tests/e2e/schemoo-connection-colors.spec.js": { 'desktop-chromium': 1498, 'android-chromium': 1665 },
  "tests/e2e/schemoo-derived.spec.js": { 'desktop-chromium': 28937, 'android-chromium': 29741 },
  "tests/e2e/schemoo-diagnostics.spec.js": { 'desktop-chromium': 5516, 'android-chromium': 7962 },
  "tests/e2e/schemoo-dropdowns.spec.js": { 'desktop-chromium': 5639, 'android-chromium': 4758 },
  "tests/e2e/schemoo-filter-authoring.spec.js": { 'desktop-chromium': 9983, 'android-chromium': 11115 },
  "tests/e2e/schemoo-filter-dialog.spec.js": { 'desktop-chromium': 35810, 'android-chromium': 36779 },
  "tests/e2e/schemoo-filter-navigation.spec.js": { 'desktop-chromium': 6298, 'android-chromium': 10228 },
  "tests/e2e/schemoo-help.spec.js": { 'desktop-chromium': 1851, 'android-chromium': 3122 },
  "tests/e2e/schemoo-membership.spec.js": { 'desktop-chromium': 15507, 'android-chromium': 16287 },
  "tests/e2e/schemoo-model-dependencies-live.spec.js": { 'desktop-chromium': 3021, 'android-chromium': 1655 },
  "tests/e2e/schemoo-model-editor-audit.spec.js": { 'desktop-chromium': 13573, 'android-chromium': 15130 },
  "tests/e2e/schemoo-model-library.spec.js": { 'desktop-chromium': 3997, 'android-chromium': 4794 },
  "tests/e2e/schemoo-pinch.spec.js": { 'desktop-chromium': 14, 'android-chromium': 1293 },
  "tests/e2e/schemoo-preview-fields.spec.js": { 'desktop-chromium': 9620, 'android-chromium': 15246 },
  "tests/e2e/schemoo-prototype.spec.js": { 'desktop-chromium': 19196, 'android-chromium': 15533 },
  "tests/e2e/schemoo-repetition-live.spec.js": { 'desktop-chromium': 18877, 'android-chromium': 19472 },
  "tests/e2e/schemoo-saved-previews.spec.js": { 'desktop-chromium': 10517, 'android-chromium': 13256 },
  "tests/e2e/schemoo-source-reconciliation.spec.js": { 'desktop-chromium': 7748, 'android-chromium': 10147 },
  "tests/e2e/semantic-api-owned.spec.js": { 'desktop-chromium': 6340, 'android-chromium': 6863 },
  "tests/e2e/shared-report-live.spec.js": { 'desktop-chromium': 17447, 'android-chromium': 21097 },
  "tests/e2e/shared-ui-accessibility.spec.js": { 'desktop-chromium': 1497, 'android-chromium': 1360 },
  "tests/e2e/shared-ui-audit.spec.js": { 'desktop-chromium': 17467, 'android-chromium': 12964 },
  "tests/e2e/sql-console.spec.js": { 'desktop-chromium': 33910, 'android-chromium': 40301 },
  "tests/e2e/toast-history-behavior.spec.js": { 'desktop-chromium': 2977, 'android-chromium': 4215 },
  "tests/e2e/workspace-lifecycle.spec.js": { 'desktop-chromium': 9781, 'android-chromium': 8105 },
  "tests/e2e/workspace-rename.spec.js": { 'desktop-chromium': 2608, 'android-chromium': 2864 },
});
