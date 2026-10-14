"""Markdown 加载器测试。"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.loader import MarkdownLoader


def test_load_markdown_files(tmp_path: Path):
    (tmp_path / "a.md").write_text("# 文档 A\n\n内容 A\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("# 文档 B\n\n内容 B\n", encoding="utf-8")

    documents = MarkdownLoader(tmp_path).load()
    paths = {doc.path for doc in documents}
    assert paths == {"a.md", "sub/b.md"}
    titles = {doc.path: doc.title for doc in documents}
    assert titles["a.md"] == "文档 A"
    assert titles["sub/b.md"] == "文档 B"


def test_skip_non_markdown_and_hidden(tmp_path: Path):
    (tmp_path / "note.txt").write_text("plain text", encoding="utf-8")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("# 秘密\n", encoding="utf-8")
    (tmp_path / "visible.md").write_text("# 可见\n", encoding="utf-8")

    documents = MarkdownLoader(tmp_path).load()
    assert [doc.path for doc in documents] == ["visible.md"]


def test_title_fallback_to_filename(tmp_path: Path):
    (tmp_path / "my-doc.md").write_text("没有一级标题的正文\n", encoding="utf-8")
    documents = MarkdownLoader(tmp_path).load()
    assert documents[0].title == "my doc"


def test_symlink_outside_root_is_ignored(tmp_path: Path):
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("# 外部文档\n", encoding="utf-8")
    root = tmp_path / "wiki"
    root.mkdir()
    try:
        (root / "link.md").symlink_to(outside)
    except OSError:  # pragma: no cover - 文件系统不支持
        return
    (root / "normal.md").write_text("# 正常\n", encoding="utf-8")

    documents = MarkdownLoader(root).load()
    assert [doc.path for doc in documents] == ["normal.md"]
    outside.unlink(missing_ok=True)


def test_broken_file_does_not_block_others(tmp_path: Path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00bad utf-8")
    (tmp_path / "good.md").write_text("# 好文件\n", encoding="utf-8")
    documents = MarkdownLoader(tmp_path).load()
    assert [doc.path for doc in documents] == ["good.md"]


def test_missing_root_returns_empty(tmp_path: Path):
    assert MarkdownLoader(tmp_path / "not-exists").load() == []
