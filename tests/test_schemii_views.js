const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const diagnostics = fs.readFileSync("src/schemii/shared_web/error-diagnostics.js", "utf8");

assert.doesNotMatch(source, /prototypeRelationCatalog|source_table|provenance: \{ availability: "available"/, "Views must not retain synthetic catalog or provenance data");
assert.match(source, /function activeViewsBinding[\s\S]*record\?\.schema\?\.postgres[\s\S]*sourceProfileId[\s\S]*record\.revision[\s\S]*record\.layoutToken/, "Views must bind to the active saved schema target and concurrency fields");
const viewsBindingFunctions = source.match(/function viewsBindingKey[\s\S]*?(?=function requireExactTarget)/)?.[0] ?? "";
const viewsBindingContext = vm.createContext({});
vm.runInContext(`${viewsBindingFunctions}; globalThis.viewsBindingKey = viewsBindingKey; globalThis.viewsCatalogTargetKey = viewsCatalogTargetKey;`, viewsBindingContext);
const firstBinding = { schemaId: "schema", revision: 2, layoutToken: "a".repeat(64), profileId: "profile", database: "database", namespace: "public" };
const layoutSavedBinding = { ...firstBinding, revision: 3, layoutToken: "b".repeat(64) };
assert.notEqual(viewsBindingContext.viewsBindingKey(firstBinding), viewsBindingContext.viewsBindingKey(layoutSavedBinding), "mutation bindings must advance after a layout save");
assert.equal(viewsBindingContext.viewsCatalogTargetKey(firstBinding), viewsBindingContext.viewsCatalogTargetKey(layoutSavedBinding), "layout-only revision changes must preserve the live catalog target");
assert.match(source, /targetKey = viewsCatalogTargetKey\(binding\)[\s\S]*targetKey !== viewsCatalogTargetKey\(binding\)[\s\S]*targetKey !== viewsCatalogTargetKey\(activeViewsBinding\(\)\)/, "catalog rendering and workspace entry must use stable target identity rather than layout revisions");
assert.match(source, /relationCatalog\(binding\.profileId, binding\.database, binding\.namespace\)[\s\S]*requireExactTarget\(payload, binding\)[\s\S]*filter\(item => item\.kind === "view" \|\| item\.kind === "materialized_view"\)/, "catalog loading must fully page, validate its target, and retain only views");
assert.match(source, /new URLSearchParams\(\{ database: binding\.database, namespace: identity\.namespace, relation: identity\.relation, expectedKind: identity\.kind \}\)[\s\S]*query\.set\("expectedFingerprint", knownFingerprint\)[\s\S]*\/relation\?\$\{query\}/, "relation inspection must preserve the verified lineage namespace and known-fingerprint guard");
assert.match(source, /function validateRelationDescriptor[\s\S]*columns[\s\S]*definition[\s\S]*owner[\s\S]*permissions[\s\S]*columnProvenance[\s\S]*joinPredicates[\s\S]*sqlStages[\s\S]*materialized[\s\S]*dependencies[\s\S]*dependents/, "relation descriptors must be strictly validated before rendering");
assert.match(source, /function validateViewColumnProvenance[\s\S]*relationFingerprint[\s\S]*outputOrdinal !== column\.ordinal[\s\S]*sources\.has\(relationIdentityKey\(input\)\)/, "column provenance must be fingerprint-bound, output-ordered, and limited to verified direct upstream relations");
assert.match(source, /function validateViewJoinPredicates[\s\S]*value\.relationFingerprint !== relationFingerprint[\s\S]*sources\.has\(relationIdentityKey\(item\)\)[\s\S]*left: endpoint\(predicate\.left\), right: endpoint\(predicate\.right\)/, "join provenance must be fingerprint-bound and limited to verified upstream relation endpoints");
assert.match(source, /mappingStatus[\s\S]*output\.reason[\s\S]*Partial mapping/, "unresolved source columns must remain visibly partial instead of receiving invented mappings");
assert.match(source, /function canInspectViewsRelation[\s\S]*"partitioned_table"[\s\S]*"foreign_table"/, "all supported PostgreSQL relation kinds must be inspectable from verified lineage");
assert.match(source, /\/lineage\?\$\{lineageQuery\}[\s\S]*catalogFingerprint !== envelope\.catalogFingerprint[\s\S]*descriptor\[direction\]\.push/, "lineage continuation must preserve one catalog fingerprint before assembling complete browser lineage");
assert.match(source, /identity\.namespace !== binding\.namespace[\s\S]*outside the active saved namespace[\s\S]*not editable in the Views workspace/, "cross-namespace and non-editable lineage inspection must explain why mutation is disabled");
assert.match(source, /catalogGeneration[\s\S]*relationGenerations: new Map\(\)[\s\S]*relationGenerations\.get\(identityKey\)[\s\S]*generation !== viewsPrototypeState\.relationGenerations\.get\(identityKey\)/, "catalog and per-relation requests need isolated stale-response generations");
assert.match(source, /function relationIdentityKey\(identity\)[\s\S]*JSON\.stringify\(\[identity\.database, identity\.namespace, identity\.relation, identity\.kind\]\)/, "relation identity keys must be collision-free and safe in parsed HTML attributes");
const relationKeyFunction = source.match(/function relationIdentityKey\(identity\) \{[\s\S]*?\n\}/)?.[0] ?? "";
assert.doesNotMatch(relationKeyFunction, /\\u0000/, "relation identity keys must never put NUL characters in HTML attributes");

assert.match(source, /async function hydrateViewsSourceDescriptors[\s\S]*filter\(source => canInspectViewsRelation\(source\)[\s\S]*Promise\.allSettled\(missing\.map\(source => inspectViewsRelation\(source\)\)\)/, "source descriptors must hydrate automatically from verified relation identities");
assert.match(source, /inspectViewsRelation\([\s\S]*await hydrateViewsSourceDescriptors\(selectedPrototypeView\(\)\)/, "selected view loading must include its source column descriptors");
assert.doesNotMatch(source, /expandedSources|togglePrototypeSourceColumns|data-toggle-source-columns|prototype-source-columns[^\n]*hidden/, "source columns must not require an expand state or hidden panel");
assert.match(source, /shell\.classList\.contains\("catalog-open"\) && viewsPrototypeState\.inspectedRelation === relationName\)[\s\S]*viewsPrototypeState\.inspectedRelation = null;[\s\S]*setPrototypeViewCatalogOpen\(false\)/, "clicking the relation already shown must close its inspector drawer");
assert.match(source, /prototype-source-inspect[\s\S]*data-prototype-relation="\$\{escapeHtml\(relationIdentityKey\(source\)\)\}"/, "upstream Inspect relation must use the same toggleable inspector identity as every relation control");
assert.match(source, /function openPrototypeRelationInspector[\s\S]*viewsPrototypeState\.catalogOpen = false;[\s\S]*updateWorkspaceRail\(\)/, "opening an inspector must remove Browse Views highlighting and expanded semantics");
assert.match(source, /const catalogMissing = open && \(!catalog\?\.querySelector\("\[data-prototype-view-filter\]"\)[\s\S]*viewsPrototypeState\.inspectedRelation = null[\s\S]*catalog\.innerHTML = prototypeCatalogPanel\(\)/, "Browse Views must authoritatively replace stale inspector content with the searchable catalog");
assert.match(source, /function validateViewSqlStages[\s\S]*version === 1[\s\S]*syntactic_dependency[\s\S]*relationFingerprint[\s\S]*stages\.length > 128[\s\S]*displayOrdinal !== index \+ 1[\s\S]*\["cte", "derived_table", "query_block"\]/, "SQL stages must be versioned, fingerprint-bound, bounded, contiguous, and limited to real query-local stage kinds");
assert.match(source, /queryBlocks\.length !== 1[\s\S]*stages\.at\(-1\)[\s\S]*parentStageId !== null[\s\S]*queryBlocks\[0\]\.recursive[\s\S]*queryBlocks\[0\]\.name !== undefined[\s\S]*invalid root query block/, "available stage envelopes must contain exactly one final, unnamed, nonrecursive outer SELECT root");
assert.match(source, /function validateViewSqlStages[\s\S]*stageIds\.has\(input\.source\.stageId\)[\s\S]*relationSources\.has\(relationIdentityKey\(input\.source\)\)[\s\S]*inconsistent SQL stage dependencies/, "stage and relation inputs must remain inside verified references with consistent dependency IDs");
const sqlStagesValidatorSource = source.slice(source.indexOf("function validateSqlExpression"), source.indexOf("function validateViewColumnProvenance"));
const validatorContext = vm.createContext({ TextEncoder });
vm.runInContext(`
  function relationIdentityKey(identity) { return JSON.stringify([identity.database, identity.namespace, identity.relation, identity.kind]); }
  ${sqlStagesValidatorSource}
  globalThis.validateViewSqlStages = validateViewSqlStages;
`, validatorContext);
const relationFingerprint = "a".repeat(64);
const analysisFingerprint = "b".repeat(64);
const dependency = { type: "relation", profileId: "profile", database: "database", namespace: "public", relation: "orders", kind: "table" };
const validStages = {
  status: "available", version: 1, orderSemantics: "syntactic_dependency", relationFingerprint, fingerprint: analysisFingerprint,
  stages: [{
    stageId: "stage_one", displayOrdinal: 1, kind: "cte", name: "selected", parentStageId: null, recursive: false, lifetime: "query",
    dependsOnStageIds: [], sql: { status: "available", sql: "SELECT id FROM public.orders" },
    outputColumns: [{ ordinal: 1, name: "id", nameSource: "source_column", expression: { status: "available", sql: "id" } }],
    inputs: [{ inputOrdinal: 1, referenceAlias: "o", source: dependency }],
    joinPredicates: [], wherePredicates: [{ ordinal: 1, expression: { status: "available", sql: "id > 0" } }], havingPredicates: [], mappingStatus: "available"
  }, {
    stageId: "root_query", displayOrdinal: 2, kind: "query_block", parentStageId: null, recursive: false, lifetime: "query",
    dependsOnStageIds: ["stage_one"], sql: { status: "available", sql: "SELECT id FROM selected" },
    outputColumns: [{ ordinal: 1, name: "id", nameSource: "source_column", expression: { status: "available", sql: "id" } }],
    inputs: [{ inputOrdinal: 1, referenceAlias: "s", source: { type: "stage", stageId: "stage_one" } }],
    joinPredicates: [], wherePredicates: [], havingPredicates: [], mappingStatus: "available"
  }]
};
assert.equal(validatorContext.validateViewSqlStages(validStages, relationFingerprint, [dependency], { profileId: "profile", database: "database" }).stages[0].name, "selected");
assert.equal(validatorContext.validateViewSqlStages(validStages, relationFingerprint, [dependency], { profileId: "profile", database: "database" }).stages[1].kind, "query_block");
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, relationFingerprint: "c".repeat(64) }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid SQL-stage envelope/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: [{ ...validStages.stages[0], displayOrdinal: 2 }] }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid SQL stage identity/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: [{ ...validStages.stages[0], inputs: [{ ...validStages.stages[0].inputs[0], source: { ...dependency, relation: "unverified" } }] }, validStages.stages[1]] }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /outside verified dependencies/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: [{ ...validStages.stages[0], sql: { status: "available", sql: "x".repeat(4097) } }, validStages.stages[1]] }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid SQL stage expression/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: validStages.stages.slice(0, 1) }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid root query block/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: [validStages.stages[0], { ...validStages.stages[1], name: "invented" }] }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid root query block/);
assert.throws(() => validatorContext.validateViewSqlStages({ ...validStages, stages: [{ ...validStages.stages[1], displayOrdinal: 1 }, { ...validStages.stages[0], displayOrdinal: 2 }] }, relationFingerprint, [dependency], { profileId: "profile", database: "database" }), /invalid root query block|invalid SQL stage metadata|inconsistent SQL stage dependencies/);
const unavailableStages = { status: "unavailable", reason: "not_supported", version: 1, orderSemantics: "syntactic_dependency", stages: [], relationFingerprint, fingerprint: analysisFingerprint };
assert.equal(validatorContext.validateViewSqlStages(unavailableStages, relationFingerprint, [dependency], { profileId: "profile", database: "database" }).status, "unavailable");
assert.match(source, /function buildViewsLineageModel[\s\S]*kind: "source"[\s\S]*kind: "stage"[\s\S]*kind: "query_block"[\s\S]*kind: "view"[\s\S]*kind: "consumer"/, "one model must preserve accessible source, query-local stage, root query-block, final-view, and consumer order");
const canvasModelFunction = source.match(/function buildViewsLineageModel[\s\S]*?(?=function viewsEdgeMarkup)/)?.[0] ?? "";
assert.match(canvasModelFunction, /stage\.kind === "query_block" \? "root-input" : "stage-input"[\s\S]*from: rootKey, to: finalKey/, "verified inputs must enter the root query block before its one result edge reaches the final view");
assert.doesNotMatch(canvasModelFunction, /!relationEdge\.has|nodeBySource\.get\(identityKey\).*to: finalKey/, "physical sources must never receive a fabricated direct bypass to the final view");
assert.match(canvasModelFunction, /laneY[\s\S]*height, gap[\s\S]*680 \+ depth \* 620[\s\S]*rootX \+ 680[\s\S]*finalNode\.x \+ 600/, "fallback columns and balanced lanes must retain generous deterministic world spacing");
assert.match(source, /function viewsNodePosition[\s\S]*Number\.isFinite\(saved\?\.x\)[\s\S]*Number\.isFinite\(saved\?\.y\)/, "persisted Views positions must remain authoritative over deterministic fallbacks");
assert.doesNotMatch(canvasModelFunction, /viewsObjects\s*\[[^\]]+\]\s*=/, "fallback model construction must never auto-save node positions");
const lineageModelSource = source.slice(source.indexOf("function viewsNodePosition"), source.indexOf("function viewsEdgeMarkup"));
const modelContext = vm.createContext({});
vm.runInContext(`
  const viewsObjects = {};
  const viewsPrototypeState = { flowFocus: null, selectedOutputOrdinal: null, selectedSourceKey: null, selectedSourceColumn: null, descriptors: new Map(), views: [] };
  function activeViewsBinding() { return { database: "database" }; }
  function relationIdentityKey(identity) { return JSON.stringify([identity.database, identity.namespace, identity.relation, identity.kind]); }
  function prototypeEndpointMatchesSource(endpoint, source) { return endpoint?.database === source?.database && endpoint?.namespace === source?.namespace && endpoint?.relation === source?.relation && endpoint?.kind === source?.kind; }
  function prototypeJoinLineage(viewItem) { return viewItem.joins; }
  function prototypeColumnLineage(viewItem) { return viewItem.provenance; }
  ${lineageModelSource}
  globalThis.buildViewsLineageModel = buildViewsLineageModel;
  globalThis.setFocus = focus => { viewsPrototypeState.flowFocus = focus; };
  globalThis.unsavedSourcePosition = viewsUnsavedSourcePosition;
  globalThis.rectsOverlap = viewsRectsOverlap;
`, modelContext);
const orders = { type: "relation", profileId: "profile", database: "database", namespace: "public", relation: "orders", kind: "table" };
const customers = { type: "relation", profileId: "profile", database: "database", namespace: "public", relation: "customers", kind: "table" };
const endpoint = (sourceIdentity, alias, columnName) => ({ ...sourceIdentity, referenceAlias: alias, referenceColumnName: columnName, columnName, columnOrdinal: 1 });
const modelView = {
  name: "summary", namespace: "public", kind: "view", sources: [orders, customers],
  dependents: [{ database: "database", namespace: "public", relation: "summary_consumer", kind: "view" }],
  sqlStages: { status: "available", stages: [{
    stageId: "cte_orders", displayOrdinal: 1, kind: "cte", name: "selected_orders", parentStageId: null,
    dependsOnStageIds: [], inputs: [{ inputOrdinal: 1, referenceAlias: "o", source: orders }], mappingStatus: "available"
  }, {
    stageId: "root_query", displayOrdinal: 2, kind: "query_block", parentStageId: null,
    dependsOnStageIds: ["cte_orders"], inputs: [
      { inputOrdinal: 1, referenceAlias: "o", source: { type: "stage", stageId: "cte_orders" } },
      { inputOrdinal: 2, referenceAlias: "c", source: customers }
    ], mappingStatus: "available"
  }] },
  joins: { status: "available", joins: [{ joinOrdinal: 1, queryScope: "root", predicates: [{ left: endpoint(orders, "o", "customer_id"), right: endpoint(customers, "c", "id") }] }] },
  provenance: { status: "available", outputs: [{ outputOrdinal: 1, outputName: "order_id", inputs: [endpoint(orders, "o", "id")] }] }
};
const ordersKey = JSON.stringify(["database", "public", "orders", "table"]);
const customersKey = JSON.stringify(["database", "public", "customers", "table"]);
let executableModel = modelContext.buildViewsLineageModel(modelView);
assert.deepEqual(Array.from(executableModel.nodes, node => node.kind), ["source", "source", "stage", "query_block", "view", "consumer"], "semantic node order must remain source, local stage, root query block, final view, consumer");
assert.equal(executableModel.edges.some(edge => edge.from === `relation:${ordersKey}` && edge.to === executableModel.finalKey), false, "the model must not create a source-to-final bypass");
assert.equal(executableModel.edges.some(edge => edge.from === executableModel.rootKey && edge.to === executableModel.finalKey), true, "the root query block must own the final-view result edge");
modelContext.setFocus({ kind: "output", outputOrdinal: 1 });
executableModel = modelContext.buildViewsLineageModel(modelView);
assert.equal(executableModel.edges.find(edge => edge.from === `relation:${ordersKey}`).emphasis, "active", "output focus must activate its verified contributor path");
assert.equal(executableModel.edges.find(edge => edge.from === `relation:${customersKey}`).emphasis, "muted", "output focus must not invent an unrelated contributor path");
assert.equal(executableModel.edges.find(edge => edge.kind === "result").emphasis, "active", "output focus must continue through the root result to the final view");
modelContext.setFocus({ kind: "join", joinOrdinal: 1 });
executableModel = modelContext.buildViewsLineageModel(modelView);
assert.deepEqual(Array.from(executableModel.focusSources).sort(), [customersKey, ordersKey].sort(), "join focus must activate both verified endpoint sources");
assert.equal(executableModel.edges.find(edge => edge.kind === "consumer").emphasis, "muted", "query focus must stop at the final view rather than imply consumer projection provenance");
const occupiedSourceCards = [{ x: 46, y: 268, width: 280, height: 164 }, { x: -4, y: 588, width: 280, height: 199 }];
const unsavedSourcePosition = modelContext.unsavedSourcePosition({ x: 80, y: 140 }, 349, occupiedSourceCards);
assert.equal(occupiedSourceCards.some(rect => modelContext.rectsOverlap({ ...unsavedSourcePosition, width: 280, height: 349 }, rect)), false, "an unsaved tall source fallback must not overlap persisted source cards");
modelView.joins.joins.push({ joinOrdinal: 2, queryScope: "nested", predicates: [] });
modelContext.setFocus({ kind: "query_block", stageId: "root_query" });
executableModel = modelContext.buildViewsLineageModel(modelView);
assert.deepEqual(Array.from(executableModel.activeJoinOrdinals), [1], "query-block focus must not claim joins owned by nested SQL scopes");
const stageCardFunction = source.match(/function renderViewsStageCard[\s\S]*?(?=function renderViewsConsumerCard)/)?.[0] ?? "";
assert.match(stageCardFunction, /CTE[\s\S]*Derived table[\s\S]*Query lifetime/, "real SQL stages must be visibly query-local");
for (const predicate of ["joinPredicates", "wherePredicates", "havingPredicates"]) assert.match(stageCardFunction, new RegExp(predicate), `stage cards must retain ${predicate}`);
const queryBlockFunction = source.match(/function renderViewsQueryBlockCard[\s\S]*?(?=function renderViewsConsumerCard)/)?.[0] ?? "";
for (const evidence of ["Root query block", "Outer SELECT", "Verified input aliases", "Join logic", "More query logic", "queryScope === \"root\"", "joinType", "rightReferenceAlias", "predicate.left.referenceAlias", "condition.sql", "join.reasons", "Filters", "wherePredicates", "havingPredicates", "Selected projection"]) assert.match(queryBlockFunction, new RegExp(evidence.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `the root query-block card must own ${evidence}`);
assert.match(queryBlockFunction, /No verified source endpoints[\s\S]*data-select-query-join/, "partial join rows must remain selectable without fabricating alias-only endpoints");
assert.match(queryBlockFunction, /activeJoinOrdinals\.has\(join\.joinOrdinal\)[\s\S]*participating \? "active"/, "query-block, source, and output focus must visibly mark participating join rows");
assert.match(queryBlockFunction, /conditionDetail = join\.mappingStatus === "available"[\s\S]*class="views-query-overview"[\s\S]*class="views-query-aliases"[\s\S]*class="views-query-more"/, "the root query card must keep verified joins compact and disclose secondary filter/projection evidence on demand");
assert.doesNotMatch(queryBlockFunction, /views-query-inputs/, "the compact query hub must not retain the large verified-input section");
assert.match(source, /function prototypeOutputButton[\s\S]*data-view-output[\s\S]*aria-pressed/, "view output columns must be ordinary accessible selectable buttons");
const outputButtonFunction = source.match(/function prototypeOutputButton[\s\S]*?(?=function prototypeInputIdentity)/)?.[0] ?? "";
assert.doesNotMatch(outputButtonFunction, /role="listbox"|role="option"|aria-selected=/, "view columns must not use invalid listbox option semantics");
assert.match(source, /selectedOutputOrdinal = Number\(output\.dataset\.viewOutput\)[\s\S]*selectedControl[\s\S]*focus\(\{ preventScroll: true \}\)/, "output selection must restore focus without scrolling the canvas");
assert.doesNotMatch(source.slice(source.indexOf('elements.viewsConceptStage.addEventListener("click"'), source.indexOf('elements.viewsConceptStage.addEventListener("input"')), /scrollIntoView/, "Views selection must not silently scroll the spatial canvas");
assert.match(source, /viewsConceptStage\.addEventListener\("keydown"[\s\S]*canvasKeyboardPanDelta\(event\.key, event\.shiftKey\)[\s\S]*viewsView\.x \+= delta\.x[\s\S]*saveViewsLayout\(\)/, "the focusable Views viewport must support intentional arrow-key pan persistence");
assert.match(source, /function prototypeSourceColumnProjections[\s\S]*output\.inputs\.some[\s\S]*columnName/, "upstream source columns must resolve to the output columns that use them");
assert.match(source, /prototype-source-column-select[\s\S]*data-view-output[\s\S]*Inspect \$\{escapeHtml\(column\.name\)\}/, "mapped upstream columns must select and reveal their output transformation");
assert.match(source, /const columns = relation\?\.columns[\s\S]*class="prototype-source-columns">\$\{columnRows/, "upstream cards must expose actual or mapped source columns by default");
assert.match(source, /class="prototype-source-column \$\{className\}/, "visible upstream cards must classify every source column mapping");
const projectionFunction = source.match(/function renderViewsProjectionDetail[\s\S]*?(?=function renderFlowViewHero)/)?.[0] ?? "";
assert.match(projectionFunction, /output\.inputs\.map[\s\S]*source-selected/, "the final view card must render and highlight every verified input");
assert.match(projectionFunction, /output\.expression\.sql[\s\S]*escapeHtml\(output\.outputName\)/, "the final view card must show the exact selected output logic");
assert.match(source, /function prototypeJoinTouchesSource[\s\S]*prototypeEndpointMatchesSource\(predicate\.left, source\)[\s\S]*prototypeEndpointMatchesSource\(predicate\.right, source\)/, "join emphasis must use only verified predicate endpoints");
assert.match(canvasModelFunction, /input\.referenceAlias/, "input aliases must remain edge evidence");
assert.match(source, /data-select-flow-source[\s\S]*selectedSourceKey = relationIdentityKey\(source\)[\s\S]*selectedSourceColumn = null[\s\S]*renderViewsPrototype\(\)/, "selecting a source relation must update the transformation and join focus together");
assert.match(source, /if \(!sourceColumn\) \{[\s\S]*selectedSourceColumn = null[\s\S]*selectedOutput\.inputs\.some\(input => prototypeEndpointMatchesSource\(input, currentSource\)\)[\s\S]*selectedSourceKey = relationIdentityKey\(matchingSource\)/, "selecting a view output must clear stale column focus and align the source stage to a verified input");
assert.match(source, /if \(viewsOpen\) \{[\s\S]*mainLayout\.scrollLeft = 0;[\s\S]*mainLayout\.scrollTop = 0;[\s\S]*viewsPrototypeWorkspace\.hidden = false/, "opening Views must clear hidden container scroll so the workspace cannot remain clipped off-screen");
assert.match(source, /function renderViewsPrototype\(\) \{[\s\S]*viewsPrototypeState\.layer === "views"[\s\S]*mainLayout\.scrollLeft = 0;[\s\S]*viewsConceptStage\.innerHTML/, "Views rerenders must retain the workspace at the visible container origin");
for (const focusMode of [/flowFocus = \{ kind: "source"/, /flowFocus = \{ kind: "join"/, /flowFocus = \{ kind: "query_block"/, /flowFocus = sourceColumn[\s\S]*kind: "output"/]) assert.match(source, focusMode, "source, join, query-block, and output controls must establish explicit focus modes");
assert.match(canvasModelFunction, /join\.predicates[\s\S]*relationIdentityKey\(predicate\.left\)[\s\S]*relationIdentityKey\(predicate\.right\)[\s\S]*edge\.emphasis[\s\S]*node\.emphasis/, "focus highlighting must derive source endpoints, paths, edges, and cards only from verified evidence");
assert.match(source, /function clearViewsFlowFocus[\s\S]*flowFocus = null[\s\S]*selectedOutputOrdinal = null[\s\S]*data-clear-views-focus[\s\S]*clearViewsFlowFocus\(\)[\s\S]*event\.target\.matches\("\.views-lineage-viewport,\.views-canvas-grid,\.views-lineage-stage"\)[\s\S]*clearViewsFlowFocus\(\)/, "focus must expose a clear control and empty-canvas clearing");
const viewsClickHandler = source.slice(source.indexOf('elements.viewsConceptStage.addEventListener("click"'), source.indexOf('elements.viewsConceptStage.addEventListener("input"'));
assert.doesNotMatch(viewsClickHandler, /saveViewsLayout|saveSchema|persist/, "selection and focus rerenders must never persist layout");
assert.match(source, /function viewsEdgeDefinitions[\s\S]*views-arrow-available[\s\S]*views-arrow-partial[\s\S]*views-arrow-active[\s\S]*views-arrow-active-partial[\s\S]*markerUnits="userSpaceOnUse"/, "direction markers must provide high-contrast available, partial, and active destination arrowheads");
assert.match(source, /function viewsEdgeMarkup[\s\S]*marker-end="url\(#\$\{marker\}\)"/, "every edge must attach its status marker at the destination endpoint");
assert.match(source, /function viewsEdgeMarkup[\s\S]*return \{[\s\S]*path:[\s\S]*label:[\s\S]*function viewsEdgesMarkup[\s\S]*markup\.map\(item => item\.path\)[\s\S]*class="views-edge-label-layer"[\s\S]*markup\.map\(item => item\.label\)/, "all paths must render before the dedicated foreground alias-label layer");
assert.match(source, /const VIEWS_QUERY_INPUT_ANCHOR_Y = 128[\s\S]*edge\.kind === "root-input" \? to\.y \+ VIEWS_QUERY_INPUT_ANCHOR_Y/, "every root input edge must terminate at one shared query-block anchor");
assert.match(source, /class="views-query-input-port"/, "the shared query-block input anchor must be visible");
assert.match(source, /function measureViewsLineageModel[\s\S]*data-views-node-key[\s\S]*offsetWidth[\s\S]*offsetHeight/, "edge routing and fit must measure rendered card dimensions");
assert.match(source, /function renderViewsCanvasEdges[\s\S]*measureViewsLineageModel\(buildViewsLineageModel\(selected\)\)/, "edge routing must include the rendered query-block dimensions");
assert.match(source, /function fitViewsCanvas[\s\S]*measureViewsLineageModel\(buildViewsLineageModel\(selected\)\)/, "fit must include the rendered query-block dimensions instead of a fixed estimate");
assert.match(source, /function applyViewsView[\s\S]*devicePixelRatio[\s\S]*stage\.style\.zoom = String\(viewsView\.zoom\)[\s\S]*translate\(\$\{renderedX \/ viewsView\.zoom\}px, \$\{renderedY \/ viewsView\.zoom\}px\)/, "Views zoom must use layout zoom with pixel-aligned translation so text is rerasterized sharply");
assert.doesNotMatch(source.match(/function applyViewsView[\s\S]*?(?=function measureViewsLineageModel)/)?.[0] ?? "", /scale\(|translate3d/, "Views zoom must not blur text through composited transform scaling");
for (const dragSurface of [/renderViewsNodeHandle\(`Source:/, /renderViewsNodeHandle\(`\$\{stage\.kind === "cte"/, /renderViewsNodeHandle\("Root query block"\)/, /renderViewsNodeHandle\("Final view"\)/, /renderViewsNodeHandle\(`Consumer:/]) assert.match(source, dragSurface, "source, local stage, query block, final view, and consumer cards must expose titled drag surfaces");
assert.match(source, /data-views-node-handle[\s\S]*setPointerCapture[\s\S]*POINTER_MOVE_THRESHOLD_PX[\s\S]*\/ viewsView\.zoom[\s\S]*pointercancel[\s\S]*viewsNodeDragState\.previous/, "Views card dragging must retain threshold, world-coordinate zoom, pointer capture, and cancel rollback");
assert.match(source, /const handle = event\.target\.closest\("\[data-views-node-handle\]"\)[\s\S]*event\.target\.closest\("button,input,textarea,select,a\[href\],details,\[contenteditable=true\],\[data-views-node-key\]"\)/, "only titled drag surfaces may start card movement while interactive content remains clickable");

assert.match(source, /function viewsMutationBody\(operation, definition, allowDestructive\)[\s\S]*schemaId: binding\.schemaId[\s\S]*expectedSchemaRevision: binding\.revision[\s\S]*layoutToken: binding\.layoutToken[\s\S]*operation, expectation: clone[\s\S]*operation === "upsert" \? \{ desired: \{ kind, definition \} \} : \{\}/, "view preview must send an explicit operation and omit desired for delete");
assert.match(source, /const bindingKey = viewsBindingKey\(activeViewsBinding\(\)\);[\s\S]*const body = viewsMutationBody\(operation, definition, allowDestructive\);[\s\S]*const plan = await postgresRequest[\s\S]*bindingKey !== viewsBindingKey\(activeViewsBinding\(\)\)/, "view preview must use the current active record binding exactly once");
const previewFunction = source.match(/async function previewViewDefinition[\s\S]*?(?=async function reloadActiveSchemaRecord)/)?.[0] ?? "";
assert.doesNotMatch(previewFunction, /schema\.views|persistCurrentSchema\(/, "preview must not persist new-view placeholders or edited SQL drafts");
assert.match(source, /if \(duplicate\) delete draft\.definitionDraft;[\s\S]*prototypeViewDefinition\(draft\)/, "duplicate definitions must be regenerated with the new identity");
assert.match(source, /destructive_preview_required[\s\S]*confirm\("This change requires a destructive recreation preview/, "destructive preview must require explicit user choice");
assert.match(source, /data-confirm-destructive-view[\s\S]*confirmDestructive/, "destructive apply must require a separate explicit confirmation");
assert.match(html, /id="views-browse-button"[\s\S]*id="views-create-button"[\s\S]*id="views-refresh-button"[\s\S]*id="views-delete-button"/, "the Views workspace actions must live in the left tool rail");
assert.match(source, /function updateWorkspaceRail[\s\S]*viewsDeleteButton\.disabled = !selectedView \|\| viewsPrototypeState\.loading[\s\S]*viewsDeleteButton\.dataset\.tooltip/, "the Views delete rail action must retain only structural and loading restrictions");
assert.match(source, /definitionHistories: new Map\(\)[\s\S]*function recordViewDefinitionEdit[\s\S]*history\.undo\.push\(previous\)[\s\S]*history\.redo = \[\]/, "each view definition draft must maintain bounded undo history and clear redo on a new edit");
assert.match(source, /function restoreViewDefinitionDraft[\s\S]*direction === "undo"[\s\S]*target\.push[\s\S]*source\.pop\(\)[\s\S]*renderViewsPrototype\(\)/, "view definition undo and redo must restore the editor from dedicated draft history");
assert.match(source, /function updateHistoryControls[\s\S]*viewsPrototypeState\.layer === "views"[\s\S]*history\?\.undo\.length[\s\S]*history\?\.redo\.length/, "shared Undo and Redo controls must reflect the selected view draft history");
assert.match(source, /viewsConceptStage\.addEventListener\("keydown"[\s\S]*data-prototype-definition-editor[\s\S]*event\.preventDefault\(\)[\s\S]*if \(event\.shiftKey\) redo\(\); else undo\(\)/, "definition-editor keyboard shortcuts must use the same history as the rail controls");
const definitionSection = source.match(/<section class="prototype-focus-definition">[\s\S]*?<\/section>/)?.[0] || "";
assert.doesNotMatch(definitionSection, /canAlter/, "advisory alter metadata must not disable definition editing or preview");
assert.match(source, /Advisory privileges[\s\S]*PostgreSQL authorizes requests/, "catalog privileges must remain visibly advisory");
assert.doesNotMatch(source.match(/views-lineage-head[\s\S]*?<\/header>/)?.[0] || "", /views-prototype-actions|data-delete-prototype-view|data-create-prototype-view/, "moved Views actions must not remain duplicated in the content header");
assert.doesNotMatch(definitionSection, /data-delete-prototype-view/, "delete must have one authoritative rail entry point");
assert.match(source, /function deleteSelectedPrototypeView[\s\S]*previewViewOperation\("delete", null, true\)[\s\S]*viewsDeleteButton\.addEventListener\("click", deleteSelectedPrototypeView\)/, "the rail delete action must use the dedicated destructive preview operation");
assert.match(source, /all rows stored in it[\s\S]*Source-table rows are not deleted[\s\S]*No CASCADE will be used/, "materialized deletion review must distinguish stored rows from source rows and prohibit CASCADE");
assert.match(source, /\/view-plans\/\$\{encodeURIComponent\(pending\.plan\.id\)\}\/apply/, "only the Schemii view-plan apply API may commit definitions");
assert.match(source, /schemaId: activeSchemaId, expectedRevision: record\.revision, layoutToken: record\.layoutToken[\s\S]*reviewDigest: postgresState\.plan\.reviewDigest/, "normal migration preview must bind the saved record and apply only its review digest plus confirmation");
assert.match(source, /postgresState\.plan\.applyCapable !== true[\s\S]*Resolve the blocking differences/, "incomplete full-schema previews must not be applyable");
assert.match(source, /blockingDifferences[\s\S]*Next action:/, "migration preview must render structured blocking reasons and next actions");
assert.match(source, /plan\.complete[\s\S]*No database changes[\s\S]*Migration preview is incomplete/, "zero-step incomplete previews must not be labeled synchronized");
assert.doesNotMatch(source.match(/async function applyPostgresMigration[\s\S]*?(?=function collectDatabaseObjects)/)?.[0] ?? "", /persistSchemaRecord|preserveTableLayout/, "normal migration apply must not make the browser the post-commit schema-sync authority");
assert.match(source, /reviewDigest: pending\.plan\.reviewDigest[\s\S]*syncRecord\.state === "conflict"[\s\S]*PostgreSQL committed[\s\S]*reloadActiveSchemaRecord\(\)[\s\S]*return loadViewsCatalog/, "schema-sync conflict must use the reviewed digest, report committed state, refresh, and never retry apply");
assert.match(source, /syncRecord\?\.receipt[\s\S]*schemaSync\.revision[\s\S]*schemaSync\.layoutToken[\s\S]*reloadActiveSchemaRecord/, "successful durable apply must advance and reload the exact active record");
assert.match(source, /syncRecord\.state === "failed"[\s\S]*PostgreSQL committed[\s\S]*plan will not be retried[\s\S]*reloadActiveSchemaRecord/, "post-commit storage failure must refresh and never retry deletion");
assert.match(html, /id="prototype-view-editor-dialog"[\s\S]*id="prototype-view-error"[^>]*role="alert"[^>]*hidden/, "the view definition editor must provide a persistent PostgreSQL diagnostic region");
assert.match(source, /function postgresDiagnosticText\(error\)[\s\S]*SchemiiShared\.formatApiError/, "Views must use the shared PostgreSQL diagnostic formatter");
for (const field of ["message", "detail", "hint", "position"]) assert.match(diagnostics, new RegExp(`postgres\\.${field}`), `shared PostgreSQL diagnostics must render ${field}`);
assert.match(source, /let databaseApplied = false;[\s\S]*databaseApplied = true;[\s\S]*catch \(error\) \{[\s\S]*!databaseApplied && error\.code === "apply_failed"[\s\S]*prototypeViewEditorDialog\.showModal\(\)[\s\S]*showPrototypeViewError\(error\)/, "only a definitively rolled-back view apply may return users to the definition with its PostgreSQL diagnostic");
assert.match(source, /result\.operation === "delete"[\s\S]*filter\(item => item\.id !== result\.deleted\.relation\)[\s\S]*loadViewsCatalog\(\{ preserveSelection: true \}\)/, "successful deletion must remove stale local selection and reload the live catalog");

assert.match(source, /data-prototype-view-filter[\s\S]*querySelectorAll\("\.prototype-focus-catalog \[data-prototype-view-id\]"\)[\s\S]*card\.hidden/, "typed search must filter existing catalog cards without rerendering the workspace");
assert.match(source, /data-view-kind-filter/, "existing catalog kind filtering must remain live");
assert.match(html, /prototype-view-namespace[^>]*readonly/, "the mutation namespace must not be editable");
assert.match(html, /value="materialized_view"/, "the editor must use the server materialized-view kind");
assert.match(styles, /\.prototype-impact-compact[\s\S]*grid-template-columns: repeat\(3,minmax\(70px,\.65fr\)\) minmax\(150px,1\.4fr\)/, "the approved compact impact strip must remain styled");
assert.match(styles, /\.views-lineage-viewport[\s\S]*\.views-canvas-grid[\s\S]*\.views-lineage-stage[\s\S]*\.views-lineage-edges[\s\S]*\.views-stage-card[\s\S]*\.views-projection-detail[\s\S]*\.views-canvas-controls/, "the graphical lineage viewport, transformed stage, edges, cards, projection, and controls must be styled");
assert.match(styles, /\.views-lineage-edge\.active > path[\s\S]*\.views-lineage-edge\.muted[\s\S]*\.views-arrow-available path[\s\S]*\.views-arrow-active-partial path/, "available, partial, active, and muted directional paths must remain visibly distinct");
assert.match(styles, /\.views-edge-label\.partial rect[\s\S]*\.views-edge-label\.active rect[\s\S]*\.views-edge-label\.muted/, "foreground alias labels must retain partial, active, and muted evidence states");
assert.match(styles, /\.prototype-source-columns ul \{[^}]*overflow-x: hidden[^}]*overflow-y: auto[\s\S]*\.prototype-source-column \{[^}]*grid-template-columns: minmax\(0,1fr\) minmax\(0,88px\)[^}]*overflow: hidden[\s\S]*\.prototype-source-projections em \{[^}]*text-overflow: ellipsis[^}]*white-space: nowrap/, "visible source columns must remain vertically scrollable without horizontal overflow");
assert.match(styles, /\.views-lineage-stage \{[^}]*text-rendering: optimizeLegibility/, "the layout-zoom stage must retain explicit legible text rendering");
assert.doesNotMatch(styles.match(/\.views-lineage-stage \{[^}]*\}/)?.[0] ?? "", /will-change/, "the lineage stage must not force a blurry transform-composited layer");
assert.match(styles, /\.views-lineage-node\.active[\s\S]*\.views-lineage-node\.muted[\s\S]*\.views-node-handle[^{]*\{[^}]*min-height: 32px[^}]*cursor: grab[\s\S]*\.views-query-block-card[\s\S]*\.views-query-join\.partial/, "focused cards, prominent drag surfaces, root grouping, and partial join rows must be styled");
assert.match(styles, /\.views-query-input-port \{[^}]*top: 128px[^}]*border: 2px solid #b9a7ff[\s\S]*\.views-query-overview[\s\S]*\.views-query-aliases[\s\S]*\.views-query-more/, "the compact query hub and its single visible input port must be styled");
assert.doesNotMatch(source, /Assembly line|Join graph|Merge lanes|viewsLineageVariants|data-view-variant|renderAssemblyWorkflow|renderJoinGraphWorkflow|renderMergeLanesWorkflow/, "the one canvas must not retain any old presentation implementation");
assert.doesNotMatch(styles, /views-wf-|views-concept-switch|views-lineage-key|assembly-sequence|lane-sequence/, "the one canvas must not retain dedicated old presentation styles");
assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.views-lineage-stage,[\s\S]*transition: none;/, "source, drawer, and canvas motion must respect reduced motion");
assert.match(styles, /@media \(max-width: 540px\)[\s\S]*\.prototype-definition-actions \.button \{ min-height: 40px; flex: 1 1 140px; \}/, "mobile view lifecycle actions must wrap with touch-sized controls");
assert.match(styles, /@media \(max-width: 540px\)[\s\S]*\.topbar \{ grid-template-columns: auto minmax\(0, 1fr\) auto;/, "mobile workspace headers must reserve intrinsic space for actions instead of overlapping the active design name");
assert.match(styles, /\.rail-button\.rail-danger \{[^}]*color: #b76f77/, "the view delete rail action must retain danger styling");
assert.match(styles, /\.design-layer-switch > \[data-design-layer="views"\]\.active \{[^}]*color: #b9a7ff[^}]*background: #1d1930/, "the Views workspace selector must use the purple workspace accent");
assert.match(styles, /\.tool-rail\[data-workspace="views"\],\.views-prototype-workspace[^{]*\{[^}]*--accent: #9b82f4/, "Views controls must inherit a scoped purple accent");
assert.match(styles, /\.views-prototype-workspace \.prototype-view-card\.selected \{[^}]*border-color: var\(--accent\)[^}]*rgba\(155,130,244/, "the selected view must use the same purple accent as the Views selector");
assert.match(styles, /\.views-prototype-workspace \.prototype-definition-editor \{[^}]*var\(--accent\)/, "the Views definition editor highlight must inherit purple");
assert.match(styles, /\.design-layer-switch > \[data-design-layer="views"\]:hover[^{]*\{[^}]*color: #b9a7ff[^}]*background: #1d1930/, "the Views selector hover must use purple");
assert.match(styles, /\.views-prototype-workspace \.prototype-view-card:hover[^{]*\{[^}]*border-color: #7c68bd[^}]*background: #171528/, "view-card hover must use a purple surface and border");
assert.match(styles, /\.views-prototype-workspace \.prototype-catalog-filters button:hover[^{]*\{[^}]*var\(--accent-bright\)[^}]*#1d1930/, "Views filter hover states must use purple");

console.log("Schemii live Views frontend contracts passed");
