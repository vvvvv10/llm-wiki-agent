"""钉钉 Stream Worker 启动入口。

- 使用 Client ID / Client Secret 主动建立长连接，不要求公网回调地址。
- Worker 构建自己的一份内存索引（与 API 进程独立，Wiki 更新后需重启）。
- ``DINGTALK_SCENARIO_CAPABILITY_ID`` 已进入业务调用链路：配置后注册钉钉
  AI 助理直通模式技能（``GraphMessage.TOPIC``），见 ``app/dingtalk/skill.py``。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.dingtalk.handler import create_chatbot_handler
from app.generation.gateway import ModelGateway
from app.ingestion.chunker import MarkdownChunker
from app.retrieval.index_store import IndexStore
from app.service.answer_service import AnswerService

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "stream.log"


def ensure_ssl_certificates() -> bool:
    """确保 SSL 证书链可用；缺失时用 certifi 的 CA bundle 兜底。

    某些 Python（尤其 python.org 安装包）未安装系统 CA，导致连接钉钉
    WebSocket 时报 ``CERTIFICATE_VERIFY_FAILED``。``ssl.create_default_context()``
    （websockets/asyncio 走的路径）只认 OpenSSL 层的 ``SSL_CERT_FILE`` 环境
    变量，不认 ``ssl._create_default_https_context``，故此处直接设置该环境
    变量，使同步与异步 TLS 握手一并生效。返回是否发生了兜底。
    """
    import ssl

    defaults = ssl.get_default_verify_paths()
    existing = [
        path
        for path in (defaults.cafile, defaults.capath)
        if path and os.path.exists(path)
    ]
    if existing:
        return False
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        logger.info("system CA missing; using certifi CA bundle via SSL_CERT_FILE")
        return True
    except ImportError:
        logger.warning(
            "system CA missing and certifi unavailable; TLS verification may fail"
        )
        return False


def configure_file_logging(log_file: str | Path = DEFAULT_LOG_FILE) -> None:
    """把 WARNING 及以上日志写入本地文件，便于排障。"""
    root = logging.getLogger()
    path = Path(log_file)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)


def build_answer_service(settings: Settings) -> AnswerService:
    store = IndexStore(
        settings.wiki_root,
        chunker=MarkdownChunker(
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        ),
    )
    store.rebuild()
    gateway = ModelGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        enable_thinking=settings.llm_enable_thinking,
        max_tokens=settings.llm_max_tokens,
    )
    return AnswerService(
        index_store=store,
        gateway=gateway,
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
        max_context_chars=settings.max_context_chars,
        history_max_messages=settings.history_max_messages,
    )


class DingTalkRunner:
    def __init__(self, settings: Settings | None = None, log_file: str = DEFAULT_LOG_FILE) -> None:
        self.settings = settings or get_settings()
        self.log_file = log_file
        self._client = None

    def start(self) -> None:
        try:
            from dingtalk_stream import Credential, DingTalkStreamClient
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise RuntimeError(
                "缺少 dingtalk-stream 依赖，请执行 pip install dingtalk-stream"
            ) from exc

        settings = self.settings
        if not settings.dingtalk_configured:
            raise RuntimeError(
                "DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET 未配置，请检查 .env"
            )

        configure_file_logging(self.log_file)

        logger.info("building wiki index for dingtalk worker (root=%s)", settings.wiki_root)
        answer_service = build_answer_service(settings)
        snapshot = answer_service.index_store.snapshot
        logger.info(
            "index ready: documents=%d chunks=%d",
            len(snapshot.documents) if snapshot else 0,
            len(snapshot.chunks) if snapshot else 0,
        )
        handler = create_chatbot_handler(answer_service, settings)
        credential = Credential(
            settings.dingtalk_client_id, settings.dingtalk_client_secret
        )
        client = DingTalkStreamClient(credential)
        from dingtalk_stream import ChatbotMessage

        client.register_callback_handler(ChatbotMessage.TOPIC, handler)

        # 场景能力 ID 真正进入业务调用链路：注册钉钉 AI 助理技能
        # （GraphMessage.TOPIC）。仅在配置了场景能力 ID 时启用。
        if settings.dingtalk_scenario_capability_id.strip():
            from dingtalk_stream import graph

            from app.dingtalk.skill import create_skill_handler

            skill_handler = create_skill_handler(answer_service, settings)
            client.register_callback_handler(
                graph.GraphMessage.TOPIC, skill_handler
            )
            logger.info(
                "scenario capability registered: capability_id=%s topic=%s",
                settings.dingtalk_scenario_capability_id.strip(),
                graph.GraphMessage.TOPIC,
            )
        else:
            logger.info(
                "scenario capability disabled: DINGTALK_SCENARIO_CAPABILITY_ID 未配置，"
                "仅注册群机器人消息回调"
            )

        self._client = client
        logger.info("dingtalk stream worker starting (pid=%s)", os.getpid())
        client.start_forever()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    ensure_ssl_certificates()
    runner = DingTalkRunner()
    try:
        runner.start()
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("dingtalk stream worker stopped by user")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
