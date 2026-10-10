"""M28 卡池领域层（规格 §8.2、§4.3）：SMW 取数、时间筛选与 UP 星级区分。

不打网（规格 §4.9）：

- SMW 响应读 ``tests/fixtures/prts/ask_gacha_pools.json``（2026-10-10 的真实响应裁到
  最近 6 条，来源与抓取日期见同目录 README.md），经 ``httpx.MockTransport`` 分发；
- UP 干员的星级读本机 ``battle_data.json``（规格 §8.2：星级是稳定事实，标
  ``source=resource``）；单测里注入 ``tmp_path`` 现造的一份，另有一条用例对着**真**
  本机资源跑（没有就跳过）。

「现在」由 ``now_provider`` 注入固定值（2026-10-10 12:00 +08），否则夹具里的开闭时间会
跟着真实时间漂。异步用例用 ``asyncio.run`` 跑（本仓库不装 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import PrtsClient
from maa_api.services.game.activities import FOREIGN_REGION_NOTE
from maa_api.services.game.gacha import (
    GACHA_CONDITION,
    GACHA_PRINTOUTS,
    MAX_LIMIT,
    SMW_LIMIT,
    GachaService,
    default_gacha_service,
)
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, SourcesSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"

#: 夹具时间轴上的一刻：2026-10-10 12:00 +08（夹具抓到的那天）。
NOW = datetime.fromtimestamp(1791604800, tz=timezone.utc).astimezone()

#: 夹具里在开的 4 个卡池（按开启时间倒序）。
CURRENT_NAMES = ["海渊巡游", "常驻标准寻访", "常驻中坚寻访", "定向甄选08"]

#: 夹具里已关闭的两个卡池（按开启时间倒序）。
RECENT_NAMES = ["常驻标准寻访", "常驻中坚寻访"]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _settings() -> Settings:
    """外部来源测试设置：不走代理、令牌桶给足（沿用 M25/M26 用例的写法）。"""
    return Settings(sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120))


def _dispatcher(routes: dict[str, Any], calls: list[httpx.Request] | None = None):
    """按 `action` 分发夹具；没安排的端点回 404（能立刻看出领域层多发了请求）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        params = dict(request.url.params)
        route = routes.get(params.get("action", ""))
        if route is None:
            return httpx.Response(404, content=b"{}")
        if callable(route):
            route = route(params)
        return httpx.Response(
            200, content=json.dumps(route, ensure_ascii=False).encode("utf-8")
        )

    return handler


def make_prts(routes: dict[str, Any], *, calls: list[httpx.Request] | None = None) -> PrtsClient:
    external = ExternalClient(
        transport=httpx.MockTransport(_dispatcher(routes, calls=calls)),
        settings_provider=_settings,
    )
    return PrtsClient(external)


class UnavailablePrts:
    """假客户端：任何取数都抛 `EXTERNAL_SOURCE_UNAVAILABLE`（规格 §3.5 的降级入口）。"""

    async def ask(self, *args: Any, **kwargs: Any) -> Any:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "PRTS 不可用（假客户端）", {"kind": "prts"}
        )

    def smw_pages(self, result: Any) -> list[Any]:
        return []


def write_battle_data(tmp_path: Path, chars: dict[str, Any]) -> Path:
    """一份最小的 `battle_data.json`：``{名字: {"rarity": n}}``。"""
    path = tmp_path / "battle_data.json"
    path.write_text(
        json.dumps(
            {
                "chars": {
                    f"char_{index:03d}": {"name": name, **raw}
                    for index, (name, raw) in enumerate(chars.items(), start=1)
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def make_service(
    routes: dict[str, Any] | None = None,
    *,
    prts: Any = None,
    battle_data_path: Path | None = None,
    now: datetime = NOW,
    calls: list[httpx.Request] | None = None,
    settings: Settings | None = None,
) -> GachaService:
    """`settings` 缺省是国服（`Bilibili`），让用例不依赖仓库里的 config.yaml。"""
    return GachaService(
        prts=prts if prts is not None else make_prts(routes or {}, calls=calls),
        battle_data_path=battle_data_path,
        now_provider=lambda: now,
        settings_provider=(lambda: settings) if settings is not None else _settings,
    )


def smw_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """一份最小的 `action=ask` 响应：``rows`` 每项是 ``{title, name, start, end, up}``。"""
    results: dict[str, Any] = {}
    for row in rows:
        results[row["title"]] = {
            "printouts": {
                "寻访名cn": [row["name"]],
                "寻访开启时间cn": [{"timestamp": str(row["start"]), "raw": ""}],
                "寻访关闭时间cn": [{"timestamp": str(row["end"]), "raw": ""}],
                "出率提升干员": list(row.get("up") or []),
            },
            "fulltext": row["title"],
            "fullurl": row.get("fullurl"),
        }
    return {"query": {"printouts": [], "results": results}}


def real_battle_data() -> Path:
    """本机资源层里的 `battle_data.json`；没有就跳过（星级只能来自本机资源）。"""
    for root in iter_resource_roots():
        path = Path(root) / "battle_data.json"
        if path.is_file():
            return path
    pytest.skip("本机没有 battle_data.json 资源层（规格 §8.2 的星级区分取本机资源）")


# --------------------------------------------------------------------------- #
# 冻结的取值与 SMW 查询形状（规格 §8.2）
# --------------------------------------------------------------------------- #
def test_gacha_constants_match_the_spec() -> None:
    assert GACHA_CONDITION == "[[寻访开启时间cn::+]]"
    assert GACHA_PRINTOUTS == ("寻访名cn", "寻访开启时间cn", "寻访关闭时间cn", "出率提升干员")
    assert SMW_LIMIT == 20  # 最近 20 个卡池（≤ 500，SMW 的 limit 上限）
    assert MAX_LIMIT == 10


def test_pools_ask_request_carries_the_condition_printouts_sort_and_limit() -> None:
    calls: list[httpx.Request] = []
    service = make_service({"ask": fixture("ask_gacha_pools.json")}, calls=calls)

    asyncio.run(service.pools(status="current", limit=5))

    params = dict(calls[0].url.params)
    assert params["action"] == "ask"
    query = params["query"]
    assert query.startswith("[[寻访开启时间cn::+]]")
    for printout in GACHA_PRINTOUTS:
        assert f"|?{printout}" in query
    assert "|sort=寻访开启时间cn|order=desc" in query
    assert f"|limit={SMW_LIMIT}" in query


# --------------------------------------------------------------------------- #
# 时间筛选
# --------------------------------------------------------------------------- #
def test_pools_sends_the_spec_query_and_filters_to_current(tmp_path) -> None:
    service = make_service(
        {"ask": fixture("ask_gacha_pools.json")},
        battle_data_path=write_battle_data(
            tmp_path, {"克莱门莎": {"rarity": 6}, "火哨": {"rarity": 5}}
        ),
    )

    result = asyncio.run(service.pools(status="current", limit=5))

    assert result["query"] == {"status": "current", "limit": 5}
    assert [pool["name"] for pool in result["pools"]] == CURRENT_NAMES
    assert result["pools_total"] == len(CURRENT_NAMES)
    assert all(pool["status"] == "current" for pool in result["pools"])
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    assert result["sources"] and result["sources"][0]["kind"] == "prts"
    assert result["region_note"] is None


def test_pools_current_keeps_open_pools_and_drops_closed_ones() -> None:
    service = make_service({"ask": fixture("ask_gacha_pools.json")})

    result = asyncio.run(service.pools(status="current", limit=10))

    names = [pool["name"] for pool in result["pools"]]
    assert names == CURRENT_NAMES
    page = result["pools"][0]
    assert page["page"] == "文件:海渊巡游.jpg"
    assert page["start_at"] == datetime.fromtimestamp(
        1791518400, tz=timezone.utc
    ).astimezone().isoformat()
    assert page["end_at"] == datetime.fromtimestamp(
        1792699140, tz=timezone.utc
    ).astimezone().isoformat()
    # 倒序：后开的排在前面。
    starts = [pool["start_at"] for pool in result["pools"]]
    assert starts == sorted(starts, reverse=True)


def test_pools_recent_lists_closed_pools_in_descending_order() -> None:
    service = make_service({"ask": fixture("ask_gacha_pools.json")})

    result = asyncio.run(service.pools(status="recent", limit=1))

    assert result["query"] == {"status": "recent", "limit": 1}
    assert len(result["pools"]) == 1
    pool = result["pools"][0]
    assert pool["name"] == "常驻标准寻访"
    assert pool["page"] == "文件:干员轮换卡池194.jpg"
    assert pool["status"] == "recent"
    assert pool["end_at"] < NOW.isoformat()
    # `pools_total` 是本状态下命中的全部条数，不受 `limit` 影响（规格 §4.4）。
    assert result["pools_total"] == len(RECENT_NAMES)


def test_pools_limit_caps_the_list_but_not_the_total() -> None:
    service = make_service({"ask": fixture("ask_gacha_pools.json")})

    result = asyncio.run(service.pools(status="current", limit=2))

    assert [pool["name"] for pool in result["pools"]] == CURRENT_NAMES[:2]
    assert result["pools_total"] == 4


def test_pools_boundary_start_is_open_and_end_is_closed(tmp_path) -> None:
    """开始 ≤ 现在 < 关闭：开始那一刻算在开，关闭那一刻算已关闭。"""

    start = int(NOW.timestamp())
    end = start + 3600
    payload = smw_payload(
        [
            {"title": "文件:刚开.jpg", "name": "刚开", "start": start, "end": end, "up": []},
            {"title": "文件:刚关.jpg", "name": "刚关", "start": start - 7200, "end": start, "up": []},
        ]
    )
    service = make_service({"ask": payload})

    current = asyncio.run(service.pools(status="current", limit=5))
    recent = asyncio.run(service.pools(status="recent", limit=5))

    assert [pool["name"] for pool in current["pools"]] == ["刚开"]
    assert [pool["name"] for pool in recent["pools"]] == ["刚关"]


# --------------------------------------------------------------------------- #
# UP 干员：星级来自本机 battle_data.json
# --------------------------------------------------------------------------- #
def test_pools_split_up_operators_by_local_rarity(tmp_path) -> None:
    service = make_service(
        {"ask": fixture("ask_gacha_pools.json")},
        battle_data_path=write_battle_data(
            tmp_path,
            {
                "克莱门莎": {"rarity": 6},
                "火哨": {"rarity": 5},
                "德·托莱多": {"rarity": 5},
                "斥罪": {"rarity": 6},
                "左乐": {"rarity": 6},
                "和弦": {"rarity": 5},
            },
        ),
    )

    result = asyncio.run(service.pools(status="current", limit=5))

    first = result["pools"][0]
    assert first["up_six"] == [
        {"name": "克莱门莎", "rarity": 6, "source": "resource"},
    ]
    assert first["up_five"] == [
        {"name": "火哨", "rarity": 5, "source": "resource"},
        {"name": "德·托莱多", "rarity": 5, "source": "resource"},
    ]
    # 页面上的顺序照原样保留（6★ 在前是数据本身的顺序）。
    assert [item["name"] for item in first["up_five"]] == ["火哨", "德·托莱多"]


def test_up_operators_missing_from_the_local_resource_keep_the_name_with_null_rarity(
    tmp_path,
) -> None:
    payload = smw_payload(
        [
            {
                "title": "文件:测试卡池.jpg",
                "name": "测试卡池",
                "start": 1791000000,
                "end": 1792000000,
                "up": ["存在于本地的6星", "存在于本地的5星", "本机没有的干员"],
            }
        ]
    )
    service = make_service(
        {"ask": payload},
        battle_data_path=write_battle_data(
            tmp_path, {"存在于本地的6星": {"rarity": 6}, "存在于本地的5星": {"rarity": 5}}
        ),
    )

    result = asyncio.run(service.pools(status="current", limit=5))

    pool = result["pools"][0]
    assert pool["up_six"] == [
        {"name": "存在于本地的6星", "rarity": 6, "source": "resource"},
    ]
    # 查不到的干员原样保留、星级为 null，也不冒充来源。
    assert pool["up_five"] == [
        {"name": "存在于本地的5星", "rarity": 5, "source": "resource"},
        {"name": "本机没有的干员", "rarity": None, "source": None},
    ]


def test_up_rarity_resolves_against_the_real_battle_data() -> None:
    """对着真本机 `battle_data.json` 的口径：UP 干员的星级落在 5★/6★。"""

    service = make_service(
        {"ask": fixture("ask_gacha_pools.json")}, battle_data_path=real_battle_data()
    )

    result = asyncio.run(service.pools(status="current", limit=5))

    stars = {
        item["name"]: item["rarity"]
        for pool in result["pools"]
        for group in ("up_six", "up_five")
        for item in pool[group]
    }
    assert stars
    assert set(stars.values()) <= {5, 6}
    # 实测（2026-10-10）：海渊巡游的 UP 是 克莱门莎(6★)、火哨(5★)、德·托莱多(5★)。
    assert stars["克莱门莎"] == 6
    assert stars["火哨"] == 5
    assert stars["德·托莱多"] == 5


def test_rarity_of_prefers_the_real_operator_over_a_local_trap_record(tmp_path) -> None:
    """实测 `battle_data.json` 里「阿米娅」也有 `trap_` 记录（rarity=1）：取非陷阱的最高星。"""

    path = tmp_path / "battle_data.json"
    path.write_text(
        json.dumps(
            {
                "chars": {
                    "char_002_amiya": {"name": "阿米娅", "rarity": 5},
                    "trap_627_xb2amy": {"name": "阿米娅", "rarity": 1},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = smw_payload(
        [
            {
                "title": "文件:测试卡池.jpg",
                "name": "测试卡池",
                "start": 1791000000,
                "end": 1792000000,
                "up": ["阿米娅"],
            }
        ]
    )
    service = make_service({"ask": payload}, battle_data_path=path)

    result = asyncio.run(service.pools(status="current", limit=5))

    assert result["pools"][0]["up_five"] == [
        {"name": "阿米娅", "rarity": 5, "source": "resource"},
    ]


def test_up_rarity_is_null_when_the_local_resource_is_missing(tmp_path) -> None:
    payload = smw_payload(
        [
            {
                "title": "文件:测试卡池.jpg",
                "name": "测试卡池",
                "start": 1791000000,
                "end": 1792000000,
                "up": ["银灰"],
            }
        ]
    )
    service = make_service({"ask": payload}, battle_data_path=tmp_path / "没有这份资源.json")

    result = asyncio.run(service.pools(status="current", limit=5))

    assert result["pools"][0]["up_five"] == [
        {"name": "银灰", "rarity": None, "source": None},
    ]


# --------------------------------------------------------------------------- #
# 参数校验、外服提示与降级
# --------------------------------------------------------------------------- #
def test_pools_rejects_unknown_status_and_out_of_range_limit_before_any_request() -> None:
    calls: list[httpx.Request] = []
    service = make_service({"ask": fixture("ask_gacha_pools.json")}, calls=calls)

    for kwargs in ({"status": "open"}, {"limit": 0}, {"limit": 11}):
        with pytest.raises(AppError) as error:
            asyncio.run(service.pools(**kwargs))
        assert error.value.code is ErrorCode.INVALID_PARAMETER
    assert calls == []


def test_pools_marks_foreign_clients_with_the_region_note() -> None:
    """显式传 client_type（工具层的路径）：外服照常查，只加 region_note（§4.3）。"""
    calls: list[httpx.Request] = []
    service = make_service({"ask": fixture("ask_gacha_pools.json")}, calls=calls)

    cn = asyncio.run(service.pools(status="current", limit=1, client_type="Official"))
    foreign = asyncio.run(service.pools(status="current", limit=1, client_type="YoStarJP"))

    assert cn["region_note"] is None
    assert foreign["region_note"] == FOREIGN_REGION_NOTE
    assert foreign["pools"] and foreign["pools_total"] == 4
    # 外服照常取数（第二次命中 ExternalClient 的缓存，所以只记到一次请求）。
    assert calls and foreign["sources"] and foreign["sources"][0]["kind"] == "prts"


def test_pools_uses_the_settings_channel_when_client_type_is_not_given() -> None:
    """注入段的路径：不带 client_type 时按设置判渠道（国服照常返回）。"""
    service = make_service({"ask": fixture("ask_gacha_pools.json")})

    result = asyncio.run(service.pools())

    assert result["pools"] and result["pools_total"] == 4
    assert result["region_note"] is None


def test_pools_omits_pools_for_foreign_channels_when_client_type_is_not_given() -> None:
    """注入段的路径 + 外服：整段不插，连 SMW 查询都不发（规格 §4.3「当期卡池不注入」）。"""
    calls: list[httpx.Request] = []
    service = make_service(
        {"ask": fixture("ask_gacha_pools.json")},
        calls=calls,
        settings=Settings(channel={"client_type": "YoStarEN"}),
    )

    result = asyncio.run(service.pools())

    assert result["pools"] == [] and result["pools_total"] == 0
    assert result["region_note"] == FOREIGN_REGION_NOTE
    assert result["unavailable"] == []
    assert calls == []


def test_pools_degrades_to_an_empty_list_when_prts_is_unavailable() -> None:
    service = make_service(prts=UnavailablePrts())

    result = asyncio.run(service.pools(status="current", limit=5))

    assert result["pools"] == []
    assert result["pools_total"] == 0
    assert result["unavailable"] == ["pools"]
    assert result["sources"] == []
    assert result["untrusted"] is True


def test_pools_skips_rows_without_both_timestamps() -> None:
    payload = smw_payload(
        [
            {
                "title": "文件:没有关闭时间.jpg",
                "name": "没有关闭时间",
                "start": 1791000000,
                "end": 0,
                "up": ["银灰"],
            }
        ]
    )
    # 只有开启时间的行：既判不了在开，也判不了已关闭。
    payload["query"]["results"]["文件:没有关闭时间.jpg"]["printouts"]["寻访关闭时间cn"] = []
    service = make_service({"ask": payload})

    current = asyncio.run(service.pools(status="current", limit=5))
    recent = asyncio.run(service.pools(status="recent", limit=5))

    assert current["pools"] == [] and current["pools_total"] == 0
    assert recent["pools"] == [] and recent["pools_total"] == 0


def test_default_gacha_service_is_a_shared_facade() -> None:
    assert default_gacha_service() is default_gacha_service()
