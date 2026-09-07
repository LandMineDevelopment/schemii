import { expect } from "@playwright/test";

// Exercise the same searchable selection flow on desktop and touch layouts.
export async function chooseModelOption(page, name, option, search = option) {
  const control = page.getByRole("combobox", { name, exact: typeof name === "string" });
  await control.click();
  await control.fill(search);
  const listId = await control.getAttribute("aria-controls");
  const list = page.locator(`#${listId}`);
  await expect(list).toBeVisible();
  await expectDropdownWithinViewport(control);
  await list.getByRole("option", { name: option, exact: true }).click();
}

export async function expectDropdownWithinViewport(control) {
  const bounds = await control.evaluate(input => {
    const list = document.getElementById(input.getAttribute("aria-controls"));
    const rect = list.getBoundingClientRect();
    const viewport = window.visualViewport;
    return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
      viewportLeft: viewport?.offsetLeft || 0, viewportTop: viewport?.offsetTop || 0,
      width: viewport?.width || innerWidth, height: viewport?.height || innerHeight };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(bounds.viewportLeft);
  expect(bounds.top).toBeGreaterThanOrEqual(bounds.viewportTop);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportLeft + bounds.width);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportTop + bounds.height);
}
