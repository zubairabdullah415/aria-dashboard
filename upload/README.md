# LiftUp SaaS — Multi-Tenant AI Reservation Platform
### v2.0 Architecture Guide

---

## Multi-Tenancy Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         TENANT ISOLATION LAYERS                      │
│                                                                      │
│  1. APPLICATION  ─── restaurant_id in every SQL WHERE clause         │
│  2. DATABASE     ─── RLS policies on all tenant-scoped tables        │
│  3. SESSION VAR  ─── SET LOCAL app.current_restaurant_id per query   │
│  4. API KEY      ─── X-Api-Key header → validates & loads tenant     │
│  5. JWT          ─── Owner auth token encodes restaurant_id          │
└──────────────────────────────────────────────────────────────────────┘
```

### Data Isolation Guarantee

A customer/reservation from Restaurant A **cannot** be accessed by Restaurant B because:

1. Every query in `database.py` takes `restaurant_id` as a parameter
2. `TenantConn` sets `app.current_restaurant_id` before any query runs
3. RLS policies (`WHERE restaurant_id = current_restaurant_id()`) block any query
   that doesn't match — even if the application code has a bug
4. The `book_table()` function verifies `AND restaurant_id = $2` on the table lock
   to prevent a crafted `table_id` from crossing tenants

---

## File Structure

```
liftup_saas/
├── schema.sql        ← Full multi-tenant schema with RLS, billing triggers
├── main.py           ← FastAPI app + tenant middleware + quota enforcement
├── agent.py          ← Dynamic prompt builder + tenant-scoped tool executor
├── database.py       ← TenantConn context manager + all DB operations
├── onboarding.py     ← Owner registration, Twilio provisioning, widget gen
├── notifications.py  ← Dynamic Twilio routing + quota warning emails
├── config.py         ← Pydantic settings
└── requirements.txt
```

---

## Request Flow (Widget → Booking)

```
Browser Widget
  │
  ├─ POST /api/widget/chat
  │   Headers: X-Api-Key: <restaurant_api_key>
  │            X-Session-Token: <session_token>
  │   Body:    { "message": "Table for 4 tomorrow at 7?" }
  │
  ├─ [TenantAuthMiddleware]
  │   • Looks up api_key in restaurants table (indexed)
  │   • Loads restaurant + subscription → request.state.tenant
  │   • Rejects with 401 if not found
  │
  ├─ [enforce_quota Dependency]
  │   • Reads quota from request.state.tenant (no extra DB query)
  │   • Blocks with 402 + upgrade URL if exhausted
  │   • Fires background quota warning tasks at 80%/95%
  │
  ├─ [widget_chat Handler]
  │   • Builds AgentContext with restaurant profile
  │   • Calls run_agent(message, ctx)
  │
  ├─ [agent.py — run_agent]
  │   • build_system_prompt(ctx) — injects restaurant name, hours, policies
  │   • Calls Claude API with dynamic tools
  │   • Tool calls → execute_tool() with ctx.restaurant_id injected
  │   • DB calls → TenantConn(pool, restaurant_id)
  │
  └─ Response: { reply, session_token, booking_complete, confirmation_code }
```

---

## Billing State Machine

```
New Restaurant
      │
      ▼
[trial_active] ──── 100 reservations used ───→ [trial_exhausted]
      │                                               │
      │ owner upgrades ($299/mo)                      │ owner upgrades
      ▼                                               ▼
   [active] ─────── cycle_reservation_limit ──→ [quota_exceeded]
      │              reached (default: 1000)          │
      │                                               │ recharge/new cycle
      │ billing_cycle_end reached                     ▼
      └──────────── cycle reset ──────────────→ [active]
```

### Quota Warnings Schedule

| Usage | Action |
|-------|--------|
| 80%   | Email owner: "heads up" warning |
| 95%   | Email owner: "action required" urgent warning |
| 100%  | Block all new bookings + 402 response with upgrade URL |

Warnings are sent once per cycle (tracked by `warned_at_80_pct`, `warned_at_95_pct` columns).

---

## Onboarding Flow

```
POST /api/onboarding/register        Step 1: Account + free trial
PUT  /api/onboarding/profile         Step 2: Business details + AI config
POST /api/onboarding/tables          Step 3: Seating inventory
POST /api/onboarding/phone/search    Step 4a: Find available Twilio numbers
POST /api/onboarding/phone/provision Step 4b: Purchase + assign number
GET  /api/onboarding/widget          Step 5: Get embed code
POST /api/onboarding/complete        Step 6: Go live
```

### Widget Embed Output (Step 5)

```html
<!-- LiftUp AI Reservation Widget — Bella Roma Trattoria -->
<script
  src="https://api.liftupai.com/widget/v2/reservation-widget.js"
  data-api-key="a3f8bc91d4e20f..."
  data-restaurant="Bella Roma Trattoria"
  data-lang="en"
  data-integrity="f4a2b3c1d9e0"
  async>
</script>
```

Each restaurant gets a **unique** `data-api-key`. The widget automatically:
- Sends `X-Api-Key` header with every request
- Stores session token in `sessionStorage`
- Handles the full booking conversation

---

## Dynamic System Prompt Example

For a restaurant named "Bella Roma" with outdoor seating and custom hours,
the generated system prompt begins:

```
You are **Maria**, the AI reservation assistant for **Bella Roma Trattoria**.

## RESTAURANT PROFILE
- Name:     Bella Roma Trattoria
- Cuisine:  Authentic Italian
- Address:  88 Via Roma, San Francisco, CA
- Avg. dining duration: 90 minutes

## OPERATING HOURS
  Monday       11:30 – 22:00
  Tuesday      11:30 – 22:00
  ...
  Sunday       Closed

## SEATING OPTIONS
  🌿 Outdoor     — Garden terrace, heated in winter
  🤫 Quiet Corner — Perfect for date nights
  🚪 Private Room — For parties of 8+, events welcome

## POLICIES
  48-hour cancellation notice required. Dress code: Smart casual.
  ...
```

This prompt is rebuilt on every request from the live database record.
Operators can update their profile in the dashboard and the AI reflects
it immediately — no redeploy required.

---

## Dynamic Twilio Routing

```python
# In notifications.py — send_confirmation_sms()

# 1. Look up this restaurant's dedicated sender number
sender_number = await get_sender_phone(restaurant_id)
# → "+14155552671" (provisioned in Step 4)

# 2. Fall back to platform number if not configured
if not sender_number:
    sender_number = settings.TWILIO_PLATFORM_NUMBER

# 3. SMS is sent FROM the restaurant's number
# → Customer sees a local number they recognize
```

This means each restaurant's customers see SMS from a consistent number
associated with that restaurant, not a generic platform number.

---

## Security Summary

| Threat | Mitigation |
|--------|-----------|
| Cross-tenant data access | RLS policies + restaurant_id in every WHERE clause |
| API key brute force | DB unique index + slowapi rate limiting (30/min) |
| restaurant_id injection via Claude | Stripped from all tool inputs in execute_tool() |
| SQL injection in profile update | Allowlisted column names in dynamic SET clause |
| Password exposure | bcrypt with cost factor 12 |
| JWT forgery | HS256 signed with SECRET_KEY (≥32 bytes) |
| Double-booking | SELECT FOR UPDATE NOWAIT + EXCLUDE USING gist |
| Unauthorized reservation cancel | Email + restaurant_id verified in WHERE clause |

---

## Environment Variables (.env)

```bash
DEBUG=false
SECRET_KEY=<32+ random bytes hex>
PLATFORM_URL=https://api.liftupai.com
DATABASE_URL=postgresql://liftup_app:password@db:5432/liftup
ANTHROPIC_API_KEY=sk-ant-...
SENDGRID_API_KEY=SG....
EMAIL_FROM_ADDRESS=noreply@liftupai.com
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PLATFORM_NUMBER=+14155550000
```

---

## Scaling to 500+ Restaurants

- **Connection pool**: `min_size=10, max_size=50` — handles burst traffic
- **Session storage**: Stored in PostgreSQL with expiry index
- **Indexes**: All hot-path queries use composite indexes starting with `restaurant_id`
- **APScheduler**: Runs `fn_reset_billing_cycle()` for each restaurant whose
  `billing_cycle_end` has passed (daily cron job)
- **Horizontal scaling**: All state in PostgreSQL — FastAPI is stateless,
  can run N workers behind a load balancer

---

*LiftUp SaaS v2.0 — Built with FastAPI, asyncpg, PostgreSQL RLS, and Claude AI.*
