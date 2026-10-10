"""M33：把本机内核日志里的**真实** ``OperBoxInfo`` 载荷重放进识别链路（规格 §4.2/§4.3）。

2026-10-10 那次真机「干员识别」在 ``resource/maa-api/debug/asst.log`` 里留下了三类载荷，
本文件逐条重放（不打真机、不写仓库的 ``resource/``）：

1. 17:20 收尾那次：``all_opers`` 463 条里 ``own=true`` 是 **383** 条（用户实际拥有数），
   而 ``own_opers`` 只有 **380** 条——差的 3 名（``char_002_amiya`` / ``char_1037_amiya3`` /
   ``char_512_aprot``）已拥有却没有练度详情。旧口径数 ``own_opers``，日志才会说
   「识别到 380 名干员」（用户报告的问题①），并且那次识别根本没有落库（问题②）。
2. 16:33 收尾那次：``own=true`` 只有 13 条（游戏刚启动，识别残缺）。若直接落库，会把
   370 名已拥有干员冲成未拥有——M33 的「识别只加不减」必须拦下，且不伪造识别记录。
3. 16:26–16:29 那四次：``own=true`` 0 条（游戏没启动，内核照样 ``done=true``）。
   M33 的空识别保护必须整次跳过。

没有日志（CI、新机器）时整个文件 ``skip``：这里的数字是**真机历史事实**，
把它们钉成回归网只有在本机跑才有意义。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from maa_api.core.enums import Message
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_OPERBOX,
    AccountOperatorRepository,
    AccountRecognitionRepository,
)
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.capture import AccountCaptureService
from maa_api.services.callback_translator import CallbackTranslator

REPO_ROOT = Path(__file__).resolve().parents[2]
ASST_LOG = REPO_ROOT / "resource" / "maa-api" / "debug" / "asst.log"

RECOGNIZED_AT = datetime(2026, 10, 10, 9, 20, 0)  # 本机 17:20 收尾那次（库里存 UTC）
LATER = datetime(2026, 10, 10, 9, 40, 0)

#: 17:20 那次识别：all_opers 463 条、own=true 383 名、own_opers 只有 380 名。
REAL_OWNED = 383
REAL_OWN_OPERS = 380
#: 16:33 那次残识别少了多少名（＝收缩保护要保留的量）。
REAL_KEPT = 370
#: 已拥有却没有练度详情的那 3 名（只在 ``all_opers`` 里 own=true、不在 ``own_opers`` 里）。
DETAIL_LESS = {"char_002_amiya", "char_1037_amiya3", "char_512_aprot"}

_CACHE: list[dict[str, Any]] | None = None


def _real_operbox_envelopes() -> list[dict[str, Any]]:
    """从内核 ``asst.log`` 里抽出全部真实 ``OperBoxInfo`` 回调载荷（按出现顺序）。"""

    global _CACHE
    if _CACHE is not None:
        return _CACHE
    _CACHE = []
    if not ASST_LOG.is_file():
        return _CACHE
    decoder = json.JSONDecoder()
    with ASST_LOG.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "OperBoxInfo" not in line:
                continue
            marker = line.find("SubTaskExtraInfo")
            start = line.find("{", marker if marker >= 0 else 0)
            if start < 0:
                continue
            try:
                payload, _end = decoder.raw_decode(line, start)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("what") == "OperBoxInfo":
                _CACHE.append(payload)
    return _CACHE


def _inner(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = envelope.get("details")
    return inner if isinstance(inner, Mapping) else {}


def _all_items(envelope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = _inner(envelope).get("all_opers")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _owned_ids(envelope: Mapping[str, Any]) -> set[str]:
    """``all_opers`` 里 ``own=true`` 的 id（＝这次识别认到的「已拥有」全集）。"""

    return {
        str(item["id"])
        for item in _all_items(envelope)
        if item.get("id") and item.get("own") is True
    }


def _own_opers_ids(envelope: Mapping[str, Any]) -> set[str]:
    """``own_opers`` 里的 id（＝带练度详情的那批，旧口径只数它）。"""

    items = _inner(envelope).get("own_opers")
    if not isinstance(items, list):
        return set()
    return {
        str(item["id"])
        for item in items
        if isinstance(item, Mapping) and item.get("id")
    }


def _pick(envelopes: list[dict[str, Any]], *, owned: int) -> dict[str, Any]:
    for envelope in reversed(envelopes):
        if len(_owned_ids(envelope)) == owned:
            return envelope
    pytest.skip(f"本机日志里没有「已拥有 {owned} 名」的识别载荷")


def test_real_operbox_replay_counts_every_owned_operator_and_guards_the_partial_ones(
    retention_session_factory,
) -> None:
    envelopes = _real_operbox_envelopes()
    if not envelopes:
        pytest.skip("本机没有 resource/maa-api/debug/asst.log 里的 OperBoxInfo 回调")

    full = _pick(envelopes, owned=REAL_OWNED)
    partial = _pick(envelopes, owned=13)
    blank = _pick(envelopes, owned=0)

    full_owned = _owned_ids(full)
    partial_owned = _owned_ids(partial)
    assert len(full_owned) == REAL_OWNED
    assert len(_own_opers_ids(full)) == REAL_OWN_OPERS
    assert full_owned - _own_opers_ids(full) == DETAIL_LESS
    assert partial_owned <= full_owned, "残识别认到的干员必须是收尾那批的子集"
    assert len(full_owned - partial_owned) == REAL_KEPT

    async def scenario() -> None:
        capture = AccountCaptureService(retention_session_factory)
        box = OperatorBoxService(retention_session_factory)

        # ① 日志口径：收尾那次应报 383 名（旧实现报 380，＝own_opers 的条数）。
        contents = [
            record.content
            for record in CallbackTranslator().translate(Message.SubTaskExtraInfo, full)
        ]
        assert contents == ["识别到 383 名干员"]

        # ② 落库：账号数据更新到 383 名已拥有，且没有触发收缩保护。
        result = await capture.handle_callback(
            full,
            message=Message.SubTaskExtraInfo,
            created_at=RECOGNIZED_AT,
            # 不造流水线行：识别记录对 pipeline_id / task_id 有外键（接线测试在
            # tests/services/test_account_capture_wiring.py）。这里只重放计数与护栏。
            client_type="Bilibili",
        )
        assert result.written is True
        assert (result.item_count, result.kept_count, result.skipped) == (
            REAL_OWNED,
            0,
            None,
        )
        summary = await box.summary()
        assert summary["total"] == REAL_OWNED
        assert summary["recognized_at"] is not None

        # 3 名没有练度详情、但确实已拥有——旧实现把它们落成「未拥有」。
        # （练度列非空且有默认值，没有详情的行记 0/0/0；这是 M27 就有的表述方式，
        # 不是 M33 的范围，这里只钉「已拥有」这一条。）
        async with retention_session_factory() as session:
            operators = AccountOperatorRepository(session)
            for char_id in sorted(DETAIL_LESS):
                row = await operators.get(char_id)
                assert row is not None and row.own is True, char_id
                assert row.recognized_values.get("own") is True, char_id

        # ③ 残缺载荷（own=true 13 条）：识别只加不减，370 名照旧是已拥有。
        shrunk = await capture.handle_callback(
            partial, message=Message.SubTaskExtraInfo, created_at=LATER
        )
        assert shrunk.written is True
        assert (shrunk.item_count, shrunk.kept_count) == (13, REAL_KEPT)
        assert (await box.summary())["total"] == REAL_OWNED
        async with retention_session_factory() as session:
            latest = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_OPERBOX
            )
            # 识别记录照实写「这次只认到 13 名」，护栏不伪造识别结果。
            assert latest is not None and latest.item_count == 13

        # ④ 空载荷（own=true 0 条）：整次跳过，连识别记录都不新增。
        blanked = await capture.handle_callback(
            blank, message=Message.SubTaskExtraInfo, created_at=LATER
        )
        assert (blanked.written, blanked.skipped) == (False, "no_owned_operators")
        assert (await box.summary())["total"] == REAL_OWNED
        async with retention_session_factory() as session:
            latest = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_OPERBOX
            )
            assert latest is not None and latest.item_count == 13

    asyncio.run(scenario())
