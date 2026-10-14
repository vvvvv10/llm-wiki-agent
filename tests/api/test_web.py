"""Web 页面测试。"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeGateway, build_test_app

WEB_PATH = Path(__file__).resolve().parents[2] / "app" / "web" / "index.html"
ADMIN_PATH = Path(__file__).resolve().parents[2] / "app" / "web" / "admin.html"


def test_root_serves_page():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "LLM Wiki Agent" in response.text


def test_page_does_not_embed_server_keys():
    _, client = build_test_app(gateway=FakeGateway())
    body = client.get("/").text
    assert "LLM_API_KEY" not in body


def test_page_needs_no_user_key():
    """问答页面不应要求用户输入任何密钥。"""
    html = WEB_PATH.read_text(encoding="utf-8")
    assert "apiKey" not in html
    assert "Authorization" not in html
    assert "localStorage" not in html


def test_page_supports_streaming_and_citations():
    html = WEB_PATH.read_text(encoding="utf-8")
    assert "text/event-stream" in html or "stream" in html
    assert "wiki.citations" in html
    # 使用 textContent 渲染，避免把模型内容当 HTML 执行
    assert "textContent" in html
    assert "innerHTML" not in html


def test_page_has_health_and_presets():
    html = WEB_PATH.read_text(encoding="utf-8")
    assert "/health" in html
    assert "汉肤欣治猫癣" in html
    assert "Shift+Enter" in html


def test_root_links_to_admin_page():
    _, client = build_test_app(gateway=FakeGateway())
    body = client.get("/").text
    assert "/admin" in body


def test_admin_page_serves():
    _, client = build_test_app(gateway=FakeGateway())
    response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Wiki 管理" in response.text


def test_admin_page_needs_no_user_key():
    """管理页面不应要求用户输入任何密钥。"""
    html = ADMIN_PATH.read_text(encoding="utf-8")
    assert "adminKey" not in html
    assert "Authorization" not in html
    assert "localStorage" not in html


def test_admin_page_uses_textcontent_not_innerhtml():
    html = ADMIN_PATH.read_text(encoding="utf-8")
    assert "textContent" in html
    assert "innerHTML" not in html


def test_admin_page_supports_document_lifecycle():
    html = ADMIN_PATH.read_text(encoding="utf-8")
    assert "/v1/wiki/documents" in html
    assert "/v1/wiki/reindex" in html
    # 读写与删除、重建索引均需覆盖
    assert "PUT" in html
    assert "DELETE" in html
    assert "reindex" in html
