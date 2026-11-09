"""种子界面与跳转（M86 spec §4.5.6）。

坐标都是 **归一化坐标**：MAA 资源以 1280×720 为基准，取 roi 中心再除以 1280、720，
保留 4 位小数。没有固定出处的入口写成 ``locate`` 跳转，第一次走时由视觉模型定位，
走通后由导航执行器学成 ``tap`` 跳转；``stage:<代号>`` 不进种子（关卡位置随滚动变化）。

``tap`` 动作不写 ``wait_ms``，使用动作模型的默认值 1200 ms。
"""

from __future__ import annotations

from typing import Any

__all__ = ["SEED_EDGES", "SEED_NODES"]

_FULL_ROI = [0.0, 0.0, 1.0, 1.0]


def _recognizer(
    description: str,
    extra_rois: list[list[float]] | None = None,
    *,
    rois: list[list[float]] | None = None,
    generic: bool = False,
) -> dict[str, Any]:
    """节点识别配置：除整屏外的 ROI、距离上限、特征描述与 generic 标记。"""
    return {
        "rois": rois if rois is not None else [_FULL_ROI, *(extra_rois or [])],
        "max_distance": 10,
        "description": description,
        "generic": generic,
    }


SEED_NODES: tuple[dict[str, Any], ...] = (
    {
        "id": "home",
        "title": "主界面",
        "kind": "screen",
        "recognizer": _recognizer(
            "游戏首页，右侧是终端、采购中心、公开招募、干员寻访、基建、仓库这些入口",
            [[0.1773, 0.4542, 0.1242, 0.2111]],
        ),
    },
    {
        "id": "terminal",
        "title": "终端",
        "kind": "screen",
        "recognizer": _recognizer(
            "作战终端，底部是关卡分类栏",
            [[0.3609, 0.8333, 0.1094, 0.1667]],
        ),
    },
    {
        "id": "main_theme",
        "title": "主题曲",
        "kind": "screen",
        "recognizer": _recognizer("主题曲页，右上角有「乐章收录」，下方有「前往章节」"),
    },
    {
        "id": "main_episode",
        "title": "主线章节",
        "kind": "screen",
        "recognizer": _recognizer("某一章主线的关卡地图，关卡节点横向排开"),
    },
    {
        "id": "activity_hub",
        "title": "当期活动",
        "kind": "screen",
        "recognizer": _recognizer("当期活动的主页或关卡地图"),
    },
    {
        "id": "stage_start",
        "title": "关卡开始页",
        "kind": "stage",
        "recognizer": _recognizer(
            "某个关卡的出战确认页，右下角有「开始行动」",
            rois=[[0.7891, 0.8681, 0.2031, 0.0847]],
            generic=True,
        ),
    },
    {
        "id": "infrast",
        "title": "基建",
        "kind": "screen",
        "recognizer": _recognizer(
            "基建总览", [[0.0, 0.1389, 0.2344, 0.2083]]
        ),
    },
    {
        "id": "recruit",
        "title": "公开招募",
        "kind": "screen",
        "recognizer": _recognizer(
            "公开招募的四个招募位", [[0.0391, 0.1389, 0.1797, 0.1389]]
        ),
    },
    {
        "id": "store",
        "title": "采购中心",
        "kind": "screen",
        "recognizer": _recognizer(
            "采购中心，右上有信用交易所这些分区",
            [[0.7812, 0.0, 0.2188, 0.4167]],
        ),
    },
    {
        "id": "depot",
        "title": "仓库",
        "kind": "screen",
        "recognizer": _recognizer("仓库物品列表"),
    },
    {
        "id": "operator_list",
        "title": "干员",
        "kind": "screen",
        "recognizer": _recognizer("干员列表"),
    },
    {
        "id": "headhunt",
        "title": "干员寻访",
        "kind": "screen",
        "recognizer": _recognizer("卡池页"),
    },
    {
        "id": "friends",
        "title": "好友",
        "kind": "screen",
        "recognizer": _recognizer("好友列表"),
    },
    {
        "id": "mail",
        "title": "邮件",
        "kind": "dialog",
        "recognizer": _recognizer("邮件面板"),
    },
    {
        "id": "quick_menu",
        "title": "快捷菜单",
        "kind": "menu",
        "recognizer": _recognizer("左上角菜单展开后的快捷跳转面板"),
    },
)


def _tap(x: float, y: float) -> dict[str, Any]:
    return {"type": "tap", "x": x, "y": y}


def _locate(query: str) -> dict[str, Any]:
    return {"type": "locate", "query": query}


#: ``ReturnButton``（tasks.json，roi ``[0,0,180,80]``）的中心。
_RETURN_BUTTON = _tap(0.0703, 0.0556)
#: ``QuickSwitch@ToHome@Open``（UiTheme/QuickSwitch.json，roi ``[197,0,144,134]``）的中心。
_OPEN_QUICK_MENU = _tap(0.2102, 0.0931)


def _edge(from_node: str, to_node: str, action: dict[str, Any]) -> dict[str, Any]:
    return {"from_node": from_node, "to_node": to_node, "action": action}


SEED_EDGES: tuple[dict[str, Any], ...] = (
    # 首页入口（UiTheme/*.json 的 *Default 中心）。
    _edge("home", "infrast", _tap(0.8164, 0.8750)),
    _edge("home", "recruit", _tap(0.8008, 0.6944)),
    _edge("home", "store", _tap(0.6758, 0.6840)),
    _edge("home", "depot", _tap(0.9332, 0.8889)),
    _edge("home", "operator_list", _tap(0.8340, 0.4931)),
    _edge("home", "headhunt", _tap(0.9109, 0.7125)),
    _edge("home", "friends", _tap(0.2734, 0.8333)),
    _edge("home", "mail", _tap(0.1523, 0.0938)),
    # 没有固定坐标的入口写成 locate，走通后学成 tap。
    _edge(
        "home",
        "terminal",
        _locate("首页上标着「终端」的大按钮，按钮里显示当前理智"),
    ),
    _edge("terminal", "main_theme", _locate("终端底部分类栏里的「主题曲」")),
    _edge("terminal", "activity_hub", _locate("终端里当期活动的入口横幅")),
    # 「前往章节」（tasks.json 的 EnterEpisodeNew-Click，roi [1078,595,112,42]）。
    _edge("main_theme", "main_episode", _tap(0.8859, 0.8556)),
    # 8 条「各界面回主界面」+ 3 条「主题曲/章节/活动回终端」都点 ReturnButton。
    *(
        _edge(node_id, "home", _RETURN_BUTTON)
        for node_id in (
            "terminal",
            "infrast",
            "recruit",
            "store",
            "depot",
            "operator_list",
            "headhunt",
            "friends",
        )
    ),
    _edge("main_theme", "terminal", _RETURN_BUTTON),
    _edge("main_episode", "main_theme", _RETURN_BUTTON),
    _edge("activity_hub", "terminal", _RETURN_BUTTON),
    # 9 条「打开快捷菜单」。
    *(
        _edge(node_id, "quick_menu", _OPEN_QUICK_MENU)
        for node_id in (
            "terminal",
            "depot",
            "store",
            "recruit",
            "operator_list",
            "main_theme",
            "main_episode",
            "activity_hub",
            "stage_start",
        )
    ),
    # 快捷菜单四个入口（UiTheme/QuickSwitch.json 的 *Entry 中心）。
    _edge("quick_menu", "home", _tap(0.0727, 0.3896)),
    _edge("quick_menu", "terminal", _tap(0.4137, 0.1028)),
    _edge("quick_menu", "operator_list", _tap(0.2402, 0.3903)),
    _edge("quick_menu", "infrast", _tap(0.5352, 0.1021)),
)
