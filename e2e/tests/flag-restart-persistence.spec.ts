/**
 * Feature-flag persistence across a backend restart (admin UI, end-to-end).
 *
 * Runs in the dedicated "restart" Playwright project, which depends on the
 * main "chromium" project — so it always executes AFTER the rest of the suite
 * has finished and never disrupts concurrently running specs.
 *
 * Flow: an admin toggles a flag in /admin/feature-flags → the backend process
 * is killed and relaunched with its exact environment (e2e/scripts/
 * restart_backend.sh) → a brand-new browser session (empty storage) logs in
 * and must see the toggled state, proving the DB-backed flag store persisted
 * it across process boundaries. The flag is reverted through the UI so the
 * suite leaves the same state it found.
 */
import { execSync } from "child_process";
import { loginAs, test, expect, type Page } from "./helpers";

const RESTART_SCRIPT = "scripts/restart_backend.sh";

/** Flag whose state is toggled; seeded by the feature-flag migration. */
const TOGGLE_FLAG = "canary_deployments";

function backendPid(): string {
  return execSync(`pgrep -f "uvicorn app.main:app" | head -1`)
    .toString()
    .trim();
}

/** flag name → toggle-button text ("Enabled" / "Disabled"), read from the UI. */
async function readFlagStates(page: Page): Promise<Record<string, string>> {
  const states: Record<string, string> = {};
  const rows = page.locator("[data-testid^='flag-row-']");
  await expect(rows.first()).toBeVisible();
  const count = await rows.count();
  for (let i = 0; i < count; i++) {
    const row = rows.nth(i);
    const testId = await row.getAttribute("data-testid");
    const name = testId!.replace("flag-row-", "");
    states[name] = await row.locator("button").first().innerText();
  }
  return states;
}

async function toggleAndWait(page: Page, name: string) {
  const row = page.getByTestId(`flag-row-${name}`);
  const before = await row.locator("button").first().innerText();
  await row.locator("button").first().click();
  await expect(
    row.locator("button").filter({ hasText: before === "Enabled" ? "Disabled" : "Enabled" })
  ).toBeVisible();
  return before === "Enabled" ? "Disabled" : "Enabled";
}

test("feature flags survive a backend restart (admin UI, fresh session)", async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000);

  const pidBefore = backendPid();

  // --- Phase 1: admin toggles a flag in the UI -----------------------------
  await loginAs(page, "admin@example.com", "password123");
  await page.goto("/admin/feature-flags");
  await expect(page.getByRole("heading", { name: /feature flag/i })).toBeVisible();

  const before = await readFlagStates(page);
  expect(before, "flag seeded by the feature-flag persistence migration").toHaveProperty(
    TOGGLE_FLAG
  );
  const originalState = before[TOGGLE_FLAG];
  const toggledState = await toggleAndWait(page, TOGGLE_FLAG);
  console.log(
    `before restart: ${TOGGLE_FLAG} ${originalState} -> ${toggledState}, ` +
      `${Object.keys(before).length} flags total`
  );

  // --- Phase 2: kill and restart the backend -------------------------------
  execSync(RESTART_SCRIPT, { timeout: 60_000, shell: "/bin/bash" });
  const pidAfter = backendPid();
  expect(pidAfter, "backend must be a genuinely new process").not.toBe(pidBefore);
  console.log(`restarted: pid ${pidBefore} -> ${pidAfter}`);

  // --- Phase 3: brand-new browser session must see the persisted state -----
  const freshContext = await browser.newContext(); // empty storage
  const freshPage = await freshContext.newPage();
  await loginAs(freshPage, "admin@example.com", "password123");
  await freshPage.goto("/admin/feature-flags");
  await expect(
    freshPage.getByRole("heading", { name: /feature flag/i })
  ).toBeVisible();

  const after = await readFlagStates(freshPage);
  expect(after[TOGGLE_FLAG], "toggled state must survive the restart").toBe(
    toggledState
  );
  for (const [name, state] of Object.entries(before)) {
    if (name === TOGGLE_FLAG) continue;
    expect(after[name], `flag ${name} must keep its pre-restart state`).toBe(state);
  }

  // --- Phase 4: revert through the UI so the environment stays pristine ----
  await toggleAndWait(freshPage, TOGGLE_FLAG);
  await expect(
    freshPage
      .getByTestId(`flag-row-${TOGGLE_FLAG}`)
      .locator("button")
      .filter({ hasText: originalState })
  ).toBeVisible();

  await freshContext.close();
});
