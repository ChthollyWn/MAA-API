"""M89 自定义任务链级校验（spec §4.5 校验、§5 T4、§6）。

``check_bundle`` 只做链级/结构检查，不做 v2 的字段类型校验；两条路径分别由
``ResourceService.validate_custom_task_content``（v2 口径）与 ``check_bundle`` 覆盖。
结构错误进 ``errors``，链级错误（未知 next、环、缺 Stop）进 ``chain_errors``。
"""

from __future__ import annotations

from maa_api.agent.custom_task.rules import (
    ALLOWED_ACTIONS,
    MAX_BUNDLE_TASKS,
    NAME_PATTERN,
    TaskIssue,
    check_bundle,
    check_entry_name,
)
from maa_api.services.resource_service import ResourceService

DONE = "Custom_Demo_Done"


def _click_node(next_name: str = DONE) -> dict[str, object]:
    return {
        "algorithm": "JustReturn",
        "action": "ClickRect",
        "specificRect": [10, 20, 10, 10],
        "next": [next_name],
    }


def _done_node() -> dict[str, object]:
    return {"algorithm": "JustReturn", "action": "Stop"}


def test_chain_errors():
    assert NAME_PATTERN.fullmatch("Custom_Demo_1")
    assert "Stop" in ALLOWED_ACTIONS
    assert "InputText" not in ALLOWED_ACTIONS
    assert MAX_BUNDLE_TASKS == 128
    assert check_entry_name("Custom_Demo_1") == []
    assert check_entry_name("Bad_1")[0].field == "name"
    assert TaskIssue("Custom_A", "next", "原因").as_dict() == {
        "task": "Custom_A",
        "field": "next",
        "reason": "原因",
    }

    # 未知 next。
    errors, chain_errors, chain = check_bundle(
        {"Custom_Demo_1": _click_node("Custom_Missing"), DONE: _done_node()},
        "Custom_Demo_1",
    )
    assert errors == []
    assert any(
        issue.field == "next" and "Custom_Missing" in issue.reason
        for issue in chain_errors
    )

    # 环：同一路径重复访问，且遍历不到 Stop。
    cyclic = {
        "Custom_A": {"action": "DoNothing", "next": ["Custom_B"]},
        "Custom_B": {"action": "DoNothing", "next": ["Custom_A"]},
    }
    errors, chain_errors, chain = check_bundle(cyclic, "Custom_A")
    assert errors == []
    assert any("环" in issue.reason for issue in chain_errors)
    assert any("Stop" in issue.reason for issue in chain_errors)
    assert chain == ()

    # #self 视为环并拒绝。
    self_loop = {
        "Custom_A": {"action": "DoNothing", "next": ["#self"]},
        "Custom_B": _done_node(),
    }
    errors, chain_errors, chain = check_bundle(self_loop, "Custom_A")
    assert any("环" in issue.reason for issue in chain_errors)
    assert any("Stop" in issue.reason for issue in chain_errors)

    # 缺 Stop 出口。
    no_stop = {
        "Custom_A": {"action": "DoNothing", "next": ["Custom_B"]},
        "Custom_B": {"action": "DoNothing"},
    }
    errors, chain_errors, chain = check_bundle(no_stop, "Custom_A")
    assert errors == []
    assert chain == ()
    assert any("Stop" in issue.reason for issue in chain_errors)

    # chain 记录第一次到 Stop 的路径。
    errors, chain_errors, chain = check_bundle(
        {"Custom_A": {"action": "DoNothing", "next": [DONE]}, DONE: _done_node()},
        "Custom_A",
    )
    assert errors == [] and chain_errors == []
    assert chain == ("Custom_A", DONE)

    # 结构：入口不在 tasks、节点名不合规、节点数超限。
    errors, chain_errors, _chain = check_bundle({DONE: _done_node()}, "Custom_Missing")
    assert any(issue.field == "entry" for issue in errors)
    errors, _chain_errors, _chain = check_bundle(
        {"Bad_1": {"action": "Stop"}}, "Bad_1"
    )
    assert any(issue.field == "name" and issue.task == "Bad_1" for issue in errors)
    too_many = {"Custom_Demo_1": _click_node()}
    too_many.update(
        {f"Custom_Demo_Extra{i}": {"action": "DoNothing"} for i in range(128)}
    )
    errors, _chain_errors, _chain = check_bundle(too_many, "Custom_Demo_1")
    assert any(issue.field == "tasks" for issue in errors)


def test_action_and_bounds(tmp_path):
    # 非法 action。
    errors, _chain_errors, chain = check_bundle(
        {
            "Custom_Demo_1": {
                "algorithm": "JustReturn",
                "action": "InputText",
                "next": [DONE],
            },
            DONE: _done_node(),
        },
        "Custom_Demo_1",
    )
    assert any(
        issue.task == "Custom_Demo_1" and issue.field == "action" for issue in errors
    )
    assert chain == ("Custom_Demo_1", DONE)

    # 矩形越出 1280×720 或宽高不大于 0。
    for rect in (
        [0, 0, 1281, 10],
        [0, 0, 10, 721],
        [-1, 0, 10, 10],
        [0, 0, 0, 10],
        [0, 0, 10, 0],
    ):
        tasks = {
            "Custom_Demo_1": {
                "algorithm": "JustReturn",
                "action": "ClickRect",
                "specificRect": rect,
                "next": [DONE],
            },
            DONE: _done_node(),
        }
        errors, _chain_errors, _chain = check_bundle(tasks, "Custom_Demo_1")
        assert any(
            issue.task == "Custom_Demo_1" and issue.field == "specificRect"
            for issue in errors
        ), rect

    # Swipe 缺 rectMove、ClickRect 缺 specificRect 都要报。
    errors, _chain_errors, _chain = check_bundle(
        {
            "Custom_Demo_1": {
                "action": "Swipe",
                "specificRect": [0, 0, 100, 100],
                "next": [DONE],
            },
            "Custom_Demo_2": {"action": "ClickRect", "next": [DONE]},
            DONE: _done_node(),
        },
        "Custom_Demo_1",
    )
    assert {issue.task for issue in errors} == {"Custom_Demo_1", "Custom_Demo_2"}

    # 公开包装：v2 结构错误转成 field=content，不抛 AppError。
    service = ResourceService(None, resource_root=tmp_path)  # type: ignore[arg-type]
    assert service.validate_custom_task_content({"action": "DoNothing"}) == []
    wrapped = service.validate_custom_task_content({"action": 7})
    assert wrapped and wrapped[0]["field"] == "content"
    assert wrapped[0]["reason"]
