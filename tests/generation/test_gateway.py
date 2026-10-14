"""模型网关测试（httpx MockTransport，无真实网络）。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.generation.gateway import (
    ModelAuthenticationError,
    ModelGateway,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUpstreamError,
)
from app.generation.models import ModelMessage, ModelRequest


def _request() -> ModelRequest:
    return ModelRequest(messages=[ModelMessage(role="user", content="你好")])


def _gateway(handler) -> ModelGateway:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return ModelGateway(
        base_url="http://llm.example.com/v1/",
        api_key="test-key",
        model="test-model",
        timeout_seconds=5,
        client=client,
    )


def test_endpoint_strips_trailing_slash():
    gateway = _gateway(lambda request: httpx.Response(200, json={}))
    assert gateway._endpoint() == "http://llm.example.com/v1/chat/completions"


def test_non_stream_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "回答内容"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        )

    response = _gateway(handler).chat(_request())
    assert response.content == "回答内容"
    assert response.usage.total_tokens == 18


def test_non_stream_missing_usage_defaults_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )

    response = _gateway(handler).chat(_request())
    assert response.usage.total_tokens == 0


def test_invalid_json_raises():
    gateway = _gateway(lambda request: httpx.Response(200, text="not-json"))
    with pytest.raises(ModelUpstreamError):
        gateway.chat(_request())


def test_invalid_structure_raises():
    gateway = _gateway(lambda request: httpx.Response(200, json={"foo": "bar"}))
    with pytest.raises(ModelUpstreamError):
        gateway.chat(_request())


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (401, ModelAuthenticationError),
        (403, ModelAuthenticationError),
        (429, ModelRateLimitError),
        (500, ModelUpstreamError),
        (400, ModelUpstreamError),
    ],
)
def test_status_mapping(status, exc_type):
    gateway = _gateway(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(exc_type):
        gateway.chat(_request())


def test_timeout_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    gateway = _gateway(handler)
    with pytest.raises(ModelTimeoutError):
        gateway.chat(_request())


def test_stream_parses_sse():
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"世界"}}]}',
        "",
        "data: [DONE]",
        "",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(200, text="\n".join(lines))

    chunks = list(_gateway(handler).stream(_request()))
    assert chunks == ["你好", "世界"]


def test_stream_skips_lines_without_data():
    lines = [
        ": comment",
        "",
        'data: {"choices":[]}',
        "",
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "",
        "data: [DONE]",
        "",
    ]
    gateway = _gateway(lambda request: httpx.Response(200, text="\n".join(lines)))
    assert list(gateway.stream(_request())) == ["ok"]


def test_stream_bad_json_raises():
    lines = ['data: {bad json}', "", "data: [DONE]", ""]
    gateway = _gateway(lambda request: httpx.Response(200, text="\n".join(lines)))
    with pytest.raises(ModelUpstreamError):
        list(gateway.stream(_request()))


def test_stream_status_error_raises():
    gateway = _gateway(lambda request: httpx.Response(401, json={}))
    with pytest.raises(ModelAuthenticationError):
        list(gateway.stream(_request()))


def _capturing_gateway(**kwargs) -> tuple[ModelGateway, list]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    gateway = ModelGateway(
        base_url="http://llm.example.com/v1",
        api_key="k",
        model="m",
        client=client,
        **kwargs,
    )
    return gateway, bodies


def test_payload_omits_optional_params_by_default():
    """默认不携带 enable_thinking / max_tokens——对任意 OpenAI 兼容后端安全。"""
    gateway, bodies = _capturing_gateway()
    gateway.chat(_request())
    assert "enable_thinking" not in bodies[0]
    assert "max_tokens" not in bodies[0]


def test_payload_includes_optional_params_when_configured():
    gateway, bodies = _capturing_gateway(enable_thinking=False, max_tokens=800)
    gateway.chat(_request())
    assert bodies[0]["enable_thinking"] is False
    assert bodies[0]["max_tokens"] == 800
