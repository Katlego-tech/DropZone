# ADR 0005 — Session lifetime, EIP-4361 handshake, and pass expiry clamping

- **Status:** Accepted
- **Date:** 2026-09-27
- **Related:** ADR 0002 — FastAPI over Django for the API layer; ADR 0003 — Testing against a local chain

## Context

The core purpose of DropZone is closing the gap between on-chain pass ownership and off-chain
HTTP request authentication. A user buys a time-limited pass by calling `AccessPass.buyPass`
on Ethereum; our API must serve protected data only when the caller cryptographically proves
control of that address.

Authentication takes place in two phases:
1. **The handshake (EIP-4361):** The user proves key ownership by signing a structured message
   containing a server-issued challenge nonce, deployment domain, chain ID, and expiry.
2. **The session (Bearer JWT):** Exchanging that verified proof for a short-lived token that
   authenticates subsequent requests to `GET /content`.

Two design decisions govern this boundary:
* How long should a session token last?
* How do we guarantee that a session never outlives the on-chain access pass that justified it?

## The Tension

Session lifetime is a direct trade-off between user friction and revocation lag:

* **Short sessions (e.g. 1 minute):** High security and rapid response if access lapses, but
  requires the user to repeatedly sign messages in their wallet while reading content. Frequent
  wallet prompts encourage "blind signing," directly degrading security.
* **Long sessions (e.g. 24 hours):** Seamless reading experience, but extends the window during
  which a lapsed subscriber continues reading protected data off-chain.
* **Pass boundary overrun:** A user who buys a 30-day pass and signs in 5 minutes before it
  expires must not be granted a full 15-minute session that outlives their paid entitlement.

## Decision

### 1. 15-minute session ceiling, strictly clamped to pass expiry

The session token expiry is computed as:

$$\text{exp} = \min(\text{now} + \text{session\_ttl}, \text{pass\_expiry})$$

* Default `session_ttl` is 15 minutes.
* If a holder's pass expires in 3 minutes, their session token expires in exactly 3 minutes.
* Once the pass lapses, subsequent handshake attempts fail with `402 Payment Required`.
* The hot path (`GET /content`) performs local cryptographic signature verification on the JWT
  and makes **zero RPC calls**, preventing rate-limit exhaustion and latency spikes.

### 2. Single-use challenge nonces with 5-minute TTL

* Nonces are 128 bits of cryptographic entropy (`secrets.token_hex(16)`).
* Stored server-side and bound to the requesting address.
* **Consumed atomically via `pop`** on the first verification attempt. Reading and deleting
  occur in a single step, preventing concurrent verification race conditions and replay attacks.
* Probing resistance: all refusal conditions in `consume()` raise a unified `NonceRejected`
  exception, returning 401 without revealing whether the challenge was missing, already spent,
  or expired.

### 3. Algorithm pinning and explicit secrets

* The JWT algorithm is pinned to `HS256`. Token decoding specifies `algorithms=["HS256"]`,
  neutralising algorithm substitution attacks (such as `alg: none` or RSA/HMAC confusion).
* The signing key is validated at startup to ensure at least 32 characters of entropy.
  There is no fallback default secret; failing at startup is loud, while a compromised default
  key is silent.

## What this cannot fix

A holder can copy their bearer token and hand it to an unauthorized party. Off-chain token
sharing is an inherent boundary limitation of bearer credentials; once the cryptographic proof
of ownership has succeeded and a token is issued, the API cannot determine who controls the
client process making the HTTP request. We document this honestly in the threat model rather
than inventing fragile browser-fingerprinting heuristics.
