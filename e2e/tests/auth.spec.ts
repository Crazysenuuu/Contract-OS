import { test, expect } from "@playwright/test";

test.describe("Authentication", () => {
  test("should redirect to login when not authenticated", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("should show login page", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
  });

  test("should show register page", async ({ page }) => {
    await page.goto("/register");
    await expect(
      page.getByRole("heading", { name: "Create your account" })
    ).toBeVisible();
    await expect(page.locator('input[type="email"]')).toBeVisible();
  });

  test("should login successfully with valid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', "test@example.com");
    await page.fill('input[type="password"]', "password123");
    await page.click('button[type="submit"]');

    // Should redirect to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10000 });
  });

  test("should show error with invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector('input[type="email"]');

    // Hydration-safe: pre-hydration fills/clicks are silently ignored by the
    // React-controlled form, so retry the submit until the banner appears.
    // A unique email per attempt avoids tripping the per-account failed-login
    // lockout ("Too many failed login attempts for this account").
    const deadline = Date.now() + 15000;
    for (;;) {
      const email = `invalid-${Date.now()}@example.com`;
      await page.fill('input[type="email"]', email);
      await page.fill('input[type="password"]', "wrongpassword");
      await page.click('button[type="submit"]');
      try {
        await expect(page.locator("text=Invalid")).toBeVisible({ timeout: 2000 });
        return;
      } catch (err) {
        if (Date.now() > deadline) throw err;
      }
    }
  });
});

test.describe("Registration", () => {
  test("should register new user successfully", async ({ page }) => {
    const uniqueEmail = `test${Date.now()}@example.com`;
    await page.goto("/register");
    await page.fill("#name", "Test User");
    await page.fill("#email", uniqueEmail);
    await page.fill("#password", "password123");
    await page.click('button[type="submit"]');

    // Should redirect to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
  });
});
