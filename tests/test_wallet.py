"""Auth, validation and the encrypted-transfer path."""

from decimal import Decimal

from httpx import AsyncClient

from app.core.config import settings
from app.core.crypto import DecryptionError, decrypt, encrypt
from tests.conftest import make_user


def test_crypto_roundtrip_and_tamper_detection() -> None:
    key = settings.aes_key
    token = encrypt('{"hello": "world"}', key)
    assert decrypt(token, key) == '{"hello": "world"}'

    iv, ciphertext = token.split(":", 1)
    flipped = ciphertext[:-6] + ("A" if ciphertext[-6] != "A" else "B") + ciphertext[-5:]
    for bad in (f"{iv}:{flipped}", "not-a-payload", f"{iv}:", encrypt("x", b"\x00" * 32)):
        try:
            decrypt(bad, key)
        except DecryptionError:
            continue
        raise AssertionError(f"tampered payload was accepted: {bad!r}")


async def test_wallet_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/wallet")).status_code == 401
    assert (
        await client.get("/wallet", headers={"Authorization": "Bearer garbage"})
    ).status_code == 401


async def test_amounts_must_be_positive(client: AsyncClient) -> None:
    headers = await make_user(client, "validator", balance="100.00")
    for bad in ("0", "-5.00", "0.001"):
        r = await client.post("/wallet/deposit", json={"amount": bad}, headers=headers)
        assert r.status_code == 422, f"{bad} was accepted"


async def test_encrypted_transfer_moves_money(client: AsyncClient) -> None:
    sender = await make_user(client, "sender", balance="300.00")
    receiver = await make_user(client, "receiver")

    body = '{"recipient_username": "receiver", "amount": "120.50"}'
    r = await client.post(
        "/wallet/transfer",
        json={"payload": encrypt(body, settings.aes_key)},
        headers=sender,
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["balance"]) == Decimal("179.50")
    assert Decimal((await client.get("/wallet", headers=receiver)).json()["balance"]) == Decimal(
        "120.50"
    )

    out = (await client.get("/wallet/transactions", headers=sender)).json()
    assert out[0]["type"] == "TRANSFER_OUT"
    incoming = (await client.get("/wallet/transactions", headers=receiver)).json()
    assert incoming[0]["type"] == "TRANSFER_IN"


async def test_plaintext_transfer_is_rejected(client: AsyncClient) -> None:
    sender = await make_user(client, "plaintext", balance="300.00")
    r = await client.post(
        "/wallet/transfer",
        json={"recipient_username": "someone", "amount": "10.00"},
        headers=sender,
    )
    assert r.status_code == 422  # no "payload" field


async def test_transfer_with_wrong_key_is_rejected(client: AsyncClient) -> None:
    sender = await make_user(client, "attacker", balance="300.00")
    forged = encrypt('{"recipient_username": "x", "amount": "1.00"}', b"\x11" * 32)
    r = await client.post("/wallet/transfer", json={"payload": forged}, headers=sender)
    assert r.status_code == 400


async def test_overdraw_is_refused(client: AsyncClient) -> None:
    headers = await make_user(client, "broke", balance="50.00")
    r = await client.post("/wallet/withdraw", json={"amount": "50.01"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "Insufficient funds"


async def test_duplicate_username_is_rejected(client: AsyncClient) -> None:
    await make_user(client, "dupe")
    r = await client.post("/auth/register", json={"username": "dupe", "password": "password123"})
    assert r.status_code == 409
