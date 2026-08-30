const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");

const inspectorMarkupStart = html.indexOf('<section class="sql-console-pane"');
const inspectorMarkupEnd = html.indexOf('<div class="canvas-hint">', inspectorMarkupStart);
const inspectorMarkup = html.slice(inspectorMarkupStart, inspectorMarkupEnd);
const dataMarkup = html.slice(html.indexOf('<section class="table-data-pane"'), inspectorMarkupStart);
assert.match(inspectorMarkup, /id="sql-console-input"[\s\S]*id="cancel-sql-console"[\s\S]*id="run-sql-console"/, "the table Console must remain one editor with cancellation and cursor execution");
assert.doesNotMatch(inspectorMarkup, /inspector-sql-(?:panes|editor-panel|result-panel|result-body)|run-all-sql-console/, "the table Console must not contain nested Script/Results components or Run all");
assert.match(dataMarkup, /id="inspector-sql-result-tabs"[\s\S]*id="table-data-scroll"/, "the existing Data view must own Console result tabs and content");

const targetStart = source.indexOf("function tableDataTarget(table)");
const executeStart = source.indexOf("async function executeSqlConsole(");
const executeEnd = source.indexOf("async function cancelSqlConsole(", executeStart);
assert.notEqual(targetStart, -1, "table target builder is missing");
assert.notEqual(executeStart, -1, "inspector Console execution is missing");
assert.notEqual(executeEnd, -1, "inspector Console execution end marker is missing");
const targetSource = source.slice(targetStart, source.indexOf("function initializeTableData", targetStart));
const executeSource = source.slice(executeStart, executeEnd);

assert.match(targetSource, /contextFingerprint[\s\S]*profileFingerprint/, "the table Console target must retain the current saved-profile fingerprint");
assert.match(executeSource, /standaloneSqlForRun\(editorSql, elements\.sqlConsoleInput\.selectionStart, elements\.sqlConsoleInput\.selectionEnd, false\)/, "Run must execute only the selection or statement under the cursor through the shared scanner");
assert.match(executeSource, /getTable\(selectedTableId\)[\s\S]*tableDataTarget\(currentTable\)[\s\S]*inspectorSqlTargetIsCurrent/, "execution must rederive and validate the currently selected table target");
assert.match(executeSource, /executeConsoleTransaction\(target,[\s\S]*mode: "managed_read"[\s\S]*settingsRevision: settings\.revision/, "the inspector must use the shared exact-target managed read transaction boundary");
assert.doesNotMatch(executeSource, /\/sql`|body: JSON\.stringify\(\{ namespace: target\.namespace, sql:/, "the inspector must not use the legacy SQL route");
assert.match(executeSource, /tableDataState\.mode = "results"[\s\S]*setTablePanelActivePane\("data"\)/, "query execution must turn the existing Data view into the Results drawer");
assert.match(executeSource, /result\.statements\.map[\s\S]*consoleId,/, "multi-statement responses must become retained inspector result tabs bound to the run-local Console identity");
assert.match(executeSource, /postgresDiagnosticText\(error\)[\s\S]*read-only transaction was rolled back/, "PostgreSQL diagnostics must remain visible with rollback state");
assert.match(executeSource, /resultTabs\.filter\(tab => tab\.statement\?\.hasMore\)\.map\(closeConsoleResultResource\)/, "a stale successful response must release every retained result instead of leaking its snapshot");

assert.match(source, /async function disposeInspectorSqlConsole[\s\S]*map\(closeConsoleResultResource\)[\s\S]*method: "DELETE"/, "target changes and Clear must dispose retained results and active execution");
assert.match(source, /function renderTableDataContent[\s\S]*mode === "results"[\s\S]*consoleResultTabContent/, "the Data view must render results through the same content renderer as the standalone Console");
assert.match(source, /tableDataScroll\.addEventListener\("click"[\s\S]*loadConsoleResultPage[\s\S]*exportConsoleResult[\s\S]*closeConsoleResultResource/, "Data-view results must support paging, export, and explicit release");
assert.match(source, /data-close-inspector-result[\s\S]*closeConsoleResultResource\(tab\)[\s\S]*resultTabs\.splice/, "Data-view result tabs must close and release retained resources like standalone result tabs");
assert.match(source, /async function clearSqlConsole[\s\S]*tableDataState\.mode = "table"/, "Clear must release Console results and restore the live Table data view");
assert.match(source, /function deactivateInspectorSqlConsole[\s\S]*tab\.kind === "loading"[\s\S]*inspectorSqlErrorTab\("Cancelled"/, "closing a running table Console must replace stale loading UI with a terminal cancelled result");
assert.match(source, /function setTableDataPanelVisible[\s\S]*tableDataPanel\.inert = true/, "a closing data-tools panel must immediately leave the interaction order");
assert.match(source, /beforeunload[\s\S]*sqlConsoleState\.executionId[\s\S]*keepalive: true/, "page exit must request cancellation for an active inspector execution");
assert.match(styles, /\.sql-console-pane \{[^}]*--accent: #f4b942[^}]*--surface-accent-hover: #211b0f/, "the unified inspector Console must retain the table area's gold accent");
assert.match(styles, /\.table-data-pane \.standalone-sql-result-tab\.active \{[^}]*var\(--accent\)/, "Data-view result tabs must use the gold table accent");
assert.match(styles, /@media \(max-width: 540px\)[\s\S]*\.sql-console-actions \{ display: grid; grid-template-columns: repeat\(2,minmax\(0,1fr\)\)/, "narrow table Console actions must remain reachable in a wrapping grid");

const targetGuardStart = source.indexOf("function inspectorSqlTargetIsCurrent");
const targetGuardEnd = source.indexOf("async function executeSqlConsole", targetGuardStart);
const context = vm.createContext({});
vm.runInContext(`${source.slice(targetGuardStart, targetGuardEnd)}\nglobalThis.targetCurrent = inspectorSqlTargetIsCurrent; globalThis.phaseControls = inspectorSqlPhaseControls;`, context);
const exactTarget = { key: "target", profileId: "profile", profileFingerprint: "fingerprint", database: "database", namespace: "public" };
assert.equal(context.targetCurrent(true, exactTarget, "target", "target"), true, "an exact visible table target may execute");
assert.equal(context.targetCurrent(false, exactTarget, "target", "target"), false, "a closed inspector Console must not execute");
assert.equal(context.targetCurrent(true, exactTarget, "other", "target"), false, "stale Table data identity must block execution");
assert.equal(context.targetCurrent(true, { ...exactTarget, profileFingerprint: null }, "target", "target"), false, "a missing profile fingerprint must block execution");
assert.deepEqual(JSON.parse(JSON.stringify(context.phaseControls("preparing"))), { busy: true, showCancel: false, cancelDisabled: true }, "Stop must stay hidden while settings are preparing");
assert.deepEqual(JSON.parse(JSON.stringify(context.phaseControls("running"))), { busy: true, showCancel: true, cancelDisabled: false }, "Stop must be available for a running execution");
assert.deepEqual(JSON.parse(JSON.stringify(context.phaseControls("cancelling"))), { busy: true, showCancel: true, cancelDisabled: true }, "Stop must remain disabled after cancellation is requested");

console.log("Inspector SQL Console contracts passed");
