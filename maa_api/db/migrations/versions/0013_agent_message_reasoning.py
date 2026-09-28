"""Persist the assistant ``reasoning_content`` for thinking-mode models.

``deepseek-flash`` 与 ``deepseek-v4-pro`` 都是思考模式：流式响应里带
``delta.reasoning_content``，而**历史里凡是后面不跟 user 消息的 assistant 消息
都必须把它原样回传**，否则上游直接拒绝：

    The `reasoning_content` in the thinking mode must be passed back to the API.

自动诊断每一轮的历史都以工具结果结尾，正好落在这个条件里，于是整个回合失败、
只留下一句「LLM 上游请求失败」。此前代码里没有这个字段，所以思考内容在流式解析
时就被丢掉了，无法回传。

列可为空：既有行没有这个值，非思考模型也不会产出它。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_agent_message_reasoning"
down_revision: Union[str, Sequence[str], None] = "0012_push_subscription"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 思考内容通常比正文长，用 TEXT（与 content 同口径），不放 VARCHAR。
    op.add_column(
        "agent_message",
        sa.Column("reasoning_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_message", "reasoning_content")
