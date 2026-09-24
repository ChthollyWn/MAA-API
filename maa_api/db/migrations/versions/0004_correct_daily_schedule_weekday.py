"""Correct the weekday convention used by the legacy daily-task migration.

Revision ID: 0004_fix_schedule_weekday
Revises: 0003_pipeline_device_deferral
Create Date: 2026-09-24

The old executor interpreted ``weekday_task`` keys as Python ``date.weekday()``
values (Monday=0). Revision 0002 copied those values directly into a POSIX cron
field (Sunday=0), shifting migrated schedules by one day. This forward-only data
repair changes only untouched, known-default migrated rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_fix_schedule_weekday"
down_revision: Union[str, Sequence[str], None] = "0003_pipeline_device_deferral"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAME_PREFIX = "daily_task_weekday_"


def _schedule_table() -> sa.TableClause:
    return sa.table(
        "schedule",
        sa.column("name", sa.String),
        sa.column("cron", sa.String),
    )


def _posix_weekday(legacy_weekday: int) -> int:
    """Translate Python Monday-zero weekday to POSIX Sunday-zero weekday."""
    return (legacy_weekday + 1) % 7


def upgrade() -> None:
    """Shift only unedited rows created with the exact 0002 default cron."""
    table = _schedule_table()
    bind = op.get_bind()

    for legacy_weekday in range(7):
        name = f"{NAME_PREFIX}{legacy_weekday}"
        old_cron = f"0 7,19 * * {legacy_weekday}"
        corrected_cron = f"0 7,19 * * {_posix_weekday(legacy_weekday)}"
        bind.execute(
            sa.update(table)
            .where(sa.and_(table.c.name == name, table.c.cron == old_cron))
            .values(cron=corrected_cron)
        )


def downgrade() -> None:
    """Restore only rows still carrying the exact corrected default cron."""
    table = _schedule_table()
    bind = op.get_bind()

    for legacy_weekday in range(7):
        name = f"{NAME_PREFIX}{legacy_weekday}"
        corrected_cron = f"0 7,19 * * {_posix_weekday(legacy_weekday)}"
        old_cron = f"0 7,19 * * {legacy_weekday}"
        bind.execute(
            sa.update(table)
            .where(sa.and_(table.c.name == name, table.c.cron == corrected_cron))
            .values(cron=old_cron)
        )
