"""Make the six M27 training columns nullable: NULL means "not recognized" (M35).

背景（M35 规格 §2）：内核 OperBox 只给得出 ``own`` 与 ``elite`` / ``level`` /
``potential``，技能等级、专精、模组本来就不在识别结果里（``RECOGNIZED_FIELDS``）。
可 0016 把六列全建成 ``NOT NULL`` 带默认值（``elite/level/potential`` 为 0、
``skill_level`` 为 1、``masteries`` 为三个 0 槽、``modules`` 为 ``{}``），于是
「没识别过」被表达成「精零 1 级、技能 1 级、无专精、无模组」——Agent 在会话里就是
这么回答的（M35 需求来源）。本迁移只改列的**可空性**并把历史默认值回填成 NULL，
不改任何识别/写入逻辑。

两段升级：

1. ``batch_alter_table`` 把 ``elite`` / ``level`` / ``potential`` / ``skill_level`` /
   ``masteries`` / ``modules`` 六列改成可空。SQLite 没有 ``ALTER COLUMN``，只能重建表；
   这里显式给 ``copy_from``（按 0016 的列定义 + 命名主键）而不是反射，免得重建后的
   约束名与 0016 漂移。
2. 回填（口径见规格 §4.2，SQL 里就是「台账里找不到依据就置 NULL」）：

   - ``elite`` / ``level`` / ``potential``：``field_sources[field]`` 是 ``manual`` /
     ``agent``（人写过），或 ``recognized_values`` 里含该键（识别确实写过）→ 保留原值；
     否则置 NULL。真机库（384 行、识别来源）预期只剩 381 行有这三列——M33 台账里
     3 名「已拥有但没有详情」的干员因此不再落成 0/0/0。
   - ``skill_level`` / ``masteries`` / ``modules``：只有 ``manual`` / ``agent`` 才保留，
     其余置 NULL（识别永不写这三列）。

   六条 ``UPDATE`` 全部用纯 SQL 表达，因此在线升级与 ``upgrade head --sql``
   离线渲染走同一条路径（逐行 SELECT 判定在离线模式拿不到结果）。

``field_sources`` / ``recognized_values`` 不动：它们本来就是诚实的来源台账。

downgrade 恢复六列 ``NOT NULL`` 并把 NULL 填回 0016 的默认值（0/0/0/1/全 0/{}）——
「未识别」这个信息在旧模型里没有位置，回退必然丢它，所以 downgrade 只保证旧代码能跑。
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_unknown_training"
down_revision: Union[str, Sequence[str], None] = "0017_copilot_grant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 识别能覆盖的三个练度列：``recognized_values`` 里有记录就等于识别写过。
_RECOGNIZED_TRAINING: tuple[str, ...] = ("elite", "level", "potential")
#: 识别永远不写、只有人工/Agent 才能给出的三列。
_MANUAL_ONLY_TRAINING: tuple[str, ...] = ("skill_level", "masteries", "modules")
#: 六列的类型（batch 重建时 ``existing_type`` 要准；JSON 列不能报成 Integer）。
_COLUMN_TYPES: dict[str, sa.types.TypeEngine[Any]] = {
    "elite": sa.Integer(),
    "level": sa.Integer(),
    "potential": sa.Integer(),
    "skill_level": sa.Integer(),
    "masteries": sa.JSON(),
    "modules": sa.JSON(),
}
_AGENT_SOURCES = ("manual", "agent")
#: 嵌进 SQL 的取值清单，与 :data:`_AGENT_SOURCES` 同一份来源。
_AGENT_SOURCE_LIST = ", ".join(f"'{source}'" for source in _AGENT_SOURCES)


def _account_operator_table() -> sa.Table:
    """0016 的 ``account_operator``（列定义不变，供 batch 重建使用）。"""
    return sa.Table(
        "account_operator",
        sa.MetaData(),
        sa.Column("char_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("rarity", sa.Integer(), nullable=True),
        sa.Column("profession", sa.String(length=16), nullable=True),
        sa.Column("own", sa.Boolean(), nullable=False),
        sa.Column("elite", sa.Integer(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("potential", sa.Integer(), nullable=True),
        sa.Column("skill_level", sa.Integer(), nullable=True),
        sa.Column("masteries", sa.JSON(), nullable=True),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("recognized_values", sa.JSON(), nullable=True),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("recognized_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("char_id", name="pk_account_operator"),
    )


def _json_field(column: str, field: str) -> str:
    """``json_extract`` 的安全形式：NULL / 坏 JSON / 非对象一律当空映射。

    SQLite 的 ``OR`` 不短路（``json_valid(...) = 0`` 为真时右侧的 ``json_extract``
    仍会被求值，坏 JSON 直接抛 ``malformed JSON``），所以先把原文折成合法 JSON。
    """
    return (
        f"json_extract(CASE WHEN json_valid({column}) THEN {column}"
        f" ELSE '{{}}' END, '$.{field}')"
    )


def _unknown_where(field: str) -> str:
    """该列「没有台账依据」的 WHERE 子句，对应规格 §4.2 的判定。"""
    source = _json_field("field_sources", field)
    where = f"({source} IS NULL OR {source} NOT IN ({_AGENT_SOURCE_LIST}))"
    if field in _RECOGNIZED_TRAINING:
        where += f" AND {_json_field('recognized_values', field)} IS NULL"
    return where


def upgrade() -> None:
    with op.batch_alter_table(
        "account_operator", copy_from=_account_operator_table()
    ) as batch_op:
        for column in (*_RECOGNIZED_TRAINING, *_MANUAL_ONLY_TRAINING):
            batch_op.alter_column(
                column, existing_type=_COLUMN_TYPES[column], nullable=True
            )

    # 回填写成六条纯 SQL（一列一条），而不是先 SELECT 再逐行改：离线
    # ``upgrade head --sql`` 里没有真连接，读不出行（0002 的先例是跳过读），
    # 这样在线离线同一条路径，渲染出来的 SQL 也能直接看。
    for field in (*_RECOGNIZED_TRAINING, *_MANUAL_ONLY_TRAINING):
        op.execute(
            sa.text(
                f"UPDATE account_operator SET {field} = NULL"
                f" WHERE {_unknown_where(field)}"
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE account_operator SET"
            " elite = COALESCE(elite, 0),"
            " level = COALESCE(level, 0),"
            " potential = COALESCE(potential, 0),"
            " skill_level = COALESCE(skill_level, 1),"
            " masteries = COALESCE(masteries, '{\"1\": 0, \"2\": 0, \"3\": 0}'),"
            " modules = COALESCE(modules, '{}')"
        )
    )
    legacy = _account_operator_table()
    with op.batch_alter_table("account_operator", copy_from=legacy) as batch_op:
        batch_op.alter_column("elite", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("level", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("potential", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("skill_level", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("masteries", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("modules", existing_type=sa.JSON(), nullable=False)
