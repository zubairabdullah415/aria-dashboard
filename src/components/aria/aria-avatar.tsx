"use client";

/**
 * Aria Avatar — soft gradient disc with a stylized "A" mark.
 *
 * The avatar is intentionally abstract (no clip-art robot, no photo) so the
 * widget reads as a premium concierge rather than a tech demo. The pulse ring
 * animates while Aria is "online".
 */

import { cn } from "@/lib/utils";

interface AriaAvatarProps {
  size?: "sm" | "md" | "lg";
  pulsing?: boolean;
  className?: string;
}

const sizeMap = {
  sm: "h-8 w-8 text-xs",
  md: "h-10 w-10 text-sm",
  lg: "h-12 w-12 text-base",
};

export function AriaAvatar({
  size = "md",
  pulsing = false,
  className,
}: AriaAvatarProps) {
  return (
    <div className={cn("relative shrink-0", className)}>
      {pulsing && (
        <span
          className="absolute inset-0 rounded-full bg-primary/30 animate-pulse-ring"
          aria-hidden="true"
        />
      )}
      <div
        className={cn(
          "relative grid place-items-center rounded-full",
          "bg-gradient-to-br from-primary to-emerald-700",
          "text-primary-foreground font-semibold tracking-tight",
          "shadow-sm ring-2 ring-white/60",
          sizeMap[size],
        )}
      >
        {/* Stylized Aria mark — a calligraphic "A" with a leaf accent */}
        <svg
          viewBox="0 0 32 32"
          className="h-1/2 w-1/2"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M8 24 L16 6 L24 24" />
          <path d="M11 18 L21 18" />
          <path
            d="M24 8 c2 0 3 1.5 3 3"
            strokeWidth="1.5"
            className="opacity-80"
          />
        </svg>
      </div>
    </div>
  );
}
