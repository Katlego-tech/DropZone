"""EIP-4361 message formatting, parsing, and signer recovery.

Sign-In With Ethereum (EIP-4361) turns an off-chain signature into an authentication
credential. The message is structured and human-readable, which is what allows a wallet
to display exactly what is being signed (domain, address, nonce, and expiration) rather
than an opaque hash.

Binding these fields is what defeats signature re-use:
* **Domain & URI:** Stops a signature captured on evil.example from authenticating here.
* **Chain ID:** Stops a Sepolia signature from being presented against Mainnet.
* **Nonce:** Single-use challenge that stops replay.
* **Issued At & Expiration Time:** Hard lifetime bounds on the signature itself.

Signer recovery uses EIP-191 personal sign (`\x19Ethereum Signed Message:\n...`),
recovering the public key and deriving the EIP-55 address that produced the signature.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from eth_account import Account
from eth_account.messages import encode_defunct

from dropzone.addresses import InvalidAddress, normalise

_HEADER_PATTERN = re.compile(
    r"^(?P<domain>[^\s]+) wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[0-9a-fA-F]{40})$"
)
_PREFIX_LINE_PATTERN = re.compile(r"^(?P<key>[A-Za-z ]+):\s*(?P<value>.+)$")


class InvalidMessage(ValueError):
    """The message does not conform to EIP-4361."""


class InvalidSignature(ValueError):
    """The signature is malformed or could not be recovered."""


@dataclass(frozen=True, slots=True)
class SiweMessage:
    domain: str
    address: str
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime | None = None
    statement: str | None = None


def format_siwe_message(
    *,
    domain: str,
    address: str,
    uri: str,
    nonce: str,
    chain_id: int,
    issued_at: datetime,
    expiration_time: datetime | None = None,
    statement: str | None = "Authenticate to retrieve your access pass session.",
    version: str = "1",
) -> str:
    """Format an EIP-4361 Sign-In With Ethereum message."""
    checksummed = normalise(address)
    issued_iso = _format_iso(issued_at)

    lines = [
        f"{domain} wants you to sign in with your Ethereum account:",
        checksummed,
        "",
    ]

    if statement:
        lines.append(statement)
        lines.append("")

    lines.extend(
        [
            f"URI: {uri}",
            f"Version: {version}",
            f"Chain ID: {chain_id}",
            f"Nonce: {nonce}",
            f"Issued At: {issued_iso}",
        ]
    )

    if expiration_time is not None:
        lines.append(f"Expiration Time: {_format_iso(expiration_time)}")

    return "\n".join(lines)


def parse_siwe_message(message: str) -> SiweMessage:
    """Parse an EIP-4361 message string into its structured fields.

    Raises `InvalidMessage` if required fields are missing or malformed.
    """
    if not isinstance(message, str) or not message.strip():
        raise InvalidMessage("message must be a non-empty string")

    # Split into sections separated by empty lines
    sections = [s.strip() for s in message.split("\n\n") if s.strip()]
    if len(sections) < 2:
        raise InvalidMessage("message must contain at least a header and field section")

    header_section = sections[0]
    header_match = _HEADER_PATTERN.match(header_section)
    if not header_match:
        raise InvalidMessage(f"malformed EIP-4361 header: {header_section!r}")

    domain = header_match.group("domain")
    raw_address = header_match.group("address")
    try:
        address = normalise(raw_address)
    except InvalidAddress as exc:
        raise InvalidMessage(f"invalid address in message: {raw_address!r}") from exc

    if sections[1].startswith("URI:"):
        statement = None
        fields_section = "\n".join(sections[1:])
    else:
        statement = sections[1]
        fields_section = "\n".join(sections[2:])

    fields: dict[str, str] = {}
    for line in fields_section.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _PREFIX_LINE_PATTERN.match(line)
        if match:
            fields[match.group("key")] = match.group("value").strip()

    # Validate required fields
    required = ["URI", "Version", "Chain ID", "Nonce", "Issued At"]
    for key in required:
        if key not in fields:
            raise InvalidMessage(f"missing required field: {key}")

    version = fields["Version"]
    if version != "1":
        raise InvalidMessage(f"unsupported EIP-4361 version: {version!r}")

    try:
        chain_id = int(fields["Chain ID"])
    except ValueError as exc:
        raise InvalidMessage(f"invalid chain ID: {fields['Chain ID']!r}") from exc

    issued_at = _parse_iso(fields["Issued At"], "Issued At")
    expiration_time = None
    if "Expiration Time" in fields:
        expiration_time = _parse_iso(fields["Expiration Time"], "Expiration Time")

    return SiweMessage(
        domain=domain,
        address=address,
        uri=fields["URI"],
        version=version,
        chain_id=chain_id,
        nonce=fields["Nonce"],
        issued_at=issued_at,
        expiration_time=expiration_time,
        statement=statement,
    )


def recover_signer(message: str, signature: str) -> str:
    """Recover the EIP-55 address that signed `message` with `signature`.

    Raises `InvalidSignature` if recovery fails.
    """
    if not isinstance(signature, str) or not signature.startswith("0x"):
        raise InvalidSignature(f"signature must be a 0x-prefixed hex string: {signature!r}")

    try:
        signable = encode_defunct(text=message)
        recovered = Account.recover_message(signable, signature=signature)
        return normalise(recovered)
    except Exception as exc:
        raise InvalidSignature(f"failed to recover signer: {exc}") from exc


def _format_iso(dt: datetime) -> str:
    """Format timezone-aware datetime as UTC ISO-8601 with trailing Z."""
    if dt.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime."""
    cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(cleaned)
    except (ValueError, TypeError) as exc:
        raise InvalidMessage(f"invalid timestamp for {field_name}: {value!r}") from exc

    if dt.tzinfo is None:
        raise InvalidMessage(f"{field_name} must include timezone offset: {value!r}")
    return dt.astimezone(UTC)
