"""界面导航图（M86 spec §4.2）：识别、图服务与导航执行器。

本包按职责分文件：``recognizer.py`` 做 dHash 与视觉分类，``graph.py`` 放图服务与
最短路径，``seed.py`` 放种子常量，``navigator.py`` 放导航执行。T5 的三个工具直接从
这里 ``from maa_api.agent.nav import NavGraphService, Recognizer, Navigator``。
"""

from maa_api.agent.nav.graph import NavGraphService
from maa_api.agent.nav.navigator import Navigator
from maa_api.agent.nav.recognizer import (
    Recognition,
    Recognizer,
    VisionClient,
    dhash,
    distances,
    signature,
)

__all__ = [
    "NavGraphService",
    "Navigator",
    "Recognition",
    "Recognizer",
    "VisionClient",
    "dhash",
    "distances",
    "signature",
]
