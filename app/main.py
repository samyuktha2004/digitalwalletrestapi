import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.middleware import request_guard
from app.db.base import Base
from app.db.session import engine
from app.routers import auth, wallet
from app.services.errors import WalletError

STATIC_DIR = Path(__file__).parent / "static"

# Uvicorn only configures its own loggers, so the access lines from
# app.core.middleware would be dropped at the root's default WARNING level.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ponytail: create_all is enough for a single-schema demo. Swap for Alembic
    # the first time a column changes on a database that holds real rows.
    import app.models  # noqa: F401  -- register mappers before create_all

    # Skipped on serverless: a cold start happens on any request, and running
    # DDL on each one is both slow and a race between concurrent cold starts.
    # Create the tables once instead:  CREATE_TABLES_ON_STARTUP=1 python -m app.main
    if os.getenv("CREATE_TABLES_ON_STARTUP", "1") == "1":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Secure Digital Wallet API", version="1.0.0", lifespan=lifespan)

# Runs before routing: body-size rejection and request-id tagging. Auth and
# payload decryption are per-route and stay in app/routers/deps.py.
app.middleware("http")(request_guard)


@app.exception_handler(WalletError)
async def wallet_error_handler(request: Request, exc: WalletError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(auth.router)
app.include_router(wallet.router)

# Guarded because StaticFiles() raises at import time if the directory is
# absent, which takes the whole API down. index.html is never imported, so a
# bundler that ships only traced Python files leaves it out (this is exactly
# what broke the first Vercel deploy). The API must outlive a missing demo page.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    if not (STATIC_DIR / "index.html").is_file():
        raise HTTPException(404, "Demo console not deployed; the API is at /docs")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/demo/crypto-key", tags=["meta"])
async def demo_crypto_key() -> dict[str, str]:
    """DEMO ONLY. Hands the browser the shared AES key so index.html can encrypt.

    A real client would never receive this: the payload key would be provisioned
    out of band (KMS, per-device key exchange), and TLS would already cover the
    wire. Guarded so it cannot be left on by accident in production.
    """
    if not settings.demo_mode:
        raise HTTPException(404, "Not found")
    return {"key": settings.payload_aes_key, "algorithm": "AES-256-GCM", "iv_bytes": "12"}
