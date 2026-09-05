from datetime import UTC, datetime, timedelta

import pytest

from dropzone.addresses import InvalidAddress
from dropzone.auth.nonces import NonceRejected, NonceStore

ALICE = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
BOB = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
TTL = timedelta(minutes=5)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def store() -> NonceStore:
    return NonceStore(ttl=TTL)


# -- issuing --------------------------------------------------------------


def test_a_challenge_is_bound_to_its_address_and_a_deadline(store: NonceStore) -> None:
    challenge = store.issue(ALICE, now=NOW)

    assert challenge.address == ALICE
    assert challenge.issued_at == NOW
    assert challenge.expires_at == NOW + TTL


def test_the_address_is_normalised_when_the_challenge_is_issued(store: NonceStore) -> None:
    """So a client that asks in lowercase and verifies in checksummed form still works."""
    challenge = store.issue(ALICE.lower(), now=NOW)

    assert challenge.address == ALICE
    assert store.consume(challenge.nonce, ALICE, now=NOW) == challenge


def test_a_challenge_for_a_malformed_address_is_never_issued(store: NonceStore) -> None:
    with pytest.raises(InvalidAddress):
        store.issue("definitely-not-an-address", now=NOW)

    assert len(store) == 0


def test_every_nonce_is_different(store: NonceStore) -> None:
    """A repeated nonce is a replay the store would accept as legitimate."""
    nonces = {store.issue(ALICE, now=NOW).nonce for _ in range(500)}

    assert len(nonces) == 500


def test_a_nonce_is_128_bits_of_hex(store: NonceStore) -> None:
    """Hex throughout, so it survives a URL, a JSON body and a wallet prompt."""
    nonce = store.issue(ALICE, now=NOW).nonce

    assert len(nonce) == 32
    assert set(nonce) <= set("0123456789abcdef")


def test_a_nonce_is_unpredictable_not_merely_unique(store: NonceStore) -> None:
    """Uniqueness is not the property that matters; unguessability is.

    A sequential counter is unique, is 32 hex characters, and is completely
    predictable - an attacker who sees one challenge knows the next, so they can
    obtain a signature for a challenge that has not been issued yet. Distinctness
    tests do not notice that at all, so this one looks at the bits: over a large
    sample every one of the 128 positions should take both values. A counter
    leaves its high bits pinned at zero and fails here immediately, while for
    `secrets` the chance of any position being constant is 2^-299.
    """
    sample = [int(store.issue(ALICE, now=NOW).nonce, 16) for _ in range(300)]

    varying = sum(1 for bit in range(128) if len({(value >> bit) & 1 for value in sample}) == 2)

    assert varying == 128, f"only {varying}/128 bit positions vary; the nonce is patterned"


def test_consecutive_nonces_are_not_adjacent(store: NonceStore) -> None:
    """The specific shape of a counter, named so a reviewer sees it was considered."""
    first = int(store.issue(ALICE, now=NOW).nonce, 16)
    second = int(store.issue(ALICE, now=NOW).nonce, 16)

    assert abs(second - first) > 1


# -- spending, which must work exactly once -------------------------------


def test_a_fresh_challenge_can_be_spent(store: NonceStore) -> None:
    challenge = store.issue(ALICE, now=NOW)

    assert store.consume(challenge.nonce, ALICE, now=NOW) == challenge


def test_a_nonce_cannot_be_spent_twice(store: NonceStore) -> None:
    """The replay defence, stated directly.

    Without this a signature captured from a request authenticates its holder
    forever, because a signature over a fixed message is valid every time.
    """
    challenge = store.issue(ALICE, now=NOW)
    store.consume(challenge.nonce, ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, ALICE, now=NOW)


def test_a_nonce_that_was_never_issued_is_rejected(store: NonceStore) -> None:
    with pytest.raises(NonceRejected):
        store.consume("00" * 16, ALICE, now=NOW)


def test_one_address_cannot_spend_anothers_challenge(store: NonceStore) -> None:
    """Otherwise an attacker requests a challenge and crosses it with a victim's."""
    challenge = store.issue(ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, BOB, now=NOW)


def test_spending_it_for_the_wrong_address_still_burns_it(store: NonceStore) -> None:
    """No retries: a rejected attempt must not leave the nonce available."""
    challenge = store.issue(ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, BOB, now=NOW)
    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, ALICE, now=NOW)


def test_one_holders_challenge_is_untouched_by_anothers(store: NonceStore) -> None:
    alice = store.issue(ALICE, now=NOW)
    bob = store.issue(BOB, now=NOW)

    store.consume(bob.nonce, BOB, now=NOW)

    assert store.consume(alice.nonce, ALICE, now=NOW) == alice


def test_rejection_does_not_say_which_check_failed(store: NonceStore) -> None:
    """One exception type for all three refusals, so probing cannot tell them apart.

    A caller who learns that a nonce is "expired" rather than "unknown" learns
    that it was real, which is a fact worth not giving away.
    """
    spent = store.issue(ALICE, now=NOW)
    store.consume(spent.nonce, ALICE, now=NOW)
    stale = store.issue(BOB, now=NOW)
    misdirected = store.issue(ALICE, now=NOW)

    failures = []
    for nonce, address, moment in [
        ("00" * 16, ALICE, NOW),  # never issued
        (spent.nonce, ALICE, NOW),  # already used
        (stale.nonce, BOB, NOW + TTL),  # expired
        (misdirected.nonce, BOB, NOW),  # someone else's
    ]:
        with pytest.raises(NonceRejected) as caught:
            store.consume(nonce, address, now=moment)
        failures.append(type(caught.value))

    assert len(set(failures)) == 1


# -- expiry ---------------------------------------------------------------


def test_a_challenge_survives_up_to_the_last_second(store: NonceStore) -> None:
    challenge = store.issue(ALICE, now=NOW)

    assert store.consume(challenge.nonce, ALICE, now=NOW + TTL - timedelta(seconds=1))


def test_a_challenge_is_gone_at_the_expiry_second(store: NonceStore) -> None:
    """Exclusive, matching the strict comparison the contract uses for a pass."""
    challenge = store.issue(ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, ALICE, now=NOW + TTL)


def test_an_expired_challenge_stays_rejected(store: NonceStore) -> None:
    challenge = store.issue(ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, ALICE, now=NOW + timedelta(days=1))


def test_expired_challenges_are_swept(store: NonceStore) -> None:
    """An unswept dict grows one entry per abandoned sign-in for the life of the process."""
    store.issue(ALICE, now=NOW)
    store.issue(BOB, now=NOW)
    live = store.issue(ALICE, now=NOW + TTL)

    assert store.discard_expired(now=NOW + TTL) == 2
    assert len(store) == 1
    assert store.consume(live.nonce, ALICE, now=NOW + TTL) == live


def test_sweeping_is_housekeeping_not_enforcement(store: NonceStore) -> None:
    """consume refuses an expired nonce whether or not the sweep has run."""
    challenge = store.issue(ALICE, now=NOW)

    with pytest.raises(NonceRejected):
        store.consume(challenge.nonce, ALICE, now=NOW + TTL)
    assert len(store) == 0


# -- construction ---------------------------------------------------------


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_a_non_positive_ttl_is_refused(ttl: timedelta) -> None:
    """Zero expires every challenge on issue, presenting as "signing in is broken"."""
    with pytest.raises(ValueError, match="positive"):
        NonceStore(ttl=ttl)


def test_naive_datetimes_are_refused(store: NonceStore) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.issue(ALICE, now=datetime(2026, 1, 1))  # noqa: DTZ001


def test_the_clock_defaults_to_now(store: NonceStore) -> None:
    """The production path passes no `now`; it must not be the untested one."""
    challenge = store.issue(ALICE)

    assert store.consume(challenge.nonce, ALICE) == challenge
