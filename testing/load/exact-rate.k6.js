import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const spec = JSON.parse(open(__ENV.SCHEMII_LOAD_INPUT));
const sent = new Counter('schemii_sent'), completed = new Counter('schemii_completed');
const rejected = new Counter('schemii_rejected'), incomplete = new Counter('schemii_incomplete');
const failed = new Counter('schemii_failed'), admitted = new Counter('schemii_admitted');
const latency = new Trend('schemii_http_ms', true), incorrect = new Counter('schemii_incorrect');
export const options = {
  scenarios: { exact: { executor: 'constant-arrival-rate', rate: spec.rate, timeUnit: '1m',
    duration: `${spec.seconds}s`, preAllocatedVUs: spec.vus, maxVUs: spec.vus, gracefulStop: '30s' } },
  insecureSkipTLSVerify: spec.origin === 'https://localhost:8001', maxRedirects: 0,
  discardResponseBodies: false, tags: { workload: spec.workload },
  systemTags: ['status', 'method', 'scenario', 'expected_response'],
  thresholds: { dropped_iterations: ['count==0'], schemii_incorrect: [{ threshold: 'count==0', abortOnFail: true }],
    schemii_incomplete: ['count==0'], schemii_failed: ['count==0'], schemii_rejected: ['count==0'],
    schemii_http_ms: ['p(95)<=300', 'p(99)<=1000'] },
};

function get(data, path) { return path.split('.').reduce((value, key) => value?.[key], data); }
export default function () {
  const account = spec.accounts[exec.scenario.iterationInTest % spec.accounts.length];
  const request = account.request;
  sent.add(1); incomplete.add(0); failed.add(0); rejected.add(0); completed.add(0); admitted.add(0); incorrect.add(0);
  const response = http.request(request.method, spec.origin + request.path, request.body ? JSON.stringify(request.body) : null,
    { headers: { Cookie: account.cookie, 'Content-Type': 'application/json' }, timeout: '30s',
      redirects: 0, responseType: 'text', tags: { name: spec.workload } });
  latency.add(response.timings.duration);
  if (!response.status || response.error_code) { incomplete.add(1); return; }
  if ([409, 429, 502, 503].includes(response.status)) { rejected.add(1); return; }
  admitted.add(1);
  if (response.status !== request.status) { failed.add(1); return; }
  let data; try { data = response.json(); } catch { failed.add(1); incorrect.add(1); return; }
  if (Object.entries(request.equals || {}).some(([key, expected]) => JSON.stringify(get(data, key)) !== JSON.stringify(expected)) ||
      (request.nonEmpty || []).some(key => !Array.isArray(get(data, key)) || !get(data, key).length) ||
      data.error || (request.requireSQL && (typeof data.sql !== 'string' || !data.sql.includes('SELECT')))) {
    failed.add(1); incorrect.add(1); return;
  }
  completed.add(1);
}
export function handleSummary(data) {
  // Only bounded numeric metrics and workload classes leave the private k6 input.
  return { [__ENV.SCHEMII_LOAD_RESULT]: JSON.stringify({ version: 1,
    metrics: Object.fromEntries(Object.entries(data.metrics).filter(([key]) => key.startsWith('schemii_') ||
      ['http_reqs', 'iterations', 'dropped_iterations', 'vus_max', 'data_received', 'data_sent'].includes(key)).map(([key, metric]) => [key, metric.values])) }) };
}
