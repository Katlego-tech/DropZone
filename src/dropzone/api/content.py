"""Protected content endpoint.

Requires an active session token in `Authorization: Bearer <token>`.
Zero RPC calls on the request path: the token is verified locally via HMAC-SHA256,
and session lifetime was already clamped to pass expiry at verification time.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dropzone.api.deps import get_session
from dropzone.auth.tokens import SessionPayload

router = APIRouter(tags=["content"])


class ContentResponse(BaseModel):
    message: str
    holder: str
    dataset: dict[str, Any]


@router.get("/content", response_model=ContentResponse)
def get_content(
    session: Annotated[SessionPayload, Depends(get_session)],
) -> ContentResponse:
    """Serve the protected content to a verified session holder."""
    return ContentResponse(
        message="Access granted to protected DropZone dataset.",
        holder=session.address,
        dataset={
            "version": "1.0",
            "status": "active",
            "items": [
                {"id": 101, "name": "Archive Document A", "classification": "restricted"},
                {"id": 102, "name": "Archive Document B", "classification": "restricted"},
            ],
        },
    )
