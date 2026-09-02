import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.SCHEMII_E2E_BASE_URL || "https://localhost:8001";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./artifacts/playwright-results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "artifacts/playwright-report", open: "never" }]]
    : "line",
  timeout: 45_000,
  expect: { timeout: 7_500 },
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "android-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
