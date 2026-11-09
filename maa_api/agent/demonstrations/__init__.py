"""M89 示教录制服务（spec §4.2）。"""

from maa_api.agent.demonstrations.service import (
    MAX_DEMONSTRATION_STEPS,
    DemonstrationService,
)

__all__ = ["DemonstrationService", "MAX_DEMONSTRATION_STEPS"]
