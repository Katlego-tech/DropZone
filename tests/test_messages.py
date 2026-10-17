from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from dropzone.auth.messages import (
    InvalidMessage,
    InvalidSignature,
    format_siwe_message,
    parse_siwe_message,
    recover_signer,
)

ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
LOWER_ADDRESS = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"


def test_format_siwe_message_contains_all_fields() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=5)

    msg = format_siwe_message(
        domain="passes.example",
        address=LOWER_ADDRESS,
        uri="https://passes.example",
        nonce="8f2b1c9ade407",
        chain_id=11155111,
        issued_at=now,
        expiration_time=exp,
        statement="Authenticate to retrieve your access pass session.",
    )

    assert "passes.example wants you to sign in with your Ethereum account:" in msg
    assert ADDRESS in msg
    assert "Authenticate to retrieve your access pass session." in msg
    assert "URI: https://passes.example" in msg
    assert "Version: 1" in msg
    assert "Chain ID: 11155111" in msg
    assert "Nonce: 8f2b1c9ade407" in msg
    assert "Issued At: 2026-08-05T18:00:00Z" in msg
    assert "Expiration Time: 2026-08-05T18:05:00Z" in msg


def test_format_siwe_message_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_siwe_message(
            domain="localhost",
            address=ADDRESS,
            uri="http://localhost",
            nonce="12345678",
            chain_id=1,
            issued_at=datetime(2026, 1, 1, 0, 0, 0),  # noqa: DTZ001
        )


def test_parse_siwe_message_roundtrip() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)
    exp = now + timedelta(minutes=5)

    formatted = format_siwe_message(
        domain="passes.example",
        address=ADDRESS,
        uri="https://passes.example",
        nonce="8f2b1c9ade407",
        chain_id=11155111,
        issued_at=now,
        expiration_time=exp,
    )

    parsed = parse_siwe_message(formatted)

    assert parsed.domain == "passes.example"
    assert parsed.address == ADDRESS
    assert parsed.uri == "https://passes.example"
    assert parsed.version == "1"
    assert parsed.chain_id == 11155111
    assert parsed.nonce == "8f2b1c9ade407"
    assert parsed.issued_at == now
    assert parsed.expiration_time == exp
    assert parsed.statement == "Authenticate to retrieve your access pass session."


def test_parse_siwe_message_without_statement() -> None:
    now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)

    formatted = format_siwe_message(
        domain="passes.example",
        address=ADDRESS,
        uri="https://passes.example",
        nonce="8f2b1c9ade407",
        chain_id=11155111,
        issued_at=now,
        statement=None,
    )

    parsed = parse_siwe_message(formatted)

    assert parsed.statement is None
    assert parsed.expiration_time is None
    assert parsed.nonce == "8f2b1c9ade407"


def test_parse_siwe_message_with_extra_blank_lines() -> None:
    text = (
        "localhost wants you to sign in with your Ethereum account:\n"
        f"{ADDRESS}\n\n"
        "Statement\n\n"
        "URI: http://localhost\n"
        "   \n"
        "Version: 1\n"
        "Chain ID: 1\n"
        "Nonce: 12345678\n"
        "Issued At: 2026-01-01T00:00:00Z\n"
    )
    parsed = parse_siwe_message(text)
    assert parsed.nonce == "12345678"


@pytest.mark.parametrize(
    ("bad_text", "match"),
    [
        ("", "non-empty string"),
        ("not a siwe message", "at least a header"),
        ("domain wants you to sign in\n0x123\n\nURI: x", "malformed EIP-4361 header"),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x0000000000000000000000000000000000000000\n\n"
            "URI: https://passes.example\nVersion: 1\nChain ID: 1\nIssued At: 2026-01-01T00:00:00Z",
            "missing required field: Nonce",
        ),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x0000000000000000000000000000000000000000\n\n"
            "URI: https://passes.example\nVersion: 2\nChain ID: 1\nNonce: 12345678\n"
            "Issued At: 2026-01-01T00:00:00Z",
            "unsupported EIP-4361 version",
        ),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x0000000000000000000000000000000000000000\n\n"
            "URI: https://passes.example\nVersion: 1\nChain ID: notanumber\nNonce: 12345678\n"
            "Issued At: 2026-01-01T00:00:00Z",
            "invalid chain ID",
        ),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x0000000000000000000000000000000000000000\n\n"
            "URI: https://passes.example\nVersion: 1\nChain ID: 1\nNonce: 12345678\n"
            "Issued At: invalid-date",
            "invalid timestamp for Issued At",
        ),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed\n\n"
            "URI: https://passes.example\nVersion: 1\nChain ID: 1\nNonce: 12345678\n"
            "Issued At: 2026-01-01T00:00:00Z",
            "invalid address in message",
        ),
        (
            "passes.example wants you to sign in with your Ethereum account:\n"
            "0x0000000000000000000000000000000000000000\n\n"
            "URI: https://passes.example\nVersion: 1\nChain ID: 1\nNonce: 12345678\n"
            "Issued At: 2026-01-01T00:00:00",
            "must include timezone offset",
        ),
    ],
)
def test_parse_siwe_message_rejects_malformed_input(bad_text: str, match: str) -> None:
    with pytest.raises(InvalidMessage, match=match):
        parse_siwe_message(bad_text)


def test_recover_signer_matches_signing_account() -> None:
    account = Account.create()
    msg = "Test signing message"
    signable = encode_defunct(text=msg)
    signed = account.sign_message(signable)

    recovered = recover_signer(msg, signed.signature.to_0x_hex())

    assert recovered == account.address


def test_recover_signer_detects_message_tampering() -> None:
    account = Account.create()
    msg = "Original message"
    signed = account.sign_message(encode_defunct(text=msg))

    tampered_msg = "Modified message"
    recovered = recover_signer(tampered_msg, signed.signature.to_0x_hex())

    assert recovered != account.address


@pytest.mark.parametrize(
    "bad_sig",
    [
        "not-a-sig",
        "0x123",
        "0x" + "00" * 65,
        "",
        12345,
    ],
)
def test_recover_signer_rejects_invalid_signatures(bad_sig: object) -> None:
    with pytest.raises(InvalidSignature):
        recover_signer("test message", bad_sig)  # type: ignore[arg-type]
