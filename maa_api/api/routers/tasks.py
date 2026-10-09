"""任务类型 schema 导出与参数校验（docs/05 §6.7、§8、§9）。

三个端点共用同一份判别联合 :data:`~maa_api.domain.task.TaskInput`：
``GET /api/tasks/types`` 给任务类型的完整参数 schema，供前端动态生成表单、agent 了解可用
参数；``GET /api/tasks/types/{type_name}`` 给单个类型；``POST /api/tasks/validate`` 只校验
不提交，返回规范化后的参数与将被注入的默认值。

前两者输出 pydantic 直出的标准 JSON Schema：中文标签、分组、控件提示等元信息挂在
``x-*`` 前缀的自定义关键字上（校验器忽略未知关键字，schema 仍可喂给任何标准校验器），
取值范围用 ``minimum`` / ``maximum`` / ``enum`` / ``minItems`` / ``maxItems`` 等原生
关键字，跨字段依赖用 ``x-depends-on``（docs/05 §8.2、docs/09 §7.2）。

``POST /validate`` 走与提交路径同一个 :func:`~maa_api.domain.task.normalize` 做「校验
之后、落库之前」的注入（docs/05 §9）。:func:`load_channel_defaults` 按 setting 仓储读
``channel.client_type`` / ``channel.server`` 两个 key，读不到或值非法时回退
:class:`~maa_api.domain.task.ChannelDefaults` 的默认值（Bilibili / CN）。两个 types 端点
还把当前生效值注入响应 schema 里这两个字段的 ``default``，供前端按生效值预填；模型里两个
字段仍是 ``default=None``，``model_fields_set`` 的注入语义没变，overlay 写在
:func:`_overlay_channel_defaults` 的深拷贝上，不污染 ``model_json_schema()``。不做
进程内缓存、不监听设置变更，每次请求两个主键点查。

``/api/tasks/types`` 必须声明在 ``/api/tasks/{task_id}`` 之前，否则 ``types`` 会被当成
task_id 走进详情端点（docs/05 §6.5，与 ``/api/pipelines/current`` 同理）。三个端点都不在
docs/05 §5.3 的豁免清单里，统一由 router 级的 ``Depends(require_auth)`` 保护。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import Task
from maa_api.db.repositories.pipeline import TaskRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import (
    RUNTIME_IMMUTABLE,
    TASK_LABELS,
    ChannelDefaults,
    TaskInput,
    normalize,
)
from maa_api.services.task_defaults import load_channel_defaults

__all__ = [
    "BASE_GROUP",
    "TASK_MODELS",
    "TaskValidateRequest",
    "_collect_groups",
    "_overlay_channel_defaults",
    "export_type_schema",
    "load_channel_defaults",
    "router",
]

#: 没有 ``x-group`` 的字段归入的分区名。与 :func:`~maa_api.domain.task.MaaField`
#: 的 ``group`` 默认值一致，因此任务模型里的字段实际上都带 ``x-group``，
#: 只有将来新增的、忘了标注的字段才会走到这个兜底。
BASE_GROUP = "基础"

#: ``name`` → 模型：按 docs/05 §8.1 的写法直接从判别联合推导，保证「联合是唯一真源」。
#: 顺序即 ``TaskInput`` 的定义顺序（docs/05 §7.4），也是 /types 的清单顺序。
TASK_MODELS: dict[str, type[BaseModel]] = {
    model.model_fields["name"].default: model
    for model in get_args(get_args(TaskInput)[0])
}

def _collect_groups(schema: Mapping[str, Any]) -> list[str]:
    """按字段声明顺序收集 ``x-group``，去重后返回（顺序即前端渲染顺序）。

    docs/09 §7.2 的契约：``groups`` 是有序的中文字符串数组，直接作为折叠面板的
    分区标题，前端不要重排；没有 ``x-group`` 的字段归入兜底分区
    :data:`BASE_GROUP`。模型的首字段 ``name`` 一定带 ``x-group: 基础``，
    所以「基础」天然排在首位；这里再显式把它移到队首，防止将来某个模型调整字段
    声明顺序后基础分区被挤到中间。
    """
    groups: list[str] = []
    for field in schema.get("properties", {}).values():
        group = field.get("x-group", BASE_GROUP)
        if group not in groups:
            groups.append(group)
    if BASE_GROUP in groups:
        groups.remove(BASE_GROUP)
        groups.insert(0, BASE_GROUP)
    return groups


#: :func:`export_type_schema` 的默认渠道默认值，供**不走 HTTP** 的调用方使用：
#: ``scripts/generate_task_types_fixture.py`` 直接调它生成前端 fixture，而该
#: fixture 必须与 ``GET /api/tasks/types`` 返回的结构完全一致（含渠道 overlay）。
#: 端点传入 ``await load_channel_defaults(session)`` 的生效值。本常量只读，
#: 调用方不要改写它。
_BUILTIN_CHANNEL_DEFAULTS = ChannelDefaults()

#: overlay 的字段名 → :class:`~maa_api.domain.task.ChannelDefaults` 属性名。
#: 当前一一对应，显式写成映射可以挡住「将来 schema 字段改名、overlay 静默失效」。
_CHANNEL_DEFAULT_FIELDS: Mapping[str, str] = {
    "client_type": "client_type",
    "server": "server",
}


def _overlay_channel_defaults(
    schema: dict[str, Any], channel_defaults: ChannelDefaults
) -> dict[str, Any]:
    """在 schema 的**副本**上给渠道字段注入当前生效的 ``default``（spec §3.4）。

    ``client_type`` / ``server`` 在模型里刻意保持 ``default=None``（服务端按
    ``model_fields_set`` 注入，docs/05 §9），但前端要按生效值预填，所以导出时
    覆盖这一处 ``default``：只动副本，模型、OpenAPI 快照与 ``POST /validate``
    的语义都不受影响。

    ``model_json_schema()`` 每次返回新 dict，但那是实现细节；
    这里先 :func:`copy.deepcopy` 再改，避免就地改写共享或缓存的结果。
    """
    copied = copy.deepcopy(schema)
    properties = copied.get("properties")
    if not isinstance(properties, dict):
        return copied
    for field_name, attribute in _CHANNEL_DEFAULT_FIELDS.items():
        field = properties.get(field_name)
        if isinstance(field, dict):
            field["default"] = getattr(channel_defaults, attribute)
    return copied


def export_type_schema(
    model: type[BaseModel],
    channel_defaults: ChannelDefaults = _BUILTIN_CHANNEL_DEFAULTS,
) -> dict[str, Any]:
    """导出一个任务类型的对外 schema（docs/05 §8.1）。

    :param model: task union 中的 pydantic 模型。
    :param channel_defaults: 注入 ``client_type`` / ``server`` ``default`` 的生效
        渠道默认值；默认取内置默认值（Bilibili / CN），端点传设置表里的值。
    :returns: ``{name, label, description, schema, groups, runtime_immutable}``；
        ``schema`` 是 ``model_json_schema(ref_template="#/$defs/{model}")`` 的
        **深拷贝**（``x-*`` 关键字保留，``$defs`` 里的模型引用指向
        ``#/$defs/<Name>``，方便前端在同一份文档内解析引用），渠道字段的
        ``default`` 已替换为 ``channel_defaults`` 里的生效值。
    """
    name = model.model_fields["name"].default
    schema = _overlay_channel_defaults(
        model.model_json_schema(ref_template="#/$defs/{model}"), channel_defaults
    )
    return {
        "name": name,
        "label": TASK_LABELS[name],
        # 类文档字符串即「这个任务是做什么的」的一句话说明（docs/05 §8.1）。
        "description": (model.__doc__ or "").strip(),
        "schema": schema,
        "groups": _collect_groups(schema),
        # 拷贝一份：调用方改到列表也不会污染 domain 层的注册表。
        "runtime_immutable": list(RUNTIME_IMMUTABLE[name]),
    }


class TaskValidateRequest(BaseModel):
    """``POST /api/tasks/validate`` 的请求体：只有任务数组。

    不含 ``title`` / ``priority`` / ``notify_on_finish``：这个端点只校验参数，
    不建流水线。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "tasks": [
                        {"name": "StartUp", "client_type": "Official"},
                        {
                            "name": "Fight",
                            "stage": "1-7",
                            "series": 0,
                            "medicine": 2,
                        },
                        {
                            "name": "Recruit",
                            "select": [4, 5],
                            "confirm": [3, 4, 5],
                            "times": 4,
                        },
                    ]
                }
            ]
        },
    )

    tasks: list[TaskInput] = Field(min_length=1, max_length=32)


router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_auth)],
)


@router.get(
    "/types",
    summary="获取任务类型 schema",
    description=(
        "返回全部任务类型的参数 schema，供前端动态生成表单。只读、无副作用。\n\n"
        "- `schema` 为标准 JSON Schema；中文标签、分组、控件提示与风险标记挂在"
        " `x-label` / `x-group` / `x-widget` / `x-enum-labels` / `x-risk` /"
        " `x-depends-on` 上，取值范围走 `minimum` / `maximum` / `enum` 等原生关键字\n"
        "- `client_type` / `server` 的 `default` 是当前生效的全局渠道默认值"
        "（默认 Bilibili / CN）；提交时不传这两个字段仍按同一默认值注入，"
        "显式传 `null` 表示留空\n"
        "- 返回当前支持的全部任务类型，不接受分页参数；`lang` 当前仅支持 `zh`，"
        "其它值返回 400 `INVALID_PARAMETER`"
    ),
    responses=error_responses("INVALID_PARAMETER", "UNAUTHORIZED"),
)
async def list_types(
    session: Annotated[AsyncSession, Depends(get_session)],
    lang: Annotated[str, Query(description="中文标签语言；当前仅支持 zh")] = "zh",
) -> dict[str, Any]:
    """任务类型的 schema 清单。渠道默认值每请求现读，overlay 只作用于响应副本。"""
    if lang != "zh":
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"不支持的语言：{lang}，可选：zh",
            {"lang": lang, "supported": ["zh"]},
        )
    channel_defaults = await load_channel_defaults(session)
    items = [
        export_type_schema(model, channel_defaults) for model in TASK_MODELS.values()
    ]
    return {"items": items, "total": len(items), "page": 1, "size": len(items)}


@router.get(
    "/types/{type_name}",
    summary="获取单个任务类型 schema",
    description=(
        "按类型名返回单个任务类型的 schema，结构与 `GET /api/tasks/types` 的 "
        "`items` 元素一致（不套分页信封）。只读、无副作用。\n\n"
        "- 渠道字段的 `default` 是当前生效的全局渠道默认值\n"
        "- `type_name` 不在支持列表内时返回 400 `UNKNOWN_TASK_TYPE`"
    ),
    responses=error_responses("UNKNOWN_TASK_TYPE", "UNAUTHORIZED"),
)
async def get_type(
    type_name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """单个任务类型的 schema。"""
    model = TASK_MODELS.get(type_name)
    if model is None:
        raise AppError(
            ErrorCode.UNKNOWN_TASK_TYPE,
            f"未知的任务类型：{type_name}，可选值见 GET /api/tasks/types",
            {"type_name": type_name, "expected": list(TASK_MODELS)},
        )
    # 未知类型在点查设置**之前**就返回 400（错误路径不依赖库表）。
    channel_defaults = await load_channel_defaults(session)
    return export_type_schema(model, channel_defaults)


@router.post(
    "/validate",
    summary="校验任务参数",
    description=(
        "校验并规范化任务数组，返回投递给内核的最终参数与原始提交。"
        "不落库、不入队、不碰内核。\n\n"
        "- 未显式指定 `client_type` / `server` 的任务会注入当前生效的全局渠道"
        "默认值（默认 Bilibili / CN）；显式传 `null` 表示留空，不注入\n"
        "- `params` 是投递给内核的形态（已剔除 null、已注入默认值），"
        "`raw_params` 是原始提交快照（保留显式 null）\n"
        "- 任务数 1–32，越界返回 422 `VALIDATION_ERROR`"
    ),
    responses=error_responses(
        "UNKNOWN_TASK_TYPE",
        "TASK_PARAM_DEPRECATED",
        "TASK_PARAM_INVALID",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
)
async def validate(
    payload: TaskValidateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """校验并规范化任务数组。

    默认值注入只发生在 :func:`~maa_api.domain.task.normalize` 里；本函数只取渠道
    默认值，否则 ``model_fields_set`` 与 ``raw_params`` 会失真。
    """
    defaults = await load_channel_defaults(session)
    items = [normalize(task, defaults) for task in payload.tasks]
    return {
        "items": [item.model_dump() for item in items],
        "total": len(items),
        "page": 1,
        "size": len(items),
    }


@router.get(
    "/{task_id}",
    summary="获取单任务详情",
    responses=error_responses("TASK_NOT_FOUND", "UNAUTHORIZED"),
)
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    task = await TaskRepository(session).get(task_id)
    if task is None:
        raise AppError(
            ErrorCode.TASK_NOT_FOUND,
            "任务不存在，请刷新流水线详情后重试",
            {"task_id": task_id},
        )
    return _task_detail(task)


def _task_detail(task: Task) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ended = task.finished_at or now
    duration = (
        max((ended - task.started_at).total_seconds(), 0.0)
        if task.started_at is not None
        else None
    )
    error = None
    if task.error_code or task.error_message:
        error = {"code": task.error_code, "message": task.error_message}
    return {
        "id": task.id,
        "pipeline_id": task.pipeline_id,
        "order_index": task.order_index,
        "type_name": task.type_name,
        "task_name": task.task_name,
        "params": task.params,
        "raw_params": task.raw_params,
        "status": str(task.status),
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "retry_delay": task.retry_delay,
        "maa_task_id": task.maa_task_id,
        "error": error,
        "created_at": _to_utc(task.created_at),
        "started_at": _to_utc(task.started_at),
        "finished_at": _to_utc(task.finished_at),
        "duration_seconds": duration,
    }


def _to_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
