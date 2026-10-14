"""Markdown 分段器测试。"""

from __future__ import annotations

from app.ingestion.chunker import MarkdownChunker
from app.ingestion.models import WikiDocument


def _doc(content: str) -> WikiDocument:
    return WikiDocument(path="doc.md", title="文档", content=content)


def test_split_by_headings():
    content = "# 文档\n\n## 第一节\n\n内容一\n\n## 第二节\n\n内容二\n"
    chunks = MarkdownChunker().chunk(_doc(content))
    assert [c.section_title for c in chunks] == ["文档", "第一节", "第二节"]
    assert chunks[0].content.startswith("# 文档")
    assert chunks[1].content == "## 第一节\n\n内容一"
    assert chunks[2].chunk_index == 2


def test_h4_is_section_boundary():
    content = "# 文档\n\n#### 四级标题\n\n四级内容\n"
    chunks = MarkdownChunker().chunk(_doc(content))
    assert [c.section_title for c in chunks] == ["文档", "四级标题"]


def test_h5_not_boundary():
    content = "# 文档\n\n##### 五级\n\n内容\n"
    chunks = MarkdownChunker().chunk(_doc(content))
    assert len(chunks) == 1


def test_long_section_split_with_overlap():
    sentence = "这是一个很长的句子，用来测试超长内容切分。"
    body = "".join(f"{sentence}{i}。" for i in range(120))
    content = f"# 文档\n\n## 长节\n\n{body}"
    chunker = MarkdownChunker(max_chars=300, overlap_chars=60)
    chunks = chunker.chunk(_doc(content))
    long_chunks = [c for c in chunks if c.section_title == "长节"]
    assert len(long_chunks) > 1
    assert all(len(c.content) <= 300 for c in long_chunks)
    # 相邻分段应有重叠
    assert long_chunks[0].content[-20:] in long_chunks[1].content


def test_chunk_id_stable_and_unique():
    content = "# 文档\n\n## A\n\n内容 A\n\n## B\n\n内容 B\n"
    chunker = MarkdownChunker()
    first = chunker.chunk(_doc(content))
    second = chunker.chunk(_doc(content))
    assert [c.id for c in first] == [c.id for c in second]
    assert len({c.id for c in first}) == len(first)


def test_empty_content_no_chunks():
    assert MarkdownChunker().chunk(_doc("   \n\n  ")) == []
