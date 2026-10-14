"""中英文混合分词。

- 英文按小写单词切分。
- 中文优先使用 jieba；jieba 不可用时退化为双字滑窗，保证检索始终可用。
"""

from __future__ import annotations

import re
from pathlib import Path

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 常见中文停用词：避免"的/了/是"等虚词造成弱匹配噪声。
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "也", "就", "都", "而", "及其",
    "一个", "我们", "你们", "他们", "这个", "那个", "这些", "那些", "什么",
    "怎么", "如何", "怎样", "可以", "需要", "进行", "通过", "使用", "或者",
    "如果", "因为", "所以", "但是", "并且", "以及", "对于", "关于", "由于",
}

# 英文停用词
_EN_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "of", "to",
    "and", "or", "in", "on", "at", "for", "with", "as", "by", "it", "its",
    "this", "that", "these", "those", "from", "into", "than", "then",
}

try:
    import jieba

    # 加载领域用户词典：品牌名/成分名为 OOV 词，不加会被切成错误片段。
    _USERDICT = Path(__file__).with_name("userdict.txt")
    if _USERDICT.is_file():
        jieba.load_userdict(str(_USERDICT))
    _HAS_JIEBA = True
except Exception:  # pragma: no cover - jieba 可选
    jieba = None  # type: ignore[assignment]
    _HAS_JIEBA = False


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    for match in _WORD_RE.finditer(lowered):
        word = match.group()
        if word in _EN_STOPWORDS:
            continue
        tokens.append(word)
    for match in _CJK_RE.finditer(lowered):
        segment = match.group()
        if _HAS_JIEBA:
            tokens.extend(
                part.strip()
                for part in jieba.lcut(segment)
                if part.strip() and part.strip() not in _STOPWORDS
            )
        else:  # pragma: no cover - 仅在缺少 jieba 时生效
            if len(segment) == 1:
                if segment not in _STOPWORDS:
                    tokens.append(segment)
            else:
                tokens.extend(
                    segment[i : i + 2]
                    for i in range(len(segment) - 1)
                    if segment[i : i + 2] not in _STOPWORDS
                )
    return tokens
