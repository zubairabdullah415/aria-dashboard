"use client";

/**
 * Sticky Restaurant Branding Header
 * ────────────────────────────────────────────────────────────────────────────
 * Builds trust at first glance with:
 *   • Restaurant name (large, serif-leaning weight)
 *   • Tagline (small, muted) — e.g. "Fine dining · Karachi"
 *   • "Online" status pill with a live green pulse dot
 *   • Powered-by LiftUp AI micro-credit on the right
 *
 * Sticks to the top of the viewport on mobile and desktop. Respects iOS
 * safe-area inset so it doesn't bleed under the notch.
 */

import { AriaAvatar } from "./aria-avatar";
import { ariaConfig } from "@/lib/aria/config";

export function ChatHeader() {
  return (
    <header
      className="sticky top-0 z-30 safe-top"
      role="banner"
    >
      <div className="bg-card/95 backdrop-blur-md border-b border-border/70 shadow-[0_1px_12px_-8px_rgba(0,0,0,0.12)]">
        <div className="mx-auto max-w-2xl px-4 py-3 flex items-center gap-3">
          {/* Avatar with online pulse */}
          <div className="relative shrink-0">
            <AriaAvatar size="md" pulsing />
          </div>

          {/* Restaurant name + tagline */}
          <div className="flex-1 min-w-0">
            <h1 className="text-base font-semibold leading-tight text-foreground truncate">
              {ariaConfig.restaurantName}
            </h1>
            <p className="text-xs text-muted-foreground truncate">
              {ariaConfig.tagline}
            </p>
          </div>

          {/* Online status pill */}
          <div
            className="flex items-center gap-1.5 rounded-full bg-emerald-50 border border-emerald-200/70 px-2.5 py-1"
            aria-label="Aria is online"
          >
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-600" />
            </span>
            <span className="text-[11px] font-medium text-emerald-700">
              Online
            </span>
          </div>
        </div>

        {/* Powered-by LiftUp micro-credit */}
        <div className="mx-auto max-w-2xl px-4 pb-2 -mt-1 flex justify-end">
          <span className="text-[10px] text-muted-foreground/80 tracking-wide uppercase">
            Powered by{" "}
            <span className="font-semibold text-primary/90">LiftUp AI</span>
          </span>
        </div>
      </div>
    </header>
  );
}
