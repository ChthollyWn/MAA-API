"""M44：仓库页签覆盖层在启动时生成（`resource/` 不进版本库，只能运行时写）。"""

import json
from pathlib import Path

from maa_api.core import depot_overlay
from maa_api.core.depot_overlay import (
    DEPOT_OVERLAY_TASKS,
    DEPOT_OVERLAY_TASKS_FILE,
    DEPOT_TAB_TEMPLATE_NAME,
    ensure_depot_all_tabs,
)


def test_writes_task_overrides_and_template(tmp_path: Path) -> None:
    layers = tmp_path / "maa-layers"

    written = ensure_depot_all_tabs(layers_root=layers)

    assert written is not None
    tasks = json.loads(written.read_text(encoding="utf8"))
    assert set(tasks) == {"Depot", "DepotAllTab", "DepotAllTabClicked"}
    # 内置链只点「养成材料」（DepotMaterialTab）；覆盖后必须走「全部」。
    assert tasks["Depot"]["next"] == ["DepotAllTab"]
    assert tasks["DepotAllTab"]["next"] == ["DepotAllTabClicked"]
    # 覆盖是整条替换：内核原有字段不能丢，否则链会断。
    assert tasks["Depot"]["sub"] == ["Depot-Entry"]
    assert tasks["Depot"]["roi"] == [227, 327, 159, 152]
    assert tasks["DepotAllTab"]["roi"] == [450, 0, 300, 138]
    # 模板落到内核推定的路径下。
    template = layers / "custom" / "resource" / "template" / "Depot" / DEPOT_TAB_TEMPLATE_NAME
    assert template.is_file() and template.read_bytes().startswith(b"\x89PNG")
    assert DEPOT_OVERLAY_TASKS["DepotAllTabClicked"]["roi"] == [450, 0, 300, 138]
    assert DEPOT_OVERLAY_TASKS_FILE.endswith(".json")


def test_missing_template_returns_none(tmp_path: Path, monkeypatch) -> None:
    """模板不在（比如打包漏了）时只放弃覆盖，不抛异常炸启动。"""
    monkeypatch.setattr(depot_overlay, "_TEMPLATE_SOURCE", tmp_path / "missing.png")
    layers = tmp_path / "maa-layers"

    assert ensure_depot_all_tabs(layers_root=layers) is None
    assert not (layers / "custom").exists()
