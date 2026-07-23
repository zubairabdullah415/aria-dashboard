"""
onboarding.py — Restaurant Owner Onboarding API
=================================================
Complete B2B onboarding flow for new restaurant tenants:

  Step 1  POST /api/onboarding/register
          → Creates restaurant account + free trial subscription
          → Returns owner JWT for subsequent steps

  Step 2  POST /api/onboarding/profile
          → Business details: cuisine, hours, address, policies

  Step 3  POST /api/onboarding/tables
          → Define seating inventory (bulk create)

  Step 4  POST /api/onboarding/phone
          → Search available Twilio numbers by area code
          → Provision and assign the chosen number

  Step 5  GET  /api/onboarding/widget
          → Generates the unique <script> embed tag
          → Returns the API key and embed code

  Step 6  POST /api/onboarding/complete
          → Marks restaurant as 'live'

Owner Auth: JWT-based. The token encodes restaurant_id and is verified
by the owner_required dependency (different from the API key used by widgets).
"""

import logging
import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

import httpx
import bcrypt
import jwt                          # PyJWT
from fastapi import APIRouter, HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, validator

from config import settings
from database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/onboarding", tags=["Owner Onboarding"])


# =============================================================================
# Owner JWT Auth
# =============================================================================

bearer = HTTPBearer(auto_error=False)


def _create_owner_jwt(restaurant_id: UUID, owner_email: str) -> str:
    payload = {
        "sub":   str(restaurant_id),
        "email": owner_email,
        "iat":   datetime.now(tz=timezone.utc),
        "exp":   datetime.now(tz=timezone.utc) + timedelta(hours=24),
        "type":  "owner",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


async def owner_required(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> Dict[str, Any]:
    """
    Dependency that validates the owner JWT and returns the restaurant record.
    Raises 401 on any auth failure.
    """
    if not credentials:
        raise HTTPException(401, "Owner authentication required.")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid authentication token.")

    if payload.get("type") != "owner":
        raise HTTPException(403, "Not an owner token.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM restaurants WHERE id = $1 AND is_active = TRUE",
            UUID(payload["sub"]),
        )
    if not row:
        raise HTTPException(401, "Restaurant account not found or inactive.")
    return dict(row)


# =============================================================================
# Pydantic Models
# =============================================================================

class RegisterRequest(BaseModel):
    owner_name:       str       = Field(..., min_length=2, max_length=150)
    owner_email:      EmailStr
    owner_phone:      Optional[str] = None
    password:         str       = Field(..., min_length=10, max_length=128,
                                        description="Min 10 chars")
    restaurant_name:  str       = Field(..., min_length=2, max_length=200)
    agreed_to_terms:  bool      = Field(..., description="Must be True")

    @validator("agreed_to_terms")
    def must_agree(cls, v):
        if not v:
            raise ValueError("You must agree to the Terms of Service.")
        return v

    @validator("password")
    def strong_password(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        return v


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class ProfileRequest(BaseModel):
    cuisine_type:       Optional[str] = None
    address:            Optional[str] = None
    city:               Optional[str] = None
    country:            Optional[str] = "US"
    timezone:           Optional[str] = "America/New_York"
    operating_hours:    Optional[Dict[str, Any]] = None
    ai_persona_name:    Optional[str] = None
    ai_welcome_message: Optional[str] = None
    custom_policies:    Optional[str] = None
    avg_dining_minutes: Optional[int] = Field(None, ge=30, le=300)


class TableDefinition(BaseModel):
    table_number: str
    capacity:     int = Field(..., ge=1, le=30)
    location:     str = "indoor"
    description:  Optional[str] = None
    is_accessible: bool = False
    has_high_chair: bool = False


class TablesRequest(BaseModel):
    tables: List[TableDefinition] = Field(..., min_items=1, max_items=100)


class PhoneSearchRequest(BaseModel):
    country_code: str  = Field("US", description="ISO country code: US, GB, CA, etc.")
    area_code:    Optional[str] = Field(None, description="3-digit area code, e.g. '415'")
    contains:     Optional[str] = None


class PhoneProvisionRequest(BaseModel):
    phone_number: str  = Field(..., description="E.164 format: +14155552671")
    # OR Twilio SID if the owner chose from the search list
    twilio_sid:   Optional[str] = None


# =============================================================================
# Step 1: Register
# =============================================================================

@router.post("/register", status_code=201)
async def register(body: RegisterRequest):
    """
    Creates a new restaurant account with a free trial subscription.
    Returns an owner JWT for subsequent onboarding steps.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check for duplicate email
        exists = await conn.fetchval(
            "SELECT 1 FROM restaurants WHERE owner_email = $1", body.owner_email
        )
        if exists:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists.",
            )

        # Hash password with bcrypt (cost factor 12)
        pw_hash = bcrypt.hashpw(
            body.password.encode(), bcrypt.gensalt(rounds=12)
        ).decode()

        async with conn.transaction():
            # Create restaurant
            restaurant = await conn.fetchrow(
                """
                INSERT INTO restaurants
                    (owner_name, owner_email, owner_phone,
                     password_hash, restaurant_name)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                body.owner_name, body.owner_email, body.owner_phone,
                pw_hash, body.restaurant_name,
            )

            # Create free trial subscription
            await conn.execute(
                """
                INSERT INTO subscriptions
                    (restaurant_id, tier, status, trial_limit, is_current)
                VALUES ($1, 'trial', 'trial_active', 100, TRUE)
                """,
                restaurant["id"],
            )

    logger.info(f"New tenant registered: {restaurant['id']} ({body.restaurant_name})")

    token = _create_owner_jwt(restaurant["id"], body.owner_email)
    return {
        "message":         "Account created successfully. Welcome to LiftUp!",
        "restaurant_id":   str(restaurant["id"]),
        "api_key":         restaurant["api_key"],   # Keep safe — this is the widget key
        "access_token":    token,
        "token_type":      "bearer",
        "trial_info": {
            "reservations_included": 100,
            "message": "Your free trial includes 100 reservations. No credit card required.",
        },
    }


# =============================================================================
# Owner Auth: Login
# =============================================================================

@router.post("/login")
async def login(body: LoginRequest):
    """Authenticates an owner and returns a fresh JWT."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM restaurants WHERE owner_email = $1 AND is_active = TRUE",
            body.email,
        )

    if not row:
        # Constant-time response to prevent email enumeration
        bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt()))
        raise HTTPException(401, "Invalid email or password.")

    if not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(401, "Invalid email or password.")

    token = _create_owner_jwt(row["id"], row["owner_email"])
    return {
        "access_token":  token,
        "token_type":    "bearer",
        "restaurant_id": str(row["id"]),
        "restaurant_name": row["restaurant_name"],
        "onboarding_step": row["onboarding_step"],
    }


# =============================================================================
# Step 2: Business Profile
# =============================================================================

@router.put("/profile")
async def update_profile(
    body: ProfileRequest,
    owner: Dict = Depends(owner_required),
):
    """Updates the restaurant's business profile and AI configuration."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if not updates:
            raise HTTPException(400, "No fields to update.")

        # Build dynamic SET clause safely
        set_parts = []
        params    = [owner["id"]]
        for i, (col, val) in enumerate(updates.items(), start=2):
            # Validate column name against allowlist (SQL injection prevention)
            allowed_cols = {
                "cuisine_type", "address", "city", "country", "timezone",
                "operating_hours", "ai_persona_name", "ai_welcome_message",
                "custom_policies", "avg_dining_minutes",
            }
            if col not in allowed_cols:
                raise HTTPException(400, f"Unknown field: {col}")
            set_parts.append(f"{col} = ${i}")
            params.append(json.dumps(val) if isinstance(val, dict) else val)

        await conn.execute(
            f"UPDATE restaurants SET {', '.join(set_parts)}, "
            f"onboarding_step = 'profile_complete' "
            f"WHERE id = $1",
            *params,
        )

    return {"message": "Profile updated.", "next_step": "Add your seating tables."}


# =============================================================================
# Step 3: Table Inventory
# =============================================================================

@router.post("/tables", status_code=201)
async def create_tables(
    body: TablesRequest,
    owner: Dict = Depends(owner_required),
):
    """Bulk-creates table inventory for the restaurant."""
    pool = await get_pool()
    restaurant_id = owner["id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = 0
            for t in body.tables:
                # Upsert: update description if table_number already exists
                await conn.execute(
                    """
                    INSERT INTO tables
                        (restaurant_id, table_number, capacity, location,
                         description, is_accessible, has_high_chair)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (restaurant_id, table_number)
                    DO UPDATE SET
                        capacity      = EXCLUDED.capacity,
                        location      = EXCLUDED.location,
                        description   = EXCLUDED.description,
                        is_accessible = EXCLUDED.is_accessible,
                        has_high_chair = EXCLUDED.has_high_chair
                    """,
                    restaurant_id, t.table_number, t.capacity,
                    t.location, t.description, t.is_accessible, t.has_high_chair,
                )
                inserted += 1

    return {
        "message":   f"{inserted} tables saved.",
        "next_step": "Provision a dedicated phone number for SMS notifications.",
    }


# =============================================================================
# Step 4a: Search Available Twilio Numbers
# =============================================================================

@router.post("/phone/search")
async def search_phone_numbers(
    body: PhoneSearchRequest,
    owner: Dict = Depends(owner_required),
):
    """
    Proxies a search to the Twilio Available Phone Numbers API.
    Returns a list of available numbers the owner can choose from.
    """
    country = body.country_code.upper()
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/AvailablePhoneNumbers/"
        f"{country}/Local.json"
    )
    params: Dict[str, str] = {"Capabilities.SMS": "true", "PageSize": "10"}
    if body.area_code:
        params["AreaCode"] = body.area_code
    if body.contains:
        params["Contains"] = body.contains

    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            params=params,
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"Twilio search failed: {resp.text[:200]}")

    numbers = resp.json().get("available_phone_numbers", [])
    return {
        "available_numbers": [
            {
                "phone_number":   n["phone_number"],
                "friendly_name":  n["friendly_name"],
                "locality":       n.get("locality", ""),
                "region":         n.get("region", ""),
            }
            for n in numbers
        ],
        "count": len(numbers),
    }


# =============================================================================
# Step 4b: Provision / Assign Phone Number
# =============================================================================

@router.post("/phone/provision", status_code=201)
async def provision_phone_number(
    body: PhoneProvisionRequest,
    owner: Dict = Depends(owner_required),
):
    """
    Purchases the selected phone number from Twilio and assigns it to the
    restaurant. All future SMS notifications will be sent FROM this number.
    """
    restaurant_id = owner["id"]
    pool = await get_pool()

    # Check if already has a number
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT phone_number FROM restaurant_phone_numbers "
            "WHERE restaurant_id = $1 AND is_active = TRUE",
            restaurant_id,
        )

    if existing:
        raise HTTPException(
            409,
            f"Restaurant already has number {existing}. Release it first.",
        )

    # Purchase from Twilio
    purchase_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers.json"
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            purchase_url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={
                "PhoneNumber":   body.phone_number,
                "FriendlyName":  f"LiftUp — {owner['restaurant_name']}",
                "SmsUrl":        f"{settings.PLATFORM_URL}/webhooks/sms",
            },
        )

    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Could not provision number: {resp.text[:300]}")

    purchased = resp.json()
    twilio_sid = purchased.get("sid")

    # Save to DB
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO restaurant_phone_numbers
                (restaurant_id, phone_number, twilio_sid, friendly_name)
            VALUES ($1, $2, $3, $4)
            """,
            restaurant_id,
            body.phone_number,
            twilio_sid,
            f"LiftUp — {owner['restaurant_name']}",
        )
        await conn.execute(
            "UPDATE restaurants SET onboarding_step = 'phone_provisioned' "
            "WHERE id = $1",
            restaurant_id,
        )

    logger.info(
        f"Phone {body.phone_number} provisioned for restaurant {restaurant_id} "
        f"(Twilio SID: {twilio_sid})"
    )
    return {
        "message":      f"Number {body.phone_number} provisioned successfully.",
        "phone_number": body.phone_number,
        "twilio_sid":   twilio_sid,
        "next_step":    "Generate your widget embed code.",
    }


# =============================================================================
# Step 5: Generate Widget Embed Code
# =============================================================================

@router.get("/widget")
async def get_widget_code(owner: Dict = Depends(owner_required)):
    """
    Returns the unique <script> embed tag for this restaurant.
    The owner pastes this into their website's HTML.

    The embed code contains:
      - The public API key (safe to expose — scoped to this restaurant only)
      - The LiftUp widget URL
      - Basic restaurant branding config
    """
    restaurant_id = owner["id"]
    api_key       = owner["api_key"]

    # Build signed integrity hash so the widget can verify it hasn't been tampered
    integrity_data = f"{restaurant_id}:{api_key}:{settings.SECRET_KEY}"
    integrity_hash = hashlib.sha256(integrity_data.encode()).hexdigest()[:16]

    embed_code = f"""<!-- LiftUp AI Reservation Widget — {owner['restaurant_name']} -->
<script
  src="{settings.PLATFORM_URL}/widget/v2/reservation-widget.js"
  data-api-key="{api_key}"
  data-restaurant="{owner['restaurant_name']}"
  data-lang="en"
  data-integrity="{integrity_hash}"
  async>
</script>
<!-- End LiftUp Widget -->"""

    # Update onboarding step
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE restaurants SET onboarding_step = 'widget_generated' WHERE id = $1",
            restaurant_id,
        )

    return {
        "api_key":       api_key,
        "embed_code":    embed_code,
        "widget_url":    f"{settings.PLATFORM_URL}/widget/v2/reservation-widget.js",
        "dashboard_url": f"{settings.PLATFORM_URL}/dashboard/{restaurant_id}",
        "next_step":     "Paste the embed code before </body> in your website.",
    }


# =============================================================================
# Step 6: Mark as Live
# =============================================================================

@router.post("/complete")
async def complete_onboarding(owner: Dict = Depends(owner_required)):
    """Final step — marks the restaurant as live on the platform."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(t.id) AS table_count,
                p.phone_number
            FROM restaurants r
            LEFT JOIN tables                 t ON t.restaurant_id = r.id
            LEFT JOIN restaurant_phone_numbers p ON p.restaurant_id = r.id AND p.is_active = TRUE
            WHERE r.id = $1
            GROUP BY p.phone_number
            """,
            owner["id"],
        )

        if not row or row["table_count"] == 0:
            raise HTTPException(
                400,
                "Please add at least one table before going live.",
            )

        await conn.execute(
            "UPDATE restaurants SET onboarding_step = 'live' WHERE id = $1",
            owner["id"],
        )

    logger.info(f"Restaurant {owner['id']} is now LIVE on LiftUp.")
    return {
        "message":       "🎉 Congratulations! Your reservation widget is now live.",
        "restaurant_id": str(owner["id"]),
        "has_phone":     bool(row["phone_number"]),
        "table_count":   row["table_count"],
    }


# =============================================================================
# Billing: Upgrade Plan
# =============================================================================

@router.post("/billing/upgrade")
async def upgrade_plan(
    tier: str,
    stripe_customer_id: Optional[str] = None,
    owner: Dict = Depends(owner_required),
):
    """
    Upgrades the restaurant from trial (or exhausted) to a paid plan.
    In production, Stripe webhook would trigger this — this endpoint is for
    direct/manual upgrades and testing.
    """
    allowed_tiers = {"starter", "growth", "enterprise"}
    if tier not in allowed_tiers:
        raise HTTPException(400, f"Invalid tier. Choose from: {allowed_tiers}")

    from database import upgrade_to_paid
    subscription = await upgrade_to_paid(
        restaurant_id=owner["id"],
        tier=tier,
        stripe_customer_id=stripe_customer_id,
    )
    return {
        "message":    f"Upgraded to {tier} plan successfully.",
        "new_plan":   tier,
        "cycle_end":  str(subscription.get("billing_cycle_end", "")),
        "quota":      subscription.get("cycle_reservation_limit"),
    }


# =============================================================================
# Owner Dashboard: Current Status
# =============================================================================

@router.get("/status")
async def get_status(owner: Dict = Depends(owner_required)):
    """Returns the restaurant's current subscription, quota, and setup status."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            """
            SELECT s.*, p.phone_number,
                   (SELECT COUNT(*) FROM tables WHERE restaurant_id = $1) AS table_count
            FROM subscriptions s
            LEFT JOIN restaurant_phone_numbers p
              ON p.restaurant_id = s.restaurant_id AND p.is_active = TRUE
            WHERE s.restaurant_id = $1 AND s.is_current = TRUE
            """,
            owner["id"],
        )

    if not sub:
        raise HTTPException(500, "Subscription record not found.")

    s = dict(sub)
    if s["tier"] == "trial":
        used     = s["trial_reservations_used"]
        limit    = s["trial_limit"]
        remaining = limit - used
    else:
        used      = s["cycle_reservations_used"]
        limit     = s["cycle_reservation_limit"]
        remaining = limit - used

    return {
        "restaurant_id":    str(owner["id"]),
        "restaurant_name":  owner["restaurant_name"],
        "onboarding_step":  owner["onboarding_step"],
        "subscription": {
            "tier":       s["tier"],
            "status":     s["status"],
            "used":       used,
            "limit":      limit,
            "remaining":  remaining,
            "pct_used":   round(used / limit * 100, 1) if limit > 0 else 0,
            "cycle_end":  str(s.get("billing_cycle_end") or "N/A"),
        },
        "setup": {
            "tables":       s["table_count"],
            "phone_number": s.get("phone_number"),
            "has_phone":    bool(s.get("phone_number")),
        },
    }
