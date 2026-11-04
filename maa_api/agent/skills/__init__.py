"""M78 技能包：内置技能加载、注册表与（M78 T4 起）中间件。

``SkillsMiddleware`` 由 T4 提供；本包先只导出注册表相关的名字，等中间件落地
时再补上 ``__all__``。用 try/except 是为了让 T3 的注册表能被单独使用与测试。
"""

from __future__ import annotations

from maa_api.agent.skills.registry import (
    SkillRegistry,
    builtin_skills,
    load_builtin_skills,
)

__all__ = [
    "SkillRegistry",
    "builtin_skills",
    "load_builtin_skills",
]

try:  # pragma: no cover - T4 落地后这个分支恒成立
    from maa_api.agent.skills.middleware import SkillsMiddleware
except ImportError:
    pass
else:
    __all__.append("SkillsMiddleware")
