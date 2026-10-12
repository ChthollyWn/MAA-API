"""Add the two M42 Skland tables and ``account_operator.favor_percent`` (spec §4.4).

M42 把干员练度的数据源从「内核读屏扫描」换成森空岛账号数据（用户 m00077）。本迁移只做
**结构**：两张森空岛表 + 一列新增，不碰任何既有列，也不回填（旧库里的练度列保持
``NULL`` ＝ 未识别，等第一次同步写）。

- ``skland_credential``：**单行表**，主键固定 1。``cred`` 与签名密钥都存 Fernet 密文
  （密钥取配置 ``skland.credential_key``），明文只允许出现在内存与签名函数里；换绑就是
  覆盖这一行，因此不保留历史行。
- ``skland_role``：森空岛账号绑定的游戏角色，``uid`` 主键。整表替换语义——``binding``
  接口返回的就是全集，多角色时同步只取 ``is_default`` 的那个，其余行留着供页面展示。
- ``account_operator.favor_percent``：信赖百分比 0–200。它是唯一一个**内核识别给不出、
  只有森空岛能给**的练度列，因此可空，与练度六列同一口径（``NULL`` ＝ 未知，不回填）。

两张表都不建索引：单行表 + 个位数的角色表，与 M27 的 ``account_operator`` /
``account_depot_item`` 同一个判断（随全表读 + 内存筛选，docs/04 §6）。

downgrade 只删结构：新加的列与两张表都是森空岛数据的快照，删掉即回到「没绑定过」，
没有别的表引用它们。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_skland_account"
down_revision: Union[str, Sequence[str], None] = "0018_unknown_training"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skland_credential",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cred_encrypted", sa.Text(), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("bound_at", sa.DateTime(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_skland_credential"),
    )
    op.create_table(
        "skland_role",
        sa.Column("uid", sa.String(length=64), nullable=False),
        sa.Column("app_code", sa.String(length=32), nullable=False),
        sa.Column("channel_master_id", sa.String(length=16), nullable=True),
        sa.Column("nickname", sa.String(length=64), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("uid", name="pk_skland_role"),
    )
    # 可空列、无默认值：SQLite 的 ADD COLUMN 能直接做，不需要 batch 重建整表。
    op.add_column(
        "account_operator",
        sa.Column("favor_percent", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("account_operator") as batch_op:
        batch_op.drop_column("favor_percent")
    op.drop_table("skland_role")
    op.drop_table("skland_credential")
