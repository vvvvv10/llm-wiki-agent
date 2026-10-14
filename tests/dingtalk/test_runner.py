"""钉钉 Worker 配置测试。"""

from __future__ import annotations

import pytest

from app.dingtalk.runner import DingTalkRunner, build_answer_service
from tests.conftest import make_settings, write_wiki


def test_runner_requires_credentials():
    settings = make_settings(dingtalk_client_id="", dingtalk_client_secret="")
    runner = DingTalkRunner(settings)
    with pytest.raises(RuntimeError, match="DINGTALK_CLIENT_ID"):
        runner.start()


def test_build_answer_service_uses_wiki_index():
    settings = make_settings(wiki_root=str(write_wiki()))
    service = build_answer_service(settings)
    snapshot = service.index_store.snapshot
    assert snapshot is not None
    assert len(snapshot.documents) == 2
    assert service.retriever is None  # 通过 index_store 动态取快照
    results = service._current_retriever().search("什么是 RAG")
    assert results


def test_runner_settings_include_scenario_capability_id():
    settings = make_settings(dingtalk_scenario_capability_id="cap-123")
    assert settings.dingtalk_scenario_capability_id == "cap-123"
    runner = DingTalkRunner(settings)
    assert runner.settings.dingtalk_scenario_capability_id == "cap-123"


def test_runner_registers_skill_topic_when_capability_id_set(monkeypatch):
    """配置了场景能力 ID 时，Runner 必须把 Graph 技能 topic 注册到 Stream client。"""
    import dingtalk_stream

    from app.dingtalk import runner as runner_module

    settings = make_settings(
        dingtalk_client_id="cid",
        dingtalk_client_secret="sec",
        dingtalk_scenario_capability_id="cap-123",
        wiki_root=str(write_wiki()),
    )

    registered = []

    class FakeClient:
        def __init__(self, credential):
            self.credential = credential

        def register_callback_handler(self, topic, handler):
            registered.append((topic, handler))

        def start_forever(self):
            return None

    monkeypatch.setattr(dingtalk_stream, "DingTalkStreamClient", FakeClient)
    monkeypatch.setattr(runner_module, "configure_file_logging", lambda *a, **k: None)

    runner = DingTalkRunner(settings)
    runner.start()  # FakeClient 不会真正连网

    topics = [topic for topic, _ in registered]
    assert dingtalk_stream.graph.GraphMessage.TOPIC in topics  # 场景能力技能
    assert dingtalk_stream.ChatbotMessage.TOPIC in topics  # 群机器人
    skill_handler = next(h for t, h in registered if t == dingtalk_stream.graph.GraphMessage.TOPIC)
    assert skill_handler.capability_id == "cap-123"


def test_runner_skips_skill_registration_without_capability_id(monkeypatch):
    import dingtalk_stream

    from app.dingtalk import runner as runner_module

    settings = make_settings(
        dingtalk_client_id="cid",
        dingtalk_client_secret="sec",
        dingtalk_scenario_capability_id="",
        wiki_root=str(write_wiki()),
    )

    registered = []

    class FakeClient:
        def __init__(self, credential):
            pass

        def register_callback_handler(self, topic, handler):
            registered.append(topic)

        def start_forever(self):
            return None

    monkeypatch.setattr(dingtalk_stream, "DingTalkStreamClient", FakeClient)
    monkeypatch.setattr(runner_module, "configure_file_logging", lambda *a, **k: None)

    DingTalkRunner(settings).start()
    assert dingtalk_stream.graph.GraphMessage.TOPIC not in registered
    assert dingtalk_stream.ChatbotMessage.TOPIC in registered


def test_build_answer_service_honors_retrieval_settings():
    settings = make_settings(
        wiki_root=str(write_wiki()),
        retrieval_top_k=1,
        retrieval_min_score=0.99,
    )
    service = build_answer_service(settings)
    assert service.top_k == 1
    assert service.min_score == 0.99


def test_ensure_ssl_certificates_noop_when_ca_present(monkeypatch):
    """默认 CA 已存在时不应做任何兜底。"""
    import os
    import ssl

    from app.dingtalk import runner as runner_module

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: type("P", (), {"cafile": __file__, "capath": None})(),
    )
    assert runner_module.ensure_ssl_certificates() is False
    assert "SSL_CERT_FILE" not in os.environ


def test_ensure_ssl_certificates_falls_back_to_certifi(monkeypatch):
    """默认 CA 缺失时用 certifi 兜底，并设置 SSL_CERT_FILE。"""
    import os
    import ssl

    from app.dingtalk import runner as runner_module

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: type("P", (), {"cafile": "/nonexistent/cert.pem", "capath": None})(),
    )
    assert runner_module.ensure_ssl_certificates() is True
    assert os.environ.get("SSL_CERT_FILE", "").endswith("cacert.pem")
