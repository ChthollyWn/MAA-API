"""规则只看自然语言句式与结构化伪造，编码过的指令不在范围内。
改规则必须同步 tests/agent/guard/test_rules.py 的样例。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["low", "medium", "high"]
_I, _M = re.IGNORECASE, re.MULTILINE
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad\u202a-\u202e]")
_SPACES = re.compile(r"[^\S\n]+")
_SKIP_KEYS = frozenset({"image_base64", "base64", "image", "nonce"})
HIDDEN_CHAR_THRESHOLD, EXCERPT_CHARS, MAX_HITS = 10, 80, 5


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    category: str
    severity: Severity
    pattern: str
    flags: int = 0


@dataclass(frozen=True, slots=True)
class GuardHit:
    rule: str
    category: str
    severity: Severity
    excerpt: str


RULES: tuple[Rule, ...] = (
    Rule("override.zh", "override", "medium",
         r"(?:忽略|无视|忘掉|忘记|不要理会|别管|覆盖|推翻)(?:掉|了)?(?:你|您)?(?:的)?(?:之前|此前|先前|以上|上面|前面|上述|刚才|原来|原有|所有|全部|一切)"
         r"(?:的|所有|全部|一切){0,2}(?:系统)?(?:指令|指示|提示词|提示|规则|设定|要求|约束|限制|命令)"),
    Rule("override.en", "override", "medium",
         r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+|of\s+)*"
         r"(?:previous|prior|above|earlier|preceding|original|system|existing)?\s*(?:instructions?|prompts?|rules?|directions?|guidelines?)\b", _I),
    Rule("role.zh", "role", "low",
         r"(?:从现在(?:起|开始)|接下来)?你(?:现在|从现在起)?(?:是|将是|将扮演|要扮演|扮演|作为|变成)(?:一个|一名)?"
         r"(?:不受限制|没有限制|无限制|新的|另一个)?(?:的)?(?:AI|人工智能|助手|模型|管理员|开发者|系统)"),
    Rule("role.zh_mode", "role", "low", r"(?:开发者|调试|越狱|DAN|无限制|上帝)模式"),
    Rule("role.en", "role", "low",
         r"\b(?:you\s+are\s+now|from\s+now\s+on,?\s+you\s+(?:are|will|must)|act\s+as\s+(?:an?\s+)?(?:unrestricted|jailbroken|developer|admin)"
         r"|pretend\s+(?:to\s+be|you\s+are))\b", _I),
    Rule("role.en_mode", "role", "low", r"\b(?:developer|DAN|jailbreak|god)\s+mode\b", _I),
    Rule("fake_system.role_label", "fake_system", "high", r"(?:^|\n)\s*(?:system|assistant|developer)\s*[:：]", _I | _M),
    Rule("fake_system.chat_token", "fake_system", "high",
         r"<\|?(?:im_start|im_end|system|endoftext|eot_id|start_header_id)\|?>|\[/?(?:INST|SYS)\]|<</?SYS>>", _I),
    Rule("fake_system.prefix", "fake_system", "high", r"【\s*(?:系统|系统提示|系统消息|系统指令|工单汇报|外部数据|安全提示|管理员指令|开发者指令)"),
    Rule("fake_system.heading", "fake_system", "high",
         r"(?:^|\n)\s*#{1,6}\s*(?:system\s*prompt|new\s+instructions|系统提示词?|新的?指令)", _I | _M),
    Rule("fake_tool_call.json", "fake_tool_call", "high",
         r"\"(?:tool_calls|function_call)\"\s*:|<(?:tool_call|function_call|tool_use|invoke)\b", _I),
    Rule("fake_confirmation.zh", "fake_confirmation", "high",
         r"(?:用户|管理员|博士|操作员)(?:已经|已)(?:确认|同意|批准|授权)(?:了)?[^。\n]{0,12}(?:操作|执行|调用|工具|无需确认|不用确认|直接)"),
    Rule("fake_confirmation.en", "fake_confirmation", "high",
         r"\b(?:the\s+)?(?:user|admin|operator|owner)\s+(?:has\s+)?(?:already\s+)?(?:approved|confirmed|authorized|consented)\b", _I),
    Rule("fake_confirmation.json", "fake_confirmation", "high",
         r"\"(?:approved|confirmed)\"\s*:\s*true|\"status\"\s*:\s*\"approved\"|confirmation_id\"?\s*[:=]", _I),
    Rule("skip_confirmation.zh", "fake_confirmation", "medium",
         r"(?:无需|不需要|不用|跳过|绕过|免去)(?:再)?(?:向用户|让用户|用户)?(?:确认|询问|审批|授权|请示)"),
    Rule("skip_confirmation.en", "fake_confirmation", "medium",
         r"\b(?:no|without)\s+(?:need\s+(?:for|to)\s+)?(?:confirmation|approval|asking)\b|\bauto[-\s]?(?:approve|confirm)", _I),
    Rule("exfiltration.zh", "exfiltration", "high",
         r"(?:系统提示词?|提示词|token|密钥|api[\s_-]?key|访问密钥|配置文件|config\.yaml|聊天记录|对话记录)(?:的)?\s*(?:原文|内容|全部)?\s*"
         r"(?:发送|发给|告诉|输出|贴出|上传|回复)", _I),
    Rule("exfiltration.en", "exfiltration", "high",
         r"\b(?:reveal|print|repeat|output|send|leak|upload)\b(?:\W+\w+){0,4}?\W+(?:system\s+prompt|api[\s_-]?key|access\s+token|password|secret|credentials?)\b", _I),
    Rule("steer_url", "steer_url", "medium", r"(?:访问|打开|请求|发送到|提交到|post\s+to|send\s+to|fetch|visit)\s*https?://", _I),
)
_COMPILED = tuple((rule, re.compile(rule.pattern, rule.flags)) for rule in RULES)


def normalize(text: str) -> tuple[str, int]:
    """NFKC、去零宽与方向控制字符、压缩行内空白（保留换行）；返回文本与去掉的隐藏字符数。"""
    text = unicodedata.normalize("NFKC", text)
    hidden = len(_ZERO_WIDTH.findall(text))
    return _SPACES.sub(" ", _ZERO_WIDTH.sub("", text)), hidden


def tool_steer_pattern(write_tools: Iterable[str]) -> re.Pattern[str] | None:
    """「调用 / 执行 某个写工具」的规则；工具名由调用方传入。"""
    names = sorted({name for name in write_tools if name}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?:调用|执行|运行|使用|触发|\bcall|\binvoke|\brun|\bexecute|\buse)\s*(?:工具|函数|the\s+tool|tool|function)?\s*"
                      r"[`'\"「(（]?\s*(?:" + "|".join(map(re.escape, names)) + r")\b", re.IGNORECASE)


def collect_text(value: Any, *, limit: int) -> str:
    """深度优先收集字符串值（不收键名与 _SKIP_KEYS 下的值），换行拼接，截到 limit 字。"""
    parts, stack = [], [value]
    while stack and sum(map(len, parts)) < limit:
        node = stack.pop()
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, Mapping):
            stack.extend(child for key, child in reversed(list(node.items())) if key not in _SKIP_KEYS)
        elif isinstance(node, (list, tuple)):
            stack.extend(reversed(node))
    return "\n".join(parts)[:limit]


def _excerpt(text: str, start: int, end: int) -> str:
    pad = max(0, (EXCERPT_CHARS - (end - start)) // 2)
    left, right = max(0, start - pad), min(len(text), end + pad)
    return ("…" if left else "") + text[left:right].replace("\n", " ")[:EXCERPT_CHARS] + ("…" if right < len(text) else "")


def scan_text(text: str, *, write_tools: Iterable[str] = ()) -> tuple[GuardHit, ...]:
    """每条规则只记第一次命中，按清单顺序，最多 MAX_HITS 条。"""
    normalized, hidden = normalize(text)
    checks = [(rule.id, rule.category, rule.severity, compiled) for rule, compiled in _COMPILED]
    steer = tool_steer_pattern(write_tools)
    if steer is not None:
        checks.append(("fake_tool_call.steer", "fake_tool_call", "high", steer))
    hits = [GuardHit(rule_id, category, severity, _excerpt(normalized, m.start(), m.end()))
            for rule_id, category, severity, compiled in checks if (m := compiled.search(normalized))]
    if hidden >= HIDDEN_CHAR_THRESHOLD:
        hits.append(GuardHit("obfuscation.hidden_chars", "obfuscation", "low", f"{hidden} 个隐藏字符"))
    return tuple(hits[:MAX_HITS])


def is_suspicious(hits: Sequence[GuardHit]) -> bool:
    """有高危或中危命中，或者有两条以上不同的低危命中。"""
    if any(hit.severity in ("high", "medium") for hit in hits):
        return True
    return len({hit.rule for hit in hits if hit.severity == "low"}) >= 2
