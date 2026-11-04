"""产物服务（M75 规格 §4.2、§4.5）。

对外的公开名只有 :class:`ArtifactService`；装配方在应用生命周期里把它挂到
``app.state.artifact_service``，中间件与 ``read_artifact`` / ``write_note`` 工具
都从那里取。
"""

from maa_api.agent.artifacts.service import (
    DB_STORAGE_MAX_BYTES,
    MAX_ARTIFACT_BYTES,
    SPILL_RETENTION_DAYS,
    ArtifactDetail,
    ArtifactService,
)

__all__ = [
    "DB_STORAGE_MAX_BYTES",
    "MAX_ARTIFACT_BYTES",
    "SPILL_RETENTION_DAYS",
    "ArtifactDetail",
    "ArtifactService",
]
