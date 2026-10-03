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
  test("should register new user and require email verification", async ({ page }) => {
    const uniqueEmail = `test${Date.now()}@example.com`;
    await page.goto("/register");
    await page.fill("#name", "Test User");
    await page.fill("#email", uniqueEmail);
    // Must satisfy the server policy in
    // backend/app/core/password_policy.py (>=12 chars, not blocklisted,
    // not a derivative of the name or email).
    await page.fill("#password", "correct horse battery");
    // COPPA age gate: an adult DOB is required to create the account.
    await page.fill("#date_of_birth", "1990-01-01");
    await page.click('button[type="submit"]');

    // An unverified account is rejected by the backend on every
    // authenticated request, so registration stops at "check your inbox"
    // instead of opening a session.
    await expect(page).toHaveURL(/\/register/, { timeout: 15000 });
    await expect(page.getByText("Check your inbox")).toBeVisible();
    await expect(page.getByText(uniqueEmail)).toBeVisible();

    // The dashboard must not be reachable without a verified session.
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/, { timeout: 15000 });
  });
});
