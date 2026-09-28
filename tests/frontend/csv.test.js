import assert from "node:assert/strict";
import test from "node:test";

import { serializeCsv } from "#common/csv.js";

test("CSV cells use explicit null, scalar, JSON, quoting, and line-ending rules", () => {
  const columns = ["null", "undefined", "boolean", "number", "string", "array", "object", "quote", "comma", "newline"];
  const values = [null, undefined, true, 42, "plain", [1, 2], { a: 1 }, 'A,"B"', "a,b", "first line\nsecond line"];
  const emptyRow = columns.map(() => null);

  assert.equal(serializeCsv(columns, [values, emptyRow]), [
    '"null","undefined","boolean","number","string","array","object","quote","comma","newline"',
    '"","","true","42","plain","[1,2]","{""a"":1}","A,""B""","a,b","first line\nsecond line"',
    '"","","","","","","","","",""',
  ].join("\r\n"));
});
