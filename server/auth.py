"""HTTP Basic Auth middleware.

Enabled when BOTH `AUTH_USERNAME` and `AUTH_PASSWORD` are set. Otherwise
runs as a no-op — convenient for local development, safe for deployment
because the default production config *must* set both for the app to be
protected.

`/healthz` is always unauthenticated so container orchestrators (Fly,
Railway, Render, Kubernetes liveness probes) can hit it without creds.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


PUBLIC_PATHS: tuple[str, ...] = ("/healthz",)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, username: str | None, password: str | None, realm: str = "Jolto") -> None:
        super().__init__(app)
        self._username = username or ""
        self._password = password or ""
        self._realm = realm
        self.enabled = bool(username and password)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        if self._valid(request.headers.get("authorization", "")):
            return await call_next(request)

        return Response(
            content="Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{self._realm}", charset="UTF-8"'},
            media_type="text/plain",
        )

    def _valid(self, header: str) -> bool:
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        user, sep, pw = decoded.partition(":")
        if not sep:
            return False
        # Constant-time comparison to avoid timing attacks.
        return secrets.compare_digest(user, self._username) and secrets.compare_digest(
            pw, self._password
        )


def install_basic_auth(app) -> bool:
    """Attach the middleware if credentials are configured. Returns whether enabled."""

    user = os.getenv("AUTH_USERNAME")
    pw = os.getenv("AUTH_PASSWORD")
    if not user or not pw:
        return False
    app.add_middleware(BasicAuthMiddleware, username=user, password=pw)
    return True
