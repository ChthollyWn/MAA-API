"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# 必须 import sqlmodel：SQLModel 的 Field(max_length=...) 在元数据里是
# sqlmodel.sql.sqltypes.AutoString，autogenerate 会按「模块.类」渲染，
# 但**不会**自动补 import，漏了它生成文件一执行就 NameError（M2-01 实测）。
import sqlmodel  # noqa: F401
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
