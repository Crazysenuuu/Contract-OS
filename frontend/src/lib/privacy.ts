/**
 * Privacy defaults for third-party product analytics / session-replay tools.
 *
 * ContractOS currently ships NO analytics or session-replay vendor. This
 * module exists so that if one is ever integrated, it comes up with
 * privacy-protective defaults instead of whatever the SDK's defaults are:
 *
 *  - Session replay is OFF unless an operator explicitly flips
 *    `NEXT_PUBLIC_SESSION_REPLAY_ENABLED=true` (and the vendor's own replay
 *    toggle stays off even when event analytics is enabled).
 *  - Sensitive inputs (passwords, OTP codes, card fields, DOB used for the
 *    COPPA age check) are masked via `data-replay-mask` plus standard
 *    autocomplete hints, so no vendor records their values even if replay is
 *    later enabled.
 */

/**
 * Whether session replay may be enabled. Defaults to false — replay must be
 * an explicit operator decision, never a vendor default.
 */
export function isSessionReplayEnabled(): boolean {
  return (
    process.env.NEXT_PUBLIC_SESSION_REPLAY_ENABLED === "true" &&
    process.env.NODE_ENV === "production"
  );
}

/**
 * Standard props every analytics/replay SDK init should spread. Denies
 * session recording and capture of form keystrokes by default.
 */
export function privacyTelemetryDefaults(): Record<string, unknown> {
  return {
    // Replay is off by default; even when enabled, mask everything not
    // explicitly allow-listed.
    disable_session_recording: !isSessionReplayEnabled(),
    mask_all_inputs: true,
    // Never ship network bodies — agreements contain confidential terms.
    capture_network_body: false,
    // Do not persist the clipboard (users paste contract text and codes).
    capture_clipboard: false,
  };
}

/**
 * Props for <input>/<textarea> elements whose values must never appear in a
 * session replay, error breadcrumb, or analytics payload.
 *
 * Usage: <input {...maskedInputProps} ... />
 * (with the concrete value/onChange supplied by the caller)
 */
export const maskedInputProps = {
  "data-replay-mask": true,
  autoComplete: "off" as const,
};

/**
 * Install a global guard that tags every sensitive input the app renders —
 * current and future — with `data-replay-mask`, even if a call site forgets
 * the explicit props. Call once from a client component; it is idempotent.
 *
 * Heuristics (conservative, low false-positive):
 *  - type=password / autocomplete containing "password" or "one-time-code"
 *  - name/id containing password, passwd, mfa, otp, token, secret, card,
 *    cvc, cvv, ssn, or date_of_birth
 */
export function installInputMaskingGuard(): void {
  if (typeof document === "undefined") return;

  const SENSITIVE = /password|passwd|mfa|otp|token|secret|card|cvc|cvv|ssn|date_of_birth/i;

  const apply = (el: Element) => {
    const input = el as HTMLInputElement | HTMLTextAreaElement;
    const type = (input.getAttribute("type") || "").toLowerCase();
    const auto = (input.getAttribute("autocomplete") || "").toLowerCase();
    const ident = `${input.name || ""} ${input.id || ""}`;
    const sensitive =
      type === "password" ||
      auto.includes("password") ||
      auto.includes("one-time-code") ||
      SENSITIVE.test(ident);
    if (sensitive && input.getAttribute("data-replay-mask") !== "true") {
      input.setAttribute("data-replay-mask", "true");
      // Belt-and-braces: many vendors honor these attribute names.
      if (!input.getAttribute("data-1p-ignore")) {
        input.setAttribute("data-1p-ignore", "true");
      }
    }
  };

  const scan = (root: ParentNode) =>
    root.querySelectorAll("input, textarea").forEach(apply);

  scan(document);
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach((n) => {
        if (n.nodeType !== Node.ELEMENT_NODE) return;
        const el = n as Element;
        if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") apply(el);
        scan(el);
      });
      if (m.type === "attributes" && m.target) apply(m.target as Element);
    }
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["type", "name", "id", "autocomplete"],
  });
}
