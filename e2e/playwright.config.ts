import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for ContractOS E2E tests
 * @see https://playwright.dev/docs/test-configuration
 *
 * The frontend dev server is started automatically via webServer. It proxies
 * /api/v1 to the FastAPI backend (see frontend/next.config.ts rewrites), so a
 * reachable backend must already be running on BACKEND_URL (default
 * http://localhost:8000) with the e2e fixtures seeded
 * (backend/scripts/seed_e2e.py). The CI e2e job does exactly that.
 */
const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "html",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    cwd: "../frontend",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      // Frontend rewrites proxy API calls to this backend URL.
      BACKEND_INTERNAL_URL: backendUrl,
    },
  },
});
