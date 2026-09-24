import { ICONS } from "./ui.js";

const target = name => name ? ` data-quick-start-target="${name}"` : "";
const glyph = name => `<span class="qs-r-glyph">${ICONS[name]}</span>`;
const button = (label, action = "", primary = false, icon = "") => `<span class="qs-r-button${primary ? " primary" : ""}"${target(action)}>${icon ? glyph(icon) : ""}${label}</span>`;
const iconButton = (name, label, action = "") => `<span class="qs-r-icon" title="${label}"${target(action)}>${ICONS[name]}</span>`;
const input = (label, value, action = "", extra = "") => `<span class="qs-r-input ${extra}"${target(action)}><small>${label}</small><b>${value}</b></span>`;
const modal = (title, body, footer, kind = "") => `<div class="qs-r-scrim ${kind}"><section class="qs-r-dialog"><header><strong>${title}</strong>${iconButton("close", `Close ${title}`)}</header><div class="qs-r-dialog-body">${body}</div><footer>${footer}</footer></section></div>`;
const bars = (action = "") => `<div class="qs-r-chart"><div class="qs-r-axis"><span>West</span><span>East</span><span>Central</span></div><div class="qs-r-bar-set"><span style="--bar:76%"${target(action)}></span><span style="--bar:51%"></span><span style="--bar:88%"></span></div><small>Orders by region</small></div>`;
const tile = (action = "") => `<article class="qs-r-tile"${target(action)}><header><div><strong>Orders by region</strong><small>Bar chart</small></div><span>${iconButton("edit", "Edit tile")}${iconButton("sql", "SQL for tile")}</span></header>${bars()}<footer><span>3 groups cached · complete</span><span>Click to expand</span></footer></article>`;
const shell = (body, { welcome = false, tileAction = "", filterAction = "", addAction = "", overlay = "", className = "" } = {}) => `<div class="qs-r-app ${className}"><header class="qs-r-top"><span class="qs-r-brand"><span class="qs-r-brand-mark"><i></i><i></i><i></i></span><span><strong>Schemer</strong><small>ANALYTICS DASHBOARDS</small></span></span></header><div class="qs-r-workspace"><aside class="qs-r-library"><header><strong>Dashboards</strong>${iconButton("add", "Create dashboard", welcome ? "" : "")}</header><div class="qs-r-library-entry">${welcome ? "" : "Sales overview"}</div><small>Manage models in Schemoo</small></aside><main class="qs-r-main">${welcome ? `<section class="qs-r-welcome"><small>YOUR MODELS. YOUR VIEWS.</small><h1>Build a dashboard</h1><p>Curate reports and charts from one Schemoo model.</p>${button("Create dashboard", "create-dashboard", true, "add")}</section>${body}` : `<header class="qs-r-heading"><div><small>Orders model</small><strong>Sales overview</strong></div><div class="qs-r-heading-actions">${iconButton("edit", "Rename dashboard")}${iconButton("copy", "Duplicate dashboard")}${iconButton("delete", "Delete dashboard")}${iconButton("refresh", "Refresh dashboard")}${button("Filters", filterAction, false, "filter")}${button("Add tile", addAction, true, "add")}</div></header><div class="qs-r-filter-summary">No filters applied</div>${body || `<div class="qs-r-grid">${tile(tileAction)}<div class="qs-r-add-card">${ICONS.add}<strong>Add analytics tile</strong><small>Report, chart, or KPI</small></div></div>`}`}</main></div>${overlay}</div>`;

const createScene = shell(`<div class="qs-r-new-dashboard"><small>Orders model</small><strong>Sales overview</strong><p>No tiles yet. Use Add tile to create a view.</p>${button("Add tile", "", true, "add")}</div>`, {
  welcome: true,
  className: "qs-r-create",
  overlay: modal("Create dashboard", `${input("Dashboard name", '<span class="qs-r-placeholder">e.g. Workforce overview</span><span class="qs-r-filled">Sales overview</span>', "dashboard-name")}${input("Schemoo model", '<span class="qs-r-placeholder">Select model</span><span class="qs-r-selected">Orders model</span>', "schemoo-model")}<p>Every tile uses this model. Required filters become shared dashboard slicers.</p>`, button("Create dashboard", "save-dashboard", true), "qs-r-create-modal"),
});

const editorFields = `<p>Choose one or more dimensions and numeric measures. The first dimension groups the bars.</p><div class="qs-r-field-columns"><section><strong>Dimensions</strong>${input("Add dimensions", '<span class="qs-r-placeholder">Select a field</span><span class="qs-r-added">orders.region</span>', "dimension")}</section><section><strong>Measures</strong>${input("Add measures", '<span class="qs-r-placeholder">Select a field</span><span class="qs-r-added">orders.id · Count</span>', "measure")}</section></div>`;
const editorDrill = `<p>Choose the raw columns shown after selecting a chart mark. Schemer adds the clicked group and measure filters automatically.</p><section><strong>Drill-through columns</strong>${input("Add drill-through columns", '<span class="qs-r-placeholder">Select a field</span><span class="qs-r-added">orders.id</span>', "drill-column")}</section>`;
const tileScene = shell(`<div class="qs-r-grid qs-r-tile-result">${tile()}<div class="qs-r-add-card">${ICONS.add}<strong>Add analytics tile</strong><small>Report, chart, or KPI</small></div></div><div class="qs-r-empty-tile"><strong>No analytics tiles yet</strong><span>Use Add tile to build a report, chart, or KPI.</span></div>`, {
  className: "qs-r-tile-demo", addAction: "add-tile",
  overlay: modal("Configure tile", `<div class="qs-r-basics">${input("Title", '<span class="qs-r-placeholder">Name this view</span><span class="qs-r-filled">Orders by region</span>', "tile-title")}${input("Analytics view", "Bar chart ▾", "chart-type")}</div><div class="qs-r-tabs"><span class="qs-r-active-tab"${target("view-fields")}>View &amp; fields</span><span>Optional filters</span><span${target("drill-tab")}>Drill-through columns</span></div><div class="qs-r-editor-fields">${editorFields}</div><div class="qs-r-editor-drill">${editorDrill}</div>`, `${button("Cancel")}${button("Apply & run", "apply-run", true)}`, "qs-r-tile-modal"),
});

const filterScene = shell(`<section class="qs-r-filter-panel"><header><div><strong>Dashboard filters</strong><small>Shared model parameters · apply to every tile</small></div><div>${button("Choose filters", "choose-filters")}${button("Apply filters", "apply-filters")}</div></header><div class="qs-r-filter-value"><span class="qs-r-activate"${target("activate-filter")}><i>✓</i>Activate</span>${input("Region", '<span class="qs-r-placeholder">Any region</span><span class="qs-r-selected">West</span>', "filter-value")}</div><p>Filters changed. Apply to update every tile.</p></section><div class="qs-r-grid">${tile()}</div>`, {
  className: "qs-r-filters", filterAction: "filters",
  overlay: modal("Dashboard filters", `<p>Choose which optional model filters viewers may activate. Inactive filters do not affect dashboard queries.</p><label class="qs-r-filter-option"${target("region-option")}><span class="qs-r-checkbox">✓</span><span><strong>Region</strong><small>Evaluated when active and its source participates</small></span></label>`, `${button("Cancel")}${button("Save available filters", "save-filters", true)}`, "qs-r-filter-modal"),
});

const detailScene = shell(`<div class="qs-r-grid">${tile("expand-tile")}</div>`, {
  className: "qs-r-details",
  overlay: `<div class="qs-r-expanded"><section class="qs-r-expanded-chart"><header><div><strong>Orders by region</strong><small>3 groups cached · Started just now</small></div><div>${iconButton("sql", "Show tile SQL")}${iconButton("refresh", "Refresh tile")}${iconButton("close", "Close expanded tile")}</div></header><div class="qs-r-expanded-body">${bars("chart-mark")}</div><footer>3 cached · end of result <span>Download full results</span></footer></section><section class="qs-r-detail-rows"><header><div><strong>Model detail rows</strong><span class="qs-r-chip">region: West</span><small>2 cached</small></div>${iconButton("close", "Close detail rows")}</header><table><thead><tr><th>id</th><th>region</th><th>amount</th></tr></thead><tbody><tr><td>101</td><td>West</td><td>124.00</td></tr><tr><td>102</td><td>West</td><td>86.00</td></tr></tbody></table><footer>2 cached · end of result <span>Download full results</span></footer></section></div>`,
});

export const guide = {
  name: "Schemer",
  steps: [
    {
      title: "Create a dashboard from a model",
      text: "Select Create dashboard in the empty view or use the plus next to Dashboards. Name the dashboard, select a Schemoo model, then Create dashboard. Every tile in this dashboard uses that model.",
      tip: "Build and save the model in Schemoo first. Required model filters become shared dashboard slicers.",
      scene: createScene,
      states: ["create", "named", "model", "saved"],
      actions: [
        { target: "create-dashboard", label: "Create dashboard", caption: "Open the dashboard form from the welcome view.", state: "create" },
        { target: "dashboard-name", label: "Dashboard name", caption: "Give this dashboard a name.", state: "named" },
        { target: "schemoo-model", label: "Schemoo model", caption: "Select the model that every tile will use.", state: "model" },
        { target: "save-dashboard", label: "Create dashboard", caption: "Save and open the empty dashboard.", state: "saved" },
      ],
      idleText: "Watch how a dashboard gets its Schemoo model.",
      staticText: "Sales overview is open with Orders model selected.",
      completeText: "Dashboard created. Replaying without saving a dashboard…",
    },
    {
      title: "Add a bar chart with drill-through",
      text: "Use Add tile in the dashboard heading. Configure tile starts with Bar chart selected. Name it, choose a dimension and measure in View & fields, then open Drill-through columns and choose at least one raw column. Apply & run saves and runs the tile.",
      tip: "Charts need a dimension, a measure, and a drill-through column; the editor checks these before it runs.",
      scene: tileScene,
      states: ["editor", "named", "dimension", "measure", "drill", "drill-column", "applied"],
      actions: [
        { target: "add-tile", label: "Add tile", caption: "Open Configure tile from the dashboard heading.", state: "editor" },
        { target: "tile-title", label: "Title", caption: "Name the view; Bar chart is selected by default.", state: "named" },
        { target: "dimension", label: "Add dimensions", caption: "In View & fields, select a grouping dimension.", state: "dimension" },
        { target: "measure", label: "Add measures", caption: "Choose the measure and its aggregation.", state: "measure" },
        { target: "drill-tab", label: "Drill-through columns", caption: "Open the required drill-through tab.", state: "drill" },
        { target: "drill-column", label: "Add drill-through columns", caption: "Choose a raw column for detail rows.", state: "drill-column" },
        { target: "apply-run", label: "Apply & run", caption: "Save the tile and run its query.", state: "applied" },
      ],
      idleText: "Watch the tile editor fill each required field.",
      staticText: "The configured bar chart has run on the dashboard.",
      completeText: "Tile complete. Replaying without running a query…",
    },
    {
      title: "Offer and apply dashboard filters",
      text: "Open Filters from the dashboard heading. Choose filters opens the optional model filters you can offer; select one and Save available filters. Activate it, choose its value in the panel, and Apply filters to update every tile.",
      tip: "If no optional filters appear, mark a model filter optional in Schemoo first. Required model filters already appear as dashboard slicers.",
      scene: filterScene,
      states: ["panel", "manage", "selected", "saved", "activated", "value", "applied"],
      actions: [
        { target: "filters", label: "Filters", caption: "Expand the shared dashboard filter panel.", state: "panel" },
        { target: "choose-filters", label: "Choose filters", caption: "Open the optional filter chooser.", state: "manage" },
        { target: "region-option", label: "Region", caption: "Offer the model's Region filter to dashboard viewers.", state: "selected" },
        { target: "save-filters", label: "Save available filters", caption: "Save which filters are available.", state: "saved" },
        { target: "activate-filter", label: "Activate", caption: "Activate the optional Region filter for this dashboard.", state: "activated" },
        { target: "filter-value", label: "Region", caption: "Choose West as the Region value.", state: "value" },
        { target: "apply-filters", label: "Apply filters", caption: "Update all tiles with the shared filter value.", state: "applied" },
      ],
      idleText: "Watch filter availability, value, and application as separate actions.",
      staticText: "Region: West is applied to all tiles.",
      completeText: "Filters applied. Replaying without changing a dashboard…",
    },
    {
      title: "Investigate the rows behind a chart mark",
      text: "Click a tile to expand it, then click a chart bar. Schemer opens Model detail rows, filtered to the selected group and measure. These are the raw columns chosen in Drill-through columns while configuring the tile.",
      tip: "A related detail field can produce multiple rows for one contributing record, so row counts may differ from the chart value.",
      scene: detailScene,
      states: ["expanded", "detail"],
      actions: [
        { target: "expand-tile", label: "Orders by region tile", caption: "Click the tile to open the expanded chart.", state: "expanded" },
        { target: "chart-mark", label: "West bar", caption: "Click a chart mark to open its model detail rows.", state: "detail" },
      ],
      idleText: "Watch how a chart mark leads to the underlying detail rows.",
      staticText: "Model detail rows show the West group behind the chart mark.",
      completeText: "Detail explored. Replaying without running a query…",
    },
  ],
};
