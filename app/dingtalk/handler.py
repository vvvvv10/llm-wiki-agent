"""钉钉群机器人消息处理。

- 流式卡片模式（默认开启）：收到群消息后先创建 AI Markdown 卡片，
  用打字机效果逐 token 更新（FR-036 / GAP-002），结束时附引用来源。
  卡片创建失败自动回退到纯文本回复。
- 非流式模式：调用 ``AnswerService.answer()`` 一次性回复正文 + 来源。
- 任何异常统一回复提示语，堆栈写入 ``stream.log``（由 runner 配置的日志文件）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.service.answer_service import AnswerService
from app.service.models import StreamCitationEvent, StreamErrorEvent, StreamTextEvent
from app.service.prompts import REFUSAL_TEXT

logger = logging.getLogger(__name__)

ERROR_TEXT = "处理消息失败，请稍后重试。"
MODEL_ERROR_TEXT = "模型服务暂时不可用，请稍后重试。"
STREAM_ERROR_TEXT = "生成中断，请稍后重试。"
MAX_REPLY_CHARS = 4000


def _to_chatbot_message(message: Any) -> Any:
    """把 SDK 传入的原始 ``CallbackMessage`` 转成 ``ChatbotMessage``。

    dingtalk-stream 的 ``register_callback_handler`` 把原始 ``CallbackMessage``
    直接传给 ``process``，不做自动转换；而 ``reply_text`` 等回复方法需要
    ``ChatbotMessage``（含 ``sender_staff_id`` / ``session_webhook`` 等字段）。
    此处做一次幂等转换：已是 ``ChatbotMessage`` 则原样返回。
    """
    # 已经是 ChatbotMessage（例如测试直接构造），无需转换。
    if hasattr(message, "session_webhook") and hasattr(message, "sender_staff_id"):
        return message
    data = getattr(message, "data", None)
    if isinstance(data, dict) and data:
        try:
            from dingtalk_stream import ChatbotMessage

            return ChatbotMessage.from_dict(data)
        except Exception:  # pragma: no cover - SDK 兼容
            logger.exception("failed to convert CallbackMessage to ChatbotMessage")
    return message


def _extract_text(message: Any) -> str:
    """从钉钉 ChatbotMessage 中提取文本内容。"""
    text = ""
    payload = getattr(message, "payload", None) or {}
    if isinstance(payload, dict):
        content = payload.get("content") or {}
        if isinstance(content, dict):
            text = content.get("text") or ""
        elif isinstance(content, str):
            text = content
    if not text:
        text = getattr(message, "text", "") or ""
    if callable(text):
        try:
            text = text()
        except Exception:  # pragma: no cover - SDK 兼容
            text = ""
    if not isinstance(text, str) and text is not None:
        # 真实 SDK 路径：ChatbotMessage.text 是 TextContent 对象（.content 属性）。
        text = getattr(text, "content", "") or ""
    return str(text).strip()


def _format_answer(content: str, citations: list, refused: bool = False) -> str:
    lines = [content.strip() or REFUSAL_TEXT]
    if citations and not refused:
        lines.append("")
        lines.append("来源：")
        for index, citation in enumerate(citations, start=1):
            lines.append(
                f"[{index}] {citation.title} · {citation.section_title}（{citation.path}）"
            )
    text = "\n".join(lines)
    if len(text) > MAX_REPLY_CHARS:
        text = text[: MAX_REPLY_CHARS - 20] + "\n……（内容过长已截断）"
    return text


def _format_sources_markdown(citations: list) -> str:
    """把引用渲染成卡片里的 Markdown 来源列表。"""
    if not citations:
        return ""
    lines = ["", "---", "**来源：**"]
    for index, citation in enumerate(citations, start=1):
        lines.append(f"{index}. {citation.title} · {citation.section_title}（`{citation.path}`）")
    return "\n".join(lines)


def run_streaming_card(card: Any, answer_service: AnswerService, question: str) -> None:
    """用打字机卡片流式回答一个问题（FR-036 / GAP-002）。

    ``card`` 需提供 ``ai_start()`` / ``ai_streaming(markdown, append)`` /
    ``ai_finish(markdown)`` / ``ai_fail()``（即 ``AIMarkdownCardInstance``）。
    此函数只做编排，便于单测时注入假卡片。

    失败态：真实 ``ai_fail()`` 只置状态、不带 ``msgContent``，故先用
    ``ai_streaming`` 把失败态文案写入卡片内容，再 ``ai_fail()`` 置失败。
    """
    card.ai_start()
    buffer = ""
    citations: list = []
    failed = False

    for event in answer_service.stream([{"role": "user", "content": question}]):
        if isinstance(event, StreamTextEvent):
            buffer += event.text
            card.ai_streaming(buffer, append=False)
        elif isinstance(event, StreamCitationEvent):
            citations = event.citations
        elif isinstance(event, StreamErrorEvent):
            logger.warning("streaming answer failed: %s", event.code)
            failed = True

    if failed:
        # ai_fail() 不带 msgContent，先用 ai_streaming 写入失败态文案，再置失败态。
        # 已吐出部分正文说明模型可达、只是中途断流；完全无输出更可能是模型不可用。
        if buffer.strip():
            failure_text = buffer + "\n\n" + STREAM_ERROR_TEXT
        else:
            failure_text = MODEL_ERROR_TEXT
        failure_text = failure_text[:MAX_REPLY_CHARS]
        card.ai_streaming(failure_text, append=False)
        card.ai_fail()
        return

    final = buffer.strip() or REFUSAL_TEXT
    final += _format_sources_markdown(citations)
    card.ai_finish(final[:MAX_REPLY_CHARS])


def create_chatbot_handler(answer_service: AnswerService, settings: Settings | None = None):
    """构造钉钉 ChatbotHandler 子类实例。

    dingtalk-stream 是可选依赖，缺失时给出明确错误。
    """
    try:
        from dingtalk_stream import ChatbotHandler
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "缺少 dingtalk-stream 依赖，请执行 pip install dingtalk-stream"
        ) from exc

    class WikiChatbotHandler(ChatbotHandler):
        def __init__(self) -> None:
            super().__init__()
            self.answer_service = answer_service
            self.settings = settings

        def _make_card(self, message: Any):
            """构造打字机 AI 卡片；SDK 缺失卡片能力时返回 None。"""
            try:
                from dingtalk_stream.card_instance import AIMarkdownCardInstance
            except ImportError:  # pragma: no cover - 取决于 SDK 版本
                return None
            card = AIMarkdownCardInstance(self.dingtalk_client, message)
            card.set_title_and_logo("Wiki 问答", "")
            return card

        async def process(self, message: Any) -> Any:
            # SDK 把原始 CallbackMessage 传进来，先转成 ChatbotMessage
            # （reply_text / AIMarkdownCardInstance 都依赖其字段）。
            message = _to_chatbot_message(message)
            try:
                question = _extract_text(message)
                if not question:
                    self.reply_text("请在群里发送文本问题。", message)
                    return None

                if self._streaming_enabled():
                    if await self._process_streaming_card(question, message):
                        return None

                answer = self.answer_service.answer(
                    [{"role": "user", "content": question}]
                )
                reply = _format_answer(answer.content, answer.citations, answer.refused)
                self.reply_text(reply, message)
            except Exception:
                logger.exception("failed to handle dingtalk chatbot message")
                try:
                    self.reply_text(ERROR_TEXT, message)
                except Exception:  # pragma: no cover - 网络异常
                    logger.exception("failed to send error reply")
            return None

        def _streaming_enabled(self) -> bool:
            return bool(self.settings and self.settings.dingtalk_streaming_card)

        async def _process_streaming_card(
            self, question: str, message: Any
        ) -> bool:
            """尝试用打字机卡片回答；成功返回 True，需回退时返回 False。"""
            card = self._make_card(message)
            if card is None:
                return False
            try:
                # 卡片建卡与推送是同步阻塞 SDK 调用，丢到线程池避免卡住事件循环。
                import anyio.to_thread

                await anyio.to_thread.run_sync(
                    run_streaming_card, card, self.answer_service, question
                )
                return True
            except Exception:
                logger.exception("streaming card failed; falling back to text reply")
                return False

    return WikiChatbotHandler()
