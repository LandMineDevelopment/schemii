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
const toolbar = (status = 'Choose a model', runTarget = '', queryTarget = '') => `
  <footer class="smq-toolbar"><div class="smq-tools">
    ${iconButton('fit', 'Fit model')}${iconButton('zoom-in', 'Zoom in')}${iconButton('zoom-out', 'Zoom out')}
    <i></i>${iconButton('schemas', 'Edit model')}${iconButton('relationship', 'Draw connection')}${iconButton('rows', 'Explore model')}${iconButton('sql', 'Show query preview', queryTarget)}
  </div><span class="smq-draft-status">${status}</span>
  <button class="smq-primary smq-run" type="button"${runTarget ? ` data-quick-start-target="${runTarget}"` : ''}>${icon('run')} Run preview</button></footer>`;
const graph = (nodeTarget = '') => `<div class="smq-canvas"><span class="smq-graph-status">2 model objects · 1 connection</span>
  <div class="smq-graph-line"></div><div class="smq-node smq-node-orders"${nodeTarget ? ` data-quick-start-target="${nodeTarget}"` : ''}><b>orders</b><span>id <em>bigint</em></span><span>customer_id <em>bigint</em></span><span>status <em>text</em></span></div>
  <div class="smq-node smq-node-customers"><b>customers</b><span>id <em>bigint</em></span><span>full_name <em>text</em></span></div>
  <small class="smq-canvas-help">Drag background to pan · click a connection to edit</small></div>`;
const inspectorTabs = target => `<header class="smq-inspector-header"><nav>${tab('Model', target === 'model' ? 'model-tab' : '')}${tab('Filters', target === 'filters' ? 'filters-tab' : '')}${tab('Preview', target === 'preview' ? 'preview-tab' : '')}</nav>${iconButton('close', 'Close inspector')}</header>`;

const createScene = `<div class="smq-stage smq-stage-create">
  ${topbar()}<div class="smq-workbench smq-empty"><div class="smq-empty-note">Choose a saved model or create one</div><div class="smq-created-graph">${graph()}</div></div>${toolbar()}
  <div class="smq-library-shade"></div><section class="smq-library">
    <header><strong>Semantic models</strong>${iconButton('close', 'Close model library')}</header>
    <div class="smq-library-content"><div class="smq-library-hint">No saved models yet. Create one from an existing PostgreSQL connection.</div>
      <div class="smq-create-form"><h3>Create a model</h3>
        <label>Model name <span class="smq-input smq-model-name" data-quick-start-target="model-name"><i>Organization model</i><b>Orders model</b></span></label>
        <label>Connection <span class="smq-input smq-select smq-connection" data-quick-start-target="source-connection"><i>Choose connection</i><b>Bookstore DB · schemii_test</b></span></label>
        <label class="smq-schema-field">Source schema <span class="smq-input smq-select smq-schema" data-quick-start-target="source-schema"><i>Choose schema</i><b>bookstore</b></span></label>
        <button class="smq-primary smq-create" type="button" data-quick-start-target="create-model">Create model</button>
      </div>
    </div>
  </section></div>`;

const modelScene = `<div class="smq-stage smq-stage-model">
  ${topbar('Orders model', 'Bookstore DB · schemii_test.bookstore')}
  <div class="smq-workbench">${graph('orders-node')}
    <aside class="smq-inspector">${inspectorTabs('model')}
      <div class="smq-pane smq-pane-model"><div class="smq-model-start"><strong>Starting object</strong><label>Start from<span class="smq-input smq-select smq-model-root" data-quick-start-target="model-start"><i>customers</i><b>orders</b></span></label></div><p>Choose the starting table and relationship paths here.</p><section><strong>Connections <em>1</em></strong><small>Toggle each connection independently.</small><div class="smq-connection-row"><span class="smq-connection-toggle" data-quick-start-target="connection-toggle">✓</span><span>orders.customer_id → customers.id</span></div></section></div>
    </aside>
    <aside class="smq-table-inspector"><header><small>MODEL TABLE</small><strong>orders</strong>${iconButton('close', 'Close table inspector')}</header>
      <div><h3>Exposed columns</h3><p>Imported fields start exposed. Uncheck fields Schemer users do not need.</p><label><span class="smq-check checked"></span>id <small>bigint</small></label><label data-quick-start-target="expose-column"><span class="smq-check checked smq-customer-check"></span>customer_id <small>bigint</small></label><label><span class="smq-check checked"></span>status <small>text</small></label></div>
    </aside>
  </div>${toolbar('Draft unchanged')}</div>`;

const filterScene = `<div class="smq-stage smq-stage-filter">
  ${topbar('Orders model', 'Bookstore DB · schemii_test.bookstore')}
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
  ${topbar('Orders model', 'Bookstore DB · schemii_test.bookstore')}
  <div class="smq-workbench smq-preview-workbench">${graph()}
    <aside class="smq-inspector">${inspectorTabs('preview')}
      <div class="smq-pane smq-pane-model"><p>Choose the starting table and relationship paths here.</p><section><strong>Connections · 1</strong></section></div>
      <div class="smq-pane smq-pane-preview"><div class="smq-preview-scroll"><div class="smq-preview-context"><strong>Working preview</strong><small>Query choices only</small></div><span class="smq-input smq-select smq-preview-root">orders</span>
        <h3>Preview outputs <em class="smq-output-count">1</em></h3><p>Imported previews keep their initial output until you remove it.</p>
        <label>Add by table<span class="smq-add-by-table"><span class="smq-input smq-select">orders · 4 exposed columns</span><button class="smq-icon smq-output-add" type="button" title="Add table columns to preview" data-quick-start-target="add-outputs">${icon('add')}</button></span></label>
        <div class="smq-output-list"><div>authors · name <small>Plain field</small></div><div class="smq-new-output">orders · id <small>Plain field</small></div><div class="smq-new-output">orders · status <small>Plain field</small></div><div class="smq-new-output">orders · ordered_at <small>Plain field</small></div><div class="smq-new-output">orders · shipped_at <small>Plain field</small></div></div></div>
      </div>
    </aside>
  </div><section class="smq-query-dock"><header><button class="smq-sql-tab">Generated SQL</button><button class="smq-results-tab" data-quick-start-target="results-tab">Results</button><small class="smq-result-status">Nothing run yet</small></header>
    <pre class="smq-sql"><span class="smq-sql-before">SELECT authors.name\nFROM bookstore.orders JOIN ...</span><span class="smq-sql-after">SELECT authors.name, orders.id, orders.status,\n       orders.ordered_at, orders.shipped_at ...</span></pre>
    <div class="smq-results"><div><b>name</b><b>id</b><b>status</b><b>ordered_at</b><b>shipped_at</b></div><div><span>Maya Chen</span><span>1</span><span>shipped</span><span>Jun 2</span><span>Jun 3</span></div><div><span>Priya Raman</span><span>1</span><span>shipped</span><span>Jun 2</span><span>Jun 3</span></div></div>
  </section>${toolbar('All changes saved', 'run-preview', 'show-query')}</div>`;

export const guide = {
  name: 'Schemoo',
  steps: [
    {
      title: 'Create a semantic model',
      text: 'Open the model library from the folder button in the top bar. Enter a model name, choose an existing PostgreSQL connection, choose its source schema, then create the model. Schemoo imports its visible tables and relationships.',
      tip: 'Use the Bookstore DB connection and bookstore schema from the Schemii guide, or your own PostgreSQL source.',
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
      text: 'The model opens on the Model tab. Choose Start from under Starting object. Disable paths you do not need; keep the path to any table you want in Preview. Select a table to control which columns Schemer users can use.',
      tip: 'Imported columns start exposed. Disabled connections appear dashed; cycles are highlighted red.',
      scene: modelScene,
      states: ['starting', 'path-disabled', 'path-restored', 'table', 'restricted'],
      actions: [
        { target: 'model-start', caption: 'Choose the model starting object on the Model tab', state: 'starting' },
        { target: 'connection-toggle', caption: 'Disable a connection path to see its effect', state: 'path-disabled' },
        { target: 'connection-toggle', caption: 'Restore this path because the preview uses customers', state: 'path-restored' },
        { target: 'orders-node', caption: 'Select a table on the canvas', state: 'table' },
        { target: 'expose-column', caption: 'Hide customer_id from Schemer while keeping status exposed', state: 'restricted' },
      ],
      idleText: 'Watch the model inspector and table column controls.',
      staticText: 'Model tab → Start from → connection checkbox → hide an unneeded exposed column.',
      completeText: 'customer_id is hidden; status stays available for filtering.',
    },
    {
      title: 'Add a filter and save the model',
      text: 'Use Filters to add a reusable rule. Choose a fixed rule or report parameter, bind it to a source column, and apply it to the draft. Then use the Save model button in the top bar. Preview execution uses the saved model.',
      tip: 'Save this fixed rule before preview. To use the next guide’s shared filter, also add an optional Order status report parameter bound to orders.status and save again.',
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
      text: 'Open Preview to build the working query from the model’s starting object. The imported preview may already contain an output. Open Show query preview, then scroll to Add by table; Generated SQL updates as you choose outputs. Run preview executes the saved model and opens Results.',
      tip: 'Remove the imported output if you do not need it. Preview choices do not change exposed model columns.',
      scene: previewScene,
      states: ['preview', 'query', 'outputs', 'run', 'results'],
      actions: [
        { target: 'preview-tab', caption: 'Open Preview', state: 'preview' },
        { target: 'show-query', caption: 'Open Show query preview to see Generated SQL', state: 'query' },
        { target: 'add-outputs', caption: 'Add exposed columns to the preview', state: 'outputs' },
        { target: 'run-preview', caption: 'Run the saved model', state: 'run' },
        { target: 'results-tab', caption: 'Inspect the Results tab', state: 'results' },
      ],
      idleText: 'Watch a saved model produce a read-only preview.',
      staticText: 'Preview → Show query preview → Add by table → Run preview → Results.',
      completeText: 'Preview rows are available in Results.',
    },
  ],
};

const labels = [
  ['Open models', 'Model name', 'Source connection', 'Source schema', 'Create model'],
  ['Start from', 'Connection checkbox', 'Connection checkbox', 'orders', 'Hide customer_id'],
  ['Filters', 'Add model filter', 'Fixed rule', 'Source column', 'Apply to model', 'Save model'],
  ['Preview', 'Show query preview', 'Add table columns', 'Run preview', 'Results'],
];
guide.steps.forEach((step, stepIndex) => step.actions.forEach((action, actionIndex) => {
  action.label = labels[stepIndex][actionIndex];
}));
