import { defineConfig, devices } from "@playwright/test";
import { manifestPattern } from "./scripts/ci/browser-shards.mjs";

const baseURL = process.env.SCHEMII_E2E_BASE_URL || "https://localhost:8001";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: manifestPattern(process.env.SCHEMII_E2E_FILE_MANIFEST),
  globalSetup: "./tests/e2e/global-setup.js",
  outputDir: "./artifacts/playwright-results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["line"], ["./scripts/ci/playwright-reporter.mjs"]]
    : "line",
  timeout: 45_000,
  expect: { timeout: 7_500 },
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
    storageState: "./artifacts/playwright-auth/admin.json",
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
      // Request-only transaction/capacity/race cases have no device behavior.
      grepInvert: /@request-only/,
      // This transfer already ran desktop-only through its existing guard.
      testIgnore: "**/raw-copy-streaming.spec.js",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
