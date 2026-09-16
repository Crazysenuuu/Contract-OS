import { test, expect, Page } from "@playwright/test";

/**
 * Hydration-safe login used by specs that don't define their own.
 *
 * The React-controlled login inputs ignore programmatic `fill` until the
 * client bundle has attached its onChange handlers, so we retry the
 * login until the dashboard URL confirms the session was created.
 */
export async function login(page: Page, timeout = 15000) {
  await page.goto("/login");
  await page.waitForSelector('input[type="email"]');

  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    await page.fill('input[type="email"]', "test@example.com");
    await page.fill('input[type="password"]', "password123");
    await page.click('button[type="submit"]');
    try {
      await page.waitForURL(/\/dashboard/, { timeout: 5000 });
      return;
    } catch {
      // Hydration race: handlers not attached yet — retry.
    }
  }
  throw new Error("Could not log in as test@example.com");
}

export { test, expect };
