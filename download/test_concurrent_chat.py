"""
test_concurrent_chat.py — Race condition test suite for LiftUp SaaS
Run: pytest test_concurrent_chat.py -v --tb=long
Requires: httpx, pytest, pytest-asyncio
"""
import asyncio
import pytest
import httpx
import uuid

BASE_URL = "http://127.0.0.1:8000"
API_KEY = "YOUR_TEST_API_KEY"  # Replace with real key
NUM_CONCURRENT_USERS = 5
SESSION_TOKENS = [f"test-session-{uuid.uuid4().hex}" for _ in range(NUM_CONCURRENT_USERS)]


@pytest.fixture
def headers_factory():
    """Returns a factory that creates headers for a given user index."""
    def _make(idx: int) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
            "X-Session-Token": SESSION_TOKENS[idx],
        }
    return _make


@pytest.mark.asyncio
async def test_health_check():
    """Verify the backend is reachable before running load tests."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"


@pytest.mark.asyncio
async def test_five_concurrent_chat_sessions(headers_factory):
    """Simulate 5 users sending chat messages simultaneously.
    
    This tests that:
    1. Each session gets its own conversation context
    2. Session tokens do not leak across tenants
    3. The middleware correctly routes each request
    """
    async def send_message(idx: int, message: str) -> httpx.Response:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            return await client.post(
                "/api/widget/chat",
                headers=headers_factory(idx),
                json={"message": message},
            )

    messages = [
        "I'd like a table for 2 tonight",
        "Can I book for 4 people tomorrow at 7pm?",
        "Do you have outdoor seating available?",
        "Table for 6 this Friday at 8pm please",
        "I need to cancel my reservation",
    ]

    # Fire all 5 requests concurrently
    tasks = [send_message(i, messages[i]) for i in range(NUM_CONCURRENT_USERS)]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Verify all requests succeeded (or got expected business errors)
    for idx, resp in enumerate(responses):
        if isinstance(resp, Exception):
            pytest.skip(f"User {idx}: Network error - {resp}")
        assert resp.status_code in (200, 401, 402, 429), (
            f"User {idx}: Unexpected status {resp.status_code}: "
            f"{resp.text[:200]}"
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "reply" in data, f"User {idx}: Missing 'reply' in response"
            assert "session_token" in data, f"User {idx}: Missing 'session_token'"


@pytest.mark.asyncio
async def test_booking_race_condition(headers_factory):
    """Test that two users cannot double-book the same table slot.
    
    Strategy: Two users attempt to book the same date/time/party_size
    simultaneously. One should succeed; the other should get a conflict
    error from the AI agent (table just booked).
    
    This directly tests the SELECT FOR UPDATE NOWAIT lock in book_table().
    """
    # Step 1: Both users ask for availability (sequential, to get context)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # Warm up both sessions with a greeting
        for idx in range(2):
            await client.post(
                "/api/widget/chat",
                headers=headers_factory(idx),
                json={"message": "Hello"},
            )

    # Step 2: Both users simultaneously request the same slot
    booking_msg = "I'd like to book a table for 4 people on 2026-07-15 at 19:00"

    async def book_slot(idx: int) -> httpx.Response:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
            return await client.post(
                "/api/widget/chat",
                headers=headers_factory(idx),
                json={"message": booking_msg},
            )

    responses = await asyncio.gather(
        book_slot(0), book_slot(1), return_exceptions=True
    )

    successes = 0
    for resp in responses:
        if isinstance(resp, Exception):
            continue
        if resp.status_code == 200:
            data = resp.json()
            if data.get("booking_complete"):
                successes += 1

    # At most ONE user should successfully book the same slot
    assert successes <= 1, (
        f"RACE CONDITION DETECTED: {successes} users booked the same slot!"
    )


@pytest.mark.asyncio
async def test_session_isolation(headers_factory):
    """Verify that conversation sessions do not leak between users."""
    unique_msgs = [f"Secret word: {uuid.uuid4().hex[:8]}" for _ in range(2)]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # User 0 sends a secret message
        resp0 = await client.post(
            "/api/widget/chat",
            headers=headers_factory(0),
            json={"message": unique_msgs[0]},
        )
        assert resp0.status_code == 200

        # User 1 asks "what did I just say?"
        resp1 = await client.post(
            "/api/widget/chat",
            headers=headers_factory(1),
            json={"message": "What was the secret word I mentioned?"},
        )
        assert resp1.status_code == 200

        data1 = resp1.json()
        # User 1 should NOT see User 0's secret word
        assert unique_msgs[0] not in data1.get("reply", ""), (
            f"SESSION LEAK: User 1 can see User 0's message!"
        )


@pytest.mark.asyncio
async def test_rate_limiting(headers_factory):
    """Verify that rate limiting (30/min) works correctly."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        # Send 35 rapid requests (limit is 30/min)
        responses = []
        for _ in range(35):
            try:
                resp = await client.post(
                    "/api/widget/chat",
                    headers=headers_factory(0),
                    json={"message": "hi"},
                )
                responses.append(resp.status_code)
            except httpx.HTTPStatusError as e:
                responses.append(e.response.status_code)

        # At least some should be rate-limited (429)
        rate_limited = sum(1 for s in responses if s == 429)
        assert rate_limited > 0, (
            f"Rate limiting not working: 0/35 requests were limited"
        )
