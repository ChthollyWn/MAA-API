"""M82 目标与里程碑仓储的行为（spec §4.1、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio）；
临时库由 ``tests/db/conftest.py`` 的 ``db_session_factory`` 建（NullPool +
``create_all``，外键约束打开）。

覆盖的硬约束：

- ``GoalRepository`` 的 ``create`` / ``get`` / ``update`` 只 ``flush``，不 commit；
  默认值（``status=active``、三个 JSON 列）来自模型；``page`` 按 ``updated_at``
  倒序且 ``total`` 与过滤条件一致；``list_by_status`` 取多状态。
- ``GoalMilestoneRepository.replace_all`` 是整体替换（旧行删掉、``goal_id`` 统一
  写入、``ord`` 由调用方决定），``list_by_goal`` 按 ``ord`` 升序；``set_status``
  按 ``(goal_id, ord)`` 条件更新，``ord`` 不存在返回 ``False``。
- 工单仓储的 ``list_by_goal`` 只取该目标的工单，``created_at`` 倒序且尊重 ``limit``。
"""

import asyncio
from datetime import datetime, timedelta

from maa_api.db.models import Goal, GoalMilestone, Ticket
from maa_api.db.repositories.goals import GoalMilestoneRepository, GoalRepository
from maa_api.db.repositories.ticket import TicketRepository


def _at(minutes: int) -> datetime:
    """固定的 naive UTC 时刻，避免用例依赖当前时间。"""
    return datetime(2026, 1, 1) + timedelta(minutes=minutes)


def test_goal_crud_page_and_list_by_status(db_session_factory) -> None:
    async def scenario() -> None:
        async with db_session_factory() as session:
            goals = GoalRepository(session)
            first = await goals.create(Goal(title="银灰精二", updated_at=_at(1)))
            second = await goals.create(
                Goal(title="能天使专三", status="paused", updated_at=_at(2))
            )
            third = await goals.create(
                Goal(title="旧目标", status="done", updated_at=_at(3))
            )
            await session.commit()

            # 模型默认值：status=active，target/schedule_ids/progress 都是空结构。
            assert first.id and len(first.id) == 36
            assert first.status == "active"
            assert first.target == {}
            assert first.schedule_ids == []
            assert first.progress == {}
            assert first.review_cron == "0 22 * * *"

            got = await goals.get(first.id)
            assert got is not None and got.title == "银灰精二"

            page = await goals.page(size=2)
            assert (page.total, page.page, page.size) == (3, 1, 2)
            assert [goal.id for goal in page.items] == [third.id, second.id]
            second_page = await goals.page(page=2, size=2)
            assert [goal.id for goal in second_page.items] == [first.id]

            paused = await goals.page(statuses=["paused"])
            assert [goal.id for goal in paused.items] == [second.id]
            assert paused.total == 1

            listed = await goals.list_by_status(["active", "paused"])
            assert {goal.id for goal in listed} == {first.id, second.id}

            # update 是全量替换：改完 commit 后 get 看到新值。
            got.title = "银灰精二专三"
            got.status = "done"
            await goals.update(got)
            await session.commit()
            reloaded = await goals.get(first.id)
            assert reloaded is not None
            assert (reloaded.title, reloaded.status) == ("银灰精二专三", "done")

    asyncio.run(scenario())


def test_milestones_replace_by_ord_and_set_status(db_session_factory) -> None:
    async def scenario() -> None:
        async with db_session_factory() as session:
            goal = await GoalRepository(session).create(Goal(title="银灰精二"))
            other = await GoalRepository(session).create(Goal(title="别的目标"))
            await session.commit()

            milestones = GoalMilestoneRepository(session)
            rows = [
                GoalMilestone(ord=3, title="专三", status="pending"),
                GoalMilestone(ord=1, title="精一", status="active"),
                GoalMilestone(ord=2, title="精二", status="pending"),
            ]
            stored = await milestones.replace_all(goal.id, rows)
            assert len(stored) == 3
            assert all(row.goal_id == goal.id and row.id for row in stored)
            await session.commit()

            listed = await milestones.list_by_goal(goal.id)
            assert [row.ord for row in listed] == [1, 2, 3]
            assert [row.title for row in listed] == ["精一", "精二", "专三"]
            assert await milestones.list_by_goal(other.id) == []

            assert await milestones.set_status(goal.id, 2, "done") is True
            await session.commit()
            assert (await milestones.list_by_goal(goal.id))[1].status == "done"
            # 不存在的 ord 与别家的 ord 都不改行。
            assert await milestones.set_status(goal.id, 9, "done") is False
            assert await milestones.set_status(other.id, 1, "done") is False

            # replace_all 是整体替换：旧三行消失，新行按新 ord 取。
            replaced = await milestones.replace_all(
                goal.id,
                [
                    GoalMilestone(ord=1, title="第一"),
                    GoalMilestone(ord=2, title="第二", status="skipped"),
                ],
            )
            await session.commit()
            assert {row.id for row in replaced}.isdisjoint(
                {row.id for row in stored}
            )
            reloaded = await milestones.list_by_goal(goal.id)
            assert [(row.ord, row.title, row.status) for row in reloaded] == [
                (1, "第一", "pending"),
                (2, "第二", "skipped"),
            ]

    asyncio.run(scenario())


def test_ticket_list_by_goal_orders_and_limits(db_session_factory) -> None:
    async def scenario() -> None:
        async with db_session_factory() as session:
            goal = await GoalRepository(session).create(Goal(title="银灰精二"))
            other_goal = await GoalRepository(session).create(Goal(title="别的目标"))
            session.add_all(
                [
                    Ticket(
                        title="规划：银灰精二",
                        kind="planner",
                        origin="goal",
                        goal_id=goal.id,
                        created_at=_at(1),
                    ),
                    Ticket(
                        title="复盘：银灰精二",
                        kind="planner",
                        origin="goal",
                        goal_id=goal.id,
                        created_at=_at(3),
                    ),
                    Ticket(
                        title="复盘：第二次",
                        kind="planner",
                        origin="goal",
                        goal_id=goal.id,
                        created_at=_at(2),
                    ),
                    Ticket(
                        title="别的目标的工单",
                        kind="planner",
                        origin="goal",
                        goal_id=other_goal.id,
                        created_at=_at(4),
                    ),
                    Ticket(title="用户工单", kind="daily_ops", origin="user"),
                ]
            )
            await session.commit()

            tickets = TicketRepository(session)
            listed = await tickets.list_by_goal(goal.id)
            assert [ticket.title for ticket in listed] == [
                "复盘：银灰精二",
                "复盘：第二次",
                "规划：银灰精二",
            ]
            limited = await tickets.list_by_goal(goal.id, limit=2)
            assert [ticket.title for ticket in limited] == [
                "复盘：银灰精二",
                "复盘：第二次",
            ]
            assert await tickets.list_by_goal("missing-goal") == []

    asyncio.run(scenario())
