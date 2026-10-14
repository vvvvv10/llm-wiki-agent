"""配置加载测试。"""

from __future__ import annotations

import app.config as config_module


def test_defaults(monkeypatch):
    for key in [
        "APP_HOST", "APP_PORT", "HOST_PORT",
        "WIKI_ROOT", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT_SECONDS",
        "RETRIEVAL_TOP_K", "RETRIEVAL_MIN_SCORE", "MAX_CONTEXT_CHARS",
        "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET", "DINGTALK_SCENARIO_CAPABILITY_ID",
    ]:
        monkeypatch.delenv(key, raising=False)
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()
    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 8080
    assert settings.host_port == 8081
    assert settings.wiki_root == "wiki"
    assert settings.llm_base_url == "http://localhost:8001/v1"
    assert settings.llm_model == "local-model"
    assert settings.llm_timeout_seconds == 30.0
    assert settings.retrieval_top_k == 5
    assert settings.retrieval_min_score == 0.1
    assert settings.max_context_chars == 12000
    assert settings.model_configured is False
    assert settings.dingtalk_configured is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("RETRIEVAL_TOP_K", "8")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "3.8-max")
    monkeypatch.setenv("LLM_BASE_URL", "https://relay.example.com/v1/")
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "cid")
    monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "sec")
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()
    assert settings.app_port == 9000
    assert settings.retrieval_top_k == 8
    assert settings.llm_model == "3.8-max"
    assert settings.model_configured is True
    assert settings.dingtalk_configured is True


def test_invalid_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("APP_PORT", "not-a-number")
    config_module.get_settings.cache_clear()
    assert config_module.get_settings().app_port == 8080
