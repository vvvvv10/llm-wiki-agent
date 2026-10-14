"""问答编排：检索 → 证据约束 → 模型生成 → 引用返回。

- 查询只使用最后一条用户消息。
- 证据不足时不调用模型，直接返回固定拒答文本。
- 发送给模型的证据总长度受 ``max_context_chars`` 限制。
- 引用返回所有实际发送给模型的检索分段。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from app.generation.gateway import (
    ModelError,
    ModelGateway,
    ModelTimeoutError,
)
from app.generation.models import ModelMessage, ModelRequest
from app.retrieval.models import Citation, RetrievalResult, to_citations
from app.service.models import (
    Answer,
    StreamCitationEvent,
    StreamErrorEvent,
    StreamEvent,
    StreamTextEvent,
)
from app.service.prompts import EVIDENCE_HEADER, REFUSAL_TEXT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AnswerService:
    def __init__(
        self,
        retriever=None,
        gateway: ModelGateway | None = None,
        *,
        index_store=None,
        top_k: int = 5,
        min_score: float = 0.1,
        max_context_chars: int = 12000,
        history_max_messages: int = 6,
    ) -> None:
        # 优先使用 index_store 的最新快照，保证重建索引后立即生效；
        # 也可直接注入固定 retriever（测试场景）。
        self.index_store = index_store
        self.retriever = retriever
        self.gateway = gateway
        self.top_k = top_k
        self.min_score = min_score
        self.max_context_chars = max_context_chars
        self.history_max_messages = history_max_messages

    def _current_retriever(self):
        if self.index_store is not None:
            snapshot = self.index_store.snapshot
            return snapshot.retriever if snapshot else None
        return self.retriever

    # ------------------------------------------------------------------ 非流式
    def answer(self, messages: list[dict[str, str]], top_k: int | None = None) -> Answer:
        prepared = self._prepare(messages, top_k)
        if prepared is None:
            return Answer(content=REFUSAL_TEXT, citations=[], refused=True)

        model_request, citations = prepared
        response = self.gateway.chat(model_request)
        return Answer(
            content=response.content.strip(),
            citations=citations,
            refused=False,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

    # -------------------------------------------------------------------- 流式
    def stream(
        self, messages: list[dict[str, str]], top_k: int | None = None
    ) -> Iterator[StreamEvent]:
        prepared = self._prepare(messages, top_k)
        if prepared is None:
            yield StreamTextEvent(text=REFUSAL_TEXT)
            return

        model_request, citations = prepared
        try:
            for delta in self.gateway.stream(model_request):
                yield StreamTextEvent(text=delta)
        except ModelTimeoutError:
            logger.exception("streaming answer timed out")
            yield StreamErrorEvent(code="MODEL_TIMEOUT", message="模型请求超时，请稍后重试。")
            return
        except ModelError:
            logger.exception("streaming answer failed")
            yield StreamErrorEvent(code="MODEL_UPSTREAM_ERROR", message="模型服务暂时不可用。")
            return
        except Exception:
            logger.exception("streaming answer failed")
            yield StreamErrorEvent()
            return
        yield StreamCitationEvent(citations=citations)

    # -------------------------------------------------------------------- 内部
    def _prepare(
        self, messages: list[dict[str, str]], top_k: int | None = None
    ) -> tuple[ModelRequest, list[Citation]] | None:
        question = self._last_user_message(messages)
        effective_top_k = self._clamp_top_k(top_k if top_k is not None else self.top_k)

        retriever = self._current_retriever()
        if retriever is None:
            return None

        results: list[RetrievalResult] = retriever.search(
            question, top_k=effective_top_k, min_score=self.min_score
        )
        if not results:
            return None

        evidence = self._build_evidence(results)
        if not evidence:
            return None

        citations = to_citations(results)

        history = messages[:-1]
        if self.history_max_messages >= 0:
            history = history[-self.history_max_messages :]

        system_message = ModelMessage(
            role="system", content=SYSTEM_PROMPT.format(evidence=evidence)
        )
        prompt_messages = [system_message]
        for item in history:
            role = item.get("role", "user")
            content = item.get("content", "")
            if role in ("user", "assistant") and content:
                prompt_messages.append(ModelMessage(role=role, content=content))
        prompt_messages.append(ModelMessage(role="user", content=question))

        return ModelRequest(messages=prompt_messages, stream=False), citations

    def _clamp_top_k(self, top_k: int) -> int:
        return max(1, min(20, int(top_k)))

    @staticmethod
    def _last_user_message(messages: list[dict[str, str]]) -> str:
        for item in reversed(messages):
            if item.get("role") == "user":
                return item.get("content", "")
        return messages[-1].get("content", "") if messages else ""

    def _build_evidence(self, results: list[RetrievalResult]) -> str:
        blocks: list[str] = []
        used = 0
        for index, result in enumerate(results, start=1):
            chunk = result.chunk
            block = EVIDENCE_HEADER.format(
                index=index,
                path=f"wiki/{chunk.document_path}",
                title=chunk.document_title,
                section=chunk.section_title,
                content=chunk.content,
            )
            separator_len = 2 if blocks else 0  # "\n\n".join 的分隔符
            if used + separator_len + len(block) > self.max_context_chars:
                remaining = self.max_context_chars - used - separator_len
                if remaining <= 0:
                    break
                blocks.append(block[:remaining])
                break
            blocks.append(block)
            used += separator_len + len(block)
        return "\n\n".join(blocks)
