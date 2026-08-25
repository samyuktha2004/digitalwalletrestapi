"""One user must never reach another user's wallet.

No endpoint takes a wallet id — every route derives the wallet from the
authenticated user — so the attack surface is the token itself. These tests
cover both halves: correct tokens stay scoped, forged tokens are refused.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
from httpx import AsyncClient

from app.core.config import settings
from app.core.crypto import encrypt
from tests.conftest import make_user


async def test_each_user_sees_only_their_own_balance(client: AsyncClient) -> None:
    alice = await make_user(client, "alice", balance="500.00")
    bob = await make_user(client, "bob", balance="10.00")

    assert Decimal((await client.get("/wallet", headers=alice)).json()["balance"]) == Decimal("500.00")
    assert Decimal((await client.get("/wallet", headers=bob)).json()["balance"]) == Decimal("10.00")


async def test_ledger_is_scoped_to_the_caller(client: AsyncClient) -> None:
    alice = await make_user(client, "alice", balance="500.00")
    bob = await make_user(client, "bob", balance="10.00")

    payload = encrypt('{"recipient_username": "bob", "amount": "40.00"}', settings.aes_key)
    assert (await client.post("/wallet/transfer", json={"payload": payload}, headers=alice)).status_code == 200

    alice_rows = (await client.get("/wallet/transactions", headers=alice)).json()
    bob_rows = (await client.get("/wallet/transactions", headers=bob)).json()

    # Alice sees her own deposit and the outgoing leg; Bob sees his own deposit
    # and the incoming leg. Neither sees the other's DEPOSIT row.
    assert {r["type"] for r in alice_rows} == {"DEPOSIT", "TRANSFER_OUT"}
    assert {r["type"] for r in bob_rows} == {"DEPOSIT", "TRANSFER_IN"}
    assert len(alice_rows) == 2 and len(bob_rows) == 2

    # The ledger deliberately does not expose wallet_id, so the proof of scoping
    # is that the two ledgers share no transaction rows at all.
    assert {r["id"] for r in alice_rows}.isdisjoint({r["id"] for r in bob_rows})
    assert Decimal(next(r["amount"] for r in alice_rows if r["type"] == "TRANSFER_OUT")) == Decimal("40.00")
    assert Decimal(next(r["amount"] for r in bob_rows if r["type"] == "TRANSFER_IN")) == Decimal("40.00")


async def test_token_signed_with_another_key_is_refused(client: AsyncClient) -> None:
    """The whole scheme rests on the signature — prove an attacker-minted one fails."""
    victim = await make_user(client, "victim", balance="900.00")
    subject = jwt.decode(
        victim["Authorization"].removeprefix("Bearer "),
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )["sub"]

    forged = jwt.encode(
        {"sub": subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "attacker-guessed-this-secret",
        algorithm=settings.jwt_algorithm,
    )
    r = await client.get("/wallet", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401, r.text


async def test_alg_none_token_is_refused(client: AsyncClient) -> None:
    """Classic JWT bypass: strip the signature and claim alg=none."""
    await make_user(client, "victim", balance="900.00")
    unsigned = jwt.encode({"sub": "whoever"}, key="", algorithm="none")
    r = await client.get("/wallet", headers={"Authorization": f"Bearer {unsigned}"})
    assert r.status_code == 401, r.text


async def test_valid_token_for_a_vanished_user_is_refused(client: AsyncClient) -> None:
    """A correctly signed token whose subject no longer exists must not authenticate."""
    ghost = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    r = await client.get("/wallet", headers={"Authorization": f"Bearer {ghost}"})
    assert r.status_code == 401, r.text


async def test_expired_token_is_refused(client: AsyncClient) -> None:
    await make_user(client, "victim", balance="900.00")
    expired = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    r = await client.get("/wallet", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401, r.text


async def test_cannot_transfer_from_someone_elses_wallet(client: AsyncClient) -> None:
    """The sender is taken from the token, never from the payload."""
    await make_user(client, "alice", balance="500.00")
    mallory = await make_user(client, "mallory", balance="0.00")

    # Mallory names Alice as the source in the plaintext she encrypts. The
    # schema has no sender field, so the extra key must not create one.
    payload = encrypt(
        '{"sender_username": "alice", "recipient_username": "mallory", "amount": "500.00"}',
        settings.aes_key,
    )
    r = await client.post("/wallet/transfer", json={"payload": payload}, headers=mallory)
    assert r.status_code == 400, r.text  # debits Mallory (empty), not Alice

    alice = await make_user(client, "alice2")  # noqa: F841 - keep fixture symmetry
    r = await client.get("/wallet", headers=mallory)
    assert Decimal(r.json()["balance"]) == Decimal("0.00")
