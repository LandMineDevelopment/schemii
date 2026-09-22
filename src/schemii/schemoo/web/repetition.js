import { element } from '#common/dom.js';

export function repetitionDiagnostics(plan) {
  return (plan?.repetitionDiagnostics || []).filter(item => item.code === 'measure_repetition');
}

/** A user-requested starting point, never a replacement for the selected measure. */
export function summarySeed(draft, measure) {
  const source = draft.nodes.find(node => node.id === measure.table);
  if (!source || source.derivation || !['sum', 'count', 'avg'].includes(measure.aggregate)) return null;
  return {
    label: `${source.label} summary`.slice(0, 100),
    derivation: { kind: 'aggregate', source: source.id, groupBy: [],
      connection: { target: source.id, columns: [] },
      outputs: [{ id: `field_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
        label: `${measure.aggregate} ${measure.column}`.slice(0, 100), operation: measure.aggregate,
        nodeId: source.id, column: measure.column, distinct: false, delimiter: ', ', conditions: [] }] },
  };
}

export function relatedSummaries(draft, measure) {
  const node = draft.nodes.find(item => item.id === measure.table);
  const source = node?.derivation?.source || node?.id;
  return draft.nodes.filter(item => item.derivation?.kind === 'aggregate'
    && (item.id === measure.table || item.derivation.source === source));
}

export function openRepetitionInspector(plan, { draft, onCreateSummary, onEditSummary, modelId } = {}) {
  const dialog = element('dialog', { className: 'ui-dialog repetition-dialog', attrs: { 'aria-label': 'Inspect repetition' } });
  const body = element('div', { className: 'repetition-body' });
  body.append(element('p', { text: 'These joins can repeat the records used by a measure. This is based on model relationships, not a count of duplicates in your data. Repetition may be intentional; the query runs as configured.' }));
  const action = (text, callback) => {
    const button = element('button', { type: 'button', className: 'ui-button', text });
    button.onclick = () => { dialog.close(); callback(); };
    return button;
  };
  for (const diagnostic of repetitionDiagnostics(plan)) {
    const section = element('section', { className: 'repetition-measure' }, [element('h3', { text: diagnostic.measure.label })]);
    const paths = element('ul');
    for (const relationship of diagnostic.relationships) {
      paths.append(element('li', {}, [
        element('strong', { text: relationship.path.map(item => item.label).join(' → ') }),
        element('p', { text: `${relationship.source.label}.${relationship.source.column} → ${relationship.target.label}.${relationship.target.column}` }),
      ]));
    }
    section.append(paths, element('p', { text: diagnostic.measure.aggregate === 'avg'
      ? 'Records with more matches can contribute more often to this average.'
      : 'A contributing record can be counted or added more than once.' }));
    if (draft) {
      const tools = element('div', { className: 'repetition-actions' });
      const source = draft.nodes.find(node => node.id === diagnostic.measure.table);
      if (onCreateSummary && source && !source.derivation) tools.append(action('Create grouped summary', () => onCreateSummary(diagnostic.measure)));
      for (const summary of relatedSummaries(draft, diagnostic.measure)) {
        if (onEditSummary) tools.append(action(`Edit ${summary.label}`, () => onEditSummary(summary)));
      }
      section.append(tools);
      if (source?.derivation?.kind === 'row') section.append(element('p', { text: 'This measure uses a row calculation. Inspect its source and choose the intended grouping in the model; no summary is generated automatically.' }));
    }
    body.append(section);
  }
  body.append(element('p', { text: 'If you want different behavior, model a grouped summary at the intended record level. Choose its grouping and connection explicitly, then select the desired output and check the preview. Later joins can repeat summaries too. Averaging group averages changes weighting; DISTINCT counts values, not necessarily records.' }));
  if (modelId) body.append(element('a', { className: 'ui-button', text: 'Open model in Schemoo', attrs: {
    href: `/schemoo?model=${encodeURIComponent(modelId)}`, target: '_blank', rel: 'noopener',
  } }));
  body.append(element('p', { className: 'hint', text: 'No changes are required. Optional modeling tools change your draft only when you apply them; save and update dashboards through the usual workflow.' }));
  const close = element('button', { type: 'button', className: 'ui-button', text: 'Close' });
  close.onclick = () => dialog.close();
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Inspect repetition' })]), body,
    element('footer', { className: 'ui-dialog__actions' }, [close]));
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  document.body.append(dialog); dialog.showModal(); close.focus();
  return dialog;
}

export function repetitionNotice(plan, options = {}) {
  const diagnostics = repetitionDiagnostics(plan);
  if (!diagnostics.length) return null;
  const notice = element('details', { className: 'aggregation-warning' }, [
    element('summary', { text: 'Aggregation warning' }),
    element('p', { text: `${diagnostics.length} measure${diagnostics.length === 1 ? '' : 's'} may use repeated records. Repetition can be intentional; the query runs as configured.` }),
  ]);
  const inspect = element('button', { type: 'button', className: 'ui-button', text: 'Inspect repetition' });
  inspect.onclick = () => openRepetitionInspector(plan, options);
  notice.append(inspect);
  notice.onclick = event => event.stopPropagation();
  notice.onkeydown = event => event.stopPropagation();
  return notice;
}
