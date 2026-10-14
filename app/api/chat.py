"""聊天 API：非流式与 SSE 流式 Chat Completions。"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.models import ChatCompletionRequest
from app.generation.gateway import (
    ModelAuthenticationError,
    ModelError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUpstreamError,
)
from app.service.models import StreamCitationEvent, StreamErrorEvent, StreamTextEvent

logger = logging.getLogger(__name__)

router = APIRouter()


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _model_error_response(exc: ModelError) -> JSONResponse:
    if isinstance(exc, ModelTimeoutError):
        return _error_response(504, "MODEL_TIMEOUT", "模型请求超时，请稍后重试。")
    if isinstance(exc, (ModelAuthenticationError, ModelRateLimitError, ModelUpstreamError)):
        return _error_response(502, "MODEL_UPSTREAM_ERROR", "模型服务暂时不可用，请稍后重试。")
    return _error_response(502, "MODEL_ERROR", "模型调用失败，请稍后重试。")


def _chat_completion_id() -> str:
    return f"chatcmpl_{uuid.uuid4().hex}"


@router.post("/v1/chat/completions")
def chat_completions(payload: ChatCompletionRequest, request: Request):
    answer_service = request.app.state.answer_service

    if answer_service is None or request.app.state.index_store.snapshot is None:
        return _error_response(422, "INDEX_UNAVAILABLE", "Wiki 索引不可用，请稍后重试。")

    messages = [{"role": m.role, "content": m.content} for m in payload.messages]

    if payload.stream:
        return StreamingResponse(
            _stream_events(answer_service, messages, payload.top_k),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        answer = answer_service.answer(messages, top_k=payload.top_k)
    except ModelError as exc:
        logger.warning("model call failed: %s", type(exc).__name__)
        return _model_error_response(exc)
    except Exception:
        logger.exception("unexpected answer failure")
        return _error_response(502, "INTERNAL_ERROR", "回答生成失败，请稍后重试。")

    return JSONResponse(
        content={
            "id": _chat_completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer.content},
                    "finish_reason": "stop",
                }
            ],
            "citations": [citation.__dict__ for citation in answer.citations],
            "usage": {
                "prompt_tokens": answer.prompt_tokens,
                "completion_tokens": answer.completion_tokens,
                "total_tokens": answer.total_tokens,
            },
            "refused": answer.refused,
        }
    )


def _sse(data: dict | str) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_events(answer_service, messages: list[dict[str, str]], top_k: int | None):
    completion_id = _chat_completion_id()
    created = int(time.time())
    yielded_citations = False

    yield _sse(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "llm-wiki-agent",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    )

    try:
        for event in answer_service.stream(messages, top_k=top_k):
            if isinstance(event, StreamTextEvent):
                yield _sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": "llm-wiki-agent",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": event.text},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            elif isinstance(event, StreamCitationEvent):
                yielded_citations = True
                yield _sse(
                    {
                        "id": completion_id,
                        "object": "wiki.citations",
                        "citations": [c.__dict__ for c in event.citations],
                    }
                )
            elif isinstance(event, StreamErrorEvent):
                yield _sse(
                    {
                        "object": "wiki.error",
                        "error": {"code": event.code, "message": event.message},
                    }
                )
    except ModelError as exc:
        logger.warning("stream model call failed: %s", type(exc).__name__)
        if isinstance(exc, ModelTimeoutError):
            code, message = "MODEL_TIMEOUT", "模型请求超时，请稍后重试。"
        else:
            code, message = "MODEL_UPSTREAM_ERROR", "模型服务暂时不可用。"
        yield _sse({"object": "wiki.error", "error": {"code": code, "message": message}})
    except Exception:
        logger.exception("unexpected stream failure")
        yield _sse(
            {
                "object": "wiki.error",
                "error": {"code": "STREAM_ERROR", "message": "流式回答失败。"},
            }
        )

    if not yielded_citations:
        yield _sse({"object": "wiki.citations", "citations": []})

    yield _sse("[DONE]")
