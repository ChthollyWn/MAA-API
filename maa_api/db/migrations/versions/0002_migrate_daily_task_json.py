"""0002 数据迁移：``resource/daily_task.json`` 逐日展开为 ``schedule`` 记录。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16

依据 docs/04 §5.5 与 §11：旧配置的 ``weekday_task`` 把星期几映射到 ``task_dict``
里的一个任务组名，本迁移逐条展开成独立 ``schedule`` 记录 —— 例如
``{"1": "workday"}`` 配合固定的 7 点与 19 点，生成 ``cron="0 7,19 * * 1"``、
``template`` 取 ``task_dict["workday"]``。

六条纪律：

1. **只写数据，不改表结构**。``name`` UNIQUE、``timezone`` 默认 ``Asia/Shanghai``、
   ``priority`` 默认 ``Priority.SCHEDULED``、``last_pipeline_id`` 外键这些约束都属于
   0001（M2-04）；这里只 INSERT/DELETE 行。
2. **``template`` 逐字存 ``task_dict[group]``**（``json.dumps(..., ensure_ascii=False)``），
   不做任何预处理。定时触发因此与前端手动提交走完全相同的校验与默认值注入路径，
   不会出现「手动能跑、定时报错」的分裂（docs/04 §5.5）。
3. **文件不存在则跳过**，不抛错：迁移跑在应用启动路径上（docs/04 §8.3），用户删掉
   旧配置不应阻塞启动。迁移后原文件保留不删，作为回滚依据（docs/04 §11）。
4. **可 ``upgrade → downgrade → upgrade``**：``upgrade`` 先查同名记录、已存在就不插，
   ``downgrade`` 按 :data:`NAME_PREFIX` 前缀精确删除本迁移插入的行，不碰用户自建的
   schedule，重复升级也不会撞 ``uq_schedule_name``。
5. **名字稳定**：``name = f"{NAME_PREFIX}{weekday}"``；``id`` 由 name 派生的 UUID5
   而不是随机 UUID4 —— 离线 SQL 可复现，降级后重放拿到同一批 id。
6. **离线模式**（``alembic upgrade head --sql``）：读表做幂等检查这一步跳过（mock
   connection 的 ``execute()`` 返回 ``None``，拿不到结果），直接渲染 INSERT。所有值都
   内联在 Core 语句里、不通过 ``execute(params=...)`` 传参 —— 离线模式会丢弃执行参数
   （``MockConnection`` 的 executor 只收 construct），传参会让 SQL 渲染成占位符。

可测性：源文件路径默认 ``<repo>/resource/daily_task.json``，可用 Alembic 配置项
``daily_task_json``（``Config.set_main_option``）覆盖；测试据此指向临时文件，
绝不改动仓库里的真实配置。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence, Union

import sqlalchemy as sa
from alembic import op

from maa_api.domain.enums import Priority

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SOURCE_PATH = REPO_ROOT / "resource" / "daily_task.json"
#: Alembic 配置项名（alembic.ini 的 [alembic] 段或 set_main_option）。
SOURCE_PATH_OPTION = "daily_task_json"

#: 本迁移插入行的 name 前缀，downgrade 据此精确删除。
NAME_PREFIX = "daily_task_weekday_"
#: 每天 7 点与 19 点（现有配置的固定触发时间，docs/04 §5.5 示例）。
CRON_HOURS = "7,19"
#: schedule 表没有 server_default 的 NOT NULL 列，必须显式写入（模型默认值，docs/04 §5.5）。
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MISFIRE_GRACE_SECONDS = 300
DEFAULT_CATCH_UP = 0
DEFAULT_SKIP_IF_RUNNING = 1


def source_path() -> Path:
    """源文件路径：``daily_task_json`` 配置项优先，缺省用仓库根下的真实配置。"""
    configured = op.get_context().config.get_main_option(SOURCE_PATH_OPTION)
    if configured:
        return Path(configured)
    return DEFAULT_SOURCE_PATH


def schedule_table() -> sa.TableClause:
    """轻量 ``table()`` 构造：只列本迁移写入/查询的列，其余可空列留 NULL。

    不用反射（``autoload_with``）：数据迁移不该在离线模式下访问数据库，且列清单
    显式写出来正好是「本迁移依赖的 0001 列」的自文档。
    """
    return sa.table(
        "schedule",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("cron", sa.String),
        sa.column("timezone", sa.String),
        sa.column("enabled", sa.Integer),
        sa.column("template", sa.String),
        sa.column("priority", sa.Integer),
        sa.column("misfire_grace_seconds", sa.Integer),
        sa.column("catch_up", sa.Integer),
        sa.column("skip_if_running", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )


def schedule_id(name: str) -> str:
    """name → 确定性 UUID5，保证离线 SQL 与重放拿到同一批 id。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"maa-api/schedule/{name}"))


def iter_rows(raw: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """把配置里的 ``weekday_task`` 展开成 schedule 行（Core insert 用的 dict）。

    ``weekday_task`` 的键是 cron 的星期字段（``0`` = 周日），值是 ``task_dict`` 的
    组名；组不存在或不是数组时只告警跳过这一天，不影响其余星期与启动流程。
    """
    task_dict = raw.get("task_dict") or {}
    weekday_task = raw.get("weekday_task") or {}
    enabled = 1 if raw.get("enable", True) else 0
    now = datetime.now(UTC).replace(tzinfo=None)

    for weekday, group in weekday_task.items():
        tasks = task_dict.get(group)
        if not isinstance(tasks, list):
            logger.warning(
                "daily_task.json: weekday %s 指向的任务组 %r 不存在或不是数组，已跳过",
                weekday,
                group,
            )
            continue
        name = f"{NAME_PREFIX}{weekday}"
        yield {
            "id": schedule_id(name),
            "name": name,
            "cron": f"0 {CRON_HOURS} * * {weekday}",
            "timezone": DEFAULT_TIMEZONE,
            "enabled": enabled,
            "template": json.dumps(tasks, ensure_ascii=False),
            "priority": int(Priority.SCHEDULED),
            "misfire_grace_seconds": DEFAULT_MISFIRE_GRACE_SECONDS,
            "catch_up": DEFAULT_CATCH_UP,
            "skip_if_running": DEFAULT_SKIP_IF_RUNNING,
            "created_at": now,
            "updated_at": now,
        }


def upgrade() -> None:
    """按 weekday 逐条插入 schedule；源文件不存在则跳过。"""
    path = source_path()
    if not path.is_file():
        logger.info("daily_task.json 不在 %s，跳过 0002 数据迁移", path)
        return

    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = list(iter_rows(raw))
    if not rows:
        return

    table = schedule_table()
    bind = op.get_bind()
    if not op.get_context().as_sql:
        # 幂等：同名记录已存在（手工建过、或迁移被重放）就不再插入 —— 既避开
        # uq_schedule_name，也不覆盖用户数据。离线模式拿不到查询结果，跳过此检查。
        existing = {
            row[0]
            for row in bind.execute(
                sa.select(table.c.name).where(
                    table.c.name.in_([row["name"] for row in rows])
                )
            )
        }
        rows = [row for row in rows if row["name"] not in existing]

    for row in rows:
        bind.execute(sa.insert(table).values(**row))


def downgrade() -> None:
    """删除本迁移插入的行；按 name 前缀精确匹配，不碰用户自建的 schedule。"""
    table = schedule_table()
    op.get_bind().execute(
        sa.delete(table).where(
            sa.func.substr(table.c.name, 1, len(NAME_PREFIX)) == NAME_PREFIX
        )
    )
