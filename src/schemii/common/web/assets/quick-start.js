/** Small, self-contained product tours. Scenes illustrate the current UI without making requests. */
const scene = (label, content) => `<div class="quick-start-scene" aria-hidden="true"><div class="quick-start-scene__bar"><span class="quick-start-scene__brand">${label}</span><span class="quick-start-scene__lights"><i></i><i></i><i></i></span></div><div class="quick-start-scene__body">${content}</div></div>`;
const card = (title, lines, extra = "") => `<div class="quick-start-scene__card ${extra}"><strong>${title}</strong>${lines.map(line => `<span>${line}</span>`).join("")}</div>`;
const rail = labels => `<div class="quick-start-scene__rail">${labels.map((label, index) => `<span class="${index === 0 ? "active" : ""}">${label}</span>`).join("")}</div>`;
const callout = (label, detail) => `<div class="quick-start-scene__callout"><small>${label}</small><strong>${detail}</strong></div>`;

export const QUICK_STARTS = Object.freeze({
  schemii: {
    name: "Schemii",
    steps: [
      {
        title: "Connect to PostgreSQL",
        text: "Open PostgreSQL connections, add a server, and test its connection. Connection details stay on the server. Your database role determines what you can see and change.",
        tip: "Use the connection button in the top bar to switch or manage servers.",
        scene: scene("SCHEMII / CONNECTIONS", `${rail(["Servers", "Workspaces", "Catalog"])}${card("PostgreSQL connections", ["Production reporting", "localhost · 5432", "Connected"], "accent")}${callout("FIRST STEP", "Add and test a connection")}`),
      },
      {
        title: "Open a workspace",
        text: "Create a workspace for the exact database and namespace you want to explore. Schemii reads its live catalog and keeps your workspace layout separately.",
        tip: "The workspace title and status at the top tell you which target is open.",
        scene: scene("SCHEMII / WORKSPACES", `${rail(["Workspaces", "Tables", "Views"])}${card("New workspace", ["Connection · Production reporting", "Database · bookstore", "Namespace · public"], "accent")}${callout("TARGET", "bookstore / public")}`),
      },
      {
        title: "Explore tables and relationships",
        text: "Use the Tables layer to pan around the diagram and inspect a table. Follow foreign key lines, open table rows, and browse other database objects from the tool rail.",
        tip: "Fit tables brings the whole diagram back into view.",
        scene: scene("SCHEMII / TABLES", `${rail(["Tables", "Views", "SQL"])}<div class="quick-start-scene__graph">${card("customers", ["◆ id · uuid", "email · varchar"], "accent")}${card("orders", ["◆ id · uuid", "customer_id · uuid"])}<span class="quick-start-scene__link">↗ foreign key</span></div>`),
      },
      {
        title: "Inspect views and run SQL",
        text: "Switch between Views and SQL in the layer selector. Browse view definitions, write a query, then run the current statement or all statements against the selected connection.",
        tip: "Check the active connection and query mode before running SQL.",
        scene: scene("SCHEMII / SQL", `${rail(["Tables", "Views", "SQL"])}${card("Query draft", ["SELECT title, price", "FROM bookstore.books", "ORDER BY title;"], "code accent")}${callout("RESULT", "Rows appear below your query")}`),
      },
      {
        title: "Review and recover your work",
        text: "Saved layouts preserve diagram positions. When a layout revision conflicts, Schemii keeps your local positions visible until you explicitly reload the server layout. Review proposed schema changes before applying them.",
        tip: "Use Save layout when it is available; a conflict banner explains recovery steps.",
        scene: scene("SCHEMII / WORKSPACE", `${rail(["Tables", "Layout", "Review"])}${card("Layout conflict", ["Local table positions are preserved", "Saving has stopped"], "warning")}${callout("RECOVERY", "Reload server layout")}`),
      },
    ],
  },
  schemoo: {
    name: "Schemoo",
    steps: [
      {
        title: "Create or open a model",
        text: "Open the model library and create a model from a connected PostgreSQL source, or choose an existing one. A model defines what reports can use; it does not change source tables.",
        tip: "Use Open models in the top bar to return to the library.",
        scene: scene("SCHEMOO / MODEL LIBRARY", `${rail(["Models", "Source", "Canvas"])}${card("Semantic models", ["Staffing analysis", "Bookstore reporting", "+ Create model"], "accent")}${callout("SOURCE", "Choose a database namespace")}`),
      },
      {
        title: "Review the relationship paths",
        text: "On the Model tab, inspect the relationship paths and enable the connections your model needs. Red connections show cycles that must be resolved before previewing. Choose a starting object in Preview.",
        tip: "Select a table card to inspect its fields and available connections.",
        scene: scene("SCHEMOO / MODEL", `${rail(["Model", "Filters", "Preview"])}<div class="quick-start-scene__graph">${card("people", ["id · key", "department_id"], "accent")}${card("departments", ["id · key", "name"])}<span class="quick-start-scene__link">→ enabled path</span></div>`),
      },
      {
        title: "Expose fields and define filters",
        text: "Choose which fields report builders may use. Add model filters for reusable rules and inputs, then bind those inputs to source columns. Apply editor changes to the draft before saving the model.",
        tip: "The Filters tab explains which rules are required and which are optional.",
        scene: scene("SCHEMOO / FIELDS & FILTERS", `${rail(["Model", "Filters", "Preview"])}${card("Exposed fields", ["☑ Person name", "☑ Department", "☐ Internal note"], "accent")}${callout("MODEL FILTER", "As of date · Date input")}`),
      },
      {
        title: "Preview, then save",
        text: "On Preview, pick output fields, provide required filter values, and inspect the generated SQL. Run a read-only preview to check the rows. Save model to keep your changes for Schemer.",
        tip: "A preview tests the current draft; Save model makes it available for dashboards.",
        scene: scene("SCHEMOO / PREVIEW", `${rail(["Model", "Filters", "Preview"])}${card("Preview outputs", ["Person name", "Department", "Start date"], "accent")}${callout("QUERY PREVIEW", "Run preview · up to 100 rows")}`),
      },
    ],
  },
  schemer: {
    name: "Schemer",
    steps: [
      {
        title: "Create a dashboard",
        text: "Choose Create dashboard, name it, and select a saved Schemoo model. Every tile on this dashboard uses that model. Your access determines which editing actions are available.",
        tip: "Create or update a model in Schemoo if the source you need is missing.",
        scene: scene("SCHEMER / DASHBOARDS", `${rail(["Dashboards", "Reports", "Filters"])}${card("New dashboard", ["Name · Publishing overview", "Model · Bookstore reporting", "Create dashboard"], "accent")}${callout("SHARED SOURCE", "One model for every tile")}`),
      },
      {
        title: "Add a report tile",
        text: "Use Add tile to choose fields, measures, filters, and a visualization from the dashboard model. Arrange tiles to make the most useful results easy to scan.",
        tip: "A tile can show a chart or a table, depending on its selected output.",
        scene: scene("SCHEMER / TILES", `${rail(["Dashboard", "Tiles", "Source"])}${card("Orders by status", ["▂ ▅ ▇ ▄", "Open · Packed · Shipped"], "accent")}${card("Revenue", ["$42,680", "Current selection"])}${callout("ACTION", "+ Add tile")}`),
      },
      {
        title: "Filter the whole dashboard",
        text: "Open Filters to set shared model inputs. Choose optional filters when editing, supply their values, then Apply filters to refresh the tiles together.",
        tip: "The summary shows applied values when the filter panel is collapsed.",
        scene: scene("SCHEMER / FILTERS", `${rail(["Dashboard", "Filters", "Tiles"])}${card("Dashboard filters", ["Region · Northeast", "As of date · Today", "Apply filters"], "accent")}${callout("SCOPE", "All dashboard tiles")}`),
      },
      {
        title: "Explore the result",
        text: "Open a tile to inspect its result and available detail actions. Refresh the dashboard when source data changes. If its model changes, review and update the dashboard model before editing affected tiles.",
        tip: "A viewer may see fewer actions than an editor, based on dashboard access.",
        scene: scene("SCHEMER / RESULTS", `${rail(["Dashboard", "Tile", "Details"])}${card("Orders by status", ["Open · 18", "Packed · 32", "Shipped · 46"], "accent")}${callout("DETAIL", "Open tile for rows and actions")}`),
      },
    ],
  },
});

export function installQuickStart(product, trigger) {
  const guide = QUICK_STARTS[product];
  if (!guide || !(trigger instanceof HTMLElement)) throw new TypeError("A known product and trigger are required");
  const menu = trigger.closest("details");
  const focusReturn = menu?.querySelector("summary") || trigger;
  const dialog = document.createElement("dialog");
  dialog.className = "ui-dialog quick-start-dialog";
  dialog.setAttribute("aria-labelledby", "quick-start-title");
  dialog.innerHTML = `<div class="quick-start-panel"><header class="quick-start-head"><div><small>Quick start</small><h2 id="quick-start-title">Welcome to ${guide.name}</h2></div><span class="quick-start-count" aria-live="polite"></span></header><div class="quick-start-pages"></div><footer class="quick-start-footer"><div class="quick-start-progress" aria-label="Guide progress"></div><div class="quick-start-actions"><button type="button" class="ui-button quick-start-skip">Skip</button><button type="button" class="ui-button quick-start-back" aria-label="Previous step">←</button><button type="button" class="ui-button primary quick-start-next">Next →</button></div></footer></div>`;
  const pages = dialog.querySelector(".quick-start-pages");
  for (const [index, step] of guide.steps.entries()) {
    const page = document.createElement("section");
    page.className = "quick-start-page";
    page.hidden = index !== 0;
    page.innerHTML = `${step.scene}<div class="quick-start-copy"><span>${String(index + 1).padStart(2, "0")}</span><div><h3>${step.title}</h3><p>${step.text}</p><p class="quick-start-tip">${step.tip}</p></div></div>`;
    pages.append(page);
  }
  const count = dialog.querySelector(".quick-start-count");
  const progress = dialog.querySelector(".quick-start-progress");
  const back = dialog.querySelector(".quick-start-back");
  const next = dialog.querySelector(".quick-start-next");
  const skip = dialog.querySelector(".quick-start-skip");
  let index = 0;
  function render() {
    [...pages.children].forEach((page, pageIndex) => { page.hidden = pageIndex !== index; });
    pages.scrollTop = 0;
    count.textContent = `${index + 1} of ${guide.steps.length}`;
    progress.replaceChildren(...guide.steps.map((_, markerIndex) => {
      const marker = document.createElement("i");
      marker.classList.toggle("active", markerIndex === index);
      marker.setAttribute("aria-hidden", "true");
      return marker;
    }));
    back.disabled = index === 0;
    next.textContent = index === guide.steps.length - 1 ? "Finish" : "Next →";
    next.focus();
  }
  function open() {
    index = 0;
    menu?.removeAttribute("open");
    dialog.showModal();
    render();
  }
  trigger.addEventListener("click", open);
  back.addEventListener("click", () => { if (index > 0) { index -= 1; render(); } });
  next.addEventListener("click", () => { if (index + 1 === guide.steps.length) dialog.close(); else { index += 1; render(); } });
  skip.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => focusReturn.focus());
  document.body.append(dialog);
  return { open, dialog };
}
