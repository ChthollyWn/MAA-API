"""M28 D4：基建方案校验对齐上游（规格 §2.7 D4、§8.3「修复」）。

上游协议见 https://docs.maa.plus/zh-cn/protocol/base-scheduling-schema.html
（2026-10-10 抓取）：``rooms`` 必选，``product`` 只许 ``Battle Record`` / ``Pure Gold`` /
``Dualchip`` / ``Originium Shard`` / ``LMD`` / ``Orundum``，``drones.index`` 取 1–5，
``period`` 是 ``hh:mm``；``duration``、``groups``、``candidates``、``use_operator_groups``、
``drones.rule`` 都是合法字段（旧校验拒收，内核其实接受）。

改动前这些用例全部失败：``duration``/``groups``/``candidates``/``use_operator_groups``/
``drones.rule`` 被「未知字段」拒绝，而缺 ``rooms``、``product: "Unknown"``、
``drones.index: 6``、``period: "8:00"`` 都能存进库。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maa_api.domain.errors import AppError
from maa_api.services.resource_service import ResourceService

#: 一个用满上游字段的方案：每组都应当被放行。
FULL_PLAN: dict[str, Any] = {
    "author": "agent",
    "description": "覆盖上游字段",
    "id": 123,
    "title": "243 三班",
    "planTimes": "3班",
    "scheduleType": {"planTimes": 3, "trading": 2},
    "buildingType": 0,
    "plans": [
        {
            "name": "第1班",
            "description": "白天",
            "description_post": "下一班 14:00",
            "duration": 360,
            "period": [["06:00", "14:00"]],
            "Fiammetta": {"enable": False, "target": "", "order": "pre"},
            "drones": {
                "room": "manufacture",
                "index": 5,
                "enable": True,
                "rule": "all",
                "order": "pre",
            },
            "groups": [
                {"name": "古+银", "operators": ["古米", "银灰", "梅"]},
                {"name": "清流", "operators": ["清流", "森蚺", "温蒂"]},
            ],
            "rooms": {
                "control": [{"operators": ["夕", "令", "凯尔希"]}],
                "manufacture": [
                    {"operators": ["芬"], "sort": True, "product": "Battle Record"},
                    {"skip": True},
                    {"operators": ["多萝西"], "candidates": ["星源", "白面鸮", "赫默"]},
                    {
                        "use_operator_groups": True,
                        "operators": ["古+银", "清流"],
                    },
                ],
                "trading": [{"operators": ["但书"], "product": "LMD"}],
                "power": [{"operators": ["承曦格雷伊"]}],
                "meeting": [{"autofill": True}],
                "hire": [{"operators": ["菲亚梅塔"]}],
                "dormitory": [{"operators": ["车尔尼"]}, {"skip": True}],
                "processing": [{"operators": ["帕拉斯"]}],
            },
        },
        {
            "name": "第2班",
            "period": [["14:00", "22:00"], ["22:00", "06:00"]],
            "drones": {"room": "trading", "index": 1, "rule": "half"},
            "rooms": {"manufacture": [{"product": "Pure Gold"}]},
        },
    ],
}


def _service(resource_root, session_factory) -> ResourceService:
    return ResourceService(session_factory, resource_root=resource_root)


def test_full_upstream_plan_is_accepted_and_stored(resource_root, retention_session_factory):
    service = _service(resource_root, retention_session_factory)
    saved = asyncio.run(
        service.set_infrast_plan(name="full", description="D4 正例", content=FULL_PLAN)
    )
    assert saved["meta"]["plan_count"] == 2
    assert saved["filename"].startswith("custom_infrast/")


@pytest.mark.parametrize(
    "product",
    ["Battle Record", "Pure Gold", "Dualchip", "Originium Shard", "LMD", "Orundum"],
)
def test_every_upstream_product_is_accepted(
    resource_root, retention_session_factory, product
):
    service = _service(resource_root, retention_session_factory)
    plan = {"plans": [{"name": "班", "rooms": {"manufacture": [{"product": product}]}}]}
    saved = asyncio.run(service.set_infrast_plan(name=f"p-{product}", description=None, content=plan))
    assert saved["meta"]["plan_count"] == 1


def test_period_accepts_cross_midnight_ranges(resource_root, retention_session_factory):
    service = _service(resource_root, retention_session_factory)
    plan = {
        "plans": [
            {"name": "夜班", "period": [["22:00", "23:59"], ["00:00", "06:00"]], "rooms": {"power": []}}
        ]
    }
    saved = asyncio.run(service.set_infrast_plan(name="night", description=None, content=plan))
    assert saved["meta"]["plan_count"] == 1


def test_empty_rooms_mapping_is_still_a_present_rooms_key(
    resource_root, retention_session_factory
):
    """上游只要求 ``rooms`` 键存在；空映射表示每个设施都走默认算法。"""
    service = _service(resource_root, retention_session_factory)
    plan = {"plans": [{"name": "班", "rooms": {}}]}
    saved = asyncio.run(service.set_infrast_plan(name="empty-rooms", description=None, content=plan))
    assert saved["meta"]["plan_count"] == 1


@pytest.mark.parametrize(
    "plan",
    [
        # rooms 必填（上游：房间信息，必选）。
        {"plans": [{"name": "班"}]},
        {"plans": [{"name": "班", "rooms": []}]},
        {"plans": [{"name": "班", "rooms": None}]},
        # product 只许 6 个值。
        {"plans": [{"name": "班", "rooms": {"manufacture": [{"product": "Unknown"}]}}]},
        {"plans": [{"name": "班", "rooms": {"manufacture": [{"product": ""}]}}]},
        {"plans": [{"name": "班", "rooms": {"manufacture": [{"product": "battle record"}]}}]},
        # drones.index 1–5。
        {"plans": [{"name": "班", "rooms": {"power": []}, "drones": {"index": 0}}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "drones": {"index": 6}}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "drones": {"index": "6"}}]},
        # period 是 HH:MM。
        {"plans": [{"name": "班", "rooms": {"power": []}, "period": [["8:00", "16:00"]]}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "period": [["08:00", "25:00"]]}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "period": [["08:60", "09:00"]]}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "period": ["08:00-16:00"]}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "period": [["08:00"]]}]},
        # 其余新放行字段的结构仍要合法。
        {"plans": [{"name": "班", "rooms": {"power": []}, "duration": "360"}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "duration": -1}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "groups": [{"operators": ["银灰"]}]}]},
        {"plans": [{"name": "班", "rooms": {"power": []}, "groups": ["古+银"]}]},
        {
            "plans": [
                {
                    "name": "班",
                    "rooms": {"manufacture": [{"candidates": [1, 2]}]},
                }
            ]
        },
        {
            "plans": [
                {
                    "name": "班",
                    "rooms": {"manufacture": [{"use_operator_groups": "yes"}]},
                }
            ]
        },
        {"plans": [{"name": "班", "rooms": {"power": []}, "drones": {"rule": 1}}]},
        # 旧校验也拒的：未知房间、operators 非字符串数组、Fiammetta、drones.room。
        {"plans": [{"name": "班", "rooms": {"unknown_room": []}}]},
        {"plans": [{"name": "班", "rooms": {"trading": [{"operators": "bad"}]}}]},
        {"plans": [{"name": "班", "rooms": {"trading": []}, "Fiammetta": {"enable": "yes"}}]},
        {"plans": [{"name": "班", "rooms": {"trading": []}, "drones": {"room": "unknown"}}]},
        # 未知顶层/方案字段照旧拒绝。
        {"plans": [{"name": "班", "rooms": {"power": []}}], "extra": 1},
        {"plans": [{"name": "班", "rooms": {"power": []}, "unknown": 1}]},
    ],
)
def test_invalid_plans_are_rejected(resource_root, retention_session_factory, plan):
    service = _service(resource_root, retention_session_factory)
    with pytest.raises(AppError) as exc_info:
        asyncio.run(service.set_infrast_plan(name="bad", description=None, content=plan))
    assert exc_info.value.code == "INFRAST_PLAN_INVALID"
