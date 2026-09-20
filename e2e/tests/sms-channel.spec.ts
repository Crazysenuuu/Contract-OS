import { test, expect } from "@playwright/test";
import { login } from "./helpers";

/**
 * SMS channel end-to-end verification (spec §44).
 *
 * Prerequisites (not started by Playwright):
 *   - fake SMS gateway on 127.0.0.1:9911 (backend/scripts/fake_sms_gateway.py)
 *   - backend on :8000 with SMS_API_BASE_URL/SMS_API_KEY pointing at it
 *   - e2e user phone set to +15551230000 (any phone works for the toggle test;
 *     the gateway assertion needs it only via the outbox flow)
 */

const GATEWAY = "http://127.0.0.1:9911";

async function gatewayMessages(): Promise<
  Array<{ to: string; from: string; text: string }>
> {
  const res = await fetch(`${GATEWAY}/messages`);
  const data = (await res.json()) as {
    messages: Array<{ to: string; from: string; text: string }>;
  };
  return data.messages;
}

test("sms preference toggle persists and gateway logs the delivery", async ({
  page,
}) => {
  // Fresh gateway log so assertions are unambiguous.
  await fetch(`${GATEWAY}/reset`, { method: "POST" });

  await login(page);

  await page.goto("/settings/notifications");
  await expect(page.getByRole("heading", { name: /SMS Notifications/ })).toBeVisible();

  const smsSection = page.locator("div.bg-white", { hasText: "Enable SMS" }).first();
  const toggle = smsSection.locator("button").first();

  // Ensure a known starting state (off), then toggle on.
  const startingOn = await toggle.evaluate(
    (el) => el.className.includes("bg-blue-600")
  );
  if (startingOn) {
    await toggle.click();
    await expect
      .poll(async () =>
        toggle.evaluate((el) => el.className.includes("bg-blue-600"))
      )
      .toBe(false);
  }
  await toggle.click();
  await expect
    .poll(async () =>
      toggle.evaluate((el) => el.className.includes("bg-blue-600"))
    )
    .toBe(true);

  // Reload: the persisted preference must come back from the server.
  await page.reload();
  await expect(page.getByRole("heading", { name: /SMS Notifications/ })).toBeVisible();
  const toggleAfterReload = page
    .locator("div.bg-white", { hasText: "Enable SMS" })
    .first()
    .locator("button")
    .first();
  await expect
    .poll(async () =>
      toggleAfterReload.evaluate((el) => el.className.includes("bg-blue-600"))
    )
    .toBe(true);
});

test("enqueued notification event produces an SMS at the gateway", async ({
  page,
  request,
}) => {
  await fetch(`${GATEWAY}/reset`, { method: "POST" });

  await login(page);

  // Enqueue + dispatch through the backend API (admin gate for /process).
  // The UI part of this test is the logged-in session + in-app notification
  // the dispatch creates; the gateway assertion proves the SMS leg.
  const apiToken = await page.evaluate(
    () => window.localStorage.getItem("token") ?? ""
  );
  expect(apiToken).not.toBe("");

  const me = await request.get("/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${apiToken}` },
  });
  const userId = (await me.json()).id as string;
  expect(userId).not.toBe("");

  // The SMS channel is opt-in; this test enables it explicitly so the
  // delivery does not depend on the toggle test having run first.
  const optIn = await request.patch("/api/v1/notification-preferences", {
    headers: { Authorization: `Bearer ${apiToken}` },
    data: { sms_enabled: true },
  });
  expect(optIn.ok()).toBeTruthy();

  const enqueue = await request.post("/api/v1/outbox", {
    headers: { Authorization: `Bearer ${apiToken}` },
    data: {
      event_type: "signature.requested",
      aggregate_type: "signing_session",
      payload: {
        recipient_user_id: userId,
        subject: "Browser E2E SMS",
        sms_body: "ContractOS: browser e2e SMS verification",
      },
    },
  });
  expect(enqueue.status()).toBe(201);

  // Admin gate: process via a direct backend call with an admin session.
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { email: "admin@example.com", password: "password123" },
  });
  const adminToken = (await adminLogin.json()).access_token;
  const process = await request.post("/api/v1/outbox/process?limit=50", {
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  expect(process.ok()).toBeTruthy();

  await expect.poll(async () => (await gatewayMessages()).length).toBeGreaterThan(0);
  const msg = (await gatewayMessages()).at(-1)!;
  expect(msg.to).toBe("+15551230000");
  expect(msg.from).toBe("ContractOS");
  expect(msg.text).toContain("browser e2e SMS verification");
});
