import { element, emptyPanel, errorPanel, replace } from "#common/dom.js";
import { createDataGrid } from "#common/data-grid.js";

const LABELS = {
  table: "Tables",
  partitioned_table: "Partitioned tables",
  view: "Views",
  materialized_view: "Materialized views",
};

export function renderRelationBrowser(container, { response = null, loading = false, error = null, onOpen, onPreview }) {
  replace(container);
  if (loading) {
    container.append(element("div", { className: "relation-browser-state", attrs: { role: "status" } }, [
      element("span", { className: "relation-browser-spinner" }),
      element("strong", { text: "Reading PostgreSQL relations…" }),
    ]));
    return { shown: 0, matching: 0 };
  }
  if (error) {
    container.append(errorPanel(error));
    return { shown: 0, matching: 0 };
  }
  const relations = response?.relations || [];
  if (!relations.length) {
    container.append(emptyPanel("0", "No matching relations", "No live table or view matches this search."));
    return { shown: 0, matching: 0 };
  }
  for (const [kind, label] of Object.entries(LABELS)) {
    const matches = relations.filter(item => item.kind === kind);
    if (!matches.length) continue;
    const section = element("section", { className: "relation-browser-group" });
    section.append(element("header", {}, [
      element("strong", { text: label }),
      element("span", { text: String(matches.length) }),
    ]));
    const list = element("div", { className: "relation-browser-items" });
    for (const relation of matches) {
      const row = element("article", { className: "relation-browser-item" });
      const open = element("button", { className: "relation-browser-open", type: "button", title: relation.name });
      open.append(
        element("span", { className: "relation-kind-mark", text: kind.includes("view") ? "V" : "T" }),
        element("span", {}, [
          element("strong", { text: relation.name }),
          element("small", { text: `${relation.columnCount} ${relation.columnCount === 1 ? "column" : "columns"}` }),
        ]),
      );
      open.addEventListener("click", () => onOpen(relation));
      const preview = element("button", { className: "ui-button compact relation-preview-action", type: "button", text: "Preview rows" });
      preview.addEventListener("click", () => onPreview(relation));
      row.append(open, preview);
      list.append(row);
    }
    section.append(list);
    container.append(section);
  }
  return { shown: relations.length, matching: relations.length };
}

export function renderRelationRows(container, { page = null, loading = false, error = null }) {
  replace(container);
  if (loading) {
    container.append(element("div", { className: "relation-preview-state", attrs: { role: "status" } }, [element("strong", { text: "Reading a bounded live snapshot…" })]));
    return;
  }
  if (error) {
    container.append(errorPanel(error));
    return;
  }
  if (!page) return;
  if (!page.rows.length) {
    container.append(emptyPanel("0", "No rows", "PostgreSQL returned an empty relation."));
    return;
  }
  container.append(createDataGrid({ columns: page.columns, rows: page.rows, className: "relation-preview-viewport" }));
}

export function pagedRowsStatus(page, { loading = false, pagingError = null } = {}) {
  if (!page) return loading ? "Loading rows…" : "Up to 100 rows";
  const count = `${page.rows.length} rows · ${page.columns.length} columns`;
  if (loading) return `${count} · loading more…`;
  if (pagingError) return `${count} · more rows could not load · refresh to retry`;
  if (page.nextCursor) return `${count} · scroll for more`;
  return count;
}
