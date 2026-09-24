import { ICONS } from './ui.js';

// These scenes are read-only replicas of the current Schemoo controls. The
// guide player moves its cursor between the marked controls and adds demo-*
// classes to show what each action changes.
const icon = name => ICONS[name];
const iconButton = (name, label, target = '') => `<button class="smq-icon" type="button" title="${label}"${target ? ` data-quick-start-target="${target}"` : ''}>${icon(name)}</button>`;
const tab = (label, target = '') => `<button class="smq-tab smq-tab-${label.toLowerCase()}" type="button"${target ? ` data-quick-start-target="${target}"` : ''}>${label}</button>`;
const topbar = (name = 'Choose a model', target = 'Choose a saved model or create one') => `
  <header class="smq-topbar"><div class="smq-brand"><small>SCHEMOO / SEMANTIC MODELS</small><strong>${name}</strong></div>
  <span class="smq-top-target">${target}</span><div class="smq-top-actions">
    ${iconButton('workspaces', 'Open models', 'open-models')}
    ${iconButton('save', 'Save model', 'save-model')}
    ${iconButton('refresh', 'Reload saved model and source')}
    ${iconButton('delete', 'Delete model')}
  </div></header>`;
const toolbar = (status = 'Choose a model', runTarget = '') => `
  <footer class="smq-toolbar"><div class="smq-tools">
    ${iconButton('fit', 'Fit model')}${iconButton('zoom-in', 'Zoom in')}${iconButton('zoom-out', 'Zoom out')}
    <i></i>${iconButton('schemas', 'Edit model')}${iconButton('relationship', 'Draw connection')}${iconButton('rows', 'Explore model')}${iconButton('sql', 'Show query preview')}
  </div><span class="smq-draft-status">${status}</span>
  <button class="smq-primary smq-run" type="button"${runTarget ? ` data-quick-start-target="${runTarget}"` : ''}>${icon('run')} Run preview</button></footer>`;
const graph = (nodeTarget = '') => `<div class="smq-canvas"><span class="smq-graph-status">2 model objects · 1 connection</span>
  <div class="smq-graph-line"></div><div class="smq-node smq-node-orders"${nodeTarget ? ` data-quick-start-target="${nodeTarget}"` : ''}><b>orders</b><span>id <em>bigint</em></span><span>customer_id <em>bigint</em></span><span>status <em>text</em></span></div>
  <div class="smq-node smq-node-customers"><b>customers</b><span>id <em>bigint</em></span><span>name <em>text</em></span></div>
  <small class="smq-canvas-help">Drag background to pan · click a connection to edit</small></div>`;
const inspectorTabs = target => `<header class="smq-inspector-header"><nav>${tab('Model', target === 'model' ? 'model-tab' : '')}${tab('Filters', target === 'filters' ? 'filters-tab' : '')}${tab('Preview', target === 'preview' ? 'preview-tab' : '')}</nav>${iconButton('close', 'Close inspector')}</header>`;

const createScene = `<div class="smq-stage smq-stage-create">
  ${topbar()}<div class="smq-workbench smq-empty"><div class="smq-empty-note">Choose a saved model or create one</div><div class="smq-created-graph">${graph()}</div></div>${toolbar()}
  <div class="smq-library-shade"></div><section class="smq-library">
    <header><strong>Semantic models</strong>${iconButton('close', 'Close model library')}</header>
    <div class="smq-library-content"><div class="smq-library-hint">No saved models yet. Create one from an existing PostgreSQL connection.</div>
      <div class="smq-create-form"><h3>Create a model</h3>
        <label>Model name <span class="smq-input smq-model-name" data-quick-start-target="model-name"><i>Organization model</i><b>Sales model</b></span></label>
        <label>Connection <span class="smq-input smq-select smq-connection" data-quick-start-target="source-connection"><i>Choose connection</i><b>Warehouse · analytics · analyst</b></span></label>
        <label class="smq-schema-field">Source schema <span class="smq-input smq-select smq-schema" data-quick-start-target="source-schema"><i>Choose schema</i><b>public</b></span></label>
        <button class="smq-primary smq-create" type="button" data-quick-start-target="create-model">Create model</button>
      </div>
    </div>
  </section></div>`;

const modelScene = `<div class="smq-stage smq-stage-model">
  ${topbar('Sales model', 'Warehouse · analytics.public')}
  <div class="smq-workbench">${graph('orders-node')}
    <aside class="smq-inspector">${inspectorTabs('model')}
      <div class="smq-pane smq-pane-model"><div class="smq-model-start"><strong>Starting object</strong><label>Start from<span class="smq-input smq-select smq-model-root" data-quick-start-target="model-start"><i>customers</i><b>orders</b></span></label></div><p>Choose the starting table and relationship paths here.</p><section><strong>Connections <em>1</em></strong><small>Toggle each connection independently.</small><div class="smq-connection-row"><span class="smq-connection-toggle" data-quick-start-target="connection-toggle">✓</span><span>orders.customer_id → customers.id</span></div></section></div>
    </aside>
    <aside class="smq-table-inspector"><header><small>MODEL TABLE</small><strong>orders</strong>${iconButton('close', 'Close table inspector')}</header>
      <div><h3>Exposed columns</h3><p>Choose fields Schemer users may use.</p><label><span class="smq-check checked"></span>id <small>bigint</small></label><label><span class="smq-check checked"></span>customer_id <small>bigint</small></label><label data-quick-start-target="expose-column"><span class="smq-check smq-status-check"></span>status <small>text</small></label></div>
    </aside>
  </div>${toolbar('Draft unchanged')}</div>`;

const filterScene = `<div class="smq-stage smq-stage-filter">
  ${topbar('Sales model', 'Warehouse · analytics.public')}
  <div class="smq-workbench">${graph()}
    <aside class="smq-inspector">${inspectorTabs('filters')}
      <div class="smq-pane smq-pane-model"><p>Choose the starting table and relationship paths here.</p><section><strong>Connections · 1</strong></section></div>
      <div class="smq-pane smq-pane-filters"><p>Author reusable model rules here. Supply parameter values in Preview.</p><h3>Model filters</h3><p class="smq-no-filter">No model filters yet. Add a fixed rule or a report parameter.</p><div class="smq-filter-card"><strong>Active orders</strong><small>Required · orders.status is not null</small></div><button class="smq-primary" type="button" data-quick-start-target="add-filter">Add model filter</button></div>
    </aside>
  </div>${toolbar('All changes saved')}
  <div class="smq-filter-shade"></div><section class="smq-filter-dialog"><header><small>MODEL RULES</small><strong>Add model filter</strong><p>Configure a fixed rule or a report parameter and its source bindings.</p></header>
    <div class="smq-rule-choices"><h3>Where does the filter value come from?</h3><button type="button" data-quick-start-target="fixed-rule"><strong>Fixed rule</strong><small>Always enforce a condition, without a report input.</small></button><button type="button"><strong>Report parameter</strong><small>Ask for a date, ID, or other value.</small></button></div>
    <div class="smq-rule-editor"><label>Filter name<span class="smq-input">Active orders</span></label><h3>Source conditions</h3><label>Source · column<span class="smq-input smq-select smq-rule-source" data-quick-start-target="rule-source"><i>Choose source column</i><b>orders · status</b></span></label><label>Comparison<span class="smq-input smq-select">Is not null</span></label></div>
    <footer><span>Applies to your draft. Use Save model to keep changes.</span><button type="button">Cancel</button><button class="smq-primary smq-apply" type="button" data-quick-start-target="apply-filter">Apply to model</button></footer>
  </section><div class="smq-saved-badge">Saved model · filter ready for preview</div></div>`;

const previewScene = `<div class="smq-stage smq-stage-preview">
  ${topbar('Sales model', 'Warehouse · analytics.public')}
  <div class="smq-workbench smq-preview-workbench">${graph()}
    <aside class="smq-inspector">${inspectorTabs('preview')}
      <div class="smq-pane smq-pane-model"><p>Choose the starting table and relationship paths here.</p><section><strong>Connections · 1</strong></section></div>
      <div class="smq-pane smq-pane-preview"><div class="smq-preview-context"><strong>Working preview</strong><small>Query choices only</small></div><span class="smq-input smq-select smq-preview-root">orders</span>
        <h3>Preview outputs <em class="smq-output-count">0</em></h3><p>Test exposed fields and measures without changing model exposure.</p>
        <label>Add by table<span class="smq-add-by-table"><span class="smq-input smq-select">orders · 3 exposed columns</span><button class="smq-icon smq-output-add" type="button" title="Add table columns to preview" data-quick-start-target="add-outputs">${icon('add')}</button></span></label>
        <div class="smq-output-list"><div>id <small>Plain field</small></div><div>customer_id <small>Plain field</small></div><div>status <small>Plain field</small></div></div>
      </div>
    </aside>
  </div><section class="smq-query-dock"><header><button class="smq-sql-tab">Generated SQL</button><button class="smq-results-tab" data-quick-start-target="results-tab">Results</button><small class="smq-result-status">Nothing run yet</small></header>
    <pre class="smq-sql">SELECT orders.id, orders.customer_id, orders.status\nFROM public.orders;</pre>
    <div class="smq-results"><div><b>id</b><b>customer_id</b><b>status</b></div><div><span>101</span><span>19</span><span>open</span></div><div><span>102</span><span>27</span><span>shipped</span></div></div>
  </section>${toolbar('All changes saved', 'run-preview')}</div>`;

export const guide = {
  name: 'Schemoo',
  steps: [
    {
      title: 'Create a semantic model',
      text: 'Open the model library from the folder button in the top bar. Enter a model name, choose an existing PostgreSQL connection, choose its source schema, then create the model. Schemoo imports its visible tables and relationships.',
      tip: 'Add the connection in Schemii first if the list is empty.',
      scene: createScene,
      states: ['library', 'named', 'connection', 'schema', 'created'],
      actions: [
        { target: 'open-models', caption: 'Open the model library', state: 'library' },
        { target: 'model-name', caption: 'Name the model', state: 'named' },
        { target: 'source-connection', caption: 'Choose the source connection', state: 'connection' },
        { target: 'source-schema', caption: 'Choose the source schema', state: 'schema' },
        { target: 'create-model', caption: 'Create the model', state: 'created' },
      ],
      idleText: 'Watch a model get created from a saved connection.',
      staticText: 'Open models → name → connection → schema → Create model.',
      completeText: 'The imported model opens on the canvas.',
    },
    {
      title: 'Set the starting object and usable paths',
      text: 'The model opens on the Model tab. Choose Start from under Starting object, then toggle connection paths. Select a table on the canvas to choose which columns Schemer users can use. Preview outputs are separate choices.',
      tip: 'Disabled connections appear dashed on the canvas; cycles are highlighted red.',
      scene: modelScene,
      states: ['starting', 'path', 'table', 'exposed'],
      actions: [
        { target: 'model-start', caption: 'Choose the model starting object on the Model tab', state: 'starting' },
        { target: 'connection-toggle', caption: 'Disable an unneeded connection path from the Model tab', state: 'path' },
        { target: 'orders-node', caption: 'Select a table on the canvas', state: 'table' },
        { target: 'expose-column', caption: 'Expose a column to Schemer', state: 'exposed' },
      ],
      idleText: 'Watch the model inspector and table column controls.',
      staticText: 'Model tab → Start from → connection checkbox → exposed columns.',
      completeText: 'The status column is exposed in the model draft.',
    },
    {
      title: 'Add a filter and save the model',
      text: 'Use Filters to add a reusable rule. Choose a fixed rule or report parameter, bind it to a source column, and apply it to the draft. Then use the Save model button in the top bar. Preview execution uses the saved model.',
      tip: 'Apply to model changes the draft. Save model persists it before Run preview.',
      scene: filterScene,
      states: ['filters', 'dialog', 'fixed', 'configured', 'applied', 'saved'],
      actions: [
        { target: 'filters-tab', caption: 'Open Filters', state: 'filters' },
        { target: 'add-filter', caption: 'Add model filter', state: 'dialog' },
        { target: 'fixed-rule', caption: 'Choose a fixed rule', state: 'fixed' },
        { target: 'rule-source', caption: 'Bind orders.status', state: 'configured' },
        { target: 'apply-filter', caption: 'Apply the rule to the draft', state: 'applied' },
        { target: 'save-model', caption: 'Save the model before running', state: 'saved' },
      ],
      idleText: 'Watch a filter move from dialog to saved model.',
      staticText: 'Filters → Add model filter → Fixed rule → Apply to model → Save model.',
      completeText: 'The filter is saved and ready for preview.',
    },
    {
      title: 'Build and run a preview',
      text: 'Open Preview to build the working query from the model’s starting object. Use Add by table to include exposed columns; Generated SQL updates as you choose outputs. Run preview executes the saved model, then Results shows its rows.',
      tip: 'Preview outputs affect the test query without changing exposed model columns.',
      scene: previewScene,
      states: ['preview', 'outputs', 'run', 'results'],
      actions: [
        { target: 'preview-tab', caption: 'Open Preview', state: 'preview' },
        { target: 'add-outputs', caption: 'Add exposed columns to the preview', state: 'outputs' },
        { target: 'run-preview', caption: 'Run the saved model', state: 'run' },
        { target: 'results-tab', caption: 'Inspect the Results tab', state: 'results' },
      ],
      idleText: 'Watch a saved model produce a read-only preview.',
      staticText: 'Preview → Add by table → Run preview → Results.',
      completeText: 'Preview rows are available in Results.',
    },
  ],
};

const labels = [
  ['Open models', 'Model name', 'Source connection', 'Source schema', 'Create model'],
  ['Start from', 'Connection checkbox', 'orders', 'Expose status'],
  ['Filters', 'Add model filter', 'Fixed rule', 'Source column', 'Apply to model', 'Save model'],
  ['Preview', 'Add table columns', 'Run preview', 'Results'],
];
guide.steps.forEach((step, stepIndex) => step.actions.forEach((action, actionIndex) => {
  action.label = labels[stepIndex][actionIndex];
}));
