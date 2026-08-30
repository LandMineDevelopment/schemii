const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("function formatDatabaseObjectDefinition(definition, kind)");
const end = source.indexOf("function updateDatabaseObjectTableState()", start);
assert.notEqual(start, -1, "formatter marker is missing");
assert.notEqual(end, -1, "formatter end marker is missing");

const context = vm.createContext({});
vm.runInContext(`${source.slice(start, end)}\nglobalThis.formatDatabaseObjectDefinition = formatDatabaseObjectDefinition;`, context);

const oneLine = "CREATE TRIGGER accounts_set_updated_at BEFORE UPDATE ON public.accounts FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()";
assert.equal(context.formatDatabaseObjectDefinition(oneLine, "trigger"), [
  "CREATE TRIGGER accounts_set_updated_at",
  "BEFORE UPDATE ON public.accounts",
  "FOR EACH ROW",
  "EXECUTE FUNCTION public.set_updated_at()",
].join("\n"));

const formatted = "CREATE TRIGGER audit\nAFTER INSERT ON public.events\nFOR EACH ROW\nEXECUTE FUNCTION public.audit()";
assert.equal(context.formatDatabaseObjectDefinition(formatted, "trigger"), formatted);
assert.equal(context.formatDatabaseObjectDefinition(oneLine, "view"), oneLine);

console.log("Trigger formatter tests passed");
