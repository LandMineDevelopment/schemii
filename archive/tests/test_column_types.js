const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("const DATA_TYPES");
const end = source.indexOf(";", start) + 1;
assert.notEqual(start, -1, "DATA_TYPES declaration is missing");
assert.notEqual(end, 0, "DATA_TYPES declaration terminator is missing");

const context = vm.createContext({});
vm.runInContext(`${source.slice(start, end)} globalThis.DATA_TYPES = DATA_TYPES;`, context);

assert.equal(context.DATA_TYPES.includes("timestamp"), true);
assert.equal(context.DATA_TYPES.includes("timestamp with time zone"), true);

console.log("Column type option tests passed");
