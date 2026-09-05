"""A chain that fits in a dictionary.

This is not a mock. It is a real implementation of `ChainGateway` with the same
arithmetic as the contract - renewal extends from the existing expiry, a lapsed
pass starts afresh - so a test written against it tests the application's
behaviour rather than a recorded stub's. The test-only levers (`buy_pass`,
`advance_blocks`, `set_time`) are what the suite needs and an RPC node cannot
offer: mine a block, move the clock, purchase without a wallet.

Keeping the arithmetic in step with AccessPass.sol is a real cost, and it is
why the Solidity suite exists: that one asserts the contract is right, this one
lets the API be tested as though it were.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from dropzone.addresses import normalise
from dropzone.chain.gateway import PassPurchased, PassRecord


class InMemoryChainGateway:
    """An in-process stand-in for a node, with a clock and a block height."""

    def __init__(
        self,
        *,
        duration: timedelta = timedelta(days=30),
        now: datetime | None = None,
        block_number: int = 1,
    ) -> None:
        if duration <= timedelta(0):
            # The contract refuses a zero duration at deployment; a fake that
            # allowed one would let a test pass against a chain that cannot exist.
            raise ValueError("duration must be positive")

        self._duration = duration
        self._now = now if now is not None else datetime.now(UTC)
        self._require_aware(self._now)
        self._block_number = block_number
        self._expiries: dict[str, datetime] = {}
        self._purchase_blocks: dict[str, int] = {}
        self._events: list[PassPurchased] = []

    # -- ChainGateway ----------------------------------------------------

    def pass_for(self, holder: str) -> PassRecord | None:
        address = normalise(holder)
        expires_at = self._expiries.get(address)
        if expires_at is None:
            return None

        # Depth is inclusive of the block the purchase landed in: a purchase in
        # the head block has one confirmation, not zero. An off-by-one here
        # would quietly shift every confirmation policy built on top by a block.
        confirmations = self._block_number - self._purchase_blocks[address] + 1
        return PassRecord(holder=address, expires_at=expires_at, confirmations=confirmations)

    def events_since(self, from_block: int, to_block: int) -> Sequence[PassPurchased]:
        if from_block > to_block:
            raise ValueError(f"from_block {from_block} is after to_block {to_block}")

        # Already in (block_number, log_index) order: blocks never go backwards
        # and log_index counts up within one, so appending preserves the sort.
        return [e for e in self._events if from_block <= e.block_number <= to_block]

    def current_block(self) -> int:
        return self._block_number

    # -- levers a real chain gives you and this one has to fake -----------

    def buy_pass(self, holder: str) -> PassRecord:
        """Record a purchase in the *current* block.

        Deliberately does not mine one. On a real chain several purchases share
        a block, which is the entire reason a cursor needs `log_index` as well
        as a block number - a fake that mined per purchase would never produce
        two events in one block and would leave that path untested. Tests that
        want confirmation depth call `advance_blocks`.

        Mirrors AccessPass.buyPass: renewing before expiry extends from the
        existing expiry, so nothing is forfeited; renewing after it starts from
        now, so a lapsed holder is not handed backdated time they never had.
        """
        address = normalise(holder)
        current = self._expiries.get(address)
        base = current if current is not None and current > self._now else self._now
        expires_at = base + self._duration

        self._expiries[address] = expires_at
        self._purchase_blocks[address] = self._block_number

        in_this_block = sum(1 for e in self._events if e.block_number == self._block_number)
        self._events.append(
            PassPurchased(
                holder=address,
                expires_at=expires_at,
                block_number=self._block_number,
                log_index=in_this_block,
                transaction_hash=f"0x{len(self._events) + 1:064x}",
            )
        )
        return PassRecord(holder=address, expires_at=expires_at, confirmations=1)

    def advance_blocks(self, count: int = 1) -> int:
        """Mine `count` empty blocks. Deepens every existing confirmation."""
        if count < 0:
            raise ValueError("cannot un-mine blocks")
        self._block_number += count
        return self._block_number

    def set_time(self, moment: datetime) -> None:
        """Move the clock. Time only goes forwards, here as on a real chain."""
        self._require_aware(moment)
        if moment < self._now:
            raise ValueError(f"cannot move the clock backwards: {self._now} -> {moment}")
        self._now = moment

    def advance_time(self, delta: timedelta) -> datetime:
        self.set_time(self._now + delta)
        return self._now

    @property
    def now(self) -> datetime:
        return self._now

    @staticmethod
    def _require_aware(moment: datetime) -> None:
        # A naive datetime compared against an aware one raises TypeError deep
        # inside an expiry check, which reads as a bug in the auth code. Refuse
        # it at the door instead.
        if moment.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware")
