"""钉钉场景能力（AI 助理直通模式 / 自定义技能）接入。

钉钉开放平台的场景能力通过 Stream 模式的 ``GraphMessage.TOPIC``
（``/v1.0/graph/api/invoke``）回调：请求为 ``GraphRequest``，响应为
``GraphResponse``。详见钉钉开发者百科《直通模式 & 技能开发》。

``DINGTALK_SCENARIO_CAPABILITY_ID`` 在本模块真正进入业务调用链路：
- 作为技能标识注入处理器，用于注册日志与请求日志追踪。
- 随响应元数据 ``capability_id`` 返回，便于钉钉侧与本端对齐核对。

技能协议要求 dingtalk-stream >= 0.22.0。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings
from app.service.answer_service import AnswerService
from app.service.prompts import REFUSAL_TEXT

logger = logging.getLogger(__name__)

SKILL_ERROR_TEXT = "场景能力处理失败，请稍后重试。"
MAX_SKILL_REPLY_CHARS = 4000


def extract_question(body: Any) -> str:
    """从 GraphRequest.body 中提取用户问题文本。

    钉钉技能回调的 body 为 JSON（字符串或 dict），用户输入可能位于
    ``input``、``text``、``query``、``question`` 等字段，也可能是纯文本。
    """
    payload: Any = body
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body.strip()

    if isinstance(payload, dict):
        for key in ("input", "text", "query", "question", "content", "prompt"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = extract_question(value)
                if nested:
                    return nested
        return ""
    if isinstance(payload, list):
        for item in payload:
            nested = extract_question(item)
            if nested:
                return nested
    if isinstance(payload, str):
        return payload.strip()
    return ""


def format_skill_payload(answer) -> dict:
    """把 Answer 转成场景能力的 JSON 响应体。"""
    citations = [
        {
            "id": c.id,
            "path": c.path,
            "title": c.title,
            "section_title": c.section_title,
            "chunk_index": c.chunk_index,
            "excerpt": c.excerpt,
        }
        for c in answer.citations
    ]
    text = (answer.content or REFUSAL_TEXT).strip()
    if len(text) > MAX_SKILL_REPLY_CHARS:
        text = text[: MAX_SKILL_REPLY_CHARS - 20] + "\n……（内容过长已截断）"
    return {
        "text": text,
        "citations": citations,
        "refused": answer.refused,
    }


def create_skill_handler(
    answer_service: AnswerService, settings: Settings
):
    """构造钉钉场景能力（Graph 技能）处理器。

    dingtalk-stream 是可选依赖，缺失时给出明确错误。
    ``settings.dingtalk_scenario_capability_id`` 必须已配置。
    """
    capability_id = (settings.dingtalk_scenario_capability_id or "").strip()
    if not capability_id:
        raise RuntimeError(
            "DINGTALK_SCENARIO_CAPABILITY_ID 未配置，无法注册钉钉场景能力技能"
        )

    try:
        from dingtalk_stream import AckMessage, GraphHandler
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "缺少 dingtalk-stream 依赖，请执行 pip install 'dingtalk-stream>=0.22.0'"
        ) from exc

    class WikiSkillHandler(GraphHandler):
        """把钉钉场景能力请求转成 Wiki 问答。"""

        def __init__(self) -> None:
            super().__init__()
            self.answer_service = answer_service
            self.settings = settings
            self.capability_id = capability_id

        async def process(self, callback: Any) -> Any:
            try:
                from dingtalk_stream import GraphRequest

                request = GraphRequest.from_dict(callback.data or {})
                method = request.request_line.method
                uri = request.request_line.uri
                logger.info(
                    "scenario capability request: capability_id=%s method=%s uri=%s",
                    self.capability_id,
                    method,
                    uri,
                )

                question = extract_question(request.body)
                if not question:
                    response = self.get_success_response(
                        {"text": "请提出一个 Wiki 相关的问题。", "citations": [], "refused": True}
                    )
                    return AckMessage.STATUS_OK, response.to_dict()

                answer = self.answer_service.answer(
                    [{"role": "user", "content": question}]
                )
                payload = format_skill_payload(answer)
                payload["capability_id"] = self.capability_id
                response = self.get_success_response(payload)
                return AckMessage.STATUS_OK, response.to_dict()
            except Exception:
                logger.exception(
                    "scenario capability failed: capability_id=%s", self.capability_id
                )
                response = self.get_success_response(
                    {
                        "text": SKILL_ERROR_TEXT,
                        "citations": [],
                        "refused": True,
                        "capability_id": self.capability_id,
                    }
                )
                return AckMessage.STATUS_OK, response.to_dict()

    return WikiSkillHandler()
