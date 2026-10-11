"""账号干员数据（M27，规格 §7.3、§7.5）：读表、概要、手动编辑与识别差异。

这一层是账号干员数据的**唯一**读写入口：REST 路由、Agent 工具与注入段都从这里读，
识别回调（``services/account/capture.py``）也从这里写。编辑约束与 wire 形状在
:mod:`maa_api.services.account.rules` 里（纯函数，可脱离数据库验证），这里只做
三件事：

- **事务**：每个写方法自成一个事务（仓储不 commit）。
- **编辑**：:meth:`OperatorBoxService.patch_operator` 先用 ``rules`` 逐字段校验，
  再算跨字段约束（精英化与稀有度、专精与技能等级、模组与精英化），最后写回。
- **识别合并**：:meth:`OperatorBoxService.apply_recognition` 实现规格 §7.3 的覆盖与
  差异规则。识别只覆盖它能识别的四个字段（是否拥有、精英化、等级、潜能）；技能等级、
  专精、模组只有手动或 Agent 能写。被识别的值盖掉的手动值记一条差异，等用户在页面上
  「知道了」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.rules import (
    BOX_FIELDS,
    DETAIL_SCAN_FIELDS,
    EDITABLE_FIELDS,
    MASTERY_ELITE_REQUIRED,
    MAX_LEVEL,
    MAX_MASTERY_LEVEL,
    MAX_MODULE_LEVEL,
    MAX_POTENTIAL,
    MAX_SKILL_LEVEL,
    MODULE_TYPES,
    RECOGNITION_SOURCE,
    RECOGNIZED_FIELDS,
    WRITE_SOURCES,
    box_row_wire,
    diff_wire,
    elite_cap,
    json_mapping,
    field_sources_wire,
    iso_z,
    mastery_wire,
    module_wire,
    normalize_masteries,
    normalize_modules,
    optional_int,
    require_bool,
    require_int,
    skill_count,
    validation_error,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "BOX_FIELDS",
    "DETAIL_SCAN_FIELDS",
    "EDITABLE_FIELDS",
    "RECOGNIZED_FIELDS",
    "RECOGNITION_SOURCE",
    "WRITE_SOURCES",
    "OperatorBoxService",
    "box_row_wire",
    "diff_wire",
    "elite_cap",
    "skill_count",
]

class OperatorBoxService:
    """账号干员数据服务（规格 §7.3、§7.5）。

    :param session_factory: ``async_sessionmaker`` 或返回 ``AsyncSession`` 的可调用
        对象；每个方法自己开会话、自己定事务边界（仓储不 commit）。
    :param settings_provider: 现读配置，用于过期阈值 ``agent.box_stale_days``
        （注入替身可在测试里固定阈值）。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider

    # -- 概要 -------------------------------------------------------------
    async def summary(self) -> dict[str, Any]:
        """账号干员概要：总数、精二数、识别时间与是否过期、差异数（规格 §7.5）。

        「总数」只算 ``own`` 为真的行：识别用 ``all_opers`` 把未拥有的干员也落库，
        页面与注入段要的是「你有多少干员」，不是「游戏里有多少干员」。
        """
        operator_repo, diff_repo_class, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            total = await repo.count(owned_only=True)
            elite_two = await repo.count(owned_only=True, elite_min=2)
            recognized_at = await repo.last_recognized_at()
            pending_diffs = await diff_repo_class(session).count_pending()
        stale_days = int(self._settings_provider().agent.box_stale_days)
        return {
            "total": total,
            "elite_two": elite_two,
            "recognized_at": iso_z(recognized_at),
            "stale": _is_stale(recognized_at, stale_days),
            "stale_days": stale_days,
            "unrecognized": max(total - elite_two, 0),
            "pending_diffs": pending_diffs,
        }

    # -- 读 ---------------------------------------------------------------
    async def list_operators(
        self,
        *,
        query: str | None = None,
        owned_only: bool = True,
        rarity_min: int | None = None,
        profession: str | None = None,
        elite_min: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """干员列表，带筛选与 ``total``（规格 §7.5 与 Agent 工具的同一组参数）。

        ``limit`` / ``offset`` 由本层夹到合法区间（负数在 SQLite 上不报错、只静默
        返回错结果）；``total`` 是筛选后的总数，不是当前页条数。``owned_only=False``
        会把未拥有的干员也列出来（识别的 ``all_opers`` 落下的行）。
        """
        limit = max(int(limit), 1)
        offset = max(int(offset), 0)
        text = (query or "").strip() or None
        prof = (profession or "").strip() or None
        # 仓储的 ``owned_only`` 是三态（``None`` 不过滤 / ``True`` 只列拥有 / ``False``
        # 只列未拥有）；接口上的 ``owned_only=False`` 是「全都列出来」，所以这里映射成
        # ``None``。直接把 False 透下去会只返回未拥有的干员。
        ownership: bool | None = True if owned_only else None
        operator_repo, _diff_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            total = await repo.count(
                query=text,
                owned_only=ownership,
                rarity_min=rarity_min,
                profession=prof,
                elite_min=elite_min,
            )
            rows = await repo.list(
                query=text,
                owned_only=ownership,
                rarity_min=rarity_min,
                profession=prof,
                elite_min=elite_min,
                limit=limit,
                offset=offset,
            )
        return {
            "items": [box_row_wire(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_operator(self, char_id: str) -> dict[str, Any]:
        """单条干员；没有记录时 ``ACCOUNT_DATA_NOT_FOUND``（规格 §7.5）。"""
        operator_repo, _diff_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            row = await operator_repo(session).get(char_id)
        if row is None:
            raise AppError(
                ErrorCode.ACCOUNT_DATA_NOT_FOUND,
                "账号数据里没有这名干员的记录，请先运行一次干员识别",
                {"char_id": char_id},
            )
        return box_row_wire(row)

    # -- 写 ---------------------------------------------------------------
    async def patch_operator(
        self,
        char_id: str,
        payload: Mapping[str, Any],
        *,
        source: str = "manual",
    ) -> dict[str, Any]:
        """手动（或 Agent）编辑一名干员，逐字段校验后写回。

        ``payload`` 里没有的字段一律不动；每个改动过的字段在 ``field_sources`` 里标
        ``source``（``manual`` / ``agent``）。识别能覆盖的字段被识别盖回时来源会变回
        ``recognition``——这是规格 §7.3 的语义，不是丢失手动值。

        校验失败的字段抛 422 ``VALIDATION_ERROR``，``details.field`` 指出是哪一个。
        """
        if source not in WRITE_SOURCES:
            raise validation_error("source", "只接受 manual 或 agent", values=list(WRITE_SOURCES))
        unknown = sorted(set(payload) - set(EDITABLE_FIELDS))
        if unknown:
            raise validation_error(
                "payload", "包含不可编辑的字段", fields=unknown, editable=list(EDITABLE_FIELDS)
            )

        operator_repo, _diff_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            row = await repo.get(char_id)
            if row is None:
                raise AppError(
                    ErrorCode.ACCOUNT_DATA_NOT_FOUND,
                    "账号数据里没有这名干员的记录，请先运行一次干员识别",
                    {"char_id": char_id},
                )

            rarity = getattr(row, "rarity", None)
            cap = elite_cap(rarity)
            slots = skill_count(rarity)

            # 先算出这次编辑之后的生效值（``final``），同时把行里当前的值留在
            # ``before`` 里：只有真正变了的字段才写库、才改来源标记——页面把未修改的
            # 字段原样回传时不该把它标成「手动改过」。练度六列用 ``None`` 表示
            # **未识别**（M35 规格 §4.5）：``None → 值`` 算一次变化，而未知本身在
            # 约束层按「不知道」处理（见下）。
            before: dict[str, Any] = {
                "own": bool(getattr(row, "own", False)),
                "elite": optional_int(getattr(row, "elite", None)),
                "level": optional_int(getattr(row, "level", None)),
                "potential": optional_int(getattr(row, "potential", None)),
                "skill_level": optional_int(getattr(row, "skill_level", None)),
                "masteries": mastery_wire(getattr(row, "masteries", None)),
                "modules": module_wire(getattr(row, "modules", None)),
            }
            final = dict(before)
            changed: set[str] = set()

            if "own" in payload:
                final["own"] = require_bool("own", payload["own"])
                if final["own"] != before["own"]:
                    changed.add("own")
            if "elite" in payload:
                elite = require_int("elite", payload["elite"], minimum=0, maximum=2)
                if elite > cap:
                    raise validation_error(
                        "elite",
                        f"{rarity}★ 干员的精英化上限是 {cap}",
                        maximum=cap,
                        rarity=rarity,
                    )
                final["elite"] = elite
                if final["elite"] != before["elite"]:
                    changed.add("elite")
            if "level" in payload:
                final["level"] = require_int(
                    "level", payload["level"], minimum=0, maximum=MAX_LEVEL
                )
                if final["level"] != before["level"]:
                    changed.add("level")
            if "potential" in payload:
                final["potential"] = require_int(
                    "potential", payload["potential"], minimum=0, maximum=MAX_POTENTIAL
                )
                if final["potential"] != before["potential"]:
                    changed.add("potential")
            if "skill_level" in payload:
                final["skill_level"] = require_int(
                    "skill_level",
                    payload["skill_level"],
                    minimum=1,
                    maximum=MAX_SKILL_LEVEL,
                )
                if final["skill_level"] != before["skill_level"]:
                    changed.add("skill_level")
            if "masteries" in payload:
                final["masteries"] = normalize_masteries(
                    payload["masteries"], slots=slots
                )
                if final["masteries"] != before["masteries"]:
                    changed.add("masteries")
            if "modules" in payload:
                final["modules"] = normalize_modules(payload["modules"])
                if final["modules"] != before["modules"]:
                    changed.add("modules")

            # 精英化被显式调到精二以下时，专精与模组在语义上不再存在：先清空，
            # 再判跨字段约束——报错会让「降精英化」这个动作根本做不成（前端不会
            # 同时把 masteries 一起传上来）。未识别的列（``None``）保持未识别：
            # 「没识别过」不等于「有专精」，不需要清成一份假的「无专精」。
            if "elite" in payload and final["elite"] < MASTERY_ELITE_REQUIRED:
                if final["masteries"] is not None and any(final["masteries"].values()):
                    final["masteries"] = {"1": 0, "2": 0, "3": 0}
                if final["modules"]:
                    final["modules"] = {}
                for field in ("masteries", "modules"):
                    if final[field] != before[field]:
                        changed.add(field)

            # 跨字段约束按**生效值**判：只改模组而不提精英化时，用的是行里既有的
            # 精英化等级。约束**只在已知时判**（M35 规格 §4.5）：「精英化没识别过」
            # 证明不了专精非法，拦住用户补录才是不讲道理。两条依次是：专精要精二且
            # 技能 7、模组要精二。
            if final["masteries"] is not None and any(final["masteries"].values()):
                if final["elite"] is not None and final["elite"] < MASTERY_ELITE_REQUIRED:
                    raise validation_error(
                        "masteries",
                        "专精只在精二时可填",
                        elite_required=MASTERY_ELITE_REQUIRED,
                    )
                if (
                    final["skill_level"] is not None
                    and final["skill_level"] != MAX_SKILL_LEVEL
                ):
                    raise validation_error(
                        "masteries",
                        "专精要求技能等级为 7",
                        skill_level_required=MAX_SKILL_LEVEL,
                    )
            if (
                "skill_level" in payload
                and final["skill_level"] is not None
                and final["skill_level"] != MAX_SKILL_LEVEL
            ):
                # 把技能等级改到 7 以下会让既有的专精变成非法状态。
                if before["masteries"] is not None and any(before["masteries"].values()):
                    raise validation_error(
                        "skill_level",
                        "已填专精时技能等级必须保持 7",
                        skill_level_required=MAX_SKILL_LEVEL,
                    )
            if (
                final["modules"]
                and final["elite"] is not None
                and final["elite"] < MASTERY_ELITE_REQUIRED
            ):
                raise validation_error(
                    "modules",
                    "模组只在精二时可填",
                    elite_required=MASTERY_ELITE_REQUIRED,
                )

            for field in changed:
                setattr(row, field, final[field])
            sources = field_sources_wire(getattr(row, "field_sources", None))
            for field in changed:
                sources[field] = source
            row.field_sources = sources
            row.updated_at = _utcnow()
            await repo.upsert(row)
            await session.commit()
            return box_row_wire(row)

    # -- 差异 -------------------------------------------------------------
    async def list_diffs(
        self, *, include_dismissed: bool = False, limit: int = 200
    ) -> dict[str, Any]:
        """识别覆盖手动值的差异列表；顶部提示条用它（规格 §7.3）。"""
        limit = max(int(limit), 1)
        _operator_repo, diff_repo_class, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = diff_repo_class(session)
            rows = await repo.list(include_dismissed=include_dismissed, limit=limit)
            total = await repo.count_pending()
        return {"items": [diff_wire(row) for row in rows], "total": total}

    async def dismiss_diffs(self, ids: Sequence[int] | None = None) -> int:
        """把差异标记为已知：``ids`` 为 ``None`` 表示全部（规格 §7.5）。

        :returns: 本次真的改到的条数（重复标记的不计）。
        """
        selected = None if ids is None else [int(item) for item in ids]
        _operator_repo, diff_repo_class, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = diff_repo_class(session)
            count = await repo.dismiss(selected)
            await session.commit()
            return count

    # -- 识别结果（capture.py 调用）---------------------------------------
    async def apply_recognition(
        self,
        operators: Mapping[str, Mapping[str, Any]],
        *,
        all_operators: Sequence[str] | None = None,
        now: datetime | None = None,
        recognized_at: datetime | None = None,
        keep_owned: bool = False,
    ) -> dict[str, Any]:
        """把一次 ``OperBox`` 识别结果合并进表（规格 §7.3）。

        :param operators: ``char_id → {name, rarity, profession, own, elite, level,
            potential}``；识别只用到后四个（``own`` / ``elite`` / ``level`` /
            ``potential``），其余字段只在**新建行**时用来补全名称与稀有度。
        :param all_operators: 本次识别到「游戏里有这些干员」的全集（``all_opers``）。
            表里已有的行若在集合里但不在 ``operators`` 里（这次没识别到＝未拥有），
            把 ``own`` 置为 ``False``；不在集合里的一律不动——``all_opers`` 缺失
            （旧版内核）时不能把整表清成未拥有。
        :param now: 写库时间（默认当前 UTC 时间）。
        :param recognized_at: 识别时间（默认与 ``now`` 相同）；capture 层传回调时间。
            ``account_recognition.pipeline_id`` / ``task_id`` 有外键，capture 层要传
            真实存在的 id（或 ``None``），这里固定写 ``None``——本层拿不到调用上下文。
        :param keep_owned: 收缩保护（M33 规格 §4.3，用户裁决「识别只加不减」）：识别说
            「未拥有」而行里已经是已拥有时保留 ``True``，并把这些 ``char_id`` 记进
            返回值的 ``kept_owned``。内核漏行远比玩家真的失去干员常见，误清一次用户要
            重填整表；真要取消拥有走 ``update_operator_box`` / 手动编辑。保留的值按
            本次识别状态记进 ``recognized_values`` 与 ``field_sources``。
        :returns: ``{"created", "updated", "diffs", "kept_owned"}``（前三个是计数，
            ``kept_owned`` 是被收缩保护留下的 ``char_id`` 列表，按命中顺序、已去重），
            供日志与测试断言。
        """
        moment = now or _utcnow()
        stamp = recognized_at or moment
        missing = sorted(
            {
                char_id
                for char_id in (all_operators or ())
                if char_id not in operators
            }
        )
        created = updated = diffs = 0
        kept_owned: list[str] = []

        operator_repo, diff_repo_class, recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            diff_repo = diff_repo_class(session)
            # 只有 all_opers 说「有」、这次却没识别到的干员才补一条未拥有记录；
            # 两条来源都没有的 id 不凭空建行。
            targets = sorted(set(operators) | set(missing))
            for char_id in targets:
                incoming = operators.get(char_id)
                row = await repo.get(char_id)
                # 本次新建的行不算「库里已有的已拥有」：下面 ``new_row`` 的 ``own=True``
                # 只是占位（字段循环随后按识别值覆盖），收缩保护不能拿它当依据。
                is_new = row is None
                if row is None:
                    if incoming is None:
                        # 只有 all_opers 里的 id、没有任何字段值：留一条「未拥有」，
                        # 名称与稀有度后续识别或手动补。
                        row = repo.new_row(char_id, own=False)
                    else:
                        row = repo.new_row(
                            char_id,
                            name=_optional_text(incoming.get("name")),
                            rarity=_optional_int(incoming.get("rarity")),
                            profession=_optional_text(incoming.get("profession")),
                            own=True,
                        )
                    created += 1
                else:
                    updated += 1

                previous = json_mapping(getattr(row, "recognized_values", None))
                sources = field_sources_wire(getattr(row, "field_sources", None))
                values: dict[str, Any] = {}
                for field in RECOGNIZED_FIELDS:
                    # 这次没识别到的字段沿用行里既有的值；``own`` 特殊：所有权只认
                    # ``all_opers[].own``（``own_opers`` 只补练度详情，2026-10-10 实测
                    # 有 3 名已拥有却没有详情，见 docs/13 §8.48）。
                    if field == "own":
                        if incoming is not None and "own" in incoming:
                            recognized_own = _coerce_recognized(
                                "own", incoming.get("own")
                            )
                        elif incoming is None:
                            recognized_own = False
                        else:
                            recognized_own = None
                        if (
                            keep_owned
                            and not is_new
                            and recognized_own is False
                            and bool(getattr(row, "own", None))
                        ):
                            # 收缩保护（M33 规格 §4.3）：识别没看到≠不再拥有。
                            if char_id not in kept_owned:
                                kept_owned.append(char_id)
                            value: Any = True
                        elif recognized_own is None:
                            value = getattr(row, "own", None)
                        else:
                            value = recognized_own
                    elif incoming is not None and field in incoming:
                        value = _coerce_recognized(field, incoming.get(field))
                    else:
                        value = getattr(row, field, None)
                    values[field] = value

                    # 只有「上次识别之后被手动/Agent 改过、且这次识别值与它不同」才记
                    # 差异（规格 §7.3）。四个条件缺一不可：
                    # 1. 这条字段有上次识别的值（``previous``）——没有就说明还没识别过，
                    #    这次不是「覆盖」，不打扰用户；
                    # 2. 来源是 manual / Agent（识别自己写的字段被覆盖不算差异）；
                    # 3. 手动值与这次识别值不同（相同就没什么可提示的）；
                    # 4. 手动值与**上次识别值**也不同——用户只是把字段改回识别值时不提示。
                    manual_value = getattr(row, field, None)
                    if field not in previous:
                        continue
                    if sources.get(field) not in WRITE_SOURCES:
                        continue
                    if manual_value == value or manual_value == previous[field]:
                        continue
                    await diff_repo.create(
                        char_id=char_id,
                        field=field,
                        manual_value=manual_value,
                        recognized_value=value,
                        created_at=moment,
                    )
                    diffs += 1

                # 只有真的拿到值的字段才算「这次识别给出了值」：没给出值的字段保持
                # 未识别（M35：``None`` 就是 ``None``，不许落成 0 / 空映射），也不把
                # 它标成识别来源——来源台账与值必须一致。``own`` 例外：``all_opers``
                # 每次都给出它，没有未知态。
                recognized_values = {
                    field: value
                    for field, value in values.items()
                    if value is not None or field == "own"
                }
                for field, value in recognized_values.items():
                    setattr(row, field, value)
                    sources[field] = RECOGNITION_SOURCE
                row.recognized_values = recognized_values
                row.field_sources = sources
                row.recognized_at = stamp
                row.updated_at = moment
                await repo.upsert(row)

            await recognition_repo(session).record(
                kind="operbox",
                pipeline_id=None,
                task_id=None,
                client_type=None,
                item_count=len(operators),
                recognized_at=stamp,
            )
            await session.commit()

        return {
            "created": created,
            "updated": updated,
            "diffs": diffs,
            "kept_owned": kept_owned,
        }

    # -- 详情扫描（M36，scan.py 调用）-------------------------------------
    async def apply_detail_scan(
        self,
        char_id: str,
        *,
        skill_level: int | None = None,
        masteries: Mapping[Any, Any] | None = None,
        modules: Mapping[Any, Any] | None = None,
        recognized_at: datetime | None = None,
        source: str = RECOGNITION_SOURCE,
    ) -> dict[str, Any] | None:
        """把一次干员详情页的读数写进表（M36 规格 §4.4）。

        与 :meth:`apply_recognition` 的分工：``OperBox`` 回调给「是否拥有 / 精英化 /
        等级 / 潜能」，详情页给技能等级、专精、模组。这里只动
        :data:`DETAIL_SCAN_FIELDS` 三列，不碰另外四列——扫描不重读它们，也不该凭详情
        页的样子去改 ``OperBox`` 的结论。

        ``None`` 一律解释成「这次没读到」：不写，也不把既有值清成未识别。读到的
        **空映射**是另一回事：``modules={}`` 表示「看过了，没装模组」，要把 ``NULL``
        落成 ``{}``（M35 规格 §4.5 的「确认没有」与「不知道」是两回事）。

        ``masteries`` / ``modules`` 逐键合并，只动给出来的键。键或值越界（技能序号
        不在 1–3、专精不在 0–3、模组类型不在 X/Y/D/A、模组等级不在 1–3、技能等级不在
        1–7）时**不写也不抛**，字段名进 ``skipped``：详情页是游戏的事实，越界只可能是
        识别读花了，不该拿脏值覆盖库里的值。专精还要至少有一个合法槽位才算「读到了」
        ——详情页每次都给三个槽位，全是脏键或空映射只能说明这次读花了；模组的空映射
        例外，那是「看过了，没有模组」的正面读数。

        专精不按稀有度的技能数裁剪（``normalize_masteries`` 在手动编辑时会把多出来的
        槽位归零）：``rarity`` 可能是 ``NULL``，那时按默认技能数会把真读到的第 2、3
        技能专精一起丢掉，而详情页只显示这名干员真的有的技能。

        :param char_id: 干员 id。
        :param skill_level: 「RANK n」读到的技能等级（1–7）。
        :param masteries: ``{技能序号: 0–3}``。
        :param modules: ``{模组类型: 1–3}``；``{}`` 表示确认没装。
        :param recognized_at: 识别时间（默认当前 UTC 时间）。
        :param source: 写进 ``field_sources`` 的来源，默认 ``recognition``。
        :returns: ``{"char_id", "updated", "skipped"}``（两个列表都是 ``DETAIL_SCAN_FIELDS``
            里的字段名，已排序）；表里没有这名干员时回 ``None``——调用方计入
            ``mismatched``，这里不凭空建行。
        """
        moment = recognized_at or _utcnow()
        operator_repo, _diff_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            row = await repo.get(char_id)
            if row is None:
                return None

            updated: set[str] = set()
            skipped: set[str] = set()

            if skill_level is not None:
                level = optional_int(skill_level)
                if level is None or not 1 <= level <= MAX_SKILL_LEVEL:
                    skipped.add("skill_level")
                elif optional_int(getattr(row, "skill_level", None)) != level:
                    row.skill_level = level
                    updated.add("skill_level")

            if masteries is not None:
                if not isinstance(masteries, Mapping):
                    skipped.add("masteries")
                else:
                    before = mastery_wire(getattr(row, "masteries", None))
                    merged = dict(before or {"1": 0, "2": 0, "3": 0})
                    read = 0
                    for key, raw in masteries.items():
                        slot = optional_int(key)
                        level = optional_int(raw)
                        if (
                            slot not in (1, 2, 3)
                            or level is None
                            or not 0 <= level <= MAX_MASTERY_LEVEL
                        ):
                            skipped.add("masteries")
                            continue
                        read += 1
                        merged[str(slot)] = level
                    # 一个合法槽位都没读到就不写：详情页每次都给出三个槽位，空映射或全
                    # 是脏键只说明这次读花了，不能顺手把三槽写成「无专精」。
                    if read and merged != before:
                        row.masteries = merged
                        updated.add("masteries")

            if modules is not None:
                if not isinstance(modules, Mapping):
                    skipped.add("modules")
                else:
                    before_modules = module_wire(getattr(row, "modules", None))
                    merged_modules = dict(before_modules or {})
                    read = 0
                    for key, raw in modules.items():
                        module_type = str(key)
                        level = optional_int(raw)
                        if (
                            module_type not in MODULE_TYPES
                            or level is None
                            or not 1 <= level <= MAX_MODULE_LEVEL
                        ):
                            skipped.add("modules")
                            continue
                        read += 1
                        merged_modules[module_type] = level
                    # 空映射是一次正面读数（面板上确实没有模组）；全是脏键则同专精：
                    # 读花了就只记 ``skipped``，不把 ``NULL`` 说成「确认没装」。
                    if read or not modules:
                        if merged_modules != before_modules:
                            row.modules = merged_modules
                            updated.add("modules")

            if updated:
                values = dict(json_mapping(getattr(row, "recognized_values", None)))
                sources = field_sources_wire(getattr(row, "field_sources", None))
                for field in updated:
                    values[field] = getattr(row, field, None)
                    sources[field] = source
                row.recognized_values = values
                row.field_sources = sources
                row.updated_at = moment
                await repo.upsert(row)
                await session.commit()

        return {"char_id": char_id, "updated": sorted(updated), "skipped": sorted(skipped)}

    async def record_training_scan(
        self,
        *,
        updated: int,
        recognized_at: datetime | None = None,
        pipeline_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """写一条 ``training`` 识别记录（M36 规格 §4.5）。

        ``item_count`` 用**真的写进库的干员数**，不是名单长度：``account_recognition``
        记的是识别成果，扫到却没读到的干员不算。一次扫描只写一条——``apply_detail_scan``
        是逐人调用的，不能一人一条。
        """
        # 常量在仓储层（与 operbox / depot 并列）；本模块刻意不在模块级 import
        # ``db.repositories``（见 :func:`_repositories` 的说明）。
        from maa_api.db.repositories.account import RECOGNITION_KIND_TRAINING

        moment = recognized_at or _utcnow()
        _operator_repo, _diff_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            await recognition_repo(session).record(
                kind=RECOGNITION_KIND_TRAINING,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=None,
                item_count=int(updated),
                recognized_at=moment,
            )
            await session.commit()


def _repositories() -> tuple[Any, Any, Any]:
    """延迟取三个仓储类（import 放在调用处，模块级不 import ``db.repositories``）。

    这样服务层与 ORM、建表脚本解耦：``rules.py`` 与路由的请求模型可以在数据库就绪
    之前被单独引用与测试（Agent 工具、前端契约测试都要用同一份约束）。
    """
    from maa_api.db.repositories import account as repositories

    return (
        repositories.AccountOperatorRepository,
        repositories.AccountOperatorDiffRepository,
        repositories.AccountRecognitionRepository,
    )


def _utcnow() -> datetime:
    """库内时间源：naive UTC（与 ``db.models.utcnow`` 同口径）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _is_stale(recognized_at: datetime | None, stale_days: int) -> bool:
    """从未识别算过期（页面要提示「尚未识别」）；否则按天数判。"""
    if recognized_at is None:
        return True
    return recognized_at <= _utcnow() - timedelta(days=stale_days)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_recognized(field: str, value: Any) -> Any:
    """识别值归一：布尔按真假、整数按 int（内核回调里可能是字符串或浮点）。

    整数解析不出来时回 ``None``（＝这次没给出值），**不再折算成 0**：0 是「精零」
    这个具体事实，不能拿来接住脏数据（M35）。
    """
    if field == "own":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)
    return _optional_int(value)
