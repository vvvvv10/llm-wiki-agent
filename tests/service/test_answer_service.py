"""AnswerService 测试：拒答、证据、上下文、引用和流式事件。"""

from __future__ import annotations

from app.service.answer_service import AnswerService
from app.service.models import (
    StreamCitationEvent,
    StreamErrorEvent,
    StreamTextEvent,
)
from app.service.prompts import REFUSAL_TEXT, SYSTEM_PROMPT
from tests.conftest import FakeGateway, make_retriever


def _service(gateway=None, **kwargs) -> AnswerService:
    defaults = dict(top_k=5, min_score=0.1, max_context_chars=12000, history_max_messages=6)
    defaults.update(kwargs)
    return AnswerService(
        retriever=make_retriever(),
        gateway=gateway or FakeGateway(),
        **defaults,
    )


def _messages(question: str = "什么是 RAG？") -> list[dict[str, str]]:
    return [{"role": "user", "content": question}]


def test_answer_with_evidence():
    gateway = FakeGateway(content="RAG 是检索增强生成。[1]")
    answer = _service(gateway).answer(_messages())
    assert answer.refused is False
    assert answer.content == "RAG 是检索增强生成。[1]"
    assert answer.citations
    assert answer.citations[0].path.startswith("wiki/")
    assert answer.citations[0].excerpt
    assert answer.total_tokens == 15


def test_refusal_when_no_evidence_does_not_call_model():
    gateway = FakeGateway()
    answer = _service(gateway).answer(_messages("公司今年的股价走势如何"))
    assert answer.refused is True
    assert answer.content == REFUSAL_TEXT
    assert answer.citations == []
    assert gateway.requests == []


def test_prompt_contains_numbered_evidence_and_rules():
    gateway = FakeGateway()
    _service(gateway).answer(_messages("什么是 RAG？"))
    request = gateway.requests[0]
    system = request.messages[0]
    assert system.role == "system"
    assert "[1]" in system.content
    assert "wiki/" in system.content
    assert "不得编造" in SYSTEM_PROMPT
    # Wiki 内容被当作不可信数据
    assert "指令" in system.content or "命令" in system.content


def test_history_limited_to_six_messages():
    gateway = FakeGateway()
    history_msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史 {i}"}
        for i in range(10)
    ]
    messages = (
        [{"role": "system", "content": "原始系统提示"}]
        + history_msgs
        + _messages("什么是 RAG？")
    )
    _service(gateway, history_max_messages=6).answer(messages)
    request = gateway.requests[0]
    # system prompt（固定）+ 6 条历史 + 当前问题
    roles = [m.role for m in request.messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert len(roles) <= 8
    assert all(m.content != "原始系统提示" for m in request.messages[1:])


def test_max_context_chars_limits_evidence():
    gateway = FakeGateway()
    service = _service(gateway, max_context_chars=120)
    answer = service.answer(_messages("什么是 RAG？"))
    if not answer.refused:
        system = gateway.requests[0].messages[0]
        evidence = system.content.split("提供的 Wiki 证据：", 1)[1].lstrip("\n")
        assert len(evidence) <= 120


def test_top_k_clamped():
    gateway = FakeGateway()
    service = _service(gateway, top_k=5)
    service.answer(_messages("什么是 RAG 大语言模型"), top_k=999)
    # clamp 后仍能正常回答
    assert gateway.requests


def test_stream_emits_text_then_citations():
    gateway = FakeGateway()
    events = list(_service(gateway).stream(_messages("什么是 RAG？")))
    assert all(isinstance(e, StreamTextEvent) for e in events[:-1])
    assert isinstance(events[-1], StreamCitationEvent)
    assert events[-1].citations
    text = "".join(e.text for e in events[:-1])
    assert text == "RAG 是检索增强生成。"


def test_stream_refusal_only_emits_text():
    gateway = FakeGateway()
    events = list(_service(gateway).stream(_messages("公司今年的股价走势如何")))
    assert len(events) == 1
    assert isinstance(events[0], StreamTextEvent)
    assert events[0].text == REFUSAL_TEXT


def test_stream_error_event():
    class BrokenGateway(FakeGateway):
        def stream(self, request):
            yield "部分文本"
            raise RuntimeError("boom")

    events = list(_service(BrokenGateway()).stream(_messages("什么是 RAG？")))
    assert isinstance(events[0], StreamTextEvent)
    assert isinstance(events[-1], StreamErrorEvent)
    assert events[-1].code == "STREAM_ERROR"
