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
const bookstoreWorkspace = "schemii_test.bookstore";

const connectionScene = shell({
  action: "connection",
  body: `<div class="qs-s-empty"><strong>Open a schema workspace</strong><span>Create a local design or connect to PostgreSQL.</span></div>`,
  overlay: `<div class="qs-s-connect-manager">${miniDialog("PostgreSQL", "Connections", `<div class="qs-s-manager-head"><span class="qs-s-count-empty">0 connections</span><span class="qs-s-count-saved">1 connection</span>${button("New connection", "new-connection", true)}</div><div class="qs-s-connection-item"><strong>Bookstore DB</strong><span>schemii@postgres:5432/schemii_test</span>${icon("check", "Test connection", "test-connection")}<em>Saved</em></div>`, "", "qs-s-connections-dialog")}</div>
    <div class="qs-s-connect-editor">${miniDialog("PostgreSQL target", "New connection", `${field("Connection name", '<span class="qs-s-placeholder">e.g. Production</span><span class="qs-s-filled">Bookstore DB</span>', "connection-name")}${field("Host", "postgres")}${field("Port", "5432")}${field("Database", "schemii_test")}${field("Username", "schemii")}${field("Password", "••••••••")}${field("SSL mode", "Disable")}${field("Connect timeout (seconds)", "10")}`, button("Save connection", "save-connection", true), "qs-s-editor-dialog")}</div>`,
  className: "qs-s-connect",
});

const workspaceScene = shell({
  action: "workspace",
  body: `<div class="qs-s-empty"><strong>Open a schema workspace</strong><span>Keep a design locally or base it on PostgreSQL.</span></div><div class="qs-s-opened-workspace"><strong>${bookstoreWorkspace}</strong>${table("customers", ["◆ id · bigint", "full_name · varchar", "email · varchar"], "", "accent")}${table("books", ["◆ id · bigint", "title · varchar", "price · numeric"], "", "accent")}</div>`,
  overlay: `<div class="qs-s-workspace-overlay">${miniDialog("Saved schema work", "Workspaces", `<div class="qs-s-workspace-form"><strong>Create workspace</strong>${field("Name", "Inventory design", "", "qs-s-workspace-name")}${field("Workspace type", '<span class="qs-s-local">Local design · no database</span><span class="qs-s-postgres">PostgreSQL-backed design</span>', "workspace-type")}<div class="qs-s-target-fields">${field("Connection", '<span class="qs-s-unset">Select a PostgreSQL connection</span><span class="qs-s-selected">Bookstore DB</span>', "workspace-connection")}${field("Database", "schemii_test")}${field("Namespace", '<span class="qs-s-unset">Choose namespace</span><span class="qs-s-selected">bookstore</span>', "workspace-namespace")}</div><span class="qs-s-button primary"${target("open-database-schema")}><span class="qs-s-local-submit">Create empty design</span><span class="qs-s-postgres-submit">Open database schema</span></span></div>`, "", "qs-s-workspaces-dialog")}</div>`,
  className: "qs-s-workspace-demo",
});

const tableScene = shell({
  workspace: bookstoreWorkspace,
  tools: [["tables", "Create table", "create-table"], ["relationship", "Create relationship"], ["key", "Create primary or unique key"], ["index", "Create index"], ["fit", "Fit tables"]],
  body: `<div class="qs-s-diagram">${table("customers", ["◆ id · bigint", "full_name · varchar", "email · varchar"], "", "existing")}${table("wishlists", ["◆ id · bigint", "name · text", "customer_id · bigint"], "select-table", "created")}</div><div class="qs-s-inspector"><small>DESIRED TABLE</small><strong>wishlists</strong><span>Columns</span><div>id <em>bigint · primary</em></div><div>name <em>text</em></div><div>customer_id <em>bigint</em></div></div>`,
  overlay: `<div class="qs-s-table-editor">${miniDialog("Desired schema", "Create table", `${field("Table name", '<span class="qs-s-placeholder">wishlists</span><span class="qs-s-filled">wishlists</span>', "table-name")}<div class="qs-s-column-list"><small>Columns · drag the six-dot handle to order</small><div>⠿ &nbsp; id <em>bigint · PRIMARY</em></div><div>⠿ &nbsp; name <em>text</em></div><div class="qs-s-added-column">⠿ &nbsp; customer_id <em>bigint</em></div>${icon("add", "Add column", "add-column")}</div>`, button("Create table", "submit-table", true), "qs-s-create-table-dialog")}</div>`,
  className: "qs-s-table-demo",
});

const relationshipScene = shell({
  workspace: bookstoreWorkspace,
  tools: [["tables", "Create table"], ["relationship", "Create relationship", "relationship-tool"], ["key", "Create primary or unique key"], ["index", "Create index"], ["fit", "Fit tables"]],
  body: `<div class="qs-s-relationship-banner"><b>Select the foreign key column</b><span class="qs-s-reference-instruction">Select the referenced key</span></div><div class="qs-s-diagram">${table("wishlists", ["◆ id · bigint", "name · text", `<span${target("foreign-key")}>customer_id · bigint</span>`], "", "created")}<span class="qs-s-relationship-line" aria-hidden="true"></span>${table("customers", [`<span${target("referenced-key")}>◆ id · bigint</span>`, "full_name · varchar", "email · varchar"], "inspect-table", "existing")}</div><div class="qs-s-inspector qs-s-relationship-inspector"><small>TABLE INSPECTOR</small><strong>customers</strong>${icon("rows", "Open table rows and console", "table-rows")}<span>Columns · 5</span><div>id <em>bigint · primary</em></div><div>full_name <em>varchar</em></div><div>email <em>varchar</em></div></div><div class="qs-s-rows"><strong>Live customers rows</strong><div>id <em>full_name</em></div><div>1 <em>Alex Morgan</em></div><div>2 <em>Sam Rivera</em></div></div>`,
  overlay: `<div class="qs-s-relationship-overlay">${miniDialog("Selected columns", "Confirm relationship", `${field("Constraint name", "wishlists_customers_fkey")}${field("Source table", "wishlists")}${field("Target table", "customers")}${field("Target key", "Primary key (id)")}${field("Column mapping", "customer_id → id")}${field("On update", "NO ACTION")}${field("On delete", "NO ACTION")}`, button("Create relationship", "save-relationship", true), "qs-s-relationship-dialog")}</div>`,
  className: "qs-s-relationship-demo",
});

const sqlScene = shell({
  workspace: bookstoreWorkspace,
  layerActions: ["", "views-layer", "sql-layer"],
  tools: [["views", "Browse views", "browse-views"], ["new-query", "New query"], ["history", "Saved queries and history"], ["run", "Run current statement", "run-statement"]],
  body: `<div class="qs-s-diagram qs-s-sql-initial">${table("books", ["◆ id · bigint", "title · varchar", "price · numeric"], "", "created")}</div><div class="qs-s-view-list"><strong>Views</strong><span class="qs-s-view-search">Search views</span><span${target("view-item")}>book_catalog</span><span>order_summary</span></div><div class="qs-s-view-definition"><small>POSTGRESQL DEFINITION</small><strong>book_catalog</strong><code>SELECT books.title, books.price, …<br>FROM bookstore.books …</code></div><div class="qs-s-sql-content"><div><small>SQL Console · read-only</small><code>SELECT title, price<br>FROM bookstore.books<br>ORDER BY title LIMIT 2;</code></div><section><strong>Results</strong><span>Ready for a query</span><div class="qs-s-sql-result"><b>title</b><b>price</b><span>A Short History of Maps</span><span>28.00</span></div></section></div>`,
  className: "qs-s-sql-demo",
});

const migrationScene = shell({
  workspace: bookstoreWorkspace,
  tools: [["tables", "Create table"], ["relationship", "Create relationship"], ["migration", "Review migration", "review-migration"]],
  body: `<div class="qs-s-diagram">${table("wishlists", ["◆ id · bigint", "name · text", "customer_id · bigint"], "", "created")}${table("customers", ["◆ id · bigint", "full_name · varchar"], "", "existing")}</div><div class="qs-s-saved-badge">Wishlists saved in design · PostgreSQL unchanged</div>`,
  overlay: `<div class="qs-s-migration-overlay">${miniDialog("Server-authoritative PostgreSQL change", "Review migration", `<p>Review the saved changes against the live PostgreSQL target before authorizing SQL.</p><div class="qs-s-migration-plan"><strong>Ordered PostgreSQL changes</strong><div class="qs-s-migration-change"${target("migration-table-change")}>01 · Create table wishlists <span>⌄</span></div><code class="qs-s-create-sql">CREATE TABLE "bookstore"."wishlists" ("id" bigint NOT NULL, "name" text NOT NULL, "customer_id" bigint, CONSTRAINT "wishlists_pkey" PRIMARY KEY ("id"));</code><div class="qs-s-migration-change"${target("migration-relationship-change")}>02 · Link wishlists.customer_id <span>⌄</span></div><code class="qs-s-relationship-sql">ALTER TABLE "bookstore"."wishlists" ADD CONSTRAINT "wishlists_customers_fkey" FOREIGN KEY ("customer_id") REFERENCES "bookstore"."customers" ("id") ON UPDATE NO ACTION ON DELETE NO ACTION;</code></div>`, `${button("Close")}${button("Refresh review", "refresh-review")}${button("Apply migration", "apply-migration", true)}`, "qs-s-migration-dialog")}</div><div class="qs-s-confirm-overlay">${miniDialog("Confirm action", "Apply migration", `<p>Apply the two reviewed changes to PostgreSQL? The server runs the displayed SQL in a managed transaction.</p>`, button("Apply migration", "", true), "qs-s-confirm-dialog")}</div>`,
  className: "qs-s-migration-demo",
});

export const guide = {
  name: "Schemii",
  steps: [
    {
      title: "Add a PostgreSQL connection",
      text: "Use the database icon in the top bar to open Connections. Add the included Mercury Books target (postgres / schemii_test), or a PostgreSQL target with the bookstore objects shown in this guide. Save the profile, then test it.",
      tip: "Connection details and credentials for the included target are in the local setup guide. A local design needs no connection, but the later steps use PostgreSQL.",
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
      text: "New workspace starts with Local design · no database. Choose PostgreSQL-backed design, select Bookstore DB and the bookstore namespace, then Open database schema. The database comes from the connection and is read-only in the form.",
      tip: "The included bookstore namespace already has customers, books, and book_catalog. Those existing objects make the next steps followable.",
      scene: workspaceScene,
      states: ["library", "postgres", "connection", "namespace", "opened"],
      actions: [
        { target: "new-workspace", label: "New workspace", caption: "Open Workspaces from the top-bar plus button.", state: "library" },
        { target: "workspace-type", label: "Workspace type", caption: "Choose PostgreSQL-backed design instead of the local default.", state: "postgres" },
        { target: "workspace-connection", label: "Connection", caption: "Select the saved connection; its database is shown automatically.", state: "connection" },
        { target: "workspace-namespace", label: "Namespace", caption: "Choose a namespace visible to that PostgreSQL role.", state: "namespace" },
        { target: "open-database-schema", label: "Open database schema", caption: "Open the existing bookstore schema and its diagram.", state: "opened" },
      ],
      idleText: "Watch the local default change to a database-backed workspace.",
      staticText: "The PostgreSQL-backed workspace is open at schemii_test.bookstore.",
      completeText: "Workspace opened. Replaying without creating a saved workspace...",
    },
    {
      title: "Create and inspect a table",
      text: "Use Create table on the left rail to add wishlists alongside the existing customers table. Keep the initial id and name columns; add customer_id with type bigint, then Create table. Select wishlists to inspect the saved design.",
      tip: "Wishlists exists only in the desired design until a migration is applied. Moving table cards saves their layout automatically.",
      scene: tableScene,
      states: ["form", "named", "column", "created", "inspector"],
      actions: [
        { target: "create-table", label: "Create table", caption: "Open the Create table editor from the left tool rail.", state: "form" },
        { target: "table-name", label: "Table name", caption: "Name the new table wishlists.", state: "named" },
        { target: "add-column", label: "Add column", caption: "Add customer_id as bigint beside the initial id and name.", state: "column" },
        { target: "submit-table", label: "Create table", caption: "Save the desired table in this workspace.", state: "created" },
        { target: "select-table", label: "wishlists table", caption: "Select wishlists to inspect and edit its desired structure.", state: "inspector" },
      ],
      idleText: "Watch a table take shape in the actual design controls.",
      staticText: "The new wishlists table is selected with its columns in the inspector.",
      completeText: "Table created. Replaying without changing the saved design...",
    },
    {
      title: "Link wishlists and inspect existing rows",
      text: "Choose Create relationship, select wishlists.customer_id, then the existing customers.id key. Review the mapping and actions, then Create relationship. Select customers to open its live rows; wishlists itself is still only a design.",
      tip: "The banner tells you which column to select next. Live rows for wishlists require the migration in the last step.",
      scene: relationshipScene,
      states: ["tool", "foreign", "confirm", "linked", "inspector", "rows"],
      actions: [
        { target: "relationship-tool", label: "Create relationship", caption: "Start relationship authoring from the left tool rail.", state: "tool" },
        { target: "foreign-key", label: "wishlists.customer_id", caption: "Choose the new table's foreign key column first.", state: "foreign" },
        { target: "referenced-key", label: "customers.id", caption: "Choose the referenced key to open the confirmation form.", state: "confirm" },
        { target: "save-relationship", label: "Create relationship", caption: "Review the column mapping and actions, then create the relationship.", state: "linked" },
        { target: "inspect-table", label: "existing customers table", caption: "Select existing customers to open its inspector.", state: "inspector" },
        { target: "table-rows", label: "Open table rows and console", caption: "Open live customers rows in the data tools pane.", state: "rows" },
      ],
      idleText: "Watch the foreign key selection order and table inspection.",
      staticText: "The relationship is saved in the design; existing customers rows are live below.",
      completeText: "Diagram flow complete. Replaying without editing the schema...",
    },
    {
      title: "Browse views, then run SQL",
      text: "The Tables, Views, and SQL selector is centered above the workspace. In Views, Browse views focuses the search field beside the already visible view list; choose book_catalog to inspect its definition. In SQL, write a query and use Run current statement for results.",
      tip: "SQL runs against the open PostgreSQL-backed workspace, so check its connection and query mode first.",
      scene: sqlScene,
      states: ["views", "browse", "definition", "sql", "results"],
      actions: [
        { target: "views-layer", label: "Views", caption: "Switch from Tables to the Views workspace.", state: "views" },
        { target: "browse-views", label: "Browse views", caption: "Focus the search field in the visible view list.", state: "browse" },
        { target: "view-item", label: "book_catalog", caption: "Select a view to read its definition.", state: "definition" },
        { target: "sql-layer", label: "SQL", caption: "Switch to the SQL workspace for the same target.", state: "sql" },
        { target: "run-statement", label: "Run current statement", caption: "Run the current query and inspect its retained result.", state: "results" },
      ],
      idleText: "Watch the workspace selector, view search, and SQL results.",
      staticText: "The SQL result is open after browsing a view definition.",
      completeText: "Views and SQL reviewed. Replaying without running a query...",
    },
    {
      title: "Review before changing PostgreSQL",
      text: "Wishlists and its relationship are saved in Schemii metadata; PostgreSQL is unchanged. Review migration compares that design with its baseline and live target. Refresh the review, expand each proposed change to inspect its SQL, then Apply migration and confirm only if every change is correct.",
      tip: "After a successful migration, wishlists exists in PostgreSQL and its live rows can open. Applying requires database permissions.",
      scene: migrationScene,
      states: ["review", "refreshed", "expanded-table", "expanded", "confirm"],
      actions: [
        { target: "review-migration", label: "Review migration", caption: "Open the review from the left tool rail.", state: "review" },
        { target: "refresh-review", label: "Refresh review", caption: "Recheck the current target before authorizing SQL.", state: "refreshed" },
        { target: "migration-table-change", label: "Create table change", caption: "Expand the table change to inspect its generated SQL.", state: "expanded-table" },
        { target: "migration-relationship-change", label: "Relationship change", caption: "Expand the relationship change to inspect its generated SQL.", state: "expanded" },
        { target: "apply-migration", label: "Apply migration", caption: "Request the separate confirmation only after checking every change.", state: "confirm" },
      ],
      idleText: "Watch where saved design becomes a reviewed PostgreSQL change.",
      staticText: "Expanded SQL is reviewed before the separate Apply migration confirmation.",
      completeText: "Review complete. Replaying without applying SQL...",
    },
  ],
};
