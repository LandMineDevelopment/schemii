import { element } from "./dom.js";

export function formatDataCell(value) {
  if (value === null) return "NULL";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function createDataGrid({ columns, rows, rowOffset = 0, className = "" }) {
  const viewport = element("div", { className: `data-grid-viewport${className ? ` ${className}` : ""}` });
  const table = element("table", { className: "data-grid" });
  const head = element("thead");
  const headRow = element("tr");
  headRow.append(element("th", { text: "#", attrs: { scope: "col" } }));
  for (const column of columns) {
    headRow.append(element("th", { title: `${column.name} · ${column.dataType}`, attrs: { scope: "col" } }, [
      element("strong", { text: column.name }),
      element("small", { text: column.dataType }),
    ]));
  }
  head.append(headRow);

  const body = element("tbody");
  rows.forEach((row, rowIndex) => {
    const tableRow = element("tr");
    tableRow.append(element("th", { text: rowOffset + rowIndex + 1, attrs: { scope: "row" } }));
    row.forEach(value => {
      const text = formatDataCell(value);
      tableRow.append(element("td", { text, title: text, className: value === null ? "is-null" : "" }));
    });
    body.append(tableRow);
  });
  table.append(head, body);
  viewport.append(table);
  return viewport;
}
