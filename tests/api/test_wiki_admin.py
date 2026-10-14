"""Wiki 管理 API 测试。"""

from __future__ import annotations

import time

from tests.conftest import FakeGateway, build_test_app


def test_documents_list():
    app, client = build_test_app(gateway=FakeGateway())
    data = client.get("/v1/wiki/documents").json()
    paths = [item["path"] for item in data["items"]]
    assert "rag-architecture.md" in paths
    assert paths == sorted(paths)
    item = next(i for i in data["items"] if i["path"] == "rag-architecture.md")
    assert item["title"] == "RAG 架构"
    assert item["chunks"] >= 1
    assert item["summary"]
    assert all(not item["path"].startswith("/") for item in data["items"])


def test_reindex_and_status():
    app, client = build_test_app(gateway=FakeGateway())
    before = client.get("/health").json()["index_version"]

    response = client.post("/v1/wiki/reindex")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "RUNNING"
    task_id = body["task_id"]

    deadline = time.time() + 10
    status = "RUNNING"
    while time.time() < deadline:
        status = client.get(f"/v1/wiki/reindex/{task_id}").json()["status"]
        if status != "RUNNING":
            break
        time.sleep(0.05)
    assert status == "SUCCEEDED"
    after = client.get("/health").json()["index_version"]
    assert after != before


def test_unknown_task_returns_404():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.get("/v1/wiki/reindex/not-exist")
    assert response.status_code == 404


def test_concurrent_reindex_returns_409():
    app, client = build_test_app(gateway=FakeGateway())
    store = app.state.index_store

    import threading

    gate = threading.Event()
    original_load = store._loader.load

    def slow_load():
        gate.wait(timeout=5)
        return original_load()

    store._loader.load = slow_load  # type: ignore[method-assign]
    first = client.post("/v1/wiki/reindex")
    assert first.status_code == 202
    time.sleep(0.1)  # 等待后台线程真正持锁
    second = client.post("/v1/wiki/reindex")
    assert second.status_code == 409
    gate.set()
    time.sleep(0.2)


def test_get_document():
    app, client = build_test_app(gateway=FakeGateway())
    resp = client.get("/v1/wiki/documents/rag-architecture.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "rag-architecture.md"
    assert body["content"].startswith("# RAG 架构")


def test_get_document_not_found():
    app, client = build_test_app(gateway=FakeGateway())
    resp = client.get("/v1/wiki/documents/no-such.md")
    assert resp.status_code == 404


def test_get_document_rejects_non_md():
    app, client = build_test_app(gateway=FakeGateway())
    resp = client.get("/v1/wiki/documents/secret.txt")
    assert resp.status_code == 400


def test_get_document_rejects_path_escape():
    app, client = build_test_app(gateway=FakeGateway())
    resp = client.get("/v1/wiki/documents/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 400


def test_put_and_delete_document_roundtrip():
    app, client = build_test_app(gateway=FakeGateway())
    root = app.state.settings.wiki_root

    # create
    resp = client.put(
        "/v1/wiki/documents/new-doc.md",
        json={"content": "# 新文档\n\n这是测试内容。\n"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is True
    assert body["reindex_required"] is True

    from pathlib import Path

    path = Path(root) / "new-doc.md"
    assert path.is_file()

    # update
    resp = client.put(
        "/v1/wiki/documents/new-doc.md",
        json={"content": "# 更新后\n"},
    )
    assert resp.status_code == 201
    assert resp.json()["created"] is False
    assert path.read_text(encoding="utf-8") == "# 更新后\n"

    # delete
    resp = client.delete("/v1/wiki/documents/new-doc.md")
    assert resp.status_code == 200
    assert not path.exists()


def test_put_document_rejects_path_escape():
    app, client = build_test_app(gateway=FakeGateway())
    resp = client.put(
        "/v1/wiki/documents/..%2F..%2Fescape.md",
        json={"content": "x"},
    )
    assert resp.status_code == 400


def test_document_write_then_reindex_makes_content_searchable():
    """写入新文档 + reindex 后，新内容应能被检索到。"""
    app, client = build_test_app(gateway=FakeGateway())

    client.put(
        "/v1/wiki/documents/knowledge.md",
        json={"content": "# 审批制度\n\n## 报销审批\n\n报销单超过五千元需要总监审批。\n"},
    )
    resp = client.post("/v1/wiki/reindex")
    task_id = resp.json()["task_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        status = client.get(f"/v1/wiki/reindex/{task_id}").json()["status"]
        if status != "RUNNING":
            break
        time.sleep(0.05)
    assert status == "SUCCEEDED"

    docs = client.get("/v1/wiki/documents").json()["items"]
    paths = [d["path"] for d in docs]
    assert "knowledge.md" in paths
