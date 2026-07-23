"use client";

/**
 * Typing indicator — three pulsing dots inside an Aria bubble.
 * Shown while the assistant reply is in flight.
 */

import { AriaAvatar } from "./aria-avatar";

export function TypingIndicator() {
  return (
    <div className="flex items-end gap-2 animate-fade-up">
      <AriaAvatar size="sm" />
      <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-md bg-card border border-border/80 px-4 py-3 shadow-sm">
        <span className="h-2 w-2 rounded-full bg-primary/60 animate-typing" style={{ animationDelay: "0ms" }} />
        <span className="h-2 w-2 rounded-full bg-primary/60 animate-typing" style={{ animationDelay: "180ms" }} />
        <span className="h-2 w-2 rounded-full bg-primary/60 animate-typing" style={{ animationDelay: "360ms" }} />
        <span className="sr-only">Aria is typing</span>
      </div>
    </div>
  );
}
