"""Application assembly.

`create_app` exists so tests build their own instance rather than importing a
module-level singleton. Once there is configuration, a database and a chain
gateway, this is where they get wired in — and being able to construct an app
with substituted dependencies is what keeps the acceptance suite from needing
a network.
"""

from fastapi import FastAPI

from dropzone import __version__
from dropzone.api import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="DropZone",
        version=__version__,
        summary="Time-limited access passes, bought on-chain and enforced off-chain",
    )
    app.include_router(health.router)
    return app


app = create_app()
