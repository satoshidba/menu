"""Small async client for the Larder REST API. The MCP server holds no data and no privileges of its own: every call
uses the caller's scoped Larder API token, so the API's scopes, review rules and audit trail all apply."""
from __future__ import annotations

import os
import ssl
import uuid
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError


def _detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("detail") or ""
        extra = {k: v for k, v in detail.items() if k not in ("message", "detail")}
        return f"{msg} {extra}".strip() if extra else msg
    if isinstance(detail, list):
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)


class LarderClient:
    def __init__(self, base_url: str, token: str, verify: "bool | ssl.SSLContext" = True, timeout: float = 60.0):
        if not token:
            raise ToolError("No Larder API token. Create one in Larder: Settings → Connected apps → New token.")
        self.base = base_url.rstrip("/") + "/api/v1"
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "larder-mcp/1.0"}
        self.verify = verify
        self.timeout = timeout

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                      data: dict | None = None, files: dict | None = None, idempotent: bool = True) -> Any:
        headers = dict(self.headers)
        if method in ("POST", "PATCH", "PUT", "DELETE") and idempotent:
            headers["Idempotency-Key"] = f"mcp-{uuid.uuid4()}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        async with httpx.AsyncClient(verify=self.verify, timeout=self.timeout, trust_env=False) as c:
            try:
                r = await c.request(method, self.base + path, params=params, json=json, data=data, files=files, headers=headers)
            except httpx.HTTPError as e:
                raise ToolError(f"Couldn't reach Larder at {self.base} ({type(e).__name__}).") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            prefix = {401: "Larder rejected the token (invalid, expired or revoked)", 403: "Not allowed with this token",
                      404: "Not found", 409: "Conflict", 422: "Invalid request", 429: "Rate limited"}.get(r.status_code, f"Error {r.status_code}")
            raise ToolError(f"{prefix}: {_detail_text(detail)}")
        if "json" in r.headers.get("content-type", ""):
            return r.json()
        return r.text

    async def get(self, path: str, **params) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Any = None, **kw) -> Any:
        return await self.request("POST", path, json=json if json is not None else {}, **kw)

    async def patch(self, path: str, json: Any) -> Any:
        return await self.request("PATCH", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)


def verify_setting() -> "bool | ssl.SSLContext":
    """LARDER_CA_CERT points at Caddy's root certificate for home-network installs (trusted in addition to the system
    roots); LARDER_INSECURE=1 disables certificate checks (testing only)."""
    if os.environ.get("LARDER_INSECURE") == "1":
        return False
    ca = os.environ.get("LARDER_CA_CERT")
    if not ca:
        return True
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=ca)
    return ctx
