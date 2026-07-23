"use client";

/**
 * Attachment Renderer
 * ────────────────────────────────────────────────────────────────────────────
 * Renders the structured attachment below an Aria message bubble.
 * Currently the only attachment kind is `booking-confirmation`. (Date/time
 * pickers are surfaced as quick-reply chips instead, which feels more natural
 * on mobile than a popover calendar.)
 */

import type { MessageAttachment } from "@/lib/aria/types";
import { BookingConfirmation } from "./booking-confirmation";

interface AttachmentRendererProps {
  attachment: MessageAttachment;
}

export function AttachmentRenderer({ attachment }: AttachmentRendererProps) {
  switch (attachment.kind) {
    case "booking-confirmation":
      return (
        <BookingConfirmation
          guestName={attachment.guestName}
          date={attachment.date}
          time={attachment.time}
          partySize={attachment.partySize}
          confirmationCode={attachment.confirmationCode}
        />
      );
    // date-picker / time-picker / party-size-picker are surfaced as quick
    // replies (see quick-replies.tsx). No additional UI rendered here.
    default:
      return null;
  }
}
