"""Markdown 预处理与分块（M76 spec §4.2、v3 docs/07 §2.1）。

分块器是所有来源共用的唯一入口：来源把内容整理成 Markdown 后交给
:func:`chunk_markdown`，得到若干 :class:`ChunkDraft`。规则：

1. 预处理去掉 YAML frontmatter、HTML 注释、VuePress 容器标记行；图片换成替代
   文字、链接只留文字；连续空行压成一个。
2. 按 ``#`` 到 ``######`` 的标题切节，节的标题路径用 ``" > "`` 连接；超过 255 字
   时从左侧截断，只保留末尾。
3. 节内按空行分段累加，加下一段会超过 ``max_chars`` 时收口成一块；单段超长按句
   末标点再切，仍超长按字数硬切；围栏代码块与表格各算一段，超长时按行切。
4. 同一节内相邻两块有重叠：后一块以前一块末尾不超过 ``overlap`` 个字开头，起点
   对齐到句首；跨节不重叠。

``text`` 只放正文，不重复标题路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CHUNKER_VERSION",
    "ChunkDraft",
    "chunk_markdown",
    "chunker_signature",
    "normalize_markdown",
]

#: 分块规则有变化时加一；写进 ``knowledge_document.meta.chunker``。
CHUNKER_VERSION = "1"

#: ``knowledge_chunk.heading`` 的列宽。
MAX_HEADING_CHARS = 255

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_CONTAINER_LINE_RE = re.compile(r"(?m)^[ \t]*:::[^\n]*(?:\n|\Z)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LINK_REF_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_LINK_DEF_RE = re.compile(r"(?m)^[ \t]{0,3}\[[^\]]+\]:[^\n]*(?:\n|\Z)")
_BLANK_LINE_RE = re.compile(r"(?m)^[ \t]+$")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_SENTENCE_ENDINGS = "。！？；!?;\n"


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """一个待写入 ``knowledge_chunk`` 的分块。"""

    ord: int
    heading: str | None
    text: str


def normalize_markdown(text: str) -> str:
    """去 frontmatter、HTML 注释、VuePress 容器标记行；图片换替代文字；链接留文字；压空行。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    text = _FRONTMATTER_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _CONTAINER_LINE_RE.sub("", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_DEF_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _LINK_REF_RE.sub(r"\1", text)
    text = _BLANK_LINE_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunker_signature(max_chars: int, overlap: int) -> str:
    """``f"{CHUNKER_VERSION}:{max_chars}:{overlap}"``，写进 ``knowledge_document.meta.chunker``。"""
    return f"{CHUNKER_VERSION}:{max_chars}:{overlap}"


def chunk_markdown(text: str, *, max_chars: int, overlap: int) -> list[ChunkDraft]:
    """先 normalize，再按标题切节、节内按段累加；空文档返回空列表。"""
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")
    normalized = normalize_markdown(text)
    if not normalized:
        return []

    drafts: list[ChunkDraft] = []
    for heading, body in _split_sections(normalized):
        blocks = _split_blocks(body)
        pieces: list[str] = []
        for block in blocks:
            pieces.extend(_split_long_block(block, max_chars))
        chunks = _pack_pieces(pieces, max_chars)
        previous: str | None = None
        for content in chunks:
            prefix = ""
            if previous is not None and overlap > 0:
                prefix = _overlap_prefix(previous, overlap)
            chunk_text = prefix + content
            if chunk_text.strip():
                drafts.append(
                    ChunkDraft(ord=len(drafts), heading=heading, text=chunk_text)
                )
            previous = content
    return drafts


# ---------------------------------------------------------------------------
# 标题切节
# ---------------------------------------------------------------------------
def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """按 Markdown 标题切节；围栏代码块里的 ``#`` 不算标题。"""
    sections: list[tuple[str | None, str]] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    fence: str | None = None

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            path = " > ".join(title for _, title in stack) or None
            sections.append((_truncate_heading(path), joined))

    for line in text.split("\n"):
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            body.append(line)
            if fence_match and fence_match.group(1)[0] == fence:
                fence = None
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush()
            body = []
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip().rstrip("#").strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        if fence_match:
            fence = fence_match.group(1)[0]
        body.append(line)

    flush()
    return sections


def _truncate_heading(path: str | None) -> str | None:
    """标题路径超过 255 字时从左侧截断（保留末尾）。"""
    if path is None:
        return None
    if len(path) <= MAX_HEADING_CHARS:
        return path
    return path[-MAX_HEADING_CHARS:]


# ---------------------------------------------------------------------------
# 段落、代码块与表格
# ---------------------------------------------------------------------------
def _split_blocks(body: str) -> list[str]:
    """按空行切段；围栏代码块和表格整块保留。"""
    blocks: list[str] = []
    buffer: list[str] = []
    mode: str | None = None  # None | "fence" | "table"
    fence_char: str | None = None

    def flush() -> None:
        nonlocal buffer, mode, fence_char
        joined = "\n".join(buffer).strip("\n")
        if joined.strip():
            blocks.append(joined)
        buffer = []
        mode = None
        fence_char = None

    for line in body.split("\n"):
        stripped = line.strip()
        fence_match = _FENCE_RE.match(line)
        if mode == "fence":
            buffer.append(line)
            if fence_match and fence_match.group(1)[0] == fence_char:
                flush()
            continue
        if mode == "table":
            if stripped.startswith("|"):
                buffer.append(line)
                continue
            flush()
        if fence_match:
            buffer = [line]
            mode = "fence"
            fence_char = fence_match.group(1)[0]
            continue
        if stripped.startswith("|"):
            buffer = [line]
            mode = "table"
            continue
        if not stripped:
            flush()
            continue
        buffer.append(line)
    flush()
    return blocks


def _split_long_block(block: str, max_chars: int) -> list[str]:
    """超长段按规则再切；放得下的块原样返回。"""
    if len(block) <= max_chars:
        return [block]
    stripped = block.lstrip()
    if stripped.startswith(("```", "~~~")):
        return _pack_lines(block.split("\n"), max_chars)
    if stripped.startswith("|"):
        return _pack_lines(block.split("\n"), max_chars)
    return _split_sentences(block, max_chars)


def _pack_lines(lines: list[str], max_chars: int) -> list[str]:
    """代码块与表格按行累加，单行超长时硬切。"""
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        text = "\n".join(current).strip("\n")
        if text:
            pieces.append(text)
        current = []
        current_len = 0

    for line in lines:
        if len(line) > max_chars:
            flush()
            for start in range(0, len(line), max_chars):
                pieces.append(line[start : start + max_chars])
            continue
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            flush()
        current.append(line)
        current_len += len(line) + (1 if len(current) > 1 else 0)
    flush()
    return pieces


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """按句末标点切句再按长度收口；单句仍超长时按字数硬切。"""
    tokens = [token.strip() for token in re.split(r"(?<=[。！？；!?;\n])", text)]
    tokens = [token for token in tokens if token]
    pieces: list[str] = []
    current = ""
    for token in tokens:
        if len(token) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            for start in range(0, len(token), max_chars):
                piece = token[start : start + max_chars].strip()
                if piece:
                    pieces.append(piece)
            continue
        if current and len(current) + len(token) > max_chars:
            pieces.append(current)
            current = token
        else:
            current += token
    if current:
        pieces.append(current)
    return pieces


# ---------------------------------------------------------------------------
# 收口与重叠
# ---------------------------------------------------------------------------
def _pack_pieces(pieces: list[str], max_chars: int) -> list[str]:
    """把节内的段累加成不超过 ``max_chars`` 的块。"""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = piece if not current else f"{current}\n\n{piece}"
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate
        if len(current) > max_chars:
            # 只可能来自单个超长片段（_split_long_block 已尽量切过）：硬切兜底。
            chunks.extend(
                current[start : start + max_chars]
                for start in range(0, len(current), max_chars)
                if current[start : start + max_chars]
            )
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _overlap_prefix(previous: str, overlap: int) -> str:
    """取前一块末尾不超过 ``overlap`` 个字，起点对齐到句首。"""
    if overlap <= 0 or not previous:
        return ""
    if len(previous) <= overlap:
        return previous.lstrip()
    suffix = previous[-overlap:]
    # suffix[0] 前一个字符是句末标点，说明 suffix 本身就从句首开始。
    if previous[-overlap - 1] in _SENTENCE_ENDINGS:
        return suffix.lstrip()
    for index in range(1, len(suffix)):
        if suffix[index - 1] in _SENTENCE_ENDINGS:
            return suffix[index:].lstrip()
    return suffix.lstrip()
