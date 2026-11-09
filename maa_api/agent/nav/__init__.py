"""界面导航图（M86 spec §4.2）：识别、图服务与导航执行器。

本包按职责分文件：``recognizer.py`` 做 dHash 与视觉分类，``graph.py`` 放图服务与
最短路径，``seed.py`` 放种子常量，``navigator.py`` 放导航执行。当前阶段只落地识别器，
后续任务补齐后再在这里一并导出 ``NavGraphService`` 与 ``Navigator``。
"""

from maa_api.agent.nav.recognizer import (
    Recognition,
    Recognizer,
    VisionClient,
    dhash,
    distances,
    signature,
)

__all__ = [
    "Recognition",
    "Recognizer",
    "VisionClient",
    "dhash",
    "distances",
    "signature",
]
