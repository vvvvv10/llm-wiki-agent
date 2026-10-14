"""模型调用数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelMessage:
    role: str  # system / user / assistant
    content: str


@dataclass(frozen=True)
class ModelRequest:
    messages: list[ModelMessage]
    stream: bool = False


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @staticmethod
    def from_payload(payload: dict | None) -> "ModelUsage":
        if not payload:
            return ModelUsage()
        return ModelUsage(
            prompt_tokens=int(payload.get("prompt_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or 0),
            total_tokens=int(payload.get("total_tokens") or 0),
        )


@dataclass(frozen=True)
class ModelResponse:
    content: str
    usage: ModelUsage = field(default_factory=ModelUsage)
