"""The pre-routing middleware: body cap and request id."""

import json

from httpx import AsyncClient

from app.core.middleware import MAX_BODY_BYTES


async def test_oversized_body_is_rejected_before_routing(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/register",
        content=json.dumps({"username": "x" * (MAX_BODY_BYTES + 100), "password": "p"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"] == "Request body too large"


async def test_request_id_is_generated_and_echoed(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.headers["X-Request-ID"]

    r = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})
    assert r.headers["X-Request-ID"] == "trace-me-123"


async def test_request_id_is_present_on_error_responses(client: AsyncClient) -> None:
    """The id is most useful on the responses you actually go looking for."""
    r = await client.get("/wallet")
    assert r.status_code == 401
    assert r.headers["X-Request-ID"]
