import { test } from "@playwright/test";
import { loginAs } from "./helpers";

/**
 * Temporary walkthrough: log in as the admin and visit every admin screen,
 * printing what renders on each (headings, tabs, key controls).
 * Run: npx playwright test tests/admin-tour.spec.ts
 */
const steps: string[] = [];
async function snapshot(page: import("@playwright/test").Page, label: string) {
  const h1 = await page.locator("h1, h2").first().textContent().catch(() => "(no heading)");
  const buttons = await page.locator("button:visible").allTextContents().catch(() => []);
  const links = await page
    .locator("a:visible")
    .evaluateAll((as) => as.map((a) => a.textContent?.trim()).filter(Boolean).slice(0, 8))
    .catch(() => []);
  steps.push(
    [
      `\n### ${label} — ${page.url()}`,
      `heading: ${h1?.trim()}`,
      `nav links: ${links.join(" · ") || "(none)"}`,
      `buttons: ${[...new Set(buttons.map((b) => b?.trim()))].slice(0, 10).join(" · ") || "(none)"}`,
    ].join("\n")
  );
}

test("admin walkthrough", async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin@example.com", "password123");

  await page.goto("/admin");
  await page.waitForLoadState("networkidle");
  await snapshot(page, "ADMIN OVERVIEW (/admin)");

  for (const tab of ["active", "history", "companies", "users"]) {
    const btn = page.locator(`button:has-text("${tab}")`).first();
    if (await btn.isVisible().catch(() => false)) {
      await btn.click();
      await page.waitForTimeout(800);
      await snapshot(page, `TAB: ${tab}`);
    }
  }

  for (const sub of ["ingestion", "approval-rules", "feature-flags", "audit-batches"]) {
    await page.goto(`/admin/${sub}`);
    await page.waitForLoadState("networkidle");
    await snapshot(page, `/admin/${sub}`);
  }

  console.log("\n================= ADMIN TOUR =================");
  console.log(steps.join("\n"));
  console.log("==============================================");
});
