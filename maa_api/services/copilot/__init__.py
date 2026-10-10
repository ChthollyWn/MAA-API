"""作业资源与草稿（M29，规格 §9.5）。

- :mod:`maa_api.services.copilot.rules`：协议校验、练度比对与静态检查（纯函数）；
- :mod:`maa_api.services.copilot.draft`：:class:`CopilotDraftService`，把作业存成
  ``kind=COPILOT`` 的 :class:`~maa_api.db.models.ResourceAsset`。

``services/copilot/grant.py``、``attempt.py``、``result.py`` 由同一里程碑的另两条
工作流放进这个包（规格 §9.6–§9.7）。
"""

from __future__ import annotations

from maa_api.services.copilot.draft import CopilotDraftService

__all__ = ["CopilotDraftService"]
