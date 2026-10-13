"""M45 W4：M44 的仓库页签覆盖层已删，启动期只负责清理磁盘上的陈旧文件。

覆盖层是启动期生成、不进版本库的（``resource/`` 被 gitignore），所以「删掉生成代码」
只对新检出有效；老检出上那份 ``MaaApi@DepotAllTabs.json`` 会在内核启动时被加载，
必须每次启动清一次。为什么删见 ``maa_api/core/depot_overlay.py`` 的模块文档与台账
``docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md`` 的 W3b-D 节。
"""

import json

from pathlib import Path

import maa_api.core.depot_overlay as depot_overlay
from maa_api.core.depot_overlay import (
    DEPOT_OVERLAY_TASKS_FILE,
    remove_depot_all_tabs_override,
)


def _tasks_dir(layers: Path) -> Path:
    return layers / "custom" / "resource" / "tasks"


def test_stale_override_file_is_removed(tmp_path: Path) -> None:
    """老检出上已经写出的 ``MaaApi@DepotAllTabs.json`` 必须被删掉。"""
    layers = tmp_path / "maa-layers"
    tasks = _tasks_dir(layers)
    tasks.mkdir(parents=True)
    stale = tasks / DEPOT_OVERLAY_TASKS_FILE
    stale.write_text(
        json.dumps({"Depot": {"next": ["DepotAllTab"]}, "DepotAllTab": {"next": []}}),
        encoding="utf8",
    )

    removed = remove_depot_all_tabs_override(layers_root=layers)

    assert removed == stale
    assert not stale.exists()
    # 同一层里的其它任务文件不许被误删（自识别链就写在这里）。
    kept = tasks / "MaaApi@DepotSelfScan.json"
    kept.write_text("{}", encoding="utf8")
    assert remove_depot_all_tabs_override(layers_root=layers) is None
    assert kept.exists(), "清理只针对那一个文件名"


def test_no_override_is_generated_anymore(tmp_path: Path) -> None:
    """W4：不再生成任何 ``Depot`` / ``DepotAllTab`` 覆盖，内核沿用自带的 ``next``。

    钉在源码层面：模块里不许再有覆盖任务定义（``DEPOT_OVERLAY_TASKS``）——
    有定义就说明有人把 M44 的写法加回来了，而它在真机正常路径上会把整轮识别打挂。
    """
    layers = tmp_path / "maa-layers"

    assert remove_depot_all_tabs_override(layers_root=layers) is None
    assert not (layers / "custom").exists(), "没东西可清时不该顺手创建 custom 层"
    assert not hasattr(depot_overlay, "DEPOT_OVERLAY_TASKS")
    assert not hasattr(depot_overlay, "ensure_depot_all_tabs")


def test_dead_tab_template_asset_removed() -> None:
    """M45 W0：模板 600×276 撞上 roi 300×138，内核每次重试都报 templ size is too large。"""
    repo_root = Path(__file__).resolve().parents[2]

    assert not (repo_root / "maa_api" / "assets" / "depot").exists()


def test_unlink_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    """磁盘删不动时只放弃这次清理，不抛异常炸启动（内核退回自带行为）。"""

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)

    assert remove_depot_all_tabs_override(layers_root=tmp_path / "maa-layers") is None
