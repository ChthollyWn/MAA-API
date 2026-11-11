"""维护性补丁：``model_profile`` 增加空闲时段价格三列（v3 specs/offpeak-pricing §4.1）。

峰谷计价需要给自定义模型档案分别记高峰价与空闲价（``docs/10`` §4、M67 的成本口径）：

- ``price_input_offpeak``：空闲时段输入价（元/百万 token）；
- ``price_cached_input_offpeak``：空闲时段缓存输入价；
- ``price_output_offpeak``：空闲时段输出价。

只加这三列，全部**可空、无默认值、不回填**：既有行保持 ``NULL``，语义是
「没有单独配置空闲价，回退高峰价」，由后续的计价逻辑兜底。既有 ``price_input`` /
``price_cached_input`` / ``price_output`` 三列不动，也不改写任何数据。

SQLite 3.35+ 支持原生 ``ADD COLUMN`` / ``DROP COLUMN``；这三列没有约束、索引或
触发器，直接走原生 ``ALTER TABLE``，不需要 batch 重建整表。``downgrade`` 反序
删列，只回退 schema，不恢复降级前写入的空闲价。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_offpeak_prices"
down_revision: Union[str, Sequence[str], None] = "0043_capability_insights"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 可空列、无默认值：SQLite 原生 ADD COLUMN 能直接做，不需要 batch 重建。
    op.add_column(
        "model_profile",
        sa.Column("price_input_offpeak", sa.Float(), nullable=True),
    )
    op.add_column(
        "model_profile",
        sa.Column("price_cached_input_offpeak", sa.Float(), nullable=True),
    )
    op.add_column(
        "model_profile",
        sa.Column("price_output_offpeak", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # 反序删列：都是可空、无索引的列，SQLite 原生 DROP COLUMN 即可。
    op.drop_column("model_profile", "price_output_offpeak")
    op.drop_column("model_profile", "price_cached_input_offpeak")
    op.drop_column("model_profile", "price_input_offpeak")
