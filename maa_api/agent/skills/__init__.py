"""M78 技能包：内置技能加载、注册表与中间件。"""

from __future__ import annotations

from maa_api.agent.skills.middleware import SkillsMiddleware
from maa_api.agent.skills.registry import (
    SkillRegistry,
    builtin_skills,
    load_builtin_skills,
)

__all__ = [
    "SkillRegistry",
    "SkillsMiddleware",
    "builtin_skills",
    "load_builtin_skills",
]
