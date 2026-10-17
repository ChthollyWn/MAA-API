"""M48 只读探索工具（规格 §4.2）的验收用例。

覆盖：未配置工作区、路径越界与软链接、密钥名单、二进制与超大文件、忽略目录、
``read_file`` 的分段续读、``search_code`` 的正则/上下文/上限与 git 白名单。

工具目录由主 agent 统一装配，所以这里**不走 ``build_registry()``**，自己注册一份——
注册项仍是真的 :func:`explore.register_tools`。工作区用 ``tmp_path`` 现造，不碰真仓库。

少数用例带 ``xfail``：它们断言的是**规格**的读法，而当前实现不合规（见文件内 reason 与
W3 报告）。``strict=False``，实现修好后会转为 XPASS，不阻塞套件。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.recon import explore
from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, Settings, WorkspaceSettings

# --------------------------------------------------------------------------- #
# 夹具与调用口
# --------------------------------------------------------------------------- #


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 ``asyncio.run`` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def build_registry() -> ToolRegistry:
    item = ToolRegistry()
    explore.register_tools(item)
    return item


def settings_for(root: Path | str, **agent: Any) -> Settings:
    """把工作区根目录与 ``agent.recon_*`` 装进真的 :class:`Settings`。"""

    return Settings(
        workspace=WorkspaceSettings(root=str(root)),
        agent=AgentSettings(**agent),
    )


def context(settings: Settings) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings_provider=lambda: settings))
        ),
        None,
    )


def call(name: str, arguments: dict[str, Any], settings: Settings) -> Any:
    """按注册表调用（参数先过 params_model，与真实调用一致）。"""

    return asyncio.run(build_registry().execute(name, arguments, context(settings)))


def error_of(name: str, arguments: dict[str, Any], settings: Settings) -> AppError:
    with pytest.raises(AppError) as info:
        call(name, arguments, settings)
    return info.value


def code_of(name: str, arguments: dict[str, Any], settings: Settings) -> ErrorCode:
    """只取错误码：断言越界/拒读必须按规格的错误码，而不是「抛了异常」。"""

    return error_of(name, arguments, settings).code


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _commit(repo: Path, message: str, *paths: str) -> None:
    _git(repo, "add", *paths)
    _git(
        repo,
        "-c",
        "user.name=M48 Test",
        "-c",
        "user.email=m48@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        message,
    )


# --------------------------------------------------------------------------- #
# 注册元数据与未配置工作区
# --------------------------------------------------------------------------- #


def test_register_tools_registers_three_safe_read_only_tools() -> None:
    """三个工具、组 ``recon``、``SAFE``、``read_only``（规格 D2 / §4.2）。"""

    items = build_registry().list("recon")
    assert [item.name for item in items] == ["list_files", "read_file", "search_code"]
    assert all(item.risk is ToolRisk.SAFE for item in items)
    assert all(item.read_only is True for item in items)
    assert all(item.params_model.model_config.get("extra") == "forbid" for item in items)


def test_unconfigured_workspace_blocks_all_three_tools() -> None:
    """``workspace.root`` 为空串 ＝ 关闭探索能力（规格 D1），不是静默降级。"""

    unconfigured = Settings()
    assert unconfigured.workspace.root == ""
    assert explore.is_configured(unconfigured) is False

    assert (
        code_of("list_files", {}, unconfigured) is ErrorCode.WORKSPACE_NOT_CONFIGURED
    )
    assert (
        code_of("read_file", {"path": "a.txt"}, unconfigured)
        is ErrorCode.WORKSPACE_NOT_CONFIGURED
    )
    assert (
        code_of("search_code", {"pattern": "x"}, unconfigured)
        is ErrorCode.WORKSPACE_NOT_CONFIGURED
    )
    # 配置了不存在的目录同样按「未配置」处理，不落到 CWD
    missing = settings_for("/nonexistent/m48/workspace")
    assert explore.is_configured(missing) is False
    assert code_of("list_files", {}, missing) is ErrorCode.WORKSPACE_NOT_CONFIGURED


def test_configured_workspace_is_reported_as_usable(ws: Path) -> None:
    assert explore.is_configured(settings_for(ws)) is True


def test_async_settings_provider_is_awaited(ws: Path) -> None:
    """M56 热修：``SettingService.refresh`` 是 async，装配层接进来的 provider 返回协程。

    实机表现（2026-10-17）：未 await 时三个只读探索工具全部报
    ``AGENT_DISABLED``「设置服务尚未启动」，模型连试三次都没拿到工作区。
    """

    async def scenario() -> None:
        settings = settings_for(ws)
        (ws / "a.txt").write_text("x", encoding="utf-8")

        async def provider():
            return settings

        ctx = ToolContext(
            CallerType.INTERNAL,
            None,
            None,
            SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(settings_provider=provider))
            ),
            None,
        )
        payload = await build_registry().execute("list_files", {}, ctx)
        assert payload["root"] == str(ws.resolve())
        assert [item["path"] for item in payload["entries"]] == ["a.txt"]

        # 同步 provider 仍然可用（测试里大多数用例是这一种）。
        sync_ctx = context(settings)
        assert await build_registry().execute("list_files", {}, sync_ctx) == payload

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 路径越界与软链接
# --------------------------------------------------------------------------- #


def test_paths_outside_the_root_are_denied(ws: Path, tmp_path: Path) -> None:
    """``../``、绝对路径、深层 ``..`` 一律 ``WORKSPACE_PATH_DENIED``（规格 §4.2 / §5）。"""

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside = outside_dir / "secret.txt"
    outside.write_text("外面的正文\n", encoding="utf-8")
    (ws / "sub").mkdir()
    settings = settings_for(ws)

    for raw in (
        "../outside/secret.txt",
        "sub/../../outside/secret.txt",
        str(outside),
        "/etc/hosts",
        "..",
        "../",
    ):
        assert (
            code_of("read_file", {"path": raw}, settings)
            is ErrorCode.WORKSPACE_PATH_DENIED
        ), raw
        assert (
            code_of("list_files", {"path": raw}, settings)
            is ErrorCode.WORKSPACE_PATH_DENIED
        ), raw

    assert (
        code_of("search_code", {"pattern": "x", "path": "../outside"}, settings)
        is ErrorCode.WORKSPACE_PATH_DENIED
    )
    err = error_of("read_file", {"path": "../outside/secret.txt"}, settings)
    assert "超出工作区" in err.message


def test_symlinks_pointing_outside_are_denied_but_inside_ones_work(
    ws: Path, tmp_path: Path
) -> None:
    """软链接按 ``resolve()`` 后的真实路径判定（规格 §4.2）。"""

    outside = tmp_path / "outside.txt"
    outside.write_text("外部正文\n", encoding="utf-8")
    (ws / "real.txt").write_text("内部正文\n", encoding="utf-8")
    (ws / "escape.txt").symlink_to(outside)
    (ws / "escape_dir").symlink_to(tmp_path, target_is_directory=True)
    (ws / "alias.txt").symlink_to(ws / "real.txt")
    settings = settings_for(ws)

    assert (
        code_of("read_file", {"path": "escape.txt"}, settings)
        is ErrorCode.WORKSPACE_PATH_DENIED
    )
    assert (
        code_of("list_files", {"path": "escape_dir"}, settings)
        is ErrorCode.WORKSPACE_PATH_DENIED
    )
    # 正对照：指向根目录内的软链接照常可读，不是「见到软链接就拒」
    assert call("read_file", {"path": "alias.txt"}, settings)["text"] == "内部正文\n"


def test_search_does_not_leak_through_symlinks_pointing_outside(
    ws: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_ONLY_TOKEN_5C1\n", encoding="utf-8")
    (ws / "escape.txt").symlink_to(outside)

    payload = call("search_code", {"pattern": "OUTSIDE_ONLY_TOKEN_5C1"}, settings_for(ws))
    assert payload["matches"] == []


# --------------------------------------------------------------------------- #
# 密钥名单
# --------------------------------------------------------------------------- #


def test_secret_files_are_denied_on_read_and_flagged_when_listed(ws: Path) -> None:
    """``.env`` / ``config.yaml`` / ``*.pem`` / ``id_rsa`` 拒读；列举里仍出现但标 blocked。"""

    (ws / ".env").write_text("API_KEY=abcdefghijklmnop\n", encoding="utf-8")
    (ws / "config.yaml").write_text("adb:\n  address: 127.0.0.1:5555\n", encoding="utf-8")
    (ws / "cert.pem").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    (ws / "deploy.key").write_text("key-material\n", encoding="utf-8")
    (ws / "id_rsa").write_text("ssh-rsa AAAA\n", encoding="utf-8")
    (ws / "notes.md").write_text("正常文件\n", encoding="utf-8")
    settings = settings_for(ws)

    entries = {item["path"]: item for item in call("list_files", {}, settings)["entries"]}
    for name in (".env", "config.yaml", "cert.pem", "deploy.key", "id_rsa"):
        assert entries[name]["blocked"] is True, name
        assert (
            code_of("read_file", {"path": name}, settings)
            is ErrorCode.WORKSPACE_PATH_DENIED
        ), name
    assert entries["notes.md"]["blocked"] is False


def test_secret_directories_and_names_are_denied(ws: Path) -> None:
    """``.dsh/`` 目录、``*secret*`` / ``*credential*`` 名字同样拒读。"""

    (ws / ".dsh").mkdir()
    (ws / ".dsh" / "settings.json").write_text("{}\n", encoding="utf-8")
    (ws / "my_secret_plan.md").write_text("计划\n", encoding="utf-8")
    (ws / "aws_credentials").write_text("AKIA...\n", encoding="utf-8")
    settings = settings_for(ws)

    for raw in (".dsh/settings.json", "my_secret_plan.md", "aws_credentials"):
        assert (
            code_of("read_file", {"path": raw}, settings)
            is ErrorCode.WORKSPACE_PATH_DENIED
        ), raw

    entries = {item["path"]: item for item in call("list_files", {"depth": 2}, settings)["entries"]}
    assert entries[".dsh/settings.json"]["blocked"] is True
    assert entries["my_secret_plan.md"]["blocked"] is True
    assert entries["aws_credentials"]["blocked"] is True


def test_secret_bodies_are_denied_even_with_an_innocent_name(ws: Path) -> None:
    """名字不在名单里、正文一眼是私钥/token 的文件同样拒读（实现加的一道闸）。"""

    (ws / "notes.txt").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEabcdef\n", encoding="utf-8"
    )
    (ws / "settings.md").write_text("api_key = sk-abcdefghijklmnopqrst\n", encoding="utf-8")
    settings = settings_for(ws)

    err = error_of("read_file", {"path": "notes.txt"}, settings)
    assert err.code is ErrorCode.WORKSPACE_PATH_DENIED
    assert "疑似密钥" in err.message
    assert (
        code_of("read_file", {"path": "settings.md"}, settings)
        is ErrorCode.WORKSPACE_PATH_DENIED
    )
    # 列举里不标 blocked（名字是干净的），但读不到
    entries = {item["path"]: item for item in call("list_files", {}, settings)["entries"]}
    assert entries["notes.txt"]["blocked"] is False


def test_secret_files_are_skipped_by_search(ws: Path) -> None:
    (ws / ".env").write_text("TOKEN_ABCDEF\n", encoding="utf-8")
    (ws / "notes.md").write_text("TOKEN_ABCDEF 出现在这里\n", encoding="utf-8")

    payload = call("search_code", {"pattern": "TOKEN_ABCDEF"}, settings_for(ws))
    assert [match["path"] for match in payload["matches"]] == ["notes.md"]


def test_search_does_not_return_lines_from_secret_bodies(ws: Path) -> None:
    (ws / "notes.txt").write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    (ws / "settings.md").write_text("api_key = sk-abcdefghijklmnopqrst\n", encoding="utf-8")

    payload = call("search_code", {"pattern": "api_key"}, settings_for(ws))
    assert payload["matches"] == []


# --------------------------------------------------------------------------- #
# 二进制与超大文件
# --------------------------------------------------------------------------- #


def test_binary_and_non_utf8_files_are_refused(ws: Path) -> None:
    (ws / "blob.bin").write_bytes(b"header\x00tail")
    (ws / "latin1.txt").write_bytes(b"caf\xe9\n")
    settings = settings_for(ws)

    err = error_of("read_file", {"path": "blob.bin"}, settings)
    assert err.code is ErrorCode.WORKSPACE_PATH_DENIED
    assert "二进制" in err.message
    assert (
        error_of("read_file", {"path": "latin1.txt"}, settings).code
        is ErrorCode.WORKSPACE_PATH_DENIED
    )


def test_files_over_two_mib_are_refused(ws: Path) -> None:
    (ws / "big.txt").write_bytes(b"x" * (explore.MAX_FILE_BYTES + 1))
    settings = settings_for(ws)

    err = error_of("read_file", {"path": "big.txt"}, settings)
    assert err.code is ErrorCode.WORKSPACE_FILE_TOO_LARGE
    assert str(explore.MAX_FILE_BYTES) in err.message


def test_search_skips_binary_and_huge_files(ws: Path) -> None:
    (ws / "blob.bin").write_bytes(b"NEEDLE_IN_BINARY\x00tail")
    (ws / "big.txt").write_bytes(b"NEEDLE_IN_BIG" + b"a" * (explore.MAX_FILE_BYTES + 1))
    (ws / "ok.txt").write_text("NEEDLE_IN_OK\n", encoding="utf-8")

    payload = call("search_code", {"pattern": "NEEDLE_IN_"}, settings_for(ws))
    assert [match["path"] for match in payload["matches"]] == ["ok.txt"]


# --------------------------------------------------------------------------- #
# 忽略目录
# --------------------------------------------------------------------------- #


def test_ignored_directories_are_not_listed_or_searched(ws: Path) -> None:
    """``node_modules/`` / ``.git/`` / ``__pycache__/`` 等默认不出现在列举与搜索结果里。"""

    token = "IGNORED_NEEDLE_9F3"
    for raw in (
        "node_modules/needle.js",
        ".git/needle.txt",
        "__pycache__/needle.pyc",
        ".venv/needle.py",
        ".pytest_cache/needle.txt",
        "dist/needle.js",
        "resource/lib/needle.bin",
    ):
        target = ws / raw
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{token} 在忽略目录里\n", encoding="utf-8")
    (ws / "app.py").write_text(f"{token} 在正常目录里\n", encoding="utf-8")
    settings = settings_for(ws)

    listed = [item["path"] for item in call("list_files", {"depth": 3}, settings)["entries"]]
    for prefix in (
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        ".pytest_cache",
        "dist",
        "resource/lib",
    ):
        assert not any(path == prefix or path.startswith(f"{prefix}/") for path in listed), prefix
    assert "app.py" in listed

    payload = call("search_code", {"pattern": token}, settings)
    assert [match["path"] for match in payload["matches"]] == ["app.py"]


# --------------------------------------------------------------------------- #
# list_files
# --------------------------------------------------------------------------- #


def test_list_files_reports_relative_paths_sizes_and_kinds(ws: Path) -> None:
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.txt").write_text("bb", encoding="utf-8")
    settings = settings_for(ws)

    payload = call("list_files", {}, settings)
    assert payload["root"] == str(ws.resolve())
    assert payload["path"] == "."
    entries = {item["path"]: item for item in payload["entries"]}
    assert entries["a.txt"] == {
        "path": "a.txt",
        "kind": "file",
        "bytes": 5,
        "blocked": False,
    }
    assert entries["sub"] == {"path": "sub", "kind": "dir", "bytes": None, "blocked": False}

    inner = call("list_files", {"path": "sub"}, settings)
    assert inner["path"] == "sub"
    assert [item["path"] for item in inner["entries"]] == ["sub/b.txt"]


def test_list_files_pattern_filters_names(ws: Path) -> None:
    (ws / "a.txt").write_text("x", encoding="utf-8")
    (ws / "b.md").write_text("x", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "c.txt").write_text("x", encoding="utf-8")

    payload = call("list_files", {"depth": 2, "pattern": "*.txt"}, settings_for(ws))
    assert [item["path"] for item in payload["entries"]] == ["a.txt", "sub/c.txt"]


def test_list_files_depth_limits_how_deep_it_descends(ws: Path) -> None:
    deep = ws / "l1" / "l2" / "l3"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("x", encoding="utf-8")
    settings = settings_for(ws)

    shallow = [item["path"] for item in call("list_files", {"depth": 1}, settings)["entries"]]
    assert "l1" in shallow
    assert "l1/l2" not in shallow

    deeper = [item["path"] for item in call("list_files", {"depth": 3}, settings)["entries"]]
    assert {"l1", "l1/l2", "l1/l2/l3", "l1/l2/l3/deep.txt"} <= set(deeper)


@pytest.mark.xfail(
    reason=(
        "实现与规格 §4.2 不符：_walk 对每个被访问的目录都收集文件，depth=1 时 sub/ 里的文件"
        "（相对深度 2）也会出现；规格的 depth 是「递归层数」；见 W3 报告「实现与规格不符」第 5 条"
    ),
    strict=False,
)
def test_list_files_depth_one_lists_only_the_root(ws: Path) -> None:
    (ws / "sub").mkdir()
    (ws / "sub" / "inner.txt").write_text("x", encoding="utf-8")

    payload = call("list_files", {"depth": 1}, settings_for(ws))
    assert [item["path"] for item in payload["entries"]] == ["sub"]


def test_list_files_truncates_at_the_hard_cap(ws: Path) -> None:
    for index in range(explore.MAX_RESULTS_CAP + 5):
        (ws / f"f{index:03d}.txt").write_text("x", encoding="utf-8")

    payload = call("list_files", {}, settings_for(ws))
    assert len(payload["entries"]) == explore.MAX_RESULTS_CAP
    assert payload["truncated"] == {"reason": "max_results"}


def test_missing_paths_and_wrong_kinds_report_not_found_or_invalid_parameter(ws: Path) -> None:
    (ws / "a.txt").write_text("x", encoding="utf-8")
    settings = settings_for(ws)

    assert code_of("list_files", {"path": "nope"}, settings) is ErrorCode.NOT_FOUND
    assert code_of("read_file", {"path": "nope.txt"}, settings) is ErrorCode.NOT_FOUND
    assert code_of("list_files", {"path": "a.txt"}, settings) is ErrorCode.INVALID_PARAMETER
    assert code_of("read_file", {"path": "."}, settings) is ErrorCode.INVALID_PARAMETER
    assert (
        code_of("search_code", {"pattern": "x", "path": "a.txt"}, settings)
        is ErrorCode.INVALID_PARAMETER
    )


# --------------------------------------------------------------------------- #
# read_file：分段续读
# --------------------------------------------------------------------------- #


def test_read_file_chunks_reassemble_the_original_text(ws: Path) -> None:
    text = "".join(f"line {index:03d} 中文内容\n" for index in range(120))
    (ws / "long.txt").write_text(text, encoding="utf-8")
    settings = settings_for(ws, recon_output_chars=1000)

    chunks: list[str] = []
    offset = 0
    for _ in range(200):
        payload = call(
            "read_file", {"path": "long.txt", "offset": offset, "max_chars": 97}, settings
        )
        assert payload["path"] == "long.txt"
        assert payload["chars"] == len(text)
        assert payload["bytes"] == (ws / "long.txt").stat().st_size
        chunks.append(payload["text"])
        if payload["truncated"] is None:
            break
        assert payload["truncated"]["reason"] == "max_chars"
        offset = payload["truncated"]["next_offset"]
    else:  # pragma: no cover - 只有在续读逻辑坏掉时才会走到
        pytest.fail("续读 200 次仍未读完 long.txt")

    assert len(chunks) > 5
    assert "".join(chunks) == text
    # 片数也要对：97 字符一片，最后一片可能不满
    assert len(chunks) == -(-len(text) // 97)


def test_read_file_reports_the_starting_line(ws: Path) -> None:
    text = "zero\none\ntwo\nthree\nfour\n"
    (ws / "lines.txt").write_text(text, encoding="utf-8")

    payload = call(
        "read_file",
        {"path": "lines.txt", "offset": text.index("three"), "max_chars": 5},
        settings_for(ws),
    )
    assert payload["start_line"] == 4
    assert payload["text"] == "three"


def test_read_file_default_chunk_comes_from_recon_output_chars(ws: Path) -> None:
    text = "甲" * 2500
    (ws / "long.txt").write_text(text, encoding="utf-8")

    payload = call("read_file", {"path": "long.txt"}, settings_for(ws, recon_output_chars=1000))
    assert len(payload["text"]) == 1000
    assert payload["chars"] == 2500
    assert payload["truncated"] == {"next_offset": 1000, "reason": "max_chars"}


def test_read_file_max_chars_cannot_exceed_recon_output_chars(ws: Path) -> None:
    """规格 §4.2：``max_chars`` 默认取 ``recon_output_chars``、上限同——只能往下要。"""

    (ws / "long.txt").write_text("甲" * 5000, encoding="utf-8")
    settings = settings_for(ws, recon_output_chars=1000)

    capped = call("read_file", {"path": "long.txt", "max_chars": 5000}, settings)
    assert len(capped["text"]) == 1000
    assert capped["truncated"] == {"next_offset": 1000, "reason": "max_chars"}

    smaller = call("read_file", {"path": "long.txt", "max_chars": 100}, settings)
    assert len(smaller["text"]) == 100
    assert smaller["truncated"]["next_offset"] == 100


def test_read_file_past_the_end_does_not_loop(ws: Path) -> None:
    """越界续读回空片段且 ``truncated=None``：模型不会拿到一个永远读不完的偏移。"""

    (ws / "small.txt").write_text("abc\n", encoding="utf-8")

    payload = call(
        "read_file", {"path": "small.txt", "offset": 1000}, settings_for(ws)
    )
    assert payload["text"] == ""
    assert payload["truncated"] is None
    assert payload["chars"] == 4


# --------------------------------------------------------------------------- #
# search_code：mode=content
# --------------------------------------------------------------------------- #


def test_search_content_reports_line_column_and_context(ws: Path) -> None:
    (ws / "src").mkdir()
    (ws / "src" / "mod.py").write_text(
        "alpha\nbeta needle\ngamma\ndelta\n", encoding="utf-8"
    )
    settings = settings_for(ws)

    payload = call("search_code", {"pattern": "needle", "path": "src"}, settings)
    assert payload["matches"] == [
        {"path": "src/mod.py", "line": 2, "column": 6, "text": "beta needle"}
    ]
    assert payload["scanned_files"] == 1
    assert payload["truncated"] is None

    with_context = call(
        "search_code", {"pattern": "needle", "path": "src", "context_lines": 1}, settings
    )
    assert with_context["matches"][0]["text"] == "alpha\nbeta needle\ngamma"


def test_search_content_respects_max_results_and_glob(ws: Path) -> None:
    (ws / "hits.py").write_text("hit\n" * 10, encoding="utf-8")
    (ws / "hits.md").write_text("hit\n" * 10, encoding="utf-8")
    settings = settings_for(ws)

    payload = call("search_code", {"pattern": "hit", "max_results": 3}, settings)
    assert len(payload["matches"]) == 3
    assert payload["truncated"] == {"reason": "max_results"}

    only_py = call("search_code", {"pattern": "hit", "glob": "*.py"}, settings)
    assert {match["path"] for match in only_py["matches"]} == {"hits.py"}


def test_search_content_rejects_an_invalid_regex(ws: Path) -> None:
    (ws / "a.py").write_text("x\n", encoding="utf-8")

    err = error_of("search_code", {"pattern": "([unclosed"}, settings_for(ws))
    assert err.code is ErrorCode.TOOL_ARGS_INVALID
    assert "正则" in err.message


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_files", {"depth": 0}),
        ("list_files", {"depth": 4}),
        ("list_files", {"unknown": 1}),
        ("read_file", {"path": "a.txt", "max_chars": 0}),
        ("read_file", {"path": "a.txt", "offset": -1}),
        ("search_code", {"pattern": "x", "mode": "nope"}),
        ("search_code", {"pattern": "x", "max_results": 101}),
        ("search_code", {"pattern": "x", "context_lines": 4}),
        ("search_code", {"pattern": "x", "max_results": 1.5}),
    ],
)
def test_parameter_bounds_are_enforced(
    name: str, arguments: dict[str, Any], ws: Path
) -> None:
    assert code_of(name, arguments, settings_for(ws)) is ErrorCode.TOOL_ARGS_INVALID


# --------------------------------------------------------------------------- #
# search_code：mode=git
# --------------------------------------------------------------------------- #


def test_search_git_allows_only_the_four_read_only_actions(ws: Path) -> None:
    """白名单外的动作一律拒绝（规格 D3）。这里不要求是仓库：动作校验在读路径之前。"""

    assert set(explore.GIT_ACTIONS) == {
        "status_local",
        "diff_local",
        "log_path",
        "blame_path",
    }
    settings = settings_for(ws)
    for action in ("push", "fetch", "checkout", "clean", "reset", "remote", "commit"):
        err = error_of(
            "search_code", {"mode": "git", "pattern": ".", "action": action}, settings
        )
        assert err.code is ErrorCode.WORKSPACE_PATH_DENIED, action
        assert action in err.message, action

    # 不写 action 同样拒绝
    assert (
        error_of("search_code", {"mode": "git", "pattern": "."}, settings).code
        is ErrorCode.WORKSPACE_PATH_DENIED
    )


def test_search_git_reports_external_source_unavailable_outside_a_repository(ws: Path) -> None:
    err = error_of(
        "search_code",
        {"mode": "git", "pattern": ".", "action": "status_local"},
        settings_for(ws),
    )
    assert err.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert "不是 git 仓库" in err.message


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:  # pragma: no cover - 本机一定有 git
        pytest.skip("本机没有 git，只跑拒绝路径")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("needle-line\n", encoding="utf-8")
    _commit(repo, "first commit", "tracked.txt")
    return repo


def test_search_git_read_only_actions_run_on_a_local_repository(git_repo: Path) -> None:
    settings = settings_for(git_repo)
    # 实现里 ``argv`` 是 ``["git", "-C", root, ...][2:]``，工作区根目录会留在里面（W3 报告记了一笔）
    root = str(git_repo.resolve())

    status = call(
        "search_code", {"mode": "git", "pattern": ".", "action": "status_local"}, settings
    )
    assert status["action"] == "status_local"
    assert status["argv"] == [root, "status", "--short", "--branch"]
    assert status["exit_code"] == 0
    assert "## " in status["output"]

    log = call("search_code", {"mode": "git", "pattern": ".", "action": "log_path"}, settings)
    assert log["argv"] == [root, "log", "--oneline", "-n", "20"]
    assert log["exit_code"] == 0
    assert "first commit" in log["output"]

    blame = call(
        "search_code",
        {"mode": "git", "pattern": ".", "action": "blame_path", "path": "tracked.txt"},
        settings,
    )
    assert blame["argv"] == [root, "blame", "--line-porcelain", "--", "tracked.txt"]
    assert "needle-line" in blame["output"]

    diff = call(
        "search_code", {"mode": "git", "pattern": ".", "action": "diff_local"}, settings
    )
    assert diff["exit_code"] == 0
    assert diff["output"].strip() == ""


def test_search_git_scopes_log_by_the_path_parameter(git_repo: Path) -> None:
    """实现用 ``path`` 做路径过滤（规格写的是 ``pattern``，见下面那条 xfail）。"""

    (git_repo / "other.txt").write_text("other\n", encoding="utf-8")
    _commit(git_repo, "second commit", "other.txt")
    settings = settings_for(git_repo)

    scoped = call(
        "search_code",
        {"mode": "git", "pattern": ".", "action": "log_path", "path": "tracked.txt"},
        settings,
    )
    assert "first commit" in scoped["output"]
    assert "second commit" not in scoped["output"]

    unscoped = call(
        "search_code", {"mode": "git", "pattern": ".", "action": "log_path"}, settings
    )
    assert "second commit" in unscoped["output"]


@pytest.mark.xfail(
    reason=(
        "已裁决的口径偏离（不是待修缺陷）：mode=git 的路径范围取自 path，规格初稿写的是"
        "「pattern 在 diff_local / log_path / blame_path 下表示路径」。保留 path 的理由是"
        "「要查哪块」与「要匹配什么」分属两个参数更直白，见 docs/13 §8.71 五、3 与规格 §4.2"
        "的实施对齐段；这条用例钉住的是规格初稿的读法，故刻意保持 xfail"
    ),
    strict=False,
)
def test_search_git_uses_pattern_as_the_path_filter(git_repo: Path) -> None:
    (git_repo / "other.txt").write_text("other\n", encoding="utf-8")
    _commit(git_repo, "second commit", "other.txt")

    payload = call(
        "search_code",
        {"mode": "git", "pattern": "tracked.txt", "action": "log_path", "path": "."},
        settings_for(git_repo),
    )
    assert "second commit" not in payload["output"]
