import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";

test("model author binds a conditional parameter; report paths activate it and required scope forces it", async ({ page }) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/schemoo");
  await expect(page.locator("#target")).toHaveText("organization.public");
  await page.getByRole("button", { name: "Staffing example", exact: true }).click();
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.getByRole("button", { name: "Delete scope Organization tree", exact: true }).click();
  await page.getByRole("button", { name: "Delete scope Time scope", exact: true }).click();
  await expect(page.locator("#run")).toBeEnabled();

  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("textbox", { name: /^Scope .+ name$/ }).fill("Certification date");
  await chooseModelOption(page, "Requirement for Model filter", "Conditional model parameter");
  await page.getByRole("button", { name: "Add input to Certification date / Default", exact: true }).click();
  await page.getByRole("textbox", { name: /^Certification date \/ Default input .+ name$/ }).fill("As of date");
  await chooseModelOption(page, /^Certification date \/ Default input .+ type$/, "Date");
  await expect(page.locator("#model-filters")).toContainText("Not bound to a source yet");
  await page.getByRole("button", { name: "Bind source to As of date", exact: true }).click();
  await expect(page.locator("#run")).toBeDisabled();
  await expect(page.getByRole("combobox", { name: "Certification date / Default condition 1 value source", exact: true })).toHaveValue("As of date");
  await chooseModelOption(page, "Certification date / Default condition 1 field", "personnel_certification_fact · effective_date");
  await chooseModelOption(page, "Certification date / Default condition 1 operator", "≤");
  await chooseModelOption(page, "Certification date / Default condition 1 value source", "As of date");
  await expect(page.locator("#model-filters")).toContainText("Bound to 1 condition: personnel_certification_fact.effective_date");

  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#parameter-values")).toContainText("Conditional · not needed by this query");
  await expect(page.locator("#run")).toBeEnabled();
  await expect(page.locator("#sql")).not.toContainText('"personnel_certification_fact"');

  await page.getByRole("button", { name: "Add report filter group", exact: true }).click();
  await chooseModelOption(page, "Report filter 1 behavior", "Has a matching related record");
  await chooseModelOption(page, "Report filter 1 condition 1 field", "personnel_certification_fact · personnel_id");
  await chooseModelOption(page, "Report filter 1 condition 1 operator", "Is not null");
  await expect(page.locator("#parameter-values")).toContainText("Conditional · active for this query");
  await expect(page.locator("#plan-status")).toContainText("Certification date requires As of date");
  await expect(page.locator("#run")).toBeDisabled();

  await page.getByRole("textbox", { name: "Certification date: As of date", exact: true }).fill("2025-01-01");
  await expect(page.locator("#run")).toBeEnabled();
  await expect(page.locator("#sql")).toContainText("EXISTS");
  await expect(page.locator("#sql")).toContainText('"effective_date"');
  await expect(page.locator("#sql")).toContainText("2025-01-01");
  await chooseModelOption(page, "Report filter 1 behavior", "Filter returned detail");
  await expect(page.locator("#sql")).toContainText("LEFT JOIN");
  await expect(page.locator("#sql")).not.toContainText("EXISTS");
  await expect(page.locator("#sql")).toContainText("2025-01-01");
  await page.getByRole("button", { name: "Delete Report filter 1", exact: true }).click();
  await expect(page.locator("#parameter-values")).toContainText("Conditional · not needed by this query");
  await expect(page.locator("#sql")).not.toContainText('"personnel_certification_fact"');

  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await chooseModelOption(page, "Requirement for Certification date", "Required model scope");
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#parameter-values")).toContainText("Required for every query");
  await expect(page.locator("#sql")).toContainText("EXISTS");
  await expect(page.locator("#sql")).toContainText('"personnel_certification_fact"');
  await expect(page.locator("#sql")).toContainText("2025-01-01");
  await page.getByRole("textbox", { name: "Certification date: As of date", exact: true }).fill("");
  await expect(page.locator("#plan-status")).toContainText("Certification date requires As of date");
  await expect(page.locator("#run")).toBeDisabled();
  expect(errors).toEqual([]);
});
