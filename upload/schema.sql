-- =============================================================================
-- LiftUp SaaS Platform — Multi-Tenant Database Schema
-- PostgreSQL 15+ | Version 2.0
-- =============================================================================
-- Design Principles:
--   1. EVERY tenant-scoped table has restaurant_id with ON DELETE CASCADE
--   2. Row-Level Security (RLS) policies enforce tenant isolation at DB level
--   3. Billing tracked in subscriptions + billing_cycles tables
--   4. Twilio numbers are per-restaurant, not global
--   5. Partial indexes on (restaurant_id, ...) for query performance
-- =============================================================================

-- Required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gist";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- For secure API key generation

-- =============================================================================
-- SECTION 1: PLATFORM ENUMERATIONS
-- =============================================================================

CREATE TYPE subscription_tier AS ENUM (
    'trial',       -- Free: 0–100 reservations lifetime
    'starter',     -- $299/mo: up to 1,000 reservations per 30-day cycle
    'growth',      -- $699/mo: up to 5,000 reservations per 30-day cycle
    'enterprise'   -- Custom pricing: unlimited
);

CREATE TYPE subscription_status AS ENUM (
    'trial_active',     -- Within free trial quota
    'trial_exhausted',  -- Used all 100 trial reservations, needs upgrade
    'active',           -- Paid plan, within quota
    'quota_exceeded',   -- Hit monthly cap, service suspended until recharge
    'payment_failed',   -- Stripe webhook reported failure
    'cancelled',        -- Owner cancelled subscription
    'suspended'         -- Admin-suspended (abuse, fraud, etc.)
);

CREATE TYPE seating_preference AS ENUM (
    'indoor', 'outdoor', 'bar', 'quiet_corner',
    'window', 'private_room', 'no_preference'
);

CREATE TYPE table_status AS ENUM (
    'available', 'reserved', 'occupied', 'maintenance'
);

CREATE TYPE reservation_status AS ENUM (
    'pending', 'confirmed', 'modified',
    'cancelled', 'completed', 'no_show'
);

CREATE TYPE notification_channel AS ENUM ('email', 'sms');
CREATE TYPE notification_status  AS ENUM ('pending', 'sent', 'failed', 'retrying');

CREATE TYPE onboarding_step AS ENUM (
    'registered',         -- Account created
    'profile_complete',   -- Business details filled
    'phone_provisioned',  -- Twilio number assigned
    'widget_generated',   -- Script tag generated
    'live'                -- First reservation received
);

-- =============================================================================
-- SECTION 2: CORE PLATFORM TABLES
-- =============================================================================

-- ---------------------------------------------------------------------------
-- RESTAURANTS (Tenants)
-- The root table. Every other table links here via restaurant_id.
-- ---------------------------------------------------------------------------
CREATE TABLE restaurants (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Auth & Identity
    owner_email         VARCHAR(255) UNIQUE NOT NULL,
    owner_name          VARCHAR(150) NOT NULL,
    owner_phone         VARCHAR(30),
    password_hash       VARCHAR(255) NOT NULL,  -- bcrypt hash

    -- API Access
    -- api_key is the secret passed in every widget request.
    -- Generated via pgcrypto — never stored in plaintext elsewhere.
    api_key             VARCHAR(64) UNIQUE NOT NULL
                            DEFAULT encode(gen_random_bytes(32), 'hex'),
    api_key_created_at  TIMESTAMPTZ DEFAULT NOW(),

    -- Business Profile (used to build dynamic AI system prompt)
    restaurant_name     VARCHAR(200) NOT NULL,
    cuisine_type        VARCHAR(100),
    address             TEXT,
    city                VARCHAR(100),
    country             VARCHAR(100) DEFAULT 'US',
    timezone            VARCHAR(50)  DEFAULT 'America/New_York',

    -- Operating Hours (JSONB for flexibility: per-day overrides)
    -- Format: {"monday": {"open": "11:30", "close": "22:00", "closed": false}, ...}
    operating_hours     JSONB NOT NULL DEFAULT '{
        "monday":    {"open": "11:30", "close": "22:00", "closed": false},
        "tuesday":   {"open": "11:30", "close": "22:00", "closed": false},
        "wednesday": {"open": "11:30", "close": "22:00", "closed": false},
        "thursday":  {"open": "11:30", "close": "22:00", "closed": false},
        "friday":    {"open": "11:30", "close": "23:00", "closed": false},
        "saturday":  {"open": "10:00", "close": "23:00", "closed": false},
        "sunday":    {"open": "10:00", "close": "21:00", "closed": false}
    }'::jsonb,

    -- AI Agent Configuration
    -- Injected verbatim into the system prompt
    ai_persona_name     VARCHAR(50)  DEFAULT 'Aria',
    ai_welcome_message  TEXT,        -- Custom greeting
    custom_policies     TEXT,        -- Cancellation policy, dress code, etc.
    avg_dining_minutes  INTEGER      DEFAULT 90,

    -- Seating inventory summary (derived — refreshed by trigger)
    total_tables        INTEGER DEFAULT 0,
    total_capacity      INTEGER DEFAULT 0,

    -- Onboarding State
    onboarding_step     onboarding_step DEFAULT 'registered',
    is_active           BOOLEAN DEFAULT TRUE,

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Fast lookups by API key (hot path — every widget request)
CREATE UNIQUE INDEX idx_restaurants_api_key  ON restaurants(api_key);
CREATE INDEX         idx_restaurants_email   ON restaurants(owner_email);
CREATE INDEX         idx_restaurants_active  ON restaurants(is_active) WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- SUBSCRIPTIONS
-- One active subscription per restaurant at any time.
-- Historical records are preserved (never deleted, only superseded).
-- ---------------------------------------------------------------------------
CREATE TABLE subscriptions (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id           UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,

    tier                    subscription_tier   NOT NULL DEFAULT 'trial',
    status                  subscription_status NOT NULL DEFAULT 'trial_active',

    -- Trial tracking
    trial_reservations_used INTEGER NOT NULL DEFAULT 0,
    trial_limit             INTEGER NOT NULL DEFAULT 100,

    -- Billing cycle tracking
    billing_cycle_start     TIMESTAMPTZ,           -- NULL during trial
    billing_cycle_end       TIMESTAMPTZ,           -- NULL during trial
    cycle_reservations_used INTEGER NOT NULL DEFAULT 0,
    cycle_reservation_limit INTEGER NOT NULL DEFAULT 1000,

    -- Pricing
    monthly_price_cents     INTEGER DEFAULT 0,     -- 29900 = $299.00
    currency                VARCHAR(3) DEFAULT 'USD',

    -- Stripe integration
    stripe_customer_id      VARCHAR(100),
    stripe_subscription_id  VARCHAR(100),

    -- Quota warning flags (reset each cycle)
    warned_at_80_pct        BOOLEAN DEFAULT FALSE,
    warned_at_95_pct        BOOLEAN DEFAULT FALSE,
    owner_notified_exhausted BOOLEAN DEFAULT FALSE,

    -- Meta
    is_current              BOOLEAN DEFAULT TRUE,   -- Only one TRUE per restaurant
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Enforce only one current subscription per restaurant
CREATE UNIQUE INDEX idx_subscriptions_current
    ON subscriptions(restaurant_id)
    WHERE is_current = TRUE;

CREATE INDEX idx_subscriptions_restaurant ON subscriptions(restaurant_id);
CREATE INDEX idx_subscriptions_status     ON subscriptions(status);

-- ---------------------------------------------------------------------------
-- TWILIO PHONE NUMBERS
-- Each restaurant has its own dedicated sender number.
-- ---------------------------------------------------------------------------
CREATE TABLE restaurant_phone_numbers (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id       UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,

    phone_number        VARCHAR(30) NOT NULL,   -- E.164: +14155552671
    twilio_sid          VARCHAR(64),            -- Twilio PhoneNumber SID: PNxxx
    friendly_name       VARCHAR(100),

    is_active           BOOLEAN DEFAULT TRUE,
    provisioned_at      TIMESTAMPTZ DEFAULT NOW(),
    released_at         TIMESTAMPTZ             -- Set when number is released back
);

CREATE UNIQUE INDEX idx_phone_restaurant ON restaurant_phone_numbers(restaurant_id)
    WHERE is_active = TRUE;  -- One active number per restaurant

-- =============================================================================
-- SECTION 3: TENANT-SCOPED TABLES
-- ALL tables below require restaurant_id on every query.
-- RLS policies are defined in Section 6.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- CUSTOMERS (Tenant-scoped)
-- A customer is always scoped to a restaurant — no cross-tenant profiles.
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,

    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(30),
    dietary_notes   TEXT,
    allergy_notes   TEXT,
    vip_status      BOOLEAN DEFAULT FALSE,
    visit_count     INTEGER DEFAULT 0,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Composite unique: same email can exist across different restaurants
    CONSTRAINT uq_customer_email_per_restaurant
        UNIQUE (restaurant_id, email) DEFERRABLE INITIALLY IMMEDIATE
);

-- Composite indexes — ALWAYS filter by restaurant_id first
CREATE INDEX idx_customers_restaurant_email
    ON customers(restaurant_id, email);
CREATE INDEX idx_customers_restaurant_phone
    ON customers(restaurant_id, phone);
CREATE INDEX idx_customers_restaurant_name
    ON customers USING gin(restaurant_id, full_name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- TABLES (Tenant-scoped)
-- Physical tables in each restaurant.
-- ---------------------------------------------------------------------------
CREATE TABLE tables (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,

    table_number    VARCHAR(10) NOT NULL,
    capacity        INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 30),
    location        seating_preference NOT NULL DEFAULT 'indoor',
    status          table_status NOT NULL DEFAULT 'available',
    has_high_chair  BOOLEAN DEFAULT FALSE,
    is_accessible   BOOLEAN DEFAULT FALSE,
    description     TEXT,
    floor_number    INTEGER DEFAULT 1,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Table numbers must be unique within a restaurant, not globally
    CONSTRAINT uq_table_number_per_restaurant
        UNIQUE (restaurant_id, table_number)
);

CREATE INDEX idx_tables_restaurant_location
    ON tables(restaurant_id, location);
CREATE INDEX idx_tables_restaurant_capacity
    ON tables(restaurant_id, capacity);
CREATE INDEX idx_tables_restaurant_status
    ON tables(restaurant_id, status);

-- ---------------------------------------------------------------------------
-- RESERVATIONS (Tenant-scoped)
-- The exclusion constraint is now scoped to (restaurant_id + table_id).
-- ---------------------------------------------------------------------------
CREATE TABLE reservations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id       UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    customer_id         UUID NOT NULL REFERENCES customers(id)   ON DELETE RESTRICT,
    table_id            UUID NOT NULL REFERENCES tables(id)       ON DELETE RESTRICT,

    reservation_date    DATE    NOT NULL,
    start_time          TIME    NOT NULL,
    end_time            TIME    NOT NULL,
    party_size          INTEGER NOT NULL CHECK (party_size >= 1),
    status              reservation_status NOT NULL DEFAULT 'pending',

    special_requests    TEXT,
    internal_notes      TEXT,
    confirmation_code   VARCHAR(16) NOT NULL,
    source              VARCHAR(50) DEFAULT 'ai_agent',

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    -- Prevent double-booking at table level (scoped to restaurant implicitly
    -- because table_id already belongs to exactly one restaurant)
    CONSTRAINT no_table_time_overlap EXCLUDE USING gist (
        table_id WITH =,
        tsrange(
            (reservation_date + start_time)::timestamp,
            (reservation_date + end_time)::timestamp,
            '[)'
        ) WITH &&
    ) WHERE (status NOT IN ('cancelled', 'no_show')),

    -- Confirmation codes are globally unique (customers may share them verbally)
    CONSTRAINT uq_confirmation_code UNIQUE (confirmation_code)
);

-- CRITICAL: restaurant_id is the first column in every composite index
CREATE INDEX idx_res_restaurant_date
    ON reservations(restaurant_id, reservation_date);
CREATE INDEX idx_res_restaurant_customer
    ON reservations(restaurant_id, customer_id);
CREATE INDEX idx_res_restaurant_status
    ON reservations(restaurant_id, status);
CREATE INDEX idx_res_confirmation_code
    ON reservations(confirmation_code);

-- ---------------------------------------------------------------------------
-- CONVERSATION SESSIONS (Tenant-scoped)
-- ---------------------------------------------------------------------------
CREATE TABLE conversation_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,

    session_token   VARCHAR(64) UNIQUE NOT NULL,
    customer_id     UUID REFERENCES customers(id)    ON DELETE SET NULL,
    reservation_id  UUID REFERENCES reservations(id) ON DELETE SET NULL,
    messages        JSONB NOT NULL DEFAULT '[]',
    context         JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN DEFAULT TRUE,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '2 hours')
);

CREATE UNIQUE INDEX idx_sessions_token
    ON conversation_sessions(session_token);
CREATE INDEX idx_sessions_restaurant_active
    ON conversation_sessions(restaurant_id, is_active, expires_at);

-- ---------------------------------------------------------------------------
-- NOTIFICATION LOG (Tenant-scoped)
-- ---------------------------------------------------------------------------
CREATE TABLE notification_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    reservation_id  UUID NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,

    channel         notification_channel NOT NULL,
    recipient       VARCHAR(255) NOT NULL,
    template_name   VARCHAR(100),
    status          notification_status DEFAULT 'pending',
    provider_id     VARCHAR(255),
    error_message   TEXT,
    attempt_count   INTEGER DEFAULT 0,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_notif_restaurant ON notification_log(restaurant_id);
CREATE INDEX idx_notif_reservation ON notification_log(reservation_id);

-- =============================================================================
-- SECTION 4: BILLING AUDIT TABLES
-- =============================================================================

-- ---------------------------------------------------------------------------
-- BILLING EVENTS
-- Immutable audit log of every quota-affecting event.
-- Used for dispute resolution and analytics.
-- ---------------------------------------------------------------------------
CREATE TABLE billing_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id   UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    subscription_id UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,

    event_type      VARCHAR(50) NOT NULL,
    -- Values: 'reservation_created', 'reservation_cancelled', 'trial_exhausted',
    --         'quota_exceeded', 'cycle_reset', 'plan_upgraded', 'warning_sent'

    reservation_id  UUID REFERENCES reservations(id) ON DELETE SET NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_billing_restaurant ON billing_events(restaurant_id, created_at DESC);

-- =============================================================================
-- SECTION 5: FUNCTIONS & TRIGGERS
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Auto-update timestamps trigger
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_restaurants_ts
    BEFORE UPDATE ON restaurants FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_tables_ts
    BEFORE UPDATE ON tables FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_customers_ts
    BEFORE UPDATE ON customers FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_reservations_ts
    BEFORE UPDATE ON reservations FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_subscriptions_ts
    BEFORE UPDATE ON subscriptions FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();

-- ---------------------------------------------------------------------------
-- Refresh restaurant table/capacity summary when tables change
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_refresh_restaurant_capacity()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE restaurants
    SET
        total_tables   = (SELECT COUNT(*)    FROM tables WHERE restaurant_id = NEW.restaurant_id),
        total_capacity = (SELECT COALESCE(SUM(capacity), 0) FROM tables WHERE restaurant_id = NEW.restaurant_id)
    WHERE id = NEW.restaurant_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tables_capacity_sync
    AFTER INSERT OR UPDATE OR DELETE ON tables
    FOR EACH ROW EXECUTE FUNCTION fn_refresh_restaurant_capacity();

-- ---------------------------------------------------------------------------
-- Increment quota counters when a reservation is CONFIRMED
-- This is the authoritative billing counter — application logic mirrors it
-- but this trigger is the source of truth.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_track_reservation_quota()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_sub subscriptions%ROWTYPE;
BEGIN
    -- Only count transitions TO 'confirmed' status
    IF (TG_OP = 'INSERT' AND NEW.status = 'confirmed')
    OR (TG_OP = 'UPDATE' AND OLD.status != 'confirmed' AND NEW.status = 'confirmed')
    THEN
        SELECT * INTO v_sub
        FROM subscriptions
        WHERE restaurant_id = NEW.restaurant_id AND is_current = TRUE
        FOR UPDATE;  -- Lock the subscription row during update

        IF v_sub.tier = 'trial' THEN
            UPDATE subscriptions
            SET trial_reservations_used = trial_reservations_used + 1
            WHERE id = v_sub.id;
        ELSE
            UPDATE subscriptions
            SET cycle_reservations_used = cycle_reservations_used + 1
            WHERE id = v_sub.id;
        END IF;

        -- Record billing event
        INSERT INTO billing_events (restaurant_id, subscription_id, event_type, reservation_id)
        VALUES (NEW.restaurant_id, v_sub.id, 'reservation_created', NEW.id);
    END IF;

    -- Decrement on cancellation (only if was confirmed)
    IF TG_OP = 'UPDATE' AND OLD.status = 'confirmed'
       AND NEW.status IN ('cancelled', 'no_show')
    THEN
        SELECT * INTO v_sub
        FROM subscriptions
        WHERE restaurant_id = NEW.restaurant_id AND is_current = TRUE
        FOR UPDATE;

        IF v_sub.tier = 'trial' THEN
            UPDATE subscriptions
            SET trial_reservations_used = GREATEST(0, trial_reservations_used - 1)
            WHERE id = v_sub.id;
        ELSE
            UPDATE subscriptions
            SET cycle_reservations_used = GREATEST(0, cycle_reservations_used - 1)
            WHERE id = v_sub.id;
        END IF;

        INSERT INTO billing_events (restaurant_id, subscription_id, event_type, reservation_id)
        VALUES (NEW.restaurant_id, v_sub.id, 'reservation_cancelled', NEW.id);
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_reservation_quota
    AFTER INSERT OR UPDATE OF status ON reservations
    FOR EACH ROW EXECUTE FUNCTION fn_track_reservation_quota();

-- ---------------------------------------------------------------------------
-- Billing cycle reset function (called by scheduler / APScheduler)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_reset_billing_cycle(p_restaurant_id UUID)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE subscriptions
    SET
        billing_cycle_start     = NOW(),
        billing_cycle_end       = NOW() + INTERVAL '30 days',
        cycle_reservations_used = 0,
        warned_at_80_pct        = FALSE,
        warned_at_95_pct        = FALSE,
        owner_notified_exhausted = FALSE,
        status                  = 'active'
    WHERE restaurant_id = p_restaurant_id AND is_current = TRUE
      AND tier != 'trial';

    INSERT INTO billing_events (restaurant_id, subscription_id, event_type)
    SELECT p_restaurant_id, id, 'cycle_reset'
    FROM subscriptions WHERE restaurant_id = p_restaurant_id AND is_current = TRUE;
END;
$$;

-- =============================================================================
-- SECTION 6: ROW-LEVEL SECURITY (RLS)
-- =============================================================================
-- RLS adds a second layer of tenant isolation enforced at the PostgreSQL level.
-- The application sets the current_restaurant_id session variable before queries.
-- Even if application code has a bug, data from other tenants is inaccessible.
-- =============================================================================

-- The app sets: SET LOCAL app.current_restaurant_id = '<uuid>';
-- These policies check that variable for every table access.

ALTER TABLE customers            ENABLE ROW LEVEL SECURITY;
ALTER TABLE tables               ENABLE ROW LEVEL SECURITY;
ALTER TABLE reservations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_events       ENABLE ROW LEVEL SECURITY;

-- Create a dedicated application role (NOT superuser)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'liftup_app') THEN
        CREATE ROLE liftup_app LOGIN PASSWORD 'change_in_production';
    END IF;
END$$;

GRANT CONNECT ON DATABASE current_database() TO liftup_app;
GRANT USAGE ON SCHEMA public TO liftup_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO liftup_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO liftup_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO liftup_app;

-- RLS helper: read the session variable safely
CREATE OR REPLACE FUNCTION current_restaurant_id()
RETURNS UUID LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.current_restaurant_id', TRUE), '')::UUID;
$$;

-- RLS Policies — one per tenant-scoped table
CREATE POLICY tenant_isolation_customers ON customers
    USING (restaurant_id = current_restaurant_id());

CREATE POLICY tenant_isolation_tables ON tables
    USING (restaurant_id = current_restaurant_id());

CREATE POLICY tenant_isolation_reservations ON reservations
    USING (restaurant_id = current_restaurant_id());

CREATE POLICY tenant_isolation_sessions ON conversation_sessions
    USING (restaurant_id = current_restaurant_id());

CREATE POLICY tenant_isolation_notifications ON notification_log
    USING (restaurant_id = current_restaurant_id());

CREATE POLICY tenant_isolation_billing ON billing_events
    USING (restaurant_id = current_restaurant_id());

-- Admin role bypasses RLS (for platform dashboards)
CREATE ROLE liftup_admin;
ALTER TABLE customers            FORCE ROW LEVEL SECURITY;
ALTER TABLE tables               FORCE ROW LEVEL SECURITY;
ALTER TABLE reservations         FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE notification_log     FORCE ROW LEVEL SECURITY;
ALTER TABLE billing_events       FORCE ROW LEVEL SECURITY;

-- liftup_admin bypasses all RLS
GRANT liftup_admin TO liftup_app;
ALTER ROLE liftup_admin BYPASSRLS;

-- =============================================================================
-- SECTION 7: USEFUL VIEWS (Platform Analytics)
-- =============================================================================

-- Subscription health view for monitoring dashboard
CREATE OR REPLACE VIEW v_subscription_health AS
SELECT
    r.id                        AS restaurant_id,
    r.restaurant_name,
    r.owner_email,
    s.tier,
    s.status,
    s.trial_reservations_used,
    s.trial_limit,
    ROUND(s.trial_reservations_used::numeric / NULLIF(s.trial_limit, 0) * 100, 1)
        AS trial_pct_used,
    s.cycle_reservations_used,
    s.cycle_reservation_limit,
    ROUND(s.cycle_reservations_used::numeric / NULLIF(s.cycle_reservation_limit, 0) * 100, 1)
        AS cycle_pct_used,
    s.billing_cycle_end,
    EXTRACT(days FROM s.billing_cycle_end - NOW()) AS days_until_cycle_end,
    s.warned_at_80_pct,
    s.warned_at_95_pct
FROM restaurants r
JOIN subscriptions s ON s.restaurant_id = r.id AND s.is_current = TRUE
WHERE r.is_active = TRUE;

-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
