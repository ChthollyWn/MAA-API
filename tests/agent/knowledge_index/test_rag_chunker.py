"""M76 T3：Markdown 预处理与分块（spec §4.2、§6、v3 docs/07 §2.1）。"""

from __future__ import annotations

from maa_api.agent.knowledge.index.chunker import (
    CHUNKER_VERSION,
    ChunkDraft,
    chunk_markdown,
    chunker_signature,
    normalize_markdown,
)


def test_normalize_strips_frontmatter_containers_and_links() -> None:
    source = """---
title: 固源岩
tags: [材料, 刷图]
---
# 理智

::: warning 注意
容器里的文字保留。
:::

<!-- 这段注释不该出现 -->
图片 ![固源岩](https://example.com/rock.png) 与 [链接文字](https://example.com/page)。

::: tip
:::
"""
    normalized = normalize_markdown(source)

    assert "title:" not in normalized
    assert "tags:" not in normalized
    assert ":::" not in normalized
    assert "<!--" not in normalized
    assert "这段注释不该出现" not in normalized
    assert "容器里的文字保留。" in normalized
    # 图片换成替代文字，链接只留文字、丢掉地址。
    assert "固源岩" in normalized
    assert "链接文字" in normalized
    assert "https://example.com" not in normalized
    assert "![固源岩]" not in normalized
    assert "[链接文字]" not in normalized
    assert "\n\n\n" not in normalized

    assert chunk_markdown("", max_chars=800, overlap=100) == []
    assert CHUNKER_VERSION == "1"
    assert chunker_signature(800, 100) == "1:800:100"


def _longest_overlap(previous: str, following: str, limit: int) -> int:
    for size in range(min(len(previous), len(following), limit), 0, -1):
        if previous.endswith(following[:size]):
            return size
    return 0


def test_chunks_follow_headings_size_and_overlap() -> None:
    section_one = "\n\n".join(f"第一节第{index}段。" + "内容" * 8 for index in range(6))
    section_two = "\n\n".join(f"第二节第{index}段。" + "资料" * 8 for index in range(4))
    source = f"# 第一节\n\n{section_one}\n\n## 第二节\n\n{section_two}\n"

    chunks = chunk_markdown(source, max_chars=80, overlap=20)

    assert all(isinstance(chunk, ChunkDraft) for chunk in chunks)
    assert [chunk.ord for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.text) <= 80 + 20 for chunk in chunks)
    assert {chunk.heading for chunk in chunks} == {"第一节", "第一节 > 第二节"}

    first = [chunk for chunk in chunks if chunk.heading == "第一节"]
    second = [chunk for chunk in chunks if chunk.heading == "第一节 > 第二节"]
    assert len(first) > 1 and len(second) > 1
    # 同节相邻块重叠，且重叠来自上一块末尾。
    for previous, following in zip(first, first[1:]):
        assert _longest_overlap(previous.text, following.text, 20) > 0
    for previous, following in zip(second, second[1:]):
        assert _longest_overlap(previous.text, following.text, 20) > 0
    # 跨节不重叠。
    assert _longest_overlap(first[-1].text, second[0].text, 20) == 0


def test_long_paragraph_and_code_fence() -> None:
    long_paragraph = "这是很长的段落里的一句话。" * 40
    code = "```python\nprint(1)\n\nprint(2)\n```"
    source = f"# 示例\n\n{long_paragraph}\n\n{code}\n"

    chunks = chunk_markdown(source, max_chars=80, overlap=10)

    assert all(len(chunk.text) <= 80 + 10 for chunk in chunks)
    # 超长段按句号切开，而不是硬切在句子中间。
    long_chunks = [chunk for chunk in chunks if "这是很长的段落里的一句话" in chunk.text]
    assert len(long_chunks) > 1
    pure_long_chunks = [chunk for chunk in long_chunks if "```" not in chunk.text]
    assert pure_long_chunks
    assert all(chunk.text.endswith("。") for chunk in pure_long_chunks)
    # 放得下的代码块完整落在一块里，不被空行拆开。
    code_chunks = [chunk for chunk in chunks if "print(1)" in chunk.text]
    assert len(code_chunks) == 1
    assert "print(2)" in code_chunks[0].text
    assert code_chunks[0].text.count("```") == 2


def test_heading_path_truncates_from_left() -> None:
    titles = ["甲" * 60, "乙" * 60, "丙" * 60, "丁" * 60, "戊" * 60]
    levels = ["#", "##", "###", "####", "#####"]
    lines = [f"{level} {title}" for level, title in zip(levels, titles)]
    path = " > ".join(titles)
    assert len(path) > 255
    source = "\n\n".join(lines) + "\n\n正文内容。\n"

    chunks = chunk_markdown(source, max_chars=800, overlap=100)

    assert len(chunks) == 1
    assert chunks[0].heading == path[-255:]
    assert len(chunks[0].heading or "") == 255
