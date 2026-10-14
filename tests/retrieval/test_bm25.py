"""BM25 检索器测试。"""

from __future__ import annotations

from app.ingestion.models import WikiChunk
from app.retrieval.bm25 import Bm25Retriever


def _chunk(index: int, title: str, content: str) -> WikiChunk:
    return WikiChunk(
        id=f"id-{index}",
        document_path="doc.md",
        document_title=title,
        section_title=title,
        chunk_index=index,
        content=content,
    )


def _retriever() -> Bm25Retriever:
    return Bm25Retriever(
        [
            _chunk(0, "RAG", "RAG 是检索增强生成，先检索证据再回答。"),
            _chunk(1, "大模型", "大语言模型基于 Transformer，通过预训练学习语言规律。"),
            _chunk(2, "部署", "部署大模型需要关注吞吐和首字延迟。"),
        ]
    )


def test_search_returns_ranked_results():
    results = _retriever().search("什么是 RAG 检索增强生成", top_k=3)
    assert results
    assert results[0].chunk.document_title == "RAG"
    assert 0 < results[0].relative_score <= 1.0


def test_top_k_limits_results():
    results = _retriever().search("大模型 RAG 部署", top_k=1)
    assert len(results) == 1


def test_min_score_filters():
    all_results = _retriever().search("RAG", top_k=3)
    filtered = _retriever().search("RAG", top_k=3, min_score=0.99)
    assert len(filtered) < len(all_results) or len(filtered) == 1
    assert all(r.relative_score >= 0.99 for r in filtered)


def test_no_match_returns_empty():
    results = _retriever().search("量子引力波暗物质", top_k=5)
    assert results == []


def test_empty_index():
    assert Bm25Retriever([]).search("任意问题") == []


def test_empty_query_returns_empty():
    assert _retriever().search("") == []
    assert _retriever().search("   ") == []


def test_english_query_supported():
    results = _retriever().search("transformer pretraining language model", top_k=3)
    assert results
    assert results[0].chunk.document_title == "大模型"
