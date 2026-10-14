"""API 请求模型与校验规则。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ALLOWED_ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 50
MAX_MESSAGE_CHARS = 8000
EXPECTED_MODEL = "llm-wiki-agent"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatCompletionRequest(BaseModel):
    model: str = EXPECTED_MODEL
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    stream: bool = False
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        if value != EXPECTED_MODEL:
            raise ValueError(f"model 必须为 {EXPECTED_MODEL}")
        return value

    @field_validator("messages")
    @classmethod
    def _check_messages(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if value[-1].role != "user":
            raise ValueError("最后一条消息必须是 user 角色")
        for message in value:
            if not message.content.strip():
                raise ValueError("消息内容不能为空")
        return value
