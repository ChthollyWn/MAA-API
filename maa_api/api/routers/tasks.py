"""任务类型 schema 导出与参数校验（docs/05 §6.7、§8、§9）。

三个端点共用同一份判别联合 :data:`~maa_api.domain.task.TaskInput`：

- ``GET /api/tasks/types``：9 种任务类型的完整参数 schema，供前端动态生成表单、
  agent 了解可用参数；
- ``GET /api/tasks/types/{type_name}``：单个类型的 schema；
- ``POST /api/tasks/validate``：只校验不提交，返回规范化后的参数与将被注入的默认值。

前两者输出的是 pydantic 直出的**标准 JSON Schema**：中文标签、分组、控件提示等
元信息一律挂在 ``x-*`` 前缀的自定义关键字上（JSON Schema 规范要求校验器忽略未知
关键字，所以 schema 仍可喂给任何标准校验器），取值范围用 ``minimum`` /
``maximum`` / ``enum`` / ``minItems`` / ``maxItems`` 等原生关键字，跨字段依赖用
``x-depends-on``（docs/05 §8.2、docs/09 §7.2）。

渠道默认值的来源
================

``POST /validate`` 走与提交路径**同一个** :func:`~maa_api.domain.task.normalize`
做「校验之后、落库之前」的注入（docs/05 §9）。:func:`load_channel_defaults` 按
setting 仓储读 ``channel.client_type`` / ``channel.server`` 两个 key，读不到
（或值非法）时回退 :class:`~maa_api.domain.task.ChannelDefaults` 的默认值
（Bilibili / CN）。

**这里刻意不做进程内缓存、不监听设置变更**：带缓存与失效的 SettingService 属 M6，
届时用 SettingService 替换 :func:`load_channel_defaults` 这一处读取即可，本模块
其余代码不需要动。每次请求两个主键点查的代价可以接受。

路由声明顺序
============

``/api/tasks/types`` 必须声明在 ``/api/tasks/{task_id}`` **之前**，否则 ``types``
会被当成一个 task_id 走进详情端点（docs/05 §6.5，与 ``/api/pipelines/current``
同理）。``GET /api/tasks/{task_id}`` 属 M5，本模块不提前实现 —— 将来加它时，
请把它声明在 ``/types`` 与 ``/types/{type_name}`` 之后。

鉴权
====

三个端点都是业务端点，不在 docs/05 §5.3 的豁免清单里，统一由 router 级的
``Depends(require_auth)`` 保护（``/api/tasks/...`` 不以 ``/api/system/health`` 等
豁免前缀开头）。
"""

from __future__ import annotations

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
    TaskInput,
    TaskInputBase,
    normalize,
)
from maa_api.services.task_defaults import load_channel_defaults

__all__ = [
    "BASE_GROUP",
    "TASK_MODELS",
    "TaskValidateRequest",
    "_collect_groups",
    "export_type_schema",
    "load_channel_defaults",
    "router",
]

#: 没有 ``x-group`` 的字段归入的分区名。与 :func:`~maa_api.domain.task.MaaField`
#: 的 ``group`` 默认值一致，因此 9 个任务模型里的字段实际上都带 ``x-group``，
#: 只有将来新增的、忘了标注的字段才会走到这个兜底。
BASE_GROUP = "基础"

#: ``name`` → 模型：按 docs/05 §8.1 的写法直接从判别联合推导，保证「联合是唯一真源」。
#: 顺序即 ``TaskInput`` 的定义顺序（docs/05 §7.4），也是 /types 的清单顺序。
TASK_MODELS: dict[str, type[TaskInputBase]] = {
    model.model_fields["name"].default: model
    for model in get_args(get_args(TaskInput)[0])
}

def _collect_groups(schema: Mapping[str, Any]) -> list[str]:
    """按字段声明顺序收集 ``x-group``，去重后返回（顺序即前端渲染顺序）。

    docs/09 §7.2 的契约：``groups`` 是有序的中文字符串数组，直接作为折叠面板的
    分区标题，前端不要重排；没有 ``x-group`` 的字段归入兜底分区
    :data:`BASE_GROUP`。9 个模型的首字段 ``name`` 一定带 ``x-group: 基础``，
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


def export_type_schema(model: type[BaseModel]) -> dict[str, Any]:
    """导出一个任务类型的对外 schema（docs/05 §8.1）。

    :param model: 9 个 ``TaskInputBase`` 子类之一。
    :returns: ``{name, label, description, schema, groups, runtime_immutable}``；
        ``schema`` 是 ``model_json_schema(ref_template="#/$defs/{model}")`` 的原样
        输出（``x-*`` 关键字保留，``$defs`` 里的模型引用指向 ``#/$defs/<Name>``，
        方便前端在同一份文档内解析引用）。
    """
    name = model.model_fields["name"].default
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
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

    有意不复用 :class:`~maa_api.domain.task.PipelineCreate`：校验端点不建流水线，
    ``title`` / ``priority`` / ``notify_on_finish`` 在这里没有意义，接受它们只会
    让调用方误以为「校验通过」等于「提交参数合法」。
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
        "返回全部 9 种任务类型的完整参数 schema，供前端动态生成表单、"
        "agent 了解可用参数。\n\n"
        "- 只读端点，**无副作用**\n"
        "- `schema` 是 pydantic 直出的标准 JSON Schema；中文标签、分组、控件提示"
        "与风险标记挂在 `x-label` / `x-group` / `x-widget` / `x-enum-labels` / "
        "`x-risk` / `x-depends-on` 上，取值范围走 `minimum` / `maximum` / `enum` "
        "等原生关键字\n"
        "- 清单固定 9 条，响应固定 `total=9, page=1, size=9`（不接受分页参数）\n"
        "- `lang` 当前仅支持 `zh`，其它值返回 400 `INVALID_PARAMETER`\n"
    ),
    responses=error_responses("INVALID_PARAMETER", "UNAUTHORIZED"),
)
async def list_types(
    lang: Annotated[str, Query(description="中文标签语言；当前仅支持 zh")] = "zh",
) -> dict[str, Any]:
    """9 种任务类型的 schema 清单（docs/05 §6.7、§8.1）。"""
    if lang != "zh":
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"暂不支持的语言：{lang}；当前仅支持 zh",
            {"lang": lang, "supported": ["zh"]},
        )
    items = [export_type_schema(model) for model in TASK_MODELS.values()]
    return {"items": items, "total": len(items), "page": 1, "size": len(items)}


@router.get(
    "/types/{type_name}",
    summary="获取单个任务类型 schema",
    description=(
        "按类型名返回单个任务类型的 schema，结构与 `GET /api/tasks/types` 的 "
        "`items` 元素一致（不套分页信封）。\n\n"
        "- 只读端点，**无副作用**\n"
        "- `type_name` 不在 9 种任务类型内时返回 400 `UNKNOWN_TASK_TYPE`"
        "（错误码表里它就是 400，不是 404）\n"
    ),
    responses=error_responses("UNKNOWN_TASK_TYPE", "UNAUTHORIZED"),
)
async def get_type(type_name: str) -> dict[str, Any]:
    """单个任务类型的 schema（docs/05 §6.7）。"""
    model = TASK_MODELS.get(type_name)
    if model is None:
        raise AppError(
            ErrorCode.UNKNOWN_TASK_TYPE,
            f"未知的任务类型：{type_name}",
            {"type_name": type_name, "expected": list(TASK_MODELS)},
        )
    return export_type_schema(model)


@router.post(
    "/validate",
    summary="校验任务参数",
    description=(
        "只校验不提交：对任务数组跑完整的 pydantic 判别联合校验，并按全局渠道"
        "默认值规范化，返回投递给内核的最终参数与用户原始提交。\n\n"
        "- **无副作用**：不落库、不入队、不碰内核；只读 setting 表\n"
        "- 未显式指定 `client_type` / `server` 的任务会注入 setting 表里的全局"
        "默认值（默认 Bilibili / CN）；显式传 `null` 视为刻意留空，不注入\n"
        "- `params` 是投递给内核的形态（已剔除 null、已注入默认值），`raw_params` "
        "是原始提交快照（保留显式 null），可用于审计与回显\n"
        "- 任务数 1..32，越界返回 422 `VALIDATION_ERROR`\n"
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
    """校验并规范化任务数组（docs/05 §6.7、§9）。

    注入只发生在 :func:`~maa_api.domain.task.normalize` 里；本函数只负责取渠道
    默认值，不自己填任何字段，否则 ``model_fields_set`` 与 ``raw_params`` 会失真。
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
            "任务不存在",
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
