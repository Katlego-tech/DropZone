"""The boundary between this service and the chain.

Everything the application needs to know about Ethereum is these three methods.
Nothing above this line imports an RPC client, and nothing below it knows what a
JWT is.

The point is testability. A gateway defined by what *this* service needs - not
by what an RPC node happens to offer - can be implemented twice: once against a
real node, once in memory. The acceptance suite then runs the entire
authentication flow with no node, no testnet, no faucet and no flakiness, which
is the only way the negative tests (an unconfirmed purchase, a pass expiring
mid-session) are cheap enough to actually write.

It is a `Protocol` rather than a base class so neither implementation has to
inherit anything, and so the interface stays a statement about the consumer's
needs. Note the limit: a `runtime_checkable` Protocol's `isinstance` check
verifies that method *names* exist, not that their signatures match. Type
checking is what enforces the shape; the runtime check only catches a class that
forgot a method wholesale.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PassRecord:
    """A holder's current pass, as the chain currently sees it.

    `confirmations` is carried alongside the expiry rather than folded into a
    boolean because the two answer different questions. The expiry says whether
    the pass is live; the confirmation count says how much we should trust that
    it happened at all. A purchase one block deep is real until the block it sits
    in is reorged away, and choosing the depth at which to grant access is a
    policy decision belonging to the caller - see the confirmation-policy ADR.
    Returning the number lets that policy live in one place instead of being
    hardcoded here.
    """

    holder: str
    expires_at: datetime
    confirmations: int


@dataclass(frozen=True, slots=True)
class PassPurchased:
    """A `PassPurchased` log, as emitted by AccessPass.buyPass.

    `block_number` and `log_index` together are the cursor. Block number alone
    is not enough: two purchases can land in one block, and an indexer that
    resumes from "the last block I saw" would either replay or skip them. The
    transaction hash is here for reconciliation - after a reorg it is how you
    tell a log that moved from a log that vanished.
    """

    holder: str
    expires_at: datetime
    block_number: int
    log_index: int
    transaction_hash: str


@runtime_checkable
class ChainGateway(Protocol):
    """What the application is allowed to ask the chain."""

    def pass_for(self, holder: str) -> PassRecord | None:
        """The holder's pass, or None if they have never bought one.

        None means "no pass on record" - not "expired". An expired pass is a
        `PassRecord` whose `expires_at` is in the past, and the distinction
        matters at the HTTP boundary: both refuse access, but only one of them
        is a renewal.
        """
        ...

    def events_since(self, from_block: int, to_block: int) -> Sequence[PassPurchased]:
        """Purchases in blocks `from_block`..`to_block`, both inclusive.

        Ordered by (block_number, log_index) so a consumer can checkpoint
        mid-range and resume without replaying. Both bounds are explicit - an
        open-ended "since" would return a different answer on every call and make
        the indexer's progress untestable.
        """
        ...

    def current_block(self) -> int:
        """The latest block height the node will admit to."""
        ...
