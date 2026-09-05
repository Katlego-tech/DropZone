"""Single-use challenges.

A nonce is the whole replay defence. Without one, a signature captured from a
request - a proxy log, a browser extension, a shared screen - authenticates its
holder forever, because a signature over a fixed message is valid every time it
is presented. Issuing a fresh random challenge and refusing to accept it twice
is what turns a signature into a credential that can be spent exactly once.

Three properties carry that weight, and each is enforced here rather than left
to the caller:

* **Unguessable.** `secrets` rather than `random`. A predictable nonce lets an
  attacker obtain a signature for a challenge that has not been issued yet.
* **Single use.** `consume` is the only way to read one, and it removes the
  nonce in the same step. There is no `get` to call and then forget to follow
  with a delete.
* **Bound to an address.** A challenge issued to Alice cannot be spent by Bob,
  so an attacker cannot request a challenge, wait for a victim to be issued one,
  and cross the two.

This implementation holds nonces in a dict, which means they are per-process and
lost on restart. That is honest for a single instance: the failure mode is a
user re-signing, not a user being let in wrongly. Two instances behind a load
balancer would each accept the same nonce once - so the day this runs on more
than one process, the store moves to Redis or Postgres behind this same
interface, and `consume` becomes a `DELETE ... RETURNING`, which is atomic for
the same reason `dict.pop` is.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dropzone.addresses import normalise

# 16 bytes of entropy, hex encoded. EIP-4361 requires at least 8 alphanumeric
# characters; this is comfortably past that and short enough to sit on one line
# of a wallet prompt without the user scrolling to reach the domain.
_NONCE_BYTES = 16


class NonceRejected(Exception):
    """The nonce was not one this server issued, or is no longer spendable.

    Deliberately one exception rather than three. The caller answers 401 either
    way, and an error that distinguishes "never issued" from "already used" from
    "expired" tells an attacker probing the endpoint which of those they are
    looking at.
    """


@dataclass(frozen=True, slots=True)
class Challenge:
    nonce: str
    address: str
    issued_at: datetime
    expires_at: datetime


class NonceStore:
    """Issues challenges and lets each be spent once."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=5)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self._ttl = ttl
        self._issued: dict[str, Challenge] = {}

    def issue(self, address: str, *, now: datetime | None = None) -> Challenge:
        """Mint a challenge for `address`.

        No limit on how many an address may hold at once. That is a rate-limiting
        concern, tracked as debt in ADR 0002, and solving it here by capping the
        count would hand an attacker a denial of service: request challenges for
        a victim's address until their real one is evicted.
        """
        moment = self._require_aware(now)
        holder = normalise(address)

        challenge = Challenge(
            nonce=secrets.token_hex(_NONCE_BYTES),
            address=holder,
            issued_at=moment,
            expires_at=moment + self._ttl,
        )
        self._issued[challenge.nonce] = challenge
        return challenge

    def consume(self, nonce: str, address: str, *, now: datetime | None = None) -> Challenge:
        """Spend a nonce, or raise `NonceRejected`.

        Reading and removing are one step. A `get` followed by a separate
        `delete` leaves a window in which two concurrent verifications both see
        the nonce as unspent, which is exactly the replay this class exists to
        prevent.
        """
        moment = self._require_aware(now)

        # pop, not get: even the paths below that reject the nonce have already
        # removed it. An expired or misdirected nonce is spent by the attempt,
        # so a caller cannot retry against a store that still holds it.
        challenge = self._issued.pop(nonce, None)
        if challenge is None:
            raise NonceRejected("unknown or already used")

        # A plain comparison, on purpose. An address is public - it is in every
        # block explorer - so there is no prefix here worth leaking and a
        # constant-time compare would be ceremony over a non-secret. The secret
        # in this exchange is the nonce, and it is protected by being 128 random
        # bits and by being gone after one read, not by how it is compared.
        if challenge.address != normalise(address):
            raise NonceRejected("issued to a different address")

        if moment >= challenge.expires_at:
            # Expiry is exclusive, matching the contract's own strict
            # comparison: at the expiry second the challenge is already gone.
            raise NonceRejected("expired")

        return challenge

    def discard_expired(self, *, now: datetime | None = None) -> int:
        """Drop expired challenges, returning how many went.

        Housekeeping only - `consume` never honours an expired nonce whether or
        not this has run. Without it an unswept dict grows for as long as the
        process lives, one entry per abandoned sign-in, which is a slow leak
        rather than a security hole.
        """
        moment = self._require_aware(now)
        stale = [n for n, c in self._issued.items() if moment >= c.expires_at]
        for nonce in stale:
            del self._issued[nonce]
        return len(stale)

    def __len__(self) -> int:
        """Outstanding challenges, expired or not. For tests and metrics."""
        return len(self._issued)

    @staticmethod
    def _require_aware(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware")
        return now
