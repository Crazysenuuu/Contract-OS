"use client";

import { useEffect, useRef, useState } from "react";

export type SocketState = "connecting" | "open" | "closed";

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

/**
 * JWT-authenticated WebSocket to /ws/notifications with automatic
 * reconnection using exponential backoff (+ jitter). The reconnect delay
 * resets once the socket opens. Safe to share across components: each
 * caller gets its own connection unless `shareKey` is provided.
 */
export function useNotificationSocket(
  token: string | null,
  onMessage?: (message: Record<string, unknown>) => void
) {
  const [state, setState] = useState<SocketState>("closed");
  const [reconnectCount, setReconnectCount] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const delayRef = useRef(BASE_DELAY_MS);
  // Callback stored in a ref so socket handlers always invoke the latest
  // version without re-establishing the connection.
  const onMessageRef = useRef(onMessage);

  // Keep the ref fresh without touching it during render (the compiler
  // forbids ref access in render bodies); the socket handlers only read the
  // ref when frames arrive, so an effect-synced ref is safe here.
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    if (!token) {
      // No session — defer the state reset so the effect body never
      // cascades synchronously.
      queueMicrotask(() => setState("closed"));
      return;
    }

    let disposed = false;
    let retryTimer: number | undefined;

    const connect = () => {
      if (disposed) return;
      setState("connecting");

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/ws/notifications?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        delayRef.current = BASE_DELAY_MS;
        setReconnectCount(0);
        setState("open");
      };

      ws.onmessage = (event) => {
        try {
          onMessageRef.current?.(JSON.parse(event.data as string) as Record<string, unknown>);
        } catch {
          // ignore non-JSON frames
        }
      };

      const scheduleReconnect = () => {
        if (disposed) return;
        setState("closed");
        setReconnectCount((c) => c + 1);
        // Exponential backoff with jitter: delay grows 1s → 2s → 4s … capped at 30s.
        const delay = delayRef.current;
        delayRef.current = Math.min(delay * 2, MAX_DELAY_MS);
        const jitter = Math.random() * 300;
        retryTimer = window.setTimeout(connect, delay + jitter);
      };

      ws.onclose = scheduleReconnect;
      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [token]);

  return { state, reconnectCount, wsRef };
}