# Secure Digital Wallet REST API

Async FastAPI wallet service with PostgreSQL row-level locking, JWT auth, and
AES-256-GCM encrypted transfer payloads.

The point of the project is the concurrency guarantee: **a balance can never be
spent twice, and money is never lost**, under simultaneous requests. Everything
else exists to make that testable.

---

## How to Use

1. Start the app using the [Quick start](#quick-start) commands below.
2. Open the demo console at <http://localhost:8000/>.
3. Register a new user, or log in with an existing account. Use a fresh username
  if the account has already been created.
4. Deposit `1000.00`, then try a withdrawal. Refresh the wallet to see the
  balance and transaction ledger update.
5. Register a second user in another browser window or after logging out. Log
  back in as the first user, enter the second username and an amount, then use
  **Encrypt & Send** to exercise the AES-256-GCM transfer flow.
6. Use **Simulate Race Condition** to set the balance to `1000.00` and send two
  `800.00` withdrawals simultaneously. The expected result is one successful
  request, one insufficient-funds response, and a final balance of `200.00`.

For the API-only walkthrough, open <http://localhost:8000/docs> for Swagger UI.
The automated verification command is `pytest -q`; it covers authentication,
validation, encrypted transfers, tamper rejection, concurrency, and deadlock
prevention.

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

### Refused debits are kept

A rejected withdrawal or transfer writes a `FAILED` row to the ledger — repeated
insufficient-funds attempts are exactly what an audit trail is for.

The catch is that the audit row cannot ride along on the caller's transaction:
raising rolls that back and takes the row with it. It does not need a second
session either. Everything the refusal path did was a locked *read*, so
`rollback()` discards no work — it just releases the locks — and the audit row
then commits in its own fresh transaction (`_record_refusal`). One consequence
worth knowing: rollback expires every ORM object, so the wallet id must be read
into an argument before the rollback rather than after it.

The concurrency test asserts this holds under contention — of two simultaneous
₹800 withdrawals against ₹1000, the ledger ends with exactly one `SUCCESS` and
exactly one `FAILED`.

---

## Payload encryption

`POST /wallet/transfer` accepts only `{"payload": "<b64 iv>:<b64 ciphertext>"}`.
A FastAPI **dependency** decrypts it and validates the plaintext into the real
`TransferIn` schema before the route handler runs
([`deps.py`](app/routers/deps.py)). See *Middleware vs. dependencies* below for
why this one is not middleware.

**GCM, not CBC.** GCM is authenticated: a tampered ciphertext fails the tag
check and returns `400` instead of decrypting into attacker-influenced garbage.
CBC without a separate MAC is a padding-oracle waiting to happen.

This encrypts the *payload*, not the transport — it is defence in depth behind
TLS (protecting the body from intermediate proxies and logs), not a replacement
for it.

---

## Middleware vs. dependencies

The two are split on one line: **does it need to run before routing?**

`app/core/middleware.py` runs before a route is matched, and holds the two
things that only work there:

- **Body-size cap** — rejects a request over 64 KB with `413` *before* anything
  reads the body. A dependency cannot do this; by the time one runs, the body
  has already been received.
- **Request id** — `X-Request-ID` is accepted from the caller or generated,
  echoed on every response including errors, and tagged onto one access log line
  per request (`rid=... POST /wallet/withdraw -> 400 (2.6ms)`). It has to exist
  before the first log line, so it cannot come from a dependency either.

The log records method, path, status and duration — never the body or the
`Authorization` header, which is the usual way credentials end up in a log
aggregator.

Authentication and payload decryption are the opposite case and stay as
dependencies in `app/routers/deps.py`: they are per-route, they need the request
DB session, and as dependencies they appear in `/docs` and return proper `401`s
through FastAPI's own machinery. Doing them as middleware would mean re-parsing
the path to decide what to protect — how you get an endpoint accidentally left
unauthenticated.

---

## Tests

```bash
createdb -O wallet wallet_test     # once
pytest -q
```

```
24 passed in 11.03s
```

**PostgreSQL is required.** There is deliberately no SQLite fallback: SQLite has
no `SELECT ... FOR UPDATE`, so the suite would pass while proving nothing.

`tests/test_concurrency.py` — the mandatory scenario and two more:

| Test | Asserts |
|---|---|
| `test_concurrent_withdrawals_only_one_succeeds` | seed ₹1000, two simultaneous ₹800 withdrawals via `asyncio.gather` → exactly one `200` and one `400`, final balance exactly ₹200, and a ledger holding exactly one `SUCCESS` and one `FAILED` withdrawal |
| `test_bidirectional_transfers_do_not_deadlock` | simultaneous A→B and B→A both succeed (lock ordering) and money is conserved |
| `test_concurrent_deposits_do_not_lose_money` | ten simultaneous ₹10 deposits total exactly ₹100 |

`tests/test_isolation.py` — *"users must not be able to access another user's
wallet"*. No endpoint accepts a wallet id (every route derives the wallet from
the authenticated user), so the attack surface is the token. It tests both
halves: balances and ledgers stay scoped to their owner, and forged tokens are
refused — wrong signing key, the `alg=none` bypass, expired, and a correctly
signed token whose subject no longer exists. It also confirms a sender cannot be
injected through the encrypted payload: the debited wallet always comes from the
token.

`tests/test_failed_audit.py` checks refused debits are persisted as `FAILED`
without moving money, and that the recipient of a refused transfer gets no row
at all. `tests/test_middleware.py` covers the `413` cap and request-id echo.

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
             middleware.py (body cap, request id, access log)
  db/        base.py  session.py (async engine, per-request session)
  models/    user.py  wallet.py  transaction.py
  schemas/   auth.py  wallet.py
  routers/   auth.py  wallet.py  deps.py (auth + payload decryption)
  services/  auth_service.py  wallet_service.py  errors.py
  static/    index.html
  main.py
scripts/     encrypt_payload.py
tests/       conftest.py  test_concurrency.py  test_wallet.py
             test_isolation.py  test_failed_audit.py  test_middleware.py
```

Services never import FastAPI. They raise `WalletError` subclasses carrying an
HTTP status, and `main.py` installs one handler that renders them as JSON.

---

## Known limitations

- **`create_all`, not migrations.** Fine for a fresh schema; add Alembic before
  the first production schema change.
- **No rate limiting or refresh tokens.** Access tokens are 60 min and
  non-revocable; a real deployment needs a denylist or short-lived tokens plus
  refresh.
- **Login is not constant-time for unknown usernames** — a missing user skips the
  bcrypt comparison, which is measurable. Hash a dummy digest to close it.
