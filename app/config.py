"""统一环境变量配置。

所有密钥与可调参数都从环境变量读取，敏感值不写入代码和文档。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:  # python-dotenv 是可选依赖，缺失时直接读取进程环境变量。
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - 运行环境差异
    pass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, str(default)).lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    host_port: int
    wiki_root: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    retrieval_top_k: int
    retrieval_min_score: float
    max_context_chars: int
    chunk_max_chars: int
    chunk_overlap_chars: int
    history_max_messages: int
    dingtalk_client_id: str
    dingtalk_client_secret: str
    dingtalk_scenario_capability_id: str
    dingtalk_streaming_card: bool

    @property
    def model_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def dingtalk_configured(self) -> bool:
        return bool(self.dingtalk_client_id and self.dingtalk_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_host=_env("APP_HOST", "0.0.0.0"),
        app_port=_env_int("APP_PORT", 8080),
        host_port=_env_int("HOST_PORT", 8081),
        wiki_root=_env("WIKI_ROOT", "wiki"),
        llm_base_url=_env("LLM_BASE_URL", "http://localhost:8001/v1"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL", "local-model"),
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 30.0),
        retrieval_top_k=_env_int("RETRIEVAL_TOP_K", 5),
        retrieval_min_score=_env_float("RETRIEVAL_MIN_SCORE", 0.1),
        max_context_chars=_env_int("MAX_CONTEXT_CHARS", 12000),
        chunk_max_chars=_env_int("CHUNK_MAX_CHARS", 1200),
        chunk_overlap_chars=_env_int("CHUNK_OVERLAP_CHARS", 120),
        history_max_messages=_env_int("HISTORY_MAX_MESSAGES", 6),
        dingtalk_client_id=_env("DINGTALK_CLIENT_ID"),
        dingtalk_client_secret=_env("DINGTALK_CLIENT_SECRET"),
        dingtalk_scenario_capability_id=_env("DINGTALK_SCENARIO_CAPABILITY_ID"),
        dingtalk_streaming_card=_env_bool("DINGTALK_STREAMING_CARD", True),
    )
