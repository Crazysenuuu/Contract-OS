import { test, expect } from "@playwright/test";
import { login, loginAs } from "./helpers";

/**
 * E2E coverage for the admin/ops surfaces added in the gap-implementation
 * pass: webhooks admin, feature flags admin, and data governance.
 *
 * Requires the e2e fixtures (backend/scripts/seed_e2e.py), which seed both
 * the regular user (test@example.com) and the admin user (admin@example.com).
 */

const ADMIN_EMAIL = "admin@example.com";
const ADMIN_PASSWORD = "password123";
const UNIQUE_SUFFIX = () => Date.now();

test.describe("Webhooks admin", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    // Wait for the auth state to hydrate before deep-navigating.
    await page.waitForSelector('a[href="/settings/webhooks"]', { timeout: 15000 });
    await page.goto("/settings/webhooks");
    await expect(page.getByRole("heading", { name: "Webhooks" })).toBeVisible();
  });

  test("shows the add-webhook form and event catalogue", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "Add webhook" })
    ).toBeVisible();
    // Event catalogue loads from the backend — at least one event pill is offered.
    await expect(page.locator('button[type="button"]').first()).toBeVisible();
  });

  test("creates a webhook endpoint, lists it, then deletes it", async ({
    page,
  }) => {
    // example.com resolves publicly; hooks.example.com does not, and the
    // backend's SSRF guard (correctly) rejects unresolvable webhook hosts.
    const url = `https://example.com/hooks/e2e-${UNIQUE_SUFFIX()}`;
    await page.fill('input[placeholder="https://example.com/hooks/agreements"]', url);

    await page.getByRole("button", { name: "Create webhook" }).click();

    // The created endpoint appears in the list with action buttons.
    const row = page.locator("div", { hasText: url }).filter({
      has: page.getByRole("button", { name: "Delete" }),
    });
    await expect(row.first()).toBeVisible({ timeout: 10000 });

    // Clean up so repeat runs stay deterministic.
    row.first().getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText(url)).toHaveCount(0, { timeout: 10000 });
  });

  test("lists deliveries section for an existing webhook", async ({ page }) => {
    // If any webhook exists, open its deliveries panel.
    const deliveriesButton = page.getByRole("button", { name: "Deliveries" }).first();
    const hasWebhooks = await deliveriesButton.isVisible().catch(() => false);
    test.skip(!hasWebhooks, "no webhooks seeded");
    await deliveriesButton.click();
    await expect(page.getByText("Event")).toBeVisible();
  });
});

test.describe("Feature flags admin (admin-only)", () => {
  test("blocks non-admin users", async ({ page }) => {
    await login(page);
    await page.goto("/admin/feature-flags");
    await expect(page.getByText("Admin access required.")).toBeVisible();
  });

  test("shows flag list, stats, and my-enabled-flags for admins", async ({
    page,
  }) => {
    await loginAs(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await page.goto("/admin/feature-flags");

    await expect(
      page.getByRole("heading", { name: "Feature Flags" })
    ).toBeVisible();
    // exact: true — the substring variant also matches
    // "No flags enabled for your user." (strict-mode violation).
    await expect(
      page.getByText("Enabled for you", { exact: true })
    ).toBeVisible();

    // The flags list renders the seeded flags (a .or("main") assertion is a
    // strict-mode trap: main always exists, so both branches match at once).
    await expect(
      page.getByTestId("flag-row-compliance_engine")
    ).toBeVisible();
  });

  test("creates, toggles, and deletes a flag", async ({ page }) => {
    await loginAs(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await page.goto("/admin/feature-flags");

    const flagName = `e2e_test_flag_${UNIQUE_SUFFIX()}`;
    await page.fill('input[placeholder="new_clause_engine"]', flagName);
    await page
      .getByRole("button", { name: /create/i })
      .first()
      .click();

    // Flag appears in the list.
    const created = page.getByText(flagName).first();
    await expect(created).toBeVisible({ timeout: 10000 });

    // Toggle it off via the row action. Stable testid avoids matching the
    // outermost ancestor div (Playwright hasText gotcha).
    const row = page.getByTestId(`flag-row-${flagName}`);
    await row.getByRole("button", { name: "Enabled" }).click();
    await expect(row.getByRole("button", { name: "Disabled" })).toBeVisible({
      timeout: 10000,
    });

    // Clean up. Scope to the flag row: the toast ("Flag \"…\" deleted")
    // echoes the name and outlives the row, so a page-wide count never
    // reaches 0.
    await row.getByRole("button", { name: /delete/i }).click();
    await expect(row).toHaveCount(0, { timeout: 10000 });
  });
});

test.describe("Data governance", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    // Wait for the auth state to hydrate before deep-navigating.
    await page.waitForSelector('a[href="/data-governance"]', { timeout: 15000 });
    await page.goto("/data-governance");
    await expect(
      page.getByRole("heading", { name: "Data Governance" })
    ).toBeVisible();
  });

  test("shows the three governance sections", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "Retention Policies" })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Legal Holds" })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Privacy Erasure Requests/ })
    ).toBeVisible();
  });

  test("creates and pauses a retention policy", async ({ page }) => {
    const policyName = `e2e-retention-${UNIQUE_SUFFIX()}`;
    await page.getByRole("button", { name: "New policy" }).click();
    await page.fill('input[placeholder="Policy name"]', policyName);
    await page.getByRole("button", { name: "Create policy" }).click();

    const row = page.getByTestId(`policy-row-${policyName}`);
    await expect(row).toBeVisible({ timeout: 10000 });

    // Pause it — button flips to "Resume".
    await row.getByRole("button", { name: "Pause" }).click();
    await expect(row.getByRole("button", { name: "Resume" })).toBeVisible({
      timeout: 10000,
    });
    // Restore.
    await row.getByRole("button", { name: "Resume" }).click();
  });

  test("places and releases a legal hold", async ({ page }) => {
    const reason = `e2e hold ${UNIQUE_SUFFIX()}`;
    await page.getByRole("button", { name: "Place hold" }).click();
    await page.fill('input[placeholder="Reason"]', reason);
    await page
      .locator("div.bg-white")
      .filter({ hasText: "Place hold" })
      .getByRole("button", { name: "Place hold" })
      .click();

    const row = page.locator("div").filter({ hasText: reason }).first();
    await expect(row).toBeVisible({ timeout: 10000 });

    await row.getByRole("button", { name: "Release" }).click();
    await expect(
      page.getByText("No active legal holds.")
    ).toBeVisible({ timeout: 10000 });
  });

  test("creates an erasure request and lists it as pending", async ({ page }) => {
    const subject = `e2e-subject-${UNIQUE_SUFFIX()}@example.com`;
    await page.getByRole("button", { name: "New request" }).click();
    await page.fill(
      'input[placeholder="Data subject (name/email)"]',
      subject
    );
    await page.getByRole("button", { name: "Create request" }).click();

    const row = page.getByTestId(`erasure-row-${subject}`);
    await expect(row).toBeVisible({ timeout: 10000 });
    // Initial backend status for an erasure request is "received"
    // ('received' → 'under_review' → 'shredded'/'completed'/'denied').
    await expect(row.getByText("received", { exact: true })).toBeVisible();
  });
});
