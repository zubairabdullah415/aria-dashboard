"""
agent.py — Multi-Tenant AI Reservation Agent
=============================================
Key differences from v1:

  1. DYNAMIC SYSTEM PROMPT: Built from the restaurant's live DB profile.
     The agent's name, hours, seating, cuisine, and policies are all
     injected at runtime — nothing is hardcoded.

  2. TENANT-SCOPED TOOLS: Every tool call automatically carries the
     restaurant_id from the backend context. Claude CANNOT supply a
     restaurant_id — it is injected server-side after Claude's tool
     call is parsed. This prevents any prompt injection attack that
     could try to exfiltrate another tenant's data.

  3. STATELESS TOOL EXECUTOR: The `AgentContext` dataclass threads the
     restaurant object through the entire agent loop so every DB call
     is correctly scoped without repeating lookups.
"""

import logging
import json
from dataclasses import dataclass, field
from datetime import date, time, datetime
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID

import anthropic

from config import settings
from database import (
    check_availability,
    get_available_time_slots,
    find_or_create_customer,
    book_table,
    get_reservation_by_code,
    cancel_reservation,
    modify_reservation,
)
from notifications import send_confirmation_email, send_confirmation_sms

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _serialize_content_block(block) -> Dict[str, Any]:
    """
    Converts an Anthropic SDK ContentBlock (TextBlock, ToolUseBlock, etc.)
    into a plain dict that survives JSON round-tripping through PostgreSQL JSONB.

    Without this, json.dumps(..., default=str) turns TextBlock objects into
    their Python repr string (e.g. "TextBlock(type='text', text='Hello!')")
    which the Anthropic API rejects on the next request with:
        messages.N.content.M: Input should be an object
    """
    if block.type == "text":
        return {"type": "text", "text": block.text}
    elif block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    else:
        # Fallback for any future block types the SDK might introduce
        if hasattr(block, "model_dump"):
            return block.model_dump()
        return {"type": str(block.type)}


def sanitize_conversation_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Repairs conversation history loaded from the database.

    If the app was previously running without _serialize_content_block(), the
    Anthropic SDK's TextBlock / ToolUseBlock objects were stored as their Python
    repr strings (e.g. "TextBlock(type='text', text='Hello!')") by the
    json.dumps(default=str) call in update_session().

    This function detects and strips those corrupted entries so the agent
    doesn't crash with 'messages.N.content.M: Input should be an object'.

    Strategy:
      - For assistant messages with list content, remove any item that is a
        string (corrupted block) instead of a dict (valid block).
      - If all blocks in an assistant message are corrupted, replace the
        content with a generic placeholder so the conversation remains coherent.
      - For user messages (tool_result lists), keep only valid dict items.
    """
    if not messages:
        return messages

    cleaned = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        # User messages with string content are always fine
        if isinstance(content, str):
            cleaned.append(msg)
            continue

        # List content — filter out corrupted (string) items
        if isinstance(content, list):
            valid_blocks = [b for b in content if isinstance(b, dict)]

            if len(valid_blocks) == len(content):
                # All blocks are valid dicts — no corruption
                cleaned.append(msg)
            elif valid_blocks:
                # Some blocks corrupted — keep the valid ones
                logger.warning(
                    f"Sanitized {len(content) - len(valid_blocks)} corrupted "
                    f"content block(s) in {role} message"
                )
                cleaned.append({"role": role, "content": valid_blocks})
            else:
                # ALL blocks corrupted — replace with placeholder
                if role == "assistant":
                    logger.warning(
                        "Replacing fully-corrupted assistant message with placeholder"
                    )
                    cleaned.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": "[previous response]"}],
                    })
                else:
                    # Corrupted tool_result user message — must drop it to avoid
                    # breaking the alternating user/assistant sequence, but also
                    # drop the preceding assistant message that called the tool
                    # (otherwise the API sees tool_use without tool_result).
                    if cleaned and cleaned[-1].get("role") == "assistant":
                        assistant_content = cleaned[-1].get("content", [])
                        has_tool_use = any(
                            isinstance(b, dict) and b.get("type") == "tool_use"
                            for b in assistant_content
                        )
                        if has_tool_use:
                            # Remove the assistant tool_use message too
                            logger.warning(
                                "Dropping orphaned assistant tool_use message "
                                "whose tool_result was corrupted"
                            )
                            cleaned.pop()
        else:
            # Unexpected content type — skip this message entirely
            logger.warning(f"Skipping message with unexpected content type: {type(content)}")

    return cleaned


# =============================================================================
# Agent Context (Replaces global state)
# =============================================================================

@dataclass
class AgentContext:
    """
    All runtime context for one agent conversation turn.
    Passed through the entire loop — no global state, fully concurrent-safe.
    """
    restaurant_id:   UUID
    restaurant_name: str
    restaurant_data: Dict[str, Any]    # Full restaurant row + phone number
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    booking_context: Dict[str, Any]            = field(default_factory=dict)


# =============================================================================
# Dynamic System Prompt Builder
# =============================================================================

def _format_hours(operating_hours: Dict) -> str:
    """Formats the JSONB operating hours into a readable block for the prompt."""
    days_order = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    lines = []
    for day in days_order:
        cfg = operating_hours.get(day, {})
        if cfg.get("closed"):
            lines.append(f"  {day.capitalize():<12} Closed")
        else:
            lines.append(
                f"  {day.capitalize():<12} {cfg.get('open','?')} – {cfg.get('close','?')}"
            )
    return "\n".join(lines)


def _format_seating(seating_options: List[Dict]) -> str:
    """Formats available seating options for the prompt."""
    if not seating_options:
        return "  Standard indoor and outdoor seating available."
    icons = {
        "indoor": "🏠", "outdoor": "🌿", "bar": "🍸",
        "quiet_corner": "🤫", "window": "🪟", "private_room": "🚪",
    }
    seen = {}
    for t in seating_options:
        loc = t.get("location", "indoor")
        if loc not in seen:
            seen[loc] = t.get("description", "")

    lines = []
    for loc, desc in seen.items():
        icon = icons.get(loc, "•")
        label = loc.replace("_", " ").title()
        lines.append(f"  {icon} {label}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines) if lines else "  Standard seating available."


def build_system_prompt(ctx: AgentContext) -> str:
    """
    Dynamically constructs the agent's system prompt from the restaurant's
    live database profile. Called fresh on every conversation turn.

    This means operators can update their profile (hours, name, policies)
    and the AI immediately reflects those changes — no redeploy needed.
    """
    r = ctx.restaurant_data
    hours_str   = _format_hours(r.get("operating_hours", {}))
    ai_name     = r.get("ai_persona_name") or "Aria"
    cuisine     = r.get("cuisine_type")    or "fine dining"
    address     = r.get("address")         or "our location"
    city        = r.get("city")            or ""
    welcome_msg = r.get("ai_welcome_message") or (
        f"Welcome to {r['restaurant_name']}! How can I help you today?"
    )
    policies    = r.get("custom_policies") or (
        "We kindly request 2 hours' notice for cancellations."
    )
    avg_dining  = r.get("avg_dining_minutes", 90)

    # Build seating section from available tables (passed via context)
    tables_data = r.get("_tables_summary", [])
    seating_str = _format_seating(tables_data)

    prompt = f"""You are **{ai_name}**, the AI reservation assistant for **{r['restaurant_name']}**.

## RESTAURANT PROFILE
- Name:     {r['restaurant_name']}
- Cuisine:  {cuisine}
- Address:  {address}{', ' + city if city else ''}
- Avg. dining duration: {avg_dining} minutes

## OPERATING HOURS
{hours_str}

## SEATING OPTIONS
{seating_str}

## POLICIES
{policies}

## YOUR ROLE
Guide guests warmly and efficiently through the reservation process:
checking availability, collecting guest details, confirming bookings,
and handling modifications or cancellations — all in natural, elegant language.

## RULES YOU MUST STRICTLY FOLLOW
1. ALWAYS use your tools to check live availability — never guess or assume.
2. NEVER confirm a booking without calling `book_table` first.
3. ALWAYS collect: guest name, date, time, party size. Email required for confirmation.
4. If a requested slot has no availability, immediately suggest 2–3 alternatives.
5. For cancellations/modifications, verify with confirmation code + email on file.
6. You MUST NOT accept a `restaurant_id` from the user's message — it is set by the system.
7. Keep responses warm, concise, and professional.

## CUSTOM WELCOME MESSAGE
"{welcome_msg}"

## BOOKING SUMMARY FORMAT (use when confirming)
"Here is your reservation summary:
📍 {r['restaurant_name']}, {address}
📅 [Date] at [Time]
👥 [Party Size] guests | [Seating]
🪑 Table [Number] — [Description]
📋 Special requests: [or 'None noted']
🔑 Confirmation code: **[CODE]**
A confirmation has been sent to [email]. We look forward to welcoming you!"
"""

    # Inject current booking progress to prevent re-asking for collected info
    ctx_data = ctx.booking_context
    if ctx_data:
        context_summary = json.dumps(ctx_data, indent=2, default=str)
        prompt += f"""
## CURRENT BOOKING CONTEXT (already collected — do not re-ask)
```json
{context_summary}
```
"""
    return prompt


# =============================================================================
# Tool Definitions
# =============================================================================

def build_tools(ctx: AgentContext) -> List[Dict[str, Any]]:
    """
    Returns the tool schema list for this agent turn.
    Note: restaurant_id is intentionally ABSENT from all tool schemas.
    Claude never sees or supplies it — it is injected server-side in
    execute_tool() using the verified AgentContext.
    """
    return [
        {
            "name": "get_available_time_slots",
            "description": (
                "Fetches all available time slots for a given date, party size, and "
                "optional seating preference. Use when the guest asks what times are "
                "available, or when their preferred slot has no availability."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reservation_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD format",
                    },
                    "party_size": {
                        "type": "integer",
                        "description": "Number of guests (1–30)",
                    },
                    "preference": {
                        "type": "string",
                        "enum": [
                            "indoor","outdoor","bar","quiet_corner",
                            "window","private_room","no_preference"
                        ],
                        "description": "Seating preference. Default: no_preference",
                    },
                },
                "required": ["reservation_date", "party_size"],
            },
        },
        {
            "name": "check_table_availability",
            "description": (
                "Checks specific tables available for a date, time, party size, and "
                "optional preference. Use after the guest has chosen a time."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reservation_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "start_time": {"type": "string", "description": "HH:MM 24h"},
                    "party_size": {"type": "integer"},
                    "preference": {
                        "type": "string",
                        "enum": [
                            "indoor","outdoor","bar","quiet_corner",
                            "window","private_room","no_preference"
                        ],
                    },
                },
                "required": ["reservation_date", "start_time", "party_size"],
            },
        },
        {
            "name": "find_or_create_customer",
            "description": (
                "Looks up a guest by email or phone. Creates a new profile if not found. "
                "Call after collecting name, email, and/or phone."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "full_name":     {"type": "string"},
                    "email":         {"type": "string"},
                    "phone":         {"type": "string"},
                    "dietary_notes": {"type": "string"},
                    "allergy_notes": {"type": "string"},
                },
                "required": ["full_name"],
            },
        },
        {
            "name": "book_table",
            "description": (
                "Confirms and creates a reservation. Only call when you have: "
                "customer_id, table_id, date, time, and party_size confirmed by the guest."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "customer_id":      {"type": "string", "description": "UUID from find_or_create_customer"},
                    "table_id":         {"type": "string", "description": "UUID from check_table_availability"},
                    "reservation_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "start_time":       {"type": "string", "description": "HH:MM"},
                    "party_size":       {"type": "integer"},
                    "special_requests": {"type": "string"},
                },
                "required": ["customer_id","table_id","reservation_date","start_time","party_size"],
            },
        },
        {
            "name": "get_reservation",
            "description": "Retrieves an existing reservation by confirmation code.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "confirmation_code": {"type": "string", "description": "e.g. RES-A3X9K2"},
                },
                "required": ["confirmation_code"],
            },
        },
        {
            "name": "modify_reservation",
            "description": (
                "Modifies an existing reservation's date, time, or party size. "
                "Requires confirmation code and the email used at booking."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "confirmation_code": {"type": "string"},
                    "customer_email":    {"type": "string"},
                    "new_date":          {"type": "string", "description": "YYYY-MM-DD"},
                    "new_time":          {"type": "string", "description": "HH:MM"},
                    "new_party_size":    {"type": "integer"},
                },
                "required": ["confirmation_code", "customer_email"],
            },
        },
        {
            "name": "cancel_reservation",
            "description": "Cancels a confirmed reservation after email verification.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "confirmation_code": {"type": "string"},
                    "customer_email":    {"type": "string"},
                },
                "required": ["confirmation_code", "customer_email"],
            },
        },
        {
            "name": "send_confirmation",
            "description": (
                "Sends email/SMS confirmation after booking or modification. "
                "Always call this immediately after a successful book_table."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reservation_id":    {"type": "string"},
                    "confirmation_code": {"type": "string"},
                    "customer_name":     {"type": "string"},
                    "customer_email":    {"type": "string"},
                    "customer_phone":    {"type": "string"},
                    "reservation_date":  {"type": "string"},
                    "reservation_time":  {"type": "string"},
                    "party_size":        {"type": "integer"},
                    "table_number":      {"type": "string"},
                    "special_requests":  {"type": "string"},
                },
                "required": [
                    "reservation_id","confirmation_code","customer_name",
                    "reservation_date","reservation_time","party_size",
                ],
            },
        },
    ]


# =============================================================================
# Tool Executor
# =============================================================================

async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    ctx: AgentContext,
) -> str:
    """
    Executes a tool call from Claude, injecting restaurant_id from the
    server-side AgentContext rather than trusting Claude's output.

    SECURITY: restaurant_id ALWAYS comes from ctx.restaurant_id.
    Any restaurant_id in tool_input is stripped and ignored.
    """
    # Strip any restaurant_id Claude might have hallucinated into the call
    tool_input.pop("restaurant_id", None)

    rid = ctx.restaurant_id  # Always authoritative
    logger.info(f"[{rid}] Tool call: {tool_name} | {tool_input}")

    def _safe(d: Dict) -> Dict:
        """Serialize UUIDs and date/time types to JSON-safe strings."""
        return {
            k: str(v) if isinstance(v, (UUID, date, time, datetime)) else v
            for k, v in d.items()
        }

    try:
        # ── get_available_time_slots ──────────────────────────────────────
        if tool_name == "get_available_time_slots":
            slots = await get_available_time_slots(
                restaurant_id=rid,
                reservation_date=date.fromisoformat(tool_input["reservation_date"]),
                party_size=tool_input["party_size"],
                preference=tool_input.get("preference", "no_preference"),
            )
            if not slots:
                return json.dumps({"available": False, "message": "No availability on that date."})
            return json.dumps({"available": True, "slots": slots})

        # ── check_table_availability ──────────────────────────────────────
        elif tool_name == "check_table_availability":
            tables = await check_availability(
                restaurant_id=rid,
                reservation_date=date.fromisoformat(tool_input["reservation_date"]),
                start_time=time.fromisoformat(tool_input["start_time"]),
                party_size=tool_input["party_size"],
                preference=tool_input.get("preference", "no_preference"),
            )
            if not tables:
                return json.dumps({"available": False, "tables": []})
            return json.dumps({"available": True, "tables": [_safe(t) for t in tables]})

        # ── find_or_create_customer ───────────────────────────────────────
        elif tool_name == "find_or_create_customer":
            customer = await find_or_create_customer(restaurant_id=rid, **tool_input)
            safe = _safe(customer)
            # Return only fields Claude needs; strip sensitive internals
            return json.dumps({
                "success": True,
                "customer": {
                    "id":            safe["id"],
                    "full_name":     safe["full_name"],
                    "email":         safe.get("email"),
                    "phone":         safe.get("phone"),
                    "visit_count":   safe.get("visit_count", 0),
                    "allergy_notes": safe.get("allergy_notes"),
                },
            })

        # ── book_table ────────────────────────────────────────────────────
        elif tool_name == "book_table":
            reservation = await book_table(
                restaurant_id=rid,
                customer_id=UUID(tool_input["customer_id"]),
                table_id=UUID(tool_input["table_id"]),
                reservation_date=date.fromisoformat(tool_input["reservation_date"]),
                start_time=time.fromisoformat(tool_input["start_time"]),
                party_size=tool_input["party_size"],
                special_requests=tool_input.get("special_requests"),
            )
            safe = _safe(reservation)
            return json.dumps({"success": True, "reservation": safe})

        # ── get_reservation ───────────────────────────────────────────────
        elif tool_name == "get_reservation":
            reservation = await get_reservation_by_code(
                rid, tool_input["confirmation_code"]
            )
            if not reservation:
                return json.dumps({"found": False})
            return json.dumps({"found": True, "reservation": _safe(reservation)})

        # ── modify_reservation ────────────────────────────────────────────
        elif tool_name == "modify_reservation":
            kwargs: Dict[str, Any] = {
                "restaurant_id":  rid,
                "code":           tool_input["confirmation_code"],
                "customer_email": tool_input["customer_email"],
            }
            if "new_date" in tool_input:
                kwargs["new_date"] = date.fromisoformat(tool_input["new_date"])
            if "new_time" in tool_input:
                kwargs["new_time"] = time.fromisoformat(tool_input["new_time"])
            if "new_party_size" in tool_input:
                kwargs["new_party_size"] = tool_input["new_party_size"]

            new_res = await modify_reservation(**kwargs)
            return json.dumps({"success": True, "new_reservation": _safe(new_res)})

        # ── cancel_reservation ────────────────────────────────────────────
        elif tool_name == "cancel_reservation":
            result = await cancel_reservation(
                restaurant_id=rid,
                code=tool_input["confirmation_code"],
                customer_email=tool_input["customer_email"],
            )
            return json.dumps({"success": True, "cancelled_code": result["confirmation_code"]})

        # ── send_confirmation ─────────────────────────────────────────────
        elif tool_name == "send_confirmation":
            email_sent = sms_sent = False
            if tool_input.get("customer_email"):
                email_sent = await send_confirmation_email(
                    restaurant_id=rid,
                    restaurant_name=ctx.restaurant_name,
                    **tool_input,
                )
            if tool_input.get("customer_phone"):
                sms_sent = await send_confirmation_sms(
                    restaurant_id=rid,
                    **tool_input,
                )
            return json.dumps({"email_sent": email_sent, "sms_sent": sms_sent})

        else:
            logger.warning(f"Unknown tool requested: {tool_name}")
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except ValueError as e:
        logger.warning(f"[{rid}] Tool {tool_name} business error: {e}")
        return json.dumps({"error": str(e), "type": "business_error"})
    except Exception as e:
        logger.exception(f"[{rid}] Tool {tool_name} unexpected error: {e}")
        return json.dumps({"error": "Unexpected error. Please try again.", "type": "system_error"})


def _update_booking_context(
    ctx: AgentContext,
    tool_name: str,
    tool_input: Dict,
    result_str: str,
) -> None:
    """Extracts booking progress from tool results into the context dict."""
    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        return

    if tool_name == "find_or_create_customer" and result.get("success"):
        c = result.get("customer", {})
        ctx.booking_context.update({
            "customer_id":    c.get("id"),
            "customer_name":  c.get("full_name"),
            "customer_email": c.get("email"),
            "customer_phone": c.get("phone"),
        })

    elif tool_name == "book_table" and result.get("success"):
        r = result.get("reservation", {})
        ctx.booking_context.update({
            "reservation_id":    r.get("id"),
            "confirmation_code": r.get("confirmation_code"),
            "booking_complete":  True,
        })

    elif tool_name in ("get_available_time_slots", "check_table_availability"):
        ctx.booking_context.update({
            "reservation_date": tool_input.get("reservation_date"),
            "party_size":       tool_input.get("party_size"),
        })


# =============================================================================
# Main Agent Loop
# =============================================================================

async def run_agent(
        user_message: str,
        ctx: AgentContext,
) -> Tuple[str, AgentContext]:
    """
    Runs one full conversational turn for a specific restaurant tenant.
    """
    ctx.conversation_history.append({"role": "user", "content": user_message})

    system_prompt = build_system_prompt(ctx)
    tools = build_tools(ctx)
    final_reply = ""

    MAX_TURNS = 10

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            system=system_prompt,
            tools=tools,
            messages=ctx.conversation_history,
        )

        # 🚨 FIX: Serialize SDK objects to plain dicts so they survive
        # JSON round-tripping through PostgreSQL JSONB.
        # Without this, TextBlock/ToolUseBlock objects get turned into
        # repr strings by json.dumps(default=str), causing:
        #   messages.N.content.M: Input should be an object
        assistant_content = response.content
        ctx.conversation_history.append({
            "role": "assistant",
            "content": [_serialize_content_block(b) for b in assistant_content],
        })

        if response.stop_reason == "end_turn":
            for block in assistant_content:
                if hasattr(block, "text"):
                    final_reply = block.text
            break

        elif response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    result_str = await execute_tool(block.name, block.input, ctx)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
                    _update_booking_context(ctx, block.name, block.input, result_str)

            ctx.conversation_history.append({
                "role": "user",
                "content": tool_results,
            })
        else:
            for block in assistant_content:
                if hasattr(block, "text"):
                    final_reply = block.text
            break
    else:
        # If the loop hits 10 turns without breaking, force a polite exit
        final_reply = "I apologize, but I am having trouble completing this request right now. Please call the restaurant directly so we can assist you."

    # 🚨 FIX: Make sure the function actually returns the result at the very end!
    return final_reply, ctx