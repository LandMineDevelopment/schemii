import { expect, test } from "@playwright/test";

test("account brand goes directly to the signed-in user's first authorized destination", async ({ page }) => {
  let authenticated = false;
  let account;
  const apiPaths = [];
  const visitedPaths = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/")) apiPaths.push(path);
  });
  page.on("framenavigated", frame => {
    if (frame === page.mainFrame()) visitedPaths.push(new URL(frame.url()).pathname);
  });

  await page.route("**/api/v1/**", route => route.fulfill({
    status: 403,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Unconfigured fixture API" }),
  }));
  await page.route("**/api/v1/auth/status", route => route.fulfill({
    json: { enabled: true, setup_required: false, authenticated },
  }));
  await page.route("**/api/v1/auth/me", route => route.fulfill({ json: account }));

  await page.goto("/login");
  const brand = page.locator(".accounts-header .ui-brand");
  await expect(brand).toHaveAttribute("href", "/account");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(apiPaths).not.toContain("/api/v1/auth/me");

  authenticated = true;
  const personas = [
    { username: "schema", capabilities: ["schemii:access"], is_admin: false, destination: "/" },
    { username: "models", capabilities: ["schemoo:access"], is_admin: false, destination: "/schemoo" },
    { username: "reports", capabilities: ["schemer:access"], is_admin: false, destination: "/schemer" },
    { username: "provisioner", capabilities: ["accounts:provision"], is_admin: true, destination: "/admin" },
  ];

  for (const persona of personas) {
    account = {
      user: { id: `user_${persona.username}`, username: persona.username,
        display_name: persona.username, is_admin: persona.is_admin, disabled: false },
      is_admin: persona.is_admin,
      capabilities: persona.capabilities,
    };
    await page.goto("/account");
    await expect(brand).toHaveAttribute("href", persona.destination);
    const navigationStart = visitedPaths.length;
    const apiStart = apiPaths.length;
    await brand.click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(persona.destination);

    const journey = visitedPaths.slice(navigationStart);
    expect(journey).toContain(persona.destination);
    if (persona.destination === "/schemoo") {
      expect(journey).not.toContain("/schemer");
      expect(apiPaths.slice(apiStart).some(path => path.startsWith("/api/v1/schemer/"))).toBe(false);
    }
  }
});
