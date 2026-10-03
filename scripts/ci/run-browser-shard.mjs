#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, symlinkSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { balanceFiles, balanceParallelFiles, coverageProfile, OBSERVATION, parallelFiles, PROJECTS, validateFile, validateShardCount } from './browser-shards.mjs';
import { hash, metadata, milliseconds, originalSourceRoot, writer } from './timing.mjs';

const cli = resolve(fileURLToPath(new URL('../../node_modules/@playwright/test/cli.js', import.meta.url)));

export function inventoryCases(report, cwd, project, files) {
  const cases = [];
  function visit(suite) {
    for (const spec of suite.specs || []) {
      const file = validateFile(relative(cwd, resolve(report.config.rootDir, spec.file)));
      if (!files.includes(file)) continue;
      if (spec.tests?.length !== 1 || spec.tests[0].projectName !== project
          || typeof spec.id !== 'string' || !spec.id || !Number.isSafeInteger(spec.line) || spec.line < 1) {
        throw new Error('Invalid discovered browser case');
      }
      cases.push({ file, test_id: hash(spec.id), source_id: hash(file), source_line: spec.line });
    }
    for (const nested of suite.suites || []) visit(nested);
  }
  if (report.errors?.length) throw new Error('Playwright discovery failed');
  for (const suite of report.suites || []) visit(suite);
  if (!cases.length || new Set(cases.map(item => item.test_id)).size !== cases.length
      || files.some(file => !cases.some(item => item.file === file))) throw new Error('Incomplete browser case inventory');
  return cases;
}

const META = ['schema', 'source_sha', 'run_id', 'run_attempt', 'lane', 'project', 'shard'];
const FIELDS = {
  start: ['kind', 'planned'], plan: ['kind', 'test_id'],
  attempt: ['kind', 'test_id', 'source_id', 'source_line', 'attempt', 'outcome', 'skip', 'setup_ms', 'execution_ms', 'teardown_ms'],
  end: ['kind', 'outcome', 'wall_ms'],
};
const outcomes = ['passed', 'failed', 'timed-out', 'skipped', 'cancelled', 'not-run'];
const terminals = ['passed', 'failed', 'timed-out', 'cancelled', 'collection-error', 'error'];
const number = (value, integer = false) => typeof value === 'number' && Number.isFinite(value)
  && value >= 0 && value <= 1e15 && (!integer || Number.isSafeInteger(value));

// Closed public schema plus original discovery identity. No error/log/title/
// environment from a child can enter the joined public receipt.
export function validateBrowserReceipt(records, cases, meta) {
  if (!Array.isArray(records) || !records.length || records.length > 100000) throw new Error('Invalid browser child receipt');
  const expected = new Map(cases.map(item => [item.test_id, item]));
  const plans = new Set();
  const attempts = [];
  const counts = new Map();
  let end;
  let inAttempts = false;
  for (const [index, record] of records.entries()) {
    const fields = FIELDS[record?.kind];
    if (!fields || Object.keys(record).sort().join() !== [...META, ...fields].sort().join()
        || META.some(key => record[key] !== meta[key]) || end) throw new Error('Invalid browser child receipt');
    if (record.kind === 'start') {
      if (index !== 0 || record.planned !== cases.length) throw new Error('Invalid browser child plan');
    } else if (index === 0) throw new Error('Missing browser child start');
    else if (record.kind === 'plan') {
      if (inAttempts || !expected.has(record.test_id) || plans.has(record.test_id)) throw new Error('Invalid browser child plan');
      plans.add(record.test_id);
    } else if (record.kind === 'attempt') {
      inAttempts = true;
      const item = expected.get(record.test_id);
      const count = counts.get(record.test_id) || 0;
      if (plans.size !== cases.length || !item || record.source_id !== item.source_id
          || record.source_line !== item.source_line || record.attempt !== count
          || !outcomes.includes(record.outcome)
          || record.skip !== (record.outcome === 'skipped' ? 'declared-or-runtime' : 'none')
          || !['setup_ms', 'execution_ms', 'teardown_ms'].every(key => number(record[key]))) {
        throw new Error('Invalid browser child attempt');
      }
      counts.set(record.test_id, count + 1);
      attempts.push(record);
    } else {
      if (!terminals.includes(record.outcome) || !number(record.wall_ms)) throw new Error('Invalid browser child end');
      end = record;
    }
  }
  if (plans.size !== cases.length) throw new Error('Incomplete browser child plan');
  const last = new Map(attempts.map(record => [record.test_id, record.outcome]));
  if (end?.outcome === 'passed' && [...last.values()].some(outcome => !['passed', 'skipped'].includes(outcome))) {
    throw new Error('Contradictory browser child outcome');
  }
  return { attempts, end, complete: Boolean(end && ['passed', 'failed'].includes(end.outcome)
    && counts.size === cases.length && !attempts.some(record => ['cancelled', 'not-run'].includes(record.outcome))) };
}

export function joinBrowserReceipts(cases, children, meta, wallMs, cancelled = false) {
  const union = children.flatMap(child => child.cases.map(item => item.test_id));
  if (new Set(union).size !== union.length || union.length !== cases.length
      || cases.some(item => !union.includes(item.test_id))
      || children.some(child => child.cases.some(item => !cases.some(original => original.test_id === item.test_id
        && original.source_id === item.source_id && original.source_line === item.source_line && original.file === item.file)))) {
    throw new Error('Invalid browser process case union');
  }
  const records = [{ ...meta, kind: 'start', planned: cases.length },
    ...cases.map(item => ({ ...meta, kind: 'plan', test_id: item.test_id }))];
  let incomplete = false;
  let failed = false;
  for (const child of children) {
    let receipt;
    try { receipt = validateBrowserReceipt(child.records, child.cases, meta); }
    catch { incomplete = true; }
    if (receipt) {
      records.push(...receipt.attempts);
      incomplete ||= !receipt.complete || !Number.isInteger(child.status) || child.status < 0
        || (child.status !== 0 && receipt.end?.outcome === 'passed');
      failed ||= child.status !== 0 || receipt.end?.outcome === 'failed';
      cancelled ||= Boolean(child.signal) || receipt.end?.outcome === 'cancelled';
    }
    const seen = new Set(receipt?.attempts.map(record => record.test_id));
    for (const item of child.cases) {
      if (!seen.has(item.test_id)) records.push({ ...meta, kind: 'attempt', test_id: item.test_id,
        source_id: item.source_id, source_line: item.source_line, attempt: 0,
        outcome: cancelled ? 'cancelled' : 'not-run', skip: 'none', setup_ms: 0, execution_ms: 0, teardown_ms: 0 });
    }
  }
  records.push({ ...meta, kind: 'end', outcome: cancelled ? 'cancelled' : incomplete ? 'error' : failed ? 'failed' : 'passed',
    wall_ms: milliseconds(wallMs) });
  return records;
}

// Every child owns a new OS process group. Cancellation and normal completion
// clean only those groups, including descendants left behind by a child.
function ownedGroupAlive(pid) {
  if (!pid) return false;
  // Exited descendants may briefly be adopted zombies. They cannot execute,
  // retain descriptors or respond to signals; do not mistake them for survivors.
  for (const name of readdirSync('/proc').filter(name => /^\d+$/.test(name))) {
    let fields;
    try {
      const stat = readFileSync(`/proc/${name}/stat`, 'utf8');
      fields = stat.slice(stat.lastIndexOf(') ') + 2).split(' ');
    } catch (error) { if (['ENOENT', 'ESRCH'].includes(error.code)) continue; throw new Error('Browser child death observation failed'); }
    if (Number(fields[2]) === pid && fields[0] !== 'Z') return true;
  }
  return false;
}

export async function runOwnedChildren(commands, { signal, graceMs = 1000 } = {}) {
  if (!Number.isInteger(graceMs) || graceMs < 1 || graceMs > 5000) throw new Error('Invalid browser cleanup grace');
  const entries = [];
  const kill = (pid, name) => {
    if (!pid) return;
    try { process.kill(-pid, name); }
    catch (error) { if (error.code !== 'ESRCH') throw new Error('Browser child cleanup failed'); }
  };
  const stop = entry => {
    if (!entry.cleanup) entry.cleanup = (async () => {
      const pid = entry.child.pid;
      if (!ownedGroupAlive(pid)) return;
      kill(pid, 'SIGTERM');
      const deadline = performance.now() + graceMs;
      while (ownedGroupAlive(pid) && performance.now() < deadline) await delay(20);
      if (ownedGroupAlive(pid)) kill(pid, 'SIGKILL');
      const deathDeadline = performance.now() + 1000;
      while (ownedGroupAlive(pid) && performance.now() < deathDeadline) await delay(20);
      if (ownedGroupAlive(pid)) throw new Error('Owned browser child survived cleanup');
    })();
    return entry.cleanup;
  };
  const terminate = () => { for (const entry of entries) void stop(entry).catch(() => {}); };
  signal?.addEventListener('abort', terminate, { once: true });
  try {
    const results = await Promise.all(commands.map(command => new Promise(resolveResult => {
      if (signal?.aborted) { resolveResult({ status: null, signal: 'SIGTERM' }); return; }
      const child = spawn(command.command, command.args, { cwd: command.cwd, env: command.env,
        stdio: command.stdio || 'inherit', detached: true });
      const entry = { child, closed: false };
      entries.push(entry);
      child.on('error', () => resolveResult({ status: null, signal: null }));
      child.on('close', () => { entry.closed = true; });
      // exit precedes close: an orphan holding piped stdout must not prevent
      // cleanup from beginning. Preserve the actual leader exit status.
      child.on('exit', (status, childSignal) => {
        void stop(entry).catch(() => {});
        resolveResult({ status, signal: childSignal });
      });
    })));
    await Promise.all(entries.map(stop));
    const closeDeadline = performance.now() + 1000;
    while (entries.some(entry => !entry.closed) && performance.now() < closeDeadline) await delay(20);
    if (entries.some(entry => !entry.closed)) throw new Error('Browser child transport did not close');
    return results;
  } finally {
    signal?.removeEventListener('abort', terminate);
    await Promise.all(entries.map(stop));
  }
}

function childRecords(path) {
  try {
    const info = lstatSync(path);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 20 * 1024 * 1024) return undefined;
    return readFileSync(path, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
  } catch { return undefined; }
}

export function executionDirectory(root, sourceRoot, phase) {
  if (!['serial', 'parallel'].includes(phase)) throw new Error('Invalid browser execution phase');
  const directory = resolve(root, phase);
  // mkdir without recursive is intentional: a prior attempt is never reused.
  mkdirSync(directory, { mode: 0o700 });
  copyFileSync(resolve(sourceRoot, 'playwright.config.js'), resolve(directory, 'playwright.config.js'));
  copyFileSync(resolve(sourceRoot, 'package.json'), resolve(directory, 'package.json'));
  for (const path of ['node_modules', 'scripts']) symlinkSync(resolve(sourceRoot, path), resolve(directory, path));
  return directory;
}

async function runParallel(options, cwd, environment, report, files, started) {
  const sourceRoot = realpathSync(cwd);
  originalSourceRoot({ SCHEMII_E2E_SOURCE_ROOT: sourceRoot });
  const manifestPath = environment.SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE;
  if (typeof manifestPath !== 'string' || resolve(manifestPath) !== manifestPath) throw new Error('Parallel browser execution requires a ready account manifest');
  // Provisioning and cleanup are explicit coordinator operations. This loader
  // authenticates the private ready barrier; children never create fixtures.
  let manifest;
  try {
    const { readParallelAccounts } = await import(pathToFileURL(resolve(sourceRoot, 'tests/e2e/helpers/parallel-account.js')));
    manifest = await readParallelAccounts(manifestPath);
  } catch { throw new Error('Invalid ready browser accounts'); }
  if (manifest.sourceRoot !== sourceRoot || manifest.physicalTargets !== 'shared'
      || manifest.baseURL !== (environment.SCHEMII_E2E_BASE_URL || 'https://localhost:8001')) {
    throw new Error('Ready browser accounts do not match this execution');
  }
  const cases = inventoryCases(report, sourceRoot, options.project, files);
  const partition = parallelFiles(files, options.project);
  const meta = metadata('browser', options.project, options.shard);
  const write = writer(resolve(cwd, environment.CI_TELEMETRY_FILE || 'artifacts/ci-timing/browser.jsonl'), meta, { exclusive: true });
  write({ kind: 'start', planned: cases.length });
  for (const item of cases) write({ kind: 'plan', test_id: item.test_id });
  const controller = new AbortController();
  let interruption;
  const interrupt = name => { interruption = name; controller.abort(); };
  const sigint = () => interrupt('SIGINT');
  const sigterm = () => interrupt('SIGTERM');
  process.on('SIGINT', sigint);
  process.on('SIGTERM', sigterm);
  const groups = [
    ...(partition.serial.length ? [{ files: partition.serial, index: 1, phase: 'serial' }] : []),
    ...partition.parallel.map((ownedFiles, index) => ({ files: ownedFiles, index: index + 1, phase: 'parallel' })),
  ].map(group => ({ ...group, cases: cases.filter(item => group.files.includes(item.file)), status: null, signal: null }));
  let infrastructureFailed = false;
  try {
    for (const group of groups) {
      const slot = manifest.accounts.find(account => account.index === group.index);
      const directory = executionDirectory(slot.root, sourceRoot, group.phase);
      group.receipt = resolve(directory, 'browser.jsonl');
      const childEnvironment = { ...environment, SCHEMII_E2E_SOURCE_ROOT: sourceRoot,
        SCHEMII_E2E_PROCESS_INDEX: String(group.index), SCHEMII_E2E_BOOTSTRAP: '0', CI_TELEMETRY_FILE: group.receipt };
      for (const key of ['SCHEMII_E2E_USERNAME', 'SCHEMII_E2E_PASSWORD', 'SCHEMII_E2E_CREDENTIALS_FILE', 'SCHEMII_E2E_SETUP_TOKEN']) delete childEnvironment[key];
      group.command = { ...invocation(options.project, group.files, options.shard, false, childEnvironment, options.shardCount ?? 2), cwd: directory };
      // Local opt-in keeps its normal retry setting while producing evidence.
      group.command.args.push(`--reporter=line,${resolve(sourceRoot, 'scripts/ci/playwright-reporter.mjs')}`);
    }
    const serial = groups.filter(group => group.phase === 'serial');
    if (serial.length) Object.assign(serial[0], (await runOwnedChildren(serial.map(group => group.command), { signal: controller.signal }))[0]);
    // A failed prerequisite never advances into overlap. Its original receipt
    // remains private and its missing successors remain explicit in the union.
    if (!controller.signal.aborted && (!serial.length || serial[0].status === 0)) {
      const parallel = groups.filter(group => group.phase === 'parallel');
      const results = await runOwnedChildren(parallel.map(group => group.command), { signal: controller.signal });
      parallel.forEach((group, index) => Object.assign(group, results[index]));
    }
  } catch { infrastructureFailed = true; }
  finally {
    process.removeListener('SIGINT', sigint);
    process.removeListener('SIGTERM', sigterm);
  }
  for (const group of groups) group.records = childRecords(group.receipt);
  const joined = joinBrowserReceipts(cases, groups, meta, performance.now() - started, controller.signal.aborted);
  if (infrastructureFailed && joined.at(-1).outcome !== 'cancelled') joined.at(-1).outcome = 'error';
  for (const record of joined.slice(cases.length + 1)) write(record);
  return interruption === 'SIGINT' ? 130 : interruption === 'SIGTERM' ? 143 : joined.at(-1).outcome === 'passed' ? 0 : 1;
}

export function inventoryFiles(report, cwd) {
  if (report.errors?.length) throw new Error('Playwright discovery failed');
  const files = new Set();
  function visit(suite) {
    for (const spec of suite.specs || []) {
      files.add(validateFile(relative(cwd, resolve(report.config.rootDir, spec.file))));
    }
    for (const nested of suite.suites || []) visit(nested);
  }
  for (const suite of report.suites || []) visit(suite);
  return [...files].sort();
}

export function scopedInventory(report, cwd, project, policy) {
  if (report.errors?.length) throw new Error('Playwright discovery failed');
  const found = Object.fromEntries(policy.files.map(file => [file, []]));
  function visit(suite) {
    for (const spec of suite.specs || []) {
      const file = validateFile(relative(cwd, resolve(report.config.rootDir, spec.file)));
      if (!policy.files.includes(file)) continue;
      if (spec.tests?.length !== 1 || spec.tests[0].projectName !== project || typeof spec.id !== 'string') {
        throw new Error('Invalid selected browser project/case');
      }
      found[file].push(createHash('sha256').update(spec.id).digest('hex'));
    }
    for (const nested of suite.suites || []) visit(nested);
  }
  for (const suite of report.suites || []) visit(suite);
  for (const file of policy.files) {
    if (JSON.stringify(found[file].sort()) !== JSON.stringify(policy.browser[project].files[file])) {
      throw new Error('Missing or changed expected browser cases');
    }
  }
  return policy.browser[project].shards;
}

function validateShard(project, shard, shardCount) {
  validateShardCount(shardCount);
  if (!PROJECTS.includes(project) || !Number.isInteger(shard) || shard < 1 || shard > shardCount) {
    throw new Error('Invalid browser project or shard index');
  }
}

function profileForShards(profile, shardCount) {
  const policy = coverageProfile(profile === undefined ? 'full' : profile);
  if (policy?.profile === 'developer-inspection' && shardCount !== 1) throw new Error('Inspection profile requires its one browser shard');
  if (policy?.profile === 'schemer-result-cache' && shardCount !== 3) throw new Error('Selected profile requires all three browser shards');
  // Explicit profiles are the hosted acceptance contract. Historical standalone
  // two/three-way local discovery remains available without a profile flag.
  if (!policy && (profile !== undefined || shardCount === 1) && shardCount !== 6) {
    throw new Error('Full and E2E profiles require all six browser shards');
  }
  return policy;
}

export function invocation(project, files, shard, list, environment = process.env, shardCount = 2) {
  validateShard(project, shard, shardCount);
  if (!Array.isArray(files) || !files.length || new Set(files).size !== files.length) {
    throw new Error('Browser shard files must be nonempty and unique');
  }
  files.forEach(validateFile);
  return {
    command: process.execPath,
    args: [cli, 'test', `--project=${project}`, ...(list ? ['--list', '--reporter=json'] : [])],
    env: {
      ...environment,
      SCHEMII_E2E_FILE_MANIFEST: JSON.stringify(files),
      CI_TELEMETRY_PROJECT: project,
      CI_TELEMETRY_SHARD: String(shard),
    },
  };
}

export function run(options, cwd = process.cwd()) {
  const { shardCount = 2 } = options;
  validateShard(options.project, options.shard, shardCount);
  const policy = profileForShards(options.profile, shardCount);
  if (options.parallel !== undefined && options.parallel !== 2) throw new Error('Only explicit two-process browser execution is supported');
  if (options.parallel && !['full', 'e2e-tests', 'schemer-result-cache', 'developer-inspection'].includes(options.profile)) {
    throw new Error('Parallel browser execution requires an explicit coverage profile');
  }
  if (options.parallel && options.list) throw new Error('Parallel browser execution cannot list tests');
  const started = options.parallel ? performance.now() : undefined;
  const environment = { ...process.env };
  delete environment.SCHEMII_E2E_FILE_MANIFEST;
  const discoveryEnvironment = { ...environment };
  if (options.parallel) {
    for (const key of ['SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE', 'SCHEMII_E2E_PROCESS_INDEX', 'SCHEMII_E2E_SOURCE_ROOT']) delete discoveryEnvironment[key];
  }
  const discovered = spawnSync(process.execPath,
    [cli, 'test', `--project=${options.project}`, '--list', '--reporter=json'],
    { cwd, env: discoveryEnvironment, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (discovered.error || discovered.status !== 0) throw new Error('Playwright discovery failed');
  let report;
  try { report = JSON.parse(discovered.stdout); }
  catch { throw new Error('Invalid Playwright discovery'); }
  const plan = policy
    ? { shards: scopedInventory(report, cwd, options.project, policy).map(files => ({ files })), unknownFiles: [] }
    : (options.parallel ? balanceParallelFiles : balanceFiles)(inventoryFiles(report, cwd), options.project, undefined, shardCount);
  const selected = plan.shards[options.shard - 1];
  if (options.plan) {
    console.log(JSON.stringify({ project: options.project, shard: options.shard, shardCount,
      observation: OBSERVATION, ...(policy ? { profile: options.profile } : {}), ...selected,
      ...(options.parallel && !policy ? parallelFiles(selected.files, options.project) : {}), unknownFiles: plan.unknownFiles }));
    return 0;
  }
  // The existing small reviewed scopes retain their single-process path.
  if (options.parallel && !policy) return runParallel(options, cwd, environment, report, selected.files, started);
  const command = invocation(options.project, selected.files, options.shard, options.list, options.parallel ? discoveryEnvironment : environment, shardCount);
  const result = spawnSync(command.command, command.args,
    { cwd, env: command.env, stdio: 'inherit' });
  if (result.error) throw new Error('Browser runner could not start');
  return result.status ?? 1;
}

export function parseOptions(args) {
  const options = { list: false, plan: false };
  for (const argument of args) {
    if (argument === '--list') options.list = true;
    else if (argument === '--plan') options.plan = true;
    else if (argument === '--parallel=2' && !options.parallel) options.parallel = 2;
    else if (argument.startsWith('--profile=') && !Object.hasOwn(options, 'profile')) options.profile = argument.slice(10);
    else if (argument.startsWith('--project=') && !options.project) options.project = argument.slice(10);
    else if (/^--shard=[1-6]\/[1236]$/.test(argument) && !options.shard) {
      const [current, total] = argument.slice(8).split('/').map(Number);
      options.shard = current;
      // Keep the existing exported two-way options shape and default intact.
      if (total !== 2) options.shardCount = total;
    }
    else throw new Error('Use --project=PROFILE --shard=CURRENT/1, CURRENT/2, CURRENT/3 or CURRENT/6, optionally --profile=full|e2e-tests|schemer-result-cache|developer-inspection, --parallel=2 and --plan or --list');
  }
  validateShard(options.project, options.shard, options.shardCount ?? 2);
  profileForShards(options.profile, options.shardCount ?? 2);
  if (options.plan && options.list) throw new Error('Choose only one of --plan or --list');
  if (options.parallel && (!options.profile || options.list)) throw new Error('Parallel browser execution requires an explicit profile and cannot list tests');
  return options;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await run(parseOptions(process.argv.slice(2))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
