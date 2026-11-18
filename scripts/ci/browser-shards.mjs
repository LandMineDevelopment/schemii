// Whole-file scheduling preserves serialized shared-account setup/cleanup.
// Costs include first-attempt fixture setup, body and teardown, not retries.
import { isAbsolute, normalize, resolve } from 'node:path';
import { readFileSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const SOURCE_ROOT = fileURLToPath(new URL('../../', import.meta.url));

export const PROJECTS = ['desktop-chromium', 'android-chromium'];
export const OBSERVATION = Object.freeze({
  sourceKind: "local-ci",
  runId: 1794454748157,
  runAttempt: 1,
  sourceSha: "4497b5c370a149f589861d6b49df6d5a5366d102",
  receiptSha256: "786a55c9c0f4f13baae22b4f12bc00238291ef4590d3ae6973747b7fe4ea9856",
  sourceFingerprint: "d80fc27effabaeb7cabadefe690feee39471929ab7d869668712e072812aa223",
  completeBrowserLanes: 12,
  wholeWorkflowOutcome: "success",
  costBasis: "first-attempt-setup+execution+teardown-ms",
  sampleCount: 1,
  fileSourcesSha256: "fad8bc00a298ff3a6f9e299d0099194f5784044c97f178ea07f0a0065be1e167",
  estimatePolicy: "Matching spec bytes use historical measured phase costs. Changed, unreadable or new specs use the device positive median; unknownFiles reports every such estimate. Future runtime and wall balance remain unmeasured.",
  profiles: Object.freeze({
    "desktop-chromium": Object.freeze({"measuredFiles": 64,"collectedCases": 257,"passed": 251,"skipped": 6,"phaseTotalMs": 532689,"costsSha256": "692a10b2ad9e52f26e8cbac298ec10f06cb59dc1d84a1363678c310aed0ce6da"}),
    "android-chromium": Object.freeze({"measuredFiles": 61,"collectedCases": 249,"passed": 243,"skipped": 6,"phaseTotalMs": 535473,"costsSha256": "06eb6ae66227888f0af8f9aafc0bb5154cbce86e393b9eb6b8ed93f2a18ac037"}),
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

function observedFileCost(file, project, costs, sourceRoot) {
  const observation = costs[file];
  if (observation?.[project] === undefined) return undefined;
  if (observation.sourceSha256 !== undefined || observation.sourceBytes !== undefined) {
    if (!/^[a-f0-9]{64}$/.test(observation.sourceSha256)
        || !Number.isSafeInteger(observation.sourceBytes) || observation.sourceBytes < 0) {
      throw new Error('Invalid observed file source');
    }
    try {
      const path = resolve(sourceRoot, file);
      const source = lstatSync(path);
      // Missing, replaced or changed source has no current measured cost. The
      // byte-size check also avoids reading an unrelated newly enlarged file.
      if (!source.isFile() || source.size !== observation.sourceBytes
          || createHash('sha256').update(readFileSync(path)).digest('hex') !== observation.sourceSha256) return undefined;
    } catch (error) {
      if (['ENOENT', 'ENOTDIR', 'EACCES'].includes(error?.code)) return undefined;
      throw error;
    }
  }
  return observation[project];
}

export function balanceFiles(files, project, costs = OBSERVED_COSTS, shardCount = 2, sourceRoot = SOURCE_ROOT) {
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
    const observed = observedFileCost(file, project, costs, sourceRoot);
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

// Shared administrator/provider/physical-target/capacity leaves finish first.
// Only the individually reviewed owner-scoped leaves may overlap. New files
// stay serial until their actual shared effects have been reviewed.
export const PARALLEL_FILES = Object.freeze([
  'account-brand-navigation', 'ai-assistant', 'ai-copy-handoff', 'ai-diagnostic-permissions',
  'ai-failure-recovery', 'ai-model-switch', 'ai-provider-settings', 'connection-lifecycle',
  'console-preferences', 'console-toolbar', 'design-editor-lifecycle', 'inspector-data',
  'inspector-editor', 'migration-disabled-apply', 'migration-review-mobile', 'permission-bundles',
  'product-navigation', 'query-plan-table', 'quick-start-schemii', 'quick-start',
  'schemer-dashboards', 'schemoo-ai', 'schemoo-aliases', 'schemoo-canvas-overlap',
  'schemoo-column-comparisons', 'schemoo-column-filters', 'schemoo-connection-colors',
  'schemoo-derived', 'schemoo-diagnostics', 'schemoo-dropdowns', 'schemoo-filter-authoring',
  'schemoo-filter-dialog', 'schemoo-filter-navigation', 'schemoo-help', 'schemoo-membership',
  'schemoo-model-dependencies-live', 'schemoo-model-editor-audit', 'schemoo-model-library',
  'schemoo-pinch', 'schemoo-preview-fields', 'schemoo-prototype', 'schemoo-repetition-live',
  'schemoo-saved-previews', 'schemoo-source-reconciliation', 'shared-ui-accessibility',
  'shared-ui-audit', 'sql-console', 'toast-history-behavior', 'workspace-lifecycle', 'workspace-rename',
].map(name => `tests/e2e/${name}.spec.js`));

export function parallelFiles(files, project, costs = OBSERVED_COSTS, sourceRoot = SOURCE_ROOT) {
  if (!Array.isArray(files) || !files.length || new Set(files).size !== files.length) {
    throw new Error('Browser shard files must be nonempty and unique');
  }
  files.forEach(validateFile);
  const concurrent = files.filter(file => PARALLEL_FILES.includes(file));
  // One candidate has no useful overlap; retain the complete serial scope.
  if (concurrent.length < 2) return { serial: [...files].sort(), parallel: [] };
  return { serial: files.filter(file => !PARALLEL_FILES.includes(file)).sort(),
    parallel: balanceFiles(concurrent, project, costs, 2, sourceRoot).shards.map(shard => shard.files) };
}

// Opt-in outer routing minimizes serial + max(two serialized children), rather
// than a serial total that would leave the old outer shard as the bottleneck.
// Weights use the same measured-source checks and fallback as serial routing.
export function balanceParallelFiles(files, project, costs = OBSERVED_COSTS, shardCount = 6, sourceRoot = SOURCE_ROOT) {
  const baseline = balanceFiles(files, project, costs, shardCount, sourceRoot);
  const weighted = files.map(file => ({ file, cost: observedFileCost(file, project, costs, sourceRoot) ?? baseline.fallbackMs }))
    .sort((a, b) => b.cost - a.cost || (a.file < b.file ? -1 : 1));
  const bins = Array.from({ length: shardCount }, () => ({ files: [], serialMs: 0, parallelMs: [0, 0] }));
  const total = bin => bin.serialMs + Math.max(...bin.parallelMs);
  for (const parallel of [false, true]) for (const item of weighted.filter(item => PARALLEL_FILES.includes(item.file) === parallel)) {
    const candidates = bins.map((bin, index) => {
      const side = bin.parallelMs[0] <= bin.parallelMs[1] ? 0 : 1;
      return { bin, index, side, next: parallel
        ? bin.serialMs + Math.max(bin.parallelMs[side] + item.cost, bin.parallelMs[1 - side])
        : total(bin) + item.cost };
    }).sort((a, b) => a.next - b.next || total(a.bin) - total(b.bin) || a.bin.files.length - b.bin.files.length || a.index - b.index);
    const selected = candidates[0];
    selected.bin.files.push(item.file);
    if (parallel) selected.bin.parallelMs[selected.side] += item.cost;
    else selected.bin.serialMs += item.cost;
  }
  if (bins.some(bin => !bin.files.length)) throw new Error('Every browser shard must own at least one file');
  return { project, shards: bins.map(bin => ({ files: bin.files.sort(), estimatedMs: total(bin) })),
    unknownFiles: baseline.unknownFiles, fallbackMs: baseline.fallbackMs };
}

// Recalibrated from one accepted natural local run. First-attempt skipped
// cases retain setup/teardown. Costs are historical phase estimates, not future
// wall measurements. Spec hashes prevent changed files from reusing old costs.
export const OBSERVED_COSTS = Object.freeze({
  "tests/e2e/account-audit.spec.js": {"desktop-chromium": 3471,"android-chromium": 3500,"sourceSha256": "8c298575556a4177373872fdfcf5d083973bd464e1bf3a63ff73ce937dabad08","sourceBytes": 5505},
  "tests/e2e/account-brand-navigation.spec.js": {"desktop-chromium": 998,"android-chromium": 884,"sourceSha256": "01ead55678ed5ce2c7333241bab27710f03368b1652a4d6c257d3f30d2733144","sourceBytes": 2619},
  "tests/e2e/accounts.spec.js": {"desktop-chromium": 8951,"android-chromium": 14097,"sourceSha256": "86762b5d6291bf64e042a6ed57703e4c643299fbc458c200805a311af1957a35","sourceBytes": 23595},
  "tests/e2e/ai-assistant.spec.js": {"desktop-chromium": 9728,"android-chromium": 9854,"sourceSha256": "5ec4e1c4bd093f9743c9e1ec1ff7a8c9f31533ee89d1a18afaf0aeeea7ab029e","sourceBytes": 32004},
  "tests/e2e/ai-copy-handoff.spec.js": {"desktop-chromium": 835,"android-chromium": 984,"sourceSha256": "28b9f6747e961b8c39bd41af7643edc5f4edd89b20acaa7e244915bb083be4cd","sourceBytes": 1603},
  "tests/e2e/ai-design-batch-live.spec.js": {"desktop-chromium": 129,"android-chromium": 187,"sourceSha256": "30234cc6b3ab44cafa044681c8eb25a793912856e2be0e970dd6523de9b7ce83","sourceBytes": 5580},
  "tests/e2e/ai-diagnostic-permissions.spec.js": {"desktop-chromium": 3550,"android-chromium": 3441,"sourceSha256": "196b0bd870b76b70111b5a808fae71e369ac9b1b45efad2392d8ea25f467420a","sourceBytes": 6510},
  "tests/e2e/ai-failure-recovery.spec.js": {"desktop-chromium": 5366,"android-chromium": 5256,"sourceSha256": "c549dc3d1d8c953a37755835f77ddd1264241f1aa2cfe0dc74f69f947161eafe","sourceBytes": 4736},
  "tests/e2e/ai-model-switch.spec.js": {"desktop-chromium": 1540,"android-chromium": 1640,"sourceSha256": "256be8ed0a6d37b417ba58cd1e74de79ef6cb3566170da54c681250e6d73d0d1","sourceBytes": 3100},
  "tests/e2e/ai-provider-settings.spec.js": {"desktop-chromium": 10954,"android-chromium": 10836,"sourceSha256": "7f939b1acec0f422720d509cf180026b6393c9baa7dc07efcb726dccd7455ef7","sourceBytes": 22073},
  "tests/e2e/ai-read-live.spec.js": {"desktop-chromium": 139,"android-chromium": 300,"sourceSha256": "783395f2445cafcc22fd8871a615114e3d8872fb7f0075e61843bd9cdbf4a861","sourceBytes": 7151},
  "tests/e2e/ai-structured-read-live.spec.js": {"desktop-chromium": 66,"android-chromium": 89,"sourceSha256": "2181bcc773a5cfc3b77efefea0ee616b8b5f50401b37d038ff5406b686b12945","sourceBytes": 3910},
  "tests/e2e/bulk-jobs.spec.js": {"desktop-chromium": 8829,"sourceSha256": "f3beb6b3c1d025aceacee3b4cf944c3fdc678fa2d82ca81a3bcefacb06f29ff6","sourceBytes": 6265},
  "tests/e2e/connection-lifecycle.spec.js": {"desktop-chromium": 2274,"android-chromium": 2087,"sourceSha256": "fb965a21f9496412215a70a2ad79b1ab12bbe05ec455d37a53ffab25161d78a2","sourceBytes": 4072},
  "tests/e2e/console-preferences.spec.js": {"desktop-chromium": 2112,"android-chromium": 2402,"sourceSha256": "9ca7c6fa2825e09a6ede0a6985c5cdcdbe367ac68b6ed8a04db9c2f8384d67ee","sourceBytes": 1895},
  "tests/e2e/console-shared-capacity.spec.js": {"desktop-chromium": 2219,"sourceSha256": "1bafde3b70cb1a03209d1088588b585aab083886d3a6cc846f1297e9f5be2e53","sourceBytes": 2742},
  "tests/e2e/console-toolbar.spec.js": {"desktop-chromium": 1719,"android-chromium": 2380,"sourceSha256": "8f802a2a15ad2e1e3bc1a23ced187bbd4d8beef43181f5ce7b70e6f267bbff10","sourceBytes": 2964},
  "tests/e2e/design-editor-lifecycle.spec.js": {"desktop-chromium": 26218,"android-chromium": 26150,"sourceSha256": "bdf39a9c77de86d5f91df0e002b26b6305bb283146618303965c7c282b4f0e6a","sourceBytes": 32523},
  "tests/e2e/inspector-data.spec.js": {"desktop-chromium": 13737,"android-chromium": 15575,"sourceSha256": "04add8014dbd430528db81b4a38a274e289567b5b03c72da291cb1c7e310b63f","sourceBytes": 10879},
  "tests/e2e/inspector-editor.spec.js": {"desktop-chromium": 8475,"android-chromium": 8865,"sourceSha256": "01c57b7de3623ce4e7a96bd2676abcdb3dae940425c3e977efa41013292d639d","sourceBytes": 10268},
  "tests/e2e/migration-disabled-apply.spec.js": {"desktop-chromium": 1021,"android-chromium": 1024,"sourceSha256": "d3ec18897fb63893b9b8efc27a8938e192b9533f7f3188c241440d91ae777f81","sourceBytes": 1486},
  "tests/e2e/migration-review-mobile.spec.js": {"desktop-chromium": 1044,"android-chromium": 1209,"sourceSha256": "3ade8a129be9cc1115956b1f8bd5158de14e19ea5a76eae5052ea25b043091fc","sourceBytes": 5292},
  "tests/e2e/permission-bundles.spec.js": {"desktop-chromium": 1222,"android-chromium": 1325,"sourceSha256": "da041430ead8196ee994b83046a2d714119eec590e6ed1d188066fa879b366d6","sourceBytes": 3448},
  "tests/e2e/product-navigation.spec.js": {"desktop-chromium": 811,"android-chromium": 1208,"sourceSha256": "74c1d8562885703ee9074fb12ddf77e2277e57647efcd8ce22221f641a3520a6","sourceBytes": 1136},
  "tests/e2e/query-diagnostics.spec.js": {"desktop-chromium": 11775,"android-chromium": 12614,"sourceSha256": "bb7d89a48a181d9eb7bc6bc0491ade44e6e41722d858a52118be96b8d70d15e6","sourceBytes": 9716},
  "tests/e2e/query-plan-table.spec.js": {"desktop-chromium": 2848,"android-chromium": 3357,"sourceSha256": "225362a5d294626407f6d0e3459ed66a3bdcf3fccf03f1fa1b8a9bd40d01382f","sourceBytes": 6448},
  "tests/e2e/quick-start-schemii.spec.js": {"desktop-chromium": 1137,"android-chromium": 9411,"sourceSha256": "c6500b5116e2c56a44a10331b1e19f529d1b9e7cd1e114148da1cab8ebdb6ca2","sourceBytes": 3497},
  "tests/e2e/quick-start.spec.js": {"desktop-chromium": 19024,"android-chromium": 20326,"sourceSha256": "09451d34c672262af2952192576fcb13cff6bb0de5e15d40b5c072d25b1aa433","sourceBytes": 16380},
  "tests/e2e/raw-console.spec.js": {"desktop-chromium": 19612,"android-chromium": 15736,"sourceSha256": "edc92bb3272683eb8d5374914676de8c8944415248c01c44577c10d9761ff3ef","sourceBytes": 18747},
  "tests/e2e/raw-copy-streaming.spec.js": {"desktop-chromium": 4480,"sourceSha256": "3a4654ac73ef38f23803a5d637027d07fc8d0ef4470467c3fa3908a74db8a4f3","sourceBytes": 4287},
  "tests/e2e/schemer-ai-live.spec.js": {"desktop-chromium": 70,"android-chromium": 85,"sourceSha256": "be44891d1fbdb51842eec8132c6520147560384fc9291db327e7c77cf57d5627","sourceBytes": 3479},
  "tests/e2e/schemer-dashboards.spec.js": {"desktop-chromium": 29424,"android-chromium": 31441,"sourceSha256": "acfe3a1fb91623cb03c135f257189eed5f0a40cd150fce154e9c15277ff88ed0","sourceBytes": 48815},
  "tests/e2e/schemii-view-column-summary.spec.js": {"desktop-chromium": 3729,"android-chromium": 3810,"sourceSha256": "f46d925b2dc054593c08535fa4e954693ee7c50fd14bdb891dc1a9b4c45c88a5","sourceBytes": 6455},
  "tests/e2e/schemoo-ai.spec.js": {"desktop-chromium": 16484,"android-chromium": 18497,"sourceSha256": "c4aa5a7923df6793bd2444b375c923ca5045a1d3e6b12a1d809c44a8ddb425f7","sourceBytes": 32293},
  "tests/e2e/schemoo-aliases.spec.js": {"desktop-chromium": 17265,"android-chromium": 18254,"sourceSha256": "4ae23e92f232c3f4299da83129623d51fe8fcb60c8ca67dfc8b356fd7885e8d1","sourceBytes": 14658},
  "tests/e2e/schemoo-canvas-overlap.spec.js": {"desktop-chromium": 4951,"android-chromium": 5891,"sourceSha256": "616222269c134f93cfad4912923a51e216b6db74cc1da55b37b459de44b421b9","sourceBytes": 8350},
  "tests/e2e/schemoo-column-comparisons.spec.js": {"desktop-chromium": 15440,"android-chromium": 17041,"sourceSha256": "92d6fceda5a98a32b608cc7d2f735ec796ef5cf16e8610f958aa0c57e75cff53","sourceBytes": 12204},
  "tests/e2e/schemoo-column-filters.spec.js": {"desktop-chromium": 6974,"android-chromium": 6540,"sourceSha256": "1393b694f5439d82462d92c6e4ce3843a0b62912cb586a825638830d12ce6da0","sourceBytes": 13208},
  "tests/e2e/schemoo-connection-colors.spec.js": {"desktop-chromium": 1619,"android-chromium": 1718,"sourceSha256": "9392511d3e8b4875756ea881c4c3c43a04616f9eb99b46983a5a3eb58b1c9f93","sourceBytes": 3968},
  "tests/e2e/schemoo-derived.spec.js": {"desktop-chromium": 20472,"android-chromium": 20718,"sourceSha256": "854d9bb64d8a2fe8826c79ffa064638424821f7ae5e8ae0e78809799fe9ded2d","sourceBytes": 16496},
  "tests/e2e/schemoo-diagnostics.spec.js": {"desktop-chromium": 5542,"android-chromium": 5783,"sourceSha256": "dedd8f34c0663b9ad216d4035bdbfd66e0f9dcc0ae5896c77f5516f18c19be56","sourceBytes": 3095},
  "tests/e2e/schemoo-dropdowns.spec.js": {"desktop-chromium": 4049,"android-chromium": 4364,"sourceSha256": "dfb55837f4a857b4402fcce484d9502454884f292e8a56c175344640b4fba983","sourceBytes": 5144},
  "tests/e2e/schemoo-filter-authoring.spec.js": {"desktop-chromium": 10220,"android-chromium": 9643,"sourceSha256": "1fa84c7842c04eafb826b94f8f42c6b27774f929fbe94635b6d0212f50d7cc0a","sourceBytes": 5808},
  "tests/e2e/schemoo-filter-dialog.spec.js": {"desktop-chromium": 25670,"android-chromium": 27948,"sourceSha256": "ede3ab14f3d8da4590830eb2e933a21be538185fdf1ef3d7bb2ac6755be3f9cb","sourceBytes": 15572},
  "tests/e2e/schemoo-filter-navigation.spec.js": {"desktop-chromium": 6697,"android-chromium": 7023,"sourceSha256": "9b57b6d527197f48bcf00af234f7ca2b8f6e79d27ca6ff3a7fa910597264e588","sourceBytes": 9400},
  "tests/e2e/schemoo-help.spec.js": {"desktop-chromium": 1984,"android-chromium": 2319,"sourceSha256": "c9ffd94c18b5668c33939dd2e4467d0893feeed6ed16d81cd71d8baa061f37b3","sourceBytes": 2274},
  "tests/e2e/schemoo-membership.spec.js": {"desktop-chromium": 16032,"android-chromium": 16076,"sourceSha256": "8fd253b719fec95a0eebc3847c227f748d6866897db27ed4264c7f442872ad64","sourceBytes": 8527},
  "tests/e2e/schemoo-model-dependencies-live.spec.js": {"desktop-chromium": 3228,"android-chromium": 1408,"sourceSha256": "0c71d40f97d30b97c81ec6e3c5311ae8f45976ff90dd0a5b010462ab6e796513","sourceBytes": 4732},
  "tests/e2e/schemoo-model-editor-audit.spec.js": {"desktop-chromium": 8803,"android-chromium": 10235,"sourceSha256": "62295675510cad24bc12122f7c3843572c3b9ff0bb886e30d1b073ce93101076","sourceBytes": 31961},
  "tests/e2e/schemoo-model-library.spec.js": {"desktop-chromium": 4148,"android-chromium": 4515,"sourceSha256": "05e116ea010eefe851ef5a2140efd92f0abc6f875995e86558793b8df827a346","sourceBytes": 14467},
  "tests/e2e/schemoo-pinch.spec.js": {"desktop-chromium": 17,"android-chromium": 1259,"sourceSha256": "053bd09c65ccd05a58beaa935ad31e83e528e49f47a9d27333b1e378cddac148","sourceBytes": 2287},
  "tests/e2e/schemoo-preview-fields.spec.js": {"desktop-chromium": 10435,"android-chromium": 10332,"sourceSha256": "2902a7375cf4cef466fecfcc894b2e874d647ff136d3288624fcd3cacd591cca","sourceBytes": 10315},
  "tests/e2e/schemoo-prototype.spec.js": {"desktop-chromium": 27589,"android-chromium": 15670,"sourceSha256": "3d13f969f3232902bb5c94f86e4fd732031006e31b409db0d1b2cb907b2fe2b3","sourceBytes": 14024},
  "tests/e2e/schemoo-repetition-live.spec.js": {"desktop-chromium": 16215,"android-chromium": 16301,"sourceSha256": "ef8ab3fcf172eb2105fad2fa4c748644cb5ef01c5f45b3b31e0ff52867014e5c","sourceBytes": 10363},
  "tests/e2e/schemoo-saved-previews.spec.js": {"desktop-chromium": 11585,"android-chromium": 11638,"sourceSha256": "035f325b1f0a26be17f8c433ad633bfc83ca673ab497648a0bb997efc491856e","sourceBytes": 12493},
  "tests/e2e/schemoo-source-reconciliation.spec.js": {"desktop-chromium": 8079,"android-chromium": 8258,"sourceSha256": "c753ae5fa50b6a0cea8560e5ef5a36c3b49fe6e22a51fa5846500e1146f06ba1","sourceBytes": 6696},
  "tests/e2e/semantic-api-owned.spec.js": {"desktop-chromium": 4627,"android-chromium": 5110,"sourceSha256": "799c65f7ff0107f4a06e46d6254fc1534f20100e73583839f2d0a523add45d61","sourceBytes": 11227},
  "tests/e2e/shared-report-live.spec.js": {"desktop-chromium": 20817,"android-chromium": 20538,"sourceSha256": "da4a8f7ac29defe6357e8b636c284f10cd35bcb1e52c2416defd98327497a9bb","sourceBytes": 11460},
  "tests/e2e/shared-ui-accessibility.spec.js": {"desktop-chromium": 1633,"android-chromium": 1098,"sourceSha256": "53dc9fe1e2f0c140a9d57fa68e346e3bee5716f071fdfb475211448e46e617ab","sourceBytes": 4168},
  "tests/e2e/shared-ui-audit.spec.js": {"desktop-chromium": 10961,"android-chromium": 11846,"sourceSha256": "ced5e3da1d843ac53748296cbf5a882b7d8873f9a426fa30018092d394d162bd","sourceBytes": 9640},
  "tests/e2e/sql-console.spec.js": {"desktop-chromium": 32233,"android-chromium": 33100,"sourceSha256": "b1692876f91cf5bb073d3b000c9efdb1521403d0927d5a245348e82fa2dbbf43","sourceBytes": 21706},
  "tests/e2e/toast-history-behavior.spec.js": {"desktop-chromium": 3074,"android-chromium": 3256,"sourceSha256": "29eedbdd3bd4fe40414a720256988c031e1935cf92c66fd0f3729edcd6012998","sourceBytes": 9012},
  "tests/e2e/workspace-lifecycle.spec.js": {"desktop-chromium": 6977,"android-chromium": 7767,"sourceSha256": "79fb208c1a7081f01cc8d21eec1bba7eb60fdfcd23440ea6848e43a9a7689d37","sourceBytes": 11446},
  "tests/e2e/workspace-rename.spec.js": {"desktop-chromium": 1362,"android-chromium": 1254,"sourceSha256": "b3b50c752b56c713bffe0d71bba8932237fada6d7e85789f2a5ba29c8f81c4e4","sourceBytes": 1840},
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
