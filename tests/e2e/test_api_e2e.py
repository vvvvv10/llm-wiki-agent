"""端到端闭环测试：真实索引 + 假模型网关，验证 API 全链路。"""

from __future__ import annotations

import json

from tests.conftest import FakeGateway, build_test_app, make_settings, write_wiki


def test_full_question_answer_loop():
    settings = make_settings(wiki_root=str(write_wiki()))
    gateway = FakeGateway(content="RAG 是检索增强生成，先检索证据再回答。[1]")
    app, client = build_test_app(settings=settings, gateway=gateway)

    health = client.get("/health").json()
    assert health["index_ready"] is True

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是 RAG，它解决什么问题？"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["refused"] is False
    assert data["citations"]
    assert any("rag-architecture" in c["path"] for c in data["citations"])

    # 引用证据内容来自真实索引
    system_sent = app.state.gateway.requests[-1].messages[0]
    assert "检索增强生成" in system_sent.content


def test_full_stream_loop():
    settings = make_settings(wiki_root=str(write_wiki()))
    app, client = build_test_app(settings=settings, gateway=FakeGateway())

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llm-wiki-agent",
            "messages": [{"role": "user", "content": "什么是大语言模型？"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    texts = []
    citations = None
    for line in response.text.split("\n\n"):
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        event = json.loads(payload)
        if event["object"] == "chat.completion.chunk":
            texts.append(event["choices"][0]["delta"].get("content", ""))
        elif event["object"] == "wiki.citations":
            citations = event["citations"]
    assert "".join(texts)
    assert citations and citations[0]["path"].startswith("wiki/")
