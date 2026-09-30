"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useNotificationSocket } from "@/hooks/useNotificationSocket";
import {
  getUnreadNotificationCount,
  markAllNotificationsRead,
} from "@/lib/api";

const POLL_INTERVAL_MS = 60_000;

export default function NotificationBell() {
  const { token } = useAuth();
  const [unread, setUnread] = useState<number | null>(null);

  const reloadCount = useCallback(() => {
    if (!token) return;
    getUnreadNotificationCount(token)
      .then((r) => setUnread(r.count))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    reloadCount();
    const interval = window.setInterval(reloadCount, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [reloadCount]);

  // Live pushes: bump the badge while connected (real-time delivery, spec 1.14).
  const handleMessage = useCallback(() => {
    setUnread((prev) => (prev ?? 0) + 1);
  }, []);

  const { state: socketState } = useNotificationSocket(token, handleMessage);

  const handleClick = () => {
    if (!token) return;
    markAllNotificationsRead(token)
      .then((r) => {
        if (r.marked > 0) setUnread(0);
      })
      .catch(() => {});
  };

  return (
    <Link
      href="/notifications"
      onClick={handleClick}
      aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
      className="relative inline-flex items-center justify-center p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100"
      title={unread ? `${unread} unread notifications` : "No unread notifications"}
    >
      <span
        className={`text-lg leading-none ${
          socketState === "open" ? "" : "opacity-60"
        }`}
        aria-hidden
      >
        🔔
      </span>
      {unread !== null && unread > 0 && (
        <span className="absolute -top-0.5 -right-0.5 min-w-[1.15rem] h-[1.15rem] px-1 rounded-full bg-rose-600 text-white text-[10px] font-bold flex items-center justify-center">
          {unread > 99 ? "99+" : unread}
        </span>
      )}
    </Link>
  );
}