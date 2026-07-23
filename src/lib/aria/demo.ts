/**
 * Aria Demo Conversation Engine
 * ────────────────────────────────────────────────────────────────────────────
 * Used when NEXT_PUBLIC_LIFTUP_API_BASE / NEXT_PUBLIC_LIFTUP_API_KEY are not
 * configured. Lets the widget be previewed and pitched without a live
 * FastAPI backend.
 *
 * The engine is a tiny state machine that simulates Aria's reservation flow
 * in a mix of English + Roman Urdu — the way a Pakistani restaurant's
 * concierge would actually chat. It produces the same {reply, quickReplies,
 * attachment, bookingComplete, confirmationCode} shape the real backend
 * response is enriched with on the client.
 */

import type {
  ChatMessage,
  MessageAttachment,
  QuickReply,
  DemoState,
} from "./types";
import { ariaConfig } from "./config";

const restaurantName = ariaConfig.restaurantName;

interface DemoTurn {
  reply: string;
  quickReplies?: QuickReply[];
  attachment?: MessageAttachment;
  bookingComplete?: boolean;
  confirmationCode?: string;
  nextState: DemoState;
}

function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function confirmationCode(): string {
  // Same shape as the backend: 6-char alphanumeric, easy to read aloud.
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let s = "";
  for (let i = 0; i < 6; i++) {
    s += chars[Math.floor(Math.random() * chars.length)];
  }
  return s;
}

const PARTY_SIZES = [1, 2, 3, 4, 5, 6, 7, 8];

function nextDays(count: number): { label: string; value: string; iso: string }[] {
  const out: { label: string; value: string; iso: string }[] = [];
  const today = new Date();
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  for (let i = 0; i < count; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    let label: string;
    if (i === 0) label = "Today";
    else if (i === 1) label = "Tomorrow";
    else label = dayNames[d.getDay()];
    out.push({
      label: i >= 2 ? `${label} · ${d.getDate()}/${d.getMonth() + 1}` : label,
      value: i === 0 ? "Today" : i === 1 ? "Tomorrow" : `${dayNames[d.getDay()]} ${d.getDate()}/${d.getMonth() + 1}`,
      iso,
    });
  }
  return out;
}

const TIME_SLOTS = [
  "6:00 PM",
  "6:30 PM",
  "7:00 PM",
  "7:30 PM",
  "8:00 PM",
  "8:30 PM",
  "9:00 PM",
  "9:30 PM",
  "10:00 PM",
];

/** Persistent session-scoped context for the demo. */
interface DemoContext {
  state: DemoState;
  partySize?: number;
  date?: string;
  time?: string;
  guestName?: string;
  phone?: string;
  confirmationCode?: string;
}

const ctxBySession = new Map<string, DemoContext>();

function getCtx(sessionToken: string): DemoContext {
  let c = ctxBySession.get(sessionToken);
  if (!c) {
    c = { state: "GREETING" };
    ctxBySession.set(sessionToken, c);
  }
  return c;
}

function partySizeChips(): QuickReply[] {
  return PARTY_SIZES.map((n) => ({
    id: `ps-${n}`,
    label: n === 8 ? "8+" : `${n}`,
    value: `${n} ${n === 1 ? "guest" : "guests"}`,
    icon: "users" as const,
  }));
}

function dateChips(): QuickReply[] {
  return nextDays(5).map((d, i) => ({
    id: `date-${i}`,
    label: d.label,
    value: d.value,
    icon: "calendar" as const,
  }));
}

function timeChips(): QuickReply[] {
  return TIME_SLOTS.slice(0, 6).map((t, i) => ({
    id: `time-${i}`,
    label: t,
    value: t,
    icon: "clock" as const,
  }));
}

function postBookingChips(): QuickReply[] {
  return [
    { id: "modify", label: "Modify reservation", value: "I'd like to modify my reservation", icon: "check" as const },
    { id: "cancel", label: "Cancel reservation", value: "Please cancel my reservation", icon: "x" as const },
    { id: "new", label: "Book another table", value: "I'd like to book another table", icon: "calendar" as const },
  ];
}

/** Parse a free-text user message for party size / date / time / contact. */
function parsePartySize(text: string): number | null {
  const m = text.match(/\b(\d{1,2})\s*(?:guests?|people|persons?|pax|log)?\b/i);
  if (m) {
    const n = parseInt(m[1], 10);
    if (n >= 1 && n <= 20) return n;
  }
  // Roman Urdu / casual
  if (/ek\b|1\s*log/i.test(text)) return 1;
  if (/do\b|2\s*log/i.test(text)) return 2;
  if (/teen\b|3\s*log/i.test(text)) return 3;
  if (/chaar\b|4\s*log/i.test(text)) return 4;
  return null;
}

function parseDate(text: string): string | null {
  const t = text.toLowerCase();
  if (t.includes("today") || t.includes("aaj")) return "Today";
  if (t.includes("tomorrow") || t.includes("kal")) return "Tomorrow";
  const days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  for (let i = 0; i < days.length; i++) {
    if (t.includes(days[i])) {
      const today = new Date();
      const todayDay = today.getDay();
      let diff = (i - todayDay + 7) % 7;
      if (diff === 0) diff = 7; // next occurrence of same weekday
      const d = new Date(today);
      d.setDate(today.getDate() + diff);
      return `${days[i].charAt(0).toUpperCase() + days[i].slice(1)} ${d.getDate()}/${d.getMonth() + 1}`;
    }
  }
  return null;
}

function parseTime(text: string): string | null {
  const t = text.toLowerCase();
  // Match "7", "7pm", "7:30 pm", "730pm"
  const m = t.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/);
  if (m) {
    let h = parseInt(m[1], 10);
    const min = m[2] ? parseInt(m[2], 10) : 0;
    let ampm = m[3];
    if (!ampm) {
      // Default to PM for dinner hours, AM for < 11
      ampm = h >= 11 || h === 12 ? "pm" : "am";
    }
    if (ampm === "pm" && h !== 12) h += 12;
    if (ampm === "am" && h === 12) h = 0;
    // Snap to nearest 30 min
    const totalMin = h * 60 + min;
    const snapped = Math.round(totalMin / 30) * 30;
    const sh = Math.floor(snapped / 60);
    const sm = snapped % 60;
    const dispH = sh === 0 ? 12 : sh > 12 ? sh - 12 : sh;
    const dispA = sh >= 12 ? "PM" : "AM";
    return `${dispH}:${sm.toString().padStart(2, "0")} ${dispA}`;
  }
  return null;
}

function parseContact(text: string): { name?: string; phone?: string } {
  const phoneMatch = text.match(/(\+?\d[\d\s-]{8,})/);
  const phone = phoneMatch ? phoneMatch[1].replace(/[\s-]/g, "") : undefined;
  // Strip the phone out, then take the remaining words as the name.
  const nameText = text.replace(phoneMatch?.[0] ?? "", "").trim();
  const name = nameText.length >= 2 ? nameText.split(/\s+/).slice(0, 4).join(" ") : undefined;
  return { name, phone };
}

/**
 * Advance the demo conversation by one user turn.
 * Returns the assistant reply + UI metadata + new session token (echoed back).
 */
export function demoRespond(
  sessionToken: string,
  userMessage: string,
): { message: ChatMessage; sessionToken: string } {
  const ctx = getCtx(sessionToken);
  const text = userMessage.trim();
  const lower = text.toLowerCase();

  let turn: DemoTurn;

  switch (ctx.state) {
    case "GREETING":
    case "ASK_PARTY_SIZE": {
      const ps = parsePartySize(text);
      if (ps) {
        ctx.partySize = ps;
        ctx.state = "ASK_DATE";
        turn = {
          reply: `Mukhtar! ${ps} ${ps === 1 ? "guest" : "guests"} — noted. Kab aana hai aapko? Pick a date below, ya bata dein agar koi specific date chahiye.`,
          quickReplies: dateChips(),
          nextState: "ASK_DATE",
        };
      } else {
        ctx.state = "ASK_PARTY_SIZE";
        turn = {
          reply: `Assalam-o-Alaikum, ${restaurantName} mein khush aamdeed! Main Aria hoon, aapki reservation mein madad karne ke liye. 👋\n\nBatayen, kitne log aayenge? (e.g. 2, 4, 6 guests)`,
          quickReplies: partySizeChips(),
          nextState: "ASK_PARTY_SIZE",
        };
      }
      break;
    }

    case "ASK_DATE": {
      const date = parseDate(text);
      if (date) {
        ctx.date = date;
        ctx.state = "ASK_TIME";
        turn = {
          reply: `Theek hai, ${date} ko ${ctx.partySize} ${ctx.partySize === 1 ? "guest" : "guests"}. Kaunsa time suit karta hai? Dinner ke popular slots neeche diye gaye hain:`,
          quickReplies: timeChips(),
          attachment: { kind: "time-picker", slots: TIME_SLOTS },
          nextState: "ASK_TIME",
        };
      } else {
        turn = {
          reply: `Maaf kijiyega, samajh nahi aaya. Konsa date chahiye? Aaj, kal, ya koi specific date?`,
          quickReplies: dateChips(),
          nextState: "ASK_DATE",
        };
      }
      break;
    }

    case "ASK_TIME": {
      const time = parseTime(text);
      if (time) {
        ctx.time = time;
        ctx.state = "ASK_CONTACT";
        turn = {
          reply: `Perfect — ${ctx.date} at ${time}, ${ctx.partySize} ${ctx.partySize === 1 ? "guest" : "guests"}. Booking confirm karne ke liye, apna naam aur phone number bata dein please.\n\nExample: "Ali Khan, 0300 1234567"`,
          nextState: "ASK_CONTACT",
        };
      } else {
        turn = {
          reply: `Time samajh nahi aaya. Ek specific slot choose karein ya type karein, e.g. "8:00 PM".`,
          quickReplies: timeChips(),
          attachment: { kind: "time-picker", slots: TIME_SLOTS },
          nextState: "ASK_TIME",
        };
      }
      break;
    }

    case "ASK_CONTACT": {
      const { name, phone } = parseContact(text);
      if (name && phone) {
        ctx.guestName = name;
        ctx.phone = phone;
        ctx.confirmationCode = confirmationCode();
        ctx.state = "CONFIRMED";
        turn = {
          reply: `Shukriya ${name}! Aapki reservation confirm ho gayi hai. 🎉\n\nBooking ki tafseel neeche di gayi hai. Hum ne ek confirmation SMS bhi bhej diya hai ${phone} par. Aap ka confirmation code yaad rakhein — kisi bhi change ke liye yeh code zaroori hoga.`,
          attachment: {
            kind: "booking-confirmation",
            restaurantName,
            guestName: name,
            date: ctx.date!,
            time: ctx.time!,
            partySize: ctx.partySize!,
            confirmationCode: ctx.confirmationCode,
          },
          bookingComplete: true,
          confirmationCode: ctx.confirmationCode,
          quickReplies: postBookingChips(),
          nextState: "POST_BOOKING",
        };
      } else {
        turn = {
          reply: `Naam aur phone number dono chahiye honge, taake hum reservation confirm kar sakein. Format: "Name, 03XX XXXXXXX"`,
          nextState: "ASK_CONTACT",
        };
      }
      break;
    }

    case "CONFIRMED":
    case "POST_BOOKING": {
      if (lower.includes("cancel")) {
        ctx.state = "POST_BOOKING";
        turn = {
          reply: `Aapki reservation (${ctx.confirmationCode}) cancel ho gayi hai. Agar dobara book karna ho to bas batayen — main yahan hoon. 👋`,
          nextState: "POST_BOOKING",
        };
      } else if (lower.includes("modify") || lower.includes("change")) {
        ctx.state = "ASK_PARTY_SIZE";
        turn = {
          reply: `Bilkul, modify kar lete hain. Naya party size batayen — kitne log aayenge?`,
          quickReplies: partySizeChips(),
          nextState: "ASK_PARTY_SIZE",
        };
      } else if (lower.includes("another") || lower.includes("new") || lower.includes("book")) {
        ctx.state = "ASK_PARTY_SIZE";
        ctx.partySize = undefined;
        ctx.date = undefined;
        ctx.time = undefined;
        ctx.guestName = undefined;
        ctx.confirmationCode = undefined;
        turn = {
          reply: `Bilkul! Nai reservation shuru karte hain. Kitne log aayenge is baar?`,
          quickReplies: partySizeChips(),
          nextState: "ASK_PARTY_SIZE",
        };
      } else {
        turn = {
          reply: `Aur kuch madad chahiye? Aap nai reservation bhi book kar sakte hain, ya existing ko modify/cancel kar sakte hain.`,
          quickReplies: postBookingChips(),
          nextState: "POST_BOOKING",
        };
      }
      break;
    }

    default: {
      ctx.state = "ASK_PARTY_SIZE";
      turn = {
        reply: `Main yahan hoon! Kitne log aayenge?`,
        quickReplies: partySizeChips(),
        nextState: "ASK_PARTY_SIZE",
      };
    }
  }

  ctx.state = turn.nextState;

  const message: ChatMessage = {
    id: uid(),
    role: "assistant",
    content: turn.reply,
    timestamp: Date.now(),
    quickReplies: turn.quickReplies,
    attachment: turn.attachment,
    bookingComplete: turn.bookingComplete,
    confirmationCode: turn.confirmationCode,
  };

  return { message, sessionToken };
}

/** Initial greeting message — shown when a new session starts. */
export function demoGreeting(sessionToken: string): ChatMessage {
  // Pre-seed context so first user message routes to ASK_PARTY_SIZE correctly.
  ctxBySession.set(sessionToken, { state: "ASK_PARTY_SIZE" });
  return {
    id: uid(),
    role: "assistant",
    content: `Assalam-o-Alaikum, ${restaurantName} mein khush aamdeed! 🌿\n\nMain Aria hoon — aapki reservation mein madad karne wali AI concierge. Bas batayen kitne log aayenge, aur main baaki sab sambhal loongi.`,
    timestamp: Date.now(),
    quickReplies: partySizeChips(),
  };
}
