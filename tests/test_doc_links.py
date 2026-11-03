from __future__ import annotations

from collections import Counter

import scripts.check_doc_links as doc_links

#: 基线条目上限：M64 核对后的 26 条（spec 记 25，差的一条见 docs/v3/13 §8.9）。
BASELINE_LIMIT = 26


def test_repository_has_no_new_broken_doc_links() -> None:
    new, _ = doc_links.compare(
        doc_links.find_broken(doc_links.REPO_ROOT),
        doc_links.load_baseline(doc_links.BASELINE_PATH),
    )
    assert new == [], "新增断链（改链接或把它记进基线）：\n" + "\n".join(new)


def test_baseline_has_no_stale_entries() -> None:
    _, stale = doc_links.compare(
        doc_links.find_broken(doc_links.REPO_ROOT),
        doc_links.load_baseline(doc_links.BASELINE_PATH),
    )
    assert stale == [], "基线里的断链已不存在，请从 scripts/doc_links_baseline.txt 删掉：\n" + "\n".join(
        stale
    )


def test_baseline_stays_in_frozen_areas_and_never_grows() -> None:
    baseline = doc_links.load_baseline(doc_links.BASELINE_PATH)

    assert sum(baseline.values()) <= BASELINE_LIMIT
    allowed = ("docs/v2/", "docs/copy-audit/", "tests/fixtures/")
    for entry in baseline:
        assert entry.startswith(allowed), entry


def test_extract_links_skips_code_and_external_targets() -> None:
    text = (
        "[行内](../a.md) ![图片](./b.png) [外链](https://example.com) [锚](#x) [邮件](mailto:a@b)\n"
        "`[行内代码](./c.md)`\n"
        "```\n"
        "[围栏](./d.md)\n"
        "```\n"
        "[引用式]: ./e.md\n"
        '<img src="./f.png">\n'
    )

    assert doc_links.extract_links(text) == ["../a.md", "./b.png", "./e.md", "./f.png"]


def test_compare_counts_duplicate_occurrences() -> None:
    baseline = Counter({"a.md: x.md": 1})

    new, stale = doc_links.compare(["a.md: x.md", "a.md: x.md", "b.md: y.md"], baseline)

    assert new == ["a.md: x.md", "b.md: y.md"]
    assert stale == []


def test_main_prints_broken_count_as_last_line(capsys) -> None:
    assert doc_links.main([]) == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "BROKEN 0"
