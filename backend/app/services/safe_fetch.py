"""SSRF-resistant HTTP fetching for user-supplied URLs.

* Only http/https on default ports (80/443) are allowed.
* Every hop's hostname is resolved and *all* resolved addresses must be public; the connection is then pinned to
  a vetted IP so DNS rebinding between check and connect cannot redirect us to a private address.
* Redirects are followed manually (max 5), re-validating each hop.
* Response size and time are capped.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import get_settings

MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; LarderRecipeImporter/1.0; personal household use)"


class FetchError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
                or addr.is_unspecified or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10")))


def validate_url(url: str) -> tuple[str, str, int, str]:
    """Return (scheme, host, port, ip) for a URL after checking it resolves only to public addresses."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https links can be imported")
    if not parts.hostname:
        raise FetchError("That link has no host name")
    if parts.username or parts.password:
        raise FetchError("Links with embedded credentials are not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise FetchError("Only standard web ports are allowed")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"Could not resolve {host}") from e
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise FetchError(f"Could not resolve {host}")
    if not get_settings().allow_private_fetch and not all(_is_public(ip) for ip in ips):
        raise FetchError("That address points to a private or local network and cannot be imported")
    return parts.scheme, host, port, ips[0]


def fetch(url: str, *, accept: str = "text/html,application/xhtml+xml,image/*;q=0.8", max_bytes: int | None = None) -> Fetched:
    settings = get_settings()
    max_bytes = max_bytes or settings.max_fetch_mb * 1024 * 1024
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, ip = validate_url(current)
        # Pin the connection to the vetted IP while keeping SNI/Host as the original hostname.
        transport = httpx.HTTPTransport(retries=0)
        with httpx.Client(transport=transport, timeout=settings.fetch_timeout_seconds, follow_redirects=False,
                          trust_env=True) as client:
            headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-GB,en;q=0.8"}
            # When an outbound proxy is configured (trust_env) the proxy performs DNS; we still validated above.
            target = current
            extensions = {}
            if not _proxy_configured(scheme):
                ip_host = f"[{ip}]" if ":" in ip else ip
                target = current.replace(f"//{host}", f"//{ip_host}", 1) if f"//{host}" in current else current
                headers["Host"] = host if port in (80, 443) else f"{host}:{port}"
                extensions = {"sni_hostname": host}
            try:
                with client.stream("GET", target, headers=headers, extensions=extensions) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise FetchError("Redirect without a location")
                        current = urljoin(current, loc)
                        continue
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared and declared > max_bytes:
                        raise FetchError("That page or file is too large to import")
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError("That page or file is too large to import")
                        chunks.append(chunk)
                    return Fetched(current, resp.status_code, resp.headers.get("content-type", ""), b"".join(chunks))
            except httpx.HTTPError as e:
                raise FetchError(f"Could not fetch the page ({type(e).__name__})") from e
    raise FetchError("Too many redirects")


def _proxy_configured(scheme: str) -> bool:
    import os

    return bool(os.environ.get("HTTPS_PROXY" if scheme == "https" else "HTTP_PROXY") or os.environ.get(
        "https_proxy" if scheme == "https" else "http_proxy"))
