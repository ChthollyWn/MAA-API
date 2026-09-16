"""``tests/api`` 共享夹具（M3-04 建，M3-05~M3-09 直接用）。

本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配：import 期
读真实配置、建目录，甚至可能连真实库）。被测 app 请在用例里用 ``FastAPI()`` 现搭，
需要哪些路由与处理器就装哪些。

三个夹具
--------

``tmp_settings``
    把 ``config.yaml`` 换成 ``tmp_path`` 下的临时文件并注入 ``get_settings()``
    的进程内缓存（用 :func:`maa_api.settings.set_settings`），默认 ``access_token``
    为空串＝免鉴权（docs/05 §5.2）。要设 token 用间接参数化：

    .. code-block:: python

        @pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
        def test_xxx(tmp_settings):
            assert tmp_settings.access_token == "s3cret"

    夹具同时清掉 ``MAA_*`` 环境变量层（它优先级高于 yaml）并把
    ``DEFAULT_CONFIG_PATH`` 也指向临时文件，用例结束把缓存还原为 ``None``。

``isolated_db``
    把 ``maa_api.db.session`` 的 ``DB_PATH`` / ``ASYNC_URL`` / ``SYNC_URL`` 指到
    ``tmp_path``，并**重建** ``engine`` 与 ``session_factory`` —— M2-13 实测：只改
    三条路径不够，模块级 engine/session_factory 是 import 期用当时的 URL 造好的
    （见 .refactor/ENVIRONMENT.md）。用例结束 ``dispose`` 临时引擎。
    **绝不触碰仓库真实 ``resource/maa_api.db``。**

    夹具只隔离不建表（``yield`` 的是临时 :class:`~sqlalchemy.ext.asyncio.AsyncEngine`）：
    要表请自己选一条路 —— ``SQLModel.metadata.create_all``（快，``tests/db`` 的做法）
    或 ``migrate.ensure_schema``（走 Alembic，靠 ``alembic_version`` 行判定）；两条路
    混用会在第二次建表时撞 "table already exists"。会话工厂从
    ``maa_api.db.session.session_factory`` 取（夹具已替换）。

``make_client``
    ``make_client(app)`` 返回 ``TestClient(app, raise_server_exceptions=False)``
    并已 ``__enter__``（lifespan 生效），用例结束由夹具统一 ``__exit__``。
    **必须关掉 re-raise**：否则 500 用例拿到的是异常而不是响应（M3-01 实测，
    见 .refactor/ENVIRONMENT.md）。仍可传 ``headers=`` 等 TestClient 关键字参数
    （``raise_server_exceptions`` 也能显式覆盖，但一般不需要）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

import maa_api.db.models  # noqa: F401  # 注册 13 张表，便于用例按需 create_all
import maa_api.settings as settings_module
from maa_api.db import session as db_session
from maa_api.settings import Settings

#: 临时 ``config.yaml`` 模板。access_token 用 JSON 引号写出（空串＝未配置＝免鉴权）；
#: adb 段一并写上，让 M3-06 之后的卡能断言非鉴权配置也被正确加载。
_CONFIG_TEMPLATE = """\
app:
  access_token: {token}
adb:
  path: /usr/bin/adb
  address: 127.0.0.1:5555
  screenshot_quality: 25
"""


@pytest.fixture
def tmp_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[Settings]:
    """临时 ``config.yaml`` + 注入的进程内缓存；``request.param`` 可传 access_token。"""
    param = getattr(request, "param", "")
    token = "" if param is None else str(param)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _CONFIG_TEMPLATE.format(token=json.dumps(token)), encoding="utf8"
    )

    # 环境变量层优先级高于 yaml（settings.resolve_settings），开发机上若真设了
    # MAA_APP_ACCESS_TOKEN 之类的变量，用例结果会随环境漂移，这里一律清掉。
    for env_name in settings_module.ENV_OVERRIDES.values():
        monkeypatch.delenv(env_name, raising=False)
    # 双保险：显式路径已给 load_settings，这里把默认路径也换掉 —— 万一用例中途
    # set_settings(None)，get_settings() 也不会去读仓库根的真实 config.yaml。
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_path)

    loaded = settings_module.load_settings(config_path)
    settings_module.set_settings(loaded)
    yield loaded
    settings_module.set_settings(None)


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncEngine]:
    """把数据层的库文件与 engine/session_factory 全部隔离到 ``tmp_path``。"""
    db_path = tmp_path / "maa_api.db"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_session, "DB_PATH", db_path)
    monkeypatch.setattr(db_session, "ASYNC_URL", async_url)
    monkeypatch.setattr(db_session, "SYNC_URL", sync_url)

    # 只改 URL 不够：模块级 engine / session_factory 是 import 期造好的（M2-13 实测），
    # 必须连对象一起换掉，否则取到的是指向真实 resource/maa_api.db 的旧引擎。
    engine = db_session.make_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "session_factory", factory)

    try:
        yield engine
    finally:
        # 同步接口 dispose：不依赖事件循环（tests/db 同款做法）。
        engine.sync_engine.dispose()


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    """``make_client(app)`` 工厂：已进入上下文的 ``TestClient``，夹具统一退出。"""
    clients: list[TestClient] = []

    def _make(app: FastAPI, **kwargs: Any) -> TestClient:
        kwargs.setdefault("raise_server_exceptions", False)
        client = TestClient(app, **kwargs)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in reversed(clients):
        client.__exit__(None, None, None)
