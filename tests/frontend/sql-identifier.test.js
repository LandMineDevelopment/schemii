import assert from "node:assert/strict";
import test from "node:test";

import { quoteSqlIdentifier } from "../../src/schemii/schemii/web/assets/sql-identifier.js";

test("PostgreSQL identifiers preserve case and escape embedded quotes", () => {
  assert.equal(quoteSqlIdentifier("Order Items"), '"Order Items"');
  assert.equal(quoteSqlIdentifier('a"b"c'), '"a""b""c"');
  assert.equal(quoteSqlIdentifier(""), '""');
});
