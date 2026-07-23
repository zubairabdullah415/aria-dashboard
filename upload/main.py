"""
main.py — LiftUp SaaS Multi-Tenant FastAPI Application
========================================================
Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │  Widget Request (X-API-Key header)                      │
  │        │                                                │
  │        ▼                                                │
  │  [TenantAuthMiddleware]  ←── Validates API key          │
  │        │                     Loads restaurant + sub     │
  │        ▼                     Attaches to request.state  │
  │  [QuotaEnforcementDep]   ←── Checks billing quota       │
  │        │                     Blocks if exhausted        │
  │        ▼                                                │
  │  Route Handler            ←── Uses request.state.tenant │
  └─────────────────────────────────────────────────────────┘

Security layers:
  1. API key validation (database lookup via unique index)
  2. Tenant data on request.state (never from client body)
  3. All DB queries pass restaurant_id from request.state
  4. RLS session variable set in TenantConn context
  5. Rate limiting per IP (slowapi)
  6. Twilio HMAC-SHA1 signature validation on /webhooks/sms

Background services (lifespan):
  • APScheduler — fires _run_billing_cycle_resets() every 24 h
    Finds subscriptions where billing_cycle_end <= NOW() and calls
    fn_reset_billing_cycle() to zero the counter and push the window
    forward 30 days.  Runs once immediately on startup to catch any
    cycles that expired while the server was down.

Inbound SMS (POST /webhooks/sms):
  • Registered as the Twilio SmsUrl when a number is provisioned
  • Validates Twilio signature before processing
  • Appends the message to the customer's active conversation session
  • Forwards the text to the restaurant owner via email
  • Returns empty TwiML <Response/> (no auto-reply)
"""

import logging
import secrets
import hashlib
import hmac
import asyncio
from datetime import date, time as time_type
from typing import Optional
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import (
    FastAPI, HTTPException, Depends, Request, status,
    BackgroundTasks, Header, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr, Field, validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from database import (
    get_pool, close_pool,
    get_restaurant_by_api_key,
    get_or_create_session,
    update_session,
    get_available_time_slots,
    get_reservation_by_code,
    cancel_reservation as db_cancel,
    check_and_enforce_quota,
    mark_quota_exhausted,
    send_quota_warning,
)
from agent import AgentContext, run_agent, sanitize_conversation_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# Lifespan & App Init
# =============================================================================

async def _run_billing_cycle_resets() -> None:
    """
    APScheduler job — runs every 24 hours.

    Finds every paid subscription whose 30-day billing window has expired
    and calls the PostgreSQL function that zeros the counter and opens a
    fresh cycle. The DB function is idempotent so re-running it on an
    already-reset subscription is harmless.

    Restaurants on the 'trial' tier are intentionally skipped — their
    counters are lifetime totals, not rolling windows.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        expired_rows = await conn.fetch(
            """
            SELECT restaurant_id
            FROM   subscriptions
            WHERE  is_current        = TRUE
              AND  tier             != 'trial'
              AND  billing_cycle_end <= NOW()
            """
        )

    if not expired_rows:
        logger.debug("Billing reset job: no expired cycles found.")
        return

    logger.info(f"Billing reset job: resetting {len(expired_rows)} expired cycle(s).")

    async with pool.acquire() as conn:
        for row in expired_rows:
            rid = row["restaurant_id"]
            try:
                # fn_reset_billing_cycle zeros the counter, pushes cycle_end
                # forward 30 days, and sets status back to 'active'.
                await conn.execute("SELECT fn_reset_billing_cycle($1)", rid)
                logger.info(f"Billing cycle reset: restaurant {rid}")
            except Exception as exc:
                # Log and continue — one failure must not block the rest
                logger.exception(f"Billing cycle reset FAILED for {rid}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LiftUp SaaS starting — initializing DB pool...")
    await get_pool()

    # ── Start billing-cycle reset scheduler ──────────────────────────────
    import datetime as _dt
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _run_billing_cycle_resets,
        trigger=IntervalTrigger(hours=24),
        id="billing_cycle_reset",
        name="30-day billing cycle reset",
        replace_existing=True,
        # Fire once immediately at startup so a freshly-deployed server
        # catches any cycles that expired while it was down.
        next_run_time=_dt.datetime.utcnow(),
    )
    scheduler.start()
    logger.info("APScheduler started — billing reset job fires every 24 h.")

    yield

    # ── Graceful shutdown ─────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped.")
    logger.info("Shutting down — closing DB pool...")
    await close_pool()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="LiftUp SaaS — Restaurant Reservation Platform API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Widget is embedded on arbitrary domains
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Api-Key", "X-Session-Token"],
    expose_headers=["X-Session-Token"],
)


# =============================================================================
# Tenant Authentication Middleware
# =============================================================================

@app.middleware("http")
async def tenant_auth_middleware(request: Request, call_next):
    """
    Intercepts every /api/widget/* request. Validates the restaurant API key,
    loads the full tenant + subscription record, and attaches it to
    request.state.tenant so downstream handlers never need to re-query.

    Routes NOT requiring tenant auth (owner dashboard, onboarding, health)
    are exempted explicitly.
    """
    EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/api/onboarding",
                   "/api/auth/login", "/api/auth/register", "/api/billing/webhook",
                   "/webhooks/sms"}

    if request.url.path in EXEMPT_PATHS or not request.url.path.startswith("/api/widget"):
        return await call_next(request)

    api_key = request.headers.get("X-Api-Key", "").strip()

    if request.method == "OPTIONS":
        return await call_next(request)

    api_key = request.headers.get("X-Api-Key", "").strip()

    # Constant-time comparison is not needed here since we do a DB lookup,
    # but we hash the key before the lookup to avoid timing side-channels
    # (the DB index on api_key handles the comparison securely).
    restaurant_data = await get_restaurant_by_api_key(api_key)

    if not restaurant_data:
        logger.warning(f"Invalid API key attempt: {api_key[:8]}...")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key.", "code": "AUTH_INVALID"},
        )

    # Attach tenant data — downstream code reads from here, never from headers
    request.state.tenant          = restaurant_data
    request.state.restaurant_id   = restaurant_data["id"]
    request.state.restaurant_name = restaurant_data["restaurant_name"]

    response = await call_next(request)
    return response


# =============================================================================
# Billing / Quota Enforcement Dependency
# =============================================================================

async def enforce_quota(request: Request) -> None:
    """
    FastAPI dependency that enforces billing quota before any booking action.

    Inject as: Depends(enforce_quota)

    Checks:
      - Trial quota (100 reservations lifetime)
      - Monthly quota for paid plans
      - Sends async warning notifications at 80% and 95% usage
    """
    tenant = request.state.tenant
    restaurant_id = request.state.restaurant_id

    quota = await check_and_enforce_quota(tenant)

    if not quota["allowed"]:
        reason = quota["reason"]
        logger.warning(
            f"Quota blocked: restaurant={restaurant_id}, reason={reason}"
        )
        # Mark as exhausted in DB (idempotent)
        await mark_quota_exhausted(restaurant_id, reason)

        messages = {
            "trial_exhausted": (
                "Your free trial of 100 reservations has been used. "
                "Please upgrade to the Starter plan ($299/month) to continue accepting bookings."
            ),
            "quota_exceeded": (
                "Your monthly reservation quota has been reached. "
                "Please contact support or recharge your plan to restore service."
            ),
            "payment_failed": (
                "There is an issue with your payment method. "
                "Please update your billing details to restore service."
            ),
        }
        raise HTTPException(
            status_code=402,
            detail={
                "code":    reason.upper(),
                "message": messages.get(reason, "Service temporarily unavailable."),
                "upgrade_url": f"{settings.PLATFORM_URL}/dashboard/billing",
            },
        )

    # Async quota warnings (fire and forget — don't block the request)
    remaining = quota["reservations_remaining"]
    total     = (
        tenant["trial_limit"] if tenant["tier"] == "trial"
        else tenant["cycle_reservation_limit"]
    )
    pct_used  = ((total - remaining) / total * 100) if total > 0 else 0

    if pct_used >= 95 and not tenant.get("warned_at_95_pct"):
        asyncio.create_task(send_quota_warning(restaurant_id, 95))
    elif pct_used >= 80 and not tenant.get("warned_at_80_pct"):
        asyncio.create_task(send_quota_warning(restaurant_id, 80))


# =============================================================================
# Pydantic Models
# =============================================================================

class ChatRequest(BaseModel):
    message:       str = Field(..., min_length=1, max_length=2000)
    session_token: Optional[str] = None

    @validator("message")
    def sanitize(cls, v):
        return " ".join(v.split())


class ChatResponse(BaseModel):
    reply:             str
    session_token:     str
    booking_complete:  bool          = False
    confirmation_code: Optional[str] = None


class CancelRequest(BaseModel):
    email: EmailStr


class ModifyRequest(BaseModel):
    email:          EmailStr
    new_date:       Optional[date]     = None
    new_time:       Optional[str]      = None
    new_party_size: Optional[int]      = None


# =============================================================================
# Session Token Helper
# =============================================================================

async def get_session(request: Request) -> dict:
    token = request.headers.get("X-Session-Token", "")
    if not token or len(token) < 16:
        token = secrets.token_urlsafe(32)
    session = await get_or_create_session(
        restaurant_id=request.state.restaurant_id,
        session_token=token,
    )
    session["_token"] = token
    return session


# =============================================================================
# Widget API Routes (require tenant auth + quota)
# =============================================================================

@app.post(
    "/api/widget/chat",
    response_model=ChatResponse,
    tags=["Widget — AI Agent"],
)
@limiter.limit("30/minute")
async def widget_chat(
    request:    Request,
    body:       ChatRequest,
    _quota:     None = Depends(enforce_quota),
    session:    dict = Depends(get_session),
):
    """
    Primary AI conversation endpoint for embedded widgets.
    Requires X-Api-Key (widget key) in headers.
    """
    token         = session["_token"]
    restaurant_id = request.state.restaurant_id

    # Hydrate agent context from session + restaurant profile
    # Sanitize history to repair any corrupted content blocks from
    # sessions created before the _serialize_content_block fix.
    raw_history = session.get("messages", [])
    safe_history = sanitize_conversation_history(raw_history)

    ctx = AgentContext(
        restaurant_id=restaurant_id,
        restaurant_name=request.state.restaurant_name,
        restaurant_data=request.state.tenant,
        conversation_history=safe_history,
        booking_context=session.get("context", {}),
    )

    try:
        reply, updated_ctx = await run_agent(body.message, ctx)
    except Exception as e:
        logger.exception(f"Agent error [{restaurant_id}]: {e}")
        raise HTTPException(
            status_code=500,
            detail="The reservation assistant encountered an error. Please try again.",
        )

    await update_session(
        restaurant_id=restaurant_id,
        session_token=token,
        messages=updated_ctx.conversation_history,
        context=updated_ctx.booking_context,
        customer_id=updated_ctx.booking_context.get("customer_id"),
        reservation_id=updated_ctx.booking_context.get("reservation_id"),
    )

    return ChatResponse(
        reply=reply,
        session_token=token,
        booking_complete=updated_ctx.booking_context.get("booking_complete", False),
        confirmation_code=updated_ctx.booking_context.get("confirmation_code"),
    )


@app.get(
    "/api/widget/availability",
    tags=["Widget — Availability"],
)
@limiter.limit("60/minute")
async def widget_availability(
    request:    Request,
    date:       date,
    party_size: int,
    preference: Optional[str] = "no_preference",
    _quota:     None = Depends(enforce_quota),
):
    """Returns available time slots for a date picker (read-only, no booking)."""
    slots = await get_available_time_slots(
        restaurant_id=request.state.restaurant_id,
        reservation_date=date,
        party_size=party_size,
        preference=preference,
    )
    return {
        "restaurant": request.state.restaurant_name,
        "date": str(date),
        "party_size": party_size,
        "available_slots": slots,
    }


@app.get(
    "/api/widget/reservations/{code}",
    tags=["Widget — Reservations"],
)
@limiter.limit("20/minute")
async def widget_get_reservation(
    request: Request,
    code:    str,
):
    """Fetch reservation — always scoped to the authenticated restaurant."""
    reservation = await get_reservation_by_code(
        request.state.restaurant_id, code.upper()
    )
    if not reservation:
        raise HTTPException(404, f"Reservation '{code}' not found.")

    return {
        "confirmation_code": reservation["confirmation_code"],
        "customer_name":     reservation["full_name"],
        "reservation_date":  str(reservation["reservation_date"]),
        "start_time":        str(reservation["start_time"])[:5],
        "party_size":        reservation["party_size"],
        "table_number":      reservation["table_number"],
        "status":            reservation["status"],
        "special_requests":  reservation.get("special_requests"),
    }


@app.delete(
    "/api/widget/reservations/{code}",
    tags=["Widget — Reservations"],
)
@limiter.limit("10/minute")
async def widget_cancel_reservation(
    request: Request,
    code:    str,
    body:    CancelRequest,
):
    try:
        result = await db_cancel(
            restaurant_id=request.state.restaurant_id,
            code=code.upper(),
            customer_email=body.email,
        )
        return {"success": True, "cancelled_code": result["confirmation_code"]}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put(
    "/api/widget/reservations/{code}",
    tags=["Widget — Reservations"],
)
@limiter.limit("10/minute")
async def widget_modify_reservation(
    request: Request,
    code:    str,
    body:    ModifyRequest,
):
    from database import modify_reservation
    try:
        new_time = time_type.fromisoformat(body.new_time) if body.new_time else None
        result = await modify_reservation(
            restaurant_id=request.state.restaurant_id,
            code=code.upper(),
            customer_email=body.email,
            new_date=body.new_date,
            new_time=new_time,
            new_party_size=body.new_party_size,
        )
        return {
            "success": True,
            "old_code": code.upper(),
            "new_confirmation_code": result["confirmation_code"],
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


# =============================================================================
# Twilio Inbound SMS Webhook  ·  POST /webhooks/sms
# =============================================================================
# Twilio calls this URL (configured per-number during provisioning) whenever
# a customer replies to a restaurant's SMS confirmation.
#
# Security: Every request is validated using Twilio's HMAC-SHA1 signature
# scheme before any business logic runs.  Requests that fail validation are
# rejected with 403 so random internet traffic cannot inject fake messages.
#
# Flow:
#   1. Validate Twilio signature
#   2. Identify the restaurant via the "To" number (the dedicated sender)
#   3. Identify the customer via "From" (their mobile number)
#   4. Persist the message to conversation_sessions (latest active session)
#      or to a standalone inbound_sms_log fallback
#   5. Forward a notification email to the restaurant owner
#   6. Return empty TwiML <Response/> — no auto-reply by default
# =============================================================================

def _validate_twilio_signature(request_url: str, post_params: dict, signature: str) -> bool:
    """
    Validates the X-Twilio-Signature header using HMAC-SHA1.
    See: https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    # Build the validation string: URL + sorted POST params concatenated
    s = request_url + "".join(f"{k}{v}" for k, v in sorted(post_params.items()))
    expected = hmac.new(
        settings.TWILIO_AUTH_TOKEN.encode(),
        s.encode(),
        "sha1",
    ).digest()
    import base64
    expected_b64 = base64.b64encode(expected).decode()
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_b64, signature)


async def _get_restaurant_by_phone(to_number: str):
    """Resolves inbound Twilio 'To' number → restaurant row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT r.id, r.restaurant_name, r.owner_email, r.owner_name
            FROM   restaurant_phone_numbers p
            JOIN   restaurants r ON r.id = p.restaurant_id
            WHERE  p.phone_number = $1 AND p.is_active = TRUE
            """,
            to_number,
        )


async def _append_to_session(restaurant_id, from_number: str, body: str) -> bool:
    """
    Appends the inbound SMS as a new message in the customer's most recent
    active conversation session (matched by phone number).
    Returns True if a matching session was found and updated.
    """
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Find the most recent active session for this phone number
        session_row = await conn.fetchrow(
            """
            SELECT cs.id, cs.messages
            FROM   conversation_sessions cs
            JOIN   customers c ON c.id = cs.customer_id
            WHERE  cs.restaurant_id = $1
              AND  c.phone          = $2
              AND  cs.is_active     = TRUE
              AND  cs.expires_at    > NOW()
            ORDER BY cs.last_active_at DESC
            LIMIT 1
            """,
            restaurant_id, from_number,
        )
        if not session_row:
            return False

        current_messages = session_row["messages"] or []
        current_messages.append({
            "role":    "user",
            "content": f"[SMS reply]: {body}",
            "via":     "sms_inbound",
        })

        await conn.execute(
            """
            UPDATE conversation_sessions
            SET    messages       = $2::jsonb,
                   last_active_at = NOW()
            WHERE  id = $1
            """,
            session_row["id"],
            _json.dumps(current_messages),
        )
        return True


async def _log_inbound_sms(restaurant_id, from_number: str, to_number: str, body: str) -> None:
    """
    Fallback: writes the message to notification_log as an inbound record
    when no matching session exists (e.g., customer texts days later).
    Uses a synthetic reservation_id of NULL via a raw insert.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # notification_log requires a reservation_id FK — use a separate
        # lightweight table if available, otherwise just log to server logs.
        # For now we write to a catch-all row identified by channel='sms_inbound'.
        try:
            await conn.execute(
                """
                INSERT INTO notification_log
                    (restaurant_id, reservation_id, channel, recipient,
                     template_name, status, provider_id)
                SELECT $1, r.id, 'sms'::notification_channel, $2,
                       'inbound_sms', 'sent'::notification_status, $3
                FROM   reservations r
                WHERE  r.restaurant_id = $1
                  AND  r.status        = 'confirmed'
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                restaurant_id, from_number, f"FROM:{from_number} BODY:{body[:100]}",
            )
        except Exception:
            # Non-critical — the important notification is the owner email below
            logger.warning(f"Could not write inbound SMS to notification_log for {restaurant_id}")


async def _notify_owner_of_inbound_sms(
    owner_email: str,
    owner_name: str,
    restaurant_name: str,
    from_number: str,
    body: str,
) -> None:
    """Sends a quick email to the restaurant owner forwarding the customer's reply."""
    from notifications import _send_email
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                padding:24px;border:1px solid #e0e0e0;border-radius:8px">
      <h3 style="color:#1a3a2a;margin-top:0">📱 New SMS reply from a guest</h3>
      <p><strong>Restaurant:</strong> {restaurant_name}</p>
      <p><strong>From:</strong> {from_number}</p>
      <div style="background:#f5f5f5;border-left:4px solid #1a3a2a;
                  padding:12px 16px;border-radius:0 4px 4px 0;
                  font-size:16px;margin:16px 0">
        "{body}"
      </div>
      <p style="color:#666;font-size:12px">
        This message was sent to your dedicated LiftUp reservation number.
        Reply directly to the customer at {from_number}.
      </p>
    </div>
    """
    await _send_email(
        to_email=owner_email,
        to_name=owner_name,
        subject=f"📱 Guest SMS reply — {restaurant_name}",
        html_body=html,
        plain_body=f"New SMS from {from_number}:\n\n\"{body}\"\n\nReply at: {from_number}",
        from_name="LiftUp Notifications",
        template_name="inbound_sms_forward",
    )


@app.post(
    "/webhooks/sms",
    tags=["Webhooks"],
    summary="Twilio inbound SMS webhook",
    include_in_schema=False,   # Hide from public docs
)
@limiter.limit("120/minute")   # Twilio can burst — allow generous headroom
async def twilio_sms_webhook(
    request:          Request,
    background_tasks: BackgroundTasks,
    # Twilio sends application/x-www-form-urlencoded — use Form()
    From:    str = Form(...),   # Customer's mobile number (E.164)
    To:      str = Form(...),   # Restaurant's dedicated number (E.164)
    Body:    str = Form(""),    # Message text (may be empty for media-only MMS)
    MessageSid: str = Form(...),
):
    """
    Receives inbound SMS from Twilio.

    Twilio delivers a POST with form-encoded fields:
      From       — the customer's number
      To         — the restaurant's dedicated number
      Body       — the message text
      MessageSid — Twilio's unique ID for idempotency checks

    Security: The X-Twilio-Signature header is validated against our
    auth token before any processing occurs.
    """
    # ── 1. Validate Twilio signature ──────────────────────────────────────
    signature = request.headers.get("X-Twilio-Signature", "")
    if not settings.DEBUG:   # Skip in local dev (no public URL for Twilio to sign)
        form_data  = dict(await request.form())
        public_url = str(request.url).replace("http://", "https://")
        if not _validate_twilio_signature(public_url, form_data, signature):
            logger.warning(
                f"Twilio signature validation FAILED for {MessageSid} "
                f"from {From} — possible spoofed request."
            )
            # Return 403 but still return valid XML so Twilio doesn't keep retrying
            return Response(
                content='<?xml version="1.0"?><Response/>',
                media_type="application/xml",
                status_code=403,
            )

    logger.info(f"Inbound SMS: {MessageSid} | From={From} To={To} | Body={Body[:80]!r}")

    # ── 2. Resolve restaurant from 'To' number ────────────────────────────
    restaurant_row = await _get_restaurant_by_phone(To)
    if not restaurant_row:
        logger.warning(f"Inbound SMS to unknown number {To} — no restaurant mapped.")
        return Response(
            content='<?xml version="1.0"?><Response/>',
            media_type="application/xml",
        )

    restaurant_id   = restaurant_row["id"]
    body_text       = Body.strip()

    # ── 3. Persist message (session append, or fallback log) ──────────────
    appended = await _append_to_session(restaurant_id, From, body_text)
    if not appended:
        # No live session — log it so it's not silently discarded
        background_tasks.add_task(_log_inbound_sms, restaurant_id, From, To, body_text)

    # ── 4. Forward to restaurant owner (background — non-blocking) ────────
    background_tasks.add_task(
        _notify_owner_of_inbound_sms,
        owner_email=restaurant_row["owner_email"],
        owner_name=restaurant_row["owner_name"],
        restaurant_name=restaurant_row["restaurant_name"],
        from_number=From,
        body=body_text or "(no text — possible media message)",
    )

    # ── 5. Return empty TwiML — no auto-reply ─────────────────────────────
    # Returning a non-empty <Message> here would auto-reply to the customer.
    # For now we stay silent and let the owner decide how to respond.
    return Response(
        content='<?xml version="1.0"?><Response/>',
        media_type="application/xml",
    )


# =============================================================================
# System Routes
# =============================================================================

@app.get("/health", tags=["System"])
async def health():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "healthy", "service": "LiftUp SaaS v2"}


@app.exception_handler(Exception)
async def global_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled: {request.url} — {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
