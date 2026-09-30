"use client";

import { useEffect } from "react";
import {
  installInputMaskingGuard,
  isSessionReplayEnabled,
} from "@/lib/privacy";

/**
 * Mounts the app-wide privacy defaults on the client:
 *  - globally masks sensitive inputs (passwords, OTP, card, DOB) so any
 *    present or future session-replay/error tooling never records them;
 *  - logs once in development when replay is (still) off, so the default
 *    stays visible to operators.
 *
 * Renders nothing.
 */
export default function PrivacyDefaults() {
  useEffect(() => {
    installInputMaskingGuard();
    if (process.env.NODE_ENV !== "production" && !isSessionReplayEnabled()) {
      // eslint-disable-next-line no-console
      console.debug(
        "[privacy] session replay disabled by default (set NEXT_PUBLIC_SESSION_REPLAY_ENABLED=true to opt in)"
      );
    }
  }, []);

  return null;
}
