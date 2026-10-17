"""Acceptance tests for the authentication handshake and content protection.

Written against HTTP requests, status codes, and JSON bodies. These tests describe
the complete user and attacker flows and import nothing from application internals,
so they serve as an executable specification.

Negative tests directly implement the threat model in README.md and build plan §8:
* rejects_a_signature_recovered_to_a_different_address
* rejects_a_nonce_that_has_already_been_consumed
* rejects_a_signature_whose_message_expired
* rejects_a_session_token_outliving_its_pass
* rejects_a_pass_purchased_but_not_yet_confirmed
* serves_content_for_a_confirmed_live_pass
"""

from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from dropzone.api.app import create_app
from dropzone.auth.messages import format_siwe_message
from dropzone.auth.nonces import NonceStore
from dropzone.chain.memory import InMemoryChainGateway
from dropzone.config import Settings

TEST_SECRET = "a" * 32


@pytest.fixture
def wallet() -> Account:
    return Account.create()


@pytest.fixture
def other_wallet() -> Account:
    return Account.create()


def test_happy_path_access_flow(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """1. Buy pass -> 2. Challenge -> 3. Sign -> 4. Verify -> 5. Get content."""
    # 1. Purchase an access pass on-chain
    chain_gateway.buy_pass(wallet.address)

    # 2. Request a challenge nonce
    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    assert challenge_res.status_code == 200
    challenge_data = challenge_res.json()
    nonce = challenge_data["nonce"]

    # 3. Client constructs and signs EIP-4361 message
    issued_at = datetime.fromisoformat(challenge_data["issued_at"])
    expires_at = datetime.fromisoformat(challenge_data["expires_at"])
    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=nonce,
        chain_id=11155111,
        issued_at=issued_at,
        expiration_time=expires_at,
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    # 4. Exchange for session token
    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 200
    verify_data = verify_res.json()
    token = verify_data["token"]
    assert verify_data["token_type"] == "bearer"

    # 5. Access protected content
    content_res = client.get("/content", headers={"Authorization": f"Bearer {token}"})
    assert content_res.status_code == 200
    assert content_res.json()["holder"] == wallet.address


def test_rejects_a_signature_recovered_to_a_different_address(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
    other_wallet: Account,
) -> None:
    """Bob signs the message, but claims to be Alice in the request."""
    chain_gateway.buy_pass(wallet.address)

    # Challenge requested for Alice
    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    assert challenge_res.status_code == 200
    challenge_data = challenge_res.json()

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    # Signed by Bob (other_wallet)
    signature = other_wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    # Present Bob's signature claiming to be Alice
    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 401
    assert "recovered signer does not match" in verify_res.json()["detail"]


def test_rejects_a_nonce_that_has_already_been_consumed(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """An intercepted signature presented a second time must be rejected."""
    chain_gateway.buy_pass(wallet.address)

    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    payload = {"address": wallet.address, "message": message, "signature": signature}

    # First attempt: succeeds and burns the nonce
    res1 = client.post("/auth/verify", json=payload)
    assert res1.status_code == 200

    # Second attempt (replay attack): fails because nonce was already consumed
    res2 = client.post("/auth/verify", json=payload)
    assert res2.status_code == 401
    assert "nonce rejected" in res2.json()["detail"]


def test_rejects_a_signature_whose_message_expired(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """A signature presented after the message's expiration time is refused."""
    chain_gateway.buy_pass(wallet.address)

    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    issued_at = datetime.fromisoformat(challenge_data["issued_at"])
    # Message explicitly expires after 2 minutes (while nonce has 5-minute TTL)
    message_exp = issued_at + timedelta(minutes=2)

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=issued_at,
        expiration_time=message_exp,
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    # Time travels 3 minutes into the future (past message_exp, but before nonce 5m TTL)
    chain_gateway.advance_time(timedelta(minutes=3))

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 401
    assert "signed message has expired" in verify_res.json()["detail"].lower()


def test_rejects_a_session_token_outliving_its_pass(
    wallet: Account,
) -> None:
    """The session ceiling must clamp to the pass expiry if earlier than session_ttl."""
    # Pass valid for only 3 minutes, but session_ttl is 15 minutes
    short_duration = timedelta(minutes=3)
    start_time = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

    gateway = InMemoryChainGateway(duration=short_duration, now=start_time)
    gateway.buy_pass(wallet.address)

    settings = Settings(
        jwt_secret=TEST_SECRET,  # type: ignore[arg-type]
        session_ttl=timedelta(minutes=15),
    )
    nonce_store = NonceStore(ttl=timedelta(minutes=5))
    app = create_app(settings=settings, chain_gateway=gateway, nonce_store=nonce_store)

    with TestClient(app) as test_client:
        challenge_res = test_client.get(f"/auth/challenge?address={wallet.address}")
        challenge_data = challenge_res.json()

        message = format_siwe_message(
            domain="localhost:8000",
            address=wallet.address,
            uri="http://localhost:8000",
            nonce=challenge_data["nonce"],
            chain_id=11155111,
            issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
            expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
        )
        signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

        verify_res = test_client.post(
            "/auth/verify",
            json={"address": wallet.address, "message": message, "signature": signature},
        )
        assert verify_res.status_code == 200
        token_data = verify_res.json()
        token = token_data["token"]
        token_exp = datetime.fromisoformat(token_data["expires_at"])

        # Token expiry must match pass expiry (start + 3 minutes), not start + 15 minutes
        assert token_exp == start_time + short_duration

        # At minute 2 (before pass expiry): content succeeds
        gateway.advance_time(timedelta(minutes=2))
        res_live = test_client.get("/content", headers={"Authorization": f"Bearer {token}"})
        assert res_live.status_code == 200

        # At minute 4 (after pass expiry): token is rejected even though
        # session_ttl (15m) has not elapsed
        gateway.advance_time(timedelta(minutes=2))
        res_expired = test_client.get("/content", headers={"Authorization": f"Bearer {token}"})
        assert res_expired.status_code == 401
        assert "expired" in res_expired.json()["detail"].lower()


def test_rejects_a_pass_purchased_but_not_yet_confirmed(
    wallet: Account,
) -> None:
    """When required_confirmations > 1, a 1-depth purchase is refused."""
    settings = Settings(
        jwt_secret=TEST_SECRET,  # type: ignore[arg-type]
        required_confirmations=2,
    )
    gateway = InMemoryChainGateway()
    # Purchase lands in head block (confirmations = 1)
    gateway.buy_pass(wallet.address)

    nonce_store = NonceStore()
    app = create_app(settings=settings, chain_gateway=gateway, nonce_store=nonce_store)

    with TestClient(app) as test_client:
        # Challenge 1
        c1 = test_client.get(f"/auth/challenge?address={wallet.address}").json()
        msg1 = format_siwe_message(
            domain="localhost:8000",
            address=wallet.address,
            uri="http://localhost:8000",
            nonce=c1["nonce"],
            chain_id=11155111,
            issued_at=datetime.fromisoformat(c1["issued_at"]),
            expiration_time=datetime.fromisoformat(c1["expires_at"]),
        )
        sig1 = wallet.sign_message(encode_defunct(text=msg1)).signature.to_0x_hex()

        # Depth 1 < 2 confirmations -> 402 Payment Required
        res1 = test_client.post(
            "/auth/verify",
            json={"address": wallet.address, "message": msg1, "signature": sig1},
        )
        assert res1.status_code == 402
        assert "confirmations" in res1.json()["detail"]

        # Mine 1 block -> now confirmations = 2
        gateway.advance_blocks(1)

        # Fresh challenge (since previous nonce was consumed)
        c2 = test_client.get(f"/auth/challenge?address={wallet.address}").json()
        msg2 = format_siwe_message(
            domain="localhost:8000",
            address=wallet.address,
            uri="http://localhost:8000",
            nonce=c2["nonce"],
            chain_id=11155111,
            issued_at=datetime.fromisoformat(c2["issued_at"]),
            expiration_time=datetime.fromisoformat(c2["expires_at"]),
        )
        sig2 = wallet.sign_message(encode_defunct(text=msg2)).signature.to_0x_hex()

        # Now confirmed -> 200 OK
        res2 = test_client.post(
            "/auth/verify",
            json={"address": wallet.address, "message": msg2, "signature": sig2},
        )
        assert res2.status_code == 200


def test_rejects_unpurchased_address_with_402(
    client: TestClient,
    wallet: Account,
) -> None:
    """A valid signature from an account with no pass returns 402 Payment Required."""
    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 402
    assert "holds no access pass" in verify_res.json()["detail"]


def test_rejects_expired_pass_with_402(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """A pass that has passed its duration returns 402 Payment Required."""
    chain_gateway.buy_pass(wallet.address)

    # Advance time by 31 days (default duration is 30 days)
    chain_gateway.advance_time(timedelta(days=31))

    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 402
    assert "access pass has expired" in verify_res.json()["detail"]


def test_rejects_wrong_domain_in_signed_message(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """A signature created for a different domain is rejected."""
    chain_gateway.buy_pass(wallet.address)

    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    # Message signed for another domain (e.g. phishing site)
    message = format_siwe_message(
        domain="phishing.example",
        address=wallet.address,
        uri="https://phishing.example",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 401
    assert "domain" in verify_res.json()["detail"]


def test_rejects_wrong_chain_id_in_signed_message(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    """A signature for a different chain ID (e.g. Mainnet=1 vs Sepolia=11155111) is rejected."""
    chain_gateway.buy_pass(wallet.address)

    challenge_res = client.get(f"/auth/challenge?address={wallet.address}")
    challenge_data = challenge_res.json()

    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=1,  # Mainnet
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    assert verify_res.status_code == 401
    assert "chain ID" in verify_res.json()["detail"]


def test_content_requires_authentication(client: TestClient) -> None:
    res = client.get("/content")
    assert res.status_code == 401
    assert "missing authorization" in res.json()["detail"]


def test_content_rejects_malformed_authorization_header(client: TestClient) -> None:
    res = client.get("/content", headers={"Authorization": "Basic 12345"})
    assert res.status_code == 401
    assert "Bearer" in res.json()["detail"]


def test_content_rejects_tampered_token(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
) -> None:
    chain_gateway.buy_pass(wallet.address)

    challenge_data = client.get(f"/auth/challenge?address={wallet.address}").json()
    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    signature = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    verify_res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": signature},
    )
    valid_token = verify_res.json()["token"]

    # Tamper with the token
    tampered_token = valid_token[:-2] + ("ab" if valid_token[-2:] != "ab" else "cd")
    content_res = client.get("/content", headers={"Authorization": f"Bearer {tampered_token}"})
    assert content_res.status_code == 401
    assert "invalid session token" in content_res.json()["detail"]


def test_challenge_rejects_malformed_address(client: TestClient) -> None:
    res = client.get("/auth/challenge?address=not-an-eth-address")
    assert res.status_code == 400
    assert "address" in res.json()["detail"].lower()


def test_verify_rejects_malformed_claimed_address(client: TestClient) -> None:
    res = client.post(
        "/auth/verify",
        json={"address": "0x123", "message": "any", "signature": "0x456"},
    )
    assert res.status_code == 400
    assert "invalid claimed address" in res.json()["detail"].lower()


def test_verify_rejects_malformed_message(client: TestClient, wallet: Account) -> None:
    res = client.post(
        "/auth/verify",
        json={
            "address": wallet.address,
            "message": "bad message format",
            "signature": "0x" + "00" * 65,
        },
    )
    assert res.status_code == 400
    assert "invalid eip-4361 message" in res.json()["detail"].lower()


def test_verify_rejects_malformed_signature(client: TestClient, wallet: Account) -> None:
    challenge_data = client.get(f"/auth/challenge?address={wallet.address}").json()
    message = format_siwe_message(
        domain="localhost:8000",
        address=wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )

    res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": "invalid-sig"},
    )
    assert res.status_code == 401
    assert "invalid signature" in res.json()["detail"].lower()


def test_verify_rejects_message_with_mismatched_address(
    client: TestClient,
    chain_gateway: InMemoryChainGateway,
    wallet: Account,
    other_wallet: Account,
) -> None:
    chain_gateway.buy_pass(wallet.address)
    challenge_data = client.get(f"/auth/challenge?address={wallet.address}").json()

    # Message specifies other_wallet.address in header, signed by wallet.
    # recover_signer matches wallet.address, but siwe_msg.address is other_wallet,
    # triggering the message address check.
    message = format_siwe_message(
        domain="localhost:8000",
        address=other_wallet.address,
        uri="http://localhost:8000",
        nonce=challenge_data["nonce"],
        chain_id=11155111,
        issued_at=datetime.fromisoformat(challenge_data["issued_at"]),
        expiration_time=datetime.fromisoformat(challenge_data["expires_at"]),
    )
    # Wallet signs this message containing other_wallet's address
    sig = wallet.sign_message(encode_defunct(text=message)).signature.to_0x_hex()

    res = client.post(
        "/auth/verify",
        json={"address": wallet.address, "message": message, "signature": sig},
    )
    assert res.status_code == 401
    assert "address" in res.json()["detail"].lower()


def test_gateway_without_now_falls_back_to_utc_now(wallet: Account) -> None:
    """When a real or minimal gateway does not offer a now clock, utc now is used."""

    class MinimalGateway:
        def pass_for(self, holder: str) -> None:
            return None

        def events_since(self, from_block: int, to_block: int) -> list:
            return []

        def current_block(self) -> int:
            return 1

    app = create_app(
        settings=Settings(jwt_secret=TEST_SECRET),  # type: ignore[arg-type]
        chain_gateway=MinimalGateway(),  # type: ignore[arg-type]
        nonce_store=NonceStore(),
    )
    with TestClient(app) as test_client:
        res = test_client.get(f"/auth/challenge?address={wallet.address}")
        assert res.status_code == 200
