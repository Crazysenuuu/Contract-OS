import { test, expect, Page } from "@playwright/test";
import { login as sharedLogin } from "./helpers";

/**
 * Per-type wizard coverage for the 13 agreement types added to the catalog
 * (Corporate & Governance + commercial types from the spec's extended §3.A
 * taxonomy).
 *
 * For each type, this walks the real creation wizard: open /agreements/new,
 * click the type's card (which loads the type's own per-type questionnaire),
 * fill every visible control, and create the agreement. Passing requires the
 * catalog to serve all 13 types AND their questionnaires to render and
 * validate through the full frontend + backend stack.
 */

const NEW_TYPES: Array<{ name: string }> = [
  { name: "Shareholders Agreement" },
  { name: "Share Purchase Agreement" },
  { name: "Investment Agreement" },
  { name: "Convertible Note Agreement" },
  { name: "SAFE-style Investment Agreement" },
  { name: "Founder Agreement" },
  { name: "Board Resolution" },
  { name: "Service Level Agreement" },
  { name: "Purchase Agreement" },
  { name: "Distribution Agreement" },
  { name: "Reseller Agreement" },
  { name: "Referral Agreement" },
  { name: "Commission Agreement" },
];

/**
 * Hydration-safe generic form filler, extracted from critical-flow.spec.ts.
 * Fills every visible control with plausible values; distinct text values so
 * party-name fields never collide ("parties must be distinct entities").
 */
async function fillVisibleControls(page: Page) {
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
    const optionCount = await select.locator("option:not([disabled])").count();
    if (optionCount > 0) {
      await select.selectOption({ index: 1 });
      // Controlled <select> needs change/input events after hydration.
      await select.dispatchEvent("change");
      await select.dispatchEvent("input");
    }
  }
  const textboxes = page.locator("input[type='text']:visible");
  const textCount = await textboxes.count();
  for (let i = 0; i < textCount; i++) {
    await textboxes.nth(i).fill(`E2E Test Value ${i + 1}`);
  }
  const areas = page.locator("textarea:visible");
  const areaCount = await areas.count();
  for (let i = 0; i < areaCount; i++) {
    await areas.nth(i).fill("E2E test purpose");
  }
}

// Each test below is fully independent: it performs its own login, opens its
// own wizard session, and creates its own agreement. No test reads state
// written by a sibling, so a failure never skips the others (the old
// describe.serial wrapper cascaded "did not run" skips from one flaky test).
test.describe("New catalog types through the wizard", () => {
  for (const t of NEW_TYPES) {
    test(`create agreement: ${t.name}`, async ({ page }) => {
      await sharedLogin(page);
      await page.goto("/agreements/new");
      await expect(page.locator("h1")).toContainText("Select Agreement Type", {
        timeout: 15000,
      });

      // 1. Click the type card.
      const card = page
        .locator("button.text-left", { hasText: t.name })
        .first();
      await expect(card).toBeVisible({ timeout: 10000 });
      await card.click();

      // 2. Wizard header shows the selected type; the per-type questionnaire
      //    (GET /agreements/types/{id}/questions) renders below.
      await expect(page.locator("h1")).toContainText(t.name, {
        timeout: 15000,
      });
      await page
        .locator("input[type='date'], input[type='text'], textarea, select")
        .first()
        .waitFor({ state: "visible", timeout: 8000 })
        .catch(() => {});

      // 3+4. Fill every visible control and walk sections with Next until
      //    "Create Agreement" appears.
      //
      // Each wizard view is hydrated fresh after a step change: its React
      // controlled inputs silently ignore programmatic fills until the
      // onChange handlers attach. Filling once and clicking Next therefore
      // races the remount — answers land empty and the save/validate calls
      // report "Missing required field" for every question of the view that
      // was skipped.
      //
      // The probe must target a control INSIDE the section panel (class
      // "bg-white shadow rounded-lg"): the Agreement Title input sits
      // outside every view, is always hydrated, and would report "stuck"
      // while the view's own controls are still dead. Under a cold dev
      // server this race is the difference between a 1-minute and a
      // 7-minute suite. Per-kind predicates: text fills are numbered
      // globally, so any "E2E Test Value <n>" proves the handler attached;
      // a dropped select fill leaves the empty placeholder selected.
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
          await fillVisibleControls(page);
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

      // Walk the remaining views. handleNext saves before advancing, so the
      // button label flips to "Saving..." (disabled) mid-round-trip. Matching
      // both labels lets Playwright auto-wait for the re-enabled "Next"
      // instead of the loop mistaking the transient save state for the last
      // step (whose button reads "Create Agreement").
      let guard = 0;
      while (guard < 8) {
        const nextBtn = page
          .locator("button:has-text('Next'), button:has-text('Saving')")
          .first();
        if (!(await nextBtn.isVisible().catch(() => false))) break;
        await nextBtn.click();
        guard += 1;
        // The freshly mounted view needs its own fill-verification before
        // the next click; bail out if we've reached the final step.
        await fillUntilStuck();
        if (!(await page
          .locator("button:has-text('Next'), button:has-text('Saving')")
          .first()
          .isVisible()
          .catch(() => false))) {
          break;
        }
      }

      // 5. Submit: "Create Agreement" triggers handleFinish — create draft,
      //    save answers, validate, navigate to the agreement detail page.
      for (let attempt = 0; attempt < 5; attempt++) {
        await fillVisibleControls(page);
        const createBtn = page
          .locator("button:has-text('Create Agreement')")
          .first();
        if (!(await createBtn.isVisible().catch(() => false))) {
          break;
        }
        await createBtn.click();
        const navigated = await page
          .waitForURL(/\/agreements\/[0-9a-f-]{36}/, { timeout: 5000 })
          .then(() => true)
          .catch(() => false);
        if (navigated) break;
        await page.waitForTimeout(400);
      }

      // 6. Land on the agreement detail page. The h1 renders only after the
      //    page's agreement fetch resolves; under a fully loaded dev server
      //    (cold compile + parallel suites) that can exceed 5s.
      await expect(page).toHaveURL(/\/agreements\/[0-9a-f-]{36}/, {
        timeout: 10000,
      });
      await expect(page.locator("h1")).toBeVisible({ timeout: 15000 });
    });
  }
});
