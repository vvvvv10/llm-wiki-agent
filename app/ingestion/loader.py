"""Wiki 加载器：Markdown 直读，PDF / DOCX / XLSX 经解析器转 Markdown。

安全规则：
- 忽略隐藏目录中的文件。
- 忽略符号链接。
- 解析后的真实路径必须仍位于 Wiki 根目录内。
- 单个文件读取/解析失败不阻断其他文件。

所有格式最终都产出 ``WikiDocument``（content 为 Markdown 文本），
下游分段与检索对来源格式无感知。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.ingestion.models import WikiDocument
from app.ingestion.parsers import SUPPORTED_EXTS, parse_to_markdown

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class MarkdownLoader:
    def __init__(self, wiki_root: str | Path) -> None:
        self.root = Path(wiki_root).expanduser().resolve()

    def load(self) -> list[WikiDocument]:
        documents: list[WikiDocument] = []
        if not self.root.is_dir():
            logger.warning("wiki root does not exist: %s", self.root)
            return documents

        for path in sorted(self._iter_candidate_files()):
            try:
                if self._is_hidden(path) or path.is_symlink():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(self.root):
                    logger.warning("skip path escaping wiki root: %s", path)
                    continue

                if resolved.suffix.lower() == ".md":
                    text = resolved.read_text(encoding="utf-8")
                else:
                    text = parse_to_markdown(resolved)
            except OSError:
                logger.exception("failed to read file: %s", path)
                continue
            except UnicodeDecodeError:
                logger.exception("failed to decode markdown file as utf-8: %s", path)
                continue
            except Exception:
                # 解析器失败（损坏文件、依赖缺失等）单文件跳过，不阻断整体。
                logger.exception("failed to parse document: %s", path)
                continue

            relative = resolved.relative_to(self.root).as_posix()
            documents.append(
                WikiDocument(path=relative, title=self._title(resolved, text), content=text)
            )
        return documents

    def _iter_candidate_files(self):
        """遍历 wiki 根目录下的 Markdown 与受支持的二进制文档。"""
        patterns = ("*.md",) + tuple(f"*{ext}" for ext in SUPPORTED_EXTS)
        seen: set[Path] = set()
        for pattern in patterns:
            for path in self.root.rglob(pattern):
                if path not in seen:
                    seen.add(path)
                    yield path

    @staticmethod
    def _is_hidden(path: Path) -> bool:
        return any(part.startswith(".") for part in path.parts)

    @staticmethod
    def _title(path: Path, text: str) -> str:
        match = _HEADING_RE.search(text)
        if match:
            return match.group(1).strip()
        return path.stem.replace("-", " ").replace("_", " ").strip() or path.name
