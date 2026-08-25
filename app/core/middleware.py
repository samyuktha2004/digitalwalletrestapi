"""Cross-cutting request middleware.

Why this is middleware when auth and payload decryption are dependencies: both
of these run *before* routing. A body-size cap is only useful if it rejects the
request before anything reads the body, and a request id has to exist before the
first log line — neither is expressible as a dependency, which FastAPI resolves
only after a route has already matched.

Auth and decryption are the opposite case: they are per-route, need the DB
session, and belong in `/docs`. Those stay dependencies (`app/routers/deps.py`).
"""

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger("wallet.access")

# Every legitimate request here is a small JSON object; the largest is an
# encrypted transfer payload, which is a few hundred bytes.
MAX_BODY_BYTES = 64 * 1024


async def request_guard(request: Request, call_next: ASGIApp) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id

    # ponytail: Content-Length catches the ordinary oversized body. A hostile
    # client using chunked encoding sends no length, so a hard cap belongs at
    # the server or proxy (uvicorn --limit-concurrency, nginx
    # client_max_body_size). This is the cheap 99% guard, not the whole story.
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        logger.warning("rid=%s %s %s rejected: body %s bytes", request_id, request.method, request.url.path, declared)
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
            headers={"X-Request-ID": request_id},
        )

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Request-ID"] = request_id
    # Path only, never the body or the Authorization header: request logs are the
    # classic place credentials and payloads leak into disk and log aggregators.
    logger.info(
        "rid=%s %s %s -> %s (%.1fms)",
        request_id, request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response
