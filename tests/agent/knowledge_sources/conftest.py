"""四个外部语料来源测试的共用夹具。

复用 ``tests/db/conftest.py`` 的临时 SQLite 库（``db_session_factory``）；异步用例
统一包在 ``asyncio.run`` 里（本仓没有 pytest-asyncio）。
"""

from tests.db.conftest import (  # noqa: F401  # pytest 从 conftest 命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)
