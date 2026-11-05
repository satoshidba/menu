"""FastAPI application: JSON API under /api/v1 (OpenAPI at /api/docs). The PWA is served by Caddy."""
import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from . import models  # noqa: F401
from .api import accounts, calendar, catalog, health, insights, meals, pantry, planning, receipts, recipes, system
from .api.common import IdempotencyMiddleware
from .services import receipts as _receipt_jobs  # noqa: F401  (registers job handlers)
from .services import recipe_jobs as _recipe_jobs  # noqa: F401
from .worker import register_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class RedactTokens(logging.Filter):
    """Calendar feeds carry a token in the URL; never write tokens to logs."""

    pattern = re.compile(r"lrd_[A-Za-z0-9_\-]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(self.pattern.sub("lrd_[redacted]", a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self.pattern.sub("lrd_[redacted]", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error", "larder", "httpx"):
    logging.getLogger(_name).addFilter(RedactTokens())
register_handlers()

app = FastAPI(
    title="Larder API",
    version="1.0.0",
    description=(
        "Household recipes, pantry, receipts, planning and nutrition. Authenticate with the session cookie (browser) "
        "or `Authorization: Bearer lrd_...` (scoped API token). Mutations accept an `Idempotency-Key` header. "
        "Timestamps are ISO 8601; amounts are metric; money is integer pence (GBP)."
    ),
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
app.add_middleware(IdempotencyMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(DBAPIError)
async def db_error(request: Request, exc: DBAPIError):
    # Never echo SQL or parameters (may include receipt contents) back to clients or logs.
    code = getattr(getattr(exc, "orig", None), "sqlstate", None)
    logging.getLogger("larder").error("database error %s on %s %s", code, request.method, request.url.path)
    if code == "23514":  # check_violation, e.g. immutable published version
        return JSONResponse({"detail": "That change isn't allowed for this record."}, 409)
    if code == "23505":
        return JSONResponse({"detail": "That already exists."}, 409)
    return JSONResponse({"detail": "Database error"}, 500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse({"detail": "Please check the highlighted fields.", "errors": errors}, 422)


for r in (accounts, catalog, pantry, recipes, receipts, planning, meals, health, calendar, insights, system):
    app.include_router(r.router)
