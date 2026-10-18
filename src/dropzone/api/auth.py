"""Authentication handshake endpoints (EIP-4361).

The handshake connects an on-chain access pass to an off-chain HTTP session:
1. `GET /auth/challenge?address=0x...` issues a single-use random challenge bound to
   that address, stored server-side with a short TTL.
2. The user signs the structured EIP-4361 message with their Ethereum wallet.
3. `POST /auth/verify` recovers the signer from the signature, asserts it matches the
   claimed address, consumes the nonce atomically, checks pass validity on the chain,
   and issues a JWT whose expiry is clamped to the pass expiry.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dropzone.addresses import InvalidAddress, normalise
from dropzone.api.deps import (
    get_chain_gateway,
    get_current_time,
    get_nonce_store,
    get_settings,
)
from dropzone.auth.messages import (
    InvalidMessage,
    InvalidSignature,
    parse_siwe_message,
    recover_signer,
)
from dropzone.auth.nonces import NonceRejected, NonceStore
from dropzone.auth.tokens import issue_token
from dropzone.chain.gateway import ChainGateway
from dropzone.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


class ChallengeResponse(BaseModel):
    nonce: str
    address: str
    issued_at: datetime
    expires_at: datetime


class VerifyRequest(BaseModel):
    address: str
    message: str
    signature: str


class VerifyResponse(BaseModel):
    token: str
    token_type: str = "bearer"  # noqa: S105
    expires_at: datetime


@router.get("/challenge", response_model=ChallengeResponse)
def get_challenge(
    address: Annotated[str, Query(description="The Ethereum address requesting a challenge")],
    nonce_store: Annotated[NonceStore, Depends(get_nonce_store)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> ChallengeResponse:
    """Issue a fresh, single-use random challenge bound to `address`."""
    try:
        holder = normalise(address)
    except InvalidAddress as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    challenge = nonce_store.issue(holder, now=now)
    return ChallengeResponse(
        nonce=challenge.nonce,
        address=challenge.address,
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
    )


@router.post("/verify", response_model=VerifyResponse)
def verify_signature(
    body: VerifyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    chain_gateway: Annotated[ChainGateway, Depends(get_chain_gateway)],
    nonce_store: Annotated[NonceStore, Depends(get_nonce_store)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> VerifyResponse:
    """Verify an EIP-4361 signature and exchange it for a session token.

    Executes the six verification steps defined in ADR 0002 / build plan §3:
    1. Parse claimed address and EIP-4361 message structure.
    2. Recover the signer from the signature and assert `recovered == claimed_address`.
    3. Consume the nonce atomically — invalidating the challenge on this first attempt.
    4. Assert domain, chain ID, and message expiration match deployment settings.
    5. Look up the pass on the chain gateway — 402 if unpurchased, lapsed or unconfirmed.
    6. Issue a JWT session token with `exp = min(now + session_ttl, pass_expiry)`.
    """
    # 1. Parse and validate the claimed address
    try:
        claimed_address = normalise(body.address)
    except InvalidAddress as exc:
        raise HTTPException(status_code=400, detail=f"invalid claimed address: {exc}") from exc

    # 1b. Parse EIP-4361 structured message
    try:
        siwe_msg = parse_siwe_message(body.message)
    except InvalidMessage as exc:
        raise HTTPException(status_code=400, detail=f"invalid EIP-4361 message: {exc}") from exc

    # 2. Recover signer and verify it matches the claimed address
    try:
        recovered = recover_signer(body.message, body.signature)
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail=f"invalid signature: {exc}") from exc

    if recovered != claimed_address:
        raise HTTPException(
            status_code=401,
            detail="recovered signer does not match claimed address",
        )

    # 3. Consume the nonce atomically.
    # pop removes it from the store; any failure past this point cannot be retried
    # with the same nonce, eliminating replay and probing windows.
    try:
        nonce_store.consume(siwe_msg.nonce, claimed_address, now=now)
    except NonceRejected as exc:
        raise HTTPException(status_code=401, detail=f"nonce rejected: {exc}") from exc

    # 4. Assert domain, chain ID, and message validity
    if siwe_msg.address != claimed_address:
        raise HTTPException(
            status_code=401,
            detail="message address does not match claimed address",
        )

    if siwe_msg.domain != settings.domain:
        raise HTTPException(
            status_code=401,
            detail=(
                f"message domain {siwe_msg.domain!r} does not match expected {settings.domain!r}"
            ),
        )

    if siwe_msg.chain_id != settings.chain_id:
        raise HTTPException(
            status_code=401,
            detail=(
                f"message chain ID {siwe_msg.chain_id} does not match expected {settings.chain_id}"
            ),
        )

    if siwe_msg.expiration_time is not None and now >= siwe_msg.expiration_time:
        raise HTTPException(status_code=401, detail="signed message has expired")

    # 5. Verify pass ownership on the chain gateway
    pass_record = chain_gateway.pass_for(claimed_address)
    if pass_record is None:
        raise HTTPException(
            status_code=402,
            detail="address holds no access pass",
        )

    if pass_record.expires_at <= now:
        raise HTTPException(
            status_code=402,
            detail="access pass has expired",
        )

    if pass_record.confirmations < settings.required_confirmations:
        raise HTTPException(
            status_code=402,
            detail=(
                f"pass requires {settings.required_confirmations} confirmations, "
                f"but has {pass_record.confirmations}"
            ),
        )

    # 6. Issue session token clamped to pass expiry
    session_expiry = min(now + settings.session_ttl, pass_record.expires_at)
    token = issue_token(
        address=claimed_address,
        expires_at=session_expiry,
        secret=settings.jwt_secret.get_secret_value(),
        issued_at=now,
    )

    return VerifyResponse(
        token=token,
        token_type="bearer",  # noqa: S106
        expires_at=session_expiry,
    )
