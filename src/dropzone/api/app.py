"""Application assembly.

`create_app` exists so tests build their own instance rather than importing a
module-level singleton. Once there is configuration, a database and a chain
gateway, this is where they get wired in — and being able to construct an app
with substituted dependencies is what keeps the acceptance suite from needing
a network.
"""

from fastapi import FastAPI

from dropzone import __version__
from dropzone.api import auth, content, health
from dropzone.auth.nonces import NonceStore
from dropzone.chain.gateway import ChainGateway
from dropzone.chain.memory import InMemoryChainGateway
from dropzone.config import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    chain_gateway: ChainGateway | None = None,
    nonce_store: NonceStore | None = None,
) -> FastAPI:
    """Build a configured FastAPI application instance."""
    app_settings = settings if settings is not None else get_settings()
    app_chain = chain_gateway if chain_gateway is not None else InMemoryChainGateway()
    app_nonces = nonce_store if nonce_store is not None else NonceStore(ttl=app_settings.nonce_ttl)

    app = FastAPI(
        title="DropZone",
        version=__version__,
        summary="Time-limited access passes, bought on-chain and enforced off-chain",
    )

    app.state.settings = app_settings
    app.state.chain_gateway = app_chain
    app.state.nonce_store = app_nonces

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(content.router)

    return app


try:
    app = create_app()
except Exception:
    # When imported in an environment where DROPZONE_JWT_SECRET is not yet configured,
    # prevent import-time failure so tooling and tests can construct their own app.
    app = None  # type: ignore[assignment]
