"""M44/M45：仓库页签覆盖层在启动时生成（`resource/` 不进版本库，只能运行时写）。"""

import json

from pathlib import Path

from maa_api.core.depot_overlay import (
    DEPOT_OVERLAY_TASKS,
    DEPOT_OVERLAY_TASKS_FILE,
    ensure_depot_all_tabs,
)


def test_writes_per_key_task_overrides(tmp_path: Path) -> None:
    layers = tmp_path / "maa-layers"

    written = ensure_depot_all_tabs(layers_root=layers)

    assert written is not None
    tasks = json.loads(written.read_text(encoding="utf8"))
    # M45 W0：DepotAllTabClicked（自带选中态模板）是死代码，已删；只剩两个覆盖节点。
    assert set(tasks) == {"Depot", "DepotAllTab"}
    # 内置链只点「养成材料」（DepotMaterialTab）；覆盖后必须走「全部」。
    assert tasks["Depot"]["next"] == ["DepotAllTab"]
    # 覆盖是逐 key 合并（TaskData.cpp:78-105）：没给的 key 继承内核值，
    # 所以这里**不该**出现 template / roi / sub —— 写了反而会盖掉内核的实现。
    assert set(DEPOT_OVERLAY_TASKS["Depot"]) == {"next", "doc"}
    assert set(DEPOT_OVERLAY_TASKS["DepotAllTab"]) == {"next", "doc"}
    # 内核 DepotAllTab.next 是 DepotMaterialTab（切回养成材料）；覆盖成空链，
    # 点完「全部」就地收尾（同内核 DepotMaterialTabClicked 的写法）。
    assert tasks["DepotAllTab"]["next"] == []
    assert DEPOT_OVERLAY_TASKS_FILE.endswith(".json")
    assert "DepotAllTabClicked" not in written.read_text(encoding="utf8")
    # 不用再自带模板：删掉死节点后，覆盖层不再往 custom 层写 template/。
    assert not (layers / "custom" / "resource" / "template").exists()


def test_dead_tab_template_asset_removed() -> None:
    """M45 W0：模板 600×276 撞上 roi 300×138，内核每次重试都报 templ size is too large。"""
    repo_root = Path(__file__).resolve().parents[2]

    assert not (repo_root / "maa_api" / "assets" / "depot").exists()


def test_write_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    """磁盘写不动时只放弃覆盖，不抛异常炸启动。"""

    def boom(*args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)

    assert ensure_depot_all_tabs(layers_root=tmp_path / "maa-layers") is None
