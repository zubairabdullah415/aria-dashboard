"""
database.py — Multi-Tenant Async Database Layer
=================================================
Every public function in this module requires a `restaurant_id` parameter.
The module enforces isolation at two levels:

  1. APPLICATION LEVEL: All queries include `WHERE restaurant_id = $n`
  2. DATABASE LEVEL:    Sets PostgreSQL session variable `app.current_restaurant_id`
                        so RLS policies provide a second independent safety net.

Pattern for all tenant queries:
    async with tenant_transaction(pool, restaurant_id) as conn:
        # All queries inside this context are RLS-scoped to restaurant_id
        row = await conn.fetchrow("SELECT * FROM reservations WHERE id = $1", id)
"""

import asyncpg
import logging
import random
import string
import secrets
import json as json_module
from datetime import date, time, datetime, timedelta
from typing import Optional, List, Dict, Any, AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from config import settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


# =============================================================================
# Connection Pool
# =============================================================================

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=10,
            max_size=50,
            command_timeout=30,
            statement_cache_size=0,
            server_settings={
                # Set the app role so RLS applies correctly
                "role": "liftup_app",
            },
            init=_init_connection,
        )
        logger.info("Multi-tenant DB pool initialized (min=10, max=50).")
    return _pool


async def _init_connection(conn: asyncpg.Connection):
    """Codec registration for JSON/JSONB on every new connection."""
    await conn.set_type_codec(
        "jsonb", schema="pg_catalog",
        encoder=json_module.dumps,
        decoder=json_module.loads,
    )
    await conn.set_type_codec(
        "json", schema="pg_catalog",
        encoder=json_module.dumps,
        decoder=json_module.loads,
    )


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# =============================================================================
# Tenant Connection Context Manager
# =============================================================================

class TenantConn:
    """
    Context manager that acquires a connection and sets the RLS session variable.

    Usage:
        async with tenant_transaction(pool, restaurant_id) as conn:
            rows = await conn.fetch("SELECT * FROM tables")
            # RLS ensures only tables for restaurant_id are visible

    The SET LOCAL is transaction-scoped, so it resets automatically when
    the connection is returned to the pool.
    """

    def __init__(self, pool: asyncpg.Pool, restaurant_id: UUID):
        self._pool          = pool
        self._restaurant_id = str(restaurant_id)
        self._conn          = None
        self._transaction   = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._conn = await self._pool.acquire()
        # Set RLS context — this is the second layer of tenant isolation
        await self._conn.execute(
            "SELECT set_config('app.current_restaurant_id', $1, TRUE)",
            self._restaurant_id,
        )
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        await self._pool.release(self._conn)
        self._conn = None


@asynccontextmanager
async def tenant_transaction(pool, restaurant_id: UUID):
    """
    Acquires a connection, sets RLS variables, and wraps EVERYTHING
    in a safe, atomic database transaction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Set the RLS variable for this specific transaction
            await conn.execute(
                "SET LOCAL app.current_restaurant_id = $1",
                str(restaurant_id)
            )
            yield conn


# =============================================================================
# Restaurant / Tenant Lookups (No RLS needed — not tenant-scoped)
# =============================================================================

async def get_restaurant_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Hot path: called on EVERY widget request.
    Uses the unique index on api_key for sub-millisecond lookups.
    Returns None if not found or inactive.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                r.*,
                s.tier,
                s.status          AS subscription_status,
                s.trial_reservations_used,
                s.trial_limit,
                s.cycle_reservations_used,
                s.cycle_reservation_limit,
                s.billing_cycle_end,
                s.warned_at_80_pct,
                s.warned_at_95_pct,
                s.owner_notified_exhausted
            FROM restaurants r
            JOIN subscriptions s
              ON s.restaurant_id = r.id AND s.is_current = TRUE
            WHERE r.api_key = $1 AND r.is_active = TRUE
            """,
            api_key,
        )
        return dict(row) if row else None


async def get_restaurant_by_id(restaurant_id: UUID) -> Optional[Dict[str, Any]]:
    """Used by the agent to load full restaurant profile for dynamic prompting."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.*, p.phone_number AS sender_phone
            FROM restaurants r
            LEFT JOIN restaurant_phone_numbers p
              ON p.restaurant_id = r.id AND p.is_active = TRUE
            WHERE r.id = $1
            """,
            restaurant_id,
        )
        return dict(row) if row else None


async def get_sender_phone(restaurant_id: UUID) -> Optional[str]:
    """Returns the active Twilio sender number for a restaurant."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT phone_number FROM restaurant_phone_numbers "
            "WHERE restaurant_id = $1 AND is_active = TRUE",
            restaurant_id,
        )


# =============================================================================
# Billing / Quota Management
# =============================================================================

async def check_and_enforce_quota(restaurant_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates whether a restaurant can accept a new reservation.

    Returns:
        {
          "allowed": bool,
          "reason": str | None,
          "tier": str,
          "reservations_remaining": int
        }
    """
    tier   = restaurant_data["tier"]
    status = restaurant_data["subscription_status"]

    # Hard blocks — service suspended
    if status in ("trial_exhausted", "quota_exceeded", "payment_failed",
                  "cancelled", "suspended"):
        return {
            "allowed": False,
            "reason": status,
            "tier": tier,
            "reservations_remaining": 0,
        }

    if tier == "trial":
        used  = restaurant_data["trial_reservations_used"]
        limit = restaurant_data["trial_limit"]
        remaining = limit - used
        if remaining <= 0:
            return {"allowed": False, "reason": "trial_exhausted", "tier": tier,
                    "reservations_remaining": 0}
        return {"allowed": True, "reason": None, "tier": tier,
                "reservations_remaining": remaining}

    # Paid tier
    used      = restaurant_data["cycle_reservations_used"]
    limit     = restaurant_data["cycle_reservation_limit"]
    remaining = limit - used
    if remaining <= 0:
        return {"allowed": False, "reason": "quota_exceeded", "tier": tier,
                "reservations_remaining": 0}
    return {"allowed": True, "reason": None, "tier": tier,
            "reservations_remaining": remaining}


async def mark_quota_exhausted(restaurant_id: UUID, reason: str) -> None:
    """Suspends service and flags for owner notification."""
    pool = await get_pool()
    new_status = "trial_exhausted" if reason == "trial_exhausted" else "quota_exceeded"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE subscriptions
            SET status = $2::subscription_status
            WHERE restaurant_id = $1 AND is_current = TRUE
            """,
            restaurant_id, new_status,
        )
    logger.warning(f"Restaurant {restaurant_id} quota exhausted: {reason}")


async def send_quota_warning(restaurant_id: UUID, pct: int) -> None:
    """
    Flags that a quota warning email should be sent (actual send via
    background task / scheduler to avoid blocking the request).
    """
    pool = await get_pool()
    flag_col = "warned_at_80_pct" if pct == 80 else "warned_at_95_pct"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE subscriptions SET {flag_col} = TRUE "
            f"WHERE restaurant_id = $1 AND is_current = TRUE",
            restaurant_id,
        )

    from notifications import send_quota_warning_email
    import asyncio
    asyncio.create_task(send_quota_warning_email(restaurant_id, pct))


async def upgrade_to_paid(
    restaurant_id: UUID,
    tier: str = "starter",
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upgrades a restaurant from trial (or exhausted) to a paid plan.
    Atomically creates a new subscription and marks old one non-current.
    """
    tier_limits = {
        "starter": {"limit": 1000, "price_cents": 29900},
        "growth":  {"limit": 5000, "price_cents": 69900},
        "enterprise": {"limit": 999999, "price_cents": 0},
    }
    cfg = tier_limits.get(tier, tier_limits["starter"])

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Archive current subscription
            await conn.execute(
                "UPDATE subscriptions SET is_current = FALSE "
                "WHERE restaurant_id = $1 AND is_current = TRUE",
                restaurant_id,
            )
            # Create new paid subscription
            row = await conn.fetchrow(
                """
                INSERT INTO subscriptions (
                    restaurant_id, tier, status,
                    billing_cycle_start, billing_cycle_end,
                    cycle_reservation_limit, monthly_price_cents,
                    stripe_customer_id, stripe_subscription_id,
                    is_current
                ) VALUES (
                    $1, $2::subscription_tier, 'active',
                    NOW(), NOW() + INTERVAL '30 days',
                    $3, $4, $5, $6, TRUE
                ) RETURNING *
                """,
                restaurant_id, tier, cfg["limit"], cfg["price_cents"],
                stripe_customer_id, stripe_subscription_id,
            )
            return dict(row)


# =============================================================================
# Availability (Tenant-Scoped)
# =============================================================================

async def check_availability(
    restaurant_id: UUID,
    reservation_date: date,
    start_time: time,
    party_size: int,
    preference: str = "no_preference",
    duration_minutes: int = 90,
) -> List[Dict[str, Any]]:
    """Returns available tables for a specific slot within a restaurant."""
    end_time = (
        datetime.combine(date.today(), start_time) + timedelta(minutes=duration_minutes)
    ).time()

    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        pref_filter = "AND t.location = $6::seating_preference" \
                      if preference != "no_preference" else ""
        query = f"""
            SELECT t.id, t.table_number, t.capacity, t.location,
                   t.description, t.is_accessible, t.has_high_chair
            FROM tables t
            WHERE
                t.restaurant_id = $1
                AND t.status = 'available'
                AND t.capacity >= $3
                AND t.id NOT IN (
                    SELECT DISTINCT r.table_id
                    FROM reservations r
                    WHERE
                        r.restaurant_id = $1
                        AND r.reservation_date = $2
                        AND r.status NOT IN ('cancelled', 'no_show')
                        AND r.start_time < $5 AND r.end_time > $4
                )
                {pref_filter}
            ORDER BY ABS(t.capacity - $3), t.location
            LIMIT 10
        """
        params = [restaurant_id, reservation_date, party_size, start_time, end_time]
        if preference != "no_preference":
            params.append(preference)

        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


async def get_available_time_slots(
    restaurant_id: UUID,
    reservation_date: date,
    party_size: int,
    preference: str = "no_preference",
) -> List[Dict[str, Any]]:
    """Returns all available time slots for a date/party/preference combination."""
    # Pull restaurant's operating hours to generate correct slots
    restaurant = await get_restaurant_by_id(restaurant_id)
    if not restaurant:
        return []

    hours = restaurant.get("operating_hours", {})
    day_name = reservation_date.strftime("%A").lower()
    day_cfg  = hours.get(day_name, {})

    if day_cfg.get("closed", False):
        return []

    # Generate 30-minute slots within operating hours
    open_str  = day_cfg.get("open", "11:30")
    close_str = day_cfg.get("close", "22:00")
    open_dt   = datetime.strptime(open_str, "%H:%M")
    close_dt  = datetime.strptime(close_str, "%H:%M")

    slots = []
    current = open_dt
    duration = restaurant.get("avg_dining_minutes", 90)

    while current + timedelta(minutes=duration) <= close_dt:
        slot_time = current.time()
        tables = await check_availability(
            restaurant_id, reservation_date, slot_time, party_size, preference, duration
        )
        if tables:
            slots.append({
                "time": current.strftime("%H:%M"),
                "tables_available": len(tables),
            })
        current += timedelta(minutes=30)

    return slots


# =============================================================================
# Customer Management (Tenant-Scoped)
# =============================================================================

async def find_or_create_customer(
    restaurant_id: UUID,
    full_name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    dietary_notes: Optional[str] = None,
    allergy_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Customer lookup and upsert, strictly scoped to restaurant_id."""
    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        # Try email lookup within this restaurant
        if email:
            row = await conn.fetchrow(
                "SELECT * FROM customers "
                "WHERE restaurant_id = $1 AND email = $2",
                restaurant_id, email,
            )
            if row:
                await conn.execute(
                    "UPDATE customers SET full_name=$1, phone=COALESCE($2,phone), "
                    "dietary_notes=COALESCE($3,dietary_notes), "
                    "allergy_notes=COALESCE($4,allergy_notes) "
                    "WHERE id=$5 AND restaurant_id=$6",
                    full_name, phone, dietary_notes, allergy_notes,
                    row["id"], restaurant_id,
                )
                return dict(row)

        # Try phone lookup
        if phone:
            row = await conn.fetchrow(
                "SELECT * FROM customers "
                "WHERE restaurant_id = $1 AND phone = $2",
                restaurant_id, phone,
            )
            if row:
                return dict(row)

        # Create new customer
        row = await conn.fetchrow(
            """
            INSERT INTO customers
                (restaurant_id, full_name, email, phone, dietary_notes, allergy_notes)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            restaurant_id, full_name, email, phone, dietary_notes, allergy_notes,
        )
        return dict(row)


# =============================================================================
# Reservation Management (Tenant-Scoped, with Locking)
# =============================================================================

def _make_confirmation_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    chars = random.choices(string.ascii_uppercase + string.digits, k=6)
    return "RES-" + "".join(chars)


async def book_table(
    restaurant_id: UUID,
    customer_id: UUID,
    table_id: UUID,
    reservation_date: date,
    start_time: time,
    party_size: int,
    special_requests: Optional[str] = None,
    duration_minutes: int = 90,
) -> Dict[str, Any]:
    """
    Atomically books a table with SELECT FOR UPDATE NOWAIT.
    All isolation checks run inside one transaction:
      1. Lock table row (NOWAIT → instant fail if contested)
      2. Re-verify table belongs to THIS restaurant (prevents cross-tenant exploit)
      3. Re-verify no overlap within the transaction
      4. Insert reservation
    """
    end_time = (
        datetime.combine(date.today(), start_time) + timedelta(minutes=duration_minutes)
    ).time()

    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        # ── Lock the table row ─────────────────────────────────────────────
        # CRITICAL: verify restaurant_id on the table — prevents a malicious
        # client from supplying a table_id belonging to another restaurant.
        table_row = await conn.fetchrow(
            """
            SELECT * FROM tables
            WHERE id = $1 AND restaurant_id = $2
            FOR UPDATE NOWAIT
            """,
            table_id, restaurant_id,
        )
        if not table_row:
            raise ValueError("Table not found for this restaurant.")
        if table_row["status"] != "available":
            raise ValueError(f"Table {table_row['table_number']} is not available.")
        if party_size > table_row["capacity"]:
            raise ValueError(
                f"Party size {party_size} exceeds table capacity {table_row['capacity']}."
            )

        # ── Re-check overlap inside the lock ──────────────────────────────
        conflict = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reservations
            WHERE
                restaurant_id = $1
                AND table_id = $2
                AND reservation_date = $3
                AND status NOT IN ('cancelled', 'no_show')
                AND start_time < $5 AND end_time > $4
            """,
            restaurant_id, table_id, reservation_date, start_time, end_time,
        )
        if conflict > 0:
            raise ValueError(
                f"Table {table_row['table_number']} was just booked for that time. "
                "Please choose another slot."
            )

        # ── Generate unique confirmation code ──────────────────────────────
        for _ in range(10):
            code = _make_confirmation_code()
            if not await conn.fetchval(
                "SELECT 1 FROM reservations WHERE confirmation_code = $1", code
            ):
                break
        else:
            raise RuntimeError("Failed to generate unique confirmation code.")

        # ── Insert reservation ─────────────────────────────────────────────
        reservation = await conn.fetchrow(
            """
            INSERT INTO reservations (
                restaurant_id, customer_id, table_id,
                reservation_date, start_time, end_time,
                party_size, special_requests, confirmation_code, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'confirmed')
            RETURNING *
            """,
            restaurant_id, customer_id, table_id,
            reservation_date, start_time, end_time,
            party_size, special_requests, code,
        )

        # ── Increment customer visit count ─────────────────────────────────
        await conn.execute(
            "UPDATE customers SET visit_count = visit_count + 1 "
            "WHERE id = $1 AND restaurant_id = $2",
            customer_id, restaurant_id,
        )

        return dict(reservation)


async def get_reservation_by_code(
    restaurant_id: UUID,
    code: str,
) -> Optional[Dict[str, Any]]:
    """Always scoped to restaurant_id — confirmation codes cannot leak across tenants."""
    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT r.*, c.full_name, c.email, c.phone,
                   t.table_number, t.location, t.capacity
            FROM reservations r
            JOIN customers c ON c.id = r.customer_id AND c.restaurant_id = $1
            JOIN tables t    ON t.id = r.table_id    AND t.restaurant_id = $1
            WHERE r.confirmation_code = $2 AND r.restaurant_id = $1
            """,
            restaurant_id, code.upper(),
        )
        return dict(row) if row else None


async def cancel_reservation(
    restaurant_id: UUID,
    code: str,
    customer_email: str,
) -> Dict[str, Any]:
    """Cancel — verifies both restaurant scope and customer email ownership."""
    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE reservations r
            SET status = 'cancelled'
            FROM customers c
            WHERE r.customer_id = c.id
              AND r.restaurant_id = $1
              AND c.restaurant_id = $1
              AND r.confirmation_code = $2
              AND c.email = $3
              AND r.status = 'confirmed'
            RETURNING r.*
            """,
            restaurant_id, code.upper(), customer_email,
        )
        if not row:
            raise ValueError(
                "Cannot cancel: verify confirmation code and email address."
            )
        return dict(row)


async def modify_reservation(
    restaurant_id: UUID,
    code: str,
    customer_email: str,
    new_date: Optional[date] = None,
    new_time: Optional[time] = None,
    new_party_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Modify via cancel + rebook — all within the same restaurant scope."""
    existing = await get_reservation_by_code(restaurant_id, code)
    if not existing:
        raise ValueError(f"Reservation '{code}' not found.")
    if existing.get("email") != customer_email:
        raise ValueError("Email does not match the reservation holder.")
    if existing["status"] != "confirmed":
        raise ValueError(f"Only confirmed reservations can be modified.")

    target_date       = new_date       or existing["reservation_date"]
    target_time       = new_time       or existing["start_time"]
    target_party_size = new_party_size or existing["party_size"]

    tables = await check_availability(
        restaurant_id, target_date, target_time, target_party_size
    )
    if not tables:
        raise ValueError(
            f"No tables available on {target_date} at {target_time} "
            f"for {target_party_size} guests."
        )

    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        await conn.execute(
            "UPDATE reservations SET status='cancelled' "
            "WHERE confirmation_code=$1 AND restaurant_id=$2",
            code.upper(), restaurant_id,
        )
    return await book_table(
        restaurant_id=restaurant_id,
        customer_id=UUID(str(existing["customer_id"])),
        table_id=UUID(str(tables[0]["id"])),
        reservation_date=target_date,
        start_time=target_time,
        party_size=target_party_size,
        special_requests=existing.get("special_requests"),
    )


# =============================================================================
# Session Management (Tenant-Scoped)
# =============================================================================

async def get_or_create_session(restaurant_id: str, session_token: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ── THE MISSING FIX: Set the RLS context for this specific connection ──
        await conn.execute(
            "SELECT set_config('app.current_restaurant_id', $1, false)",
            str(restaurant_id)
        )

        # ── Now the upsert will safely return the row ──
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_sessions (id, restaurant_id, session_token, is_active, messages, context)
            VALUES (gen_random_uuid(), $1, $2, TRUE, '[]'::jsonb, '{}'::jsonb)
            ON CONFLICT (session_token) 
            DO UPDATE SET last_active_at = NOW()
            RETURNING *
            """,
            restaurant_id, session_token
        )

        if not row:
            raise RuntimeError("Session row vanished mid-upsert; investigate schema.")

        return dict(row)


async def update_session(
    restaurant_id: UUID,
    session_token: str,
    messages: List[Dict],
    context: Dict,
    customer_id: Optional[UUID] = None,
    reservation_id: Optional[UUID] = None,
) -> None:
    pool = await get_pool()
    async with tenant_transaction(pool, restaurant_id) as conn:
        await conn.execute(
            """
            UPDATE conversation_sessions
            SET messages        = $3::jsonb,
                context         = $4::jsonb,
                customer_id     = COALESCE($5, customer_id),
                reservation_id  = COALESCE($6, reservation_id),
                last_active_at  = NOW()
            WHERE session_token = $1 AND restaurant_id = $2
            """,
            session_token,
            restaurant_id,
            json_module.dumps(messages, default=str),
            json_module.dumps(context, default=str),
            customer_id,
            reservation_id,
        )
