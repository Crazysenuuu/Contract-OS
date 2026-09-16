import { test, expect } from "@playwright/test";
import { login } from "./helpers";

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
    await page.click('a[href="/analytics"]');
    await expect(page).toHaveURL(/\/analytics/);
  });

  test("should navigate to jurisdictions page", async ({ page }) => {
    await page.click('a[href="/jurisdictions"]');
    await expect(page).toHaveURL(/\/jurisdictions/);
  });

  test("should navigate to e-signature page", async ({ page }) => {
    await page.click('a[href="/esignature"]');
    await expect(page).toHaveURL(/\/esignature/);
  });

  test("should navigate to bulk operations page", async ({ page }) => {
    await page.click('a[href="/bulk"]');
    await expect(page).toHaveURL(/\/bulk/);
  });

  test("should navigate to clause library page", async ({ page }) => {
    await page.click('a[href="/clause-library"]');
    await expect(page).toHaveURL(/\/clause-library/);
  });

  test("should navigate to translations page", async ({ page }) => {
    await page.click('a[href="/translations"]');
    await expect(page).toHaveURL(/\/translations/);
  });

  test("should navigate to translation queue page", async ({ page }) => {
    await page.click('a[href="/translation-queue"]');
    await expect(page).toHaveURL(/\/translation-queue/);
  });

  test("should navigate to settings pages", async ({ page }) => {
    await page.click('a[href="/settings/alerting"]');
    await expect(page).toHaveURL(/\/settings\/alerting/);
  });

  test("should show user info in nav", async ({ page }) => {
    // The nav shows the account holder's display name.
    await expect(page.locator("nav")).toContainText("E2E Test User");
  });

  test("should logout successfully", async ({ page }) => {
    await page.click("button:has-text('Sign out')");
    await expect(page).toHaveURL(/\/login/);
  });
});
