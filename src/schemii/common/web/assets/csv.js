export function csvCell(value) {
  const text = value == null
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
  return `"${String(text ?? "").replaceAll('"', '""')}"`;
}

export function serializeCsv(columns, rows) {
  return [columns, ...rows].map(row => row.map(csvCell).join(",")).join("\r\n");
}
