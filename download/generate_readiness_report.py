#!/usr/bin/env python3
"""
LiftUp SaaS — Production Readiness Assessment Report
Generated via ReportLab
"""
import os, sys, hashlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, CondPageBreak, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus.tableofcontents import TableOfContents

# ── Palette ──
ACCENT       = colors.HexColor('#24738e')
TEXT_PRIMARY  = colors.HexColor('#191b1c')
TEXT_MUTED    = colors.HexColor('#7b8187')
BG_SURFACE   = colors.HexColor('#dadee2')
BG_PAGE      = colors.HexColor('#eff1f3')
TABLE_HEADER_COLOR = ACCENT
TABLE_HEADER_TEXT  = colors.white
TABLE_ROW_EVEN     = colors.white
TABLE_ROW_ODD      = BG_SURFACE

# ── Fonts ──
pdfmetrics.registerFont(TTFont('LiberationSerif', '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf'))
pdfmetrics.registerFont(TTFont('LiberationSerif-Bold', '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf'))
pdfmetrics.registerFont(TTFont('Carlito', '/usr/share/fonts/truetype/english/Carlito-Regular.ttf'))
pdfmetrics.registerFont(TTFont('Carlito-Bold', '/usr/share/fonts/truetype/english/Carlito-Bold.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
registerFontFamily('LiberationSerif', normal='LiberationSerif', bold='LiberationSerif-Bold')
registerFontFamily('Carlito', normal='Carlito', bold='Carlito-Bold')
registerFontFamily('DejaVuSans', normal='DejaVuSans', bold='DejaVuSans')

PAGE_W, PAGE_H = A4
LEFT_M = 1.0 * inch
RIGHT_M = 1.0 * inch
TOP_M = 0.9 * inch
BOT_M = 0.9 * inch
AVAILABLE_W = PAGE_W - LEFT_M - RIGHT_M

# ── Styles ──
body_style = ParagraphStyle(
    'Body', fontName='LiberationSerif', fontSize=10.5, leading=17,
    alignment=TA_JUSTIFY, textColor=TEXT_PRIMARY, spaceAfter=6,
)
body_left = ParagraphStyle(
    'BodyLeft', parent=body_style, alignment=TA_LEFT,
)
h1_style = ParagraphStyle(
    'H1', fontName='LiberationSerif', fontSize=20, leading=26,
    alignment=TA_LEFT, textColor=ACCENT, spaceBefore=18, spaceAfter=10,
)
h2_style = ParagraphStyle(
    'H2', fontName='LiberationSerif', fontSize=15, leading=20,
    alignment=TA_LEFT, textColor=TEXT_PRIMARY, spaceBefore=14, spaceAfter=8,
)
h3_style = ParagraphStyle(
    'H3', fontName='LiberationSerif', fontSize=12.5, leading=17,
    alignment=TA_LEFT, textColor=TEXT_PRIMARY, spaceBefore=10, spaceAfter=6,
)
caption_style = ParagraphStyle(
    'Caption', fontName='LiberationSerif', fontSize=9.5, leading=14,
    alignment=TA_CENTER, textColor=TEXT_MUTED, spaceBefore=3, spaceAfter=6,
)
header_cell = ParagraphStyle(
    'HeaderCell', fontName='LiberationSerif', fontSize=10,
    textColor=colors.white, alignment=TA_CENTER, leading=14,
)
cell_style = ParagraphStyle(
    'Cell', fontName='LiberationSerif', fontSize=9.5,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, leading=14,
)
cell_center = ParagraphStyle(
    'CellCenter', parent=cell_style, alignment=TA_CENTER,
)
callout_style = ParagraphStyle(
    'Callout', fontName='LiberationSerif', fontSize=11, leading=17,
    alignment=TA_LEFT, textColor=ACCENT, leftIndent=18,
    borderPadding=8, spaceBefore=8, spaceAfter=8,
)
muted_style = ParagraphStyle(
    'Muted', fontName='LiberationSerif', fontSize=9.5, leading=14,
    textColor=TEXT_MUTED, alignment=TA_LEFT,
)
bullet_style = ParagraphStyle(
    'Bullet', parent=body_style, leftIndent=24, bulletIndent=12,
    spaceAfter=4,
)
risk_high = ParagraphStyle('RiskHigh', parent=cell_style, textColor=colors.HexColor('#c62828'))
risk_med = ParagraphStyle('RiskMed', parent=cell_style, textColor=colors.HexColor('#e65100'))
risk_low = ParagraphStyle('RiskLow', parent=cell_style, textColor=colors.HexColor('#2e7d32'))

# ── TOC Template ──
class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            self.notify('TOCEntry', (level, text, self.page, key))

H1_ORPHAN = (PAGE_H - TOP_M - BOT_M) * 0.15

def add_heading(text, style, level=0):
    key = 'h_%s' % hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph('<a name="%s"/>%s' % (key, text), style)
    p.bookmark_name = text
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p

def add_major_section(text, style):
    return [
        CondPageBreak(H1_ORPHAN),
        add_heading(text, style, level=0),
    ]

def make_table(data, col_widths, caption_text=None):
    t = Table(data, colWidths=col_widths, hAlign='CENTER')
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), TABLE_HEADER_TEXT),
        ('GRID', (0, 0), (-1, -1), 0.5, TEXT_MUTED),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(data)):
        bg = TABLE_ROW_EVEN if i % 2 == 1 else TABLE_ROW_ODD
        style_cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
    t.setStyle(TableStyle(style_cmds))
    elements = [Spacer(1, 18), t]
    if caption_text:
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(caption_text, caption_style))
    elements.append(Spacer(1, 18))
    return elements

def P(text, style=None):
    return Paragraph(text, style or body_style)

def BP(text):
    return Paragraph(text, bullet_style)

# ── Build Document ──
OUTPUT = '/home/z/my-project/download/liftup_production_readiness.pdf'
doc = TocDocTemplate(
    OUTPUT, pagesize=A4,
    leftMargin=LEFT_M, rightMargin=RIGHT_M,
    topMargin=TOP_M, bottomMargin=BOT_M,
    title='LiftUp SaaS Production Readiness Assessment',
    author='Z.ai', creator='Z.ai',
)

story = []

# ── TOC ──
toc = TableOfContents()
toc.levelStyles = [
    ParagraphStyle('TOC1', fontName='LiberationSerif', fontSize=13, leftIndent=20, leading=22, spaceBefore=6, spaceAfter=3),
    ParagraphStyle('TOC2', fontName='LiberationSerif', fontSize=11, leftIndent=40, leading=18, spaceBefore=2, spaceAfter=2),
]
story.append(Paragraph('<b>Table of Contents</b>', h1_style))
story.append(toc)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════
# SECTION 1: Executive Summary
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('1. Executive Summary', h1_style))

story.append(P(
    'This report evaluates the production readiness of the LiftUp SaaS multi-tenant AI Reservation '
    'platform for a planned launch supporting 50 restaurants. The assessment is based on a thorough '
    'review of the server runtime logs from a live test session conducted on 2026-06-03, a deep '
    'analysis of the Python/FastAPI backend source code (main.py, agent.py, database.py, '
    'notifications.py, config.py), the PostgreSQL schema (schema.sql), and the integration between '
    'the Anthropic Claude API agent and the booking pipeline. The review examines functional '
    'correctness, data integrity, security posture, error handling, concurrency safety, and '
    'operational observability.'
))

story.append(P(
    'The core booking flow (check availability, create customer, book table, send confirmation) is '
    'functionally operational. The recent serialization bug that caused the agent to crash after the '
    'first message has been resolved. Multi-turn conversations now persist correctly through '
    'PostgreSQL JSONB round-tripping. However, this assessment identifies several production-blocking '
    'defects, moderate-risk vulnerabilities, and operational gaps that must be addressed before the '
    'system can safely serve real customers at scale. The most critical issue is a logic bug in the '
    'modify_reservation flow that makes same-slot modifications impossible when the restaurant has '
    'limited table inventory, which would directly impact real guest experiences and generate '
    'immediate support tickets from restaurant operators.'
))

# Score callout
story.append(Spacer(1, 12))
score_data = [
    [Paragraph('<b>Category</b>', header_cell), Paragraph('<b>Score (1-10)</b>', header_cell), Paragraph('<b>Status</b>', header_cell)],
    [Paragraph('Functional Correctness', cell_style), Paragraph('7/10', cell_center), Paragraph('Partial - modify broken', cell_style)],
    [Paragraph('Security & Tenant Isolation', cell_style), Paragraph('8/10', cell_center), Paragraph('Strong foundation', cell_style)],
    [Paragraph('Data Integrity & Concurrency', cell_style), Paragraph('6/10', cell_center), Paragraph('Gaps in modify flow', cell_style)],
    [Paragraph('Error Handling & Resilience', cell_style), Paragraph('5/10', cell_center), Paragraph('Needs hardening', cell_style)],
    [Paragraph('Operational Observability', cell_style), Paragraph('4/10', cell_center), Paragraph('Insufficient for prod', cell_style)],
    [Paragraph('Production Infrastructure', cell_style), Paragraph('3/10', cell_center), Paragraph('Missing critical pieces', cell_style)],
    [Paragraph('<b>Overall</b>', ParagraphStyle('BoldCell', parent=cell_style, fontName='LiberationSerif')), Paragraph('<b>5.5/10</b>', ParagraphStyle('BoldCenter', parent=cell_center, fontName='LiberationSerif')), Paragraph('<b>Not Production Ready</b>', ParagraphStyle('BoldRed', parent=cell_style, textColor=colors.HexColor('#c62828'), fontName='LiberationSerif'))],
]
story.extend(make_table(score_data, [AVAILABLE_W*0.40, AVAILABLE_W*0.25, AVAILABLE_W*0.35], 'Table 1: Production Readiness Scorecard'))

# ═══════════════════════════════════════════════════════════
# SECTION 2: Log Analysis
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('2. Runtime Log Analysis', h1_style))

story.append(add_heading('2.1 Positive Observations', h2_style, level=1))
story.append(P(
    'The server logs from the 2026-06-03 test session confirm that the core booking pipeline is '
    'functionally correct end-to-end. The startup sequence completes cleanly: the PostgreSQL '
    'connection pool initializes with min=10, max=50 connections, APScheduler starts the 24-hour '
    'billing cycle reset job, and the first billing reset fires successfully on startup. The health '
    'check endpoint responds consistently with 200 OK throughout the session, confirming that the '
    'database connection pool remains stable under continuous health-check polling from the frontend '
    'dashboard (approximately every 15 seconds).'
))
story.append(P(
    'The complete booking flow was exercised successfully: the agent received a user message, '
    'called check_table_availability with correct parameters (reservation_date, start_time, '
    'party_size), then executed find_or_create_customer, followed by book_table with the correct '
    'customer_id, table_id, and all required fields. The send_confirmation tool fired immediately '
    'after booking, and SendGrid accepted the email with HTTP 202. The confirmation code RES-VUWVRL '
    'was generated and returned to the user. This confirms the entire happy path works correctly.'
))
story.append(P(
    'Critically, the second and third messages in the conversation both returned 200 OK, confirming '
    'that the _serialize_content_block fix from the previous debugging session is working correctly. '
    'The conversation history survives the JSON round-trip through PostgreSQL JSONB without '
    'corrupting Anthropic SDK objects. Multi-turn dialogue with tool use is now functional.'
))

story.append(add_heading('2.2 Issues Identified in Logs', h2_style, level=1))

story.append(add_heading('2.2.1 Modify Reservation Failure', h3_style, level=1))
story.append(P(
    'The most significant issue observed in the logs is the modify_reservation failure. When the user '
    'attempted to modify an existing reservation (confirmation code RES-VUWVRL, party size change '
    'from 4 to 5), the tool call was made with the correct parameters, but the system returned a '
    'business error: "No tables available on 2025-07-18 at 19:00:00 for 4 guests" (and subsequently '
    '"for 5 guests"). This is not an API error or a network failure; it is a logic bug in the '
    'modify_reservation function. The function calls check_availability BEFORE cancelling the '
    'existing reservation, meaning the old reservation still occupies the time slot during the '
    'availability check. For restaurants with limited table inventory (as is common with small or '
    'mid-size establishments), this makes it impossible for a guest to modify their reservation to '
    'the same date and time with a different party size, even though their own booking should not '
    'count against availability. This bug would generate immediate customer complaints and support '
    'tickets at launch.'
))

story.append(add_heading('2.2.2 Excessive Health Check Polling', h3_style, level=1))
story.append(P(
    'The frontend dashboard is polling GET /health approximately every 15 seconds. Over the '
    'approximately 10-minute test session, more than 25 health check requests were logged. While '
    'this is not a bug per se, at 50 restaurants each running a dashboard, this creates '
    'approximately 200 health check requests per minute against the backend. This is wasteful but '
    'not production-blocking. The recommended fix is to increase the polling interval to 60 seconds '
    'or implement a WebSocket-based health monitor that pushes status changes rather than polling.'
))

# ═══════════════════════════════════════════════════════════
# SECTION 3: Critical Bugs
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('3. Critical Bugs Found in Source Code', h1_style))

story.append(add_heading('3.1 Modify Reservation: Availability Check Before Cancel (BLOCKER)', h2_style, level=1))
story.append(P(
    'In database.py lines 638-683, the modify_reservation function implements a cancel-then-rebook '
    'strategy, but the execution order is incorrect. The function first calls check_availability() '
    'to verify that tables exist for the new parameters, then cancels the old reservation, and '
    'finally calls book_table() to create the new one. The problem is that check_availability() '
    'runs while the old reservation is still active, so the old reservation continues to occupy the '
    'table slot. If the restaurant has only one suitable table for the requested time, the '
    'availability check will fail because it sees the old reservation as blocking the slot. This '
    'means a guest cannot change their party size while keeping the same date and time, which is '
    'the most common modification scenario. The fix is to cancel the old reservation FIRST within '
    'the same database transaction, then check availability, then book. This must all happen inside '
    'a single tenant_transaction() to maintain atomicity and prevent race conditions where another '
    'customer could grab the newly-freed slot between the cancel and rebook.'
))

story.append(add_heading('3.2 Confirmation Code Uses Insecure random.choices()', h2_style, level=1))
story.append(P(
    'In database.py lines 482-486, the _make_confirmation_code() function has a subtle but '
    'important security flaw. The function generates two separate 6-character codes: one using '
    'secrets.choice() (cryptographically secure) and another using random.choices() (not '
    'cryptographically secure). However, the function returns only the random.choices() result, '
    'completely discarding the secure code. This means confirmation codes are generated using '
    'Python\'s Mersenne Twister PRNG, which is deterministic and can be reverse-engineered by an '
    'attacker who observes enough generated codes. While confirmation codes are not authentication '
    'credentials, they serve as proof of booking and could be used to cancel or modify reservations. '
    'The fix is straightforward: remove the random.choices() line and use only secrets.choice() '
    'for code generation, which was clearly the original intent.'
))

story.append(add_heading('3.3 TenantConn Context Manager Leaks Connections on Error', h2_style, level=1))
story.append(P(
    'In database.py lines 83-113, the TenantConn context manager acquires a connection from the '
    'pool in __aenter__ and releases it in __aexit__. However, the __aexit__ method does not check '
    'whether self._conn is None before releasing, and there is no exception handling for the case '
    'where the pool.acquire() call itself fails. If an exception occurs after pool.acquire() but '
    'before the connection is assigned to self._conn, the __aexit__ method would attempt to release '
    'None, potentially masking the original error. While the tenant_transaction() function at lines '
    '116-129 uses the safer pool.acquire() context manager pattern, the TenantConn class is defined '
    'and imported but appears to be unused in favor of tenant_transaction(). This is dead code that '
    'could confuse future developers. The recommendation is to either fix TenantConn with proper '
    'error handling and use it consistently, or remove it entirely to avoid confusion.'
))

# ═══════════════════════════════════════════════════════════
# SECTION 4: Security
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('4. Security & Tenant Isolation Assessment', h1_style))

story.append(add_heading('4.1 Strengths', h2_style, level=1))
story.append(P(
    'The multi-tenant isolation architecture is well-designed with defense-in-depth: the application '
    'layer enforces restaurant_id on every query, and PostgreSQL Row-Level Security (RLS) provides '
    'a second independent safety net. The RLS policies are properly configured with FORCE ROW LEVEL '
    'SECURITY enabled, meaning even the application role cannot bypass them. The tenant_auth_middleware '
    'correctly validates the X-Api-Key header against the database, loads the full tenant record, '
    'and attaches it to request.state so downstream handlers never read tenant identity from '
    'client-supplied data. The execute_tool function in agent.py explicitly strips any restaurant_id '
    'that Claude might hallucinate into tool calls, preventing prompt injection attacks from '
    'exfiltrating another tenant\'s data. The API key is stored as a 64-character hex string '
    'generated by pgcrypto, which is cryptographically strong.'
))

story.append(add_heading('4.2 Concerns', h2_style, level=1))
story.append(P(
    'Several security concerns remain. First, CORS is configured with allow_origins=["*"], which '
    'is acceptable for an embedded widget that runs on arbitrary domains but means any website can '
    'make requests to the API if they possess a valid API key. This is by design for the widget '
    'use case, but the documentation should clearly state that the API key itself is the sole '
    'authentication boundary. Second, the config.py requires SECRET_KEY but it is not used anywhere '
    'in the codebase, suggesting incomplete implementation of a signing or encryption feature. '
    'Third, the database password in schema.sql is hardcoded as "change_in_production" for the '
    'liftup_app role, which must be rotated before deployment. Fourth, there is no rate limiting on '
    'the health check endpoint or the availability endpoint, which could be used for denial-of-service '
    'amplification. The slowapi rate limiter is configured for widget routes but not for system routes.'
))

# ═══════════════════════════════════════════════════════════
# SECTION 5: Top 3 Risks
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('5. Top 3 Risks for 50-Restaurant Launch', h1_style))

risk_data = [
    [Paragraph('<b>Rank</b>', header_cell), Paragraph('<b>Risk</b>', header_cell), Paragraph('<b>Impact</b>', header_cell), Paragraph('<b>Likelihood</b>', header_cell), Paragraph('<b>Mitigation</b>', header_cell)],
    [
        Paragraph('1', cell_center),
        Paragraph('Modify reservation logic bug renders same-slot modifications impossible for restaurants with limited table inventory', cell_style),
        Paragraph('HIGH - Direct revenue loss from failed modifications; restaurant operators lose trust in the platform', risk_high),
        Paragraph('CERTAIN - Will occur for any restaurant with fewer tables than concurrent demand at a time slot', risk_high),
        Paragraph('Fix modify_reservation to cancel first within a single transaction, then check availability and rebook', cell_style),
    ],
    [
        Paragraph('2', cell_center),
        Paragraph('No structured logging, request tracing, or alerting. Agent errors only surface as generic 500 responses', cell_style),
        Paragraph('HIGH - Production incidents will be invisible; debugging guest complaints requires manual log scraping', risk_med),
        Paragraph('HIGH - Every production deployment without observability suffers this', risk_med),
        Paragraph('Add correlation IDs, structured JSON logging, error tracking (Sentry), and uptime monitoring', cell_style),
    ],
    [
        Paragraph('3', cell_center),
        Paragraph('No HTTPS enforcement, no database password rotation, unused SECRET_KEY, and no authentication for system routes', cell_style),
        Paragraph('MEDIUM - API keys transmitted in plaintext over HTTP; database credentials are default values', risk_med),
        Paragraph('MEDIUM - Depends on deployment environment; local dev is safe but production exposure varies', risk_med),
        Paragraph('Enforce HTTPS, rotate DB credentials, implement SECRET_KEY-based signing, add auth to system routes', cell_style),
    ],
]
story.extend(make_table(risk_data, [AVAILABLE_W*0.06, AVAILABLE_W*0.26, AVAILABLE_W*0.24, AVAILABLE_W*0.20, AVAILABLE_W*0.24], 'Table 2: Top 3 Production Launch Risks'))

# ═══════════════════════════════════════════════════════════
# SECTION 6: Detailed Findings
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('6. Detailed Findings by Category', h1_style))

story.append(add_heading('6.1 Data Integrity & Concurrency', h2_style, level=1))
story.append(P(
    'The book_table function uses SELECT FOR UPDATE NOWAIT to lock table rows during booking, which '
    'is the correct approach for preventing double-bookings under concurrent access. The PostgreSQL '
    'exclusion constraint (no_table_time_overlap) using a GiST index provides a database-level '
    'safety net that prevents overlapping reservations even if the application-level lock somehow '
    'fails. The fn_track_reservation_quota trigger atomically increments and decrements billing '
    'counters within the same transaction, ensuring quota tracking is always consistent with actual '
    'reservation state. These are strong concurrency controls for the booking path.'
))
story.append(P(
    'However, the modify_reservation function does not use SELECT FOR UPDATE or any locking '
    'mechanism. It reads the existing reservation, checks availability, cancels the old one, and '
    'creates a new one across two separate transactions (lines 668-683). Between the cancel and '
    'rebook, another concurrent request could grab the freed table slot, causing the rebook to fail. '
    'This is a classic TOCTOU (Time-of-Check-Time-of-Use) race condition. The entire modify '
    'operation must be wrapped in a single tenant_transaction() with appropriate locking to be safe.'
))

story.append(add_heading('6.2 Error Handling & Resilience', h2_style, level=1))
story.append(P(
    'The agent error handler in main.py (lines 382-387) catches all exceptions from run_agent and '
    'returns a generic 500 error with the message "The reservation assistant encountered an error. '
    'Please try again." While this is acceptable for user-facing responses, it loses all diagnostic '
    'context. There is no structured error tracking, no request correlation IDs, and no integration '
    'with error monitoring services like Sentry or Rollbar. The server logs capture the full '
    'traceback, but these are plain text logs that are difficult to search, filter, or alert on. '
    'For a production system serving 50 restaurants, the operations team needs the ability to '
    'trace a specific guest\'s failed booking attempt across multiple service boundaries (FastAPI, '
    'Anthropic API, PostgreSQL, SendGrid, Twilio).'
))
story.append(P(
    'The notification layer has no retry mechanism. If SendGrid or Twilio returns a transient error '
    '(5xx, timeout), the confirmation is simply lost. The notification_log table records the failure, '
    'but there is no scheduled job to retry failed notifications. For a reservation system, an '
    'unconfirmed booking creates significant operational risk: the guest thinks they have a table, '
    'the restaurant has no record in their email, and the guest shows up expecting to be seated. A '
    'retry queue with exponential backoff should be implemented for critical notification failures.'
))

story.append(add_heading('6.3 Operational Observability', h2_style, level=1))
story.append(P(
    'The current observability stack consists entirely of Python\'s logging.basicConfig with INFO '
    'level and a simple format string. There are no metrics (request latency, booking conversion '
    'rate, error rate by endpoint), no distributed tracing, no health dashboards, and no alerting. '
    'The health check endpoint (GET /health) only verifies database connectivity, not the status '
    'of external dependencies like Anthropic, SendGrid, or Twilio. A production system needs at '
    'minimum: structured JSON logging for machine-parseable logs, a /health endpoint that checks '
    'all critical dependencies, Prometheus-compatible metrics for auto-scaling decisions, and alert '
    'rules for error rate spikes, database connection pool exhaustion, and external API failures.'
))

story.append(add_heading('6.4 Production Infrastructure Gaps', h2_style, level=1))
story.append(P(
    'The application runs with uvicorn --reload (StatReload), which is a development-only feature '
    'that should never be used in production. The reload watcher monitors file changes and restarts '
    'the server, which can cause brief availability gaps and is not designed for production traffic. '
    'The production deployment should use a process manager like Gunicorn with Uvicorn workers, '
    'behind a reverse proxy like Nginx or Caddy that handles TLS termination, connection pooling, '
    'and static file serving. The application also lacks: database migration tooling (no Alembic or '
    'similar), environment-specific configuration validation, graceful shutdown handling for in-flight '
    'requests, horizontal scaling strategy (the current in-process APScheduler will fire duplicate '
    'jobs if multiple instances are running), and a CDN strategy for the embedded widget JavaScript.'
))

# ═══════════════════════════════════════════════════════════
# SECTION 7: Required Fixes
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('7. Required Fixes Before Production', h1_style))

fixes_data = [
    [Paragraph('<b>Priority</b>', header_cell), Paragraph('<b>Issue</b>', header_cell), Paragraph('<b>File / Location</b>', header_cell), Paragraph('<b>Effort</b>', header_cell)],
    [Paragraph('P0 - Blocker', risk_high), Paragraph('Fix modify_reservation: cancel first, then check availability, all in one transaction', cell_style), Paragraph('database.py L638-683', cell_style), Paragraph('4 hours', cell_center)],
    [Paragraph('P0 - Blocker', risk_high), Paragraph('Fix _make_confirmation_code: use secrets.choice exclusively, remove random.choices', cell_style), Paragraph('database.py L482-486', cell_style), Paragraph('15 minutes', cell_center)],
    [Paragraph('P1 - Critical', risk_med), Paragraph('Add structured JSON logging with request correlation IDs', cell_style), Paragraph('main.py, agent.py', cell_style), Paragraph('8 hours', cell_center)],
    [Paragraph('P1 - Critical', risk_med), Paragraph('Implement notification retry queue with exponential backoff', cell_style), Paragraph('notifications.py, new file', cell_style), Paragraph('12 hours', cell_center)],
    [Paragraph('P1 - Critical', risk_med), Paragraph('Rotate database password, enforce HTTPS, implement SECRET_KEY usage', cell_style), Paragraph('schema.sql, config.py', cell_style), Paragraph('4 hours', cell_center)],
    [Paragraph('P2 - High', risk_med), Paragraph('Add Alembic migrations, remove --reload from production config', cell_style), Paragraph('Project setup', cell_style), Paragraph('8 hours', cell_center)],
    [Paragraph('P2 - High', risk_med), Paragraph('Distributed scheduler lock for multi-instance APScheduler', cell_style), Paragraph('main.py', cell_style), Paragraph('6 hours', cell_center)],
    [Paragraph('P3 - Medium', risk_low), Paragraph('Enhanced /health with dependency checks (Anthropic, SendGrid, Twilio)', cell_style), Paragraph('main.py', cell_style), Paragraph('4 hours', cell_center)],
    [Paragraph('P3 - Medium', risk_low), Paragraph('Reduce health check polling interval from 15s to 60s', cell_style), Paragraph('Frontend dashboard', cell_style), Paragraph('5 minutes', cell_center)],
    [Paragraph('P3 - Medium', risk_low), Paragraph('Remove dead TenantConn class or add proper error handling', cell_style), Paragraph('database.py L83-113', cell_style), Paragraph('1 hour', cell_center)],
]
story.extend(make_table(fixes_data, [AVAILABLE_W*0.14, AVAILABLE_W*0.38, AVAILABLE_W*0.28, AVAILABLE_W*0.10], 'Table 3: Prioritized Fix List with Estimated Effort'))

# ═══════════════════════════════════════════════════════════
# SECTION 8: Verdict
# ═══════════════════════════════════════════════════════════
story.extend(add_major_section('8. Verdict', h1_style))

story.append(P(
    'The LiftUp SaaS platform demonstrates a solid architectural foundation with strong multi-tenant '
    'isolation, proper database-level concurrency controls for the booking path, and a well-designed '
    'AI agent integration that correctly scopes tool calls to the authenticated restaurant. The core '
    'booking happy path works end-to-end, including real email confirmations via SendGrid. The recent '
    'fix for the conversation history serialization bug has resolved the most visible crash.'
))
story.append(P(
    'However, the system is not ready for a 50-restaurant production launch. The modify_reservation '
    'logic bug is a production blocker that will cause immediate customer-facing failures. The lack '
    'of structured observability means the team will be flying blind when issues inevitably arise. '
    'The insecure confirmation code generation, while low-probability for exploitation, represents '
    'an unnecessary risk that takes 15 minutes to fix. And the production infrastructure gaps '
    '(dev-mode server, no migrations, no distributed scheduler lock) will cause operational problems '
    'as soon as the system is deployed to more than one instance.'
))
story.append(P(
    'With the P0 and P1 fixes completed (estimated 28 hours of engineering work), the system would '
    'reach a defensible production-ready state for a controlled launch with 5-10 restaurants. The '
    'P2 and P3 items should be addressed before scaling to 50 restaurants, as the operational '
    'complexity increases non-linearly with tenant count. The recommended launch sequence is: fix '
    'P0 items immediately, complete P1 items in the following sprint, then begin a staged rollout '
    'starting with 5 restaurants, monitoring error rates and latency closely, before expanding to '
    'the full 50-restaurant target.'
))

# ── Build ──
doc.multiBuild(story)
print(f"PDF generated: {OUTPUT}")
