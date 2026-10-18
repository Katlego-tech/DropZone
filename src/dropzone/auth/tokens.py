"""Session tokens.

Once a holder proves ownership of an address holding an active access pass, the service
issues a signed session token. Presenting the token on subsequent requests allows access
to protected content without repeating the challenge-and-signature handshake, and without
hitting an RPC node on the request path.

Two invariants govern session tokens:
* **The algorithm is pinned.** Explicitly `HS256`. A JWT implementation that trusts the `alg`
  header from an untrusted token opens algorithm substitution attacks (e.g. `none` or
  public-key confusion). We specify `algorithms=["HS256"]` so no other algorithm is accepted.
* **The session cannot outlive the pass.** The token's `exp` is clamped to the earlier of
  `(now + session_ttl)` and `pass_expiry`. A holder whose pass expires 5 minutes from now
  receives a 5-minute session, never a 15-minute one.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from dropzone.addresses import InvalidAddress, normalise

ALGORITHM = "HS256"


class SessionTokenError(Exception):
    """Base exception for session token failures."""


class TokenExpired(SessionTokenError):
    """The session token has reached its expiry timestamp."""


class InvalidToken(SessionTokenError):
    """The token is forged, tampered with, or malformed."""


@dataclass(frozen=True, slots=True)
class SessionPayload:
    address: str
    issued_at: datetime
    expires_at: datetime


def issue_token(
    *,
    address: str,
    expires_at: datetime,
    secret: str,
    issued_at: datetime | None = None,
) -> str:
    """Mint a signed JWT session token for `address`.

    `expires_at` must be timezone-aware and must be after `issued_at`.
    """
    _require_aware(expires_at)
    now = issued_at if issued_at is not None else datetime.now(UTC)
    _require_aware(now)

    if expires_at <= now:
        raise ValueError("expires_at must be strictly in the future")

    holder = normalise(address)
    payload = {
        "sub": holder,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(
    token: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> SessionPayload:
    """Verify and decode a session token.

    Raises `TokenExpired` if the token has passed its expiration time, or
    `InvalidToken` if signature verification fails or required claims are missing.
    """
    if not isinstance(token, str) or not token:
        raise InvalidToken("token must be a non-empty string")

    current_time = now if now is not None else datetime.now(UTC)
    _require_aware(current_time)

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidToken(f"invalid session token: {exc}") from exc

    if "exp" not in payload:
        raise InvalidToken("token missing expiration claim")

    try:
        exp_timestamp = float(payload["exp"])
    except (ValueError, TypeError) as exc:
        raise InvalidToken("token expiration claim is not a number") from exc

    if current_time.timestamp() >= exp_timestamp:
        raise TokenExpired("session token has expired")

    if "sub" not in payload:
        raise InvalidToken("token missing subject claim")

    try:
        holder = normalise(payload["sub"])
    except InvalidAddress as exc:
        raise InvalidToken("token subject is not a valid address") from exc

    iat = datetime.fromtimestamp(payload.get("iat", int(current_time.timestamp())), tz=UTC)
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)

    return SessionPayload(
        address=holder,
        issued_at=iat,
        expires_at=exp,
    )


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
