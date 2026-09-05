"""One way to spell an address.

An Ethereum address arrives as a string from three directions - a query
parameter, a JSON body, an RPC log - and each may use a different case.
`0xabc...` and `0xABC...` are the same account, so anything keyed by a raw
address string (a nonce store, a pass cache, a rate limiter) treats one holder
as two the moment the casing changes. Normalise once, at the boundary, and
every layer inside can compare with `==`.

EIP-55 checksummed form is the canonical spelling: it is what a wallet shows the
user and what an explorer links to, so it is what belongs in a log line or an
error message.
"""

from eth_utils import is_address, is_checksum_address, to_checksum_address


class InvalidAddress(ValueError):
    """The string is not an address this service will accept."""


def normalise(value: object) -> str:
    """Return `value` as an EIP-55 checksummed address.

    Raises `InvalidAddress` for anything else. Callers at an HTTP boundary
    should catch it and answer 400: a malformed address is the client's
    mistake, and saying so beats a 500 raised deeper by a lookup that found
    nothing.
    """
    if not isinstance(value, str):
        raise InvalidAddress(f"not an Ethereum address: {value!r}")

    # eth-utils accepts a bare 40-hex string without the prefix. Nothing a user
    # can copy - a wallet, an explorer, a receipt - omits `0x`, so a string
    # missing it did not come from where the caller thinks it did.
    if not value.startswith("0x"):
        raise InvalidAddress(f"address must start with 0x: {value!r}")

    if not is_address(value):
        raise InvalidAddress(f"not an Ethereum address: {value!r}")

    # Mixed case *is* the checksum: EIP-55 encodes it in which hex letters are
    # capitalised, so a mixed-case string is a claim that the digits have been
    # verified. Honour the claim - that is the whole point of the scheme, and it
    # is the only chance we get to catch a mistyped address before it becomes a
    # holder who can never sign in. A string that is entirely upper or entirely
    # lower makes no such claim (RPC logs emit lowercase), so it is accepted as
    # given and checksummed on the way out.
    #
    # eth-utils' own `is_address` does not enforce this; it returns True for a
    # mixed-case address whose checksum is wrong. Hence the explicit check.
    letters = [c for c in value[2:] if c.isalpha()]
    claims_checksum = any(c.isupper() for c in letters) and any(c.islower() for c in letters)
    if claims_checksum and not is_checksum_address(value):
        raise InvalidAddress(f"address fails its EIP-55 checksum: {value!r}")

    return to_checksum_address(value)
