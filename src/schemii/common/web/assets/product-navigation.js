import { loginUrl } from './login-return.js';
import { createIconElement } from "./ui.js";
import { currentAccount, canAccessProduct, landingPath, signOut } from "./accounts-session.js";

const PRODUCTS = Object.freeze([
  { id: "schemii", name: "Schemii", description: "Schema design", href: "/" },
  { id: "schemoo", name: "Schemoo", description: "Semantic models", href: "/schemoo" },
  { id: "schemer", name: "Schemer", description: "Analytics dashboards", href: "/schemer" },
]);

/** Install the common product switcher without advertising unavailable routes. */
export function installProductNavigation(host, { activeProduct = null, account: providedAccount = null } = {}) {
  if (!(host instanceof HTMLElement)) throw new TypeError("A product navigation host is required");
  if (activeProduct !== null && !PRODUCTS.some(product => product.id === activeProduct)) {
    throw new TypeError(`Unknown active product: ${activeProduct}`);
  }
  if (activeProduct === null && !providedAccount) throw new TypeError("An account is required outside a product");

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
  void (providedAccount ? Promise.resolve(providedAccount) : currentAccount()).then(account => {
    for (const product of PRODUCTS) {
      if (!canAccessProduct(account, product.id)) surface.querySelector(`a[href="${product.href}"]`)?.remove();
    }
    if (activeProduct && !canAccessProduct(account, activeProduct)) { location.replace(landingPath(account)); return; }
    for (const [label, href] of [[activeProduct ? account.user.display_name || account.user.username : 'Account', '/account'], ...(account.is_admin ? [['Administration', '/admin']] : [])]) {
      const item = document.createElement('a'); item.href = href; item.className = 'ui-product-navigation__item'; item.textContent = label;
      if (location.pathname === href) item.setAttribute('aria-current', 'page');
      surface.append(item);
    }
    const logout = document.createElement('button'); logout.type = 'button'; logout.textContent = 'Sign out';
    logout.onclick = async () => { logout.disabled = true; try { await signOut(); } catch (error) { logout.textContent = error.message; logout.disabled = false; } }; surface.append(logout);
  }).catch(error => { if (error.status === 401) location.replace(loginUrl()); });
  return menu;
}
