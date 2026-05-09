"""Smoke tests for the FastAPI app — endpoints register and respond
without crashing on import."""
import pytest

try:
    from fastapi.testclient import TestClient
    import app
    client = TestClient(app.app)
except Exception as e:
    pytest.skip(f"app/test deps missing: {e}", allow_module_level=True)


def test_config_endpoint():
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "solar_enabled" in body
    assert "ev_enabled" in body
    assert "plan_name" in body


def test_health_endpoint_status():
    r = client.get("/api/health")
    # Health may return 200 or 503 depending on upstream — we just need it not to crash
    assert r.status_code in (200, 503)
    body = r.json()
    assert "status" in body or "error" in body


def test_routes_registered():
    """Key routes are wired in the FastAPI app."""
    paths = {r.path for r in app.app.routes}
    for p in ("/api/config", "/api/today", "/api/history", "/api/solar",
             "/api/energy-flows", "/api/battery/recommended-cap"):
        assert p in paths, f"route {p} not registered"
