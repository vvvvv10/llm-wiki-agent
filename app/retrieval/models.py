"""retrieval 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.ingestion.models import WikiChunk, WikiDocument


@dataclass(frozen=True)
class Citation:
    """对外返回的一条引用证据。"""

    id: str
    path: str  # wiki/<relative-path>
    title: str
    section_title: str
    chunk_index: int
    excerpt: str  # 最多 240 字符


@dataclass(frozen=True)
class RetrievalResult:
    chunk: WikiChunk
    score: float  # BM25 原始分数
    relative_score: float  # 归一化到 [0, 1] 的相对相关度


@dataclass(frozen=True)
class IndexSnapshot:
    """完整构建后一次性替换的不可变索引快照。"""

    version: str
    documents: tuple[WikiDocument, ...]
    chunks: tuple[WikiChunk, ...]
    built_at: datetime
    retriever: Any = None  # Bm25Retriever，与快照同生命周期

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


def to_citations(results: list[RetrievalResult], excerpt_chars: int = 240) -> list[Citation]:
    citations: list[Citation] = []
    for result in results:
        chunk = result.chunk
        citations.append(
            Citation(
                id=chunk.id,
                path=f"wiki/{chunk.document_path}",
                title=chunk.document_title,
                section_title=chunk.section_title,
                chunk_index=chunk.chunk_index,
                excerpt=chunk.content[:excerpt_chars],
            )
        )
    return citations
