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
  const selected = entries.filter(entry => entry.isFile() && /\.test\.(js|mjs)$/.test(entry.name))
    .map(entry => `${family}/${entry.name}`).sort();
  if (!selected.length) throw new Error(`No deterministic Node tests discovered in ${family}`);
  return selected;
});

// Keep npm's argument array intact and before the files, where Node parses test
// options. In particular, a focused name filter must not silently run failures
// outside its requested selection, and unknown options must retain Node's error.
const requested = process.argv.slice(2);
const args = ['--test', ...requested];
const output = process.env.CI_TELEMETRY_FILE;
if (output) {
  const destination = resolve(root, output);
  mkdirSync(dirname(destination), { recursive: true });
  const count = option => requested.filter(arg => arg === option || arg.startsWith(`${option}=`)).length;
  const reporters = count('--test-reporter');
  const destinations = count('--test-reporter-destination');
  if (!reporters) args.push('--test-reporter=spec');
  // Node defaults a single human reporter to stdout. Make only that implicit
  // destination explicit when appending telemetry; preserve mismatched reporter
  // and destination counts so invalid requests still fail in the native runner.
  if (!destinations && reporters <= 1) args.push('--test-reporter-destination=stdout');
  args.push('--test-reporter=./scripts/ci/node-reporter.mjs',
    `--test-reporter-destination=${destination}`);
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
