"""索引快照与重建测试。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.ingestion.chunker import MarkdownChunker
from app.retrieval.index_store import IndexStore, RebuildInProgressError
from tests.conftest import SAMPLE_DOCS, write_wiki


def _store(tmp_path: Path) -> IndexStore:
    root = write_wiki()
    return IndexStore(str(root), chunker=MarkdownChunker())


def test_rebuild_produces_snapshot(tmp_path: Path):
    store = _store(tmp_path)
    version = store.rebuild()
    snapshot = store.snapshot
    assert snapshot is not None
    assert snapshot.version == version
    assert len(snapshot.documents) == 2
    assert len(snapshot.chunks) > 0
    assert snapshot.retriever is not None


def test_rebuild_replaces_snapshot_atomically(tmp_path: Path):
    store = _store(tmp_path)
    first = store.rebuild()
    second = store.rebuild()
    assert first != second
    assert store.snapshot.version == second


def test_concurrent_rebuild_rejected(tmp_path: Path):
    store = _store(tmp_path)
    started = threading.Event()
    release = threading.Event()

    original_load = store._loader.load

    def slow_load():
        started.set()
        release.wait(timeout=5)
        return original_load()

    store._loader.load = slow_load  # type: ignore[method-assign]

    thread = threading.Thread(target=store.rebuild)
    thread.start()
    assert started.wait(timeout=5)
    with pytest.raises(RebuildInProgressError):
        store.rebuild()
    release.set()
    thread.join(timeout=5)
    assert not store.is_rebuild_running


def test_background_rebuild_rejects_second_call(tmp_path: Path):
    store = _store(tmp_path)
    store.rebuild()

    gate = threading.Event()
    started = threading.Event()
    original_load = store._loader.load

    def slow_load():
        started.set()
        gate.wait(timeout=5)
        return original_load()

    store._loader.load = slow_load  # type: ignore[method-assign]

    thread = store.try_rebuild_background()
    assert thread is not None
    assert started.wait(timeout=5)
    assert store.try_rebuild_background() is None
    gate.set()
    thread.join(timeout=5)
    time.sleep(0.05)
    assert store.snapshot is not None


def test_new_files_visible_after_rebuild(tmp_path: Path):
    root = write_wiki()
    store = IndexStore(str(root), chunker=MarkdownChunker())
    store.rebuild()
    before = len(store.snapshot.documents)
    (root / "agent-design.md").write_text(
        "# Agent 设计\n\n## 工具调用\n\nAgent 通过白名单和参数校验安全调用工具。\n",
        encoding="utf-8",
    )
    store.rebuild()
    assert len(store.snapshot.documents) == before + 1
    results = store.snapshot.retriever.search("Agent 工具调用 白名单")
    assert results
    assert SAMPLE_DOCS is not None
