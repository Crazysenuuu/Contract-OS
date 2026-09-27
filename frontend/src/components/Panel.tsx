"use client";

import { useState, useRef, useEffect } from "react";

interface PanelProps {
  /** Panel title shown in the header */
  title: string;
  /** Optional subtitle / description */
  subtitle?: string;
  /** Icon (emoji or JSX element) shown beside the title */
  icon?: React.ReactNode;
  /** Content rendered inside the panel body */
  children: React.ReactNode;
  /** Whether the panel starts collapsed. Default: false (expanded) */
  defaultCollapsed?: boolean;
  /** Extra class names applied to the outer wrapper */
  className?: string;
  /** Action element (e.g. a button) rendered at the trailing edge of the header */
  action?: React.ReactNode;
  /** If true, removes the inner padding from the body (useful for full-bleed tables) */
  noPadding?: boolean;
  /** Visual variant */
  variant?: "default" | "glass" | "flat";
}

export default function Panel({
  title,
  subtitle,
  icon,
  children,
  defaultCollapsed = false,
  className = "",
  action,
  noPadding = false,
  variant = "default",
}: PanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [height, setHeight] = useState<number | undefined>(
    defaultCollapsed ? 0 : undefined
  );
  const bodyRef = useRef<HTMLDivElement>(null);

  /* Animate height on toggle */
  useEffect(() => {
    if (!bodyRef.current) return;
    if (collapsed) {
      setHeight(bodyRef.current.scrollHeight);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setHeight(0));
      });
    } else {
      setHeight(bodyRef.current.scrollHeight);
      const el = bodyRef.current;
      const onEnd = () => setHeight(undefined);
      el.addEventListener("transitionend", onEnd, { once: true });
    }
  }, [collapsed]);

  const wrapperCls =
    variant === "glass"
      ? "glass-panel rounded-2xl overflow-hidden"
      : variant === "flat"
      ? "bg-white/50 rounded-2xl border border-gray-100 overflow-hidden"
      : "bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden";

  return (
    <div className={`${wrapperCls} ${className}`}>
      {/* Header */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left transition-colors duration-150 hover:bg-black/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400"
        aria-expanded={!collapsed}
      >
        <div className="flex items-center gap-3 min-w-0">
          {icon && (
            <span className="text-xl leading-none shrink-0 select-none">{icon}</span>
          )}
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900 truncate leading-tight">
              {title}
            </div>
            {subtitle && (
              <div className="text-xs text-gray-500 mt-0.5 truncate">{subtitle}</div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {action && (
            <div onClick={(e) => e.stopPropagation()}>{action}</div>
          )}
          <span
            className={`text-gray-400 transition-transform duration-300 ease-in-out ${collapsed ? "rotate-0" : "rotate-180"}`}
            aria-hidden="true"
          >
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </div>
      </button>

      {/* Body */}
      <div
        ref={bodyRef}
        style={height !== undefined ? { height } : undefined}
        className="overflow-hidden transition-[height] duration-300 ease-in-out"
        aria-hidden={collapsed}
      >
        <div
          className={`border-t border-gray-100 ${noPadding ? "" : "p-5"} transition-opacity duration-200 ${collapsed ? "opacity-0" : "opacity-100"}`}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
