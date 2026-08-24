# Secure Digital Wallet REST API

Async FastAPI wallet service with PostgreSQL row-level locking, JWT auth, and
AES-256-GCM encrypted transfer payloads.

The point of the project is the concurrency guarantee: **a balance can never be
spent twice, and money is never lost**, under simultaneous requests. Everything
else exists to make that testable.

---

## Stack

| Concern | Choice |
|---|---|
| API | FastAPI 0.115 (Pydantic v2) |
| ORM | SQLAlchemy 2.0 async (`Mapped[...]` style) |
| Driver | asyncpg |
| DB | PostgreSQL 17 |
| Auth | bcrypt password hashing + PyJWT HS256 bearer tokens |
| Payload crypto | `cryptography` AES-256-GCM |
| Tests | pytest + pytest-asyncio + httpx `ASGITransport` |

---

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# PostgreSQL (macOS/Homebrew shown; any Postgres 14+ works)
brew install postgresql@17
brew services start postgresql@17
createuser -s wallet 2>/dev/null; createdb -O wallet wallet; createdb -O wallet wallet_test

cp .env.example .env          # then edit JWT_SECRET / PAYLOAD_AES_KEY
uvicorn app.main:app --reload
```

Open **http://localhost:8000** for the demo console, or
**http://localhost:8000/docs** for Swagger.

Tables are created on startup (`Base.metadata.create_all`). Swap in Alembic
before the schema ever changes on a database holding real rows.

### Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | must use the `postgresql+asyncpg://` driver |
| `TEST_DATABASE_URL` | separate database; the test suite **drops and recreates every table** |
| `JWT_SECRET` | `openssl rand -base64 32` |
| `PAYLOAD_AES_KEY` | base64 of exactly 32 bytes; startup fails otherwise |
| `DEMO_MODE` | exposes the AES key at `GET /demo/crypto-key` so the browser page can encrypt. **Must be `false` in production.** |

---

## API

| Method | Path | Auth | Body |
|---|---|---|---|
| `POST` | `/auth/register` | — | `{"username","password"}` → 201, creates the user's wallet |
| `POST` | `/auth/login` | — | form-encoded `username`/`password` → `{"access_token"}` |
| `GET` | `/auth/me` | Bearer | — |
| `GET` | `/wallet` | Bearer | — |
| `GET` | `/wallet/transactions?limit=50` | Bearer | — |
| `POST` | `/wallet/deposit` | Bearer | `{"amount": "100.00"}` |
| `POST` | `/wallet/withdraw` | Bearer | `{"amount": "100.00"}` → `400 Insufficient funds` |
| `POST` | `/wallet/transfer` | Bearer | **encrypted**: `{"payload": "<b64 iv>:<b64 ciphertext>"}` |

Amounts are validated by Pydantic as `Decimal(gt=0, max_digits=12,
decimal_places=2)` and stored as `NUMERIC(12,2)` — never floats.

### Making an encrypted transfer by hand

```bash
python -m scripts.encrypt_payload payee 300.00
# {"payload": "qAR8Y52BmsEmVLX6:cbKwdHY+pW7iPbpIlLAVrPd..."}

curl -X POST localhost:8000/wallet/transfer \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$(python -m scripts.encrypt_payload payee 300.00)"
```

---

## How the concurrency guarantee works

**Withdraw** takes a row lock before reading the balance, so the
read-check-write sequence is serialised against other writers:

```python
SELECT ... FROM wallets WHERE user_id = :id FOR UPDATE
```

Two details that are easy to get wrong and are the reason this works:

1. **`populate_existing=True` on every locking query.** Without it SQLAlchemy
   takes the row lock but still returns whatever copy of the row is already in
   the session's identity map — so you read a balance from *before* the lock and
   concurrent writers silently lose updates. This was a real bug during
   development: ten concurrent ₹10 deposits landed a final balance of ₹10.
   ([`wallet_service.py`](app/services/wallet_service.py))

2. **Transfers lock both wallets in ascending id order**, as two separate
   statements. Simultaneous A→B and B→A transfers otherwise each hold the row
   the other needs. It is done as two statements rather than one
   `ORDER BY ... FOR UPDATE` because Postgres does not promise it *acquires* row
   locks in the query's sort order.

Deposits lock too — `balance += amount` races exactly like a withdrawal does, it
just loses money instead of overdrawing.

Belt and braces: `CHECK (balance >= 0)` on the table, and a 5 s `lock_timeout`
on the connection so a stuck lock fails the request instead of hanging a worker.

---

## Payload encryption

`POST /wallet/transfer` accepts only `{"payload": "<b64 iv>:<b64 ciphertext>"}`.
A FastAPI **dependency** (not ASGI middleware) decrypts it and validates the
plaintext into the real `TransferIn` schema before the route handler runs
([`deps.py`](app/routers/deps.py)). A dependency rather than middleware because
middleware would have to buffer and rewrite the body for a single route, and
could not reuse FastAPI's validation or show a useful schema in `/docs`.

**GCM, not CBC.** GCM is authenticated: a tampered ciphertext fails the tag
check and returns `400` instead of decrypting into attacker-influenced garbage.
CBC without a separate MAC is a padding-oracle waiting to happen.

This encrypts the *payload*, not the transport — it is defence in depth behind
TLS (protecting the body from intermediate proxies and logs), not a replacement
for it.

---

## Tests

```bash
createdb -O wallet wallet_test     # once
pytest -q
```

```
11 passed in 4.33s
```

**PostgreSQL is required.** There is deliberately no SQLite fallback: SQLite has
no `SELECT ... FOR UPDATE`, so the suite would pass while proving nothing.

`tests/test_concurrency.py` — the mandatory scenario and two more:

| Test | Asserts |
|---|---|
| `test_concurrent_withdrawals_only_one_succeeds` | seed ₹1000, two simultaneous ₹800 withdrawals via `asyncio.gather` → exactly one `200` and one `400`, final balance exactly ₹200, and exactly one `WITHDRAWAL` row in the ledger |
| `test_bidirectional_transfers_do_not_deadlock` | simultaneous A→B and B→A both succeed (lock ordering) and money is conserved |
| `test_concurrent_deposits_do_not_lose_money` | ten simultaneous ₹10 deposits total exactly ₹100 |

`tests/test_wallet.py` covers auth, amount validation, the encrypted-transfer
round trip, tamper rejection, wrong-key rejection, and overdraw refusal.

---

## Demo frontend

`GET /` serves a single static `index.html` — no build step, no dependencies.
Login (JWT in `localStorage`), balance, ledger, deposit/withdraw, and an
encrypted transfer that does **AES-256-GCM in the browser via WebCrypto**,
producing the exact wire format the server decrypts.

The **Simulate Race Condition** button sets the balance to ₹1000, fires two
₹800 withdrawals through `Promise.all`, and renders both responses side by side
with a pass/fail verdict — the browser-visible version of the concurrency test.

The page gets its AES key from `GET /demo/crypto-key`, which is why that route
is gated behind `DEMO_MODE` and 404s otherwise. A real client would never
receive the payload key; it would be provisioned out of band (KMS, per-device
key exchange).

---

## Layout

```
app/
  core/      config.py  security.py (bcrypt + JWT)  crypto.py (AES-256-GCM)
  db/        base.py  session.py (async engine, per-request session)
  models/    user.py  wallet.py  transaction.py
  schemas/   auth.py  wallet.py
  routers/   auth.py  wallet.py  deps.py (auth + payload decryption)
  services/  auth_service.py  wallet_service.py  errors.py
  static/    index.html
  main.py
scripts/     encrypt_payload.py
tests/       conftest.py  test_concurrency.py  test_wallet.py
```

Services never import FastAPI. They raise `WalletError` subclasses carrying an
HTTP status, and `main.py` installs one handler that renders them as JSON.

---

## Known limitations

- **`create_all`, not migrations.** Fine for a fresh schema; add Alembic before
  the first production schema change.
- **Failed transactions are not persisted.** The `FAILED` enum value exists, but
  an insufficient-funds attempt rolls back its transaction, which would roll back
  the audit row with it. Writing it needs a second, independent session — worth
  doing for a real audit trail.
- **No rate limiting or refresh tokens.** Access tokens are 60 min and
  non-revocable; a real deployment needs a denylist or short-lived tokens plus
  refresh.
- **Login is not constant-time for unknown usernames** — a missing user skips the
  bcrypt comparison, which is measurable. Hash a dummy digest to close it.
