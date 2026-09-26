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
  // CI wizard specs (critical-flow, new-catalog-types) walk the real
  // Next.js dev server, whose per-route cold compiles routinely exceed the
  // 30s default. 12/12 CI failures were "Test timeout of 30000ms exceeded"
  // inside the hydration-wait loops (2 of them passed on retry — flaky), so
  // the loops simply need more headroom. 90s × (retries≈2) keeps the job
  // within its 25-minute budget. Non-wizard specs keep the 30s default.
  timeout: 30_000,
  expect: { timeout: 15_000 },
  // assertions (toHaveURL after a click) need headroom for cold route
  // compiles. Healthy assertions still pass instantly.
  expect: { timeout: 15_000 },
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
      // The restart-persistence spec runs in its own project (see below) so it
      // never restarts the backend while other specs are in flight.
      testIgnore: /flag-restart-persistence\.spec\.ts/,
    },
    {
      name: "restart",
      // Depends on "chromium": guaranteed to run only after the entire main
      // suite has finished. Kills and relaunches the backend mid-test, which
      // would break any spec running concurrently.
      dependencies: ["chromium"],
      testMatch: /flag-restart-persistence\.spec\.ts/,
    },
  ],
  webServer: {
    command: "npm run dev",
    cwd: "../frontend",
    url: "http://localhost:3000",
    // scripts/test.sh owns the dev server (boot + hot-route warm-up) in BOTH
    // local and CI runs — cold per-route compiles behind this webServer were
    // the #1 e2e flake, so warming must happen before tests start, and only
    // the process that boots the server can warm it in time. reuseExisting
    // lets Playwright attach to test.sh's server; a bare `npx playwright
    // test` (no test.sh) still boots one here as a fallback.
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      // Frontend rewrites proxy API calls to this backend URL.
      BACKEND_INTERNAL_URL: backendUrl,
    },
  },
});
