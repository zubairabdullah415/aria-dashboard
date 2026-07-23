/**
 * Aria Chat Widget — Type Definitions
 * LiftUp SaaS · Customer-facing AI reservation concierge
 *
 * These types describe the contract between the React widget and the
 * FastAPI backend (POST /api/widget/chat). The backend response shape is:
 *
 *   {
 *     "reply":             string,
 *     "session_token":     string,
 *     "booking_complete":  boolean,
 *     "confirmation_code": string | null
 *   }
 *
 * The widget extends this with locally-rendered UI metadata (quick replies,
 * date/time pickers, booking tickets) so we can present rich interactive
 * elements without leaking any technical detail to the guest.
 */

/** A single message in the conversation transcript. */
export interface ChatMessage {
  /** Stable client-generated id (crypto.randomUUID when available). */
  id: string;
  /** Who sent the message. "assistant" === Aria. */
  role: "user" | "assistant";
  /** The visible text. May contain a mix of English + Roman Urdu / Nastaliq. */
  content: string;
  /** Epoch ms — used for timestamp pills in the chat header. */
  timestamp: number;
  /** Optional structured attachment rendered below the bubble. */
  attachment?: MessageAttachment;
  /** Optional quick-reply chips rendered under an Aria message. */
  quickReplies?: QuickReply[];
  /** True once Aria confirms a booking — drives the celebration overlay. */
  bookingComplete?: boolean;
  /** Confirmation code returned by the backend, if any. */
  confirmationCode?: string;
}

/**
 * Structured attachment — what to render instead of (or alongside) plain text.
 * The widget parses Aria's reply to decide which attachment to show.
 */
export type MessageAttachment =
  | {
      kind: "booking-confirmation";
      restaurantName: string;
      guestName: string;
      date: string;
      time: string;
      partySize: number;
      confirmationCode: string;
    }
  | {
      kind: "date-picker";
      /** Earliest selectable date (yyyy-mm-dd). Defaults to today. */
      min?: string;
      /** Latest selectable date (yyyy-mm-dd). Defaults to +30 days. */
      max?: string;
    }
  | {
      kind: "time-picker";
      /** Available time slots in 12-hour "h:mm AM/PM" format. */
      slots: string[];
    }
  | {
      kind: "party-size-picker";
      sizes: number[];
    };

/** A single tappable quick-reply chip. */
export interface QuickReply {
  /** Stable id for React keys. */
  id: string;
  /** Visible label, e.g. "Today", "7:30 PM", "4 guests". */
  label: string;
  /** The text sent to the backend as the user's message when tapped. */
  value: string;
  /** Optional Lucide icon name. */
  icon?: "calendar" | "clock" | "users" | "check" | "x";
}

/** Backend response shape from POST /api/widget/chat. */
export interface AriaBackendResponse {
  reply: string;
  session_token: string;
  booking_complete?: boolean;
  confirmation_code?: string | null;
}

/** Configuration resolved from env vars at module load. */
export interface AriaConfig {
  /** Base URL of the FastAPI backend, e.g. https://api.liftup.saas */
  apiBase: string | null;
  /** Tenant widget API key (X-Api-Key header). */
  apiKey: string | null;
  /** Restaurant display name shown in the header. */
  restaurantName: string;
  /** Tagline shown under the restaurant name. */
  tagline: string;
  /** True when no backend is configured — widget runs in pitch/demo mode. */
  demoMode: boolean;
}

/** Conversation state for the demo engine. */
export type DemoState =
  | "GREETING"
  | "ASK_PARTY_SIZE"
  | "ASK_DATE"
  | "ASK_TIME"
  | "ASK_CONTACT"
  | "CONFIRMED"
  | "POST_BOOKING";
