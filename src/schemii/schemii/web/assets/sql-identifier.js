/** Quote a PostgreSQL identifier without changing its case or embedded quotes. */
export function quoteSqlIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}
