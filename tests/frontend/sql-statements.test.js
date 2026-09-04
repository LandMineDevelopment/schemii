import assert from "node:assert/strict";
import test from "node:test";

import {
  sqlForRun,
  sqlStatementRanges,
  transactionTerminalAction,
} from "../../src/schemii/schemii/web/assets/sql-statements.js";

test("statement ranges ignore semicolons inside PostgreSQL strings, identifiers, and comments", () => {
  const sql = `SELECT ';' AS value, "semi;column";
-- not; a statement boundary
SELECT E'escaped\\';value';
/* outer; /* inner; */ still outer; */ SELECT 3;`;

  assert.deepEqual(
    sqlStatementRanges(sql).map(range => range.sql),
    [
      `SELECT ';' AS value, "semi;column"`,
      `-- not; a statement boundary\nSELECT E'escaped\\';value'`,
      `/* outer; /* inner; */ still outer; */ SELECT 3`,
    ],
  );
});

test("statement ranges retain dollar-quoted routine bodies as one statement", () => {
  const sql = `CREATE FUNCTION demo() RETURNS void AS $body$
BEGIN
  PERFORM 1;
END;
$body$ LANGUAGE plpgsql;
SELECT 2;`;
  assert.equal(sqlStatementRanges(sql).length, 2);
  assert.match(sqlStatementRanges(sql)[0].sql, /PERFORM 1;/);
});

test("selection wins, cursor targets one statement, and run all is explicit", () => {
  const sql = "SELECT 1;\n\nSELECT 2;\nSELECT 3;";
  assert.equal(sqlForRun(sql, 2, 8), "LECT 1");
  assert.equal(sqlForRun(sql, sql.indexOf("2"), sql.indexOf("2")), "SELECT 2");
  assert.equal(sqlForRun(sql, sql.indexOf("\n\n") + 1, sql.indexOf("\n\n") + 1), "SELECT 1");
  assert.equal(sqlForRun(sql, 0, 0, { all: true }), sql);
});

test("terminal commit or rollback is separated from preceding transaction work", () => {
  assert.deepEqual(transactionTerminalAction("UPDATE jobs SET ready = true; COMMIT;"), {
    sql: "UPDATE jobs SET ready = true",
    action: "commit",
  });
  assert.deepEqual(transactionTerminalAction("ROLLBACK TRANSACTION;"), {
    sql: "",
    action: "rollback",
  });
  assert.deepEqual(transactionTerminalAction("ROLLBACK TO SAVEPOINT checkpoint;"), {
    sql: "ROLLBACK TO SAVEPOINT checkpoint;",
    action: null,
  });
});
