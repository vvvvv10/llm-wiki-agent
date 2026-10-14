"""健康检查测试。"""

from __future__ import annotations

from tests.conftest import build_test_app, make_settings


def test_health_no_auth(tmp_path):
    app, client = build_test_app(make_settings())
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["index_ready"] is True
    assert data["documents"] >= 1
    assert data["chunks"] >= 1
    assert data["index_version"]
    assert data["model_configured"] is False
    assert data["index_error"] is None


def test_health_does_not_leak_paths(tmp_path):
    app, client = build_test_app()
    data = client.get("/health").json()
    assert all(not str(v).startswith("/") for v in data.values() if isinstance(v, str))


def test_request_id_header_present():
    app, client = build_test_app()
    response = client.get("/health")
    assert response.headers.get("X-Request-Id")


def test_request_id_echoed():
    app, client = build_test_app()
    response = client.get("/health", headers={"X-Request-Id": "trace-123"})
    assert response.headers.get("X-Request-Id") == "trace-123"
