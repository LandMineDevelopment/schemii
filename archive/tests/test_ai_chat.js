const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const shared = fs.readFileSync("src/schemii/shared_web/ai-assistant.js", "utf8");
const sharedStyles = fs.readFileSync("src/schemii/shared_web/ai-assistant.css", "utf8");
const start = source.indexOf("const AI_SCHEMA_ACTIONS");
const end = source.indexOf("async function applyAiSchemaAction", start);
assert.notEqual(start, -1, "AI action validation marker is missing");
assert.notEqual(end, -1, "AI action validation end marker is missing");

const context = vm.createContext({ TextEncoder });
vm.runInContext(`
  let activeSchemaId = "schema_current";
  const TABLE_WIDTH = 270;
  const COLORS = ["amber", "blue"];
  let nextId = 0;
  function uid(prefix) { nextId += 1; return prefix + "_" + nextId; }
  function readSchemaLibrary() {
    return { schemas: [{ id: "schema_orders", schema: { projectName: "Orders", tables: [], relationships: [] } }] };
  }
  function relationshipColumnPairs(relationship) {
    const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
    const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
    return fromColumnIds.map((fromColumnId, index) => ({ fromColumnId, toColumnId: toColumnIds[index] }));
  }
  ${source.slice(start, end)}
  globalThis.validateAiSchemaAction = validateAiSchemaAction;
  globalThis.validateAiNavigationAction = validateAiNavigationAction;
  globalThis.applyAiPopulation = applyAiPopulation;
`, context);

const schema = {
  tables: [
    { id: "users", name: "users", columns: [{ id: "user_id", name: "id", type: "uuid", primary: true, unique: true }], uniqueConstraints: [] },
    { id: "orders", name: "orders", columns: [{ id: "owner_id", name: "owner_id", type: "uuid" }], uniqueConstraints: [] }
  ],
  relationships: []
};

assert.equal(context.validateAiSchemaAction(schema, { type: "add_table", payload: { name: "events", columns: [{ name: "id", type: "uuid" }] } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "add_table", payload: { name: "users" } }).error, /already exists/);
assert.equal(context.validateAiSchemaAction(schema, { type: "add_column", payload: { tableId: "orders", column: { name: "total", type: "numeric" } } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "update_column", payload: { tableId: "orders", columnId: "owner_id", changes: { nullable: "yes" } } }).error, /true or false/);
assert.equal(context.validateAiSchemaAction(schema, { type: "add_relationship", payload: { fromTableId: "orders", fromColumnId: "owner_id", toTableId: "users", toColumnId: "user_id" } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "delete_element", payload: { tableId: "orders", elementType: "column", columnId: "owner_id" } }).error, /at least one column/);
const population = {
  type: "populate_schema",
  purpose: "Library teaching schema",
  tables: [
    { name: "authors", purpose: "Authors", columns: [{ name: "id", type: "serial", primary: true }, { name: "name", type: "text", nullable: false }] },
    { name: "books", purpose: "Books", columns: [{ name: "id", type: "uuid", primary: true }, { name: "author_id", type: "integer", nullable: false }, { name: "title", type: "text", nullable: false }] }
  ],
  relationships: [{ fromTableName: "books", fromColumnName: "author_id", toTableName: "authors", toColumnName: "id", onDelete: "RESTRICT", onUpdate: "CASCADE" }],
  requiresConfirmation: true
};
const validatedPopulation = context.validateAiSchemaAction({ tables: [], relationships: [] }, population);
assert.equal(validatedPopulation.ok, true);
assert.equal(validatedPopulation.tables.length, 2);
assert.equal(validatedPopulation.relationships.length, 1);
const populatedSchema = { tables: [], relationships: [] };
context.applyAiPopulation(populatedSchema, validatedPopulation, { startX: 100, startY: 80, gridColumns: 2 });
assert.equal(populatedSchema.tables.length, 2);
assert.equal(populatedSchema.relationships.length, 1);
assert.notEqual(populatedSchema.tables[0].id, populatedSchema.tables[1].id);
assert.notEqual(populatedSchema.tables[0].x, populatedSchema.tables[1].x);
const appliedRelation = populatedSchema.relationships[0];
const appliedBooks = populatedSchema.tables.find(table => table.name === "books");
const appliedAuthors = populatedSchema.tables.find(table => table.name === "authors");
assert.equal(appliedRelation.fromTableId, appliedBooks.id);
assert.equal(appliedRelation.fromColumnId, appliedBooks.columns.find(column => column.name === "author_id").id);
assert.equal(appliedRelation.toTableId, appliedAuthors.id);
assert.equal(appliedRelation.toColumnId, appliedAuthors.columns.find(column => column.name === "id").id);
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, { ...population, path: "/tmp/schema" }).error, /unsupported fields/);
const noPrimary = structuredClone(population);
noPrimary.tables[0].columns[0].primary = false;
noPrimary.relationships = noPrimary.relationships.filter(relation => relation.toTableName !== "authors");
assert.equal(context.validateAiSchemaAction({ tables: [], relationships: [] }, noPrimary).ok, true, "PostgreSQL permits keyless tables when no foreign key references them");
const keylessJunction = structuredClone(population);
keylessJunction.tables.push({ name: "book_tags", purpose: "Keyless staging junction", columns: [{ name: "book_id", type: "uuid", nullable: false }, { name: "tag_id", type: "uuid", nullable: false }] });
keylessJunction.tables.push({ name: "tags", purpose: "Tags", columns: [{ name: "id", type: "uuid", primary: true }] });
keylessJunction.relationships.push(
  { fromTableName: "book_tags", fromColumnName: "book_id", toTableName: "books", toColumnName: "id", onDelete: "CASCADE", onUpdate: "CASCADE" },
  { fromTableName: "book_tags", fromColumnName: "tag_id", toTableName: "tags", toColumnName: "id", onDelete: "CASCADE", onUpdate: "CASCADE" }
);
assert.equal(context.validateAiSchemaAction({ tables: [], relationships: [] }, keylessJunction).ok, true, "keyless source/junction tables remain buildable PostgreSQL");
const badTarget = structuredClone(population);
badTarget.relationships[0].toColumnName = "name";
badTarget.tables[0].columns[1].type = "integer";
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, badTarget).error, /primary or unique/);
const badType = structuredClone(population);
badType.tables[1].columns[1].type = "bigint";
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, badType).error, /mismatched column types/);
assert.equal(context.validateAiNavigationAction({ type: "create_project", projectName: "Orders v2", requiresConfirmation: true }).ok, true);
assert.equal(context.validateAiNavigationAction({ type: "open_project", schemaId: "schema_orders", projectName: "Orders", requiresConfirmation: true }).ok, true);
assert.equal(context.validateAiNavigationAction({ type: "open_connection", profileId: "local", name: "Local", database: "demo", namespace: "public", requiresConfirmation: true }).ok, true);
assert.match(context.validateAiNavigationAction({ type: "open_project", schemaId: "../../secret", projectName: "Orders" }).error, /ID is invalid/);
assert.match(context.validateAiNavigationAction({ type: "open_connection", profileId: "local", name: "Local", database: "demo", password: "secret" }).error, /unsupported fields/);
assert.match(context.validateAiNavigationAction({ type: "create_project", projectName: "Demo", path: "/tmp/demo" }).error, /unsupported fields/);

const connectionMetadataStart = source.indexOf("function postgresConnectionType");
const connectionMetadataEnd = source.indexOf("async function loadSchemaLibraryConnections", connectionMetadataStart);
const connectionContext = vm.createContext({ postgresState: { profiles: [
  { id: "local", name: "Local", host: "127.0.0.1", dbname: "demo" },
  { id: "docker", name: "Docker", host: "postgres", dbname: "app" },
  { id: "remote", name: "Reporting", host: "db.example", dbname: "reports" }
] } });
vm.runInContext(`${source.slice(connectionMetadataStart, connectionMetadataEnd)}\nthis.postgresConnectionType = postgresConnectionType; this.schemaLibraryConnection = schemaLibraryConnection;`, connectionContext);
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[0]), "Local DB");
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[1]), "Docker DB");
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[2]), "Remote DB");
assert.equal(connectionContext.schemaLibraryConnection({ projectName: "Draft" }).type, "Local project");
const linkedConnection = connectionContext.schemaLibraryConnection({ postgres: { sourceProfileId: "docker", database: "app", namespace: "public" } });
assert.equal(linkedConnection.type, "Docker DB");
assert.equal(linkedConnection.identity, "Docker (docker) · app.public");
const libraryLoader = source.slice(source.indexOf("async function loadSchemaLibraryConnections"), source.indexOf("function renderSchemaLibrary"));
assert.match(libraryLoader, /postgresProfileRepository\.list\(\)/, "schema library must load redacted saved-profile metadata through the shared repository");
assert.doesNotMatch(libraryLoader, /namespaces|password/, "opening the schema library must not contact PostgreSQL or expose credentials");

assert.match(source, /if \(!confirm\(confirmationText\)\) return;/, "write actions must require explicit confirmation");
const confirmationFlow = source.slice(source.indexOf("async function confirmAiAction"), source.indexOf("async function handleSchemiiAiOperationResult"));
assert.doesNotMatch(confirmationFlow, /type === "data_read"[\s\S]{0,400}confirm\(/, "structured reads must not ask once before the proposal-wide confirmation");
assert.doesNotMatch(confirmationFlow, /type === "migration_apply"[^\n]*&&[^\n]*confirm\(/, "destructive migration wording must be folded into the one proposal-wide confirmation");
assert.match(confirmationFlow, /confirmationText[\s\S]*type === "migration_apply" && aiActionPayload\(action\)\.destructive[\s\S]*if \(!confirm\(confirmationText\)\) return;/, "destructive AI migration apply must retain one explicit destructive confirmation");
assert.match(source, /Apply this separately reviewed PostgreSQL write/, "AI PostgreSQL writes must require a separate apply confirmation");
assert.match(source, /This preview does not write/, "AI PostgreSQL preview confirmation must not imply write authorization");
assert.match(source, /Previewed, no changes applied/, "successful write previews must not be labeled as completed writes");
assert.match(source, /Submitted \$\{result\.submittedRowCount\} row\(s\)/, "successful insert cards must show submitted rows separately from PostgreSQL command count");
assert.match(source, /if \(!confirm\("Run this generated read-only SQL query/, "every AI SQL query must require confirmation");
assert.doesNotMatch(html, /allow-session/, "session-wide AI SQL approval must not be available");
assert.match(source, /aiAccessIncludes\(context\.accessLevel, "rawread"\) && aiAccessIncludes\(elements\.aiAccessSelect\.value, "rawread"\)/, "SQL actions must require both captured and current raw-read permission");
assert.match(source, /!aiAccessIncludes\(context\.accessLevel, "rawread"\) \|\| !aiAccessIncludes\(elements\.aiAccessSelect\.value, "rawread"\)/, "query execution must reject stale raw-read permission");
assert.doesNotMatch(html, /id="ai-(?:schema|data-read|write|raw-read|raw-write)-permission"/, "Schemii must not retain browser-owned per-chat capability checkboxes");
assert.match(html, /class="ai-policy-summary"[\s\S]*data-ai="access"[^>]*type="hidden"/, "Schemii must show server policy while keeping session capability narrowing non-editable");
assert.match(shared, /disabled: "Disabled"[\s\S]*every_action: "Every action"[\s\S]*once_per_chat: "Once per chat"[\s\S]*automatic: "Automatic"/, "shared policy settings must expose every supported mode");
assert.match(source, /function syncSchemiiAiPolicy[\s\S]*settings\?\.capabilities\?\.\[name\]\?\.effectiveMode !== "disabled"/, "Schemii session capabilities must derive from effective server policy");
assert.doesNotMatch(html, /SQL policy|ai-sql-policy/, "read approval must not require a redundant SQL policy control");
assert.match(source, /postgresProfileForm\.clearPassword\(\)|postgresProfileForm\.fill\(profile\)/, "connection workflows must clear the password field through the shared form contract");
assert.match(shared, /proposalRequest\(proposal, "execute", body\)/, "confirmed proposals must execute through the server-owned operation boundary");
assert.match(source, /buildSessionPayload: \(context, accessLevel, model\) => \(\{[\s\S]*schemaId: context\.schemaId, accessLevel/, "Schemii session creation must send narrowing context fields for server-owned canonical binding");
assert.doesNotMatch(source.slice(source.indexOf("buildSessionPayload:"), source.indexOf("parseSession:", source.indexOf("buildSessionPayload:"))), /approvals|configuredMode|effectiveMode/, "session creation must not resubmit or broaden server-owned modes");
assert.doesNotMatch(source, /createSessionTitle:/, "Schemii must not create authorization-bearing session titles in the browser");
assert.doesNotMatch(source, /\^SCHEMII_CONTEXT:/, "Schemii must use application-owned chat records instead of parsing authorization from titles");
assert.match(source, /const accessLevel = Array\.isArray\(session\.capabilities\)/, "history binding must use server-owned chat capabilities");
assert.match(source, /buildMessagePayload:[\s\S]*text, model,[\s\S]*resultRef/, "message payloads must omit browser-resubmitted chat authority");
assert.match(source, /buildProposalClaimPayload: \(\) => \(\{\}\)/, "proposal execution must omit browser-resubmitted chat authority");
assert.match(shared, /policyRevision: policy\.policyRevision/, "explicit execution must bind to the proposal policy revision");
assert.doesNotMatch(shared, /confirmation:\s*\{[^}]*mode:\s*"automatic"/, "the browser must never forge automatic approval");
assert.match(shared, /proposalRequest\(proposal, "reconcile", context\)/, "lost execute responses must reconcile by proposal ID");
assert.match(shared, /response\.operation\?\.state === "uncertain"[\s\S]*proposalRequest\(proposal, "reconcile", context\)/, "an existing uncertain operation must reconcile even when execute returns HTTP 200");
assert.match(shared, /history\.pendingProposals[\s\S]*renderAction/, "history restoration must expose only server-returned pending recovery proposals");
assert.match(source, /renderServerAiProposal\(result\.applyProposal, \{ \.\.\.context, schemaSnapshot: postgresState\.schemaSnapshot \}\)/, "server-issued apply proposals must bind to the exact preview-time schema snapshot");
assert.match(source, /result\?\.kind === "postgres_write_plan"[\s\S]*renderServerAiProposal\(result\.applyProposal, context\)/, "PostgreSQL apply cards must come only from the server-issued preview result");
assert.doesNotMatch(source, /type:\s*"postgres_write_apply"/, "the browser must never synthesize a PostgreSQL apply action");
assert.match(source, /result\.schemaSync\?\.revision/, "post-apply browser refresh must require the server-owned schema synchronization revision");
assert.match(source, /result\.migrationPreview\?\.status === "ready"[\s\S]*renderServerAiProposal/, "saved schema mutations must render the server-generated migration preview proposal");
assert.match(source, /migration preview is unavailable/, "post-save preview failure must not mislabel the saved schema mutation as failed");
assert.doesNotMatch(shared, /claimProposal|completeProposal|"finalize"|"release"/, "browser proposal execution must not coordinate claims or completion");
const authUi = shared.slice(shared.indexOf("function buildAuthForm"), shared.indexOf("function renderProviders"));
assert.doesNotMatch(authUi, /localStorage|sessionStorage/, "provider credentials must not use browser storage");
assert.match(shared, /path\.startsWith\("\/api\/ai\/"\)/, "AI requests must be restricted to the local API");
assert.doesNotMatch(shared, /fetch\([^)]*(?:opencode|provider\.|8080)/i, "AI request code must not fetch external services");
const targetResolverStart = source.indexOf("function currentAiPostgresTarget");
const targetResolverEnd = source.indexOf("async function executeAiReadQuery", targetResolverStart);
const targetContext = vm.createContext({
  postgresState: { selectedProfileId: null, namespace: "", profiles: [] },
  schema: { postgres: { sourceProfileId: "tutorial", namespace: "bookstore" } }
});
vm.runInContext(`${source.slice(targetResolverStart, targetResolverEnd)}\nthis.currentAiPostgresTarget = currentAiPostgresTarget; this.completeAiPostgresTarget = completeAiPostgresTarget; this.aiSessionUsesTarget = aiSessionUsesTarget;`, targetContext);
assert.equal(targetContext.currentAiPostgresTarget().profileId, "tutorial", "AI queries must use the design's linked profile when the connection dialog has not been opened");
assert.equal(targetContext.currentAiPostgresTarget().namespace, "bookstore", "AI queries must use the design's linked namespace when the connection dialog has not been opened");
targetContext.postgresState.selectedProfileId = "reporting";
targetContext.postgresState.aiTargetExplicit = true;
targetContext.postgresState.namespace = "analytics";
targetContext.postgresState.profiles = [{ id: "reporting", dbname: "reports", contextFingerprint: "0123456789abcdef".repeat(4) }];
assert.equal(targetContext.currentAiPostgresTarget().profileId, "reporting", "an explicitly selected profile must override the linked design profile");
assert.equal(targetContext.currentAiPostgresTarget().namespace, "analytics", "an explicitly selected namespace must override the linked design namespace");
assert.equal(targetContext.currentAiPostgresTarget().profileFingerprint, "0123456789abcdef".repeat(4), "AI target binding must use the server-issued redacted profile fingerprint");
assert.equal(targetContext.aiSessionUsesTarget("schema", targetContext.completeAiPostgresTarget()), true, "schema-only chats must retain an available exact target for migration work");
targetContext.postgresState.aiTargetExplicit = false;
targetContext.schema = { projectName: "Local design" };
assert.equal(targetContext.completeAiPostgresTarget(), null, "local-only designs must not inherit a prior transient PostgreSQL target");
assert.equal(targetContext.aiSessionUsesTarget("schema", null), false, "local schema assistance must remain targetless");
assert.equal(targetContext.aiSessionUsesTarget("structured", null), false, "data permissions must never serialize a partial target");
assert.doesNotMatch(targetResolverStart >= 0 ? source.slice(targetResolverStart, targetResolverEnd) : "", /aiContextFingerprint\(\[profile\./, "the browser must not independently derive profile identity");
const queryExecutor = source.slice(source.indexOf("async function executeAiReadQuery"), source.indexOf("async function sendAiMessage"));
assert.match(queryExecutor, /currentTarget\.profileId !== context\.profileId/, "actions must recheck the effective PostgreSQL profile");
assert.match(queryExecutor, /currentTarget\.namespace !== context\.namespace/, "actions must recheck the effective PostgreSQL namespace");
assert.doesNotMatch(source.slice(source.indexOf("async function confirmAiAction"), source.indexOf("function detailAiActionError")), /profileFingerprint !==/, "browser write review must not reject a server-authorized proposal because cached profile metadata lacks its fingerprint");
assert.match(queryExecutor, /appendAiQueryResult\(result\.display\)/, "successful SQL must display the server operation result instead of model-facing JSON");
assert.match(queryExecutor, /Tool error for SQL:/, "failed SQL must be returned to the assistant for correction");
assert.match(queryExecutor, /await sendAiMessage\(text, "tool"\)/, "failed SQL feedback must continue through the bounded assistant context");
assert.match(source, /handleSchemiiAiOperationResult/, "Schemii must consume only allow-listed server operation results");
assert.match(source, /card\.querySelectorAll\("\.ai-action-error"\).*remove/, "repeated review attempts must replace prior validation errors");
assert.match(shared, /function invalidateContext[\s\S]*state\.requestGeneration \+= 1/, "application context changes must invalidate in-flight assistant responses");
assert.match(shared, /function proposalContextIsCurrent[\s\S]*contextKey\(capture, capturedAccess\) === contextKey\(currentCapture, currentAccess\)[\s\S]*if \(!proposalContextIsCurrent\(capture\)\)/, "proposal execution must reject stale application context at click time");
assert.match(shared, /operationSucceeded = true;[\s\S]*!proposalContextIsCurrent\(capture\)[\s\S]*return/, "completed shared operations must not apply their result after the application context changes");
assert.match(source, /operationSucceeded = true;[\s\S]*!aiAssistant\.proposalContextIsCurrent\(context\)[\s\S]*return/, "completed Schemii operations must not mutate a replacement design or chat context");
assert.match(shared, /control\.disabled = busy \|\| control\.dataset\.aiUnavailable === "true"/, "shared busy-state updates must preserve application-disabled permission controls");
assert.match(shared, /AI provider/, "provider status must not look like a PostgreSQL connection count");
assert.match(source, /targetCapabilities = new Set[\s\S]*targetAvailable \|\| !targetCapabilities\.has\(name\)/, "local designs must narrow target-dependent server capabilities");
const messageRenderer = source.slice(source.indexOf("function appendAiMessage"), source.indexOf("function aiActionSummary"));
assert.match(shared, /body\.textContent = value/, "chat text must render with textContent");
assert.match(shared, /function copyChatText[\s\S]*navigator\.clipboard\?\.writeText[\s\S]*document\.execCommand\?\.\("copy"\)/, "chat message copying must use the Clipboard API with a local browser fallback");
assert.match(shared, /function appendCopyControl[\s\S]*icon: "copy"[\s\S]*selectedTextWithin\(surface\)[\s\S]*setAttribute\("aria-label", "Copied"\)/, "copy surfaces must use an accessible copy icon and prefer text selected within that surface");
assert.match(shared, /appendMessageCopyControl\(body, value, role\)[\s\S]*message\.append\(label, body\)/, "the copy icon must be embedded inside the message bubble");
assert.match(sharedStyles, /\.ai-message p \{[^}]*cursor: text;[^}]*user-select: text/, "AI message text must remain directly selectable");
assert.match(sharedStyles, /\.ai-copy-button\.shared-icon-button \{[^}]*position: absolute;[^}]*opacity: 0;[^}]*pointer-events: none[^}]*\}[\s\S]*\.ai-copy-surface:hover > \.ai-copy-button/, "copy icons must stay out of the text flow and appear when their bubble is hovered");
assert.match(source, /type === "schema_read_query" \|\| type === "raw_write"[\s\S]*appendCopyControl\(sql, sqlText, "Copy SQL proposal"\)/, "SQL proposal previews must use the shared copy interaction");
assert.match(shared, /function renderProposalDiagnostic[\s\S]*Proposal not created[\s\S]*response\.proposalDiagnostics/, "rejected model actions must render a clear server-authored proposal diagnostic");
assert.match(messageRenderer, /function appendAiQueryResult/, "structured SQL results must have a dedicated chat renderer");
assert.match(messageRenderer, /aiAssistant\.appendQueryResult\(result\)/, "Schemii must reuse the shared structured result renderer");
assert.match(shared, /function appendQueryResult[\s\S]*document\.createElement\("table"\)/, "structured SQL results must render as a shared table");
assert.match(shared, /cell\.textContent = typeof value === "object" \? JSON\.stringify\(value\) : String\(value\)/, "query result cells must render as text");
assert.doesNotMatch(messageRenderer, /innerHTML/, "chat text must not render as HTML");
assert.match(html, /id="ai-provider[^" ]*"|id="ai-providers"/, "provider settings UI is missing");
assert.doesNotMatch(html, /id="ai[^\n]+value="[^\n]*(?:key|token|secret)/i, "provider secrets must not be embedded in HTML");
const panelState = source.slice(source.indexOf("const aiAssistant ="), source.indexOf("elements.tablesLayer.addEventListener"));
assert.match(panelState, /panelModal: false[\s\S]*mainLayout\.classList\.toggle\("ai-open", open\)/, "Schemii AI chat must be a non-modal workspace companion");
assert.doesNotMatch(panelState, /backgroundStates|background\.inert|background\.setAttribute\("aria-hidden"/, "opening AI must not disable the active workspace or inspector");
assert.doesNotMatch(panelState, /mobile-open|inspector-dismissed/, "AI chat must not open, dismiss, or resize the right inspector");
assert.match(sharedStyles, /\.ai-panel \{[^}]*left: 0;[^}]*translate3d\(-100%/, "AI chat must dock from the left");
assert.match(styles, /\.schema-library-connection/, "saved schema cards must display connection ownership");
assert.doesNotMatch(styles, /\.main-layout\.ai-open \.tool-rail \{[^}]*visibility: hidden/, "AI chat must preserve workspace navigation and actions");
assert.match(styles, /@media \(min-width: 701px\)[\s\S]*\.main-layout\.ai-open \.workspace \{ left: calc\(var\(--schemii-ai-edge\) \+ 8px\)[\s\S]*\.views-prototype-workspace[\s\S]*\.standalone-sql-workspace/, "Tables, Views, and SQL must share the desktop AI dock boundary");
assert.match(styles, /\.main-layout\.ai-open \.table-data-panel \{ left: 8px; \}/, "table data and its embedded SQL console must use the docked table workspace");
assert.match(styles, /@media \(max-width: 700px\)[\s\S]*\.main-layout > \.ai-panel[\s\S]*top: calc\(50vh \+ 4px\)[\s\S]*\.main-layout\.ai-open \.workspace[\s\S]*bottom: calc\(50vh \+ 6px\)/, "narrow screens must split the workspace and AI panel instead of leaving a workspace sliver");
assert.match(sharedStyles, /\.ai-query-result-scroll \{[^}]*overflow: auto/, "wide or long query results must scroll inside the chat panel");
assert.match(sharedStyles, /\.ai-query-result-table th \{[^}]*position: sticky/, "query result column headings must remain visible while scrolling");
assert.match(sharedStyles, /\.ai-context-bar \.ai-permissions, \.ai-context-bar \.ai-policy-summary \{[^}]*align-self: end/, "the policy summary must align with the model selector");
assert.match(shared, /elements\.prompt\.disabled = busy \|\| !state\.available \|\| !elements\.model\.value/, "chat input must remain disabled until a provider model is connected");
assert.match(shared, /Connect a provider in settings to start chatting/, "chat must explain how to enable a provider");
assert.match(shared, /free \? "Free access"/, "anonymous free providers must be identified accurately");
assert.match(shared, /state\.default\?\.\[item\.id\][\s\S]*defaultOption \|\| fallback/, "model selection must honor OpenCode's live default and fall back to another advertised active model");
assert.match(shared, /default: payload\.default \?\? \{\}/, "status refresh must copy OpenCode's live default model map into assistant state");
assert.match(shared, /option\.disabled = !active[\s\S]*`\$\{model\.name\} \(\$\{model\.status\}\)`/, "advertised non-active models must remain visible but unavailable");
assert.match(shared, /provider_timeout[\s\S]*provider_empty_response[\s\S]*await loadStatus\(\)/, "provider failures must refresh the dynamic model catalog");
assert.match(shared, /help\.rel = "noopener noreferrer"/, "provider key links must not control the local application window");
assert.match(shared, /getReader\(\)/, "chat must consume the local agent activity stream");
assert.match(shared, /new TextDecoder\(\)/, "agent activity must parse bounded streamed records incrementally");
const activityRenderer = shared.slice(shared.indexOf("function beginActivity"), shared.indexOf("function renderGenericAction"));
assert.match(activityRenderer, /toolLabels\[event\.tool\]/, "live tool activity must use injected local labels");
assert.match(activityRenderer, /skillLabels\[event\.skill\]/, "live skill activity must use injected local labels");
assert.match(activityRenderer, /event\.type === "part"\) setStage\("model", "Model started", "completed"\)/, "the first model output must complete the model-started stage");
assert.match(activityRenderer, /body\.textContent = part\.text/, "reasoning must render as text rather than HTML");
assert.doesNotMatch(activityRenderer, /innerHTML|insertAdjacentHTML|eval\(/, "agent visualizations must not interpret model output as code or HTML");
const proposalActivity = shared.slice(shared.indexOf("function tickProposalActivities"), shared.indexOf("function beginActivity"));
assert.match(proposalActivity, /function beginProposalOperation[\s\S]*ai-action-progress running[\s\S]*aria-busy[\s\S]*setInterval\(tickProposalActivities, 100\)/, "confirmed proposals must show a visible busy state with a live elapsed timer");
assert.match(proposalActivity, /role", "status"[\s\S]*aria-live", "polite"[\s\S]*elapsed\.setAttribute\("aria-hidden", "true"\)/, "proposal state changes must be announced without repeatedly announcing timer ticks");
assert.match(proposalActivity, /finish\(outcome = "completed"\)[\s\S]*formatDuration\(performance\.now\(\) - startedAt\)[\s\S]*Proposal failed[\s\S]*Proposal completed/, "terminal proposal visuals must retain the measured success or failure duration");
assert.match(shared, /const activity = beginProposalOperation\(card,[\s\S]*activity\.finish\("completed"\)[\s\S]*activity\.finish\(cancelled \? "cancelled" : operationSucceeded \? "warning" : "failed"\)/, "shared Schemer proposal cards must drive the operation timer through authoritative and client-sync terminal states");
assert.match(source, /executeAiReadQuery[\s\S]*beginProposalOperation\(card, \{[\s\S]*runningLabel: "Running query"[\s\S]*activity\.finish\("completed"\)[\s\S]*activity\.finish\(cancelled \? "cancelled" : operationSucceeded \? "warning" : "failed"\)/, "Schemii query proposals must show and retain query execution time");
assert.match(shared, /function cancelProposal[\s\S]*\/proposals\/\$\{encodeURIComponent\(proposal\.proposalId\)\}\/execution[\s\S]*method: "DELETE"/, "query Stop must request server-side proposal cancellation");
assert.match(shared, /cancel\.textContent = labels\.cancelLabel \|\| "Stop"[\s\S]*Cancelling query and waiting for rollback[\s\S]*finish\(outcome = "completed"\)[\s\S]*outcome === "cancelled"/, "query progress must expose an accessible Stop control and retain a terminal cancelled state");
assert.match(source, /executeAiReadQuery[\s\S]*onCancel: \(\) => aiAssistant\.cancelProposal\(proposal\)[\s\S]*isCancellationError\(error\)[\s\S]*button\.textContent = cancelled \? "Cancelled"/, "Schemii query cards must stop through the shared cancellation API without sending cancellation back as a model tool error");
assert.match(shared, /function renderCancellationRecovery[\s\S]*waitForOperation\(proposal, proposal\.operation\)[\s\S]*activity\.finish\("cancelled"\)[\s\S]*history\.pendingProposals[\s\S]*proposal\.operation\?\.cancellationRequested/, "reopened chats must recover and retain a cancellation-requested query outcome");
assert.match(shared, /session\.contextTitle[\s\S]*contextTitle[^\n]*saved/, "chat history must show the database or dashboard context beneath the conversation-specific title");
assert.match(shared, /icon: "edit"[\s\S]*ai-history-rename-form[\s\S]*\/title`[^\n]*method: "PUT"[\s\S]*binding\.title = updated\.title/, "chat history titles must support durable inline rename with immediate UI feedback");
assert.match(source, /confirmAiAction[\s\S]*beginProposalOperation\(card, \{ runningLabel: "Preparing proposal"[\s\S]*activity\.finish\("completed"\)/, "Schemii non-query proposals must use the shared running visual from preflight through completion");
assert.match(shared, /requestGeneration !== state\.requestGeneration/, "stale agent responses must not enter a reset conversation");
const newChat = shared.slice(shared.indexOf("function resetConversation"), shared.indexOf("function formatHistoryDate"));
assert.doesNotMatch(newChat, /DELETE|delete_session/, "starting a new chat must preserve the prior persistent session");
assert.match(shared, /elements\.access\.addEventListener\("change", \(\) => \{[\s\S]*resetConversation\("Access changed\./, "changing disclosure access must discard the active session binding before another message can be sent");
const historyUi = shared.slice(shared.indexOf("async function restoreSession"), shared.indexOf("const api ="));
assert.match(historyUi, /actions: \[\]/, "restored messages must not recreate historical actionable proposals");
assert.match(historyUi, /state\.sessionId = resumable \? session\.id : null/, "opening a saved chat must restore its persistent session ID only in the matching context");
assert.match(historyUi, /method: "DELETE"/, "chat history must provide explicit session deletion");
assert.doesNotMatch(historyUi, /innerHTML|insertAdjacentHTML|eval\(/, "chat history must render untrusted content as text");
assert.match(html, /id="ai-history-dialog"/, "chat history dialog is missing");
assert.match(sharedStyles, /@keyframes ai-dot-wave/, "agent progress animation is missing");
assert.match(sharedStyles, /prefers-reduced-motion[\s\S]*\.ai-progress-grid i[\s\S]*animation: none/, "agent animations must respect reduced motion");
assert.match(sharedStyles, /\.ai-action-card\.running[\s\S]*\.ai-action-progress\.running \.ai-action-progress-indicator/, "running proposals need a distinct card and operation marker");
assert.match(sharedStyles, /@keyframes ai-proposal-spin/, "the proposal operation marker animation is missing");
assert.match(sharedStyles, /prefers-reduced-motion[\s\S]*\.ai-action-progress\.running \.ai-action-progress-indicator[\s\S]*animation: none/, "proposal operation animation must respect reduced motion");

const preferenceStart = shared.indexOf("function normalizeStoredModel");
const preferenceEnd = shared.indexOf("function formatDuration", preferenceStart);
assert.notEqual(preferenceStart, -1, "AI model preference marker is missing");
const storageValues = new Map();
const preferenceContext = vm.createContext({
  localStorage: {
    getItem: key => storageValues.get(key) ?? null,
    setItem: (key, value) => storageValues.set(key, value)
  }
});
vm.runInContext(`${shared.slice(preferenceStart, preferenceEnd)}\nthis.normalizeStoredAiModel = normalizeStoredModel;`, preferenceContext);
const selectedModel = JSON.stringify({ providerId: "openai", modelId: "gpt-5.4/mini" });
assert.equal(preferenceContext.normalizeStoredAiModel(selectedModel), selectedModel, "valid model preferences must survive normalization");
assert.equal(preferenceContext.normalizeStoredAiModel(JSON.stringify({ providerId: "openai", modelId: "gpt", key: "secret" })), "", "model preference must reject credential-like extra fields");
assert.equal(preferenceContext.normalizeStoredAiModel(JSON.stringify({ providerId: "openai", modelId: "gpt\nsecret" })), "", "model preference must reject control characters");

const disclosureStart = source.indexOf("function updateAiAccessDisclosure");
const disclosureEnd = source.indexOf("const aiAssistant =", disclosureStart);
const disclosureElements = {
  aiAccessSelect: { value: "schema-structured-write-rawread-rawwrite" }, aiFunctionCaveat: { hidden: false }, aiAccessDisclosure: { textContent: "" }, aiPermissionsSummary: { textContent: "" }
};
const disclosureContext = vm.createContext({
  elements: disclosureElements,
  aiState: { settings: { capabilities: {} } },
  completeAiPostgresTarget: () => ({ profileId: "local", database: "demo", namespace: "public" }),
  aiAccessNeedsTarget: access => ["structured", "write", "rawread", "rawwrite"].some(permission => access.split("-").includes(permission)),
  postgresTargetPresentation: () => ({ label: "Linked", identity: "Demo (local) · demo.public", freshness: "Saved schema link" }),
  window: { SchemiiShared: { formatTargetPresentation: target => `${target.label}: ${target.identity} · Source: ${target.freshness}` } },
});
vm.runInContext(`${source.slice(disclosureStart, disclosureEnd)}\nthis.updateAiAccessDisclosure = updateAiAccessDisclosure;`, disclosureContext);
disclosureContext.updateAiAccessDisclosure();
assert.match(disclosureElements.aiAccessDisclosure.textContent, /schema, data read, data write, raw read, raw write/, "combined permissions must be disclosed together");
disclosureElements.aiAccessSelect.value = "schema";
disclosureContext.updateAiAccessDisclosure();
assert.equal(disclosureElements.aiFunctionCaveat.hidden, true, "leaving data-read permission must hide its query warning");

console.log("AI chat safety and action validation tests passed");
