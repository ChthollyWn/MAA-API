"""M89 示教步骤编译器（spec §4.5 编译、§5 T4、§6）。

纯函数测试：不碰库、不碰文件。编译结果还要能被 v2 的
``validate_custom_task_content`` 逐节点接受（``_validate_custom_task`` 的公开包装）。
"""

from __future__ import annotations

import pytest

from maa_api.agent.custom_task.rules import NAME_PATTERN
from maa_api.agent.demonstrations.compiler import (
    Compiled,
    CompileError,
    Step,
    compile_steps,
    parse_step,
    scale_point,
)
from maa_api.services.resource_service import ResourceService

PREFIX = "Custom_Demo_ab12cd34"


def _node_names(*suffixes: str) -> list[str]:
    return [f"{PREFIX}_{suffix}" for suffix in suffixes]


def _service(tmp_path) -> ResourceService:
    """只用到结构校验，不需要会话工厂。"""
    return ResourceService(None, resource_root=tmp_path)  # type: ignore[arg-type]


def test_compile_node_shapes(tmp_path):
    compiled = compile_steps(
        prefix=PREFIX,
        steps=[
            Step(action="click", x=640, y=360, screen=(1280, 720)),
            Step(
                action="swipe",
                x1=100,
                y1=200,
                x2=300,
                y2=400,
                duration_ms=250,
                delay_ms=800,
                screen=(1280, 720),
            ),
            Step(
                action="long_press",
                x=50,
                y=60,
                duration_ms=900,
                delay_ms=1500,
                screen=(1280, 720),
            ),
        ],
    )

    assert isinstance(compiled, Compiled)
    names = _node_names("1", "2", "3", "Done")
    assert compiled.entry == names[0]
    assert list(compiled.tasks) == names
    assert compiled.chain == tuple(names)

    click = compiled.tasks[names[0]]
    assert click == {
        "algorithm": "JustReturn",
        "action": "ClickRect",
        "specificRect": [640, 360, 10, 10],
        "postDelay": 800,
        "next": [names[1]],
    }

    swipe = compiled.tasks[names[1]]
    assert swipe["algorithm"] == "JustReturn"
    assert swipe["action"] == "Swipe"
    assert swipe["specificRect"] == [100, 200, 100, 100]
    assert swipe["rectMove"] == [300, 400, 100, 100]
    assert swipe["specialParams"] == [250, 1, 37, 1]
    assert swipe["postDelay"] == 1500
    assert swipe["next"] == [names[2]]

    # 长按没有独立 action：编译成起终点相同的滑动。
    press = compiled.tasks[names[2]]
    assert press["action"] == "Swipe"
    assert press["specificRect"] == [50, 60, 100, 100]
    assert press["rectMove"] == [50, 60, 100, 100]
    assert press["specialParams"] == [900, 1, 37, 1]
    assert press["postDelay"] == 800
    assert press["next"] == [names[3]]

    assert compiled.tasks[names[3]] == {"algorithm": "JustReturn", "action": "Stop"}

    # 编译结果必须能被 v2 的结构校验逐节点接受。
    service = _service(tmp_path)
    for name, node in compiled.tasks.items():
        assert service.validate_custom_task_content(node) == [], name


def test_scale_and_chain_tail():
    # 1080 高：y=540 → 360；x 超界先夹到 1279（ClickRect 再夹 1270）。
    assert scale_point(540, 1080, 719) == 360
    assert scale_point(1920, 1080, 1279) == 1279
    assert scale_point(0, 1080, 1279) == 0
    assert scale_point(5000, 1080, 719) == 719

    compiled = compile_steps(
        prefix=PREFIX,
        steps=[Step(action="click", x=1919, y=540, screen=(1920, 1080))],
    )
    click = compiled.tasks[compiled.entry]
    assert click["specificRect"] == [1270, 360, 10, 10]
    assert click["next"] == _node_names("Done")
    assert compiled.tasks[_node_names("Done")[0]]["action"] == "Stop"
    assert compiled.chain == (compiled.entry, _node_names("Done")[0])

    # 720 高：y=719 换算后正好 719，再按 10×10 矩形夹到 710。
    click = compile_steps(
        prefix=PREFIX,
        steps=[Step(action="click", x=1279, y=719, screen=(1280, 720))],
    ).tasks[f"{PREFIX}_1"]
    assert click["specificRect"] == [1270, 710, 10, 10]

    # 滑动起终点夹 0–1180 / 0–620；时长夹 100–5000。
    swipe = compile_steps(
        prefix=PREFIX,
        steps=[
            Step(
                action="swipe",
                x1=1279,
                y1=719,
                x2=0,
                y2=0,
                duration_ms=50,
                screen=(1280, 720),
            )
        ],
    ).tasks[f"{PREFIX}_1"]
    assert swipe["specificRect"] == [1180, 620, 100, 100]
    assert swipe["rectMove"] == [0, 0, 100, 100]
    assert swipe["specialParams"] == [100, 1, 37, 1]
    assert swipe["next"] == _node_names("Done")

    # 长按时长夹 600–5000。
    press = compile_steps(
        prefix=PREFIX,
        steps=[
            Step(
                action="long_press",
                x=10,
                y=20,
                duration_ms=9000,
                screen=(1280, 720),
            )
        ],
    ).tasks[f"{PREFIX}_1"]
    assert press["specialParams"] == [5000, 1, 37, 1]


def test_compile_rejects_zero_height():
    with pytest.raises(CompileError) as caught:
        compile_steps(
            prefix=PREFIX,
            steps=[Step(action="click", x=0, y=0, screen=(0, 0))],
        )

    assert caught.value.errors
    assert caught.value.errors[0]["field"] in {"screen", "height"}

    # 坐标为负或越过录制分辨率也拒绝（与 DeviceManager.click 的越界口径一致）。
    for step in (
        Step(action="click", x=-1, y=0, screen=(1280, 720)),
        Step(action="click", x=1280, y=0, screen=(1280, 720)),
        Step(action="click", x=0, y=720, screen=(1280, 720)),
        Step(
            action="swipe",
            x1=0,
            y1=0,
            x2=1280,
            y2=720,
            duration_ms=300,
            screen=(1280, 720),
        ),
    ):
        with pytest.raises(CompileError):
            compile_steps(prefix=PREFIX, steps=[step])


def test_parse_step_and_names():
    step = parse_step(
        {
            "seq": 1,
            "action": "click",
            "x": 640,
            "y": 360,
            "x1": None,
            "duration_ms": None,
            "delay_ms": None,
            "screen": [1280, 720],
        }
    )
    assert step == Step(
        action="click",
        x=640,
        y=360,
        delay_ms=0,
        screen=(1280, 720),
    )
    assert isinstance(step.screen, tuple)

    # 名字按 spec 第 8 条清理：非白名单字符换 `_`、首字符必须是字母或数字。
    compiled = compile_steps(
        prefix="Custom_活动 2/3",
        steps=[Step(action="click", x=1, y=1, screen=(1280, 720))],
    )
    for name in compiled.tasks:
        assert NAME_PATTERN.fullmatch(name), name
        assert len(name) <= 62
    compiled = compile_steps(
        prefix="Custom_" + "x" * 80,
        steps=[Step(action="click", x=1, y=1, screen=(1280, 720))],
    )
    assert all(len(name) <= 62 for name in compiled.tasks)
    assert all(NAME_PATTERN.fullmatch(name) for name in compiled.tasks)
