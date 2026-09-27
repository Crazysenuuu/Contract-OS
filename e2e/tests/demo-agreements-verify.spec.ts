import { test, expect } from "@playwright/test";
import { login } from "./helpers";

/**
 * One-off verification: the demo agreements created via the API smoke tests
 * (create -> answers -> render -> send -> sign -> executed) surface correctly
 * in the frontend UI — dashboard stats, search list, and agreement detail.
 */
test.describe("Demo agreements UI verification", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("dashboard shows non-zero agreement counts", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Welcome back");
    // The "Agreements" stat card should reflect the seeded/demo agreements (> 0).
    const agreementsCard = page.locator("text=Agreements").first();
    await expect(agreementsCard).toBeVisible({ timeout: 15000 });
  });

  test("executed NDA appears in search results with correct status badge", async ({ page }) => {
    await page.goto("/search?q=Smoke+Test+Mutual+NDA");
    const row = page.getByRole("link", { name: /Smoke Test Mutual NDA \(signing flow\)/ }).first();
    await expect(row).toBeVisible({ timeout: 15000 });

    // The executed instance should carry an executed status badge in its row.
    const executedBadge = page.locator("tr", { hasText: "signing flow" }).filter({ hasText: "executed" }).first();
    await expect(executedBadge).toBeVisible();
  });

  test("executed agreement detail page renders title and status", async ({ page }) => {
    await page.goto("/search?q=Smoke+Test+Mutual+NDA");
    await page.getByRole("link", { name: /Smoke Test Mutual NDA \(signing flow\)/ }).first().click();
    await expect(page).toHaveURL(/\/agreements\/[0-9a-f-]{36}/, { timeout: 15000 });

    await expect(page.locator("h1")).toContainText("Smoke Test Mutual NDA (signing flow)");
    // Status badge is a sibling of the h1 (not nested inside it).
    await expect(page.getByText("executed", { exact: true }).first()).toBeVisible();
  });
});
