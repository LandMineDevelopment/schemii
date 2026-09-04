/** Browser-side statement targeting for every Schemii SQL editor.
 *
 * This only decides which source text the user asked to submit. PostgreSQL
 * parsing and execution policy remain server-authoritative.
 */

function trimmedRange(source, start, end) {
  let first = start;
  let last = end;
  while (first < last && /\s/.test(source[first])) first += 1;
  while (last > first && /\s/.test(source[last - 1])) last -= 1;
  return first < last ? { start: first, end: last, sql: source.slice(first, last) } : null;
}

export function sqlStatementRanges(source) {
  const sql = String(source ?? "");
  const ranges = [];
  let start = 0;
  let quote = null;
  let escapeString = false;
  let dollarQuote = null;
  let blockDepth = 0;

  const append = end => {
    const range = trimmedRange(sql, start, end);
    if (range) ranges.push(range);
  };

  for (let index = 0; index < sql.length; index += 1) {
    const character = sql[index];
    const next = sql[index + 1] || "";
    if (dollarQuote) {
      if (sql.startsWith(dollarQuote, index)) {
        index += dollarQuote.length - 1;
        dollarQuote = null;
      }
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (next === quote) index += 1;
        else {
          quote = null;
          escapeString = false;
        }
      } else if (character === "\\" && quote === "'" && escapeString && next) {
        index += 1;
      }
      continue;
    }
    if (blockDepth) {
      if (character === "/" && next === "*") {
        blockDepth += 1;
        index += 1;
      } else if (character === "*" && next === "/") {
        blockDepth -= 1;
        index += 1;
      }
      continue;
    }
    if (character === "-" && next === "-") {
      const newline = sql.indexOf("\n", index + 2);
      index = newline === -1 ? sql.length : newline;
      continue;
    }
    if (character === "/" && next === "*") {
      blockDepth = 1;
      index += 1;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      escapeString = character === "'"
        && index > 0
        && /[eE]/.test(sql[index - 1])
        && (index < 2 || !/[\w$]/.test(sql[index - 2]));
      continue;
    }
    if (character === "$") {
      const match = sql.slice(index).match(/^\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$/);
      if (match) {
        dollarQuote = match[0];
        index += dollarQuote.length - 1;
        continue;
      }
    }
    if (character === ";") {
      append(index);
      start = index + 1;
    }
  }
  append(sql.length);
  return ranges;
}

export function sqlForRun(source, selectionStart, selectionEnd, { all = false } = {}) {
  const sql = String(source ?? "");
  if (all) return sql.trim();
  const start = Number.isInteger(selectionStart) ? selectionStart : 0;
  const end = Number.isInteger(selectionEnd) ? selectionEnd : start;
  if (start !== end) return sql.slice(Math.min(start, end), Math.max(start, end)).trim();

  const ranges = sqlStatementRanges(sql);
  const containing = ranges.find(range => start >= range.start && start <= range.end);
  if (containing) return containing.sql;
  const preceding = ranges.filter(range => range.end <= start).at(-1);
  return (preceding ?? ranges.find(range => range.start >= start))?.sql ?? "";
}

export function transactionTerminalAction(source) {
  const statements = sqlStatementRanges(source);
  if (!statements.length) return { sql: "", action: null };
  const final = statements.at(-1);
  const normalized = final.sql.replace(/\s+/g, " ").trim();
  const action = /^(?:COMMIT|END)(?: (?:WORK|TRANSACTION))?$/i.test(normalized)
    ? "commit"
    : /^ROLLBACK(?: (?:WORK|TRANSACTION))?$/i.test(normalized)
      ? "rollback"
      : null;
  if (!action) return { sql: source.trim(), action: null };
  return {
    sql: source.slice(0, final.start).replace(/;\s*$/, "").trim(),
    action,
  };
}
