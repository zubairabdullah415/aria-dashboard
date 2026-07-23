"""
notifications.py — Multi-Tenant Notification Service
======================================================
Key changes from v1:

  1. DYNAMIC TWILIO ROUTING: Every SMS lookup queries restaurant_phone_numbers
     for the restaurant's dedicated sender number — no global env variable.

  2. RESTAURANT-BRANDED EMAILS: Subject lines and "from" name use the
     restaurant's name, not a hardcoded platform name.

  3. QUOTA WARNING EMAILS: Sent to restaurant owners when approaching quota.

  4. All sends are logged to notification_log with restaurant_id.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import UUID

import httpx

from config import settings
from database import get_pool, get_sender_phone, get_restaurant_by_id

logger = logging.getLogger(__name__)


# =============================================================================
# Email Templates (Branded per-restaurant)
# =============================================================================

def _build_confirmation_html(restaurant_name: str, data: Dict[str, Any]) -> str:
    """Generates a restaurant-branded HTML confirmation email."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reservation Confirmed — {restaurant_name}</title>
  <style>
    body{{font-family:Georgia,serif;background:#f9f5f0;margin:0;padding:20px}}
    .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
           overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
    .hdr{{background:#1a3a2a;color:#fff;padding:32px 40px;text-align:center}}
    .hdr h1{{margin:0;font-size:26px;letter-spacing:1px}}
    .hdr p{{margin:6px 0 0;opacity:.8;font-size:13px}}
    .body{{padding:36px 40px}}
    .card{{background:#f5f0e8;border-radius:8px;padding:22px;margin:20px 0}}
    .row{{display:flex;justify-content:space-between;padding:8px 0;
          border-bottom:1px solid #e8e0d0;font-size:15px}}
    .row:last-child{{border-bottom:none}}
    .lbl{{color:#777}}.val{{color:#222;font-weight:bold}}
    .code-box{{background:#1a3a2a;color:#fff;text-align:center;
               border-radius:8px;padding:16px;margin:24px 0}}
    .code{{font-size:28px;font-weight:700;letter-spacing:4px}}
    .footer{{background:#f0ebe1;padding:18px 40px;text-align:center;
             font-size:12px;color:#999}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>{restaurant_name}</h1>
    <p>Reservation Confirmation</p>
  </div>
  <div class="body">
    <p>Dear {data.get('customer_name','Guest')},</p>
    <p>Your reservation at <strong>{restaurant_name}</strong> is confirmed.</p>
    <div class="card">
      <div class="row"><span class="lbl">📅 Date</span>
        <span class="val">{data.get('reservation_date','')}</span></div>
      <div class="row"><span class="lbl">🕐 Time</span>
        <span class="val">{data.get('reservation_time','')}</span></div>
      <div class="row"><span class="lbl">👥 Guests</span>
        <span class="val">{data.get('party_size','')} guests</span></div>
      <div class="row"><span class="lbl">🪑 Table</span>
        <span class="val">{data.get('table_number','TBD')}</span></div>
      {"<div class='row'><span class='lbl'>📋 Requests</span>" +
       f"<span class='val'>{data.get('special_requests')}</span></div>"
       if data.get('special_requests') else ""}
    </div>
    <div class="code-box">
      <div class="code">{data.get('confirmation_code','')}</div>
      <p style="margin:4px 0 0;font-size:12px;opacity:.8">Your confirmation code</p>
    </div>
    <p style="color:#555;font-size:13px">
      To modify or cancel, contact us with your confirmation code and the email used to book.
    </p>
  </div>
  <div class="footer">
    <p>Powered by <strong>LiftUp</strong> · liftupai.com</p>
  </div>
</div>
</body>
</html>"""


def _build_quota_warning_html(
    restaurant_name: str,
    owner_name: str,
    pct: int,
    used: int,
    limit: int,
    remaining: int,
    tier: str,
    upgrade_url: str,
) -> str:
    color = "#e65100" if pct >= 95 else "#f57c00"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Reservation Quota Alert — LiftUp</title>
  <style>
    body{{font-family:Arial,sans-serif;background:#f5f5f5;padding:20px}}
    .wrap{{max-width:560px;margin:0 auto;background:#fff;
           border-radius:8px;padding:32px;box-shadow:0 2px 12px rgba(0,0,0,.1)}}
    .badge{{display:inline-block;background:{color};color:#fff;
            padding:6px 14px;border-radius:20px;font-size:13px;font-weight:700}}
    .bar-bg{{background:#e0e0e0;border-radius:4px;height:12px;margin:16px 0}}
    .bar{{background:{color};height:12px;border-radius:4px;width:{pct}%}}
    .cta{{display:block;background:#1a3a2a;color:#fff;text-align:center;
          padding:14px;border-radius:6px;text-decoration:none;
          font-weight:700;font-size:15px;margin-top:20px}}
  </style>
</head>
<body>
<div class="wrap">
  <span class="badge">⚠ Quota Alert — {pct}% Used</span>
  <h2 style="color:#1a3a2a;margin:16px 0 8px">Hi {owner_name},</h2>
  <p>Your reservation quota for <strong>{restaurant_name}</strong> on LiftUp is
     <strong>{pct}% used</strong>.</p>
  <div class="bar-bg"><div class="bar"></div></div>
  <p><strong>{used}</strong> of {limit} reservations used
     &nbsp;·&nbsp; <strong>{remaining} remaining</strong></p>
  {"<p style='color:#c62828'><strong>Action required:</strong> When you reach 100%, new reservations will be blocked.</p>" if pct >= 95 else ""}
  <p>Current plan: <strong>{tier.title()}</strong></p>
  <a class="cta" href="{upgrade_url}">
    {'Upgrade Now to Avoid Service Interruption' if pct >= 95 else 'View Billing & Upgrade Options'}
  </a>
  <p style="margin-top:20px;font-size:12px;color:#999">
    You're receiving this because you're the owner of {restaurant_name} on LiftUp.
  </p>
</div>
</body>
</html>"""


# =============================================================================
# Core Email Sender (SendGrid)
# =============================================================================

async def _send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    plain_body: str,
    from_name: str,
    reservation_id: Optional[str] = None,
    restaurant_id:  Optional[UUID] = None,
    template_name:  str = "generic",
) -> bool:
    payload = {
        "personalizations": [{"to": [{"email": to_email, "name": to_name}]}],
        "from": {"email": settings.EMAIL_FROM_ADDRESS, "name": from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": plain_body},
            {"type": "text/html",  "value": html_body},
        ],
    }
    provider_id = error_msg = None
    success     = False

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
            )
            if resp.status_code == 202:
                provider_id = resp.headers.get("X-Message-Id")
                success     = True
            else:
                error_msg = f"SG {resp.status_code}: {resp.text[:200]}"
                logger.error(error_msg)
    except httpx.RequestError as e:
        error_msg = f"Email network error: {e}"
        logger.exception(error_msg)

    if reservation_id and restaurant_id:
        await _log(
            restaurant_id, reservation_id, "email", to_email,
            template_name, success, provider_id, error_msg,
        )
    return success


# =============================================================================
# Reservation Confirmation Email
# =============================================================================

async def send_confirmation_email(
    restaurant_id: UUID,
    restaurant_name: str,
    reservation_id: str,
    customer_email: str,
    customer_name: str,
    confirmation_code: str,
    reservation_date: str,
    reservation_time: str,
    party_size: int,
    table_number: str = "TBD",
    special_requests: Optional[str] = None,
    **kwargs,
) -> bool:
    data = {
        "customer_name":     customer_name,
        "confirmation_code": confirmation_code,
        "reservation_date":  reservation_date,
        "reservation_time":  reservation_time,
        "party_size":        party_size,
        "table_number":      table_number,
        "special_requests":  special_requests,
    }
    return await _send_email(
        to_email=customer_email,
        to_name=customer_name,
        subject=f"✅ Reservation Confirmed — {confirmation_code} | {restaurant_name}",
        html_body=_build_confirmation_html(restaurant_name, data),
        plain_body=(
            f"Reservation at {restaurant_name} confirmed!\n"
            f"Date: {reservation_date} at {reservation_time}\n"
            f"Party: {party_size} guests | Table: {table_number}\n"
            f"Code: {confirmation_code}"
        ),
        from_name=f"{restaurant_name} Reservations",
        reservation_id=reservation_id,
        restaurant_id=restaurant_id,
        template_name="booking_confirmation",
    )


# =============================================================================
# Reservation Confirmation SMS (Dynamic Twilio Routing)
# =============================================================================

async def send_confirmation_sms(
    restaurant_id: UUID,
    reservation_id: str,
    customer_phone: str,
    customer_name: str,
    confirmation_code: str,
    reservation_date: str,
    reservation_time: str,
    party_size: int,
    table_number: str = "TBD",
    **kwargs,
) -> bool:
    """
    Sends SMS using the restaurant's own dedicated Twilio number.
    Falls back to the platform number if no dedicated number is configured.
    """
    # ── Dynamic sender number lookup ─────────────────────────────────────
    sender_number = await get_sender_phone(restaurant_id)

    if not sender_number:
        logger.warning(
            f"No dedicated phone for restaurant {restaurant_id}. "
            f"Falling back to platform number."
        )
        sender_number = settings.TWILIO_PLATFORM_NUMBER  # Platform fallback

    if not sender_number:
        logger.error(f"No SMS sender available for restaurant {restaurant_id}.")
        return False

    # Build per-restaurant branded SMS body
    restaurant = await get_restaurant_by_id(restaurant_id)
    rest_name  = restaurant["restaurant_name"] if restaurant else "The Restaurant"

    body = (
        f"{rest_name} ✓\n"
        f"Booking confirmed for {customer_name}!\n"
        f"📅 {reservation_date} at {reservation_time}\n"
        f"👥 {party_size} guests | Table {table_number}\n"
        f"Code: {confirmation_code}"
    )

    provider_id = error_msg = None
    success = False

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/"
                f"{settings.TWILIO_ACCOUNT_SID}/Messages.json",
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                data={
                    "From": sender_number,
                    "To":   customer_phone,
                    "Body": body,
                },
            )
            result = resp.json()
            if resp.status_code == 201:
                provider_id = result.get("sid")
                success     = True
                logger.info(
                    f"SMS sent from {sender_number} to {customer_phone} "
                    f"(restaurant {restaurant_id})"
                )
            else:
                error_msg = f"Twilio {resp.status_code}: {result.get('message','')}"
                logger.error(error_msg)

    except httpx.RequestError as e:
        error_msg = f"SMS network error: {e}"
        logger.exception(error_msg)

    await _log(
        restaurant_id, reservation_id, "sms", customer_phone,
        "booking_confirmation", success, provider_id, error_msg,
    )
    return success


# =============================================================================
# Quota Warning Email (to Restaurant Owner)
# =============================================================================

async def send_quota_warning_email(restaurant_id: UUID, pct: int) -> None:
    """
    Sends a quota warning email to the restaurant OWNER (not the guest).
    Called as a background task from the billing quota checks in main.py.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.owner_email, r.owner_name, r.restaurant_name,
                   s.tier, s.trial_reservations_used, s.trial_limit,
                   s.cycle_reservations_used, s.cycle_reservation_limit,
                   s.status
            FROM restaurants r
            JOIN subscriptions s ON s.restaurant_id = r.id AND s.is_current = TRUE
            WHERE r.id = $1
            """,
            restaurant_id,
        )
    if not row:
        return

    if row["tier"] == "trial":
        used, limit = row["trial_reservations_used"], row["trial_limit"]
    else:
        used, limit = row["cycle_reservations_used"], row["cycle_reservation_limit"]

    remaining = limit - used
    upgrade_url = f"{settings.PLATFORM_URL}/dashboard/billing"

    html = _build_quota_warning_html(
        restaurant_name=row["restaurant_name"],
        owner_name=row["owner_name"],
        pct=pct,
        used=used,
        limit=limit,
        remaining=remaining,
        tier=row["tier"],
        upgrade_url=upgrade_url,
    )

    await _send_email(
        to_email=row["owner_email"],
        to_name=row["owner_name"],
        subject=(
            f"⚠️ Action Required: {pct}% of reservations used — {row['restaurant_name']}"
            if pct >= 95
            else f"Heads up: {pct}% of reservation quota used — {row['restaurant_name']}"
        ),
        html_body=html,
        plain_body=(
            f"Hi {row['owner_name']},\n\n"
            f"Your reservation quota for {row['restaurant_name']} is {pct}% used "
            f"({used}/{limit}, {remaining} remaining).\n\n"
            f"Upgrade at: {upgrade_url}"
        ),
        from_name="LiftUp Platform",
        template_name=f"quota_warning_{pct}pct",
    )
    logger.info(f"Quota warning ({pct}%) sent to {row['owner_email']}")


# =============================================================================
# Notification Logger
# =============================================================================

async def _log(
    restaurant_id: UUID,
    reservation_id: str,
    channel: str,
    recipient: str,
    template_name: str,
    success: bool,
    provider_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO notification_log
                (restaurant_id, reservation_id, channel, recipient,
                 template_name, status, provider_id, error_message, sent_at)
            VALUES ($1,$2,$3::notification_channel,$4,$5,
                    $6::notification_status,$7,$8,$9)
            """,
            restaurant_id, reservation_id, channel, recipient,
            template_name,
            "sent" if success else "failed",
            provider_id, error_message,
            datetime.now(timezone.utc) if success else None,
        )
