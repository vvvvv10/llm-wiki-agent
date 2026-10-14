"""SSE 流式聊天 API 测试。"""

from __future__ import annotations

import json

from tests.conftest import FakeGateway, build_test_app


def _parse_sse(text: str) -> list:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        data = block[len("data:") :].strip()
        if data == "[DONE]":
            events.append("[DONE]")
        else:
            events.append(json.loads(data))
    return events


def _post(client, payload):
    return client.post(
        "/v1/chat/completions",
        json=payload,
    )


def test_stream_success():
    app, client = build_test_app(gateway=FakeGateway())
    response = _post(
        client,
        {
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是 RAG？"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert events[-1] == "[DONE]"

    text = ""
    citations = None
    for event in events:
        if event == "[DONE]":
            continue
        if event["object"] == "chat.completion.chunk":
            delta = event["choices"][0]["delta"]
            text += delta.get("content", "")
        elif event["object"] == "wiki.citations":
            citations = event["citations"]
    assert text == "RAG 是检索增强生成。"
    assert citations and citations[0]["path"].startswith("wiki/")


def test_stream_refusal_has_empty_citations():
    app, client = build_test_app(gateway=FakeGateway())
    response = _post(
        client,
        {
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "公司今年的股价走势如何"}],
            "stream": True,
        },
    )
    events = _parse_sse(response.text)
    text = ""
    citations = None
    for event in events:
        if event == "[DONE]":
            continue
        if event["object"] == "chat.completion.chunk":
            text += event["choices"][0]["delta"].get("content", "")
        elif event["object"] == "wiki.citations":
            citations = event["citations"]
    assert "没有足够证据" in text
    assert citations == []
    assert events[-1] == "[DONE]"


def test_stream_error_event_then_done():
    from app.generation.gateway import ModelUpstreamError

    class BrokenGateway(FakeGateway):
        def stream(self, request):
            yield "部分"
            raise ModelUpstreamError("boom")

    app, client = build_test_app(gateway=BrokenGateway())
    response = _post(
        client,
        {
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是 RAG？"}],
            "stream": True,
        },
    )
    events = _parse_sse(response.text)
    error_events = [e for e in events if isinstance(e, dict) and e["object"] == "wiki.error"]
    assert error_events
    assert error_events[0]["error"]["code"] == "MODEL_UPSTREAM_ERROR"
    assert "boom" not in response.text
    assert events[-1] == "[DONE]"


def test_stream_timeout_error_event():
    from app.generation.gateway import ModelTimeoutError

    class TimeoutGateway(FakeGateway):
        def stream(self, request):
            raise ModelTimeoutError("timeout")
            yield  # pragma: no cover

    app, client = build_test_app(gateway=TimeoutGateway())
    response = _post(
        client,
        {
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是 RAG？"}],
            "stream": True,
        },
    )
    events = _parse_sse(response.text)
    error_events = [e for e in events if isinstance(e, dict) and e["object"] == "wiki.error"]
    assert error_events and error_events[0]["error"]["code"] == "MODEL_TIMEOUT"
    assert events[-1] == "[DONE]"


def test_stream_unexpected_error_still_done():
    class BoomGateway(FakeGateway):
        def stream(self, request):
            raise RuntimeError("internal secret")
            yield  # pragma: no cover

    app, client = build_test_app(gateway=BoomGateway())
    response = _post(
        client,
        {
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是 RAG？"}],
            "stream": True,
        },
    )
    events = _parse_sse(response.text)
    assert events[-1] == "[DONE]"
    assert "secret" not in response.text
