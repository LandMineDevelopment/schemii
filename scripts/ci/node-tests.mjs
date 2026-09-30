// One discovery path for local feedback and CI; instrumentation changes reporters,
// never the selected tests. These deterministic families start no application.
import { mkdirSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const families = ['tests/frontend', 'testing/harness', 'scripts/ci', 'testing/load'];
const files = families.flatMap(family => {
  let entries;
  try {
    entries = readdirSync(new URL(`${family}/`, new URL('../../', import.meta.url)), { withFileTypes: true });
  } catch (error) {
    // The load family is collected as soon as its focused implementation lands.
    if (family === 'testing/load' && error.code === 'ENOENT') return [];
    throw error;
  }
  return entries.filter(entry => entry.isFile() && /\.test\.(js|mjs)$/.test(entry.name))
    .map(entry => `${family}/${entry.name}`).sort();
});
if (!files.length) throw new Error('No deterministic Node tests discovered');

const args = ['--test'];
const output = process.env.CI_TELEMETRY_FILE;
if (output) {
  const destination = resolve(root, output);
  mkdirSync(dirname(destination), { recursive: true });
  args.push('--test-reporter=spec', '--test-reporter=./scripts/ci/node-reporter.mjs',
    '--test-reporter-destination=stdout', `--test-reporter-destination=${destination}`);
}
const child = spawn(process.execPath, [...args, ...files], { cwd: root, stdio: 'inherit' });
const forwarders = new Map(['SIGINT', 'SIGTERM'].map(signal => [signal, () => child.kill(signal)]));
for (const [signal, forward] of forwarders) process.on(signal, forward);
child.on('error', error => { throw error; });
child.on('exit', (status, signal) => {
  for (const [value, forward] of forwarders) process.removeListener(value, forward);
  if (signal) process.kill(process.pid, signal);
  else process.exitCode = status ?? 1;
});
