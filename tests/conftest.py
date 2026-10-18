import pytest
from fastapi.testclient import TestClient

from dropzone.api.app import create_app
from dropzone.auth.nonces import NonceStore
from dropzone.chain.memory import InMemoryChainGateway
from dropzone.config import Settings

TEST_SECRET = "a" * 32


@pytest.fixture(autouse=True)
def _default_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests calling create_app() or get_settings() have a default secret.

    Tests specifically verifying missing secrets (like test_config.py) monkeypatch.delenv
    to clear this.
    """
    monkeypatch.setenv("DROPZONE_JWT_SECRET", TEST_SECRET)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        jwt_secret=TEST_SECRET,  # type: ignore[arg-type]
        domain="localhost:8000",
        uri="http://localhost:8000",
        chain_id=11155111,
    )


@pytest.fixture
def chain_gateway() -> InMemoryChainGateway:
    return InMemoryChainGateway()


@pytest.fixture
def nonce_store(test_settings: Settings) -> NonceStore:
    return NonceStore(ttl=test_settings.nonce_ttl)


@pytest.fixture
def client(
    test_settings: Settings,
    chain_gateway: InMemoryChainGateway,
    nonce_store: NonceStore,
) -> TestClient:
    app = create_app(
        settings=test_settings,
        chain_gateway=chain_gateway,
        nonce_store=nonce_store,
    )
    return TestClient(app)
