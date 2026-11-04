"""事件源与两级过滤（M81 规格 §4.2）。

本包按 spec 的分层提供：

- :mod:`maa_api.agent.events.kinds`：12 种事件、准入校验、摘要与「交总控」判断；
- :mod:`maa_api.agent.events.builtin_rules`：五条内置规则与诊断工单草稿；
- :mod:`maa_api.agent.events.rules`：生效规则、匹配、覆盖语义与 ``TriggerRuleService``；
- :mod:`maa_api.agent.events.evaluator`：``trigger_evaluator`` 研判与事件卡。

``TriggerService``（事件入表与路由）由 M81 T5 加在本包；本模块只导出上面的类型。
"""

from maa_api.agent.events.builtin_rules import BUILTIN_RULES
from maa_api.agent.events.evaluator import TriggerEvaluator
from maa_api.agent.events.kinds import KNOWN_KINDS
from maa_api.agent.events.rules import (
    EffectiveRule,
    RuleSet,
    TriggerRuleService,
)

__all__ = [
    "BUILTIN_RULES",
    "KNOWN_KINDS",
    "EffectiveRule",
    "RuleSet",
    "TriggerEvaluator",
    "TriggerRuleService",
]
