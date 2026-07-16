from __future__ import annotations

"""Admin/ops endpoint protection (LLD §3.5 W-64).

A single shared token (``AUTO_ROUTER_ADMIN_TOKEN``) gates the sensitive
``/admin/*`` and ``/jobs/agent`` endpoints. The token is supplied via the
``X-Admin-Token`` header or HTTP Basic auth (user ``admin``, token as password).

When no token is configured the endpoints are locked down: every request is
rejected with 401 so operators cannot accidentally expose them.
"""

from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer

from auto_router.settings import get_settings

_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Admin authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": 'Basic realm="auto-router-admin"'},
    )


async def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    basic: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    settings = get_settings()
    token = getattr(settings, "admin_token", "") or ""
    if not token:
        raise _unauthorized("Admin token not configured (set AUTO_ROUTER_ADMIN_TOKEN)")

    provided: list[str] = []
    if x_admin_token:
        provided.append(x_admin_token.strip())
    if bearer is not None and bearer.credentials:
        provided.append(bearer.credentials.strip())
    if basic is not None and basic.password:
        provided.append(basic.password.strip())

    if any(secret == token for secret in provided):
        return

    raise _unauthorized()


# Type alias used by route decorators.
AdminAuth = Any
