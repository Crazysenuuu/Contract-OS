import { test, expect, Page } from "@playwright/test";

/**
 * Hydration-safe login used by specs that don't define their own.
 *
 * The React-controlled login inputs ignore programmatic `fill` until the
 * client bundle has attached its onChange handlers, so we retry the
 * login until the dashboard URL confirms the session was created.
 */
export async function login(page: Page, timeout = 15000) {
  await loginAs(page, "test@example.com", "password123", timeout);
}

/**
 * Hydration-safe login for arbitrary seeded credentials.
 * admin@example.com / password123 is seeded by scripts/seed_e2e.py.
 */
export async function loginAs(
  page: Page,
  email: string,
  password: string,
  timeout = 15000
) {
  await page.goto("/login");
  await page.waitForSelector('input[type="email"]');

  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    await page.click('button[type="submit"]');
    try {
      await page.waitForURL(/\/dashboard/, { timeout: 5000 });
      return;
    } catch {
      // Hydration race: handlers not attached yet — retry.
    }
  }
  throw new Error(`Could not log in as ${email}`);
}

export { test, expect };
