#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { balanceFiles, OBSERVATION, PROJECTS, validateFile } from './browser-shards.mjs';

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

export function invocation(project, files, shard, list, environment = process.env) {
  if (!PROJECTS.includes(project) || ![1, 2].includes(shard) || !files.length) {
    throw new Error('Invalid or empty browser shard');
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
  const environment = { ...process.env };
  delete environment.SCHEMII_E2E_FILE_MANIFEST;
  const discovered = spawnSync(process.execPath,
    [cli, 'test', `--project=${options.project}`, '--list', '--reporter=json'],
    { cwd, env: environment, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (discovered.error || discovered.status !== 0) throw new Error('Playwright discovery failed');
  const plan = balanceFiles(inventoryFiles(JSON.parse(discovered.stdout), cwd), options.project);
  const selected = plan.shards[options.shard - 1];
  if (options.plan) {
    console.log(JSON.stringify({ project: options.project, shard: options.shard,
      observation: OBSERVATION, ...selected, unknownFiles: plan.unknownFiles }));
    return 0;
  }
  const command = invocation(options.project, selected.files, options.shard, options.list, environment);
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
    else if (argument.startsWith('--project=') && !options.project) options.project = argument.slice(10);
    else if (/^--shard=[12]\/2$/.test(argument) && !options.shard) options.shard = Number(argument[8]);
    else throw new Error('Use --project=PROFILE --shard=1/2 or 2/2, optionally --plan or --list');
  }
  if (!PROJECTS.includes(options.project) || !options.shard || (options.plan && options.list)) {
    throw new Error('A supported project and exactly one of the two shards are required');
  }
  return options;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = run(parseOptions(process.argv.slice(2))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
