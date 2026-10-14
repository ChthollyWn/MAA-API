"""M42 §4.4/§4.5：森空岛凭据落库与同步编排（真实 SQLite + 假上游）。

覆盖四组行为：

- **凭据**：Fernet 加密往返、密钥缺失/换过时的 409、单行覆盖、解绑只删凭据不动练度；
- **同步**：写库字段与来源标记、识别记录、角色 ``synced_at``、摘要计数；
- **护栏**：没绑定的 409、``len(chars)`` 不足时**整轮不写**、冷却期 429、并发 409；
- **状态**：``bound`` / ``key_configured`` / ``last_sync`` / ``last_error``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel, select

from maa_api.db.models import AccountOperator, AccountRecognition, SklandRole, utcnow
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.capture import OperatorMeta
from maa_api.services.skland.client import BindingRole, SklandCredential
from maa_api.services.skland.store import SklandStore
from maa_api.services.skland.sync import SklandSyncService
from maa_api.settings import SKLAND_MIN_CHARS, Settings
from sqlalchemy.pool import NullPool

NOW = datetime(2026, 10, 12, 4, 0, 0)
KEY = Fernet.generate_key().decode("ascii")


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture
def session_factory(tmp_path):
    """临时库 + ``NullPool`` 会话工厂（跨多次 ``asyncio.run`` 复用同一个 engine）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'skland.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _settings(*, key: str = KEY, auto_sync: int = 0) -> Settings:
    settings = Settings()
    settings.skland.credential_key = key
    settings.skland.auto_sync_interval_minutes = auto_sync
    return settings


def _store(factory, *, key: str = KEY) -> SklandStore:
    settings = _settings(key=key)
    return SklandStore(factory, settings_provider=lambda: settings)


def _box(factory) -> OperatorBoxService:
    return OperatorBoxService(factory, settings_provider=lambda: Settings())


async def _seed_operator(factory, char_id: str, **fields: Any) -> None:
    """直接写一行「同步之前就存在」的干员（M47 起没有识别写入口了）。"""
    defaults: dict[str, Any] = dict(name="测试干员", rarity=6, own=True, recognized_at=NOW)
    defaults.update(fields)
    async with factory() as session:
        session.add(AccountOperator(char_id=char_id, **defaults))
        await session.commit()


def _chars(count: int, **overrides: Any) -> list[dict[str, Any]]:
    """造 ``count`` 名干员；``char_%04d`` 这种 id 只用于计数类断言。"""
    base = {
        "level": 80,
        "evolvePhase": 2,
        "potentialRank": 5,
        "mainSkillLvl": 7,
        "favorPercent": 200,
        "skills": [{"id": "skchr_amiya_2", "specializeLevel": 3}],
        "equip": [{"id": "eq1", "level": 2, "locked": False}],
    }
    rows = []
    for index in range(count):
        row = {**base, "charId": f"char_{index:04d}"}
        row.update(overrides)
        rows.append(row)
    return rows


@dataclass
class FakeClient:
    """假上游：只实现同步链路用到的三个方法。"""

    chars: list[dict[str, Any]] = field(default_factory=list)
    roles: list[BindingRole] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    refresh_error: AppError | None = None
    binding_error: AppError | None = None
    closed: bool = False

    async def refresh(self, credential: SklandCredential) -> SklandCredential:
        if self.refresh_error is not None:
            raise self.refresh_error
        return SklandCredential(cred=credential.cred, token=f"{credential.token}-new")

    async def binding(self, credential: SklandCredential) -> list[BindingRole]:
        if self.binding_error is not None:
            raise self.binding_error
        return list(self.roles)

    async def player_info(self, credential: SklandCredential, uid: str) -> dict[str, Any]:
        payload = {"chars": self.chars}
        payload.update(self.data)
        return payload

    async def aclose(self) -> None:
        self.closed = True


def _role(uid: str = "1234567890", *, is_default: bool = True) -> BindingRole:
    return BindingRole(
        uid=uid,
        app_code="arknights",
        channel_master_id="1",
        nickname="博士",
        level=120,
        is_default=is_default,
    )


def _sync_service(factory, client: FakeClient, *, now=NOW) -> SklandSyncService:
    settings = _settings()
    return SklandSyncService(
        store=_store(factory),
        box=_box(factory),
        client_factory=lambda **_: client,
        catalog_loader=lambda: {"char_0000": OperatorMeta(name="阿米娅", rarity=5, profession="术师")},
        settings_provider=lambda: settings,
        now=lambda: now,
    )


async def _seed_credential(factory, *, uid: str = "1234567890") -> None:
    store = _store(factory)
    await store.save_credential(
        SklandCredential(cred="cred-value", token="token-value"), bound_at=NOW
    )
    await store.replace_roles([_role(uid)])


# --------------------------------------------------------------------------- #
# 凭据
# --------------------------------------------------------------------------- #
def test_store_round_trips_credentials_encrypted(session_factory):
    async def scenario():
        store = _store(session_factory)
        assert await store.has_credential() is False
        assert await store.load_credential() is None

        await store.save_credential(
            SklandCredential(cred="cred-1", token="token-1"), bound_at=NOW
        )
        stored = await store.load_credential()
        assert stored is not None
        assert (stored.cred, stored.token) == ("cred-1", "token-1")

        # 库里落的是密文：明文一个字节都不该出现。
        async with session_factory() as session:
            from maa_api.db.models import SklandCredential as Row

            row = await session.get(Row, 1)
            assert "cred-1" not in row.cred_encrypted
            assert "token-1" not in row.token_encrypted

        # 换绑覆盖同一行。
        await store.save_credential(
            SklandCredential(cred="cred-2", token="token-2"), bound_at=NOW
        )
        assert (await store.load_credential()).cred == "cred-2"

    asyncio.run(scenario())


def test_store_reports_missing_or_rotated_key(session_factory):
    async def scenario():
        await _store(session_factory).save_credential(
            SklandCredential(cred="cred-1", token="token-1")
        )

        # 密钥没配：读要 409，但「有没有绑定」不需要密钥就能回答。
        no_key = _store(session_factory, key="")
        assert no_key.key_configured is False
        assert await no_key.has_credential() is True
        with pytest.raises(AppError) as missing:
            await no_key.load_credential()
        assert missing.value.code is ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID
        with pytest.raises(AppError) as cannot_write:
            await no_key.save_credential(SklandCredential(cred="x", token="y"))
        assert cannot_write.value.code is ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID

        # 密钥换过：解不开，同样是 409（提示重新绑定）。
        rotated = _store(session_factory, key=Fernet.generate_key().decode("ascii"))
        with pytest.raises(AppError) as rotated_error:
            await rotated.load_credential()
        assert rotated_error.value.code is ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID

    asyncio.run(scenario())


def test_unbind_only_drops_credentials(session_factory):
    async def scenario():
        await _seed_credential(session_factory)
        box = _box(session_factory)
        await box.apply_skland_sync(
            [
                _update("char_0001", level=90),
            ],
            synced_at=NOW,
        )
        store = _store(session_factory)
        assert await store.clear() is True
        assert await store.has_credential() is False
        assert await store.list_roles() == []

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_0001")
            assert row is not None and row.level == 90  # 练度是账号数据，不随解绑消失

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 同步
# --------------------------------------------------------------------------- #
def _update(char_id: str, **fields: Any):
    from maa_api.services.skland.mapping import OperatorTrainingUpdate

    return OperatorTrainingUpdate(char_id=char_id, **fields)


def test_sync_writes_training_and_records_the_round(session_factory):
    client = FakeClient(chars=_chars(SKLAND_MIN_CHARS), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        # 同步之前库里已经有一行（历史遗留形状），这一轮会整行覆盖它。
        await _seed_operator(
            session_factory,
            "char_0000",
            name="阿米娅",
            rarity=5,
            profession="术师",
            elite=1,
            level=50,
            potential=3,
        )
        summary = await service.sync()

        assert summary["status"] == "succeeded"
        assert summary["total"] == SKLAND_MIN_CHARS
        assert summary["role"]["uid"] == "1234567890"
        assert client.closed is True

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_0000")
            assert (row.elite, row.level, row.potential) == (2, 80, 6)
            assert row.skill_level == 7
            assert row.masteries == {"1": 3, "2": 0, "3": 0}
            # 假上游的 equip 用的是 ``eq1``，没有 equipmentInfoMap ⇒ 类型认不出，
            # 归一返回 ``None``（不知道）而不是 ``{}``（确认没装）。
            assert row.modules is None
            assert summary["dropped_invalid"] >= 1
            assert row.favor_percent == 200
            # M43：技能 id 按槽位落库（卡片拼技能图标要用），模组明细在类型认不出时
            # 与 `modules` 同口径留 NULL（不知道），不是空列表。
            assert row.skill_ids == ["skchr_amiya_2"]
            assert row.module_details is None
            assert row.field_sources["level"] == "skland"
            assert row.recognized_at is not None

            kinds = (
                await session.execute(select(AccountRecognition.kind))
            ).scalars().all()
            assert "skland" in kinds

            role = await session.get(SklandRole, "1234567890")
            assert role.synced_at is not None

        status = await service.status()
        assert status["bound"] is True
        assert status["last_sync_at"] is not None
        assert status["last_sync"]["total"] == SKLAND_MIN_CHARS
        assert status["last_error"] is None

    asyncio.run(scenario())


def test_sync_guard_keeps_the_database_untouched(session_factory):
    """低于 ``SKLAND_MIN_CHARS`` 的一轮：报错、**一个字段都不写**、不收缩所有权。"""
    client = FakeClient(chars=_chars(SKLAND_MIN_CHARS - 1), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        await _seed_operator(
            session_factory, "char_0000", elite=2, level=90, potential=6
        )

        with pytest.raises(AppError) as error:
            await service.sync()
        assert error.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
        assert error.value.details["chars"] == SKLAND_MIN_CHARS - 1

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_0000")
            assert row.level == 90
            assert row.own is True  # 没被误清成未拥有
            assert row.favor_percent is None
            assert "skland" not in set(row.field_sources.values())

        # 失败进 last_error，绑定关系不动。
        status = await service.status()
        assert status["bound"] is True
        assert "护栏" in status["last_error"]

    asyncio.run(scenario())


def test_sync_clears_ownership_of_missing_operators(session_factory):
    client = FakeClient(chars=_chars(SKLAND_MIN_CHARS - 1), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        # 先造一名「库里已拥有、这一轮 chars 里没有」的干员。
        await _seed_operator(session_factory, "char_9999", elite=2, level=90)
        # 补一名凑到恰好等于护栏，让这一轮通过护栏并允许收缩。
        client.chars = [*client.chars, {"charId": "char_extra", "level": 1, "evolvePhase": 0}]
        summary = await service.sync()
        assert summary["ownership_cleared"] == 1

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_9999")
            assert row.own is False
            assert row.field_sources["own"] == "skland"
            assert row.recognized_values["own"] is False
            # 练度值本身没被这一轮抹掉（这一轮 chars 里没有它）。
            assert row.level == 90

    asyncio.run(scenario())


def test_sync_requires_a_binding(session_factory):
    service = _sync_service(session_factory, FakeClient(chars=_chars(SKLAND_MIN_CHARS)))

    async def scenario():
        with pytest.raises(AppError) as error:
            await service.sync()
        assert error.value.code is ErrorCode.SKLAND_NOT_BOUND
        status = await service.status()
        assert status["bound"] is False
        assert status["auto_sync_enabled"] is False

    asyncio.run(scenario())


def test_sync_cooldown_and_busy_guard(session_factory):
    client = FakeClient(chars=_chars(SKLAND_MIN_CHARS), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        await service.sync()
        with pytest.raises(AppError) as cooldown:
            await service.sync()
        assert cooldown.value.code is ErrorCode.RATE_LIMITED
        assert cooldown.value.headers is not None and "Retry-After" in cooldown.value.headers

        # 锁被占用时回 409（模拟同时刻的另一次同步）。
        await service._lock.acquire()  # noqa: SLF001 - 直接验证忙态分支
        try:
            with pytest.raises(AppError) as busy:
                await service.sync()
            assert busy.value.code is ErrorCode.SKLAND_SYNC_BUSY
        finally:
            service._lock.release()

    asyncio.run(scenario())


def test_sync_reports_upstream_failure_without_dropping_the_binding(session_factory):
    # 用 binding 失败代表「上游在同步过程中报错」：续期失败已经不再算整轮失败（见上一条用例）。
    exit_error = AppError(ErrorCode.SKLAND_LOGIN_FAILED, "用户未登录")
    client = FakeClient(
        chars=_chars(SKLAND_MIN_CHARS), roles=[_role()], binding_error=exit_error
    )
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        with pytest.raises(AppError) as error:
            await service.sync()
        assert error.value.code is ErrorCode.SKLAND_LOGIN_FAILED

        status = await service.status()
        assert status["bound"] is True  # 偶发失败不该让用户重新扫码
        assert status["last_error"] == "用户未登录"

    asyncio.run(scenario())


def test_sync_writes_module_details_with_branch_and_label(session_factory):
    """M43：`typeIcon` 能解析时，`modules` 与 `module_details` 一起落库。"""

    class TypedClient(FakeClient):
        async def player_info(self, credential, uid):
            payload = await super().player_info(credential, uid)
            payload["equipmentInfoMap"] = {
                "eq1": {"id": "eq1", "name": "模组", "typeIcon": "ccr-y"},
                "eq2": {"id": "eq2", "name": "证章", "typeIcon": "original"},
            }
            for char in payload["chars"]:
                char["equip"] = [
                    {"id": "eq1", "level": 3, "locked": False},
                    {"id": "eq2", "level": 1, "locked": False},
                ]
            return payload

    client = TypedClient(chars=_chars(SKLAND_MIN_CHARS), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        await service.sync()

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_0000")
            assert row.modules == {"Y": 3}
            assert row.module_details == [
                {
                    "type": "Y",
                    "level": 3,
                    "equip_id": "eq1",
                    "branch": "ccr",
                    "label": "CCR-Y",
                    "type_icon": "ccr-y",
                }
            ]

    asyncio.run(scenario())


def test_sync_survives_a_failed_refresh(session_factory):
    """续期失败不该让整轮同步失败：换用已存的密钥继续（真过期时后面自己会报 401）。"""

    class FlakyRefresh(FakeClient):
        async def refresh(self, credential):
            raise AppError(ErrorCode.SKLAND_UPSTREAM_FAILED, "刷新接口挂了")

    client = FlakyRefresh(chars=_chars(SKLAND_MIN_CHARS), roles=[_role()])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        summary = await service.sync()
        assert summary["status"] == "succeeded"
        assert summary["total"] == SKLAND_MIN_CHARS
        # 绑定关系与凭据都还在，且没有被续期结果覆盖坏。
        stored = await _store(session_factory).load_credential()
        assert stored is not None and stored.token == "token-value"

    asyncio.run(scenario())


def test_sync_rejects_an_account_without_arknights_roles(session_factory):
    client = FakeClient(chars=_chars(SKLAND_MIN_CHARS), roles=[])
    service = _sync_service(session_factory, client)

    async def scenario():
        await _seed_credential(session_factory)
        with pytest.raises(AppError) as error:
            await service.sync()
        assert error.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
        assert "明日方舟" in error.value.message

    asyncio.run(scenario())
