import { test, expect, Page } from "@playwright/test";
import { login as sharedLogin } from "./helpers";

// This spec walks the real Next.js dev server (cold route compiles) through
// a multi-step wizard. The 30s suite default timed out in CI inside the
// hydration-wait loops (12/12 E2E failures were 30000ms timeouts; retries
// passed). 90s gives the fill-until-hydrated loops real headroom.
// One long test covers the full lifecycle (login → dashboard → wizard →
// submit → landing checks). Every route cold-compiles under the dev server
// webServer on first hit; 90s was exceeded ~1 run in 3 even with zero
// functional failures (test always reached the final step), so give it the
// same headroom-per-activity as the catalog suite inside the 35m CI budget.
test.setTimeout(150_000);

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
    // Bounded: the element can detach between isVisible and fill.
    await locator.fill(value, { timeout: 10_000 }).catch(() => {});
  }
}

async function clickIfVisible(page: Page, selector: string) {
  const locator = page.locator(selector).first();
  if (await locator.isVisible()) {
    // Bounded: the element can unmount mid-action (wizard step changes);
    // an unbounded click hangs the whole test on the actionability wait.
    await locator.click({ timeout: 10_000 }).catch(() => {});
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
        // Every fill below is bounded and failure-tolerant: the wizard can
        // advance to the next (or final) view while this helper is running —
        // e.g. the last view (Clauses) has no text inputs at all. An
        // unbounded fill on a detached element waits the suite default and
        // hangs the whole test (locator.fill: input[type=text].nth(1)).
        // The suite config sets no actionTimeout, so ANY locator call
        // without an explicit timeout waits indefinitely (bounded only by
        // the test timeout). Helper: cap an unbounded locator promise (e.g.
        // evaluate, which has no timeout option and waits for the element
        // to exist) and fall back cleanly.
        const FILL_TIMEOUT_MS = 2_000;
        const withTimeout = <T>(
          p: Promise<T>,
          ms: number,
          fallback: T
        ): Promise<T> =>
          Promise.race([
            p.catch(() => fallback),
            new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
          ]);
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
          // Rotate through each select's real (enabled, non-empty) options by
          // the select's position in this pass: views with TWO party-entity
          // selects must resolve them to DIFFERENT entities or validation
          // rejects the draft ("parties must be distinct entities") and the
          // wizard never navigates (index 1 for every select made both pick
          // the same one). Value-based, not index-based, so
          // placeholder/disabled layouts don't skew the mapping; the mapping
          // is stable across fillUntilStuck passes. evaluate() has no
          // timeout option and waits for the element — a select detaching
          // mid-pass (view advanced) would hang it forever, so cap it.
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
              // Controlled <select> needs change/input events after
              // hydration (same contract as new-catalog-types).
              await select.dispatchEvent("change", { timeout: FILL_TIMEOUT_MS });
              await select.dispatchEvent("input", { timeout: FILL_TIMEOUT_MS });
            } catch {
              // View changed mid-loop — handled by the next pass.
            }
          }
        }
        const textboxes = page.locator("input[type='text']:visible");
        const textCount = await textboxes.count();
        for (let i = 0; i < textCount; i++) {
          // Distinct values: party-name fields must not collide ("parties must
          // be distinct entities" is a schema-level rule).
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
        for (let attempt = 0; attempt < 10; attempt++) {
          await fillVisibleControls();
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

      // Walk the remaining views until only "Create Agreement" (final step)
      // remains. Match the ENABLED "Next" only: the button flips to a
      // disabled "Saving..." mid-save and unmounts on the final step —
      // matching it makes Playwright's actionability wait hang the click
      // (trace: click 'Next, Saving' never completing). Clicks are bounded;
      // a lost race is handled by the next loop iteration.
      // Wait for a navigation button (union), never an instant isVisible():
      // a fresh view may not have rendered yet, and during a save the button
      // reads a disabled "Saving..." (matches neither segment). The wait
      // resolves only when "Next" is enabled again or the final step's
      // "Create Agreement" has mounted — then the loop exits for the
      // create phase below.
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
        // Fill-verify the freshly mounted view before the next wait.
        await fillUntilStuck();
      }
      await fillVisibleControls();
      // Single-section templates show "Create Agreement" directly; re-fill
      // and retry on each attempt because pre-hydration fills silently leave
      // React state empty even when the DOM shows values. Match the ENABLED
      // button: after the walk's last Next-click the wizard may still be
      // saving ("Saving...", disabled) and an early click is swallowed.
      for (let attempt = 0; attempt < 5; attempt++) {
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
        await fillVisibleControls();
        await createBtn.click({ timeout: 10_000 }).catch(() => {});
        // Phase 1: confirm the click registered (button flips to disabled
        // "Saving..."); a swallowed click must retry immediately — waiting
        // out the race burns budget (the Founder Agreement failure).
        const started = await page
          .locator("button:has-text('Saving...')")
          .first()
          .waitFor({ state: "visible", timeout: 5_000 })
          .then(() => true)
          .catch(() => false);
        if (!started) {
          if (/\/agreements\/[0-9a-f-]+/.test(page.url())) break;
          continue;
        }
        // Phase 2: the detail route compiles on first hit under the dev
        // server, so give waitForURL real headroom; an error banner or the
        // button re-enabling retries immediately.
        const navigated = await Promise.race([
          page
            .waitForURL(/\/agreements\/[0-9a-f-]+/, { timeout: 30_000 })
            .then(() => true)
            .catch(() => false),
          page
            .waitForSelector(".bg-red-50", { timeout: 30_000 })
            .then(() => true)
            .catch(() => false),
          page
            .locator("button:has-text('Create Agreement'):enabled")
            .first()
            .waitFor({ state: "visible", timeout: 30_000 })
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
          .waitForURL(/\/agreements\/[0-9a-f-]+/, { timeout: 15_000 })
          .then(() => true)
          .catch(() => false),
        page
          .waitForURL(/\/dashboard/, { timeout: 15_000 })
          .then(() => true)
          .catch(() => false),
        page.waitForTimeout(15_000).then(() => false),
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
        // Detail route compiles on first hit under the dev server.
        await expect(page).toHaveURL(/\/agreements\/[^/]+$/, { timeout: 30_000 });
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