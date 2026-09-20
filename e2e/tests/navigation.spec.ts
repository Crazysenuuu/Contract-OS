import { test, expect, type Page } from "@playwright/test";
import { login } from "./helpers";

/**
 * Click a nav link and wait for the URL change, retrying the click.
 *
 * Under parallel-worker load the React hydration can re-render the nav DOM
 * between Playwright's hit-test and the dispatched click, swallowing the
 * first click entirely (the URL stays on /dashboard). Retrying the whole
 * click+assert pair via toPass is the canonical mitigation.
 */
async function clickNav(page: Page, selector: string, urlPattern: RegExp) {
  await expect(async () => {
    await page.click(selector);
    await expect(page).toHaveURL(urlPattern);
  }).toPass({ timeout: 20_000 });
}

test.describe("Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should show navigation menu", async ({ page }) => {
    // Check nav elements
    await expect(page.locator("nav")).toBeVisible();
    await expect(page.locator("text=ContractOS")).toBeVisible();
  });

  test("should navigate to analytics page", async ({ page }) => {
    await clickNav(page, 'a[href="/analytics"]', /\/analytics/);
  });

  test("should navigate to jurisdictions page", async ({ page }) => {
    await clickNav(page, 'a[href="/jurisdictions"]', /\/jurisdictions/);
  });

  test("should navigate to e-signature page", async ({ page }) => {
    await clickNav(page, 'a[href="/esignature"]', /\/esignature/);
  });

  test("should navigate to bulk operations page", async ({ page }) => {
    await clickNav(page, 'a[href="/bulk"]', /\/bulk/);
  });

  test("should navigate to clause library page", async ({ page }) => {
    await clickNav(page, 'a[href="/clause-library"]', /\/clause-library/);
  });

  test("should navigate to translations page", async ({ page }) => {
    await clickNav(page, 'a[href="/translations"]', /\/translations/);
  });

  test("should navigate to translation queue page", async ({ page }) => {
    await clickNav(page, 'a[href="/translation-queue"]', /\/translation-queue/);
  });

  test("should navigate to settings pages", async ({ page }) => {
    await clickNav(page, 'a[href="/settings/alerting"]', /\/settings\/alerting/);
  });

  test("should show user info in nav", async ({ page }) => {
    // The nav shows the account holder's display name.
    await expect(page.locator("nav")).toContainText("E2E Test User");
  });

  test("should logout successfully", async ({ page }) => {
    await clickNav(page, "button:has-text('Sign out')", /\/login/);
  });
});
