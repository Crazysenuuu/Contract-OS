import { test, expect, Page } from "@playwright/test";
import { login as sharedLogin } from "./helpers";

// 13 independent wizard runs against the dev server: cold-route compiles and
// hydration races routinely exceeded the 30s suite default in CI (all 12
// failures were 30000ms timeouts; 2 passed on retry). Each create attempt
// can cost up to ~30s (Saving-flip wait + outcome race), so 90s left no
// room for a single swallowed click on a cold server; 120s covers 3
// attempts + the walk while 13 tests stay well inside the 35m job budget.
test.setTimeout(120_000);

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
// The suite config sets no actionTimeout, so ANY locator call without an
// explicit timeout waits indefinitely (bounded only by the test timeout).
// Helper: cap an unbounded locator promise (e.g. evaluate, which has no
// timeout option and waits for the element to exist) and fall back cleanly.
function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return Promise.race([
    p.catch(() => fallback),
    new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
  ]);
}

async function fillVisibleControls(page: Page) {
  // Every fill below is bounded and failure-tolerant: the wizard can advance
  // to the next (or final) view while this helper is running — the last view
  // (Clauses) has no text inputs at all. An unbounded fill on a detached
  // element waits the suite default and hangs the whole test
  // (locator.fill: input[type=text].nth(1)).
  //
  // Pure queries have NO timeout option at all (locator.count(), etc.) and
  // were observed hanging indefinitely on the final Clauses view: both
  // Purchase Agreement runs burned the entire 120s budget inside
  // textboxes.count() while the page itself rendered fine and nothing was
  // left to fill. Cap the WHOLE pass — if any single query wedges, the
  // caller proceeds to the next step (on the final view: straight to the
  // Create click) instead of losing the test.
  const FILL_PASS_DEADLINE_MS = 15_000;
  await withTimeout(
    fillVisibleControlsInner(page),
    FILL_PASS_DEADLINE_MS,
    undefined
  );
}

async function fillVisibleControlsInner(page: Page) {
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
    // Rotate through each select's real (enabled, non-empty) options by the
    // select's position in this pass: views with TWO party-entity selects
    // must resolve them to DIFFERENT entities or validation rejects the
    // draft ("parties must be distinct entities") and the wizard never
    // navigates (index 1 for every select made both pick the same one).
    // Value-based, not index-based, so placeholder/disabled layouts don't
    // skew the mapping; the mapping is stable across fillUntilStuck passes.
    // evaluate() has no timeout option and waits for the element — a select
    // detaching mid-pass (view advanced) would hang it forever, so cap it.
    const optionValues = await withTimeout(
      select.evaluate((el) =>
        Array.from((el as HTMLSelectElement).options)
          .filter((o) => !o.disabled && o.value !== "")
          .map((o) => o.value)
      ),
      FILL_TIMEOUT_MS,
      [] as string[]
    );
    const value = optionValues[i % Math.max(1, optionValues.length)];
    if (value !== undefined) {
      try {
        await select.selectOption(value, { timeout: FILL_TIMEOUT_MS });
        // Controlled <select> needs change/input events after hydration.
        await select.dispatchEvent("change", { timeout: FILL_TIMEOUT_MS });
        await select.dispatchEvent("input", { timeout: FILL_TIMEOUT_MS });
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
        for (let attempt = 0; attempt < 10; attempt++) {
          await fillVisibleControls(page);
          // Verify EVERY panel control holds a value, not just the first:
          // a single-control probe passes while the remaining fills race
          // hydration and silently drop (the Shareholders failure — the
          // section view saved only effective_date and validation rejected
          // 9 missing required fields). Explicit inputValue timeouts:
          // unbounded waits hang when the view advances mid-check.
          const panel = page.locator(
            `${SECTION_PANEL} input:visible, ${SECTION_PANEL} select:visible, ${SECTION_PANEL} textarea:visible`
          );
          const count = await panel.count().catch(() => 0);
          let allFilled = count > 0;
          for (let i = 0; i < count && allFilled; i++) {
            const v = await panel
              .nth(i)
              .inputValue({ timeout: 2_000 })
              .catch(() => null);
            if (v === null || v === "") allFilled = false;
          }
          if (allFilled) return;
          await page.waitForTimeout(400);
        }
      };

      // First view: fill until hydration confirms.
      await fillUntilStuck();

      // Walk the remaining views until the final step ("Create Agreement")
      // is offered. Match a UNION of both navigation buttons and WAIT for it:
      // a freshly mounted view may not have rendered yet, and during a save
      // the button reads a disabled "Saving..." (matches neither segment) —
      // an instant isVisible() check breaks the loop mid-wizard on slow dev
      // servers (the Shareholders CI failure: walk exited on step 2, then no
      // Create button ever appeared). Waiting on the union absorbs both gaps:
      // it resolves only when "Next" is enabled again or the final step's
      // "Create Agreement" has mounted.
      const NAV_BTN =
        "button:has-text('Next'):enabled, button:has-text('Create Agreement')";
      let guard = 0;
      while (guard < 8) {
        const nav = page.locator(NAV_BTN).first();
        const appeared = await nav
          .waitFor({ state: "visible", timeout: 15_000 })
          .then(() => true)
          .catch(() => false);
        if (!appeared) break;
        const label = (await nav.textContent().catch(() => "")) ?? "";
        if (label.includes("Create Agreement")) break; // final step reached
        await nav.click({ timeout: 10_000 }).catch(() => {});
        guard += 1;
        // The freshly mounted view needs its own fill-verification before
        // the next wait-for-navigation.
        await fillUntilStuck();
      }

      // 5. Submit: "Create Agreement" triggers handleFinish — create draft,
      //    save answers, validate, navigate to the agreement detail page.
      //    Wait for the button to be ENABLED first: after the walk's last
      //    Next-click the wizard may still be finishing its save ("Saving...",
      //    disabled) — an early click is silently swallowed and handleFinish
      //    never runs (no validate call, no navigation, test times out).
      //    The detail route also compiles on first hit under the dev server,
      //    so waitForURL gets real headroom.
      for (let attempt = 0; attempt < 3; attempt++) {
        const createBtn = page
          .locator("button:has-text('Create Agreement'):enabled")
          .first();
        if (
          !(await createBtn
            .waitFor({ state: "visible", timeout: 10_000 })
            .then(() => true)
            .catch(() => false))
        ) {
          break;
        }
        await fillVisibleControls(page);
        await createBtn.click({ timeout: 10_000 }).catch(() => {});
        // Phase 1: confirm the click registered — handleFinish flips the
        // button to a disabled "Saving...". If it never flips, the click
        // was swallowed (React re-render race — the Founder Agreement
        // failure burned 30s per attempt on a dead waitForURL).
        const started = await page
          .locator("button:has-text('Saving...')")
          .first()
          .waitFor({ state: "visible", timeout: 5_000 })
          .then(() => true)
          .catch(() => false);
        if (!started) {
          if (/\/agreements\/[0-9a-f-]{36}/.test(page.url())) break;
          continue;
        }
        // Phase 2: three outcomes decide the next step IMMEDIATELY:
        // navigation wins; an error banner means validation failed
        // (retry); the button re-enabling without either means
        // handleFinish completed silently (retry now).
        const outcome = await Promise.race([
          page
            .waitForURL(/\/agreements\/[0-9a-f-]{36}/, { timeout: 25_000 })
            .then(() => "navigated" as const)
            .catch(() => "timeout" as const),
          page
            .locator(".bg-red-50")
            .first()
            .waitFor({ state: "visible", timeout: 25_000 })
            .then(() => "error" as const)
            .catch(() => "timeout" as const),
          page
            .locator("button:has-text('Create Agreement'):enabled")
            .first()
            .waitFor({ state: "visible", timeout: 25_000 })
            .then(() => "re-enabled" as const)
            .catch(() => "timeout" as const),
        ]);
        if (outcome === "navigated") break;
        if (outcome === "timeout") break;
        await page.waitForTimeout(400);
      }

      // 6. Land on the agreement detail page. The h1 renders only after the
      //    page's agreement fetch resolves; the route also compiles on first
      //    hit under the dev server, so give this real headroom.
      await expect(page).toHaveURL(/\/agreements\/[0-9a-f-]{36}/, {
        timeout: 30_000,
      });
      await expect(page.locator("h1")).toBeVisible({ timeout: 15000 });
    });
  }
});
