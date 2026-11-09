"""维护性补丁：提问不再有有效期，``agent_question.expires_at`` 改为历史列。

提问的时间失效整体删除（v3 specs/questions-no-expiry §4.4）：新提问不写
``expires_at``，服务也不再按它判过期。列本身保留用于回放旧数据，因此只放宽
可空约束，不删列、不清理历史值。

- ``upgrade``：``batch_alter_table`` 把 ``expires_at`` 改为可空（SQLite 需要整表
  重建，用 batch 模式）。
- ``downgrade``：先加回可空列、用 ``created_at`` 回填，再恢复非空约束；旧提问的
  原始过期时刻无法恢复，降级后统一等于创建时间。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_question_expiry_historical"
down_revision: Union[str, Sequence[str], None] = "0039_drop_spend_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_question") as batch_op:
        batch_op.alter_column(
            "expires_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )


def downgrade() -> None:
    # 升级只放宽了可空约束、没有删列；降级要先回填再在同一次 batch 重建里恢复非空。
    op.execute(
        "UPDATE agent_question SET expires_at = created_at WHERE expires_at IS NULL"
    )
    with op.batch_alter_table("agent_question") as batch_op:
        batch_op.alter_column(
            "expires_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
