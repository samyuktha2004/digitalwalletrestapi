"""Refused debits must survive as FAILED rows in the ledger."""

from decimal import Decimal

from httpx import AsyncClient

from app.core.config import settings
from app.core.crypto import encrypt
from tests.conftest import make_user


async def test_refused_withdrawal_is_audited(client: AsyncClient) -> None:
    headers = await make_user(client, "broke", balance="50.00")

    r = await client.post("/wallet/withdraw", json={"amount": "50.01"}, headers=headers)
    assert r.status_code == 400

    txs = (await client.get("/wallet/transactions", headers=headers)).json()
    failed = [t for t in txs if t["status"] == "FAILED"]
    assert len(failed) == 1
    assert failed[0]["type"] == "WITHDRAWAL"
    assert Decimal(failed[0]["amount"]) == Decimal("50.01")

    # The audit row must not have moved money.
    assert Decimal((await client.get("/wallet", headers=headers)).json()["balance"]) == Decimal("50.00")


async def test_refused_transfer_is_audited_against_the_sender(client: AsyncClient) -> None:
    sender = await make_user(client, "poor", balance="5.00")
    recipient = await make_user(client, "rich", balance="0.00")

    payload = encrypt('{"recipient_username": "rich", "amount": "100.00"}', settings.aes_key)
    r = await client.post("/wallet/transfer", json={"payload": payload}, headers=sender)
    assert r.status_code == 400

    sender_txs = (await client.get("/wallet/transactions", headers=sender)).json()
    failed = [t for t in sender_txs if t["status"] == "FAILED"]
    assert len(failed) == 1 and failed[0]["type"] == "TRANSFER_OUT"

    # The recipient gets no row at all — nothing happened to their wallet.
    recipient_txs = (await client.get("/wallet/transactions", headers=recipient)).json()
    assert [t for t in recipient_txs if t["status"] == "FAILED"] == []
    assert Decimal((await client.get("/wallet", headers=recipient)).json()["balance"]) == Decimal("0.00")


async def test_successful_operations_are_still_marked_success(client: AsyncClient) -> None:
    headers = await make_user(client, "solvent", balance="100.00")
    assert (await client.post("/wallet/withdraw", json={"amount": "30.00"}, headers=headers)).status_code == 200

    txs = (await client.get("/wallet/transactions", headers=headers)).json()
    assert {t["status"] for t in txs} == {"SUCCESS"}
