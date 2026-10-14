"""M47：清空 ``account_operator`` —— 干员数据只留森空岛同步这一条来源。

背景（M47 规格 §2、§3 D3）：``account_operator`` 历史上由三个写入者写——内核
``OperBox`` 识别、森空岛练度同步、手动 / Agent 补录。用户 2026-10-14 裁决「干员识别任务
去掉、干员数据只能来源于森空岛同步、旧数据清掉等重新同步」，于是 OperBox 任务类型、
采集链路、手动编辑与 Agent 补录整块下线（docs/13 §8.70），库里由这几条通道写出来的行
也不该再留在页面上——它们没法自证是森空岛给的。

**这是一次不可逆的数据删除**，``downgrade`` 只写说明、不重建任何行：

- 被删的数据不是「唯一副本」：绑定森空岛后跑一次 ``sync_skland_training``，同一批干员
  会带着 ``field_sources = skland`` 重新落库（``apply_skland_sync``）。
- 没绑定森空岛的用户在重新绑定并同步之前，干员页就是空的——这正是这次裁决想要的
  语义（页面不再展示来路不明的旧数据），页面上有绑定卡片与空状态引导。

保留不动的两处历史：

- ``account_operator_diff``：差异表只在「识别覆盖手改值」时产生过行；写入者已经没了，
  表与历史行保留（``docs/04`` §5.19 记「不再产生新行」）。
- ``account_recognition`` 里 ``kind = 'operbox'`` 的旧记录：识别审计历史，保留。

不动的结构：``account_operator`` 的列一个不改（``field_sources`` / ``recognized_values``
仍是数据来源的台账），只删行。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0022_skland_only_operators"
down_revision: Union[str, Sequence[str], None] = "0021_depot_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """清空干员表。没有外键指向它（``account_operator_diff.char_id`` 只是字符串列、
    ``upgrade_plan`` 的目标干员存在 JSON 里），所以直接删即可。"""
    op.execute("DELETE FROM account_operator")


def downgrade() -> None:
    """**不做任何事**：删掉的行没有副本，唯一的重建路径是用户在森空岛同步一次。

    写成一个显式的 no-op（而不是 ``pass`` 里留空），是为了让读到这里的人知道
    「降级不会把数据变回来」不是遗漏。
    """
    op.execute("SELECT 1")
