import { element } from './dom.js';
import { createIconButton } from './ui.js';

export function parseQueryPlan(value) {
  const decoded = typeof value === 'string' ? JSON.parse(value) : value;
  const plan = Array.isArray(decoded) ? decoded[0] : decoded;
  if (!plan?.Plan || typeof plan.Plan !== 'object') throw new Error('PostgreSQL did not return a JSON query plan.');
  return plan;
}

export function planNodeMetrics(node) {
  const metrics = [`Estimated rows: ${node['Plan Rows'] ?? 'unknown'}`, `Cost: ${node['Startup Cost'] ?? '?'}–${node['Total Cost'] ?? '?'}`];
  if (node['Actual Loops'] === 0) {
    metrics.push('Never executed');
  } else if (node['Actual Rows'] != null) {
    metrics.push(`Actual rows / loop: ${node['Actual Rows']}`, `Loops: ${node['Actual Loops']}`, `Time / loop: ${node['Actual Total Time']} ms`);
    const estimated = node['Plan Rows'];
    const actual = node['Actual Rows'];
    if (estimated > 0 && actual > 0 && Math.max(actual / estimated, estimated / actual) >= 10) metrics.push(`Row estimate differs ${Math.max(actual / estimated, estimated / actual).toFixed(1)}×`);
    else if (estimated !== actual && (estimated === 0 || actual === 0)) metrics.push('Estimated and actual rows differ (one is zero)');
  }
  for (const key of ['Rows Removed by Filter', 'Shared Hit Blocks', 'Shared Read Blocks', 'Temp Read Blocks', 'Temp Written Blocks']) {
    if (node[key] != null) metrics.push(`${key}: ${node[key]}`);
  }
  return metrics;
}

const number = value => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString('en-US', { maximumFractionDigits: 3 }) : '—';

function planTable(plan, measured, label) {
  const container = element('div', { className: 'query-plan-workbench' });
  const viewport = element('div', { className: 'query-plan-viewport', attrs: { tabindex: '0', 'aria-label': `${label} operations; scroll for more columns` } });
  const table = element('table', { className: 'query-plan-table', attrs: { 'aria-label': `${label} operations` } });
  const headings = ['Operation', 'Object', 'Cost', 'Est. rows', ...(measured ? ['Actual rows', 'Loops', 'Time (ms)'] : [])];
  table.append(element('thead', {}, [element('tr', {}, headings.map(text => element('th', { text, attrs: { scope: 'col' } })))]));
  const body = element('tbody'); table.append(body); viewport.append(table);
  const properties = element('section', { className: 'query-plan-properties', attrs: { 'aria-label': 'Operation details' } });
  const entries = [];
  function collect(node, parent = null, depth = 0) {
    const entry = { node, parent, depth, expanded: true };
    entries.push(entry);
    for (const child of node.Plans || []) collect(child, entry, depth + 1);
  }
  collect(plan.Plan);
  let selected = entries[0];
  function select(entry) {
    selected = entry;
    for (const item of entries) {
      item.row.classList.toggle('is-selected', item === entry);
      item.select.setAttribute('aria-pressed', String(item === entry));
    }
    const values = element('dl');
    for (const [key, value] of Object.entries(entry.node)) {
      if (key === 'Plans') continue;
      values.append(element('dt', { text: key }), element('dd', { text: typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value) }));
    }
    const warnings = planNodeMetrics(entry.node).filter(text => /Never executed|differ/.test(text));
    properties.replaceChildren(element('h4', { text: 'Operation details' }),
      element('p', { className: 'query-plan-selected-name', text: entry.node['Node Type'] }),
      ...warnings.map(text => element('p', { className: 'query-plan-warning', text })), values);
  }
  function updateVisibility() {
    for (const entry of entries) {
      let ancestor = entry.parent, visible = true;
      while (ancestor) { if (!ancestor.expanded) visible = false; ancestor = ancestor.parent; }
      entry.row.hidden = !visible;
      if (entry.toggle) {
        const action = entry.expanded ? 'Collapse' : 'Expand';
        entry.toggle.setAttribute('aria-expanded', String(entry.expanded));
        entry.toggle.setAttribute('aria-label', `${action} ${entry.node['Node Type']}`);
        entry.toggle.dataset.uiTooltip = `${action} ${entry.node['Node Type']}`;
        entry.toggle.classList.toggle('is-expanded', entry.expanded);
      }
    }
    if (selected.row.hidden) {
      let visible = selected.parent;
      while (visible.row.hidden) visible = visible.parent;
      select(visible);
    }
  }
  for (const entry of entries) {
    const node = entry.node;
    const row = element('tr'); entry.row = row;
    const operation = element('td', { className: 'query-plan-operation' });
    const tree = element('div', { className: 'query-plan-tree-cell' });
    tree.style.paddingInlineStart = `${entry.depth * 16}px`;
    if (node.Plans?.length) {
      entry.toggle = createIconButton({ icon: 'expand', label: `Collapse ${node['Node Type']}`, className: 'query-plan-toggle' });
      entry.toggle.onclick = () => { entry.expanded = !entry.expanded; updateVisibility(); };
      tree.append(entry.toggle);
    } else tree.append(element('span', { className: 'query-plan-leaf-space' }));
    entry.select = element('button', { type: 'button', className: 'query-plan-select', text: node['Node Type'] || 'Operation', attrs: { 'aria-label': `Select ${node['Node Type']}`, 'aria-pressed': 'false', title: [node['Node Type'], node['Join Type'], node['Parent Relationship']].filter(Boolean).join(' · ') } });
    entry.select.onclick = () => select(entry);
    tree.append(entry.select); operation.append(tree); row.append(operation);
    const object = [node['Schema'], node['Relation Name']].filter(Boolean).join('.') || node['Index Name'] || node['CTE Name'] || '—';
    row.append(element('td', { text: object, title: [object, node['Index Name']].filter(Boolean).join(' · ') }));
    const fields = ['Total Cost', 'Plan Rows', ...(measured ? ['Actual Rows', 'Actual Loops', 'Actual Total Time'] : [])];
    for (const key of fields) row.append(element('td', { className: 'query-plan-number', text: node['Actual Loops'] === 0 && ['Actual Rows', 'Actual Total Time'].includes(key) ? '—' : number(node[key]), title: key === 'Total Cost' ? `Startup cost: ${number(node['Startup Cost'])}` : key.startsWith('Actual') ? `${key}${key === 'Actual Loops' ? '' : ' per loop'}` : key }));
    row.onclick = event => { if (!event.target.closest('button')) select(entry); };
    body.append(row);
  }
  select(selected); updateVisibility();
  container.append(viewport, properties);
  return { container, expandAll(expanded) { for (const entry of entries) entry.expanded = expanded; updateVisibility(); } };
}

export function downloadArtifact(filename, value, type = 'application/json') {
  const url = URL.createObjectURL(value instanceof Blob ? value : new Blob([JSON.stringify(value, null, 2)], { type }));
  const link = element('a', { attrs: { href: url, download: filename } });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function createQueryPlanView(record, { comparison = null, onCompare = null } = {}) {
  const root = element('article', { className: 'query-plan-view' });
  const toolbar = element('div', { className: 'query-plan-toolbar' });
  toolbar.append(element('strong', { text: 'Execution plan' }));
  const exportButton = createIconButton({ icon: 'download', label: 'Export plan + SQL', className: 'compact' });
  exportButton.onclick = () => downloadArtifact('query-plan.json', record);
  const expand = createIconButton({ icon: 'fit', label: 'Expand all', className: 'compact' });
  const collapse = createIconButton({ icon: 'minimize', label: 'Collapse all', className: 'compact' });
  toolbar.append(expand, collapse, exportButton);
  if (onCompare) {
    const compare = createIconButton({ icon: 'pin', label: 'Keep for comparison', className: 'compact' });
    compare.onclick = () => onCompare(record);
    toolbar.append(compare);
  }
  const columns = element('div', { className: 'query-plan-columns' });
  const tables = [];
  for (const [label, entry] of comparison && comparison !== record ? [['Saved plan', comparison], ['Current plan', record]] : [['Current plan', record]]) {
    const column = element('section');
    const plan = entry.plan;
    const summary = element('div', { className: 'query-plan-summary' }, [element('strong', { text: `${label} · ${entry.analyze ? 'Measured' : 'Estimated'}` })]);
    if (plan['Execution Time'] != null) summary.append(element('span', { text: `Execution: ${number(plan['Execution Time'])} ms` }));
    if (plan['Planning Time'] != null) summary.append(element('span', { text: `Planning: ${number(plan['Planning Time'])} ms` }));
    const tree = planTable(plan, entry.analyze, label); tables.push(tree);
    column.append(summary, tree.container,
      element('p', { className: 'query-plan-note', text: entry.analyze ? 'Actual rows and times are per loop. Cost is a planner estimate, not milliseconds.' : 'Estimated rows and costs; this plan contains no execution measurements.' }));
    const sql = element('details', { className: 'query-plan-source' }, [element('summary', { text: 'SQL and execution settings' }),
      element('p', { text: entry.sessionContext ? 'Current PostgreSQL session · uses its transaction and session settings.' : 'Full statement · fresh read-only snapshot. Cursor fetching can use a different plan.' }),
      element('pre', { text: entry.sql }), element('pre', { text: JSON.stringify(entry.settings || {}, null, 2) })]);
    const raw = element('details', { className: 'query-plan-source' }, [element('summary', { text: 'Raw PostgreSQL JSON' }), element('pre', { text: JSON.stringify(plan, null, 2) })]);
    column.append(sql, raw); columns.append(column);
  }
  expand.onclick = () => tables.forEach(tree => tree.expandAll(true));
  collapse.onclick = () => tables.forEach(tree => tree.expandAll(false));
  root.append(toolbar, columns);
  return root;
}
