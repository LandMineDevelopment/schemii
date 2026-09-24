import { ICONS } from "./ui.js";

const target = name => name ? ` data-quick-start-target="${name}"` : "";
const icon = (name, label, action = "") => `<span class="qs-s-icon" data-qs-icon="${name}" title="${label}"${target(action)}>${ICONS[name]}</span>`;
const field = (label, value, action = "", className = "") => `<span class="qs-s-field ${className}"${target(action)}><small>${label}</small><b>${value}</b></span>`;
const button = (label, action = "", primary = false) => `<span class="qs-s-button${primary ? " primary" : ""}"${target(action)}>${label}</span>`;
const top = (workspace, action = "") => `<header class="qs-s-top"><span class="qs-s-brand"><span class="qs-s-brand-mark"><i></i><i></i><i></i></span><strong>Schemii</strong></span><span class="qs-s-workspace">${workspace}</span><span class="qs-s-top-actions">${icon("add", "New workspace", action === "workspace" ? "new-workspace" : "")}${icon("database", "PostgreSQL connections", action === "connection" ? "connections" : "")}${icon("workspaces", "Workspaces")}${icon("refresh", "Refresh live catalog")}${icon("save", "Save layout")}</span></header>`;
const layers = (active = "Tables", actions = []) => `<nav class="qs-s-layers">${["Tables", "Views", "SQL"].map((label, index) => `<span class="${active === label ? "active " + label.toLowerCase() : ""}"${target(actions[index])}>${ICONS[label === "Tables" ? "tables" : label === "Views" ? "views" : "sql"]}${label}</span>`).join("")}</nav>`;
const rail = items => `<aside class="qs-s-rail">${items.map(([iconName, label, action]) => icon(iconName, label, action)).join("")}</aside>`;
const shell = ({ workspace = "No workspace open", action, layer = "Tables", layerActions, tools = [], body = "", overlay = "", className = "" }) => `<div class="qs-s-app ${className}">${top(workspace, action)}<div class="qs-s-main">${layers(layer, layerActions)}${rail(tools)}<div class="qs-s-canvas">${body}</div>${overlay}</div></div>`;
const miniDialog = (eyebrow, title, content, footer = "", extra = "") => `<section class="qs-s-dialog ${extra}"><header><small>${eyebrow}</small><strong>${title}</strong></header><div class="qs-s-dialog-body">${content}</div>${footer ? `<footer>${footer}</footer>` : ""}</section>`;
const table = (name, rows, action = "", extra = "") => `<div class="qs-s-table ${extra}"${target(action)}><strong>${name}</strong>${rows.map(row => `<span>${row}</span>`).join("")}</div>`;

const connectionScene = shell({
  action: "connection",
  body: `<div class="qs-s-empty"><strong>Open a schema workspace</strong><span>Create a local design or connect to PostgreSQL.</span></div>`,
  overlay: `<div class="qs-s-connect-manager">${miniDialog("PostgreSQL", "Connections", `<div class="qs-s-manager-head"><span class="qs-s-count-empty">0 connections</span><span class="qs-s-count-saved">1 connection</span>${button("New connection", "new-connection", true)}</div><div class="qs-s-connection-item"><strong>Bookstore DB</strong><span>analyst@books.example:5432/bookstore</span>${icon("check", "Test connection", "test-connection")}<em>Saved</em></div>`, "", "qs-s-connections-dialog")}</div>
    <div class="qs-s-connect-editor">${miniDialog("PostgreSQL target", "New connection", `${field("Connection name", '<span class="qs-s-placeholder">e.g. Production</span><span class="qs-s-filled">Bookstore DB</span>', "connection-name")}${field("Host", "books.example")}${field("Port", "5432")}${field("Database", "bookstore")}${field("Username", "analyst")}${field("Password", "••••••••")}${field("SSL mode", "Prefer")}${field("Connect timeout (seconds)", "10")}`, button("Save connection", "save-connection", true), "qs-s-editor-dialog")}</div>`,
  className: "qs-s-connect",
});

const workspaceScene = shell({
  action: "workspace",
  body: `<div class="qs-s-empty"><strong>Open a schema workspace</strong><span>Keep a design locally or base it on PostgreSQL.</span></div><div class="qs-s-opened-workspace"><strong>Inventory design</strong>${table("products", ["◆ id · bigint", "name · text"], "", "accent")}</div>`,
  overlay: `<div class="qs-s-workspace-overlay">${miniDialog("Saved schema work", "Workspaces", `<div class="qs-s-workspace-form"><strong>Create workspace</strong>${field("Name", "Inventory design")}${field("Workspace type", '<span class="qs-s-local">Local design · no database</span><span class="qs-s-postgres">PostgreSQL-backed design</span>', "workspace-type")}<div class="qs-s-target-fields">${field("Connection", '<span class="qs-s-unset">Select a PostgreSQL connection</span><span class="qs-s-selected">Bookstore DB</span>', "workspace-connection")}${field("Database", "bookstore")}${field("Namespace", '<span class="qs-s-unset">Choose namespace</span><span class="qs-s-selected">public</span>', "workspace-namespace")}</div>${button("Create and open", "create-and-open", true)}</div>`, "", "qs-s-workspaces-dialog")}</div>`,
  className: "qs-s-workspace-demo",
});

const tableScene = shell({
  workspace: "Inventory design · bookstore / public",
  tools: [["tables", "Create table", "create-table"], ["relationship", "Create relationship"], ["key", "Create primary or unique key"], ["index", "Create index"], ["fit", "Fit tables"]],
  body: `<div class="qs-s-diagram">${table("orders", ["◆ id · bigint", "customer_id · bigint"], "", "existing")}${table("customers", ["◆ id · bigint", "name · text", "email · text"], "select-table", "created")}</div><div class="qs-s-inspector"><small>DESIRED TABLE</small><strong>customers</strong><span>Columns</span><div>id <em>bigint · primary</em></div><div>name <em>text</em></div><div>email <em>text</em></div></div>`,
  overlay: `<div class="qs-s-table-editor">${miniDialog("Desired schema", "Create table", `${field("Table name", '<span class="qs-s-placeholder">customers</span><span class="qs-s-filled">customers</span>', "table-name")}<div class="qs-s-column-list"><small>Columns · drag the six-dot handle to order</small><div>⠿ &nbsp; id <em>bigint · PRIMARY</em></div><div>⠿ &nbsp; name <em>text</em></div><div class="qs-s-email-column">⠿ &nbsp; email <em>text</em></div>${icon("add", "Add column", "add-column")}</div>`, button("Create table", "submit-table", true), "qs-s-create-table-dialog")}</div>`,
  className: "qs-s-table-demo",
});

const relationshipScene = shell({
  workspace: "Inventory design · bookstore / public",
  tools: [["tables", "Create table"], ["relationship", "Create relationship", "relationship-tool"], ["key", "Create primary or unique key"], ["index", "Create index"], ["fit", "Fit tables"]],
  body: `<div class="qs-s-relationship-banner"><b>Select the foreign key column</b><span class="qs-s-reference-instruction">Select the referenced key</span></div><div class="qs-s-diagram"><span class="qs-s-relationship-line"></span>${table("orders", ["◆ id · bigint", `<span${target("foreign-key")}>customer_id · bigint</span>`], "", "existing")}${table("customers", [`<span${target("referenced-key")}>◆ id · bigint</span>`, "name · text"], "inspect-table", "created")}</div><div class="qs-s-inspector qs-s-relationship-inspector"><small>TABLE INSPECTOR</small><strong>customers</strong>${icon("rows", "Open table rows and console", "table-rows")}<span>Columns · 2</span><div>id <em>bigint · primary</em></div><div>name <em>text</em></div></div><div class="qs-s-rows"><strong>Table rows</strong><div>id <em>name</em></div><div>1 <em>Ada</em></div><div>2 <em>Lin</em></div></div>`,
  overlay: `<div class="qs-s-relationship-overlay">${miniDialog("Selected columns", "Confirm relationship", `${field("Constraint name", "orders_customers_fkey")}${field("Source table", "orders")}${field("Target table", "customers")}${field("Target key", "Primary key (id)")}${field("Column mapping", "customer_id → id")}${field("On update", "NO ACTION")}${field("On delete", "NO ACTION")}`, button("Create relationship", "save-relationship", true), "qs-s-relationship-dialog")}</div>`,
  className: "qs-s-relationship-demo",
});

const sqlScene = shell({
  workspace: "Inventory design · bookstore / public",
  layerActions: ["", "views-layer", "sql-layer"],
  tools: [["views", "Browse views", "browse-views"], ["new-query", "New query"], ["history", "Saved queries and history"], ["run", "Run current statement", "run-statement"]],
  body: `<div class="qs-s-diagram qs-s-sql-initial">${table("books", ["◆ id · bigint", "title · text", "price · numeric"], "", "created")}</div><div class="qs-s-views-content"><strong>Views</strong><span>Explore the workspace's live view definitions.</span></div><div class="qs-s-view-drawer"><strong>Browse views</strong><span${target("view-item")}>book_catalog</span><span>order_summary</span></div><div class="qs-s-view-definition"><small>POSTGRESQL DEFINITION</small><strong>book_catalog</strong><code>SELECT title, price FROM books;</code></div><div class="qs-s-sql-content"><div><small>SQL Console · read-only</small><code>SELECT title, price<br>FROM books<br>ORDER BY title;</code></div><section><strong>Results</strong><span>Ready for a query</span><div class="qs-s-sql-result"><b>title</b><b>price</b><span>Atlas of Maps</span><span>18.00</span></div></section></div>`,
  className: "qs-s-sql-demo",
});

const migrationScene = shell({
  workspace: "Inventory design · bookstore / public",
  tools: [["tables", "Create table"], ["relationship", "Create relationship"], ["migration", "Review migration", "review-migration"]],
  body: `<div class="qs-s-diagram">${table("customers", ["◆ id · bigint", "name · text", "email · text"], "", "created")}</div><div class="qs-s-saved-badge">Desired design saved · PostgreSQL unchanged</div>`,
  overlay: `<div class="qs-s-migration-overlay">${miniDialog("Server-authoritative PostgreSQL change", "Review migration", `<p>Compare the saved design with its PostgreSQL baseline and current target before authorizing SQL.</p><div class="qs-s-migration-plan"><strong>Proposed changes</strong><span>ADD COLUMN customers.email text</span><code>ALTER TABLE public.customers ADD COLUMN email text;</code></div>`, `${button("Close")}${button("Refresh review", "refresh-review")}${button("Apply migration", "apply-migration", true)}`, "qs-s-migration-dialog")}</div><div class="qs-s-confirm-overlay">${miniDialog("Confirm action", "Apply migration", `<p>Apply the reviewed change to PostgreSQL? The server runs the displayed SQL in a managed transaction.</p>`, button("Apply migration", "", true), "qs-s-confirm-dialog")}</div>`,
  className: "qs-s-migration-demo",
});

export const guide = {
  name: "Schemii",
  steps: [
    {
      title: "Add a PostgreSQL connection",
      text: "Use the database icon in the top bar to open Connections. New connection collects the server address, database, and credentials; Save connection stores the profile on this Schemii server. Test the saved connection before using it.",
      tip: "If you only want to sketch a schema, you can create a local design without a connection.",
      scene: connectionScene,
      states: ["manager", "editor", "named", "saved", "tested"],
      actions: [
        { target: "connections", label: "PostgreSQL connections", caption: "Open Connections from the top bar.", state: "manager" },
        { target: "new-connection", label: "New connection", caption: "Create a reusable PostgreSQL target.", state: "editor" },
        { target: "connection-name", label: "Connection name", caption: "Enter the connection details, including its database and credentials.", state: "named" },
        { target: "save-connection", label: "Save connection", caption: "Save the connection profile on this server.", state: "saved" },
        { target: "test-connection", label: "Test connection", caption: "Test the saved target before opening a backed workspace.", state: "tested" },
      ],
      idleText: "Watch where connections are created and tested.",
      staticText: "Connections opens from the top bar; test the saved profile before using it.",
      completeText: "Connection flow complete. Replaying without contacting PostgreSQL...",
    },
    {
      title: "Create the right workspace",
      text: "New workspace opens a form that starts with Local design · no database. Choose PostgreSQL-backed design to select a saved connection and namespace, then Create and open. The database is taken from the connection and shown read-only.",
      tip: "A local design skips the connection and namespace fields. You can build a schema without a database.",
      scene: workspaceScene,
      states: ["library", "postgres", "connection", "namespace", "opened"],
      actions: [
        { target: "new-workspace", label: "New workspace", caption: "Open Workspaces from the top-bar plus button.", state: "library" },
        { target: "workspace-type", label: "Workspace type", caption: "Choose PostgreSQL-backed design instead of the local default.", state: "postgres" },
        { target: "workspace-connection", label: "Connection", caption: "Select the saved connection; its database is shown automatically.", state: "connection" },
        { target: "workspace-namespace", label: "Namespace", caption: "Choose a namespace visible to that PostgreSQL role.", state: "namespace" },
        { target: "create-and-open", label: "Create and open", caption: "Create the workspace and open its diagram.", state: "opened" },
      ],
      idleText: "Watch the local default change to a database-backed workspace.",
      staticText: "The PostgreSQL-backed workspace is open at bookstore / public.",
      completeText: "Workspace opened. Replaying without creating a saved workspace...",
    },
    {
      title: "Create and inspect a table",
      text: "Use Create table on the left tool rail. The form starts with id and name columns; name the table, adjust its columns, then Create table. Selecting the table opens its inspector for further edits.",
      tip: "The desired design is saved separately from PostgreSQL. Moving table cards updates their saved layout automatically.",
      scene: tableScene,
      states: ["form", "named", "column", "created", "inspector"],
      actions: [
        { target: "create-table", label: "Create table", caption: "Open the Create table editor from the left tool rail.", state: "form" },
        { target: "table-name", label: "Table name", caption: "Enter a name such as customers.", state: "named" },
        { target: "add-column", label: "Add column", caption: "Add and configure a column alongside the initial id and name.", state: "column" },
        { target: "submit-table", label: "Create table", caption: "Save the desired table in this workspace.", state: "created" },
        { target: "select-table", label: "customers table", caption: "Select the new table to inspect and edit its structure.", state: "inspector" },
      ],
      idleText: "Watch a table take shape in the actual design controls.",
      staticText: "The customers table is selected with its columns in the inspector.",
      completeText: "Table created. Replaying without changing the saved design...",
    },
    {
      title: "Link tables and inspect live rows",
      text: "Choose Create relationship from the left rail, select the foreign key column, then the referenced key. Review the mapping and actions in Confirm relationship, then Create relationship. Select a table card to inspect its structure and open live rows.",
      tip: "The banner tells you which column to select next. Fit tables restores the full diagram view.",
      scene: relationshipScene,
      states: ["tool", "foreign", "confirm", "linked", "inspector", "rows"],
      actions: [
        { target: "relationship-tool", label: "Create relationship", caption: "Start relationship authoring from the left tool rail.", state: "tool" },
        { target: "foreign-key", label: "orders.customer_id", caption: "Choose the foreign key column first.", state: "foreign" },
        { target: "referenced-key", label: "customers.id", caption: "Choose the referenced key to open the confirmation form.", state: "confirm" },
        { target: "save-relationship", label: "Create relationship", caption: "Review the column mapping and actions, then create the relationship.", state: "linked" },
        { target: "inspect-table", label: "customers table", caption: "Select a table card to open its inspector.", state: "inspector" },
        { target: "table-rows", label: "Open table rows and console", caption: "Open live table rows in the data tools pane.", state: "rows" },
      ],
      idleText: "Watch the foreign key selection order and table inspection.",
      staticText: "The relationship is drawn, with live rows open below the diagram.",
      completeText: "Diagram flow complete. Replaying without editing the schema...",
    },
    {
      title: "Browse views, then run SQL",
      text: "The Tables, Views, and SQL selector is centered above the workspace. In Views, use Browse views on the left rail to inspect definitions. In SQL, write a query and use Run current statement; results appear in the Results pane.",
      tip: "SQL runs against the open PostgreSQL-backed workspace, so check its connection and query mode first.",
      scene: sqlScene,
      states: ["views", "browse", "definition", "sql", "results"],
      actions: [
        { target: "views-layer", label: "Views", caption: "Switch from Tables to the Views workspace.", state: "views" },
        { target: "browse-views", label: "Browse views", caption: "Open the searchable live view catalog.", state: "browse" },
        { target: "view-item", label: "book_catalog", caption: "Select a view to read its definition.", state: "definition" },
        { target: "sql-layer", label: "SQL", caption: "Switch to the SQL workspace for the same target.", state: "sql" },
        { target: "run-statement", label: "Run current statement", caption: "Run the current query and inspect its retained result.", state: "results" },
      ],
      idleText: "Watch the peer workspace selector, view catalog, and SQL results.",
      staticText: "The SQL result is open after browsing a view definition.",
      completeText: "Views and SQL reviewed. Replaying without running a query...",
    },
    {
      title: "Review before changing PostgreSQL",
      text: "Design edits save to Schemii metadata; they do not immediately change PostgreSQL. Use Review migration to compare the saved design with its baseline and current target, inspect the proposed SQL, then authorize Apply migration only when it is correct.",
      tip: "Apply migration opens a separate confirmation and requires the database permissions for the change.",
      scene: migrationScene,
      states: ["review", "refreshed", "confirm"],
      actions: [
        { target: "review-migration", label: "Review migration", caption: "Open the review from the left tool rail.", state: "review" },
        { target: "refresh-review", label: "Refresh review", caption: "Check the current target and the exact proposed SQL again.", state: "refreshed" },
        { target: "apply-migration", label: "Apply migration", caption: "Only after review, request the separate apply confirmation.", state: "confirm" },
      ],
      idleText: "Watch where saved design becomes a reviewed PostgreSQL change.",
      staticText: "Apply migration asks for explicit confirmation after the review.",
      completeText: "Review complete. Replaying without applying SQL...",
    },
  ],
};
