"use client";

/**
 * Chat Bubble
 * ────────────────────────────────────────────────────────────────────────────
 * Renders a single message in the conversation. User messages are right-
 * aligned in brand emerald; Aria messages are left-aligned in white with her
 * avatar beside them. Whitespace and line breaks in the reply text are
 * preserved so bilingual (English + Roman Urdu) messages read naturally.
 *
 * The bubble also:
 *   • Shows a soft timestamp under the bubble (12-hour, no seconds)
 *   • Renders the structured attachment (e.g. booking card) below the text
 *   • Renders quick-reply chips below the attachment
 */

import { cn } from "@/lib/utils";
import { AriaAvatar } from "./aria-avatar";
import { AttachmentRenderer } from "./attachment-renderer";
import { QuickReplies } from "./quick-replies";
import type { ChatMessage } from "@/lib/aria/types";

interface ChatBubbleProps {
  message: ChatMessage;
  onQuickReplyPick: (value: string) => void;
  isLastAssistant: boolean;
}

function formatTimestamp(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function ChatBubble({
  message,
  onQuickReplyPick,
  isLastAssistant,
}: ChatBubbleProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end animate-fade-up">
        <div className="flex flex-col items-end max-w-[82%] sm:max-w-[70%]">
          <div
            className={cn(
              "rounded-2xl rounded-br-md",
              "bg-primary text-primary-foreground",
              "px-4 py-2.5 shadow-sm",
              "whitespace-pre-wrap break-words text-[15px] leading-relaxed",
            )}
          >
            {message.content}
          </div>
          <span className="mt-1 px-1 text-[10px] text-muted-foreground">
            {formatTimestamp(message.timestamp)}
          </span>
        </div>
      </div>
    );
  }

  // Aria
  return (
    <div className="flex items-end gap-2 animate-fade-up">
      <div className="shrink-0 mb-5">
        <AriaAvatar size="sm" />
      </div>
      <div className="flex flex-col items-start max-w-[82%] sm:max-w-[70%]">
        <div
          className={cn(
            "rounded-2xl rounded-bl-md",
            "bg-card border border-border/80 text-foreground",
            "px-4 py-2.5 shadow-sm",
            "whitespace-pre-wrap break-words text-[15px] leading-relaxed",
          )}
        >
          {message.content}
        </div>

        {/* Attachment (booking confirmation card, etc.) */}
        {message.attachment && (
          <div className="w-full">
            <AttachmentRenderer attachment={message.attachment} />
          </div>
        )}

        {/* Quick replies — only render on the latest assistant message
            so old chips don't linger and clutter the transcript. */}
        {isLastAssistant && message.quickReplies && message.quickReplies.length > 0 && (
          <div className="w-full">
            <QuickReplies
              replies={message.quickReplies}
              onPick={onQuickReplyPick}
            />
          </div>
        )}

        <span className="mt-1 px-1 text-[10px] text-muted-foreground">
          Aria · {formatTimestamp(message.timestamp)}
        </span>
      </div>
    </div>
  );
}
