"use client";

/**
 * Chat Input Bar
 * ────────────────────────────────────────────────────────────────────────────
 * Fixed to the bottom of the viewport with a soft shadow and safe-area
 * padding so the home indicator on iOS doesn't cover it. The textarea grows
 * naturally up to 4 lines, then scrolls internally. The send button is
 * disabled while Aria is replying or the input is empty.
 *
 * On Enter (without Shift), the message is sent — standard chat UX. Shift+Enter
 * inserts a newline for multi-line input.
 */

import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  disabled,
  placeholder = "Type your message…",
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize the textarea to fit content (up to 4 lines).
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    // Reset height after sending.
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
    });
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const canSend = value.trim().length > 0 && !disabled;

  return (
    <div
      className="sticky bottom-0 z-30 safe-bottom"
      role="region"
      aria-label="Message composer"
    >
      {/* Top fade so messages don't visually collide with the input */}
      <div className="h-3 bg-gradient-to-t from-background to-transparent pointer-events-none" />

      <div className="bg-card/95 backdrop-blur-md border-t border-border/70">
        <div className="mx-auto max-w-2xl px-3 py-2.5">
          <div
            className={cn(
              "flex items-end gap-2",
              "rounded-3xl border border-border bg-background",
              "px-2.5 py-1.5 shadow-sm",
              "focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/15",
              "transition-all duration-200",
            )}
          >
            <textarea
              ref={taRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              placeholder={placeholder}
              disabled={disabled}
              aria-label="Message Aria"
              className={cn(
                "flex-1 resize-none bg-transparent",
                "py-2 px-1.5 text-[15px] leading-relaxed",
                "placeholder:text-muted-foreground/70",
                "focus:outline-none disabled:opacity-60",
                "scroll-thin",
              )}
            />
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              aria-label="Send message"
              className={cn(
                "grid place-items-center h-10 w-10 rounded-full shrink-0",
                "transition-all duration-200",
                canSend
                  ? "bg-primary text-primary-foreground hover:bg-primary/90 active:scale-95 shadow-sm"
                  : "bg-muted text-muted-foreground/50",
              )}
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
