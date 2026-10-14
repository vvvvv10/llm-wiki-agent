"""聊天 API（非流式）测试。"""

from __future__ import annotations

from tests.conftest import FakeGateway, build_test_app


def _payload(content: str = "什么是 RAG？", **extra):
    payload = {
        "model": "llm-wiki-agent",
        "messages": [{"role": "user", "content": content}],
    }
    payload.update(extra)
    return payload


def test_non_stream_success():
    app, client = build_test_app(gateway=FakeGateway(content="RAG 是检索增强生成。[1]"))
    response = client.post(
        "/v1/chat/completions",
        json=_payload(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "llm-wiki-agent"
    assert data["choices"][0]["message"]["content"] == "RAG 是检索增强生成。[1]"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["refused"] is False
    assert data["citations"]
    citation = data["citations"][0]
    assert set(citation) == {"id", "path", "title", "section_title", "chunk_index", "excerpt"}
    assert citation["path"].startswith("wiki/")
    assert not citation["path"].startswith("/")
    assert data["usage"]["total_tokens"] == 15
    assert data["id"].startswith("chatcmpl_")


def test_refusal_response():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload("公司今年的股价走势如何"),
    )
    data = response.json()
    assert data["refused"] is True
    assert "没有足够证据" in data["choices"][0]["message"]["content"]
    assert data["citations"] == []


def test_wrong_model_name_rejected():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload() | {"model": "gpt-4"},
    )
    assert response.status_code == 422


def test_last_message_must_be_user():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llm-wiki-agent",
            "messages": [
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "回答"},
            ],
        },
    )
    assert response.status_code == 422


def test_invalid_role_rejected():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llm-wiki-agent",
            "messages": [{"role": "tool", "content": "x"}],
        },
    )
    assert response.status_code == 422


def test_empty_message_rejected():
    app, client = build_test_app(gateway=FakeGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload("   "),
    )
    assert response.status_code == 422


def test_top_k_bounds():
    app, client = build_test_app(gateway=FakeGateway())

    def post_top_k(top_k):
        return client.post(
            "/v1/chat/completions", json=_payload(top_k=top_k)
        )

    assert post_top_k(0).status_code == 422
    assert post_top_k(21).status_code == 422
    assert post_top_k(20).status_code == 200


def test_message_limits():
    app, client = build_test_app(gateway=FakeGateway())
    too_many = [{"role": "user", "content": "q"} for _ in range(51)]
    response = client.post(
        "/v1/chat/completions",
        json={"model": "llm-wiki-agent", "messages": too_many},
    )
    assert response.status_code == 422
    long_message = [{"role": "user", "content": "字" * 8001}]
    response = client.post(
        "/v1/chat/completions",
        json={"model": "llm-wiki-agent", "messages": long_message},
    )
    assert response.status_code == 422


def test_model_timeout_maps_to_504():
    from app.generation.gateway import ModelTimeoutError

    class TimeoutGateway(FakeGateway):
        def chat(self, request):
            raise ModelTimeoutError("timeout")

    app, client = build_test_app(gateway=TimeoutGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload(),
    )
    assert response.status_code == 504
    assert "超时" in response.json()["error"]["message"]


def test_model_auth_error_maps_to_502():
    from app.generation.gateway import ModelAuthenticationError

    class AuthGateway(FakeGateway):
        def chat(self, request):
            raise ModelAuthenticationError("auth")

    app, client = build_test_app(gateway=AuthGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload(),
    )
    assert response.status_code == 502
    body = response.text
    assert "auth" not in body.lower()
    assert "Bearer" not in body


def test_errors_do_not_leak_internals():
    class BoomGateway(FakeGateway):
        def chat(self, request):
            raise RuntimeError("secret-path /Users/alice/keys.env")

    app, client = build_test_app(gateway=BoomGateway())
    response = client.post(
        "/v1/chat/completions",
        json=_payload(),
    )
    assert response.status_code == 502
    assert "secret-path" not in response.text
