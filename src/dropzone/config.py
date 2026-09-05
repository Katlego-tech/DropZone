"""Settings, read once from the environment.

Everything configurable lives here so that reading this file tells you what the
service needs to run - and so nothing deeper reaches for `os.environ` and
invents its own default.

Two rules the rest of the codebase depends on:

* **The signing secret has no default.** A fallback value is the single worst
  thing this file could contain: it would ship in the image, be identical for
  every deployment, and let anyone holding the source mint a session for any
  address. Absent secret, no app. Failing at startup is loud; failing at the
  first forged token is silent.
* **Settings are constructed, not imported.** `create_app` builds one and hands
  it down, so a test can pass a different one without touching the process
  environment - and so two tests can hold different settings at once.
"""

from datetime import timedelta
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DROPZONE_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Catches a typo in .env: `DROPZONE_SESSION_TTLL=...` fails at startup
        # rather than leaving you with a setting you think you changed and did
        # not. Note the limit - this cannot police the process environment,
        # where pydantic-settings only ever reads names it recognises, so a
        # typo'd `export` is still silent. .env is where humans typo.
        extra="forbid",
    )

    # -- identity of this deployment, bound into the signed message --------

    domain: str = Field(
        default="localhost:8000",
        description=(
            "The domain a client must name in its EIP-4361 message. Binding it "
            "is what stops a signature harvested on another site being replayed "
            "here, so it has to match what the frontend actually serves from."
        ),
    )
    uri: str = Field(default="http://localhost:8000")
    chain_id: int = Field(
        default=11155111,  # Sepolia
        description="Bound into the signed message alongside the domain.",
    )

    # -- secrets -----------------------------------------------------------

    jwt_secret: SecretStr = Field(
        description=(
            "HMAC key for session tokens. No default, deliberately. SecretStr so "
            "that logging the settings object, or letting one reach a traceback, "
            "prints '**********' rather than the key."
        ),
    )

    # -- lifetimes ---------------------------------------------------------

    nonce_ttl: timedelta = Field(
        default=timedelta(minutes=5),
        description=(
            "How long a challenge stays claimable. Long enough for a human to "
            "read a wallet prompt, short enough that a captured challenge is "
            "worthless by the time it is used."
        ),
    )
    session_ttl: timedelta = Field(
        default=timedelta(minutes=15),
        description=(
            "Ceiling on a session token. The token's actual expiry is the "
            "earlier of this and the pass expiry, so a session can never "
            "outlive the pass that justified it."
        ),
    )

    # -- chain policy ------------------------------------------------------

    required_confirmations: int = Field(
        default=1,
        ge=1,
        description=(
            "Block depth before a purchase is honoured. 1 grants immediately "
            "and accepts that a reorg can take it back. See the "
            "confirmation-policy ADR."
        ),
    )

    @field_validator("jwt_secret")
    @classmethod
    def _reject_a_weak_secret(cls, value: SecretStr) -> SecretStr:
        # 32 bytes is the HMAC-SHA256 block the token is signed with. Shorter is
        # not a policy preference, it is less entropy than the algorithm assumes,
        # and it is the kind of thing that gets set to "dev" in a hurry and then
        # deployed.
        if len(value.get_secret_value()) < 32:
            raise ValueError("DROPZONE_JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("nonce_ttl", "session_ttl")
    @classmethod
    def _reject_a_non_positive_lifetime(cls, value: timedelta) -> timedelta:
        # A zero TTL expires every nonce and every session on issue, which
        # presents as "signing in is broken" rather than as a misconfiguration.
        if value <= timedelta(0):
            raise ValueError("lifetimes must be positive")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read from the environment on first use.

    Cached because reading and validating them on every request is waste, and
    because a value that could change between two requests in one process is a
    source of behaviour nobody can reproduce. Tests that need different settings
    construct `Settings(...)` directly rather than clearing this cache.
    """
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills from env
