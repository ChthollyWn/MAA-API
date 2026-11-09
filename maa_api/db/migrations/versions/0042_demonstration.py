"""M89：示教录制表 ``demonstration``（v3 specs/m89 §4.1、docs/v3/03 §12）。

- 纯新增，不回填：七列逐列照 spec §4.1。
- ``id`` 是 36 字符 UUID 主键，主键约束显式命名 ``pk_demonstration``。
- ``status`` 只存 ``recording`` / ``recorded`` / ``converted`` / ``discarded``
  四个字符串（枚举在 ``maa_api/domain/enums.py``）。
- ``steps`` 是非空 JSON 数组，元素是扁平对象；``converted_to`` 可空，注册成功后
  写自定义任务链记录。元素形状由服务层定义，表不做约束。
- 不建索引、不做数据迁移（03 §12 不列索引）；``downgrade`` 直接删表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_demonstration"
down_revision: Union[str, Sequence[str], None] = "0041_nav_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demonstration",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("converted_to", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_demonstration"),
    )


def downgrade() -> None:
    op.drop_table("demonstration")
