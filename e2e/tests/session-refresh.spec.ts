import { test, expect, Page } from "@playwright/test";
import { login } from "./helpers";

/**
 * Access-token expiry simulation (spec §7/§34: 15-minute access tokens +
 * rotating refresh tokens).
 *
 * The web session layer must renew an expired access token transparently:
 * any 401 on an authenticated call triggers exactly one /auth/refresh and a
 * replay of the failed request, so the user never sees a login wall while
 * the refresh token is valid. When the refresh token itself is dead, the
 * session must end with a redirect to /login.
 *
 * An expired JWT and a garbage JWT are indistinguishable to the backend —
 * both fail verification with 401 — so overwriting the stored access token
 * deterministically simulates expiry without waiting 15 minutes.
 */

async function sabotageAccessToken(page: Page, value: string) {
  await page.evaluate((v) => window.localStorage.setItem("token", v), value);
}

async function sabotageRefreshToken(page: Page, value: string) {
  await page.evaluate(
    (v) => window.localStorage.setItem("refresh_token", v),
    value
  );
}

test.describe("session refresh", () => {
  test("expired access token is refreshed transparently mid-session", async ({
    page,
  }) => {
    await login(page);

    // Simulate expiry: the stored access token is now invalid, exactly like
    // a token that crossed its 15-minute exp claim.
    await sabotageAccessToken(page, "expired-access-token");

    // A full navigation re-hydrates the session from storage: getMe with the
    // expired token 401s, the interceptor refreshes with the (valid) stored
    // refresh token, and the request replays with the fresh access token.
    await page.goto("/dashboard");

    // Session fully restored — dashboard data renders, no login wall.
    await expect(
      page.getByRole("heading", { name: /Welcome back/ })
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("Agreements", { exact: true })).toBeVisible();

    // Proof the refresh actually happened: the access token in storage was
    // rotated away from the sabotaged value by the refresh response.
    const restoredToken = await page.evaluate(() =>
      window.localStorage.getItem("token")
    );
    expect(restoredToken).not.toBe("expired-access-token");
    expect(restoredToken).not.toBe("");
  });

  test("dead refresh token ends the session with a redirect to login", async ({
    page,
  }) => {
    await login(page);

    // Both credentials are now rejected by the backend.
    await sabotageAccessToken(page, "expired-access-token");
    await sabotageRefreshToken(page, "revoked-refresh-token");

    // Any guarded page must resolve the dead session instead of rendering.
    await page.goto("/data-governance");

    // The refresh attempt fails → tokens cleared → auth guard redirects.
    await page.waitForURL(/\/login/, { timeout: 15000 });

    // Credentials are wiped, not left half-dead in storage.
    const accessToken = await page.evaluate(() =>
      window.localStorage.getItem("token")
    );
    const refreshToken = await page.evaluate(() =>
      window.localStorage.getItem("refresh_token")
    );
    expect(accessToken).toBeNull();
    expect(refreshToken).toBeNull();
  });
});
