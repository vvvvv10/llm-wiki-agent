"""钉钉场景能力（AI 助理直通模式 / 自定义技能）测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.dingtalk.skill import (
    SKILL_ERROR_TEXT,
    create_skill_handler,
    extract_question,
    format_skill_payload,
)
from app.retrieval.models import Citation
from app.service.answer_service import AnswerService
from app.service.prompts import REFUSAL_TEXT
from tests.conftest import FakeGateway, make_retriever, make_settings

CAPABILITY_ID = "cap-test-001"


def _answer_service(gateway=None) -> AnswerService:
    return AnswerService(
        retriever=make_retriever(),
        gateway=gateway or FakeGateway(content="RAG 是检索增强生成。[1]"),
        top_k=5,
        min_score=0.1,
    )


def _settings(**overrides):
    base = dict(dingtalk_scenario_capability_id=CAPABILITY_ID)
    base.update(overrides)
    return make_settings(**base)


def _citation() -> Citation:
    return Citation(
        id="abc",
        path="wiki/rag-architecture.md",
        title="RAG 架构",
        section_title="什么是 RAG",
        chunk_index=0,
        excerpt="RAG 即检索增强生成……",
    )


class FakeCallback:
    def __init__(self, body, method="POST", uri="/api/wiki/ask"):
        self.data = {
            "requestLine": {"method": method, "uri": uri},
            "headers": {"Content-Type": "application/json"},
            "body": body,
        }


# ---------------------------------------------------------------- extract_question


def test_extract_from_json_string_input():
    body = json.dumps({"input": "什么是 RAG？"})
    assert extract_question(body) == "什么是 RAG？"


def test_extract_from_dict_text():
    assert extract_question({"text": "你好"}) == "你好"


def test_extract_from_nested_content():
    body = json.dumps({"content": {"query": "什么是 RAG？"}})
    assert extract_question(body) == "什么是 RAG？"


def test_extract_from_list_payload():
    body = json.dumps([{"question": "问题一"}])
    assert extract_question(body) == "问题一"


def test_extract_plain_text():
    assert extract_question("  纯文本问题  ") == "纯文本问题"


def test_extract_empty():
    assert extract_question("") == ""
    assert extract_question("{}") == ""


# ---------------------------------------------------------------- format_skill_payload


def _answer(content="回答内容", citations=None, refused=False):
    from app.service.models import Answer

    return Answer(content=content, citations=citations or [], refused=refused)


def test_format_payload_includes_citations():
    payload = format_skill_payload(_answer(citations=[_citation()]))
    assert payload["text"] == "回答内容"
    assert payload["refused"] is False
    assert payload["citations"][0]["path"] == "wiki/rag-architecture.md"
    assert payload["citations"][0]["title"] == "RAG 架构"


def test_format_payload_refusal():
    payload = format_skill_payload(_answer(REFUSAL_TEXT, refused=True))
    assert payload["refused"] is True
    assert payload["citations"] == []


def test_format_payload_truncates_long_text():
    payload = format_skill_payload(_answer("字" * 5000))
    assert len(payload["text"]) < 5000
    assert "截断" in payload["text"]


# ---------------------------------------------------------------- create_skill_handler


def test_requires_capability_id():
    with pytest.raises(RuntimeError, match="DINGTALK_SCENARIO_CAPABILITY_ID"):
        create_skill_handler(_answer_service(), _settings(dingtalk_scenario_capability_id=""))


def test_capability_id_injected_into_handler():
    handler = create_skill_handler(_answer_service(), _settings())
    assert handler.capability_id == CAPABILITY_ID


# ---------------------------------------------------------------- process（真实协议）


def _process(handler, body):
    return asyncio.run(handler.process(FakeCallback(body)))


def _decode(result):
    status, response = result
    payload = json.loads(response["body"])
    return status, payload


def test_skill_answers_question():
    handler = create_skill_handler(_answer_service(), _settings())
    body = json.dumps({"input": "什么是 RAG？"})
    status, payload = _decode(_process(handler, body))
    assert status == 200  # AckMessage.STATUS_OK
    assert payload["text"] == "RAG 是检索增强生成。[1]"
    assert payload["refused"] is False
    assert payload["citations"]
    # 场景能力 ID 真正出现在业务响应中
    assert payload["capability_id"] == CAPABILITY_ID


def test_skill_uses_graph_protocol_fields():
    handler = create_skill_handler(_answer_service(), _settings())
    body = json.dumps({"input": "什么是 RAG？"})
    _, response = _process(handler, body)
    assert response["statusLine"]["code"] == 200
    assert response["statusLine"]["reasonPhrase"] == "OK"
    assert response["headers"]["Content-Type"] == "application/json"
    assert response["body"]


def test_skill_refusal_reply():
    handler = create_skill_handler(_answer_service(), _settings())
    body = json.dumps({"input": "公司今年的股价走势如何"})
    status, payload = _decode(_process(handler, body))
    assert payload["refused"] is True
    assert payload["text"] == REFUSAL_TEXT
    assert payload["citations"] == []
    assert payload["capability_id"] == CAPABILITY_ID


def test_skill_empty_question_prompts_user():
    handler = create_skill_handler(_answer_service(), _settings())
    status, payload = _decode(_process(handler, "{}"))
    assert payload["refused"] is True
    assert "Wiki 相关的问题" in payload["text"]


def test_skill_error_returns_standard_message():
    class BrokenGateway(FakeGateway):
        def chat(self, request):
            raise RuntimeError("internal secret detail")

    handler = create_skill_handler(_answer_service(BrokenGateway()), _settings())
    body = json.dumps({"input": "什么是 RAG？"})
    status, payload = _decode(_process(handler, body))
    assert status == 200
    assert payload["text"] == SKILL_ERROR_TEXT
    assert "secret" not in json.dumps(payload, ensure_ascii=False)
    assert payload["capability_id"] == CAPABILITY_ID


def test_skill_error_written_to_log(tmp_path):
    import logging

    from app.dingtalk import runner as runner_module

    log_file = tmp_path / "stream.log"
    runner_module.configure_file_logging(log_file)

    class BrokenGateway(FakeGateway):
        def chat(self, request):
            raise RuntimeError("skill-boom-detail")

    handler = create_skill_handler(_answer_service(BrokenGateway()), _settings())
    body = json.dumps({"input": "什么是 RAG？"})
    _process(handler, body)
    logging.getLogger().handlers[-1].flush()
    content = log_file.read_text(encoding="utf-8")
    assert "skill-boom-detail" in content
    assert CAPABILITY_ID in content
