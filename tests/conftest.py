"""pytest 公共 fixture。

这里只放不依赖内核、不依赖设备的 fixture；需要真机的用例请标
``@pytest.mark.hardware``（默认不进 CI）。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from tests.fakes.fake_asst import FakeAsst, FakeScript  # noqa: E402


@pytest.fixture
def fake_asst() -> FakeAsst:
    """一个跑“成功”剧本的 FakeAsst，用例结束自动停线程并复位类级状态。"""
    FakeAsst.reset_class_state()
    asst = FakeAsst(script="success")
    yield asst
    asst.stop()
    FakeAsst.reset_class_state()


@pytest.fixture
def fake_asst_factory():
    """按剧本名构造 FakeAsst 的工厂，统一负责清理。"""
    FakeAsst.reset_class_state()
    created: list[FakeAsst] = []

    def make(script: FakeScript | str = "success", **kwargs) -> FakeAsst:
        asst = FakeAsst(script=script, **kwargs)
        created.append(asst)
        return asst

    yield make

    for asst in created:
        asst.stop()
    FakeAsst.reset_class_state()
