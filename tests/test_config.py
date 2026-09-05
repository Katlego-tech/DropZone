from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dropzone.config import Settings, get_settings

SECRET = "a" * 32


def settings(**overrides: object) -> Settings:
    return Settings(jwt_secret=SECRET, **overrides)  # type: ignore[arg-type]


def test_the_service_refuses_to_start_without_a_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most important test in this file.

    A default secret would ship in the image, be identical everywhere, and let
    anyone holding the source mint a session for any address. Failing at startup
    is loud; failing at the first forged token is silent.
    """
    monkeypatch.delenv("DROPZONE_JWT_SECRET", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_short_secret_is_refused() -> None:
    """Caught at startup, not left to be discovered by whoever forges a token."""
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(jwt_secret="dev")  # type: ignore[call-arg]


def test_the_secret_does_not_appear_when_settings_are_printed() -> None:
    """Settings reach logs and tracebacks. SecretStr is what keeps the key out."""
    rendered = f"{settings()!r} {settings()!s}"

    assert SECRET not in rendered
    assert "**********" in rendered


def test_the_secret_is_still_readable_by_the_code_that_needs_it() -> None:
    assert settings().jwt_secret.get_secret_value() == SECRET


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DROPZONE_JWT_SECRET", SECRET)
    monkeypatch.setenv("DROPZONE_DOMAIN", "passes.example")
    monkeypatch.setenv("DROPZONE_CHAIN_ID", "1")

    loaded = Settings(_env_file=None)  # type: ignore[call-arg]

    assert loaded.domain == "passes.example"
    assert loaded.chain_id == 1


def test_a_typo_in_the_env_file_is_an_error(tmp_path: Path) -> None:
    """A misspelled name that is ignored is a setting you think you changed and did not.

    This holds for .env, which is where a human types one. It does not hold for
    the process environment: pydantic-settings reads only names it recognises
    there, so a typo'd `export` stays silent and no config can change that.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(f"DROPZONE_JWT_SECRET={SECRET}\nDROPZONE_SESSION_TTLL=PT1H\n")

    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        Settings(_env_file=env_file)  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["PT5M", 300, timedelta(minutes=5)])
def test_lifetimes_accept_iso_durations_and_seconds(value: object) -> None:
    """.env.example documents PT5M; a deployment may well pass 300."""
    assert settings(nonce_ttl=value).nonce_ttl == timedelta(minutes=5)


@pytest.mark.parametrize("field", ["nonce_ttl", "session_ttl"])
@pytest.mark.parametrize("value", [0, -60])
def test_a_non_positive_lifetime_is_refused(field: str, value: int) -> None:
    """Zero expires every nonce on issue, which presents as "login is broken"."""
    with pytest.raises(ValidationError, match="positive"):
        settings(**{field: value})


def test_confirmations_below_one_are_refused() -> None:
    """Zero confirmations means honouring a purchase not yet in a block."""
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        settings(required_confirmations=0)


def test_the_defaults_are_the_documented_ones() -> None:
    """Sepolia, a 5 minute challenge, a 15 minute session ceiling."""
    defaults = settings()

    assert defaults.chain_id == 11155111
    assert defaults.nonce_ttl == timedelta(minutes=5)
    assert defaults.session_ttl == timedelta(minutes=15)
    assert defaults.required_confirmations == 1


def test_settings_are_read_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value that changes between two requests is behaviour nobody can reproduce."""
    monkeypatch.setenv("DROPZONE_JWT_SECRET", SECRET)
    get_settings.cache_clear()

    first = get_settings()
    monkeypatch.setenv("DROPZONE_DOMAIN", "changed.example")

    assert get_settings() is first
    get_settings.cache_clear()


def test_the_example_env_file_lists_every_setting() -> None:
    """A setting missing from .env.example is one nobody knows they can set."""
    example = Path(__file__).parent.parent / ".env.example"
    text = example.read_text()

    for name in Settings.model_fields:
        assert f"DROPZONE_{name.upper()}=" in text, f"{name} is undocumented"
