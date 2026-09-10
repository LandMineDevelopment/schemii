import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

let modelId;
test.beforeEach(async ({ request }) => { modelId = await createOrganizationModel(request, "E2E filter authoring"); });
test.afterEach(async ({ request }) => { await deleteModel(request, modelId); modelId = null; });

test("model author binds a conditional parameter; report paths activate it and required scope forces it", async ({ page }) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#target")).toHaveText("organization.public");
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.getByRole("button", { name: "Staffing example", exact: true }).click();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Delete scope Organization tree", exact: true }).click();
  await page.getByRole("button", { name: "Delete scope Time scope", exact: true }).click();
  await expect(page.locator("#run")).toBeEnabled();

  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Report parameter/ }).click();
  await page.getByRole("textbox", { name: /^Scope .+ name$/ }).fill("Certification date");
  await chooseModelOption(page, "Requirement for Model filter", "Conditional model parameter");
  await page.getByRole("textbox", { name: /^Certification date \/ Default input .+ name$/ }).fill("As of date");
  await chooseModelOption(page, /^Certification date \/ Default input .+ type$/, "Date");
  const filterDialog=page.getByRole("dialog",{name:"Add model filter",exact:true});
  await expect(filterDialog).toContainText("Awaiting source selection");
  await expect(page.getByRole("combobox", { name: "Certification date / Default condition 1 value source", exact: true })).toHaveValue("As of date");
  await chooseModelOption(page, "Certification date / Default condition 1 field", "personnel_certification_fact · effective_date");
  await chooseModelOption(page, "Certification date / Default condition 1 operator", "≤");
  await chooseModelOption(page, "Certification date / Default condition 1 value source", "As of date");
  await expect(filterDialog).toContainText("personnel_certification_fact.effective_date");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await expect(page.locator("#model-filters")).toContainText("personnel_certification_fact.effective_date ≤ [As of date]");

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

  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Edit filter Certification date", exact: true }).click();
  await chooseModelOption(page, "Requirement for Certification date", "Required model scope");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
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
