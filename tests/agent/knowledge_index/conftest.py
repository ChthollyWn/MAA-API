"""复用 ``tests/db/conftest.py`` 的临时库 fixture（T3/T4 的 FTS 与向量缓存用例）。"""

from tests.db.conftest import (  # noqa: F401  # pytest 从 conftest 命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)
