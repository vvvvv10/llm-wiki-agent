"""进程内 BM25 检索器。

- 相对相关度按“理论最高可达分数”归一化到 [0, 1]，使 ``min_score``
  阈值真正可过滤弱匹配（而不是按本次最高分归一化导致最高分恒为 1.0）。
- 支持 ``top_k``（1 起）限制返回数量。
"""

from __future__ import annotations

from rank_bm25 import BM25Okapi

from app.ingestion.models import WikiChunk
from app.retrieval.models import RetrievalResult
from app.retrieval.tokenizer import tokenize


class Bm25Retriever:
    # 语料是"短 FAQ 小节 + 长产品资料小节"的混合体，默认 b=0.75 的长度
    # 归一化会把产品正文压到 FAQ 之下（产品名等关键信息都在长正文里）。
    # 降低 b 弱化长度惩罚，实测"产品名 + 症状"类查询的召回排序显著改善。
    _B = 0.3
    _K1 = 1.5

    def __init__(self, chunks: list[WikiChunk] | tuple[WikiChunk, ...]) -> None:
        self.chunks: tuple[WikiChunk, ...] = tuple(chunks)
        corpus = [tokenize(chunk.content) for chunk in self.chunks]
        self._bm25 = (
            BM25Okapi(corpus, k1=self._K1, b=self._B) if corpus else None
        )

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievalResult]:
        if self._bm25 is None or not self.chunks:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = list(self._bm25.get_scores(query_tokens))
        max_score = max(scores) if scores else 0.0
        if max_score <= 0:
            return []

        upper_bound = self._max_possible_score(query_tokens)
        if upper_bound <= 0:
            return []

        candidates = [
            (index, score) for index, score in enumerate(scores) if score > 0
        ]
        candidates.sort(key=lambda item: item[1], reverse=True)

        results: list[RetrievalResult] = []
        for index, score in candidates[: max(1, top_k)]:
            relative = min(1.0, score / upper_bound)
            if relative < min_score:
                continue
            results.append(
                RetrievalResult(
                    chunk=self.chunks[index],
                    score=float(score),
                    relative_score=float(relative),
                )
            )
        return results

    def _max_possible_score(self, query_tokens: list[str]) -> float:
        """查询在“每个词都饱和命中”时的理论最高 BM25 分数。"""
        bm25 = self._bm25
        upper = 0.0
        for token in set(query_tokens):
            idf = bm25.idf.get(token)
            if idf is None or idf <= 0:
                continue
            # tf→∞ 时单个词的贡献上限为 (k1 + 1) * idf
            upper += idf * (bm25.k1 + 1)
        return upper
