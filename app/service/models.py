"""service 层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.retrieval.models import Citation


@dataclass(frozen=True)
class Answer:
    """一次问答的完整结果。"""

    content: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class StreamTextEvent:
    """流式文本增量。"""

    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class StreamCitationEvent:
    """流式结束后的引用事件。"""

    type: str = "citations"
    citations: list[Citation] = field(default_factory=list)


@dataclass(frozen=True)
class StreamErrorEvent:
    """流式过程中的错误事件。"""

    type: str = "error"
    code: str = "STREAM_ERROR"
    message: str = "流式回答失败。"


StreamEvent = StreamTextEvent | StreamCitationEvent | StreamErrorEvent
