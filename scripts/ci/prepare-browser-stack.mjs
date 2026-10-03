#!/usr/bin/env node
import { realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runOwnedChildren } from './run-browser-shard.mjs';

export function parseOptions(args) {
  if (!Array.isArray(args) || (args.length && (args.length !== 1 || args[0] !== '--discovery'))) {
    throw new Error('Use prepare-browser-stack.mjs with optional --discovery');
  }
  return args.length === 1;
}

export function preparationCommands(discovery = false) {
  if (typeof discovery !== 'boolean') throw new Error('Discovery must be explicitly enabled');
  const cwd = realpathSync(fileURLToPath(new URL('../../', import.meta.url)));
  const env = { ...process.env };
  return [{ command: './start.sh', args: [], cwd, env },
    ...(discovery ? [{ command: process.execPath,
      args: ['--test', 'tests/browser-infrastructure/shards.test.mjs'], cwd, env }] : [])];
}

export async function run(discovery = false) {
  const commands = preparationCommands(discovery);
  const controller = new AbortController();
  let interrupted;
  const sigint = () => { interrupted = 130; controller.abort(); };
  const sigterm = () => { interrupted = 143; controller.abort(); };
  process.on('SIGINT', sigint);
  process.on('SIGTERM', sigterm);
  try {
    const results = await runOwnedChildren(commands, { signal: controller.signal });
    if (interrupted) return interrupted;
    const failed = results.find(result => result.status !== 0);
    return failed ? failed.status || 1 : 0;
  } finally {
    process.removeListener('SIGINT', sigint);
    process.removeListener('SIGTERM', sigterm);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await run(parseOptions(process.argv.slice(2))); }
  catch { console.error('Browser stack preparation failed'); process.exitCode = 1; }
}
