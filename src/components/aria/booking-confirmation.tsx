"use client";

/**
 * Booking Confirmation Card
 * ────────────────────────────────────────────────────────────────────────────
 * Premium "ticket" rendered when a reservation is confirmed. Designed to feel
 * like a paper restaurant reservation card — perforated edge, generous padding,
 * the confirmation code in large monospace, and a "Confirmed" seal.
 *
 * The same card is used in both demo and real-backend modes; the data is
 * sourced from the message's `attachment` payload.
 */

import { Calendar, Clock, Users, User, MapPin, Check } from "lucide-react";
import { ariaConfig } from "@/lib/aria/config";

interface BookingConfirmationProps {
  guestName: string;
  date: string;
  time: string;
  partySize: number;
  confirmationCode: string;
}

export function BookingConfirmation({
  guestName,
  date,
  time,
  partySize,
  confirmationCode,
}: BookingConfirmationProps) {
  return (
    <div className="mt-3 relative animate-fade-up">
      {/* Card body */}
      <div className="relative rounded-2xl bg-white border border-emerald-100 shadow-[0_8px_30px_-12px_rgba(4,120,87,0.25)] overflow-hidden">
        {/* Top band with seal */}
        <div className="bg-gradient-to-r from-emerald-700 to-emerald-600 px-5 py-3.5 flex items-center justify-between">
          <div>
            <p className="text-[10px] uppercase tracking-[0.18em] text-emerald-100/80 font-medium">
              Reservation Confirmed
            </p>
            <p className="text-base font-semibold text-white leading-tight mt-0.5">
              {ariaConfig.restaurantName}
            </p>
          </div>
          <div className="grid place-items-center h-10 w-10 rounded-full bg-white/15 ring-2 ring-white/40 animate-success-pop">
            <Check className="h-5 w-5 text-white" strokeWidth={3} />
          </div>
        </div>

        {/* Perforation */}
        <div className="relative h-3 bg-white">
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 border-t border-dashed border-emerald-200" />
          <div className="absolute -left-2 top-1/2 -translate-y-1/2 h-4 w-4 rounded-full bg-background" />
          <div className="absolute -right-2 top-1/2 -translate-y-1/2 h-4 w-4 rounded-full bg-background" />
        </div>

        {/* Detail rows */}
        <div className="px-5 py-4 space-y-3">
          <DetailRow icon={<User className="h-4 w-4" />} label="Guest" value={guestName} />
          <DetailRow icon={<Calendar className="h-4 w-4" />} label="Date" value={date} />
          <DetailRow icon={<Clock className="h-4 w-4" />} label="Time" value={time} />
          <DetailRow
            icon={<Users className="h-4 w-4" />}
            label="Party size"
            value={`${partySize} ${partySize === 1 ? "guest" : "guests"}`}
          />
          <DetailRow
            icon={<MapPin className="h-4 w-4" />}
            label="Location"
            value={ariaConfig.tagline}
          />
        </div>

        {/* Confirmation code */}
        <div className="mx-5 mb-5 rounded-xl bg-emerald-50/60 border border-emerald-100 px-4 py-3 text-center">
          <p className="text-[10px] uppercase tracking-[0.18em] text-emerald-700/80 font-medium">
            Confirmation code
          </p>
          <p className="font-mono text-2xl font-bold tracking-[0.18em] text-emerald-800 mt-0.5">
            {confirmationCode}
          </p>
          <p className="text-[11px] text-emerald-700/70 mt-1">
            Save this code to modify or cancel your booking
          </p>
        </div>
      </div>
    </div>
  );
}

function DetailRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid place-items-center h-8 w-8 rounded-lg bg-emerald-50 text-emerald-700 shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0 flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-sm font-medium text-foreground truncate text-right">
          {value}
        </span>
      </div>
    </div>
  );
}
