"""索引快照与重建管理。

- 完整构建后一次性原子替换 ``IndexSnapshot``，查询不会读到半成品。
- 同一时间只允许一个重建任务；并发重建请求抛出 ``RebuildInProgressError``。
"""

from __future__ import annotations

import logging
import threading
import uuid

from app.ingestion.chunker import MarkdownChunker
from app.ingestion.loader import MarkdownLoader
from app.ingestion.models import WikiChunk
from app.retrieval.bm25 import Bm25Retriever
from app.retrieval.models import IndexSnapshot

logger = logging.getLogger(__name__)


class RebuildInProgressError(RuntimeError):
    """已有索引重建任务正在执行。"""


class IndexStore:
    def __init__(
        self,
        wiki_root: str,
        chunker: MarkdownChunker | None = None,
        loader: MarkdownLoader | None = None,
    ) -> None:
        self.wiki_root = wiki_root
        self._loader = loader or MarkdownLoader(wiki_root)
        self._chunker = chunker or MarkdownChunker()
        self._lock = threading.Lock()
        self._snapshot: IndexSnapshot | None = None
        self._last_error: str | None = None

    @property
    def snapshot(self) -> IndexSnapshot | None:
        return self._snapshot

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def is_rebuild_running(self) -> bool:
        return self._lock.locked()

    def rebuild(self) -> str:
        """构建新索引并原子替换快照，返回新版本号。

        并发调用时立即抛出 ``RebuildInProgressError``。
        """
        if not self._lock.acquire(blocking=False):
            raise RebuildInProgressError("index rebuild already running")
        try:
            documents = self._loader.load()
            chunks: list[WikiChunk] = []
            for document in documents:
                chunks.extend(self._chunker.chunk(document))
            retriever = Bm25Retriever(chunks)
            snapshot = IndexSnapshot(
                version=str(uuid.uuid4()),
                documents=tuple(documents),
                chunks=tuple(chunks),
                built_at=IndexSnapshot.now(),
                retriever=retriever,
            )
            self._snapshot = snapshot
            self._last_error = None
            logger.info(
                "index rebuilt: version=%s documents=%d chunks=%d",
                snapshot.version,
                len(documents),
                len(chunks),
            )
            return snapshot.version
        except Exception:
            self._last_error = "INDEX_BUILD_FAILED"
            raise
        finally:
            self._lock.release()

    def try_rebuild_background(self, on_finished=None) -> threading.Thread | None:
        """尝试在后台线程重建；已有任务时返回 ``None``。"""
        if self.is_rebuild_running:
            return None

        def _run() -> None:
            try:
                version = self.rebuild()
            except Exception:  # pragma: no cover - 由 last_error 暴露
                logger.exception("background index rebuild failed")
                version = None
            if on_finished is not None:
                on_finished(version)

        thread = threading.Thread(target=_run, name="index-rebuild", daemon=True)
        thread.start()
        return thread
