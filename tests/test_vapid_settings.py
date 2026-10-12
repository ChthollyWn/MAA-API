"""M14 设置契约：VAPID 密钥只从 config.yaml 提供，不走设置 API。

代理头、cookie 的 Secure 判定与浏览器来源白名单属于 M15（Docker/Tailscale 部署
配套），它们的测试在 M15 分支上，依赖 M15 才引入的 ``maa_api.main`` 辅助函数。
"""

from __future__ import annotations

import pytest


def test_vapid_keys_are_config_only_not_settings_api_keys() -> None:
    """私钥只能来自 config.yaml：不出现在设置 API，也不落库。

    放进设置 API 会同时踩两个坑：多行 PEM 在单行输入框里回写会丢换行；而
    ``sensitive`` 项读取只回 ``***``，用户保存任意**其它**字段时前端会把 ``***``
    当新值提交，从而静默覆盖真实私钥。

    但它们**必须**留在 ``SETTING_KEYS`` 里：``config.yaml`` 与环境变量两层都由那张
    表驱动解析，表里没有的段会被整个忽略（notify 段曾因此静默读不到值）。
    """
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import (
        CONFIG_ONLY_SETTING_KEYS,
        ENV_OVERRIDES,
        SETTING_KEYS,
        Settings,
        load_settings,
    )

    # 这张表是「哪几个 key 刻意不进设置 API」的完整清单：M15 的 VAPID 两条，
    # M42 的森空岛三条（凭据密钥敏感、定时间隔与超时不做前端开关，规格 §3.2）。
    assert CONFIG_ONLY_SETTING_KEYS == {
        "notify.vapid_private_key",
        "notify.vapid_subject",
        "skland.credential_key",
        "skland.auto_sync_interval_minutes",
        "skland.request_timeout_seconds",
    }
    for key in CONFIG_ONLY_SETTING_KEYS:
        assert key in SETTING_KEYS, f"{key} 必须留在 SETTING_KEYS 里，否则 yaml 段会被忽略"
        assert key in ENV_OVERRIDES
    schema_keys = {item["key"] for item in SETTINGS_SCHEMA}
    assert not (CONFIG_ONLY_SETTING_KEYS & schema_keys), "config-only 的 key 不应出现在设置 API"
    with pytest.raises(KeyError):
        schema_for("notify.vapid_private_key")

    # 值仍然能从 config.yaml 读到，且多行 PEM 不被压平
    import tempfile
    from pathlib import Path

    from maa_api.services import webpush_vapid as vapid

    private_pem, public_key = vapid.generate_vapid_key_pair()
    indented = "\n".join(f"    {line}" for line in private_pem.strip().splitlines())
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "config.yaml"
        config_path.write_text(
            "app:\n  access_token: t\n"
            "notify:\n  vapid_private_key: |\n"
            f"{indented}\n"
            "  vapid_subject: mailto:deploy@example.com\n",
            encoding="utf8",
        )
        loaded = load_settings(config_path)
    assert loaded.notify.vapid_subject == "mailto:deploy@example.com"
    assert loaded.notify.vapid_private_key.strip().startswith("-----BEGIN PRIVATE KEY-----")
    assert (
        vapid.public_key_from_private(
            vapid.load_vapid_private_key(loaded.notify.vapid_private_key)
        )
        == public_key
    )
    assert Settings().notify.vapid_private_key == ""


def test_settings_api_rejects_config_only_keys_explicitly(tmp_path, monkeypatch) -> None:
    """明确报「只能改 config.yaml」而不是含糊的「未知 key」。"""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    import maa_api.db.models  # noqa: F401
    from maa_api.db.session import make_engine
    from maa_api.domain.errors import AppError, ErrorCode
    from maa_api.services.setting_service import SettingService

    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def scenario() -> None:
        service = SettingService(factory)
        with pytest.raises(AppError) as excinfo:
            await service.update({"notify.vapid_private_key": "-----BEGIN PRIVATE KEY-----"})
        assert excinfo.value.code is ErrorCode.SETTING_READONLY
        assert "notify.vapid_private_key" in (excinfo.value.details or {}).get("keys", [])

        # 正常 key 仍然可写，且 config-only 的 key 不出现在读接口里
        await service.update({"agent.auto_diagnose_failures": False})
        listed = await service.get()
        keys = {item["key"] for item in listed["items"]}
        assert "agent.auto_diagnose_failures" in keys
        assert not (keys & {"notify.vapid_private_key", "notify.vapid_subject"})

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
