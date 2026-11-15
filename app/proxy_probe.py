"""TEMPORARY: describe the shape of the proxy headers a request arrives with.

Behind Render, a request passes through Cloudflare and Render's proxy before
reaching the app, and each hop can add to X-Forwarded-For. To choose which
entry the rate limiter should trust, this logs the header's shape: how many
entries, what kind of address each is, which client-IP headers are present,
and which entry each of them (and uvicorn's chosen client) matches. It never
logs an address. It runs only for a request that sends the probe header, so
visitors' requests are never examined. Remove it once the fix is in.
"""

import ipaddress
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

PROBE_HEADER = "x-dopolitics-probe"

# Cloudflare's published IPv4 ranges (cloudflare.com/ips-v4, read 2026-11-15).
CLOUDFLARE = [
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
    )
]
# The documentation range the probe's own fake entries use.
TEST_NET = ipaddress.ip_network("203.0.113.0/24")

logger = logging.getLogger("app.proxy_probe")


def kind(value: str) -> str:
    """Return what sort of address a value is, without the address itself."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return "not-an-ip"
    if ip.version == 6:
        return "ipv6"
    if ip in TEST_NET:
        return "probe-fake"
    if any(ip in net for net in CLOUDFLARE):
        return "cloudflare"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    return "public"


def shape(request: Request) -> str:
    """Describe the proxy headers by kinds, counts, names and positions only."""
    entries = [
        e.strip()
        for e in request.headers.get("x-forwarded-for", "").split(",")
        if e.strip()
    ]

    def position(value: str | None) -> str:
        if not value:
            return "absent"
        return (
            f"xff[{entries.index(value.strip())}]"
            if value.strip() in entries
            else f"not-in-xff ({kind(value)})"
        )

    names = sorted(
        name
        for name in request.headers
        if any(word in name for word in ("ip", "forward", "client", "real"))
    )
    client = request.client.host if request.client else None
    return (
        f"xff={[kind(e) for e in entries]} "
        f"uvicorn_client={position(client)} "
        f"cf_connecting_ip={position(request.headers.get('cf-connecting-ip'))} "
        f"true_client_ip={position(request.headers.get('true-client-ip'))} "
        f"x_real_ip={position(request.headers.get('x-real-ip'))} "
        f"header_names={names}"
    )


def add_proxy_probe(app: FastAPI) -> None:
    """Log the shape of a request's proxy headers when it asks to be probed."""

    @app.middleware("http")
    async def proxy_probe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if PROBE_HEADER in request.headers:
            logger.info("proxy probe: %s", shape(request))
        return await call_next(request)
