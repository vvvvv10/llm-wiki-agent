"""ingestion 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WikiDocument:
    """一篇本地 Markdown 文档。"""

    path: str  # 相对 wiki 根目录的路径
    title: str  # 第一个一级标题；不存在时使用文件名
    content: str  # Markdown 原始内容


@dataclass(frozen=True)
class WikiChunk:
    """文档切分后的一个证据分段。"""

    id: str  # 基于路径、章节、序号与正文的稳定 SHA-256
    document_path: str
    document_title: str
    section_title: str
    chunk_index: int
    content: str
