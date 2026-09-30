# Compliance Launch Checklist

Legal-compliance work landed in the codebase and the **human actions still
required before serving production traffic**. Items marked ⚠️ block the
related compliance claim; the rest are hygiene.

---

## 1. COPPA age gate — status: code complete

Shipped:
- DOB collected at signup; <13 rejected on the client (`/register`) and
  server (`/auth/register` + Pydantic validator).
- DOB is **never persisted** — only the derived `users.is_adult` boolean
  (data minimization).
- Tests: `backend/tests/test_coppa_age_gate.py`.

⚠️ Human actions:
- [ ] **Privacy policy** must disclose: DOB collected transiently for an age
      check and not stored; children under 13 may not use the service;
      parents may contact you to remove a child's account if one was
      created despite the gate.
- [ ] Verify marketing emails are **never** targeted at minors — with the
      gate in place this is structural, but confirm no ad audiences target
      under-13 users.

---

## 2. Self-hosted fonts — status: complete, no action

- `next/font/local` + vendored Inter woff2 (`frontend/src/fonts/`). No
  requests reach Google; visitor IPs are not exposed to a font CDN.
- No further action.

---

## 3. Session replay — status: locked off by default

Shipped:
- `frontend/src/lib/privacy.ts`: replay off unless
  `NEXT_PUBLIC_SESSION_REPLAY_ENABLED=true` **and** production; SDK-init
  defaults (no session recording, masked inputs, no network bodies, no
  clipboard).
- Global input-masking guard tags every sensitive input (password, OTP,
  card, DOB) with `data-replay-mask`.

Human actions (only if replay is ever enabled):
- [ ] Keep the vendor's own masking on top of `data-replay-mask`.
- [ ] Update the privacy policy + DPA before enabling.

---

## 4. Email compliance (CAN-SPAM) — ⚠️ env-dependent

Shipped:
- Unsubscribe link + physical postal address stamped on **every** outbound
  email (transactional + digest), single-footer guaranteed.
- `List-Unsubscribe` + `List-Unsubscribe-Post: One-Click` (RFC 8058)
  headers on SendGrid sends.
- One-click opt-out endpoint (`GET`/`POST /api/v1/email/opt-out`,
  HMAC-verified) that disables all email channels incl. digests.

⚠️ Human actions:
- [ ] **Set `EMAIL_POSTAL_ADDRESS`** to your real physical business address
      (default is a placeholder — CAN-SPAM requires a *valid* postal
      address; P.O. boxes are acceptable, fiction is not).
- [ ] **Set `EMAIL_OPT_OUT_TOKEN`** to a strong random secret (e.g.
      `openssl rand -hex 32`). With the default value the HMAC is
      forgeable; the endpoint logs a warning in that state.
- [ ] Set `APP_BASE_URL` to the production origin so opt-out links resolve.
- [ ] Monitor opt-outs: the endpoint is the unsubscribe system of record.

---

## 5. Auto-renewal disclosure (FTC Negative Option / ROSCA) — status: complete

- Renewal frequency, price, and cancel path displayed immediately next to
  every subscribe/switch button; cancellation behavior described matches
  actual backend behavior (immediate cancellation).
- No further action. If you ever add a *free trial converting to paid*,
  that requires its own explicit informed-consent flow — do not reuse this
  UI.

---

## 6. DMCA designated agent — ⚠️ registration required

Shipped:
- `/legal/dmca` policy page (agent, notice requirements, counter-notice,
  repeat-infringer policy, § 512(f) warning), linked from /login and
  /register.
- Public intake API `POST /api/v1/legal/dmca/notices` + admin queue
  (`/admin/dmca`) with `PATCH` processing endpoints.

⚠️ Human actions:
- [ ] **Register the designated agent** with the U.S. Copyright Office:
      https://dmca.copyright.gov (≈ $6 for 3 years). The registration
      statement must list the same name/address/email as `/legal/dmca` —
      keep them in sync; a mismatch can forfeit safe harbor.
- [ ] **Create the `dmca@contractos.lk` mailbox** and make sure it is
      actively monitored (agent must be reachable).
- [ ] Designate who processes notices and commit to the § 512(c)(1)(C)
      expeditious-removal timeline (target: 1–2 business days).
- [ ] Docket the 3-year renewal date.

---

## Database

- [ ] Run `make migrate` (applies `d5a6f8e1c2b3` — `users.is_adult`; and
      `e7b9a1c4d5f6` — `dmca_notices`).

---

## Post-launch hygiene

- [ ] Snapshot the DMCA queue + opt-out audit trail in backups.
- [ ] Annual review: postal address, agent details, renewal disclosures,
      and privacy-policy claims against actual behavior.
