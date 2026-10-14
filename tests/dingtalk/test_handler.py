"""钉钉消息处理测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.dingtalk.handler import (
    ERROR_TEXT,
    MODEL_ERROR_TEXT,
    _extract_text,
    _format_answer,
    _format_sources_markdown,
    _to_chatbot_message,
    create_chatbot_handler,
    run_streaming_card,
)
from app.retrieval.models import Citation
from app.service.answer_service import AnswerService
from app.service.prompts import REFUSAL_TEXT
from tests.conftest import FakeGateway, make_retriever, make_settings


class FakeMessage:
    def __init__(self, text: str = "什么是 RAG？"):
        self.payload = {"content": {"text": text}}
        self.sender_staff_id = "staff-1"
        self.session_webhook = "https://example.com/webhook"


def _answer_service(gateway=None) -> AnswerService:
    return AnswerService(
        retriever=make_retriever(),
        gateway=gateway or FakeGateway(content="RAG 是检索增强生成。[1]"),
        top_k=5,
        min_score=0.1,
    )


def _citation() -> Citation:
    return Citation(
        id="abc",
        path="wiki/rag-architecture.md",
        title="RAG 架构",
        section_title="什么是 RAG",
        chunk_index=0,
        excerpt="RAG 即检索增强生成……",
    )


def test_extract_text_from_payload():
    assert _extract_text(FakeMessage("你好")) == "你好"


def test_extract_text_empty_payload():
    message = FakeMessage()
    message.payload = {}
    assert _extract_text(message) == ""


def test_format_answer_includes_sources():
    text = _format_answer("回答内容", [_citation()], refused=False)
    assert "回答内容" in text
    assert "来源：" in text
    assert "[1] RAG 架构 · 什么是 RAG（wiki/rag-architecture.md）" in text


def test_format_answer_refusal_has_no_sources():
    text = _format_answer(REFUSAL_TEXT, [_citation()], refused=True)
    assert "来源" not in text
    assert REFUSAL_TEXT in text


def test_format_answer_truncates_long_text():
    text = _format_answer("字" * 5000, [], refused=False)
    assert len(text) < 5000
    assert "截断" in text


def test_handler_answers_question():
    handler = create_chatbot_handler(_answer_service(), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    assert len(replies) == 1
    assert "RAG 是检索增强生成" in replies[0]
    assert "来源：" in replies[0]
    assert "wiki/rag-architecture.md" in replies[0]


def test_handler_refusal_reply():
    handler = create_chatbot_handler(_answer_service(), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(FakeMessage("公司今年的股价走势如何")))
    assert REFUSAL_TEXT in replies[0]
    assert "来源" not in replies[0]


def test_handler_empty_question_prompts_user():
    handler = create_chatbot_handler(_answer_service(), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    message = FakeMessage("")
    asyncio.run(handler.process(message))
    assert "文本问题" in replies[0]


def test_handler_error_returns_standard_message(monkeypatch):
    class BrokenGateway(FakeGateway):
        def chat(self, request):
            raise RuntimeError("internal secret detail")

    handler = create_chatbot_handler(_answer_service(BrokenGateway()), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    assert replies[-1] == ERROR_TEXT
    assert all("secret" not in reply for reply in replies)


def test_handler_error_written_to_log(tmp_path, monkeypatch):
    import logging

    from app.dingtalk import runner as runner_module

    log_file = tmp_path / "stream.log"
    # 复用 runner 的文件日志配置
    runner_module.configure_file_logging(log_file)

    class BrokenGateway(FakeGateway):
        def chat(self, request):
            raise RuntimeError("boom-detail")

    handler = create_chatbot_handler(_answer_service(BrokenGateway()), make_settings())
    handler.reply_text = lambda text, message: None
    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    logging.getLogger().handlers[-1].flush()

    content = log_file.read_text(encoding="utf-8")
    assert "boom-detail" in content


def test_handler_requires_dingtalk_stream(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dingtalk_stream":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="dingtalk-stream"):
        create_chatbot_handler(_answer_service(), make_settings())


# ------------------------------------------------------------------ 打字机卡片


class FakeCard:
    """记录 AI 卡片生命周期调用的假卡片。"""

    def __init__(self):
        self.started = 0
        self.snapshots: list[str] = []
        self.finished: list[str] = []
        self.failed = 0
        self.markdown = ""

    def ai_start(self, *args, **kwargs):
        self.started += 1

    def ai_streaming(self, markdown, append=False):
        # 镜像真实 SDK：ai_streaming 会写入 self.markdown
        self.markdown = markdown
        self.snapshots.append(markdown)

    def ai_finish(self, markdown=None, **kwargs):
        if markdown is not None:
            self.markdown = markdown
        self.finished.append(markdown)

    def ai_fail(self):
        # 与真实 AIMarkdownCardInstance.ai_fail 签名一致：无参数，只置失败态。
        # msgContent 由失败前最后一次 ai_streaming 决定。
        self.failed += 1
        self.failed_markdown = self.markdown


def test_run_streaming_card_appends_deltas_and_finishes_with_sources():
    from app.service.models import StreamCitationEvent, StreamTextEvent

    events = [
        StreamTextEvent(text="RAG "),
        StreamTextEvent(text="是检索增强生成。"),
        StreamCitationEvent(citations=[_citation()]),
    ]
    service = AnswerService(
        retriever=make_retriever(),
        gateway=FakeGateway(),
        top_k=5,
        min_score=0.1,
    )
    service.stream = lambda messages: iter(events)

    card = FakeCard()
    run_streaming_card(card, service, "什么是 RAG？")

    assert card.started == 1
    # 每个增量都用全量快照（append=False），最后一帧是完整正文
    assert card.snapshots == ["RAG ", "RAG 是检索增强生成。"]
    assert len(card.finished) == 1
    final = card.finished[0]
    assert final.startswith("RAG 是检索增强生成。")
    assert "来源" in final
    assert "wiki/rag-architecture.md" in final
    assert card.failed == 0


def test_run_streaming_card_refusal_without_sources():
    from app.service.models import StreamTextEvent

    service = AnswerService(
        retriever=make_retriever(), gateway=FakeGateway(), top_k=5, min_score=0.1
    )
    service.stream = lambda messages: iter([StreamTextEvent(text=REFUSAL_TEXT)])

    card = FakeCard()
    run_streaming_card(card, service, "与 Wiki 无关的问题")

    assert card.finished  # 拒答也走完成态
    assert REFUSAL_TEXT in card.finished[0]
    assert "来源" not in card.finished[0]
    assert card.failed == 0


def test_run_streaming_card_error_marks_failed():
    from app.service.models import StreamErrorEvent, StreamTextEvent

    events = [
        StreamTextEvent(text="部分答案"),
        StreamErrorEvent(code="MODEL_TIMEOUT", message="超时"),
    ]
    service = AnswerService(
        retriever=make_retriever(), gateway=FakeGateway(), top_k=5, min_score=0.1
    )
    service.stream = lambda messages: iter(events)

    card = FakeCard()
    run_streaming_card(card, service, "什么是 RAG？")

    assert card.finished == []
    assert card.failed == 1
    assert card.failed_markdown.startswith("部分答案")
    assert "重试" in card.failed_markdown
    assert card.failed_markdown == card.markdown
    # 失败态文案经 ai_streaming 落到 msgContent（最后一帧），因 ai_fail 不带内容
    assert card.snapshots[-1] == card.failed_markdown


def test_run_streaming_card_error_with_no_output_uses_model_error_text():
    from app.service.models import StreamErrorEvent

    service = AnswerService(
        retriever=make_retriever(), gateway=FakeGateway(), top_k=5, min_score=0.1
    )
    service.stream = lambda messages: iter([StreamErrorEvent()])

    card = FakeCard()
    run_streaming_card(card, service, "什么是 RAG？")

    assert card.failed == 1
    assert card.failed_markdown == MODEL_ERROR_TEXT
    assert card.markdown == MODEL_ERROR_TEXT


def test_handler_streaming_card_path_used_when_enabled(monkeypatch):
    """开启流式卡片后，handler 走卡片路径，不再走纯文本 reply。"""
    from app.dingtalk import handler as handler_module

    seen = {}

    def fake_run(card, answer_service, question):
        seen["question"] = question
        card.ai_start()
        card.ai_finish("流式卡片答案")

    monkeypatch.setattr(handler_module, "run_streaming_card", fake_run)

    handler = create_chatbot_handler(
        _answer_service(), make_settings(dingtalk_streaming_card=True)
    )
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    # 让卡片工厂返回假卡片
    fake_card = FakeCard()
    monkeypatch.setattr(handler, "_make_card", lambda message: fake_card)

    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    assert replies == []  # 未走纯文本回复
    assert seen["question"] == "什么是 RAG？"
    assert fake_card.finished == ["流式卡片答案"]


def test_handler_streaming_card_failure_falls_back_to_text(monkeypatch):
    """卡片路径抛异常时回退到纯文本回复，且不泄露内部细节。"""
    from app.dingtalk import handler as handler_module

    def boom(card, answer_service, question):
        raise RuntimeError("card backend secret detail")

    monkeypatch.setattr(handler_module, "run_streaming_card", boom)

    handler = create_chatbot_handler(
        _answer_service(), make_settings(dingtalk_streaming_card=True)
    )
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)
    monkeypatch.setattr(handler, "_make_card", lambda message: FakeCard())

    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    assert len(replies) == 1
    assert "RAG 是检索增强生成" in replies[0]
    assert "secret" not in replies[0]


def test_handler_streaming_disabled_keeps_plain_reply(monkeypatch):
    """开关关闭时保持原有非流式文本回复。"""
    from app.dingtalk import handler as handler_module

    calls = []

    def fake_run(card, answer_service, question):
        calls.append(question)

    monkeypatch.setattr(handler_module, "run_streaming_card", fake_run)

    handler = create_chatbot_handler(
        _answer_service(), make_settings(dingtalk_streaming_card=False)
    )
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(FakeMessage("什么是 RAG？")))
    assert calls == []
    assert len(replies) == 1


def test_format_sources_markdown_empty():
    assert _format_sources_markdown([]) == ""


# ------------------------------------------------------------------
# 真实 SDK 路径：register_callback_handler 传入的是原始 CallbackMessage，
# 需要转换成 ChatbotMessage（含 sender_staff_id / session_webhook）后
# reply_text 才能工作。这些测试是钉钉真实链路的回归防线。
# ------------------------------------------------------------------


def _make_callback_message(text: str = "什么是 RAG？"):
    """构造真实 dingtalk-stream CallbackMessage（.data 为钉钉回调字典）。"""
    from dingtalk_stream.frames import CallbackMessage, Headers

    msg = CallbackMessage()
    msg.headers = Headers()
    msg.data = {
        "msgtype": "text",
        "text": {"content": text},
        "senderStaffId": "staff-1",
        "sessionWebhook": "https://example.com/webhook",
        "msgId": "msg-1",
    }
    return msg


def test_to_chatbot_message_converts_callback_message():
    """真实 CallbackMessage 必须被转成带 sender_staff_id / session_webhook 的消息。"""
    converted = _to_chatbot_message(_make_callback_message("你好"))
    assert hasattr(converted, "sender_staff_id")
    assert converted.sender_staff_id == "staff-1"
    assert converted.session_webhook == "https://example.com/webhook"


def test_to_chatbot_message_is_idempotent():
    """已是 ChatbotMessage（FakeMessage 有这两个字段）时应原样返回。"""
    fake = FakeMessage("你好")
    assert _to_chatbot_message(fake) is fake


def test_extract_text_handles_textcontent_object():
    """真实 SDK 路径下 ChatbotMessage.text 是 TextContent 对象（.content 属性）。"""
    converted = _to_chatbot_message(_make_callback_message("真实文本"))
    assert _extract_text(converted) == "真实文本"


def test_handler_processes_real_callback_message(monkeypatch):
    """端到端：真实 CallbackMessage → 转换 → 提取 → 回复。"""
    handler = create_chatbot_handler(_answer_service(), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(_make_callback_message("什么是 RAG？")))
    assert len(replies) == 1
    assert "RAG 是检索增强生成" in replies[0]
    assert "来源" in replies[0]


def test_handler_real_callback_reply_uses_sender_staff_id(monkeypatch):
    """真实路径下 reply_text 能拿到 sender_staff_id（此前崩溃点）。"""
    seen = {}

    def fake_reply_text(text, message):
        seen["staff"] = message.sender_staff_id
        seen["webhook"] = message.session_webhook

    handler = create_chatbot_handler(_answer_service(), make_settings())
    handler.reply_text = fake_reply_text

    asyncio.run(handler.process(_make_callback_message("什么是 RAG？")))
    assert seen["staff"] == "staff-1"
    assert seen["webhook"] == "https://example.com/webhook"


def test_handler_real_callback_refusal(monkeypatch):
    """真实路径下无关问题仍正确拒答，不误报文本为空。"""
    handler = create_chatbot_handler(_answer_service(), make_settings())
    replies = []
    handler.reply_text = lambda text, message: replies.append(text)

    asyncio.run(handler.process(_make_callback_message("今天杭州天气怎么样")))
    assert len(replies) == 1
    assert REFUSAL_TEXT in replies[0]
    # 关键：不应误判为「请在群里发送文本问题」
    assert "文本问题" not in replies[0]
