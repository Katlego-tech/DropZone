import pytest

from dropzone.addresses import InvalidAddress, normalise

# anvil's first development account. Public, funded only on a throwaway chain.
CHECKSUMMED = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_a_checksummed_address_survives_unchanged() -> None:
    assert normalise(CHECKSUMMED) == CHECKSUMMED


def test_a_lowercase_address_is_checksummed() -> None:
    """RPC logs emit lowercase. It is not a typo, it is just unchecksummed."""
    assert normalise(CHECKSUMMED.lower()) == CHECKSUMMED


def test_an_uppercase_address_is_checksummed() -> None:
    assert normalise("0x" + CHECKSUMMED[2:].upper()) == CHECKSUMMED


def test_case_alone_never_makes_two_holders() -> None:
    """The bug this module exists to prevent."""
    holders = {normalise(CHECKSUMMED), normalise(CHECKSUMMED.lower())}
    assert len(holders) == 1


def test_an_address_failing_its_checksum_is_rejected() -> None:
    """Mixed case is a claim that the digits were verified. Check the claim.

    eth-utils' own is_address returns True here, which is why normalise does
    the check itself.
    """
    flipped = CHECKSUMMED.replace("F", "f", 1)
    assert flipped != CHECKSUMMED

    with pytest.raises(InvalidAddress, match="checksum"):
        normalise(flipped)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        (CHECKSUMMED[:-1], "one character short"),
        (CHECKSUMMED + "0", "one character long"),
        (CHECKSUMMED[2:], "no 0x prefix"),
        ("0x" + "z" * 40, "not hexadecimal"),
        ("", "empty"),
        ("0x", "prefix only"),
    ],
)
def test_malformed_addresses_are_rejected(value: str, why: str) -> None:
    with pytest.raises(InvalidAddress):
        normalise(value)


@pytest.mark.parametrize("value", [None, 42, b"0x00", ["0x00"]])
def test_things_that_are_not_strings_are_rejected(value: object) -> None:
    """JSON bodies deliver whatever the client sent, not whatever we annotated."""
    with pytest.raises(InvalidAddress):
        normalise(value)
