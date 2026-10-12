"""Add ``account_operator.skill_ids`` and ``module_details`` (M43 spec §4.1).

M43 把账号页的干员从表格行换成卡片网格（用户 2026-10-12 的要求）。卡片要画两样现在的
契约给不出的东西：

- **技能图标**：URL 是 ``char_skill/{skill_id}.png``，需要技能 id，而技能 id 只有森空岛
  的 ``chars[].skills[].id`` 给得出来（内核 ``OperBox`` 与手动编辑都没有）⇒ 新列
  ``skill_ids``（按槽位顺序的字符串数组，可空）。
- **模组标签**：卡片上要显示 ``CCR-X`` 这类「分支码-类型字母」，它来自森空岛
  ``equipmentInfoMap[id].typeIcon``（``ccr-y``）。``modules`` 只存 ``{"Y": 3}``，丢掉
  了分支码 ⇒ 新列 ``module_details``（``[{type, level, equip_id, branch, label,
  type_icon}]``，可空）。

两列都**可空且不回填**：老库里的行、以及只由 ``OperBox`` 识别或手动编辑写过的行保持
``NULL`` ＝ 未知（docs/13 §8.61 的口径：不知道就只能是不认识，不许拿默认值冒充事实）。
``modules`` 的语义与 ``PATCH`` 校验不动，计划层继续用它。

downgrade 只删这两列：它们都是森空岛同步的快照，删掉即回到「只有模块类型没有分支码」，
没有别的表或计算依赖它们。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_operator_card_fields"
down_revision: Union[str, Sequence[str], None] = "0019_skland_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 可空列、无默认值：SQLite 的 ADD COLUMN 能直接做，不需要 batch 重建整表。
    op.add_column(
        "account_operator",
        sa.Column("skill_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "account_operator",
        sa.Column("module_details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("account_operator") as batch_op:
        batch_op.drop_column("module_details")
        batch_op.drop_column("skill_ids")
