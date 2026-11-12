import { createHash, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';

// Coverage is a bounded declaration of literal cases and attempts, never a note
// or a reusable permission to attach an old image to another check.
export const maximumCoverage = 4;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const clone = value => structuredClone(value);
const text = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(value);
export function coverageIDs(input, primary) {
  if (!Array.isArray(input) || input.length < 2 || input.length > maximumCoverage
      || input.some(id => !text(id)) || new Set(input).size !== input.length || input[0] !== primary)
    throw new Error(`Shared coverage needs 2–${maximumCoverage} distinct literal assigned case IDs, with the primary case first.`);
  return input;
}
export function activityState(value) {
  if (!value || value.schema !== 1 || !Number.isSafeInteger(value.epoch) || value.epoch < 0
      || !Number.isSafeInteger(value.pending) || value.pending < 0 || value.pending > 256)
    throw new Error('Shared coverage requires activity-aware browser metadata; use a fresh native provider session.');
  if (value.pending) throw new Error('Shared coverage cannot use browser state while an action is in flight.');
  return { schema: 1, epoch: value.epoch, pending: 0 };
}
export function declareCoverage(contexts, { lane, session, activity, at }) {
  const first = contexts[0];
  coverageIDs(contexts.map(item => item.scenario), first.scenario);
  const context = item => Object.fromEntries(['generation','agent','source','product','expectedState','assignmentURL','requestedURL','url','viewport','resources','phase'].map(key => [key, item[key]]));
  if (contexts.some(item => !isDeepStrictEqual(context(item), context(first))))
    throw new Error('Shared cases must have exactly the same product, resource, route, viewport and expected state.');
  if (first.expectedState !== 'product') throw new Error('Denied-state checks require separate captures.');
  const declaration = { schema: 1, id: randomUUID(), lane, session, activity: activityState(activity), ...clone(context(first)),
    members: contexts.map(item => ({ scenario: item.scenario, attempt: item.id })), declaredAt: at };
  return { declaration, sha256: digest(declaration) };
}
export function validCoverage(coverage) {
  try {
    const d = coverage.declaration;
    coverageIDs(d.members.map(item => item.scenario), d.members[0].scenario);
    return d.schema === 1 && text(d.id) && text(d.lane) && text(d.session)
      && d.phase === 'scenario' && d.expectedState === 'product'
      && d.members.every(item => Object.keys(item).sort().join() === 'attempt,scenario' && text(item.attempt))
      && new Set(d.members.map(item => item.attempt)).size === d.members.length
      && activityState(d.activity).epoch === d.activity.epoch && coverage.sha256 === digest(d);
  } catch { return false; }
}
export function assertCoverageActivity(coverage, session, activity) {
  if (!validCoverage(coverage)) throw new Error('Shared coverage declaration changed or is incomplete.');
  if (coverage.declaration.session !== session || !isDeepStrictEqual(coverage.declaration.activity, activityState(activity)))
    throw new Error('Browser activity or session changed after shared coverage declaration; begin a new group and capture.');
}
export function captureSupports(capture, scenario, attempt, lane) {
  if (!capture.coverage) return capture.scenario === scenario && capture.scenarioAttempt === attempt;
  if (!validCoverage(capture.coverage)) return false;
  const d = capture.coverage.declaration;
  return (!lane || d.lane === lane) && d.members.some(member => member.scenario === scenario && member.attempt === attempt)
    && d.members[0].scenario === capture.scenario && d.members[0].attempt === capture.scenarioAttempt
    && ['source','generation','agent','product','expectedState','url','viewport','resources','phase'].every(key => isDeepStrictEqual(d[key], capture[key]))
    && d.session === (capture.browserSession || capture.nativeSession)
    && (!capture.nativeSession || d.session === capture.nativeSession);
}
export function inspectionCoverage(capture, ids) {
  if (!capture.coverage) {
    if (ids !== undefined) throw new Error('Single-case historical images cannot acquire shared coverage after capture.');
    return {};
  }
  const d = capture.coverage.declaration;
  if (!validCoverage(capture.coverage) || !Array.isArray(ids) || !ids.length || ids.length > maximumCoverage
      || ids.some(id => !text(id)) || new Set(ids).size !== ids.length || ids.some(id => !d.members.some(member => member.scenario === id)))
    throw new Error('Image inspection must explicitly name distinct cases from this image declared coverage.');
  return { coverageSha256: capture.coverage.sha256, coverage: ids.map(id => clone(d.members.find(member => member.scenario === id))) };
}
export function inspectionSupports(capture, inspection, scenario, attempt) {
  if (inspection.sha256 !== capture.sha256 || !captureSupports(capture, scenario, attempt)) return false;
  if (!capture.coverage) return !inspection.coverage && !inspection.coverageSha256;
  return inspection.coverageSha256 === capture.coverage.sha256 && inspection.coverage?.some(member => member.scenario === scenario && member.attempt === attempt);
}
