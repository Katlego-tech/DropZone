from datetime import UTC, datetime, timedelta

import jwt
import pytest

from dropzone.auth.tokens import (
    InvalidToken,
    TokenExpired,
    issue_token,
    verify_token,
)

SECRET = "a" * 32
OTHER_SECRET = "b" * 32
ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
LOWER_ADDRESS = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"


def test_issue_and_verify_token_roundtrip() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=15)

    token = issue_token(
        address=LOWER_ADDRESS,
        expires_at=exp,
        secret=SECRET,
        issued_at=now,
    )

    payload = verify_token(token, secret=SECRET, now=now)

    assert payload.address == ADDRESS
    assert payload.issued_at == now
    assert payload.expires_at == exp


def test_token_expiration_is_enforced() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=15)

    token = issue_token(
        address=ADDRESS,
        expires_at=exp,
        secret=SECRET,
        issued_at=now,
    )

    # 1 second after expiry
    after = exp + timedelta(seconds=1)
    with pytest.raises(TokenExpired, match="expired"):
        verify_token(token, secret=SECRET, now=after)


def test_token_with_wrong_secret_is_rejected() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=15)

    token = issue_token(
        address=ADDRESS,
        expires_at=exp,
        secret=SECRET,
        issued_at=now,
    )

    with pytest.raises(InvalidToken):
        verify_token(token, secret=OTHER_SECRET, now=now)


def test_tampered_token_is_rejected() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=15)

    token = issue_token(
        address=ADDRESS,
        expires_at=exp,
        secret=SECRET,
        issued_at=now,
    )

    # Flip the last character
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(InvalidToken):
        verify_token(tampered, secret=SECRET, now=now)


def test_algorithm_confusion_is_rejected() -> None:
    """An 'alg: none' token must be rejected even if unsigned."""
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=15)

    none_token = jwt.encode(
        {"sub": ADDRESS, "iat": int(now.timestamp()), "exp": int(exp.timestamp())},
        key="",
        algorithm="none",
    )

    with pytest.raises(InvalidToken):
        verify_token(none_token, secret=SECRET, now=now)


def test_issue_token_refuses_expiry_in_past_or_present() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="strictly in the future"):
        issue_token(
            address=ADDRESS,
            expires_at=now,
            secret=SECRET,
            issued_at=now,
        )


def test_issue_token_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_token(
            address=ADDRESS,
            expires_at=datetime(2026, 8, 5, 18, 15, 0),  # noqa: DTZ001
            secret=SECRET,
        )


@pytest.mark.parametrize("bad_token", ["", "not-a-token", 12345])
def test_verify_token_rejects_malformed_input(bad_token: object) -> None:
    with pytest.raises(InvalidToken):
        verify_token(bad_token, secret=SECRET)  # type: ignore[arg-type]


def test_verify_token_rejects_missing_exp() -> None:
    token = jwt.encode({"sub": ADDRESS}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidToken, match="missing expiration claim"):
        verify_token(token, secret=SECRET)


def test_verify_token_rejects_non_numeric_exp() -> None:
    token = jwt.encode({"sub": ADDRESS, "exp": "not-a-timestamp"}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidToken, match="not a number"):
        verify_token(token, secret=SECRET)


def test_verify_token_rejects_missing_sub() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    token = jwt.encode(
        {"exp": int((now + timedelta(minutes=15)).timestamp())}, SECRET, algorithm="HS256"
    )
    with pytest.raises(InvalidToken, match="missing subject claim"):
        verify_token(token, secret=SECRET, now=now)


def test_verify_token_rejects_invalid_sub_address() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    token = jwt.encode(
        {"sub": "not-an-eth-address", "exp": int((now + timedelta(minutes=15)).timestamp())},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken, match="not a valid address"):
        verify_token(token, secret=SECRET, now=now)
