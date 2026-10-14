"""2026-10-14：M44 的页签覆盖与 M45 的自识别链都已删实现，启动期只清陈旧任务文件。

这些文件都是启动期生成、不进版本库的（``resource/`` 被 gitignore），所以「删掉生成代码」
只对新检出有效；老检出上残留的那几份会在内核启动时被加载 —— 页签覆盖会把整轮仓库识别
打成空结果，自识别链与探针链则是没人再引用的死文件。为什么删见
``maa_api/core/depot_overlay.py`` 的模块文档、``docs/13`` §8.67/§8.68，以及台账
``docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md`` 的 W3b-D 节。
"""

import json

from pathlib import Path

import pytest

from maa_api.core.depot_overlay import STALE_DEPOT_TASKS_FILES, remove_stale_depot_tasks


def _tasks_dir(layers: Path) -> Path:
    return layers / "custom" / "resource" / "tasks"


def test_stale_task_files_are_removed_and_others_kept(tmp_path: Path) -> None:
    """三份历史文件全部删掉；同一层里还在用的任务文件不许误删。"""
    layers = tmp_path / "maa-layers"
    tasks = _tasks_dir(layers)
    tasks.mkdir(parents=True)
    stale = [tasks / name for name in STALE_DEPOT_TASKS_FILES]
    for path in stale:
        path.write_text(
            json.dumps({"Depot": {"next": ["DepotAllTab"]}, "DepotAllTab": {"next": []}}),
            encoding="utf8",
        )
    kept = tasks / "MaaApi@OperatorScan.json"
    kept.write_text("{}", encoding="utf8")

    removed = remove_stale_depot_tasks(layers_root=layers)

    assert sorted(removed) == sorted(stale)
    assert not any(path.exists() for path in stale)
    assert kept.exists(), "清理只针对那几个已知文件名"


def test_removal_is_idempotent_and_creates_nothing(tmp_path: Path) -> None:
    """第二次调用没有东西可删；这一层还不存在时不顺手创建 custom 层。"""
    layers = tmp_path / "maa-layers"

    assert remove_stale_depot_tasks(layers_root=layers) == []
    assert not (layers / "custom").exists()

    tasks = _tasks_dir(layers)
    tasks.mkdir(parents=True)
    (tasks / STALE_DEPOT_TASKS_FILES[1]).write_text("{}", encoding="utf8")
    assert len(remove_stale_depot_tasks(layers_root=layers)) == 1
    assert remove_stale_depot_tasks(layers_root=layers) == []


def test_dead_tab_template_asset_removed() -> None:
    """M45 W0：模板 600×276 撞上 roi 300×138，内核每次重试都报 templ size is too large。"""
    repo_root = Path(__file__).resolve().parents[2]

    assert not (repo_root / "maa_api" / "assets" / "depot").exists()


def test_unlink_failure_is_only_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """磁盘删不动时只放弃这次清理，不抛异常炸启动（内核退回自带行为）。"""
    layers = tmp_path / "maa-layers"
    tasks = _tasks_dir(layers)
    tasks.mkdir(parents=True)
    for name in STALE_DEPOT_TASKS_FILES:
        (tasks / name).write_text("{}", encoding="utf8")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)

    assert remove_stale_depot_tasks(layers_root=layers) == []
