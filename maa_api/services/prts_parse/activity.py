"""活动页解析（规格 §5.6）：`{{活动信息}}` 的字段 + 正文里的关卡引用。

只做纯函数：wikitext 进，结构出，不联网、不读盘。

实测（2026-10-10）两种写法：

- ``矢量突破/03``：``{{关卡链接|VEC-01|act3break 01}}``——第二参数是 stageId（空格代下划线）；
- ``昨日海``：``{{关卡链接|YW-1}}``——只有代号，要靠重定向解析页面标题。

第二参数也可能是页面名（``{{关卡链接|VEC-SP01|VEC-SP01 重力危机|…}}``），所以只把
「全小写字母/数字/下划线/空格」的形状当 stageId。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from maa_api.services.prts_parse.wikitext import (
    Template,
    find_templates,
    parse_templates,
    strip_markup,
)

__all__ = [
    "ACTIVITY_TEMPLATE",
    "CN_TIMEZONE",
    "ActivityPage",
    "StageRef",
    "clean_text",
    "is_stage_code",
    "parse_activity_page",
    "parse_page_time",
    "template_param",
    "template_positional",
]

#: 活动信息模板名（实测）。
ACTIVITY_TEMPLATE = "活动信息"

#: PRTS 上的时间都是北京时间（SMW 的 `raw` 是 UTC，页面正文写的是 UTC+8）。
CN_TIMEZONE = timezone(timedelta(hours=8))

#: 关卡代号：``VEC-01``、``YW-ST-1``、``YW-EX-1``、``7-18``。
_STAGE_CODE = re.compile(r"^[A-Z0-9]{1,8}(?:-[A-Za-z0-9]{1,8})*$")

#: stageId 的形状：全小写字母/数字/下划线（空格按实测当写错的下划线）。
_STAGE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

#: 页面里的关卡链接：``[[VEC-01 寂静螺旋]]`` / ``[[VEC-01 寂静螺旋|显示名]]``。
_WIKI_LINK = re.compile(r"\[\[([^\[\]|#<>]{1,120})(?:\|[^\[\]<>]{0,120})?\]\]")

#: 时间写法：``2026-09-29 16:00``（也可能带秒）。
_PAGE_TIME = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"(?:[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?$"
)


@dataclass(frozen=True, slots=True)
class StageRef:
    """活动页里提到的一个关卡：代号 + 可选的 stageId / 页面名提示。"""

    code: str
    stage_id: str | None = None
    page_title: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityPage:
    """活动页解析结果。时间优先用 SMW，页面正文的时间作兜底。"""

    page_title: str
    name: str | None
    type: str | None
    start_at: datetime | None
    end_at: datetime | None
    exchange_end_at: datetime | None
    stages: tuple[StageRef, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        """全部代号，保持页面出现顺序、去重。"""
        seen: dict[str, None] = {}
        for stage in self.stages:
            seen.setdefault(stage.code, None)
        return tuple(seen)


def is_stage_code(value: str) -> bool:
    """看起来像关卡代号：字母数字加连字符，且不是纯数字。"""
    if not value or not _STAGE_CODE.match(value):
        return False
    return not value.isdigit()


#: 日期形状：正文里 ``[[2026-09-29 活动公告]]`` 这类链接不是关卡。
_DATE_LIKE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")

#: 主线代号：``7-18``（两段纯数字）。
_MAIN_CODE_LIKE = re.compile(r"^\d{1,3}-\d{1,3}$")


def is_body_stage_code(value: str) -> bool:
    """正文链接里的代号判据：比模板参数严一档。

    模板里的 ``{{关卡链接|…}}`` 是作者显式写的，形状够就行；正文链接是猜的，要求
    「带字母」或「两段数字的主线形状」，把 ``2026-09-29`` 这类日期挡在外面。
    """
    if not is_stage_code(value) or _DATE_LIKE.match(value):
        return False
    return bool(re.search(r"[A-Za-z]", value)) or bool(_MAIN_CODE_LIKE.match(value))


def parse_page_time(value: str | None) -> datetime | None:
    """``2026-09-29 16:00`` → 北京时间的时间点；解析不了返回 ``None``。"""
    if not isinstance(value, str):
        return None
    match = _PAGE_TIME.match(value.strip())
    if match is None:
        return None
    parts = {key: int(raw) if raw else 0 for key, raw in match.groupdict().items() if key != "second"}
    second = int(match.group("second") or 0)
    try:
        return datetime(
            parts["year"], parts["month"], parts["day"], parts["hour"], parts["minute"], second,
            tzinfo=CN_TIMEZONE,
        )
    except ValueError:
        return None


def parse_activity_page(content: str, *, page_title: str) -> ActivityPage:
    """解析活动页：模板字段 + 正文里的关卡引用。"""
    info = _first_template(content, ACTIVITY_TEMPLATE)
    stages = _stage_refs(content)
    return ActivityPage(
        page_title=page_title,
        name=clean_text(template_param(info, "名称缩短")) or clean_text(template_param(info, "名称")),
        type=clean_text(template_param(info, "类型")),
        start_at=parse_page_time(template_param(info, "活动开始时间")),
        end_at=parse_page_time(template_param(info, "活动结束时间")),
        exchange_end_at=parse_page_time(template_param(info, "兑换结束时间")),
        stages=stages,
    )


def _stage_refs(content: str) -> tuple[StageRef, ...]:
    """从 `{{关卡链接}}` 与正文链接里取关卡引用（保持出现顺序、按代号去重）。"""
    refs: dict[str, StageRef] = {}
    for template in find_templates(content, "关卡链接"):
        positional = template_positional(template)
        if not positional:
            continue
        # 实测有 `VEC-<big>'''A'''</big>` 这类带强调标记的代号，先清洗再判形状。
        code = clean_text(positional[0]) or ""
        if not is_stage_code(code):
            continue
        hint = clean_text(positional[1]) if len(positional) > 1 else None
        ref = _ref_from_hint(code, hint)
        _remember(refs, ref)

    for match in _WIKI_LINK.finditer(content):
        target = match.group(1).strip()
        code, _, tail = target.partition(" ")
        if not is_body_stage_code(code) or not tail.strip():
            continue
        _remember(refs, StageRef(code=code, page_title=target))
    return tuple(refs.values())


def _remember(refs: dict[str, StageRef], ref: StageRef) -> None:
    """同代号多次出现：保留首次的顺序，但把缺失的 stageId / 页面名补上。

    实测活动页可能「模板只给代号、正文链接给页面名」混写，先到先得会把提示丢掉。
    """
    existing = refs.get(ref.code)
    if existing is None:
        refs[ref.code] = ref
        return
    refs[ref.code] = StageRef(
        code=ref.code,
        stage_id=existing.stage_id or ref.stage_id,
        page_title=existing.page_title or ref.page_title,
    )


def _ref_from_hint(code: str, hint: str | None) -> StageRef:
    """第二参数：stageId（空格代下划线）优先，否则当页面名。"""
    if not hint:
        return StageRef(code=code)
    normalized = hint.replace(" ", "_")
    if _STAGE_ID.match(normalized):
        return StageRef(code=code, stage_id=normalized)
    return StageRef(code=code, page_title=hint)


def _first_template(content: str, name: str) -> Template | None:
    templates = find_templates(content, name)
    return templates[0] if templates else None


# -- 与 wikitext 解析层的读取适配 -------------------------------------------------
# `Template` 的读取辅助由解析层提供（`param` / `positional`）；这里再兜一层，
# 以便参数形状微调时不至于让活动解析整套跟着改。`stage.py` 复用这三个公开名字。
def template_param(template: Template | None, name: str) -> str | None:
    if template is None:
        return None
    getter = getattr(template, "param", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return value
    for arg in getattr(template, "args", ()):  # pragma: no cover - 兜底路径
        if getattr(arg, "key", None) == name:
            return getattr(arg, "value", None)
    return None


def template_positional(template: Template) -> list[str]:
    getter = getattr(template, "positional", None)
    if callable(getter):
        return [str(value) for value in getter()]
    return [  # pragma: no cover - 兜底路径
        str(getattr(arg, "value", ""))
        for arg in getattr(template, "args", ())
        if getattr(arg, "key", None) is None
    ]


def clean_text(value: str | None) -> str | None:
    """模板字段清洗：去掉展示模板与链接，压掉多余空白。"""
    if value is None:
        return None
    cleaned = strip_markup(value).strip()
    return cleaned or None


def template_names(content: str) -> tuple[str, ...]:
    """页面里出现的模板名（调试与解析器选择用）。"""
    return tuple(template.name for template in parse_templates(content))


def param_map(content: str, template_name: str) -> Mapping[str, Any]:
    """某个模板的命名参数表（调试用）。"""
    template = _first_template(content, template_name)
    if template is None:
        return {}
    return {
        getattr(arg, "key", None): getattr(arg, "value", None)
        for arg in getattr(template, "args", ())
        if getattr(arg, "key", None)
    }


def iter_links(content: str) -> Iterable[tuple[str, str]]:
    """页面里的 ``(目标, 显示名)`` 链接表（调试与后续里程碑用）。"""
    for match in _WIKI_LINK.finditer(content):
        target = match.group(1).strip()
        yield target, target
