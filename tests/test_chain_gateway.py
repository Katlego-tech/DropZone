from datetime import UTC, datetime, timedelta

import pytest

from dropzone.chain.gateway import ChainGateway, PassPurchased, PassRecord
from dropzone.chain.memory import InMemoryChainGateway

ALICE = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
BOB = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
DURATION = timedelta(days=30)
START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def chain() -> InMemoryChainGateway:
    return InMemoryChainGateway(duration=DURATION, now=START, block_number=100)


def test_the_fake_satisfies_the_gateway_interface(chain: InMemoryChainGateway) -> None:
    """A structural check: it catches a method dropped, not a signature changed."""
    assert isinstance(chain, ChainGateway)


# -- passes ---------------------------------------------------------------


def test_an_address_that_never_bought_has_no_record(chain: InMemoryChainGateway) -> None:
    """None means "never bought", which is not the same as "expired"."""
    assert chain.pass_for(ALICE) is None


def test_buying_grants_a_pass_for_the_full_duration(chain: InMemoryChainGateway) -> None:
    record = chain.buy_pass(ALICE)

    assert record == PassRecord(holder=ALICE, expires_at=START + DURATION, confirmations=1)
    assert chain.pass_for(ALICE) == record


def test_a_pass_is_looked_up_regardless_of_case(chain: InMemoryChainGateway) -> None:
    chain.buy_pass(ALICE.lower())

    found = chain.pass_for(ALICE.upper().replace("0X", "0x"))
    assert found is not None
    assert found.holder == ALICE


def test_one_holders_pass_is_not_anothers(chain: InMemoryChainGateway) -> None:
    chain.buy_pass(ALICE)

    assert chain.pass_for(BOB) is None


def test_an_expired_pass_is_still_a_record(chain: InMemoryChainGateway) -> None:
    """The distinction the HTTP layer needs: lapsed is a renewal, absent is a sale."""
    chain.buy_pass(ALICE)
    chain.advance_time(DURATION * 2)

    record = chain.pass_for(ALICE)
    assert record is not None
    assert record.expires_at < chain.now


# -- renewal arithmetic, which must match AccessPass.sol -------------------


def test_renewing_early_extends_from_the_existing_expiry(chain: InMemoryChainGateway) -> None:
    """Renewing on day 10 of 30 must not forfeit the 20 days left."""
    first = chain.buy_pass(ALICE)
    chain.advance_time(timedelta(days=10))

    second = chain.buy_pass(ALICE)

    assert second.expires_at == first.expires_at + DURATION


def test_renewing_after_expiry_starts_afresh(chain: InMemoryChainGateway) -> None:
    """A lapsed holder gets 30 days from now, not backdated time they never had."""
    chain.buy_pass(ALICE)
    chain.advance_time(DURATION + timedelta(days=5))

    renewed = chain.buy_pass(ALICE)

    assert renewed.expires_at == chain.now + DURATION


def test_renewing_at_the_exact_expiry_starts_afresh(chain: InMemoryChainGateway) -> None:
    """The boundary the contract draws with a strict comparison."""
    first = chain.buy_pass(ALICE)
    chain.set_time(first.expires_at)

    renewed = chain.buy_pass(ALICE)

    assert renewed.expires_at == first.expires_at + DURATION


# -- confirmations --------------------------------------------------------


def test_a_fresh_purchase_has_one_confirmation(chain: InMemoryChainGateway) -> None:
    """Inclusive of its own block. Zero would shift every policy by one."""
    assert chain.buy_pass(ALICE).confirmations == 1


def test_confirmations_deepen_as_blocks_are_mined(chain: InMemoryChainGateway) -> None:
    chain.buy_pass(ALICE)
    chain.advance_blocks(4)

    record = chain.pass_for(ALICE)
    assert record is not None
    assert record.confirmations == 5


def test_renewing_resets_the_confirmation_depth(chain: InMemoryChainGateway) -> None:
    """The expiry that matters now came from the new transaction, not the old one."""
    chain.buy_pass(ALICE)
    chain.advance_blocks(50)
    chain.buy_pass(ALICE)

    record = chain.pass_for(ALICE)
    assert record is not None
    assert record.confirmations == 1


def test_blocks_cannot_be_un_mined(chain: InMemoryChainGateway) -> None:
    with pytest.raises(ValueError, match="un-mine"):
        chain.advance_blocks(-1)


# -- events, as the iteration 3 indexer will consume them ------------------


def test_no_purchases_means_no_events(chain: InMemoryChainGateway) -> None:
    assert list(chain.events_since(0, chain.current_block())) == []


def test_a_purchase_emits_an_event_carrying_the_new_expiry(
    chain: InMemoryChainGateway,
) -> None:
    record = chain.buy_pass(ALICE)

    (event,) = chain.events_since(0, chain.current_block())
    assert isinstance(event, PassPurchased)
    assert event.holder == ALICE
    assert event.expires_at == record.expires_at
    assert event.block_number == chain.current_block()


def test_purchases_in_one_block_are_ordered_by_log_index(
    chain: InMemoryChainGateway,
) -> None:
    """Why a cursor needs more than a block number."""
    chain.buy_pass(ALICE)
    chain.buy_pass(BOB)

    events = chain.events_since(0, chain.current_block())
    assert [e.log_index for e in events] == [0, 1]
    assert len({e.block_number for e in events}) == 1


def test_log_index_restarts_in_each_block(chain: InMemoryChainGateway) -> None:
    chain.buy_pass(ALICE)
    chain.advance_blocks()
    chain.buy_pass(BOB)

    assert [e.log_index for e in chain.events_since(0, chain.current_block())] == [0, 0]


def test_transaction_hashes_are_distinct(chain: InMemoryChainGateway) -> None:
    """After a reorg it is how you tell a log that moved from one that vanished."""
    chain.buy_pass(ALICE)
    chain.buy_pass(BOB)

    hashes = {e.transaction_hash for e in chain.events_since(0, chain.current_block())}
    assert len(hashes) == 2


def test_both_range_bounds_are_inclusive(chain: InMemoryChainGateway) -> None:
    """An indexer resuming at its last-seen block must not re-read or skip it."""
    chain.buy_pass(ALICE)
    at = chain.current_block()
    chain.advance_blocks()
    chain.buy_pass(BOB)

    assert [e.holder for e in chain.events_since(at, at)] == [ALICE]
    assert [e.holder for e in chain.events_since(at, at + 1)] == [ALICE, BOB]


def test_a_range_before_any_purchase_is_empty(chain: InMemoryChainGateway) -> None:
    chain.buy_pass(ALICE)

    assert list(chain.events_since(0, chain.current_block() - 1)) == []


def test_a_backwards_range_is_rejected(chain: InMemoryChainGateway) -> None:
    """Silently returning nothing would look exactly like "no purchases yet"."""
    with pytest.raises(ValueError, match="after"):
        chain.events_since(10, 9)


# -- the clock ------------------------------------------------------------


def test_the_clock_does_not_run_backwards(chain: InMemoryChainGateway) -> None:
    with pytest.raises(ValueError, match="backwards"):
        chain.set_time(START - timedelta(seconds=1))


def test_naive_datetimes_are_refused(chain: InMemoryChainGateway) -> None:
    """Otherwise the TypeError surfaces inside an expiry check and reads as an auth bug."""
    with pytest.raises(ValueError, match="timezone-aware"):
        chain.set_time(datetime(2027, 1, 1))  # noqa: DTZ001


def test_a_zero_duration_chain_cannot_be_built() -> None:
    """The contract refuses it at deployment; the fake must not model what cannot exist."""
    with pytest.raises(ValueError, match="positive"):
        InMemoryChainGateway(duration=timedelta(0))
