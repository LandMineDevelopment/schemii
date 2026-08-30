const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const appCss = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const sharedCss = fs.readFileSync("src/schemii/shared_web/onboarding.css", "utf8");
const sharedUi = fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8");
const css = `${sharedCss}\n${appCss}`;

assert.match(appCss, /@import url\("\/shared\/onboarding\.css"\);/, "Schemii must load the shared onboarding shell");
assert.match(sharedCss, /\.onboarding-head \.eyebrow \{[^}]*color: #88733f;[^}]*font-size: 7px;[^}]*letter-spacing: \.14em;/, "Schemii and Schemer must share Quick start typography and color");
assert.match(sharedCss, /\.onboarding-opt-out \{[^}]*grid-column: auto;/, "Schemii's generic dialog label layout must not move the onboarding checkbox to its own row");
assert.match(source, /createOnboardingController\([\s\S]*storagePrefix: "schemii"/, "Schemii must use shared restart and opt-out rules");
assert.match(sharedUi, /const disabledKey = `\$\{storagePrefix\}\.onboarding\.disabled\.v1`[\s\S]*const serverKey = `\$\{storagePrefix\}\.onboarding\.server\.v1`/, "shared onboarding must own application-scoped storage keys");

assert.equal((html.match(/data-onboarding-page=/g) || []).length, 7, "the introduction should have seven pages");
assert.equal((html.match(/class="onboarding-screenshot/g) || []).length, 7, "every introduction page needs a screenshot-style preview");
assert.equal((html.match(/class="onboarding-screenshot[^>]+aria-hidden="true"/g) || []).length, 7, "synthetic tutorial controls must stay out of the accessibility tree");
assert.equal((html.match(/class="tour-demo-status"[^>]+role="status"[^>]+aria-live="polite"/g) || []).length, 7, "every changing demo status must be a polite live region");
assert.match(html, /data-onboarding-page="0"[\s\S]+Create tables and columns[\s\S]+data-onboarding-page="1"[\s\S]+Design on the canvas/, "table and column creation must be the first tutorial page");
assert.match(html, /tour-add-table-tool[\s\S]+Create a table[\s\S]+data-onboarding-target="table-name"[\s\S]+data-onboarding-target="create"[\s\S]+tour-create-inspector[\s\S]+data-onboarding-target="add-column"/, "the first page must mirror the Add table dialog and inspector column flow");
assert.match(html, /tour-create-email-editor[\s\S]+column_2[\s\S]+email[\s\S]+varchar\(255\)[\s\S]+tour-create-timestamp-editor[\s\S]+column_3[\s\S]+created_at[\s\S]+timestamptz/, "the table tutorial must show generated columns being configured with PostgreSQL types");
assert.match(source, /TABLE_CREATION_DEMO_STATES[\s\S]+target: "tool"[\s\S]+target: "table-name"[\s\S]+target: "create"[\s\S]+target: "add-column"[\s\S]+target: "email-name"[\s\S]+target: "add-column"[\s\S]+target: "created-name"/, "the first demo must create the table before adding and configuring columns");
assert.match(source, /demos: \[[\s\S]+tableCreationDemo,[\s\S]+start: startRelationshipDemo/, "the table creation demo must run before every existing tutorial");
assert.match(source, /demos: \[[\s\S]+tableCreationDemo,[\s\S]+start: startRelationshipDemo[\s\S]+start: startInspectorDemo[\s\S]+workspaceDemo[\s\S]+start: startPostgresDemo[\s\S]+start: startAssistantDemo[\s\S]+recoveryDemo/, "all seven tutorial pages must have an ordered demo controller entry");
assert.match(appCss, /\.tour-table-creation-demo\.demo-created \.tour-create-inspector \{ visibility: visible; opacity: 1;/, "creating the synthetic table must reveal its inspector");
assert.match(appCss, /@media \(max-width: 540px\)[\s\S]+\.tour-table-creation-demo \{ height: 280px; \}/, "the mobile table tutorial must leave enough height to show its generated columns");
assert.match(html, /tour-foreign-column[^>]*>[\s\S]*aria-label="Foreign key"[\s\S]*owner_id/, "the relationship source must use the real foreign-key icon on projects.owner_id");
assert.match(html, /tour-referenced-column[^>]*>[\s\S]*aria-label="Primary key"[\s\S]*id/, "the relationship target must use the real primary-key icon on users.id");
assert.match(html, /tour-relationship-desktop" d="M59 64[^>]+41 42"/, "the desktop relationship must terminate on the exact table-row edges");
assert.match(html, /tour-relationship-mobile" d="M57 85[^>]+47 56"/, "the mobile relationship must terminate on the exact table-row edges");
assert.match(html, /tour-shot-rail"><i><\/i><i><\/i><span class="tour-relationship-tool"/, "only the relationship tool should replace a placeholder toolbar box");
assert.match(html, /<circle cx="6" cy="7" r="2\.5"\/><circle cx="18" cy="17" r="2\.5"\/>/, "the introduction must use the real relationship tool icon");
assert.match(html, /click the foreign-key column first, then click the referenced primary or unique key/, "the relationship instructions must use the real click order");
assert.match(html, /data-relationship-demo-target="tool"[\s\S]+data-relationship-demo-target="target"[\s\S]+data-relationship-demo-target="source"/, "the relationship preview must expose the tool and both column targets");
assert.match(html, /id="relationship-demo-toggle"[^>]*>Pause demo<\/button>/, "the relationship demonstration needs a pause control");
assert.match(source, /RELATIONSHIP_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "source"[\s\S]+target: "target"[\s\S]+state: "editor"[\s\S]+target: "save"[\s\S]+state: "complete"/, "the relationship demonstration must select both columns, open the editor, and save");
assert.match(html, /tour-relationship-editor[\s\S]+Create relationship[\s\S]+projects → users[\s\S]+projects_owner_id_fkey[\s\S]+owner_id · uuid[\s\S]+id · uuid[\s\S]+Save relationship/, "the relationship demonstration must mirror the real creation editor");
assert.match(css, /demo-editor-open \.tour-relationship-editor-backdrop \{ visibility: visible; opacity: 1;/, "the relationship editor must appear after the referenced column is selected");
assert.match(css, /demo-source-selected \.tour-foreign-column[^}]+background: #2a2415[^}]+inset 3px 0 var\(--accent\)/, "the selected foreign-key column must match the production source highlight");
assert.match(css, /demo-relationship-complete \.tour-relationship \{ opacity: 1; \}/, "the connection must appear after both columns are selected");
assert.match(css, /\.tour-relationship path \{[^}]+stroke-dasharray: none;/, "the tutorial connection must be solid like the application connection");
assert.match(html, /Review &amp; confirm/, "the assistant preview should use the real confirmation label");
assert.match(html, /data-postgres-demo-target="tool"[\s\S]+data-postgres-demo-target="profile"[\s\S]+data-postgres-demo-target="import"[\s\S]+data-postgres-demo-target="preview"/, "the PostgreSQL demonstration must use the rail, profile, import, and preview controls");
assert.match(html, /tour-migration-preview[\s\S]+REVIEWED DATABASE CHANGE[\s\S]+Migration preview[\s\S]+0 destructive[\s\S]+CREATE TABLE[\s\S]+Review before apply/, "the PostgreSQL demonstration must show reviewed SQL and safety details");
assert.match(html, /id="postgres-demo-toggle"[^>]*>Pause demo<\/button>/, "the PostgreSQL demonstration needs a pause control");
assert.match(source, /POSTGRES_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "profile"[\s\S]+target: "import"[\s\S]+target: "preview"/, "the PostgreSQL animation must open, connect, import, and preview in order");
assert.match(source, /Preview complete\. Replaying without applying changes/, "the PostgreSQL demonstration must not imply that preview applies a migration");
const workspaceTutorialMarkup = html.slice(html.indexOf('data-onboarding-page="3"'), html.indexOf('data-onboarding-page="4"'));
assert.match(workspaceTutorialMarkup, /tour-peer-switch[\s\S]+aria-label="Tables"[\s\S]+aria-label="Views"[\s\S]+aria-label="Open SQL Console"/, "the workspace tutorial must reuse the production graphical selectors rather than text approximations");
assert.match(workspaceTutorialMarkup, /tour-peer-views-rail[\s\S]+Browse[\s\S]+Create[\s\S]+Refresh[\s\S]+Delete/, "the Views tutorial must put the production lifecycle actions in the left rail");
assert.doesNotMatch(workspaceTutorialMarkup, /Create · Replace · Recreate · Delete/, "the tutorial must not invent direct catalog lifecycle controls");
assert.match(workspaceTutorialMarkup, /data-tour-view-pane="lineage"[\s\S]+data-tour-view-role="root-query-block"[\s\S]+ROOT QUERY BLOCK[\s\S]+Outer SELECT[\s\S]+data-tour-view-role="final-view"[\s\S]+FINAL VIEW/, "the Views scene must preserve the production source-to-root-query-to-final-view boundary");
assert.match(workspaceTutorialMarkup, /data-tour-view-pane="definition"[\s\S]+REVIEWED POSTGRESQL DEFINITION[\s\S]+data-onboarding-target="preview-view"[\s\S]+Preview changes/, "Preview changes must live in the separate definition pane");
assert.match(workspaceTutorialMarkup, /data-tour-view-pane="catalog"[\s\S]+LIVE RELATION BROWSER[\s\S]+Search views[\s\S]+All[\s\S]+Views[\s\S]+Materialized/, "Browse must reveal the searchable ordinary and materialized view catalog");
assert.match(workspaceTutorialMarkup, /tour-peer-sql-rail[\s\S]+New[\s\S]+Queries[\s\S]+Save[\s\S]+Read[\s\S]+Run[\s\S]+Run all[\s\S]+Console[\s\S]+Stop/, "the SQL scene must put distinct production actions in the left rail");
assert.match(workspaceTutorialMarkup, /data-tour-sql-role="query-menu"[\s\S]+<strong>Query 1<\/strong>[\s\S]+data-tour-sql-role="exact-target"[\s\S]+CONNECTION[\s\S]+DATABASE[\s\S]+NAMESPACE/, "the SQL header must use a query menu and separate exact-target fields");
assert.match(workspaceTutorialMarkup, /data-tour-sql-pane="editor"[\s\S]+data-tour-sql-pane="results"/, "Editor and Results must be sibling SQL panes");
assert.match(workspaceTutorialMarkup, /RUNNING · STOP AVAILABLE[\s\S]+UNKNOWN · 5 ROWS · ROLLED BACK[\s\S]+Result 1[\s\S]+A Short History of Maps[\s\S]+Retained result actions do not replay SQL[\s\S]+Load more[\s\S]+Export JSON[\s\S]+Close result/, "the SQL scene must separate running cancellation from the captured completed result and its retained-result actions");
assert.doesNotMatch(workspaceTutorialMarkup, /\d+\s*ms|multiple tabs/, "the tutorial must not invent elapsed timing or a horizontal query-tab interaction");
assert.match(workspaceTutorialMarkup, /Tables, Views, and SQL workspaces[\s\S]+outer-SELECT root[\s\S]+exact profile, database, and namespace[\s\S]+Stop<\/strong> appears only while PostgreSQL is running[\s\S]+Diagnostics stay in their error result/, "the page copy must describe the real Views and SQL ownership boundaries");
assert.match(workspaceTutorialMarkup, /Managed read, all-or-nothing, explicit transaction, and autocommit\/maintenance modes[\s\S]+PostgreSQL role permissions remain authoritative/, "the shared Console modes must not imply application authorization");
assert.match(source, /WORKSPACE_DEMO_STATES = \["views", "catalog", "lineage", "definition", "sql", "queries", "query-menu", "running", "retained"\][\s\S]+target: "views"[\s\S]+target: "browse-views"[\s\S]+target: "root-query"[\s\S]+target: "definition"[\s\S]+target: "preview-view"[\s\S]+target: "sql"[\s\S]+target: "sql-queries"[\s\S]+target: "query-menu"[\s\S]+target: "run-sql"[\s\S]+state: "retained"[\s\S]+target: "retained-result"/, "the peer workspace scene must stage only reachable Views and SQL states");
assert.match(appCss, /demo-views:not\(\.demo-sql\)[^{]+\[data-onboarding-target="views"\][^{]*\{[^}]*color: #b9a7ff[^}]*background: #1d1930/, "the tutorial Views selector must use the production purple accent");
assert.match(appCss, /demo-sql \.tour-peer-switch \[data-onboarding-target="sql"\][^{]*\{[^}]*color: #9fd8ff[^}]*background: #132533/, "the tutorial SQL selector must use the production blue accent");
assert.match(appCss, /demo-catalog:not\(\.demo-sql\) \.tour-peer-catalog \{ transform: translateX\(0\); \}[\s\S]+demo-catalog:not\(\.demo-sql\) \.tour-peer-lineage-panel[^}]+right: 150px;/, "the view catalog must stay open as a right drawer through lineage and definition review");
assert.match(appCss, /demo-definition:not\(\.demo-sql\) \.tour-peer-lineage-panel \{[^}]*height: 42px;[^}]*\}[\s\S]+demo-definition:not\(\.demo-sql\) \.tour-peer-definition-panel \{[^}]*top: 42px;[^}]*height: auto;/, "definition review must collapse lineage to its header and use the remaining main pane");
assert.match(appCss, /demo-queries:not\(\.demo-query-menu\) \.tour-peer-sql-drawer \{[^}]*visibility: visible;[^}]*opacity: 1;[^}]*translateX\(0\)/, "Queries must open as a transient right drawer over the editor");
assert.match(appCss, /demo-query-menu:not\(\.demo-running\) \.tour-peer-query-menu \{[^}]*visibility: visible;[^}]*opacity: 1;/, "the browser-local query menu must attach to the View control and close before execution");
assert.match(appCss, /demo-running:not\(\.demo-retained\) \.tour-peer-sql-rail \.tour-peer-stop \{ display: grid; \}/, "Stop must be visible only during the running state");
assert.match(appCss, /\.tour-peer-sql-results \{[^}]*flex: 0 0 34px[\s\S]+demo-retained \.tour-peer-sql-editor \{ flex: 0 0 42px; \}[\s\S]+demo-retained \.tour-peer-sql-results \{ flex: 1 1 0; \}/, "completed Results must take the body while retaining the collapsed Editor header");
assert.match(html, /data-assistant-demo-target="tool"[\s\S]+data-assistant-demo-target="composer"[\s\S]+data-assistant-demo-target="send"/, "the assistant demonstration must use the rail, composer, and send controls");
assert.match(html, /tour-assistant-user[\s\S]+Create a small library schema\.[\s\S]+tour-assistant-response[\s\S]+authors, books, and loans[\s\S]+tour-assistant-proposal/, "the assistant demonstration must show a quick conversation and reviewable proposal");
assert.match(html, /id="assistant-demo-toggle"[^>]*>Pause demo<\/button>/, "the assistant demonstration needs a pause control");
assert.match(source, /ASSISTANT_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "composer"[\s\S]+typePrompt: true[\s\S]+target: "send"[\s\S]+state: "working"[\s\S]+state: "complete"/, "the assistant animation must open, type, send, work, and respond in order");
assert.match(source, /ASSISTANT_DEMO_PROMPT\.slice\(0, index\)/, "the assistant prompt must be typed progressively");
assert.match(html, /provider and model[\s\S]+separate chat history[\s\S]+Disclosure mode, capabilities, and limits[\s\S]+confirmation floors[\s\S]+separate preview from apply[\s\S]+raw SQL remains proposal-bound/, "the AI page must distinguish previewed write plans from proposal-bound raw SQL");
assert.doesNotMatch(html, /every action waits for your confirmation/i, "the tutorial must not claim one universal AI approval mode");
assert.match(css, /\.tour-assistant-panel \{[^}]+translate3d\(-100%,0,0\)[^}]+transform \.25s cubic-bezier\(\.22,1,\.36,1\)/, "the tutorial assistant must use the production left-slide transition");
assert.match(html, /Click any table to open its inspector on the right/, "page two must explain how to open the inspector");
assert.match(html, /data-tools icon in the inspector header/, "page two must explain how live data tools open relative to the inspector");
assert.match(html, /Table data[\s\S]+read-only[\s\S]+SQL console/, "page two must identify both live data views");
assert.match(html, /use maximize to temporarily cover the inspector, use minimize to close data tools/, "page two must explain data-view layout controls");
assert.doesNotMatch(html, /tour-(?:table-tag|visual-tag)|<b>[123]<\/b>/, "the animated demonstration must not retain its obsolete numbered cues");
assert.match(html, /tour-demo-playback[\s\S]+onboarding-screenshot inspector-tour-shot/, "playback annotations must sit above the animated window");
assert.match(html, /id="tour-demo-toggle"[^>]*>Pause demo<\/button>/, "the animated demonstration needs a pause control");
assert.match(html, /tour-sql-editor[^>]*>[\s\S]*SELECT \*[\s\S]*FROM "public"\."orders"[\s\S]*LIMIT 100;[\s\S]*tour-sql-actions[\s\S]*Run/, "the table SQL console must mirror its single selection-or-cursor Run action");
const tourDataMarkup = html.slice(html.indexOf('<div class="tour-data-row">'), html.indexOf('</section>', html.indexOf('<div class="tour-data-row">')));
assert.equal((tourDataMarkup.match(/<th>/g) || []).length, 5, "the Table data preview must show several columns");
assert.equal((tourDataMarkup.match(/<tr>/g) || []).length, 9, "the Table data preview must show several PostgreSQL rows plus its header");
assert.match(html, /class="tour-demo-cursor"[\s\S]*<span>Left click<\/span>/, "the demonstration needs a visible mouse and click tooltip");
assert.match(source, /const INSPECTOR_DEMO_STEPS = \[[\s\S]+target: "table", click: "Left click"/, "the demonstration must begin by clicking a table");
assert.match(source, /target: "inspector-header", click: "Left click"[\s\S]+target: "inspector-header", click: "Right click"/, "the demonstration must show both inspector-header gestures");
assert.match(source, /target: "sql-header", click: "Left click"[\s\S]+target: "sql-header", click: "Right click"/, "the demonstration must show both SQL console header gestures");
assert.match(source, /target: "data-header", click: "Left click"[\s\S]+target: "data-header", click: "Right click"/, "the demonstration must show both Table data header gestures");
for (const target of ["maximize", "minimize", "data-toggle", "inspector-close"]) {
  assert.match(source, new RegExp(`target: "${target}", click: "Left click"`), `the demonstration must use the ${target} button`);
}
assert.match(source, /prefers-reduced-motion: reduce/, "the demonstration must respect reduced-motion preferences");
assert.match(source, /staticState: "retained"[\s\S]+staticState: "conflict"/, "new synthetic scenes need useful reduced-motion end states");
assert.match(sharedUi, /top > rootBounds\.height \* \.7/, "lower controls must flip click tooltips above the cursor");
assert.match(source, /target\.matches\("\.tour-inspector-head, \.tour-data-tools header, \.tour-sql-console"\)[^\n]+classList\.add\("demo-hover"\)/, "the scripted cursor must highlight selectable headers while hovering");
assert.match(css, /\.tour-inspector-head:hover, \.tour-inspector-head\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial inspector header must use a clear hover highlight");
assert.match(css, /\.tour-data-tools header:hover, \.tour-data-tools header\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial Table data header must use a clear hover highlight");
assert.match(css, /\.tour-inspector-demo \.tour-sql-console:hover, \.tour-inspector-demo \.tour-sql-console\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial SQL console hover must override its active-pane color");
assert.match(css, /\.tour-demo-status \{[^}]+font-size: 10px;/, "the instructions above the demonstration must remain readable");
assert.match(css, /\.tour-inspector \{[^}]+height: 100%[^}]+height \.3s cubic-bezier\(\.22,1,\.36,1\)[^}]+opacity \.22s ease-out[^}]+transform \.26s cubic-bezier\(\.22,1,\.36,1\)/, "the tutorial inspector must use the production inspector transition timing");
assert.match(css, /demo-inspector-collapsed \.tour-inspector \{ height: 50px;/, "the tutorial inspector must collapse to its production-style header tile");
assert.match(css, /demo-inspector-collapsed \.tour-inspector-body[^}]+translate3d\(0,-6px,0\)[^}]+opacity \.16s ease[^}]+transform \.22s ease/, "the collapsed tutorial body must match the production fade and lift");
assert.match(css, /\.tour-data-tools \{[^}]+translate3d\(calc\(100% \+ 12px\), 0, 0\)[^}]+right \.3s cubic-bezier\(\.22,1,\.36,1\)[^}]+opacity \.2s ease[^}]+transform \.24s cubic-bezier\(\.2,\.8,\.2,1\)/, "data tools must slide in with the production transition");
assert.match(css, /\.tour-data-tools \{[^}]+calc\(39% - 24px\)/, "the data tools must sit one background-grid cell from the inspector in the desktop preview");
assert.match(css, /@media \(max-width: 540px\)[\s\S]+\.tour-data-tools \{ right: calc\(46% \+ 1px\)/, "the data tools must sit close to the inspector in the mobile preview");
assert.match(css, /\.tour-data-pane, \.tour-sql-pane[^}]+flex-basis \.34s cubic-bezier\(\.22,1,\.36,1\)/, "Table data and SQL console must exchange space with production timing");
assert.doesNotMatch(css, /\.tour-data-tools[^}]+scale\(/, "data tools must not use the old non-production scale animation");
assert.match(source, /target: "maximize"[\s\S]+state: \{ dataMaximized: true \}[\s\S]+target: "minimize"[\s\S]+state: \{ dataMaximized: false \}/, "the maximize button must restore through the production restore/minimize control");
assert.match(html, /id="onboarding-dont-show"[^>]+type="checkbox"/, "the future-start opt-out is missing");
assert.match(html, /id="onboarding-back"[^>]+aria-label="Previous introduction page"/, "the back arrow needs an accessible label");
assert.match(html, /id="show-onboarding-button"/, "the introduction must be reopenable from the Help menu");
assert.match(html, /id="shutdown-button"/, "the browser shutdown control is missing");
assert.match(css, /\.onboarding-dialog[^}]+max-height:/, "the introduction must fit within the viewport");
assert.match(css, /\.onboarding-dialog \{[^}]+height: min\(720px, calc\(100vh - 32px\)\)/, "the introduction must keep one stable height across pages");
assert.match(css, /\.onboarding-panel \{[^}]+grid-template-rows: auto minmax\(0, 1fr\) auto;[^}]+height: 100%/, "the onboarding footer must stay anchored while page content scrolls");
assert.match(css, /\.onboarding-next \{[^}]+width: 80px;/, "the Next and Finish states must not move the footer controls");
assert.match(css, /@media \(max-width: 540px\)[\s\S]+\.onboarding-dialog/, "the introduction needs mobile layout rules");
assert.doesNotMatch(sharedCss, /\.onboarding-skip\s*\{[^}]*display:\s*none/, "touch users must retain the explicit Skip action");
const recoveryTutorialMarkup = html.slice(html.indexOf('data-onboarding-page="6"'), html.indexOf('class="onboarding-footer"'));
assert.match(recoveryTutorialMarkup, /Save, move, and recover safely[\s\S]*SQL upload creates a new saved design[\s\S]*downloads are one-way local copies[\s\S]*Saved to file/, "the recovery page must mirror saved status and one-way transfer behavior");
assert.match(recoveryTutorialMarkup, /Restore examples[\s\S]*lives under Help/, "the recovery page must place example restoration under Help");
assert.match(recoveryTutorialMarkup, /semantic PostgreSQL refresh preserves established positions, colors, and per-layer viewports/, "the recovery page must preserve established layouts during semantic refresh");
assert.match(recoveryTutorialMarkup, /stale schema or layout conflict freezes autosave[\s\S]*Export local edits[\s\S]*Refresh saved design/, "the recovery page must mirror the exact conflict state and controls");
assert.match(source, /RECOVERY_DEMO_STATES[\s\S]+target: "library"[\s\S]+target: "transfer"[\s\S]+target: "drift"[\s\S]+target: "conflict"/, "the recovery scene must stage all recovery states without real operations");
const tutorialSource = [
  source.slice(source.indexOf("const TABLE_CREATION_DEMO_STATES"), source.indexOf("function tutorialStateRenderer")),
  source.slice(source.indexOf("const WORKSPACE_DEMO_STATES"), source.indexOf("const RECOVERY_DEMO_STATES")),
  source.slice(source.indexOf("const RECOVERY_DEMO_STATES"), source.indexOf("function clone(value)")),
  source.slice(source.indexOf("const RELATIONSHIP_DEMO_STEPS"), source.indexOf("const ASSISTANT_DEMO_STEPS")),
  source.slice(source.indexOf("const ASSISTANT_DEMO_STEPS"), source.indexOf("const POSTGRES_DEMO_STEPS")),
  source.slice(source.indexOf("const POSTGRES_DEMO_STEPS"), source.indexOf("const INSPECTOR_DEMO_STEPS")),
  source.slice(source.indexOf("const INSPECTOR_DEMO_STEPS"), source.indexOf("function createSchemiiOnboardingController")),
].join("\n");
assert.doesNotMatch(tutorialSource, /postgresRequest\(|sharedSessionClient\.json\(|putRecordFile\(|saveRecordFile\(/, "tutorial demos must not query PostgreSQL or mutate saved designs");

const shutdownStart = source.indexOf("async function shutdownSchemii");
const shutdownEnd = source.indexOf("function schemaForStorage");
const shutdown = source.slice(shutdownStart, shutdownEnd);
assert.ok(shutdown.indexOf("await flushPendingSave()") < shutdown.indexOf('sharedSessionClient.json("/api/shutdown"'), "shutdown must save pending edits before stopping the server");
assert.match(shutdown, /allowPath: path => path === "\/api\/shutdown"/, "shutdown must use the exact authenticated local API path");
assert.match(source, /initializeSchemaLibrary\(\)\.finally[\s\S]+initializeOnboarding\(\)/, "onboarding must initialize after the workspace");
assert.match(source, /workspace\.addEventListener\("pointerdown"[\s\S]*event\.target\.closest\("\.database-drift-banner"\)[^\n]*return;/, "database drift controls must not be captured as blank-canvas selection gestures");
assert.match(source, /refresh-database-drift"\)\.addEventListener\("click", refreshLinkedPostgresDesign\)[\s\S]*dismiss-database-drift"\)\.addEventListener\("click"[\s\S]*databaseDriftBanner\.hidden = true/, "the drift banner must retain working refresh and dismiss actions");

console.log("Onboarding and browser shutdown tests passed");
