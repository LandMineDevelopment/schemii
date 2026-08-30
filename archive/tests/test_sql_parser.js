const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("const SQL_IDENTIFIER_PART");
const end = source.indexOf("async function importSqlFile");
const generateStart = source.indexOf("function generateSql()");
const generateEnd = source.indexOf("const aiAssistant =", generateStart);
assert.notEqual(start, -1, "SQL parser start marker is missing");
assert.notEqual(end, -1, "SQL parser end marker is missing");
assert.notEqual(generateStart, -1, "SQL generator start marker is missing");
assert.notEqual(generateEnd, -1, "SQL generator end marker is missing");

const context = vm.createContext({});
vm.runInContext(`
  const COLORS = ["#f4b942"];
  let nextId = 0;
  function uid(prefix) { nextId += 1; return prefix + "_" + nextId; }
  ${source.slice(start, end)}
  globalThis.parseSqlSchema = parseSqlSchema;
  globalThis.splitStatements = sql => splitSqlAtTopLevel(stripSqlComments(sql), ";");
`, context);

const sql = `
  -- This comment should be removed before parsing.
  CREATE TABLE accounts (
    tenant_id uuid NOT NULL,
    account_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, account_id)
  );

  CREATE TABLE jobs (
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    account_id uuid NOT NULL,
    created_at timestamp NOT NULL DEFAULT now()
  );

  ALTER TABLE jobs ADD CONSTRAINT jobs_pk PRIMARY KEY (tenant_id, job_id);
  ALTER TABLE jobs ADD CONSTRAINT jobs_tenant_created UNIQUE (tenant_id, created_at);
  ALTER TABLE jobs ADD CONSTRAINT jobs_account_fk
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);

  CREATE OR REPLACE FUNCTION run_job()
  RETURNS void
  LANGUAGE plpgsql
  AS $body$
  BEGIN
    PERFORM 1; -- This is function text, not a top-level SQL comment.
    PERFORM 2;
  END;
  $body$;
`;

const statements = context.splitStatements(sql);
assert.equal(statements.length, 6, "dollar-quoted function bodies must remain one statement");

const schema = context.parseSqlSchema(sql, "Parser test");
assert.equal(schema.tables.length, 2);
assert.equal(schema.functions.length, 1);
assert.match(schema.functions[0].definition, /PERFORM 1;/);
assert.match(schema.functions[0].definition, /PERFORM 2;/);
const jobs = schema.tables.find(table => table.name === "jobs");
assert.deepEqual(Array.from(jobs.columns.filter(column => column.primary), column => column.name), ["tenant_id", "job_id"]);
assert.deepEqual(Array.from(jobs.columns.filter(column => column.primary), column => column.unique), [false, false]);
assert.equal(jobs.columns.find(column => column.name === "created_at").default, "now()");
assert.equal(jobs.uniqueConstraints.length, 1);
assert.equal(schema.relationships.length, 1);
assert.equal(schema.relationships[0].fromColumnIds.length, 2);
assert.equal(schema.relationships[0].toColumnIds.length, 2);
schema.relationships[0].name = "jobs_account_fk";
schema.relationships[0].constraintName = "jobs_account_fk";

context.schema = schema;
vm.runInContext(`
  function sqlName(value) {
    return /^[a-z_][a-z0-9_]*$/i.test(value) ? value : '"' + value.replaceAll('"', '""') + '"';
  }
  function getTable(tableId) { return schema.tables.find(table => table.id === tableId); }
  function getColumn(tableId, columnId) { return getTable(tableId)?.columns.find(column => column.id === columnId); }
  function defaultPrimaryKeyName(tableName) { return tableName + "_pkey"; }
  function availableUniqueConstraintName(table, columnIds) {
    const names = columnIds.map(columnId => table.columns.find(column => column.id === columnId).name);
    return table.name + "_" + names.join("_") + "_key";
  }
  function relationshipColumnPairs(relationship) {
    const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
    const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
    return fromColumnIds.map((fromColumnId, index) => ({ fromColumnId, toColumnId: toColumnIds[index] }));
  }
  ${source.slice(generateStart, generateEnd)}
  globalThis.generateSql = generateSql;
`, context);

const generatedSql = context.generateSql();
assert.match(generatedSql, /PRIMARY KEY \(tenant_id, account_id\)/);
assert.match(generatedSql, /PRIMARY KEY \(tenant_id, job_id\)/);
assert.match(generatedSql, /UNIQUE \(tenant_id, created_at\)/);
assert.match(generatedSql, /CONSTRAINT jobs_account_fk FOREIGN KEY/);
assert.match(generatedSql, /FOREIGN KEY \(tenant_id, account_id\) REFERENCES accounts \(tenant_id, account_id\)/);
assert.doesNotMatch(generatedSql, /tenant_id UUID PRIMARY KEY/);

const roundTripped = context.parseSqlSchema(generatedSql, "Round trip");
assert.equal(roundTripped.relationships.length, 1);
assert.equal(roundTripped.relationships[0].fromColumnIds.length, 2);
assert.equal(roundTripped.relationships[0].toColumnIds.length, 2);

console.log("SQL parser tests passed");
