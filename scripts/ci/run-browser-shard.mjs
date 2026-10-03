#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { balanceFiles, coverageProfile, OBSERVATION, PROJECTS, validateFile, validateShardCount } from './browser-shards.mjs';

const cli = resolve(fileURLToPath(new URL('../../node_modules/@playwright/test/cli.js', import.meta.url)));

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
  const policy = coverageProfile(options.profile === undefined ? 'full' : options.profile);
  if (policy && shardCount !== 3) throw new Error('Selected profile requires all three browser shards');
  const environment = { ...process.env };
  delete environment.SCHEMII_E2E_FILE_MANIFEST;
  const discovered = spawnSync(process.execPath,
    [cli, 'test', `--project=${options.project}`, '--list', '--reporter=json'],
    { cwd, env: environment, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (discovered.error || discovered.status !== 0) throw new Error('Playwright discovery failed');
  const report = JSON.parse(discovered.stdout);
  const plan = policy
    ? { shards: scopedInventory(report, cwd, options.project, policy).map(files => ({ files })), unknownFiles: [] }
    : balanceFiles(inventoryFiles(report, cwd), options.project, undefined, shardCount);
  const selected = plan.shards[options.shard - 1];
  if (options.plan) {
    console.log(JSON.stringify({ project: options.project, shard: options.shard, shardCount,
      observation: OBSERVATION, ...(policy ? { profile: options.profile } : {}), ...selected, unknownFiles: plan.unknownFiles }));
    return 0;
  }
  const command = invocation(options.project, selected.files, options.shard, options.list, environment, shardCount);
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
    else if (argument.startsWith('--profile=') && !Object.hasOwn(options, 'profile')) options.profile = argument.slice(10);
    else if (argument.startsWith('--project=') && !options.project) options.project = argument.slice(10);
    else if (/^--shard=[1-3]\/[23]$/.test(argument) && !options.shard) {
      const [current, total] = argument.slice(8).split('/').map(Number);
      options.shard = current;
      // Keep the existing exported two-way options shape and default intact.
      if (total !== 2) options.shardCount = total;
    }
    else throw new Error('Use --project=PROFILE --shard=CURRENT/2 or CURRENT/3, optionally --profile=full|e2e-tests|schemer-result-cache and --plan or --list');
  }
  validateShard(options.project, options.shard, options.shardCount ?? 2);
  coverageProfile(options.profile === undefined ? 'full' : options.profile);
  if (options.profile === 'schemer-result-cache' && options.shardCount !== 3) throw new Error('Selected profile requires all three browser shards');
  if (options.plan && options.list) throw new Error('Choose only one of --plan or --list');
  return options;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = run(parseOptions(process.argv.slice(2))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
