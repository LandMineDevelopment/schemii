import { createIconElement } from "./ui.js";

const PRODUCTS = Object.freeze([
  { id: "schemii", name: "Schemii", description: "Schema design", href: "/" },
  { id: "schemoo", name: "Schemoo", description: "Semantic models", href: "/schemoo" },
  { id: "schemer", name: "Schemer", description: "Analytics dashboards", href: "/schemer" },
]);

/** Install the common product switcher without advertising unavailable routes. */
export function installProductNavigation(host, { activeProduct } = {}) {
  if (!(host instanceof HTMLElement)) throw new TypeError("A product navigation host is required");
  if (!PRODUCTS.some(product => product.id === activeProduct)) {
    throw new TypeError(`Unknown active product: ${activeProduct}`);
  }

  const menu = document.createElement("details");
  menu.className = "ui-menu ui-product-navigation";

  const trigger = document.createElement("summary");
  trigger.className = "ui-icon-button";
  trigger.title = "Switch application";
  trigger.setAttribute("aria-label", "Switch application");
  trigger.append(createIconElement("objects"));

  const surface = document.createElement("nav");
  surface.className = "ui-menu__surface ui-product-navigation__surface";
  surface.setAttribute("aria-label", "Schemii applications");
  for (const product of PRODUCTS) {
    const item = document.createElement(product.href ? "a" : "span");
    item.className = "ui-product-navigation__item";
    if (product.href) item.href = product.href;
    else item.setAttribute("aria-disabled", "true");
    if (product.id === activeProduct) item.setAttribute("aria-current", "page");

    const name = document.createElement("strong");
    name.textContent = product.name;
    const description = document.createElement("small");
    description.textContent = product.description;
    item.append(name, description);
    surface.append(item);
  }

  menu.append(trigger, surface);
  host.replaceChildren(menu);
  return menu;
}
