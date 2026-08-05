from fastapi.testclient import TestClient

from dropzone import __version__
from dropzone.api.app import create_app


def test_health_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_health_needs_no_authentication() -> None:
    """An operator's liveness probe cannot hold a session token."""
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={})

    assert response.status_code == 200
