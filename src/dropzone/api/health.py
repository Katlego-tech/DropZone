"""Liveness endpoint.

Deliberately dependency-free: it answers whether this process is up and serving,
not whether the database or the chain node are reachable. Those get their own
readiness check once they exist, because conflating the two makes an orchestrator
restart a healthy API over a dependency it cannot fix by restarting it.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from dropzone import __version__

router = APIRouter()


class Health(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=Health, tags=["operations"])
def health() -> Health:
    return Health(status="ok", version=__version__)
