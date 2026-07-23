/**
 * Aria Chat Client (v3 — Hardened)
 * ────────────────────────────────────────────────────────────────────────────
 * Single entrypoint for sending a guest message and receiving Aria's reply.
 *
 * - If `NEXT_PUBLIC_LIFTUP_API_BASE` + `NEXT_PUBLIC_LIFTUP_API_KEY` are set,
 *   hits the real FastAPI backend at POST /api/widget/chat with the
 *   `X-Api-Key` and `X-Session-Token` headers. The session token is persisted
 *   silently in localStorage — the guest never sees it.
 * - Otherwise, falls back to the built-in demo engine so the UX can still be
 *   previewed/pitched without a live backend.
 *
 * In BOTH modes, the assistant reply is post-processed to enrich it with
 * quick-reply chips and structured attachments (date/time pickers, booking
 * confirmation cards) by detecting intent from the reply text. This keeps the
 * backend free of UI concerns while letting the widget feel premium.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PATCHES APPLIED IN THIS v3 BUILD
 * ────────────────────────────────────────────────────────────────────────────
 * 1. ABORT CONTROLLER + 30s TIMEOUT
 *    The previous `fetch()` had no timeout. If the FastAPI backend hung (DB
 *    pool exhaustion, Anthropic API stall, etc.) the widget stayed in the
 *    "typing" state forever and the guest had no way to recover short of a
 *    page reload. Now every request is wrapped in an AbortController with a
 *    30-second deadline. On timeout the user sees a friendly message and the
 *    input is re-enabled so they can retry immediately.
 *
 * 2. STRUCTURED ERROR MESSAGES
 *    Previously every non-2xx response threw the same generic "trouble
 *    connecting" error. Now we parse the backend's JSON error body and map
 *    known status codes to actionable messages:
 *      402 → "This restaurant's reservation quota has been reached."
 *      429 → "You're sending messages too quickly. Please wait a moment."
 *      5xx → "Aria is having trouble right now. Please try again."
 *    This lets the frontend show a toast that actually helps the guest
 *    instead of a cryptic failure.
 *
 * 3. SAFE JSON PARSE
 *    If the backend returns a non-JSON body (e.g. an HTML 502 from a reverse
 *    proxy), `res.json()` would throw a SyntaxError that surfaced as a
 *    confusing "Unexpected token < in JSON" message. Now wrapped in try/catch
 *    so any non-JSON response falls through to the network-error path.
 *
 * 4. SESSION TOKEN GUARD
 *    `getSessionToken()` previously returned a freshly-generated token even
 *    during SSR (when `window` is undefined), which could cause a hydration
 *    mismatch between server-rendered HTML and the client. Now it returns an
 *    empty string during SSR so the first client effect can hydrate cleanly.
 * ────────────────────────────────────────────────────────────────────────────
 */

import { ariaConfig } from "./config";
import { demoGreeting, demoRespond } from "./demo";
import type { AriaBackendResponse, ChatMessage, QuickReply } from "./types";

const SESSION_KEY = "aria.session_token";
const REQUEST_TIMEOUT_MS = 30_000;

function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Generate a fresh session token (only used if none is stored yet). */
function newSessionToken(): string {
  if (typeof crypto !== "undefined" && "getRandomValues" in crypto) {
    const a = new Uint8Array(32);
    crypto.getRandomValues(a);
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Read the persisted session token, creating one on first visit.
 *  Returns "" during SSR so the client can hydrate without mismatch. */
export function getSessionToken(): string {
  if (typeof window === "undefined") return "";
  let t = window.localStorage.getItem(SESSION_KEY);
  if (!t || t.length < 16) {
    t = newSessionToken();
    window.localStorage.setItem(SESSION_KEY, t);
  }
  return t;
}

/** Force a fresh session (used by the "New conversation" reset button). */
export function resetSession(): string {
  const t = newSessionToken();
  if (typeof window !== "undefined") {
    window.localStorage.setItem(SESSION_KEY, t);
  }
  return t;
}

/**
 * Parse Aria's reply text and detect what interactive UI to surface.
 * Looks for keywords like "date", "time", "party size", "guests" and decides
 * which quick-reply chips / attachment to render.
 */
function enrichReply(
  reply: string,
  bookingComplete: boolean,
  confirmationCode?: string | null,
): { quickReplies?: QuickReply[]; dateIntent?: boolean; timeIntent?: boolean; partyIntent?: boolean } {
  const lower = reply.toLowerCase();

  // Booking already complete — offer post-booking actions.
  if (bookingComplete) {
    return {
      quickReplies: [
        { id: "modify", label: "Modify reservation", value: "I'd like to modify my reservation", icon: "check" },
        { id: "cancel", label: "Cancel reservation", value: "Please cancel my reservation", icon: "x" },
        { id: "new", label: "Book another table", value: "I'd like to book another table", icon: "calendar" },
      ],
    };
  }

  // Party-size question
  if (/(kitne log|how many|party size|guests?|people|persons|pax)/i.test(lower)) {
    return {
      partyIntent: true,
      quickReplies: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({
        id: `ps-${n}`,
        label: n === 8 ? "8+" : `${n}`,
        value: `${n} ${n === 1 ? "guest" : "guests"}`,
        icon: "users" as const,
      })),
    };
  }

  // Date question
  if (/(kab|date|aaj|kal|which day|what day)/i.test(lower)) {
    const today = new Date();
    const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const chips: QuickReply[] = [];
    for (let i = 0; i < 5; i++) {
      const d = new Date(today);
      d.setDate(today.getDate() + i);
      let label: string;
      if (i === 0) label = "Today";
      else if (i === 1) label = "Tomorrow";
      else label = `${dayNames[d.getDay()]} ${d.getDate()}/${d.getMonth() + 1}`;
      chips.push({
        id: `date-${i}`,
        label,
        value: i === 0 ? "Today" : i === 1 ? "Tomorrow" : label,
        icon: "calendar" as const,
      });
    }
    return { dateIntent: true, quickReplies: chips };
  }

  // Time question
  if (/(time|kaunsa time|slot|what time|baje)/i.test(lower)) {
    return {
      timeIntent: true,
      quickReplies: ["6:30 PM", "7:00 PM", "7:30 PM", "8:00 PM", "8:30 PM", "9:00 PM"].map((t, i) => ({
        id: `time-${i}`,
        label: t,
        value: t,
        icon: "clock" as const,
      })),
    };
  }

  return {};
}

/**
 * Map a non-2xx HTTP response to a guest-friendly error message.
 * Tries to parse the backend's JSON error body; falls back to a generic
 * network-error message if the body isn't JSON.
 */
async function explainHttpError(res: Response): Promise<string> {
  // Try to extract a structured error message from the body.
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // Non-JSON body (e.g. HTML 502 from a reverse proxy) — fall through.
  }

  // FastAPI validation errors return {detail: [...]} or {detail: "string"}
  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : null;

  // Quota exhausted (402) — backend returns {detail: {code, message, upgrade_url}}
  if (res.status === 402 && detail && typeof detail === "object") {
    const msg = (detail as { message?: string }).message;
    if (typeof msg === "string") return msg;
  }

  // Simple string detail (400 business errors from cancel/modify)
  if (typeof detail === "string" && detail.length > 0) {
    return detail;
  }

  // Fallback by status code
  if (res.status === 401) return "This restaurant's widget key is invalid. Please contact the restaurant.";
  if (res.status === 402) return "This restaurant's reservation quota has been reached. Please try again later.";
  if (res.status === 404) return "That reservation could not be found. Please check your confirmation code.";
  if (res.status === 429) return "You're sending messages too quickly. Please wait a moment and try again.";
  if (res.status >= 500) return "Aria is having trouble right now. Please try again in a moment.";

  return "Something went wrong. Please try again.";
}

/**
 * Send a message and get Aria's reply.
 * In real mode, POSTs to /api/widget/chat with X-Api-Key + X-Session-Token.
 * In demo mode, routes through the local conversation engine.
 *
 * The request is aborted after REQUEST_TIMEOUT_MS (30s) so a hung backend
 * never leaves the guest stuck in the "typing" state.
 */
export async function sendAriaMessage(
  message: string,
  sessionToken: string,
): Promise<{ message: ChatMessage; sessionToken: string }> {
  // ── Demo mode ─────────────────────────────────────────────────────────────
  if (ariaConfig.demoMode) {
    // Simulate network latency so the typing indicator shows.
    await new Promise((r) => setTimeout(r, 700 + Math.random() * 600));
    return demoRespond(sessionToken, message);
  }

  // ── Real backend mode ─────────────────────────────────────────────────────
  const url = `${ariaConfig.apiBase!.replace(/\/+$/, "")}/api/widget/chat`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Api-Key": ariaConfig.apiKey!,
        "X-Session-Token": sessionToken,
      },
      body: JSON.stringify({ message, session_token: sessionToken }),
      signal: controller.signal,
    });
  } catch (err) {
    // AbortError → timeout; everything else → network/CDN/DNS failure.
    clearTimeout(timeoutId);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Aria is taking longer than expected. Please try again.");
    }
    throw new Error("Can't reach the reservation service right now. Please check your connection and try again.");
  }
  clearTimeout(timeoutId);

  if (!res.ok) {
    // Surface a friendly, actionable error to the guest without exposing
    // HTTP status codes or stack traces.
    throw new Error(await explainHttpError(res));
  }

  // Parse the JSON body defensively — a misconfigured proxy could return
  // a 200 with an HTML body (e.g. a captive portal).
  let data: AriaBackendResponse;
  try {
    data = await res.json();
  } catch {
    throw new Error("Received an unexpected response from the server. Please try again.");
  }

  // Guard against a malformed but 2xx response.
  if (!data || typeof data.reply !== "string") {
    throw new Error("The server returned an incomplete response. Please try again.");
  }

  // Backend may rotate the token; persist + use the latest.
  const newToken = data.session_token || sessionToken;
  if (typeof window !== "undefined" && newToken) {
    window.localStorage.setItem(SESSION_KEY, newToken);
  }

  const bookingComplete = !!data.booking_complete;
  const enrichment = enrichReply(
    data.reply,
    bookingComplete,
    data.confirmation_code ?? undefined,
  );

  const replyMessage: ChatMessage = {
    id: uid(),
    role: "assistant",
    content: data.reply,
    timestamp: Date.now(),
    quickReplies: enrichment.quickReplies,
    bookingComplete,
    confirmationCode: data.confirmation_code ?? undefined,
  };

  return { message: replyMessage, sessionToken: newToken };
}

/** Returns the initial greeting message — from demo or by sending an empty
 *  ping to the backend. For demo mode we use the canned greeting; for real
 *  mode we show a minimal welcome bubble and let the first user message
 *  trigger the actual conversation. */
export function getInitialGreeting(sessionToken: string): ChatMessage {
  if (ariaConfig.demoMode) {
    return demoGreeting(sessionToken);
  }
  return {
    id: uid(),
    role: "assistant",
    content: `Assalam-o-Alaikum, ${ariaConfig.restaurantName} mein khush aamdeed! 🌿\n\nMain Aria hoon — aapki AI reservation concierge. Bas batayen kitne log aayenge, aur main baaki sambhal loongi.`,
    timestamp: Date.now(),
  };
}
