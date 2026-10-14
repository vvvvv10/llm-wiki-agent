"""OpenAI Compatible 模型网关。

请求地址：``<LLM_BASE_URL 去除末尾斜线>/chat/completions``。
所有上游异常在此映射为标准内部异常，由 API 层转成对外状态码。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx

from app.generation.models import ModelRequest, ModelResponse, ModelUsage

logger = logging.getLogger(__name__)


class ModelError(RuntimeError):
    """模型调用错误基类。"""


class ModelAuthenticationError(ModelError):
    """401/403：认证失败。"""


class ModelRateLimitError(ModelError):
    """429：限流。"""


class ModelUpstreamError(ModelError):
    """其他 4xx/5xx 或协议错误。"""


class ModelTimeoutError(ModelError):
    """请求超时。"""


class ModelGateway:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        enable_thinking: bool | None = None,
        max_tokens: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        # None 表示请求里不携带该参数（对任意 OpenAI 兼容后端安全）。
        # qwen3 系列默认开启思考模式：先生成大段 reasoning 再输出正文，
        # 客服场景证据已随 prompt 给全，关掉思考可把回答耗时从 ~20s 降到 ~5s。
        self.enable_thinking = enable_thinking
        self.max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------ 非流式
    def chat(self, request: ModelRequest) -> ModelResponse:
        payload = self._payload(request, stream=False)
        response = self._post(payload)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ModelUpstreamError("模型返回内容不是合法 JSON") from exc

        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelUpstreamError("模型响应结构不符合 Chat Completions 协议") from exc

        usage = ModelUsage.from_payload(data.get("usage"))
        return ModelResponse(content=content, usage=usage)

    # -------------------------------------------------------------------- 流式
    def stream(self, request: ModelRequest) -> Iterator[str]:
        """按行读取上游 SSE，逐 token 产出 ``choices[0].delta.content``。"""
        payload = self._payload(request, stream=True)
        try:
            with self._client.stream(
                "POST",
                self._endpoint(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            ) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ModelUpstreamError("模型 SSE 数据不是合法 JSON") from exc
                    choices = parsed.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise ModelUpstreamError("模型服务网络错误") from exc

    # -------------------------------------------------------------------- 内部
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ModelRequest, stream: bool) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": stream,
        }
        if self.enable_thinking is not None:
            payload["enable_thinking"] = self.enable_thinking
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

    def _post(self, payload: dict) -> httpx.Response:
        try:
            response = self._client.post(
                self._endpoint(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise ModelUpstreamError("模型服务网络错误") from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise ModelAuthenticationError("模型服务认证失败")
        if status == 429:
            raise ModelRateLimitError("模型服务限流")
        if status >= 400:
            body = ""
            try:
                body = response.text[:200]
            except Exception:  # pragma: no cover - 流式响应无正文
                pass
            logger.warning("model upstream error status=%s body=%s", status, body)
            raise ModelUpstreamError(f"模型服务返回错误状态 {status}")
