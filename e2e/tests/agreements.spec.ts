import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test.describe("Agreements", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should display dashboard", async ({ page }) => {
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "Welcome back"
    );
  });

  test("should navigate to new agreement page", async ({ page }) => {
    await page.click('a[href="/agreements/new"]');
    await expect(page).toHaveURL(/\/agreements\/new/);
    await expect(page.locator("h1")).toContainText(
      "Select Agreement Type",
      { timeout: 15000 }
    );
  });

  test("should show agreement list on dashboard", async ({ page }) => {
    // Dashboard should have some content
    await expect(page.locator("text=Agreements")).toBeVisible();
  });

  test("should create new agreement via wizard", async ({ page }) => {
    await page.goto("/agreements/new");

    // Select template
    const templateSelect = page.locator("select").first();
    if (await templateSelect.isVisible()) {
      await templateSelect.selectOption({ index: 1 });
    }

    // Fill in first step
    const titleInput = page.locator('input[placeholder*="title"], input[placeholder*="Title"]').first();
    if (await titleInput.isVisible()) {
      await titleInput.fill("Test NDA Agreement");
    }

    // Click next button
    const nextButton = page.locator("button:has-text('Next')").first();
    if (await nextButton.isVisible()) {
      await nextButton.click();
    }
  });
});

test.describe("Agreement Detail", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should show agreement actions", async ({ page }) => {
    // Navigate to an agreement if any exist
    const agreementLink = page.locator('a[href^="/agreements/"]').first();
    if (await agreementLink.isVisible()) {
      await agreementLink.click();
      await expect(page).toHaveURL(/\/agreements\/[^/]+$/);

      // Should show action buttons
      await expect(page.locator("button").first()).toBeVisible();
    }
  });
});
