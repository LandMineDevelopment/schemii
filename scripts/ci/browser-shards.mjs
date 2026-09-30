// Whole-file scheduling preserves serialized shared-account setup/cleanup.
// Costs include first-attempt fixture setup, body and teardown, not retries.
import { isAbsolute, normalize } from 'node:path';

export const PROJECTS = ['desktop-chromium', 'android-chromium'];
export const OBSERVATION = Object.freeze({
  runId: 36651493606,
  sourceSha: 'f2cf555e8a62263d2caf97dcae9f27407f8c7508',
  completeBrowserLanes: 4,
  wholeWorkflowOutcome: 'failure',
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

// Seed weights are reviewed observations, not a claim about the modified suite.
// Android raw-console/model-dependency costs omit the tagged request-only
// attempts (6,708ms/2,615ms); whole-file API scopes are absent from discovery.
// Updating them requires complete comparable natural-run receipts; new files
// receive the profile's median positive cost and can never drop out of discovery.
export const OBSERVED_COSTS = Object.freeze({
  "tests/e2e/account-audit.spec.js": { 'desktop-chromium': 3831, 'android-chromium': 4588 },
  "tests/e2e/account-brand-navigation.spec.js": { 'desktop-chromium': 1223, 'android-chromium': 1390 },
  "tests/e2e/accounts.spec.js": { 'desktop-chromium': 9139, 'android-chromium': 12200 },
  "tests/e2e/ai-assistant.spec.js": { 'desktop-chromium': 12029, 'android-chromium': 13755 },
  "tests/e2e/ai-copy-handoff.spec.js": { 'desktop-chromium': 1009, 'android-chromium': 1253 },
  "tests/e2e/ai-design-batch-live.spec.js": { 'desktop-chromium': 81, 'android-chromium': 120 },
  "tests/e2e/ai-diagnostic-permissions.spec.js": { 'desktop-chromium': 3672, 'android-chromium': 4178 },
  "tests/e2e/ai-failure-recovery.spec.js": { 'desktop-chromium': 5634, 'android-chromium': 6191 },
  "tests/e2e/ai-model-switch.spec.js": { 'desktop-chromium': 1425, 'android-chromium': 1747 },
  "tests/e2e/ai-provider-settings.spec.js": { 'desktop-chromium': 15111, 'android-chromium': 17147 },
  "tests/e2e/ai-read-live.spec.js": { 'desktop-chromium': 164, 'android-chromium': 235 },
  "tests/e2e/ai-structured-read-live.spec.js": { 'desktop-chromium': 78, 'android-chromium': 128 },
  "tests/e2e/bulk-jobs.spec.js": { 'desktop-chromium': 9071, 'android-chromium': 9910 },
  "tests/e2e/connection-lifecycle.spec.js": { 'desktop-chromium': 2832, 'android-chromium': 3042 },
  "tests/e2e/console-preferences.spec.js": { 'desktop-chromium': 2472, 'android-chromium': 2993 },
  "tests/e2e/console-shared-capacity.spec.js": { 'desktop-chromium': 2241, 'android-chromium': 2590 },
  "tests/e2e/console-toolbar.spec.js": { 'desktop-chromium': 2296, 'android-chromium': 3067 },
  "tests/e2e/design-editor-lifecycle.spec.js": { 'desktop-chromium': 8394, 'android-chromium': 8774 },
  "tests/e2e/inspector-data.spec.js": { 'desktop-chromium': 18664, 'android-chromium': 21715 },
  "tests/e2e/inspector-editor.spec.js": { 'desktop-chromium': 9421, 'android-chromium': 11206 },
  "tests/e2e/migration-disabled-apply.spec.js": { 'desktop-chromium': 1300, 'android-chromium': 1669 },
  "tests/e2e/migration-review-mobile.spec.js": { 'desktop-chromium': 1441, 'android-chromium': 2319 },
  "tests/e2e/permission-bundles.spec.js": { 'desktop-chromium': 1858, 'android-chromium': 1998 },
  "tests/e2e/product-navigation.spec.js": { 'desktop-chromium': 1173, 'android-chromium': 1939 },
  "tests/e2e/query-diagnostics.spec.js": { 'desktop-chromium': 14643, 'android-chromium': 17718 },
  "tests/e2e/query-plan-table.spec.js": { 'desktop-chromium': 3520, 'android-chromium': 4819 },
  "tests/e2e/quick-start-schemii.spec.js": { 'desktop-chromium': 1474, 'android-chromium': 9982 },
  "tests/e2e/quick-start.spec.js": { 'desktop-chromium': 39817, 'android-chromium': 45849 },
  "tests/e2e/raw-console.spec.js": { 'desktop-chromium': 24366, 'android-chromium': 22184 },
  "tests/e2e/raw-copy-streaming.spec.js": { 'desktop-chromium': 5764, 'android-chromium': 13 },
  "tests/e2e/schemer-ai-live.spec.js": { 'desktop-chromium': 69, 'android-chromium': 128 },
  "tests/e2e/schemer-dashboards.spec.js": { 'desktop-chromium': 32615, 'android-chromium': 39209 },
  "tests/e2e/schemoo-ai.spec.js": { 'desktop-chromium': 17412, 'android-chromium': 21022 },
  "tests/e2e/schemoo-aliases.spec.js": { 'desktop-chromium': 18551, 'android-chromium': 25422 },
  "tests/e2e/schemoo-canvas-overlap.spec.js": { 'desktop-chromium': 5206, 'android-chromium': 8561 },
  "tests/e2e/schemoo-column-comparisons.spec.js": { 'desktop-chromium': 16249, 'android-chromium': 23058 },
  "tests/e2e/schemoo-column-filters.spec.js": { 'desktop-chromium': 7443, 'android-chromium': 9921 },
  "tests/e2e/schemoo-connection-colors.spec.js": { 'desktop-chromium': 1603, 'android-chromium': 2159 },
  "tests/e2e/schemoo-derived.spec.js": { 'desktop-chromium': 21069, 'android-chromium': 29666 },
  "tests/e2e/schemoo-diagnostics.spec.js": { 'desktop-chromium': 5651, 'android-chromium': 7690 },
  "tests/e2e/schemoo-dropdowns.spec.js": { 'desktop-chromium': 4619, 'android-chromium': 5774 },
  "tests/e2e/schemoo-filter-authoring.spec.js": { 'desktop-chromium': 10329, 'android-chromium': 11304 },
  "tests/e2e/schemoo-filter-dialog.spec.js": { 'desktop-chromium': 27870, 'android-chromium': 39815 },
  "tests/e2e/schemoo-filter-navigation.spec.js": { 'desktop-chromium': 7044, 'android-chromium': 10118 },
  "tests/e2e/schemoo-help.spec.js": { 'desktop-chromium': 1951, 'android-chromium': 3183 },
  "tests/e2e/schemoo-membership.spec.js": { 'desktop-chromium': 16096, 'android-chromium': 23308 },
  "tests/e2e/schemoo-model-dependencies-live.spec.js": { 'desktop-chromium': 3140, 'android-chromium': 2237 },
  "tests/e2e/schemoo-model-editor-audit.spec.js": { 'desktop-chromium': 3878, 'android-chromium': 5971 },
  "tests/e2e/schemoo-model-library.spec.js": { 'desktop-chromium': 4397, 'android-chromium': 6045 },
  "tests/e2e/schemoo-pinch.spec.js": { 'desktop-chromium': 8, 'android-chromium': 2023 },
  "tests/e2e/schemoo-preview-fields.spec.js": { 'desktop-chromium': 10536, 'android-chromium': 14953 },
  "tests/e2e/schemoo-prototype.spec.js": { 'desktop-chromium': 15290, 'android-chromium': 20108 },
  "tests/e2e/schemoo-repetition-live.spec.js": { 'desktop-chromium': 14990, 'android-chromium': 19936 },
  "tests/e2e/schemoo-saved-previews.spec.js": { 'desktop-chromium': 11495, 'android-chromium': 17113 },
  "tests/e2e/schemoo-source-reconciliation.spec.js": { 'desktop-chromium': 8284, 'android-chromium': 10100 },
  "tests/e2e/shared-report-live.spec.js": { 'desktop-chromium': 18591, 'android-chromium': 26224 },
  "tests/e2e/shared-ui-accessibility.spec.js": { 'desktop-chromium': 1688, 'android-chromium': 1940 },
  "tests/e2e/shared-ui-audit.spec.js": { 'desktop-chromium': 11202, 'android-chromium': 18299 },
  "tests/e2e/sql-console.spec.js": { 'desktop-chromium': 34633, 'android-chromium': 51181 },
  "tests/e2e/workspace-lifecycle.spec.js": { 'desktop-chromium': 6771, 'android-chromium': 9789 },
  "tests/e2e/workspace-rename.spec.js": { 'desktop-chromium': 1831, 'android-chromium': 2670 },
});
