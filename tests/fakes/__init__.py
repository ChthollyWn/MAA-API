"""测试替身（Fake）包：不依赖 MaaCore、不依赖真实设备。"""

from tests.fakes.fake_asst import (
    DISCONNECT_SCRIPT,
    FAILURE_SCRIPT,
    SCRIPTS,
    STUCK_SCRIPT,
    SUCCESS_SCRIPT,
    CallbackRecord,
    FakeAsst,
    FakeScript,
    ScriptedEvent,
    get_script,
)

__all__ = [
    "CallbackRecord",
    "DISCONNECT_SCRIPT",
    "FAILURE_SCRIPT",
    "FakeAsst",
    "FakeScript",
    "SCRIPTS",
    "STUCK_SCRIPT",
    "SUCCESS_SCRIPT",
    "ScriptedEvent",
    "get_script",
]
