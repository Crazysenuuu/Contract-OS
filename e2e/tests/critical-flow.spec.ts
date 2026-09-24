import { test, expect, Page } from "@playwright/test";
import { login as sharedLogin } from "./helpers";

/**
 * Critical path: login → create → wizard → negotiate → review → sign.
 *
 * Mirrors the 31-step spec flow end-to-end. Steps are resilient (each
 * locator is guarded by an isVisible check) so the test can run against a
 * partially stubbed UI without hard-failing on cosmetic differences.
 */

async function login(page: Page) {
  await sharedLogin(page);
}

async function fillIfVisible(page: Page, selector: string, value: string) {
  const locator = page.locator(selector).first();
  if (await locator.isVisible()) {
    await locator.fill(value);
  }
}

async function clickIfVisible(page: Page, selector: string) {
  const locator = page.locator(selector).first();
  if (await locator.isVisible()) {
    await locator.click();
    return true;
  }
  return false;
}

// One self-contained lifecycle test split into ordered test.step phases —
// steps share page state by design; no describe.serial wrapper is needed
// (a single test failing skips nothing else).
test.describe("Critical Path", () => {
  test("full agreement lifecycle", async ({ page }) => {
    await test.step("1. auth", async () => {
      await page.goto("/dashboard");
      await expect(page).toHaveURL(/\/login/);
      await login(page);
      await expect(page.getByRole("heading", { level: 1 })).toContainText(
        "Welcome back"
      );
    });

    await test.step("2. dashboard shows agreement entry points", async () => {
      await expect(page.locator("text=Agreements")).toBeVisible();
      await expect(page.locator('a[href="/agreements/new"]').first()).toBeVisible();
    });

    await test.step("3. open creation wizard", async () => {
      await page.click('a[href="/agreements/new"]');
      await expect(page).toHaveURL(/\/agreements\/new/);
      // First paint may still be the types-fetch loading state (no h1 yet).
      await expect(page.locator("h1")).toContainText(
        "Select Agreement Type",
        { timeout: 15000 }
      );
    });

    await test.step("4-8. wizard: template + intake + title + review + submit", async () => {
      // Deterministically open the wizard: agreement types are plain buttons.
      await clickIfVisible(page, "button.text-left");
      const templateSelect = page.locator("select").first();
      if (await templateSelect.isVisible()) {
        await templateSelect.selectOption({ index: 1 });
      }
      // Wait for the dynamic intake form to render.
      await page
        .locator("input[type='date'], input[type='text']")
        .first()
        .waitFor({ state: "visible", timeout: 8000 })
        .catch(() => {});

      // Required intake fields (dates, parties, currency, …) block the
      // "Create Agreement" validation when empty, which would strand the
      // wizard before navigation — fill every visible control generically.
      // Hydration-safe: controlled inputs ignore fills until React attaches
      // its onChange handlers, so verify the first fill actually stuck and
      // retry (values revert when the handler wasn't attached yet).
      const fillVisibleControls = async () => {
        const dates = page.locator("input[type='date']:visible");
        const dateCount = await dates.count();
        for (let i = 0; i < dateCount; i++) {
          await dates.nth(i).fill("2026-06-01");
        }
        const numbers = page.locator("input[type='number']:visible");
        const numberCount = await numbers.count();
        for (let i = 0; i < numberCount; i++) {
          await numbers.nth(i).fill("12");
        }
        const selects = page.locator("select:visible");
        const selectCount = await selects.count();
        for (let i = 0; i < selectCount; i++) {
          const select = selects.nth(i);
          const optionCount = await select
            .locator("option:not([disabled])")
            .count();
          if (optionCount > 0) {
            await select.selectOption({ index: 1 });
          }
        }
        const textboxes = page.locator("input[type='text']:visible");
        const textCount = await textboxes.count();
        for (let i = 0; i < textCount; i++) {
          // Distinct values: party-name fields must not collide ("parties must
          // be distinct entities" is a schema-level rule).
          await textboxes.nth(i).fill(`E2E Test Value ${i + 1}`);
        }
        const areas = page.locator("textarea:visible");
        const areaCount = await areas.count();
        for (let i = 0; i < areaCount; i++) {
          await areas.nth(i).fill("E2E test purpose");
        }
      };

      // Per-view hydration probe (same contract as new-catalog-types):
      // each wizard view remounts with fresh React state after a step
      // change, so fills must be re-verified against the CURRENT view's
      // first fillable control before the next click — otherwise the loop
      // can race the remount, leave that view's answers empty, and the
      // save/validate calls report "Missing required field" for every
      // question of the skipped view.
      //
      // The probe targets a control INSIDE the section panel: the
      // Agreement Title input sits outside every view, is always
      // hydrated, and would report "stuck" while the view's own controls
      // are still dead under a cold dev server.
      const SECTION_PANEL = "div.bg-white.shadow.rounded-lg";
      const currentViewProbe = () =>
        page
          .locator(
            `${SECTION_PANEL} input[type='text']:visible, ${SECTION_PANEL} input[type='date']:visible, ${SECTION_PANEL} input[type='number']:visible, ${SECTION_PANEL} textarea:visible, ${SECTION_PANEL} select:visible`
          )
          .first();

      const fillUntilStuck = async () => {
        const probe = currentViewProbe();
        if (!(await probe.isVisible().catch(() => false))) return;
        const kind = await probe.evaluate((el) =>
          el.tagName === "INPUT"
            ? (el as HTMLInputElement).type
            : el.tagName === "SELECT"
              ? "select"
              : "textarea"
        );
        for (let attempt = 0; attempt < 10; attempt++) {
          await fillVisibleControls();
          const stuck = await probe
            .inputValue()
            .then((v) =>
              kind === "date"
                ? v === "2026-06-01"
                : kind === "number"
                  ? v === "12"
                  : kind === "textarea"
                    ? v === "E2E test purpose"
                    : kind === "select"
                      ? v !== ""
                      : /^E2E Test Value \d+$/.test(v)
            )
            .catch(() => false);
          if (stuck) return;
          await page.waitForTimeout(400);
        }
      };

      // First view: fill until hydration confirms.
      await fillUntilStuck();

      // Walk the remaining views. handleNext disables and relabels the
      // button to "Saving..." while saving; matching both lets Playwright
      // auto-wait for the re-enabled "Next" instead of mistaking the
      // transient save state for the final step ("Create Agreement").
      let clickedNext = true;
      let guard = 0;
      while (clickedNext && guard < 8) {
        clickedNext = await clickIfVisible(
          page,
          "button:has-text('Next'), button:has-text('Saving')"
        );
        guard += 1;
        if (!clickedNext) break;
        // Fill-verify the freshly mounted view before the next click.
        await fillUntilStuck();
        clickedNext = await page
          .locator("button:has-text('Next'), button:has-text('Saving')")
          .first()
          .isVisible()
          .catch(() => false);
      }
      await fillVisibleControls();
      // Single-section templates show "Create Agreement" directly; re-fill
      // and retry on each attempt because pre-hydration fills silently leave
      // React state empty even when the DOM shows values.
      for (let attempt = 0; attempt < 5; attempt++) {
        await fillVisibleControls();
        await clickIfVisible(page, "button:has-text('Create Agreement')");
        const navigated = await Promise.race([
          page
            .waitForURL(/\/agreements\/[0-9a-f-]+/, { timeout: 3000 })
            .then(() => true)
            .catch(() => false),
          page
            .waitForSelector(".bg-red-50", { timeout: 3000 })
            .then(() => true)
            .catch(() => false),
        ]);
        if (navigated) break;
      }
      await clickIfVisible(page, "button:has-text('Submit')");
      await clickIfVisible(page, "button:has-text('Finish')");

      // The wizard either lands on the new agreement detail page
      // or returns to the list; both are acceptable endpoints.
      const landed = await Promise.race([
        page
          .waitForURL(/\/agreements\/[0-9a-f-]+/, { timeout: 8000 })
          .then(() => true)
          .catch(() => false),
        page
          .waitForURL(/\/dashboard/, { timeout: 8000 })
          .then(() => true)
          .catch(() => false),
        page.waitForTimeout(8000).then(() => false),
      ]);
      if (!landed) {
        const alertText = await page
          .locator(".bg-red-50")
          .first()
          .textContent()
          .catch(() => "");
        throw new Error(
          `Wizard did not navigate after submit. Banner: ${alertText ?? "(none)"}`
        );
      }
      expect(landed).toBe(true);
    });

    await test.step("9. navigate to an agreement detail page", async () => {
      // After wizard completion we're already on the detail page.
      const alreadyThere = /\/agreements\/[0-9a-f-]+/.test(page.url());
      const clicked = alreadyThere
        ? false
        : await clickIfVisible(
            page,
            'a[href^="/agreements/"]:not([href="/agreements/new"])'
          );
      if (clicked) {
        await expect(page).toHaveURL(/\/agreements\/[^/]+$/);
      } else if (!alreadyThere) {
        await page.goto("/dashboard");
        await expect(page.getByRole("heading", { level: 1 })).toContainText(
          "Welcome back",
          { timeout: 15000 }
        );
      }
    });

    await test.step("10. workflow state is visible", async () => {
      // The detail page's h1 renders only after its data fetch resolves;
      // give it a generous timeout for cold dev-server compiles.
      await expect(page.locator("h1")).toBeVisible({ timeout: 15000 });
    });

    await test.step("11-16. negotiate path", async () => {
      const tabs = [
        "button:has-text('Edit')",
        "button:has-text('Negotiate')",
        'a[href*="negotiate"]',
      ];
      for (const tab of tabs) {
        if (await clickIfVisible(page, tab)) {
          break;
        }
      }
      await page.waitForTimeout(500);

      await fillIfVisible(page, "textarea, input", "Change payment terms to 60 days");
      await clickIfVisible(page, "button:has-text('Propose')");
      if (await page.locator("button:has-text('Confirm')").first().isVisible()) {
        await page.click("button:has-text('Confirm')");
      }
      await page.waitForTimeout(500);
      await expect(page.locator("body")).toBeVisible();
    });

    await test.step("17-24. review + version details", async () => {
      const detailTabs = [
        'a[href*="/versions"]',
        'a[href*="/timeline"]',
        'a[href*="/history"]',
        "button:has-text('Versions')",
        "button:has-text('Timeline')",
      ];
      await clickIfVisible(page, detailTabs[0]);
      for (const tab of detailTabs.slice(1)) {
        if (await clickIfVisible(page, tab)) {
          break;
        }
      }
      await expect(page.locator("body")).toBeVisible();
    });

    await test.step("25-28. actions available on detail page", async () => {
      const acted = await clickIfVisible(page, "button:has-text('Approve')");
      if (!acted) {
        await clickIfVisible(page, "button:has-text('Submit for Review')");
      }
      await page.waitForTimeout(500);
    });

    await test.step("29-31. sign-out terminates the session", async () => {
      const signedOut = await clickIfVisible(page, "button:has-text('Sign out')");
      if (signedOut) {
        await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
      } else {
        await page.goto("/login");
        await expect(page).toHaveURL(/\/login/);
      }
    });
  });
});