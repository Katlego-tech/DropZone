"""Dependency injection for API route handlers.

Dependencies are pulled from `request.app.state`, where `create_app` attached them.
This keeps route handlers decoupled from singletons and lets tests provide in-memory
implementations without touching the process environment or network.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from dropzone.auth.nonces import NonceStore
from dropzone.auth.tokens import InvalidToken, SessionPayload, TokenExpired, verify_token
from dropzone.chain.gateway import ChainGateway
from dropzone.config import Settings


def get_settings(request: Request) -> Settings:
    """The configuration attached to this application instance."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_chain_gateway(request: Request) -> ChainGateway:
    """The chain gateway attached to this application instance."""
    return request.app.state.chain_gateway  # type: ignore[no-any-return]


def get_nonce_store(request: Request) -> NonceStore:
    """The nonce store attached to this application instance."""
    return request.app.state.nonce_store  # type: ignore[no-any-return]


def get_current_time(
    chain_gateway: Annotated[ChainGateway, Depends(get_chain_gateway)],
) -> datetime:
    """Return the current time.

    If the gateway has an internal clock (like InMemoryChainGateway), use it so that
    time-travel in acceptance tests advances auth expirations in sync with the chain.
    Otherwise, return the real UTC now.
    """
    if hasattr(chain_gateway, "now") and isinstance(chain_gateway.now, datetime):
        return chain_gateway.now
    return datetime.now(UTC)


def get_session(
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    authorization: Annotated[str | None, Header()] = None,
) -> SessionPayload:
    """Validate the Bearer session token on protected routes.

    Zero RPC calls on this path. Verifies the HMAC-SHA256 signature locally against
    the configured secret and enforces the expiry timestamp.
    """
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="authorization header must be in the format 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    secret = settings.jwt_secret.get_secret_value()

    try:
        return verify_token(token, secret=secret, now=now)
    except TokenExpired as exc:
        raise HTTPException(
            status_code=401,
            detail="session token has expired",
            headers={
                "WWW-Authenticate": (
                    'Bearer error="invalid_token", error_description="token expired"'
                )
            },
        ) from exc
    except InvalidToken as exc:
        raise HTTPException(
            status_code=401,
            detail=f"invalid session token: {exc}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
