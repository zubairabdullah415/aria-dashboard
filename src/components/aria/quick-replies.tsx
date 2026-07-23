"use client";

/**
 * Quick Reply Chip Row
 * ────────────────────────────────────────────────────────────────────────────
 * Horizontally scrollable, touch-friendly chips rendered under an Aria message.
 * Each chip sends its `value` as the user's next message on tap, then the row
 * disappears (handled by the parent — the chips are stateless here).
 */

import { Users, Calendar, Clock, Check, X, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { QuickReply } from "@/lib/aria/types";

const iconMap: Record<NonNullable<QuickReply["icon"]>, LucideIcon> = {
  users: Users,
  calendar: Calendar,
  clock: Clock,
  check: Check,
  x: X,
};

interface QuickRepliesProps {
  replies: QuickReply[];
  onPick: (value: string) => void;
  disabled?: boolean;
}

export function QuickReplies({ replies, onPick, disabled }: QuickRepliesProps) {
  if (!replies || replies.length === 0) return null;
  return (
    <div
      className="mt-2 -mx-1 overflow-x-auto scroll-thin"
      role="group"
      aria-label="Suggested replies"
    >
      <div className="flex gap-2 px-1 pb-1">
        {replies.map((r) => {
          const Icon = r.icon ? iconMap[r.icon] : null;
          return (
            <button
              key={r.id}
              type="button"
              disabled={disabled}
              onClick={() => onPick(r.value)}
              className={cn(
                "inline-flex items-center gap-1.5 whitespace-nowrap",
                "rounded-full border border-primary/25 bg-primary/5",
                "px-3.5 py-2 text-sm font-medium text-primary",
                "transition-all duration-200",
                "hover:bg-primary hover:text-primary-foreground hover:border-primary",
                "active:scale-95",
                "disabled:opacity-50 disabled:pointer-events-none",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
              )}
            >
              {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
              <span>{r.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
