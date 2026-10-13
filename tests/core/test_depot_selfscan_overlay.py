"""M45 W2：仓库自识别任务链在启动时生成（`resource/` 不进版本库，只能运行时写）。

三条要钉住的约定：**一个内核节点都不覆盖**（规格 §4.2）、**节点名全带前缀**（同一层
重名会让整层 `TaskData::load` 失败）、**不写进任何模板资产**（M44 的 600×276 教训）。
"""

import json

from pathlib import Path

from maa_api.core.depot_selfscan_overlay import (
    DEPOT_SELF_SCAN_PREFIX,
    DEPOT_SELF_SCAN_START,
    DEPOT_SELF_SCAN_TASKS,
    DEPOT_SELF_SCAN_TASKS_FILE,
    ensure_depot_self_scan,
)


def test_writes_a_self_contained_chain(tmp_path: Path) -> None:
    layers = tmp_path / "maa-layers"

    written = ensure_depot_self_scan(layers_root=layers)

    assert written is not None
    assert written.name == DEPOT_SELF_SCAN_TASKS_FILE
    tasks = json.loads(written.read_text(encoding="utf8"))
    assert set(tasks) == set(DEPOT_SELF_SCAN_TASKS)
    # 一个内核节点都不在文件里：M45 与内核那一趟并存，不覆盖它（规格 §4.2）。
    assert all(name.startswith(DEPOT_SELF_SCAN_PREFIX) for name in tasks)
    assert DEPOT_SELF_SCAN_START in tasks
    # 链头先试「已经在仓库页」，没命中才去导航。
    assert tasks[DEPOT_SELF_SCAN_START]["next"] == [
        f"{DEPOT_SELF_SCAN_PREFIX}AllTab",
        f"{DEPOT_SELF_SCAN_PREFIX}EnterDepot",
    ]


def test_all_tab_inherits_the_kernel_node_but_never_the_kernel_next() -> None:
    """点「全部」用内核模板与 roi，走向必须换成我们自己的链尾。"""
    node = DEPOT_SELF_SCAN_TASKS[f"{DEPOT_SELF_SCAN_PREFIX}AllTab"]

    assert node["baseTask"] == "DepotAllTab"
    # 内核 DepotAllTab.next 是 DepotMaterialTab（会切回养成材料），M45 必须停住。
    assert node["next"] == [f"{DEPOT_SELF_SCAN_PREFIX}Done"]
    assert node["exceededNext"] == node["next"]
    # **template 必须显式写**：内核加载这一层时会给每个需要模板的节点找模板，没写就按任务名
    # 推 `<任务名>.png`，推出来的文件不存在会让整层加载失败（真机实测，见模块 docstring）。
    assert node["template"] == "DepotAllTab.png"
    # roi 不给：给了就等于把内核坐标写死一遍，换分辨率/换主题会踩空。
    assert "roi" not in node
    assert set(node) == {"baseTask", "template", "maxTimes", "next", "exceededNext", "doc"}


def test_navigation_only_calls_the_kernel_chain() -> None:
    """进仓库页只调用内核 DepotBegin，不给它写 template/roi/action。"""
    node = DEPOT_SELF_SCAN_TASKS[f"{DEPOT_SELF_SCAN_PREFIX}EnterDepot"]

    assert node["next"] == ["DepotBegin"]
    assert set(node) == {"algorithm", "action", "next", "doc"}


def test_swipes_turn_one_page_and_settle_before_the_next_frame() -> None:
    """翻页 = 横滑一页（8 列 ≈ 1039 px）；滑完要等惯性落定，否则取到的是过渡态（规格 §5）。

    真机实测（2026-10-13）：竖向滑动怎么划都不动，横向一划一页 —— 与内核自带的
    ``DepotSlowlySwipeToTheRight``（``x 1150 → 110``、``y 340``）同款几何。
    """
    nxt = DEPOT_SELF_SCAN_TASKS[f"{DEPOT_SELF_SCAN_PREFIX}SwipeNextPage"]
    prev = DEPOT_SELF_SCAN_TASKS[f"{DEPOT_SELF_SCAN_PREFIX}SwipePrevPage"]

    for node in (nxt, prev):
        assert node["action"] == "Swipe"
        assert node["maxTimes"] == 1
        assert node["postDelay"] >= 1000
        # 竖向坐标必须一样：纵向滑动实测不动，这一条挡住「改回竖向」的回归。
        assert node["specificRect"][1] == node["rectMove"][1] == 340
        # specialParams 的第一项是滑动时长：200 ms，与内核 DepotSlowlySwipeToTheRight 相同。
        assert node["specialParams"][0] == 200
    # 位移 = 一页宽：8 列 × 129.92 ≈ 1039 px（1150 - 110 = 1040）。
    assert nxt["specificRect"][0] - nxt["rectMove"][0] == 1040
    # 反向节点就是起止对调。
    assert prev["specificRect"] == nxt["rectMove"]
    assert prev["rectMove"] == nxt["specificRect"]


def test_no_template_assets_are_written(tmp_path: Path) -> None:
    """M44 的 DepotAllTabClicked 因为模板 600×276 > roi 300×138 成了死代码，M45 不重蹈。"""
    layers = tmp_path / "maa-layers"

    ensure_depot_self_scan(layers_root=layers)

    # 只写任务文件，不往 custom 层写任何模板。
    assert not (layers / "custom" / "resource" / "template").exists()
    # 唯一引用到的模板是内核自带的那一张（`baseTask` 那条路也要显式写 template，见上一条用例）。
    assert {
        node.get("template") for node in DEPOT_SELF_SCAN_TASKS.values()
    } == {None, "DepotAllTab.png"}


def test_write_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    """磁盘写不动时只放弃这条链，不抛异常炸启动。"""

    def boom(*args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)

    assert ensure_depot_self_scan(layers_root=tmp_path / "maa-layers") is None
