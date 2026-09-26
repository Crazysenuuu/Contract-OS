import { test, expect, Page } from "@playwright/test";
import { login as sharedLogin } from "./helpers";

// 13 independent wizard runs against the dev server: cold-route compiles and
// hydration races routinely exceeded the 30s suite default in CI (all 12
// failures were 30000ms timeouts; 2 passed on retry). 90s per test keeps the
// fill-until-hydrated loops safe while staying inside the job budget.
test.setTimeout(90_000);

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
  // Every fill below is bounded and failure-tolerant: the wizard can advance
  // to the next (or final) view while this helper is running — the last view
  // (Clauses) has no text inputs at all. An unbounded fill on a detached
  // element waits the suite default and hangs the whole test
  // (locator.fill: input[type=text].nth(1)).
  const FILL_TIMEOUT_MS = 2_000;
  const dates = page.locator("input[type='date']:visible");
  const dateCount = await dates.count();
  for (let i = 0; i < dateCount; i++) {
    await dates
      .nth(i)
      .fill("2026-06-01", { timeout: FILL_TIMEOUT_MS })
      .catch(() => {});
  }
  const numbers = page.locator("input[type='number']:visible");
  const numberCount = await numbers.count();
  for (let i = 0; i < numberCount; i++) {
    await numbers
      .nth(i)
      .fill("12", { timeout: FILL_TIMEOUT_MS })
      .catch(() => {});
  }
  const selects = page.locator("select:visible");
  const selectCount = await selects.count();
  for (let i = 0; i < selectCount; i++) {
    const select = selects.nth(i);
    const optionCount = await select.locator("option:not([disabled])").count();
    if (optionCount > 0) {
      try {
        await select.selectOption({ index: 1 }, { timeout: FILL_TIMEOUT_MS });
        // Controlled <select> needs change/input events after hydration.
        await select.dispatchEvent("change");
        await select.dispatchEvent("input");
      } catch {
        // View changed mid-loop — handled by the next fillUntilStuck pass.
      }
    }
  }
  const textboxes = page.locator("input[type='text']:visible");
  const textCount = await textboxes.count();
  for (let i = 0; i < textCount; i++) {
    // Distinct values so party-name fields never collide ("parties must be
    // distinct entities").
    await textboxes
      .nth(i)
      .fill(`E2E Test Value ${i + 1}`, { timeout: FILL_TIMEOUT_MS })
      .catch(() => {});
  }
  const areas = page.locator("textarea:visible");
  const areaCount = await areas.count();
  for (let i = 0; i < areaCount; i++) {
    await areas
      .nth(i)
      .fill("E2E test purpose", { timeout: FILL_TIMEOUT_MS })
      .catch(() => {});
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

      // Walk the remaining views until only "Create Agreement" (final step)
      // remains. The Next button flips to a disabled "Saving..." mid-save;
      // matching it would race the final step, where it unmounts instead of
      // re-enabling — and Playwright's actionability wait then hangs the
      // click forever (trace: click 'Next, Saving' with no matching after).
      // Match the enabled "Next" only, bound the click, and re-check before
      // every iteration.
      const NEXT_BTN = "button:has-text('Next'):enabled";
      let guard = 0;
      while (guard < 8) {
        const nextBtn = page.locator(NEXT_BTN).first();
        if (!(await nextBtn.isVisible().catch(() => false))) break;
        await nextBtn.click({ timeout: 10_000 }).catch(() => {});
        guard += 1;
        // The freshly mounted view needs its own fill-verification before
        // the next click; bail out if we've reached the final step.
        await fillUntilStuck();
        if (!(await page
          .locator(NEXT_BTN)
          .first()
          .isVisible()
          .catch(() => false))) {
          break;
        }
      }

      // 5. Submit: "Create Agreement" triggers handleFinish — create draft,
      //    save answers, validate, navigate to the agreement detail page.
      //    The detail route compiles on first hit under the dev server, so
      //    the URL can stay on /agreements/new for tens of seconds after the
      //    (already successful) POST — give waitForURL real headroom.
      for (let attempt = 0; attempt < 3; attempt++) {
        await fillVisibleControls(page);
        const createBtn = page
          .locator("button:has-text('Create Agreement')")
          .first();
        if (!(await createBtn.isVisible().catch(() => false))) {
          break;
        }
        await createBtn.click({ timeout: 10_000 }).catch(() => {});
        const navigated = await page
          .waitForURL(/\/agreements\/[0-9a-f-]{36}/, { timeout: 30_000 })
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
