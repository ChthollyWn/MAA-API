"""M45 W3a：仓库识别记「这一行是谁给的」（``account_depot_item.sources`` 等四列改动）。

M45 的仓库识别是**两路合并**：内核 ``Depot`` 那一趟（95 个材料 + 5 个硬编码基础物品）
照跑照收，M45 的自识别把内核白名单外的道具补成差集（规格 §4.5）。于是「这一行是谁认出来
的」变成了必须能回答的问题 —— 页面要显示「未识别」而不是 0，排障要知道某一行的来源。
M45 W3a 之前的表结构答不出来：``count`` 非空（0 既表示「没有」也表示「不知道」），
也没有任何列记来源。

四处改动，都是**可空、不回填**（与 docs/13 §8.61 的口径一致：不知道就只能是不认识，
不许拿默认值冒充事实）：

- ``account_depot_item.count`` 改为可空：自识别只保证身份、数量要等 W3b 的 OCR，
  这时写 ``NULL``；旧行的整数原样保留（内核给过数量的那些行本来就是确定值）。
- ``account_depot_item.sources``（JSON，可空）：``{item_id: "kernel" | "self_scan"}``。
- ``account_recognition.detail``（JSON，可空）：这次识别的过程事实
  （来源表 + 未识别清单 + 逐屏计数），让「未识别」在**接口层**可见。
- 无索引：与 M27 建表时的判断一致（行数上限就是物品种类数）。

**为什么放宽 ``count`` 要重建整表**：SQLite 不支持直接改列约束，只能 batch 重建 +
搬运数据。batch 模式默认要**连库反射**表结构，而 ``alembic upgrade --sql``（离线渲染，
``tests/db/test_unknown_training_migration.py`` 盯的就是这条路）没有连接可用 ⇒ 显式传
``copy_from``（本迁移自己声明的表对象，字段与模型一致）让两种模式都能跑。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_depot_sources"
down_revision: Union[str, Sequence[str], None] = "0020_operator_card_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _depot_table(*, with_sources: bool = False) -> sa.Table:
    """``account_depot_item`` 的**本次迁移视角**定义（字段与 ``db/models.py`` 一致）。

    只用于 batch 重建时的 ``copy_from``：离线 ``--sql`` 模式下没有连接可反射，
    alembic 需要一份表结构才能生成搬运 SQL，而且这份结构必须与 **batch 开始时**的
    表一致（少了列会在搬运时 ``KeyError``）。所以 ``with_sources`` 按步骤给：

    - upgrade 的 batch 在 ``add_column("sources")`` 之后 ⇒ ``with_sources=True``；
    - downgrade 的 batch 在删列之前 ⇒ 也是 ``with_sources=True``（此时列还在）。
    """
    metadata = sa.MetaData()
    columns = [
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(16), nullable=True),
        sa.Column("recognized_at", sa.DateTime(), nullable=False),
    ]
    if with_sources:
        columns.append(sa.Column("sources", sa.JSON(), nullable=True))
    return sa.Table("account_depot_item", metadata, *columns)


def _recognition_table(*, with_detail: bool = False) -> sa.Table:
    """``account_recognition`` 的本次迁移视角定义（同上）。"""
    metadata = sa.MetaData()
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("pipeline_id", sa.String(36), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("client_type", sa.String(16), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("recognized_at", sa.DateTime(), nullable=False),
    ]
    if with_detail:
        columns.append(sa.Column("detail", sa.JSON(), nullable=True))
    table = sa.Table("account_recognition", metadata, *columns)
    # 索引也要声明：batch 重建会按 ``copy_from`` 重建表上的索引，漏掉它就会在
    # 重建后去 drop 一个不存在的索引（``DROP INDEX ix_account_recognition_...`` 报错）。
    sa.Index("ix_account_recognition_kind_recognized_at", table.c.kind, table.c.recognized_at)
    return table


def upgrade() -> None:
    op.add_column(
        "account_depot_item",
        sa.Column("sources", sa.JSON(), nullable=True),
    )
    op.add_column(
        "account_recognition",
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    # SQLite 改列约束要重建表；batch 模式在搬运时保留既有的 count 值。
    with op.batch_alter_table(
        "account_depot_item", copy_from=_depot_table(with_sources=True)
    ) as batch_op:
        batch_op.alter_column(
            "count",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # 旧版本把 NULL 当 0 读，所以降级把「未识别」折算成 0 —— 这正是本迁移要区分的那
    # 两件事，降级会重新混在一起（可接受：旧代码只有这一种表达方式）。
    op.execute("UPDATE account_depot_item SET count = 0 WHERE count IS NULL")
    with op.batch_alter_table(
        "account_depot_item", copy_from=_depot_table(with_sources=True)
    ) as batch_op:
        batch_op.alter_column(
            "count",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.drop_column("sources")
    with op.batch_alter_table(
        "account_recognition", copy_from=_recognition_table(with_detail=True)
    ) as batch_op:
        batch_op.drop_column("detail")
