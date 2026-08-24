"""The point of the whole project: money cannot be spent twice.

Requires PostgreSQL — row locks are what is under test.
"""

import asyncio
from decimal import Decimal

from httpx import AsyncClient

from tests.conftest import make_user


async def test_concurrent_withdrawals_only_one_succeeds(client: AsyncClient) -> None:
    """Wallet holds 1000. Two simultaneous 800 withdrawals. Exactly one wins."""
    headers = await make_user(client, "racer", balance="1000.00")

    first, second = await asyncio.gather(
        client.post("/wallet/withdraw", json={"amount": "800.00"}, headers=headers),
        client.post("/wallet/withdraw", json={"amount": "800.00"}, headers=headers),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400], (
        f"expected one 200 and one 400, got {first.status_code} and {second.status_code}: "
        f"{first.text} | {second.text}"
    )
    loser = first if first.status_code == 400 else second
    assert loser.json()["detail"] == "Insufficient funds"

    balance = await client.get("/wallet", headers=headers)
    assert Decimal(balance.json()["balance"]) == Decimal("200.00")

    # And the ledger agrees with the balance: one withdrawal was recorded, not two.
    txs = (await client.get("/wallet/transactions", headers=headers)).json()
    withdrawals = [t for t in txs if t["type"] == "WITHDRAWAL"]
    assert len(withdrawals) == 1


async def test_bidirectional_transfers_do_not_deadlock(client: AsyncClient) -> None:
    """A->B and B->A at the same moment. Consistent lock ordering means both
    complete instead of one dying on a Postgres deadlock detection."""
    from app.core.config import settings
    from app.core.crypto import encrypt

    alice = await make_user(client, "alice", balance="500.00")
    bob = await make_user(client, "bob", balance="500.00")

    def payload(recipient: str, amount: str) -> dict[str, str]:
        body = f'{{"recipient_username": "{recipient}", "amount": "{amount}"}}'
        return {"payload": encrypt(body, settings.aes_key)}

    a_to_b, b_to_a = await asyncio.gather(
        client.post("/wallet/transfer", json=payload("bob", "100.00"), headers=alice),
        client.post("/wallet/transfer", json=payload("alice", "100.00"), headers=bob),
    )

    assert a_to_b.status_code == 200, a_to_b.text
    assert b_to_a.status_code == 200, b_to_a.text

    # Money is conserved and both ended where they started.
    assert Decimal((await client.get("/wallet", headers=alice)).json()["balance"]) == Decimal("500.00")
    assert Decimal((await client.get("/wallet", headers=bob)).json()["balance"]) == Decimal("500.00")


async def test_concurrent_deposits_do_not_lose_money(client: AsyncClient) -> None:
    """Ten simultaneous deposits of 10 must total exactly 100, not less."""
    headers = await make_user(client, "depositor")

    results = await asyncio.gather(
        *[
            client.post("/wallet/deposit", json={"amount": "10.00"}, headers=headers)
            for _ in range(10)
        ]
    )
    assert all(r.status_code == 200 for r in results)

    balance = await client.get("/wallet", headers=headers)
    assert Decimal(balance.json()["balance"]) == Decimal("100.00")
