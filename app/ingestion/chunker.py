"""Markdown 分段器。

- 一级至四级标题形成章节边界。
- 每个 Chunk 最多 ``max_chars`` 字符。
- 超长章节在换行、中文句号或英文句号附近切分，相邻 Chunk 保留重叠。
- Chunk ID 在内容不变时保持稳定（SHA-256）。
"""

from __future__ import annotations

import hashlib
import re

from app.ingestion.models import WikiChunk, WikiDocument

_SECTION_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_SPLIT_MARKERS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", ";")


class MarkdownChunker:
    def __init__(self, max_chars: int = 1200, overlap_chars: int = 120) -> None:
        self.max_chars = max(200, max_chars)
        self.overlap_chars = max(0, min(overlap_chars, self.max_chars // 2))

    def chunk(self, document: WikiDocument) -> list[WikiChunk]:
        sections = self._sections(document.content)
        chunks: list[WikiChunk] = []
        chunk_index = 0
        for section_title, body in sections:
            for piece in self._split(body):
                content = piece.strip()
                if not content:
                    continue
                chunks.append(
                    WikiChunk(
                        id=self._chunk_id(document.path, section_title, chunk_index, content),
                        document_path=document.path,
                        document_title=document.title,
                        section_title=section_title,
                        chunk_index=chunk_index,
                        content=content,
                    )
                )
                chunk_index += 1
        return chunks

    @staticmethod
    def _sections(content: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, list[str]]] = []
        current_title = "前言"
        current_lines: list[str] = []
        for line in content.splitlines():
            match = _SECTION_RE.match(line)
            if match:
                sections.append((current_title, current_lines))
                current_title = match.group(2).strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        sections.append((current_title, current_lines))

        result: list[tuple[str, str]] = []
        for title, lines in sections:
            body = "\n".join(lines).strip()
            if body:
                result.append((title, body))
        return result

    def _split(self, text: str) -> list[str]:
        if len(text) <= self.max_chars:
            return [text]

        pieces: list[str] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(start + self.max_chars, length)
            if end < length:
                cut = self._best_cut(text, start, end)
                if cut > start:
                    end = cut
            pieces.append(text[start:end])
            if end >= length:
                break
            start = max(end - self.overlap_chars, start + 1)
        return pieces

    @staticmethod
    def _best_cut(text: str, start: int, end: int) -> int:
        window_low = start + (end - start) // 2
        best = -1
        for marker in _SPLIT_MARKERS:
            index = text.rfind(marker, window_low, end)
            if index != -1:
                best = max(best, index + len(marker))
        return best if best != -1 else end

    @staticmethod
    def _chunk_id(path: str, section_title: str, chunk_index: int, content: str) -> str:
        payload = f"{path}|{section_title}|{chunk_index}|{content}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
