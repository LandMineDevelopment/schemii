import { defineConfig, devices } from "@playwright/test";
import { manifestPattern } from "./scripts/ci/browser-shards.mjs";
import { originalSourceRoot } from "./scripts/ci/timing.mjs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const baseURL = process.env.SCHEMII_E2E_BASE_URL || "https://localhost:8001";
const sourceRoot = originalSourceRoot();
if (Boolean(sourceRoot) !== Boolean(process.env.SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE)) {
  throw new Error("Parallel browser source and ready accounts must be supplied together");
}
const sourcePath = path => sourceRoot ? resolve(sourceRoot, path) : `./${path}`;
const storageState = sourceRoot
  ? (await import(pathToFileURL(resolve(sourceRoot, "tests/e2e/helpers/account-auth.js"))))
      .accountStorageState(process.env)
  : "./artifacts/playwright-auth/admin.json";

export default defineConfig({
  testDir: sourcePath("tests/e2e"),
  testMatch: manifestPattern(process.env.SCHEMII_E2E_FILE_MANIFEST),
  globalSetup: sourcePath("tests/e2e/global-setup.js"),
  outputDir: "./artifacts/playwright-results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["line"], [sourcePath("scripts/ci/playwright-reporter.mjs")]]
    : "line",
  timeout: 45_000,
  expect: { timeout: 7_500 },
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
    storageState,
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
