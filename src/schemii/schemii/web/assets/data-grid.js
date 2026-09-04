import { element } from "./dom.js";

export function formatDataCell(value) {
  if (value === null) return "NULL";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function createDataRow(row, rowNumber) {
  const tableRow = element("tr");
  tableRow.append(element("th", { text: rowNumber, attrs: { scope: "row" } }));
  row.forEach(value => {
    const text = formatDataCell(value);
    tableRow.append(element("td", { text, title: text, className: value === null ? "is-null" : "" }));
  });
  return tableRow;
}

export function appendDataGridRows(viewport, rows, { rowOffset = 0 } = {}) {
  const body = viewport?.querySelector("tbody");
  if (!body) return 0;
  const fragment = viewport.ownerDocument.createDocumentFragment();
  rows.forEach((row, rowIndex) => fragment.append(createDataRow(row, rowOffset + rowIndex + 1)));
  body.append(fragment);
  return rows.length;
}

export function mergeDataGridPages(currentPage, nextPage) {
  if (!currentPage) return nextPage;
  if (!nextPage) return currentPage;
  return {
    ...nextPage,
    columns: nextPage.columns?.length ? nextPage.columns : currentPage.columns,
    rows: [...currentPage.rows, ...nextPage.rows],
  };
}

export function appendDataGridPage(viewport, currentPage, nextPage) {
  const page = mergeDataGridPages(currentPage, nextPage);
  const expected = nextPage?.rows?.length || 0;
  const appended = expected > 0
    && appendDataGridRows(viewport, nextPage.rows, { rowOffset: currentPage?.rows?.length || 0 }) === expected;
  return { page, appended };
}

export function isNearScrollEnd(container, threshold = 120) {
  if (!container) return false;
  return container.scrollHeight - container.scrollTop - container.clientHeight <= threshold;
}

export function installAutoPageLoader({
  container,
  canLoad,
  loadNext,
  threshold = 120,
  schedule = callback => {
    if (globalThis.requestAnimationFrame) globalThis.requestAnimationFrame(callback);
    else globalThis.queueMicrotask(callback);
  },
}) {
  if (!container || typeof canLoad !== "function" || typeof loadNext !== "function") {
    throw new TypeError("Automatic paging requires a scroll container and paging callbacks");
  }
  let destroyed = false;
  let loading = false;
  let scheduled = false;

  const check = async () => {
    scheduled = false;
    if (destroyed || loading || !canLoad() || !isNearScrollEnd(container, threshold)) return;
    loading = true;
    container.setAttribute("aria-busy", "true");
    try {
      await loadNext();
    } finally {
      loading = false;
      container.removeAttribute("aria-busy");
      if (canLoad() && isNearScrollEnd(container, threshold)) queueCheck();
    }
  };
  const queueCheck = () => {
    if (destroyed || scheduled) return;
    scheduled = true;
    schedule(check);
  };
  container.addEventListener("scroll", queueCheck, { passive: true });

  return Object.freeze({
    check: queueCheck,
    destroy() {
      destroyed = true;
      container.removeEventListener("scroll", queueCheck);
      container.removeAttribute("aria-busy");
    },
  });
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
  rows.forEach((row, rowIndex) => body.append(createDataRow(row, rowOffset + rowIndex + 1)));
  table.append(head, body);
  viewport.append(table);
  return viewport;
}
