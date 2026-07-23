/**
 * Aria configuration — resolved once at module load.
 *
 * Backend wiring is read from public env vars so the widget can be deployed
 * per-tenant without code changes. When `NEXT_PUBLIC_LIFTUP_API_BASE` and
 * `NEXT_PUBLIC_LIFTUP_API_KEY` are both set, the widget talks to the real
 * FastAPI backend. When either is missing, it gracefully falls back to a
 * built-in demo conversation so the UX can be previewed/pitched without a
 * live backend (very useful for sales demos to restaurant owners).
 */

import type { AriaConfig } from "./types";

function readEnv(key: string): string | null {
  // Next.js inlines NEXT_PUBLIC_* vars at build time. We guard for SSR + browser.
  if (typeof process !== "undefined" && process.env) {
    const v = process.env[key];
    return v && v.trim().length > 0 ? v.trim() : null;
  }
  return null;
}

const apiBase = readEnv("NEXT_PUBLIC_LIFTUP_API_BASE");
const apiKey = readEnv("NEXT_PUBLIC_LIFTUP_API_KEY");
const restaurantName =
  readEnv("NEXT_PUBLIC_RESTAURANT_NAME") ?? "Café Aroma";
const tagline =
  readEnv("NEXT_PUBLIC_RESTAURANT_TAGLINE") ?? "Fine dining · Sargodha";

export const ariaConfig: AriaConfig = {
  apiBase,
  apiKey,
  restaurantName,
  tagline,
  demoMode: !apiBase || !apiKey,
};
