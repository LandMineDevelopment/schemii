const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("function tableDataValue(value)");
const end = source.indexOf("function renderTableDataContent()", start);
assert.notEqual(start, -1, "table data formatter marker is missing");
assert.notEqual(end, -1, "table data formatter end marker is missing");

const context = vm.createContext({ JSON });
vm.runInContext(`${source.slice(start, end)}\nglobalThis.tableDataValue = tableDataValue;`, context);

assert.deepEqual(JSON.parse(JSON.stringify(context.tableDataValue(null))), { text: "NULL", className: "null" });
assert.deepEqual(JSON.parse(JSON.stringify(context.tableDataValue(""))), { text: "(empty)", className: "empty" });
assert.deepEqual(JSON.parse(JSON.stringify(context.tableDataValue({ ok: true }))), { text: '{"ok":true}', className: "" });
assert.deepEqual(JSON.parse(JSON.stringify(context.tableDataValue(42))), { text: "42", className: "" });

const quoteStart = source.indexOf("function quoteSqlIdentifier(value)");
const quoteEnd = source.indexOf("function initializeSqlConsole(target)", quoteStart);
assert.notEqual(quoteStart, -1, "SQL identifier quote marker is missing");
assert.notEqual(quoteEnd, -1, "SQL identifier quote end marker is missing");
vm.runInContext(`${source.slice(quoteStart, quoteEnd)}\nglobalThis.quoteSqlIdentifier = quoteSqlIdentifier;`, context);
assert.equal(context.quoteSqlIdentifier('Odd"Table'), '"Odd""Table"');

const paneStart = source.indexOf("function tablePaneVisibility(pane)");
const paneEnd = source.indexOf("function setTablePanelActivePane(", paneStart);
assert.notEqual(paneStart, -1, "table pane visibility marker is missing");
assert.notEqual(paneEnd, -1, "table pane visibility end marker is missing");
vm.runInContext(`${source.slice(paneStart, paneEnd)}\nglobalThis.tablePaneVisibility = tablePaneVisibility;`, context);
assert.deepEqual(JSON.parse(JSON.stringify(context.tablePaneVisibility("data"))), {
  activePane: "data", dataHidden: false, consoleHidden: true
});
assert.deepEqual(JSON.parse(JSON.stringify(context.tablePaneVisibility("console"))), {
  activePane: "console", dataHidden: true, consoleHidden: false
});

console.log("Table data preview tests passed");
