"""Response headers that tell browsers to lock the site down.

Render serves every page over HTTPS. These headers add what a browser should
enforce on top of that: stay on HTTPS, run only this site's own scripts and
styles, never show the site inside another site's frame, and never guess a
file's type.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

# Every script, stylesheet, image and request comes from this site. HTMX and
# the stylesheet are served from /static and no page runs inline code, so
# nothing else needs allowing.
SITE_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
)
SITE_POLICY = "; ".join(SITE_DIRECTIVES)

# FastAPI's interactive API docs load Swagger UI and ReDoc from a CDN and
# start them with inline code, so those pages allow exactly that and no more.
DOCS_PATHS = frozenset({"/docs", "/redoc"})
DOCS_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
)
DOCS_POLICY = "; ".join(DOCS_DIRECTIVES)

HEADERS = {
    # Use HTTPS for this site and its subdomains for a year, even if a link says http.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Treat a file as the type the server says, never as a guessed one.
    "X-Content-Type-Options": "nosniff",
    # Tell other sites nothing about the page a visitor came from.
    "Referrer-Policy": "same-origin",
    # The same as frame-ancestors 'none', for older browsers.
    "X-Frame-Options": "DENY",
    # The site never needs a visitor's location, camera or microphone.
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def policy_for(path: str) -> str:
    """Return the Content-Security-Policy for a page: the docs' looser one, or the site's."""
    return DOCS_POLICY if path in DOCS_PATHS else SITE_POLICY


def add_security_headers(app: FastAPI) -> None:
    """Add the headers to every response the app sends, without replacing any a route set."""

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy", policy_for(request.url.path)
        )
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        return response
