import { element, formatTimestamp, replace } from '#common/dom.js';

const renderedWorkspaces = new WeakMap();
export const compatibilityObjectLabel = identity => `${identity.namespace ? `${JSON.stringify(identity.namespace)}.` : ''}${JSON.stringify(identity.name)}${identity.signature ? ` (${identity.signature})` : ''}`;

export function importCompatibility(workspace) {
  const summary = workspace?.importSummary;
  if (!summary) return null;
  const issues = summary.issues || [];
  const unknown = [...new Set(issues.flatMap(issue => issue.unknownReasons || []))];
  if (summary.safetyEvidenceVersion == null) unknown.unshift('This historical import has no versioned preservation evidence.');
  if (issues.some(issue => !issue.dependencyComplete)) unknown.push('At least one protected object has an incomplete recorded dependency boundary.');
  return { complete: summary.complete === true, capturedAt: summary.catalogCapturedAt, version: summary.safetyEvidenceVersion,
    issues, unknown, guidance: summary.migrationGuidance || 'Import coverage does not establish operation-specific migration safety.' };
}

export function materializedAuthoringGuidance(existing = false) {
  return `${existing ? 'Changing an existing materialized view can require replacement and can be blocked by dependencies, ownership, indexes or privileges.' : 'A materialized view stores cached query results and needs explicit maintenance.'} Saving this editor changes the desired design only. Populate on create applies when the view is created; it does not refresh an existing cache. To refresh unchanged SQL, open Review migration and select a one-time materialized-view refresh. Review the lock and cached-data consequences before Apply.`;
}

export function renderWorkspaceCompatibility(host, { workspace, design, loading = false, reviewDisabled = false, exportDisabled = false, onReview, onExport }) {
  const summary = workspace?.importSummary;
  const available = Boolean(design) && !loading;
  const actionState = `${available}:${reviewDisabled}:${exportDisabled}`;
  const previous = renderedWorkspaces.get(host);
  if (previous?.summary === summary && previous?.workspaceId === workspace?.id && previous?.actionState === actionState) return;
  renderedWorkspaces.set(host, { summary, workspaceId: workspace?.id, actionState });
  replace(host); host.hidden = !summary;
  if (!summary) return;
  const value = importCompatibility(workspace);
  host.classList.toggle('partial', !value.complete);
  const heading = element('div', { className: 'compatibility-heading' }, [
    element('strong', { text: value.complete ? 'Editable import complete · migration safety still needs review' : 'This workspace has protected PostgreSQL features' }),
    element('span', { text: `Import snapshot: ${formatTimestamp(value.capturedAt)}` }),
  ]);
  const actions = element('div', { className: 'compatibility-actions' });
  for (const [label, callback, disabled] of [['Review migration safety', onReview, reviewDisabled], ['Export editable schema SQL', onExport, exportDisabled]]) {
    const button = element('button', { type: 'button', className: 'ui-button compact', text: label });
    button.disabled = !available || disabled; button.addEventListener('click', callback); actions.append(button);
  }
  host.append(heading, element('p', { text: value.complete
    ? 'Import coverage is historical. A fresh migration review decides which operations are safe against the current target.'
    : 'Supported objects can be edited, but omitted features are not editable or reconstructed by Schemii. A fresh plan must prove preservation before changes outside their protected dependency boundary can apply.' }), actions);
  if (!value.complete || value.issues.length || value.unknown.length) {
    const details = element('details', { className: 'compatibility-import-details' });
    details.append(element('summary', { text: `${value.issues.length} protected feature${value.issues.length === 1 ? '' : 's'} · known limits and recovery` }),
      element('p', { text: value.guidance }),
      element('p', { text: 'Keep the original PostgreSQL schema as the source of truth. Exported desired SQL contains the editable design only; resetting that design to its baseline cannot restore omitted objects. Unsupported operations remain blocked until the server can prove a safe preservation boundary.' }));
    for (const reason of value.unknown) details.append(element('p', { className: 'compatibility-unknown', text: reason }));
    const search = element('input', { type: 'search', attrs: { 'aria-label': 'Find protected PostgreSQL features', placeholder: 'Find an object or limitation' } });
    const list = element('div', { className: 'compatibility-object-list' });
    const more = element('button', { type: 'button', className: 'ui-button compact', text: 'Show more protected features' });
    let count = 30;
    function draw() {
      replace(list);
      const needle = search.value.trim().toLocaleLowerCase();
      const matches = value.issues.filter(issue => `${issue.objectName} ${issue.reason} ${issue.featureCode}`.toLocaleLowerCase().includes(needle));
      for (const issue of matches.slice(0, count)) {
        const row = element('article', {}, [element('strong', { text: issue.objectName }), element('p', { text: issue.reason })]);
        const scope = issue.protectedScope || [];
        if (scope.length) row.append(element('p', { text: `Recorded protected scope: ${scope.map(compatibilityObjectLabel).join(', ')}` }));
        if (!issue.dependencyComplete) row.append(element('p', { text: 'Recorded dependency coverage is incomplete; operation safety is not established.' }));
        list.append(row);
      }
      if (!matches.length) list.append(element('p', { text: 'No matching protected features.' }));
      more.hidden = matches.length <= count;
    }
    search.addEventListener('input', () => { count = 30; draw(); });
    more.addEventListener('click', () => { count += 30; draw(); });
    details.append(search, list, more); draw(); host.append(details);
  }
}

export function renderCompatibilityEvidence(evidence) {
  const section = element('section', { className: 'migration-section compatibility-evidence', attrs: { 'aria-label': 'Current migration compatibility evidence' } });
  section.append(element('header', {}, [element('h3', { text: 'Current operation safety' })]));
  if (!evidence) {
    section.append(element('p', { text: 'This response did not report structured compatibility evidence. Import coverage alone does not establish preservation. Apply remains governed by the server review.' }));
    return section;
  }
  section.append(element('strong', { text: evidence.operationSafe ? 'Server reports this operation can preserve protected objects' : 'Server could not prove this operation safe' }),
    element('p', { text: `Editable import: ${evidence.editableComplete ? 'complete' : 'partial'}. Preservation evidence: ${evidence.evidenceVersion == null ? 'version not reported' : `v${evidence.evidenceVersion}`}. This assessment belongs to this immutable plan.` }));
  for (const [title, values] of [['Blocking reasons', evidence.blockingReasons], ['Unknowns', evidence.unknownReasons]]) {
    if (!values?.length) continue;
    section.append(element('h4', { text: title }), ...values.map(text => element('p', { className: 'migration-message', text })));
  }
  for (const [title, identities] of [['Affected objects', evidence.impact], ['Protected dependency scope', evidence.protectedScope]]) {
    const details = element('details');
    details.append(element('summary', { text: `${title} · ${identities?.length || 0}` }));
    for (const identity of identities || []) details.append(element('code', { text: compatibilityObjectLabel(identity) }));
    section.append(details);
  }
  return section;
}
