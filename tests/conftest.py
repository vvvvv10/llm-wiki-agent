"""测试共享工具。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.generation.models import ModelRequest, ModelResponse, ModelUsage


class FakeGateway:
    """测试用假模型网关，不发起真实网络请求。"""

    def __init__(
        self,
        content: str = "RAG 是检索增强生成。[1]",
        usage: ModelUsage | None = None,
        stream_deltas: list[str] | None = None,
    ):
        self.content = content
        self.usage = usage or ModelUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        self.requests: list[ModelRequest] = []
        self.stream_calls = 0
        self.stream_deltas = stream_deltas or ["RAG ", "是检索", "增强生成。"]
        self.close_called = False

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, usage=self.usage)

    def stream(self, request: ModelRequest):
        self.stream_calls += 1
        self.requests.append(request)
        yield from self.stream_deltas

    def close(self) -> None:
        self.close_called = True


SAMPLE_DOCS = {
    "rag-architecture.md": (
        "# RAG 架构\n\n"
        "## 什么是 RAG\n\n"
        "RAG 即检索增强生成，先从知识库检索相关证据，再让大模型依据证据回答，"
        "能有效减少幻觉并支持引用溯源。\n\n"
        "## 引用与拒答\n\n"
        "当检索不到足够证据时，系统应当明确拒答而不是编造答案。\n"
    ),
    "llm-basics.md": (
        "# 大语言模型基础\n\n"
        "## 什么是大语言模型\n\n"
        "大语言模型是基于 Transformer 的大规模语言模型，通过预训练学习语言规律。\n\n"
        "## 采样参数\n\n"
        "温度 temperature 控制输出随机性，较低温度更稳定。\n"
    ),
}


def write_wiki(docs: dict[str, str] | None = None) -> Path:
    docs = docs or SAMPLE_DOCS
    root = Path(tempfile.mkdtemp()) / "wiki"
    for name, content in docs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def make_retriever(docs: dict[str, str] | None = None):
    from app.ingestion.chunker import MarkdownChunker
    from app.ingestion.loader import MarkdownLoader
    from app.retrieval.bm25 import Bm25Retriever

    root = write_wiki(docs)
    documents = MarkdownLoader(root).load()
    chunker = MarkdownChunker()
    chunks = []
    for document in documents:
        chunks.extend(chunker.chunk(document))
    return Bm25Retriever(chunks)


def make_settings(**overrides):
    """构造测试用 Settings，默认指向临时 wiki 且不配置模型。"""
    from app.config import Settings

    defaults = dict(
        app_host="127.0.0.1",
        app_port=8080,
        host_port=8081,
        wiki_root=str(write_wiki()),
        llm_base_url="http://localhost:8001/v1",
        llm_api_key="",
        llm_model="local-model",
        llm_timeout_seconds=5.0,
        retrieval_top_k=5,
        retrieval_min_score=0.1,
        max_context_chars=12000,
        chunk_max_chars=1200,
        chunk_overlap_chars=120,
        history_max_messages=6,
        dingtalk_client_id="",
        dingtalk_client_secret="",
        dingtalk_scenario_capability_id="",
        dingtalk_streaming_card=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def build_test_app(settings=None, gateway=None):
    """构建带测试配置与假网关的 FastAPI 应用。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = settings or make_settings()
    app = create_app(settings)
    if gateway is not None:
        from app.service.answer_service import AnswerService

        app.state.gateway = gateway
        app.state.answer_service = AnswerService(
            index_store=app.state.index_store,
            gateway=gateway,
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
            max_context_chars=settings.max_context_chars,
            history_max_messages=settings.history_max_messages,
        )
    client = TestClient(app, raise_server_exceptions=False)
    return app, client
