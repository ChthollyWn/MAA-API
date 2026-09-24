#!/usr/bin/env python3
"""M3-10 端到端冒烟脚本：真实 app 装配 + lifespan 迁移 + 文档 / 鉴权 / 校验全链路。

这是 docs/12 给 M3 点名的「可交付状态」的**可执行形式**：新的 API 骨架可访问，
``/docs`` 可用且文档完整，鉴权生效，任务参数校验生效，业务端点尚未接内核
（docs/12 §M3）。脚本只断言 M3 已经实现的端点（docs/05 §6.1/§6.7）：

- ``GET /docs``、``GET /openapi.json``：15 个 tag（首 system、末 ws）、
  operationId 形如 ``{tag}_{name}`` 且唯一（docs/05 §11.1/§11.4）
- ``GET /api/system/health``：免鉴权、``auth_enabled`` 如实反映配置（docs/05 §5.3）
- ``POST`` / ``DELETE /api/system/auth/cookie``：token ↔ cookie 换取与清除（docs/05 §5.4）
- ``GET /api/tasks/types``、``GET /api/tasks/types/{type_name}``、
  ``POST /api/tasks/validate``：9 类 schema 与全局渠道默认值注入（docs/05 §6.7/§8/§9）

**刻意不断言** pipelines / queue / device / core / updates 等后续里程碑的端点：那会
把「M3 的完成」绑到后面几张卡的产出上，与 docs/12 §1「每个里程碑结束时系统可运行」
冲突。错误码只断言统一错误体里的 ``code``（docs/05 §4）。

两种模式
========

**默认模式（确定性，进 acceptance）**：在 ``tempfile.mkdtemp()`` 里造隔离环境，
``import maa_api.main`` 拿真实装配出的 :data:`maa_api.main.app`，用
``TestClient``（context manager，lifespan 真的执行）跑完全部检查：

1. lifespan 跑了迁移：临时库 ``alembic_version`` 恰好一行，等于迁移目录的 head
   （不写死 ``0002``，head 会随新迁移前移，M2-05/M2-14 实测教训）；
2. ``/docs``、``/openapi.json`` 可用；tag 数量与顺序、operationId 规范且唯一；
3. 免鉴权模式：``health.auth_enabled=false``、9 类任务 schema 齐全；
4. 切到鉴权模式（``set_settings`` 带 token 的 :class:`~maa_api.settings.Settings`）：
   无凭据 401（``WWW-Authenticate: Bearer`` + ``code=UNAUTHORIZED``）、``X-Token``
   与 ``?token=`` 200、仅凭 cookie 的 GET 200 / 写方法 403、``health`` 仍 200；
5. cookie 换取与清除的 ``Set-Cookie`` 属性（``HttpOnly`` / ``SameSite=lax`` /
   清除时 ``Max-Age=0``）；
6. ``POST /api/tasks/validate``：默认注入 ``Bilibili`` / ``CN``、显式 ``null`` 不注入、
   未知类型 400、``Infrast mode=10000`` 缺 ``filename`` 422、``Roguelike mode=2`` 400；
7. 尾斜杠 404（不是 307）、未知 ``/api`` 路径是统一错误体 404、CORS 预检矩阵；
8. ``TestClient`` 退出无异常；仓库根零写入（``*.db`` / ``config.yaml`` 指纹比对）。

**``--serve`` 模式（advisory，真实 uvicorn + 真端口）**：在另一个临时目录里起真实
进程 ``[sys.executable, "-m", "uvicorn", "maa_api.main:app", "--host", "127.0.0.1",
"--port", ...]``，轮询 ``/api/system/health`` 直到就绪（脚本内部 30s 超时，macOS
没有 ``timeout`` 命令），随后断言 ``auth_enabled=true``、``/docs`` 200、无 token 401、
带 ``X-Token`` 200，最后 ``terminate()`` + ``wait()``。默认只跑默认模式，``--serve``
= 默认模式 + 真实进程两段都跑。

隔离纪律（照 M2-13 的实测结论，见 docs/ENVIRONMENT.md）
==========================================================

- **只改** ``maa_api.db.session`` 的 ``DB_PATH`` / ``SYNC_URL`` / ``ASYNC_URL``
  **不够**：``engine`` 与 ``session_factory`` 是 import 期造好的，必须一并替换成
  指向临时目录的新对象，否则会连上仓库真实 ``resource/maa_api.db``（SQLite 连库
  即建文件）。
- 配置必须走 ``settings.set_settings(load_settings(临时 config.yaml))`` 并把
  ``settings.DEFAULT_CONFIG_PATH`` 也指向临时文件：lifespan 第 1 步会重新
  ``load_settings()``（M3-09 实测），只注入缓存会在进 TestClient 时被真实
  ``config.yaml`` 覆盖。同时清掉 ``MAA_*`` 环境变量层（优先级最高）。
- 本脚本**不切临时 cwd**：``maa_api/db/migrate.py`` 的 ``ALEMBIC_INI`` 与
  ``SCRIPT_LOCATION`` 都是 CWD 相对路径（调用方 cwd 必须是仓库根），切走会让
  lifespan 的迁移直接失败；临时库靠替换成**绝对路径**的模块属性隔离。
- ``--serve`` 必须用临时 cwd（子进程里没法替换模块属性，``DB_PATH`` 相对路径
  正好落在临时目录），但同一个 CWD 相对约束要求把 ``maa_api`` 桥接进临时目录
  （一个指向仓库包目录的符号链接）；另外 ``DEFAULT_CONFIG_PATH`` 锚在仓库根、
  与 CWD 无关（M3-03 实测），所以临时目录里的 ``config.yaml`` **不会**被读，
  鉴权开关走 ``MAA_APP_ACCESS_TOKEN`` 环境变量层，两者都写、以环境变量为准。
- 跑完仓库里不得留下任何 ``*.db`` / ``-wal`` / ``-shm`` / ``config.yaml``：脚本
  在运行前后对仓库内这些文件做指纹快照并比对，发现写入直接 ``[FAIL]``。

退出码即结论：全部检查通过 exit 0 并打印最后一行 ``SMOKE OK``；任何一项失败打印
``[FAIL] <项>`` 与细节后 exit 1。运行不需要网络、MaaCore 内核与真实设备；
``--help`` 不 import ``maa_api``（重导入全部延迟到参数解析之后），可当轻量门禁。

示例::

    .venv/bin/python scripts/api_smoke.py                 # 默认模式（门禁用）
    .venv/bin/python scripts/api_smoke.py --serve         # 追加真实 uvicorn 冒烟
    .venv/bin/python scripts/api_smoke.py --keep-temp     # 保留临时目录排查
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional

#: 直接执行脚本时 ``sys.path[0]`` 是 ``scripts/``；仓库根进 sys.path 后即使解释器
#: 没装 editable 包也能 import ``maa_api``（``--help`` 不触发任何重导入）。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 临时目录前缀（``--keep-temp`` 时保留现场，便于排查）。
TEMP_PREFIX = "maa-api-smoke-"
SERVE_TEMP_PREFIX = "maa-api-smoke-serve-"
#: 临时库相对路径：与 ``maa_api.db.session.DB_PATH`` 同形，最终落在临时目录下。
DB_RELATIVE = Path("resource") / "maa_api.db"
#: 测试用 token（只存在于本次运行的临时配置 / 环境变量里）。
TOKEN = "s3cret-smoke-token"
#: ``--serve`` 的默认端口与内部超时（macOS 没有 ``timeout`` 命令，超时只能自己数）。
DEFAULT_PORT = 8123
SERVE_READY_TIMEOUT = 30.0
SERVER_STOP_TIMEOUT = 10.0

#: 仓库零写入自查要跳过的目录（第三方包 / VCS 元数据 / 缓存，不属于本仓产物）。
_SNAPSHOT_SKIP_DIRS = frozenset(
    {".git", ".venv", ".pytest_cache", "__pycache__", ".idea", ".mypy_cache", "node_modules"}
)

#: M3 已交付的六个 operation（docs/05 §6.1/§6.7）：只断言它们存在，不禁止后续里程碑追加。
_EXPECTED_OPERATION_IDS = frozenset(
    {
        "system_health",
        "system_exchange_cookie",
        "system_clear_cookie",
        "tasks_list_types",
        "tasks_get_type",
        "tasks_validate",
    }
)

_EPILOG = """\
示例:
  # 默认模式 (门禁用的那条: 真实 app + TestClient, 秒级结束, 不需要网络/内核/设备)
  .venv/bin/python scripts/api_smoke.py

  # 追加真实 uvicorn 进程 (127.0.0.1 本地端口; 默认 8123)
  .venv/bin/python scripts/api_smoke.py --serve --port 8123

  # 失败时保留临时目录 (临时库 / server.log / 失败现场) 以便排查
  .venv/bin/python scripts/api_smoke.py --keep-temp

`--help` 只解析参数: 不 import maa_api.main、不建库、不碰任何文件。
"""


class SmokeFailure(RuntimeError):
    """冒烟步骤失败（脚本内信号，统一转成退出码 1）。"""


# ----------------------------------------------------------------------
# 无副作用的小工具（都在调用时才 import 重依赖 / 只读文件）
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器（只解析参数，不 import ``maa_api.main``）。"""
    parser = argparse.ArgumentParser(
        prog="api_smoke.py",
        description=(
            "M3-10 API 骨架冒烟：真实 app 装配 + lifespan 迁移 + 文档/鉴权/校验全链路"
            "（默认 TestClient；--serve 追加真实 uvicorn）。"
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="在默认模式之后追加真实 uvicorn 进程冒烟（仅监听 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"--serve 监听的本地端口（默认 {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留临时目录（临时库 / server.log / 失败现场），默认退出时清理",
    )
    return parser


def _alembic_head() -> str:
    """从迁移脚本目录读 head（不连库；路径按仓库根绝对解析，与 CWD 无关）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "maa_api" / "db" / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise SmokeFailure("迁移目录没有 head")
    return head


def _alembic_version_rows(db_path: Path) -> list[str]:
    """读库里的 ``alembic_version`` 全部行（标准库 sqlite3，不经过 ORM）。"""
    import sqlite3

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return [
            row[0]
            for row in conn.execute("select version_num from alembic_version").fetchall()
        ]


def _snapshot_repo_artifacts() -> dict[str, tuple[int, int]]:
    """仓库内 ``*.db`` / ``-wal`` / ``-shm`` 与 ``config.yaml`` 的指纹快照。

    相对路径 → ``(size, mtime_ns)``。跳过 ``.git`` / ``.venv`` / 缓存目录：它们不是
    本仓产物，第三方包里出现 ``*.db`` 与本脚本的隔离纪律无关。
    """
    snapshot: dict[str, tuple[int, int]] = {}
    for base, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _SNAPSHOT_SKIP_DIRS)
        for name in filenames:
            if not name.endswith((".db", ".db-wal", ".db-shm")) and name != "config.yaml":
                continue
            path = Path(base) / name
            stat = path.stat()
            snapshot[str(path.relative_to(REPO_ROOT))] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _http_get(
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 2.0,
) -> tuple[Optional[int], dict[str, str], bytes]:
    """发一次 GET；HTTP 错误状态原样返回 ``(status, headers, body)``，连不上返回 ``None``。

    ``--serve`` 的轮询与断言都用它：不引入第三方依赖，也不需要网络（只连 127.0.0.1）。
    """
    request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(),
        )
    except (urllib.error.URLError, OSError):
        return None, {}, b""


# ----------------------------------------------------------------------
# 冒烟主体
# ----------------------------------------------------------------------


class ApiSmoke:
    """一次冒烟运行的编排与断言（构造只做只读快照，重导入全在 ``run()`` 之后）。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tmp_dir: Optional[Path] = None
        self.db_path: Optional[Path] = None
        self.serve_dir: Optional[Path] = None
        self.engine: Any = None
        self.session_factory: Any = None
        self.client: Any = None
        self.proc: Optional[subprocess.Popen] = None
        self._openapi: Optional[dict[str, Any]] = None

        #: 硬约束自检：仓库里的 ``*.db`` / ``config.yaml`` 在本次冒烟前后必须完全不变。
        self.repo_before = _snapshot_repo_artifacts()

        self.checks: list[tuple[str, str]] = []
        self.failures: list[str] = []
        self.started = time.monotonic()

    # ---- 输出 ----

    @staticmethod
    def step(message: str) -> None:
        print(f"[api_smoke] {message}", flush=True)

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[api_smoke][FAIL] {message}", flush=True)

    def ok(self, name: str, detail: str) -> None:
        self.checks.append((name, detail))
        self.step(f"[ok] {name}：{detail}")

    @staticmethod
    def require(condition: bool, message: str) -> None:
        """断言失败即抛 :class:`SmokeFailure`（当前检查项记一条 ``[FAIL]``）。"""
        if not condition:
            raise SmokeFailure(message)

    def _check(self, name: str, check: Callable[[], str]) -> bool:
        """跑一个检查项：成功打 ``[ok]``，失败打 ``[FAIL]`` 并继续跑后面的检查。"""
        try:
            detail = check()
        except SmokeFailure as exc:
            self.fail(f"{name}：{exc}")
            return False
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须自己收尾并给出结论
            self.fail(f"{name}：未预期异常 {type(exc).__name__}: {exc}")
            traceback.print_exc()
            return False
        self.ok(name, detail)
        return True

    # ------------------------------------------------------------------
    # 准备：临时目录 + 隔离（session 对象 / settings / 环境变量）
    # ------------------------------------------------------------------

    def _prepare_tempdir(self) -> None:
        """建临时目录；cwd 固定回仓库根（alembic.ini 与 script_location 都是 CWD 相对）。"""
        if Path.cwd() != REPO_ROOT:
            os.chdir(REPO_ROOT)
            self.step(f"已切换工作目录到仓库根：{REPO_ROOT}（alembic.ini 按仓库根相对解析）")

        self.tmp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
        self.require(
            not self.tmp_dir.is_relative_to(REPO_ROOT),
            f"临时目录落在仓库内，隔离失效：{self.tmp_dir}",
        )
        self.db_path = self.tmp_dir / DB_RELATIVE
        self.step(f"临时目录：{self.tmp_dir}")
        self.step(f"临时库：{self.db_path}（仓库 resource/{DB_RELATIVE.name} 不参与本次冒烟）")

    def _bind_isolated_env(self) -> None:
        """把 session 的路径/引擎/会话工厂与 settings 全部指到临时目录。

        M2-13 实测：模块级 ``engine`` / ``session_factory`` 是 import 期造好的，只改
        三条 URL 会让请求侧连上真实库；M3-09 实测：lifespan 第 1 步会重新
        ``load_settings()``，所以 ``DEFAULT_CONFIG_PATH`` 也必须换掉。
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.pool import NullPool

        import maa_api.settings as settings_module
        from maa_api.db import session as db_session
        from maa_api.db.session import make_engine

        assert self.tmp_dir is not None and self.db_path is not None

        # 临时 config.yaml：access_token 留空＝免鉴权（docs/05 §5.2），adb 段照模板写。
        config_path = self.tmp_dir / "config.yaml"
        config_path.write_text(
            "app:\n"
            "  access_token:\n"
            "adb:\n"
            "  path: /usr/bin/adb\n"
            "  address: 127.0.0.1:5555\n"
            "  screenshot_quality: 25\n",
            encoding="utf8",
        )
        # 环境变量层优先级高于 yaml：开发机上的 MAA_* 会让结果随环境漂移，一律清掉。
        for env_name in settings_module.ENV_OVERRIDES.values():
            os.environ.pop(env_name, None)

        db_session.DB_PATH = self.db_path
        db_session.SYNC_URL = f"sqlite:///{self.db_path}"
        db_session.ASYNC_URL = f"sqlite+aiosqlite:///{self.db_path}"
        # NullPool：请求侧每个会话开/关自己的连接（关闭发生在 TestClient 的事件循环里）。
        # 默认池会把连接留到 TestClient 退出之后，那时脚本再 dispose 只能在没有 greenlet
        # 的同步上下文里关连接，SQLAlchemy 池会打出 MissingGreenlet 的 error 日志。
        self.engine = make_engine(db_session.ASYNC_URL, poolclass=NullPool)
        db_session.engine = self.engine
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        db_session.session_factory = self.session_factory

        settings_module.DEFAULT_CONFIG_PATH = config_path
        settings_module.set_settings(settings_module.load_settings(config_path))

        # 自证：迁移与请求侧都从模块属性读路径，读到的必须是临时库/临时配置。
        self.require(
            Path(db_session.DB_PATH) == self.db_path
            and db_session.SYNC_URL.endswith(str(self.db_path))
            and db_session.ASYNC_URL.endswith(str(self.db_path)),
            "把 session 指到临时库之后，模块属性读回来不是临时库",
        )
        self.require(
            db_session.engine is self.engine
            and db_session.session_factory is self.session_factory,
            "session.engine / session_factory 未替换成临时对象（只改 URL 不够，M2-13 实测）",
        )
        self.require(
            settings_module.DEFAULT_CONFIG_PATH == config_path
            and settings_module.get_settings().access_token == "",
            "settings 未指向临时配置（lifespan 会重新 load_settings，M3-09 实测）",
        )
        self.ok(
            "隔离环境就绪",
            "session 四元组 + settings 缓存/默认路径均指向临时目录；MAA_* 环境变量已清空",
        )

    # ------------------------------------------------------------------
    # 默认模式：TestClient + 真实 app
    # ------------------------------------------------------------------

    def _openapi_spec(self) -> dict[str, Any]:
        """取一次 ``/openapi.json``（进程内缓存，供三个文档检查项共用）。"""
        self.require(self.client is not None, "TestClient 未就绪")
        if self._openapi is None:
            response = self.client.get("/openapi.json")
            self.require(
                response.status_code == 200,
                f"/openapi.json 状态 {response.status_code}: {response.text[:200]}",
            )
            self._openapi = response.json()
        return self._openapi

    def _auth_headers(self) -> dict[str, str]:
        """带 token 的请求头（四渠道里最明确的一个：``X-Token``）。"""
        return {"X-Token": TOKEN}

    # ---- 1. lifespan 迁移 ----

    def _check_migration(self) -> str:
        assert self.db_path is not None
        self.require(self.db_path.exists(), f"lifespan 之后临时库仍不存在：{self.db_path}")
        rows = _alembic_version_rows(self.db_path)
        head = _alembic_head()
        self.require(len(rows) == 1, f"alembic_version 应有且仅有一行，实际 {rows!r}")
        self.require(
            rows[0] == head,
            f"库里的迁移版本 {rows[0]!r} != 迁移目录 head {head!r}",
        )
        return f"alembic_version={rows[0]}（= ScriptDirectory head），库 {self.db_path.name}"

    # ---- 2. 文档 ----

    def _check_docs(self) -> str:
        response = self.client.get("/docs")
        self.require(
            response.status_code == 200,
            f"/docs 状态 {response.status_code}: {response.text[:200]}",
        )
        spec = self._openapi_spec()
        info = spec.get("info", {})
        return f"/docs 与 /openapi.json 均 200（{info.get('title')} {info.get('version')}）"

    def _check_tags(self) -> str:
        tags = [entry["name"] for entry in self._openapi_spec().get("tags", [])]
        self.require(len(tags) == 15, f"tag 数量应为 15，实际 {len(tags)}：{tags!r}")
        self.require(tags[0] == "system", f"首个 tag 应为 system，实际 {tags[0]!r}")
        self.require(tags[-1] == "ws", f"末个 tag 应为 ws，实际 {tags[-1]!r}")
        return f"{len(tags)} 个 tag：{tags[0]!r} … {tags[-1]!r}"

    def _check_operation_ids(self) -> str:
        ids: list[str] = []
        for path, item in self._openapi_spec().get("paths", {}).items():
            for method, operation in item.items():
                if not isinstance(operation, dict) or "operationId" not in operation:
                    continue
                operation_tags = operation.get("tags") or []
                self.require(
                    bool(operation_tags),
                    f"{method.upper()} {path} 的 operation 没有 tags，无法校验命名",
                )
                prefix = f"{operation_tags[0]}_"
                operation_id = operation["operationId"]
                self.require(
                    operation_id.startswith(prefix) and len(operation_id) > len(prefix),
                    f"{method.upper()} {path} 的 operationId {operation_id!r} "
                    f"不是 {{tag}}_{{name}} 形态（tag={operation_tags[0]!r}）",
                )
                ids.append(operation_id)

        self.require(len(set(ids)) == len(ids), f"operationId 有重复：{ids!r}")
        missing = _EXPECTED_OPERATION_IDS - set(ids)
        self.require(not missing, f"M3 端点的 operationId 缺失：{sorted(missing)}")
        return f"{len(ids)} 条 operationId 全部形如 {{tag}}_{{name}} 且唯一；M3 六条端点齐全"

    # ---- 3. 免鉴权模式 ----

    def _check_health_anonymous(self) -> str:
        response = self.client.get("/api/system/health")
        self.require(
            response.status_code == 200,
            f"/api/system/health 状态 {response.status_code}: {response.text[:200]}",
        )
        body = response.json()
        self.require(
            body.get("auth_enabled") is False,
            f"免鉴权模式 auth_enabled 应为 false，实际 {body.get('auth_enabled')!r}",
        )
        return f"200，auth_enabled=false，version={body.get('version')!r}"

    def _check_types(self) -> str:
        response = self.client.get("/api/tasks/types")
        self.require(
            response.status_code == 200,
            f"/api/tasks/types 状态 {response.status_code}: {response.text[:200]}",
        )
        body = response.json()
        items = body.get("items")
        self.require(
            isinstance(items, list) and len(items) == 9,
            f"items 应为 9 项，实际 {len(items) if isinstance(items, list) else items!r}",
        )
        required_keys = {
            "name",
            "label",
            "description",
            "schema",
            "groups",
            "runtime_immutable",
        }
        names: list[str] = []
        for item in items:
            self.require(isinstance(item, dict), f"types 的元素应为对象，实际 {type(item).__name__}")
            missing = required_keys - set(item)
            self.require(
                not missing,
                f"类型 {item.get('name')!r} 的 schema 项缺少键 {sorted(missing)}",
            )
            self.require(
                isinstance(item["schema"], dict)
                and isinstance(item["groups"], list)
                and isinstance(item["runtime_immutable"], list),
                f"类型 {item.get('name')!r} 的 schema/groups/runtime_immutable 类型不对",
            )
            names.append(item["name"])
        self.require(len(set(names)) == 9, f"9 个类型名应互不相同：{names!r}")
        return f"9 类：{', '.join(names)}"

    # ---- 4. 鉴权模式 ----

    def _check_unauthorized(self) -> str:
        response = self.client.get("/api/tasks/types")
        self.require(
            response.status_code == 401,
            f"无凭据应为 401，实际 {response.status_code}: {response.text[:200]}",
        )
        self.require(
            response.headers.get("www-authenticate") == "Bearer",
            f"401 必须带 WWW-Authenticate: Bearer，实际 {response.headers.get('www-authenticate')!r}",
        )
        body = response.json()
        self.require(list(body) == ["error"], f"错误体应只有 error 键，实际 {list(body)!r}")
        self.require(
            body["error"]["code"] == "UNAUTHORIZED",
            f"错误码应为 UNAUTHORIZED，实际 {body['error']['code']!r}",
        )
        return "401 + WWW-Authenticate: Bearer + 统一错误体 code=UNAUTHORIZED"

    def _check_x_token(self) -> str:
        response = self.client.get("/api/tasks/types", headers=self._auth_headers())
        self.require(
            response.status_code == 200,
            f"X-Token 渠道应为 200，实际 {response.status_code}: {response.text[:200]}",
        )
        return f"X-Token → 200（total={response.json().get('total')}）"

    def _check_query_token(self) -> str:
        response = self.client.get("/api/tasks/types", params={"token": TOKEN})
        self.require(
            response.status_code == 200,
            f"?token= 渠道应为 200，实际 {response.status_code}: {response.text[:200]}",
        )
        return "?token= → 200"

    def _check_cookie_channel(self) -> str:
        headers = {"Cookie": f"maa_token={TOKEN}"}
        read = self.client.get("/api/tasks/types", headers=headers)
        self.require(
            read.status_code == 200,
            f"仅凭 cookie 的 GET 应为 200，实际 {read.status_code}: {read.text[:200]}",
        )
        write = self.client.post("/api/system/auth/cookie", headers=headers)
        self.require(
            write.status_code == 403,
            f"仅凭 cookie 的 POST 应为 403，实际 {write.status_code}: {write.text[:200]}",
        )
        self.require(
            write.json()["error"]["code"] == "FORBIDDEN",
            f"cookie 写操作错误码应为 FORBIDDEN，实际 {write.json()['error']['code']!r}",
        )
        validated = self.client.post(
            "/api/tasks/validate",
            headers=headers,
            json={"tasks": [{"name": "StartUp"}]},
        )
        self.require(
            validated.status_code == 403,
            f"仅凭 cookie 调 /api/tasks/validate 应为 403，实际 {validated.status_code}",
        )
        return "仅凭 cookie：GET 200、POST 403（FORBIDDEN，docs/05 §5.1 渠道收窄）"

    def _check_health_authenticated(self) -> str:
        response = self.client.get("/api/system/health")
        self.require(
            response.status_code == 200,
            f"鉴权模式 health 仍应免鉴权 200，实际 {response.status_code}",
        )
        body = response.json()
        self.require(
            body.get("auth_enabled") is True,
            f"鉴权模式 auth_enabled 应为 true，实际 {body.get('auth_enabled')!r}",
        )
        return "200，auth_enabled=true（免鉴权豁免仍生效）"

    # ---- 5. cookie 换取 / 清除 ----

    def _check_exchange_cookie(self) -> str:
        response = self.client.post(
            "/api/system/auth/cookie",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.require(
            response.status_code == 204,
            f"换取 cookie 应为 204，实际 {response.status_code}: {response.text[:200]}",
        )
        set_cookie = response.headers.get("set-cookie", "")
        compact = set_cookie.lower().replace(" ", "")
        self.require(f"maa_token={TOKEN}" in set_cookie, f"Set-Cookie 缺少 maa_token：{set_cookie!r}")
        self.require("httponly" in compact, f"Set-Cookie 缺少 HttpOnly：{set_cookie!r}")
        self.require("samesite=lax" in compact, f"Set-Cookie 缺少 SameSite=lax：{set_cookie!r}")
        return f"204 + Set-Cookie: {set_cookie}"

    def _check_clear_cookie(self) -> str:
        response = self.client.delete(
            "/api/system/auth/cookie",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.require(
            response.status_code == 204,
            f"清除 cookie 应为 204，实际 {response.status_code}: {response.text[:200]}",
        )
        set_cookie = response.headers.get("set-cookie", "")
        compact = set_cookie.lower().replace(" ", "")
        self.require("maa_token=" in compact, f"清除响应缺少 maa_token：{set_cookie!r}")
        self.require("max-age=0" in compact, f"清除响应缺少 Max-Age=0：{set_cookie!r}")
        return f"204 + Set-Cookie: {set_cookie}"

    # ---- 6. 参数校验 ----

    def _validate(self, tasks: list[dict[str, Any]]) -> Any:
        """POST /api/tasks/validate（带合法凭据），返回响应对象。"""
        return self.client.post(
            "/api/tasks/validate", headers=self._auth_headers(), json={"tasks": tasks}
        )

    def _validate_item(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """校验一个任务并返回 ``items[0]``（状态码与信封形状都在这里断言）。"""
        response = self._validate(tasks)
        self.require(
            response.status_code == 200,
            f"/api/tasks/validate 状态 {response.status_code}: {response.text[:300]}",
        )
        body = response.json()
        self.require(
            body.get("total") == 1 and len(body.get("items", [])) == 1,
            f"校验响应信封不对：{body!r}",
        )
        return body["items"][0]

    def _check_validate_injects(self) -> str:
        item = self._validate_item([{"name": "Fight", "stage": "1-7"}])
        params, raw_params = item["params"], item["raw_params"]
        self.require(
            params.get("client_type") == "Bilibili",
            f"未指定 client_type 应注入 Bilibili，实际 {params.get('client_type')!r}",
        )
        self.require(
            params.get("server") == "CN",
            f"未指定 server 应注入 CN，实际 {params.get('server')!r}",
        )
        injected = {"client_type", "server"} & set(raw_params)
        self.require(
            not injected,
            f"raw_params 不应包含注入键 {sorted(injected)}（它是原始提交快照）：{raw_params!r}",
        )
        self.require(raw_params.get("stage") == "1-7", f"raw_params 丢了原始参数：{raw_params!r}")
        return f"params={params!r}；raw_params={raw_params!r}"

    def _check_validate_explicit_null(self) -> str:
        item = self._validate_item(
            [{"name": "Fight", "stage": "1-7", "client_type": None}]
        )
        params, raw_params = item["params"], item["raw_params"]
        self.require(
            "client_type" not in params,
            f"显式 null 不该被注入（params 应无该键），实际 {params!r}",
        )
        self.require(
            "client_type" in raw_params and raw_params["client_type"] is None,
            f"raw_params 应保留显式 null，实际 {raw_params!r}",
        )
        return "显式 client_type=null：params 无该键、raw_params 保留 null（三态语义）"

    def _check_validate_unknown(self) -> str:
        response = self._validate([{"name": "Nope"}])
        self.require(
            response.status_code == 400,
            f"未知任务类型应为 400，实际 {response.status_code}: {response.text[:200]}",
        )
        code = response.json()["error"]["code"]
        self.require(code == "UNKNOWN_TASK_TYPE", f"错误码应为 UNKNOWN_TASK_TYPE，实际 {code!r}")
        return "未知 name → 400 UNKNOWN_TASK_TYPE（discriminator 特判，不是 422）"

    def _check_validate_infrast(self) -> str:
        response = self._validate([{"name": "Infrast", "mode": 10000}])
        self.require(
            response.status_code == 422,
            f"Infrast mode=10000 缺 filename 应为 422，实际 {response.status_code}: "
            f"{response.text[:200]}",
        )
        code = response.json()["error"]["code"]
        self.require(code == "TASK_PARAM_INVALID", f"错误码应为 TASK_PARAM_INVALID，实际 {code!r}")
        return "Infrast mode=10000 缺 filename → 422 TASK_PARAM_INVALID"

    def _check_validate_roguelike(self) -> str:
        response = self._validate([{"name": "Roguelike", "mode": 2}])
        self.require(
            response.status_code == 400,
            f"Roguelike mode=2 应为 400，实际 {response.status_code}: {response.text[:200]}",
        )
        code = response.json()["error"]["code"]
        self.require(
            code == "TASK_PARAM_DEPRECATED", f"错误码应为 TASK_PARAM_DEPRECATED，实际 {code!r}"
        )
        return "Roguelike mode=2 → 400 TASK_PARAM_DEPRECATED"

    # ---- 7. 路由与 CORS ----

    def _check_trailing_slash(self) -> str:
        response = self.client.get(
            "/api/tasks/types/", headers=self._auth_headers(), follow_redirects=False
        )
        self.require(
            response.status_code == 404,
            f"尾斜杠应 404（redirect_slashes=False），实际 {response.status_code}",
        )
        self.require(
            "location" not in response.headers,
            f"404 不该带 Location 头，实际 {response.headers.get('location')!r}",
        )
        return "GET /api/tasks/types/ → 404，无 Location 头（不是 307）"

    def _check_unknown_path(self) -> str:
        response = self.client.get("/api/nope")
        self.require(
            response.status_code == 404,
            f"/api/nope 应 404，实际 {response.status_code}: {response.text[:200]}",
        )
        body = response.json()
        self.require(list(body) == ["error"], f"404 应走统一错误体，实际 {list(body)!r}")
        self.require(
            body["error"]["code"] == "NOT_FOUND",
            f"错误码应为 NOT_FOUND，实际 {body['error']['code']!r}",
        )
        return '404 + {"error": {"code": "NOT_FOUND"}}（不是框架默认 {"detail": ...}）'

    def _check_cors(self) -> str:
        allowed = "http://localhost:8002"
        preflight = self.client.options(
            "/api/tasks/types",
            headers={
                "Origin": allowed,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Token, Content-Type",
            },
        )
        self.require(
            preflight.status_code == 200,
            f"白名单 origin 预检应 200，实际 {preflight.status_code}: {preflight.text[:200]}",
        )
        self.require(
            preflight.headers.get("access-control-allow-origin") == allowed,
            "白名单预检必须回显具体 origin，实际 "
            f"{preflight.headers.get('access-control-allow-origin')!r}",
        )
        self.require(
            preflight.headers.get("access-control-allow-credentials") == "true",
            "白名单预检必须带 access-control-allow-credentials: true",
        )

        denied = self.client.options(
            "/api/tasks/types",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Token",
            },
        )
        self.require(
            "access-control-allow-origin" not in denied.headers,
            "未知 origin 不得回显 access-control-allow-origin，实际 "
            f"{denied.headers.get('access-control-allow-origin')!r}",
        )
        return f"{allowed} 预检回显 origin + credentials=true；未知 origin 无 ACAO 头"

    # ---- 8. 产物与收尾 ----

    def _check_temp_artifacts(self) -> str:
        assert self.tmp_dir is not None and self.db_path is not None
        self.require(self.db_path.exists(), f"临时库里没有生成库文件：{self.db_path}")
        self.require(
            self.db_path.is_relative_to(self.tmp_dir),
            f"库文件跑到临时目录之外：{self.db_path}",
        )
        names = sorted(path.name for path in self.db_path.parent.iterdir())
        return f"{self.db_path.name}（{self.db_path.stat().st_size} 字节）位于 {self.db_path.parent}；同级 {names}"

    def _check_repo_untouched(self) -> str:
        after = _snapshot_repo_artifacts()
        added = sorted(set(after) - set(self.repo_before))
        removed = sorted(set(self.repo_before) - set(after))
        changed = sorted(
            name
            for name in set(after) & set(self.repo_before)
            if after[name] != self.repo_before[name]
        )
        self.require(
            not (added or removed or changed),
            f"仓库内 *.db / config.yaml 被改动：新增 {added}，删除 {removed}，"
            f"指纹变化 {changed}；冒烟脚本必须只写临时目录",
        )
        return (
            f"{len(after)} 个受监控文件（*.db / -wal / -shm / config.yaml）"
            "的存在性与指纹均未变化"
        )

    def _run_default_mode(self) -> None:
        """进入 TestClient（跑 lifespan）→ 逐项检查 → 退出并确认无异常。"""
        self.step("=== 默认模式：真实 app 装配 + TestClient（lifespan 生效）")
        from fastapi.testclient import TestClient

        import maa_api.main as main_module
        from maa_api.api import deps
        from maa_api.settings import Settings, set_settings

        try:
            client = TestClient(main_module.app, raise_server_exceptions=False)
            client.__enter__()
        except Exception as exc:  # noqa: BLE001 - lifespan 失败也要给出结论
            self.fail(f"进入 TestClient（lifespan）异常：{type(exc).__name__}: {exc}")
            traceback.print_exc()
            return
        self.client = client

        try:
            self._check("lifespan 迁移到 head", self._check_migration)
            self._check("文档端点可用", self._check_docs)
            self._check("OpenAPI tag 分组", self._check_tags)
            self._check("operationId 规范", self._check_operation_ids)
            self._check("免鉴权模式 health", self._check_health_anonymous)
            self._check("任务类型清单", self._check_types)

            # 切到鉴权模式：require_auth/auth_enabled 每次调用都读 get_settings()，
            # 无需重进 TestClient（lifespan 已经把缓存刷成了临时配置里的免鉴权模式）。
            set_settings(Settings(access_token=TOKEN))
            deps.reset_rate_limiter()  # 让 401 计数与上一次运行/前面的用例无关
            self.step(f"已切到鉴权模式：set_settings(access_token={TOKEN!r})")

            self._check("无凭据 401", self._check_unauthorized)
            self._check("X-Token 渠道", self._check_x_token)
            self._check("query 渠道", self._check_query_token)
            self._check("cookie 渠道收窄", self._check_cookie_channel)
            self._check("鉴权模式 health 免鉴权", self._check_health_authenticated)
            self._check("换取 cookie", self._check_exchange_cookie)
            self._check("清除 cookie", self._check_clear_cookie)
            self._check("校验：注入渠道默认值", self._check_validate_injects)
            self._check("校验：显式 null 不注入", self._check_validate_explicit_null)
            self._check("校验：未知任务类型", self._check_validate_unknown)
            self._check("校验：Infrast mode=10000", self._check_validate_infrast)
            self._check("校验：Roguelike mode=2", self._check_validate_roguelike)
            self._check("尾斜杠 404", self._check_trailing_slash)
            self._check("未知路径统一 404", self._check_unknown_path)
            self._check("CORS 预检", self._check_cors)
            self._check("临时库文件落位", self._check_temp_artifacts)
        finally:
            try:
                client.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - 关闭序列异常必须上报
                self.fail(f"退出 TestClient 异常：{type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                self.ok("TestClient 退出", "lifespan 关闭序列无异常")
            self.client = None

    # ------------------------------------------------------------------
    # --serve 模式：真实 uvicorn + 真端口
    # ------------------------------------------------------------------

    def _serve_log_tail(self, limit: int = 800) -> str:
        """读 server.log 末尾（失败时把启动 traceback 打出来）。"""
        if self.serve_dir is None:
            return "<无临时目录>"
        log_path = self.serve_dir / "server.log"
        if not log_path.exists():
            return "<无日志>"
        text = log_path.read_text(encoding="utf8", errors="replace")
        return text[-limit:].strip()

    def _serve_start(self) -> None:
        """起真实 uvicorn 子进程并轮询 health 直到就绪（内部 30s 超时）。"""
        assert self.tmp_dir is not None
        serve_dir = Path(tempfile.mkdtemp(prefix=SERVE_TEMP_PREFIX)).resolve()
        self.require(
            not serve_dir.is_relative_to(REPO_ROOT),
            f"--serve 临时目录落在仓库内，隔离失效：{serve_dir}",
        )
        self.serve_dir = serve_dir

        # 临时 config.yaml（照卡面写一份；但见下面的环境变量说明，真正生效的是 env）。
        (serve_dir / "config.yaml").write_text(
            f"app:\n  access_token: {TOKEN}\n", encoding="utf8"
        )
        # ALEMBIC_INI / SCRIPT_LOCATION 是 CWD 相对路径（migrate.py 的契约是「调用方
        # cwd = 仓库根」）：--serve 必须用临时 cwd 才能让相对 DB_PATH 落在临时目录，
        # 所以这里把 maa_api 包目录桥接进临时 cwd，让 script_location 相对可解析。
        (serve_dir / "maa_api").symlink_to(REPO_ROOT / "maa_api", target_is_directory=True)

        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            # DEFAULT_CONFIG_PATH 锚在仓库根、与 CWD 无关（M3-03 实测），临时目录里的
            # config.yaml 不会被读；鉴权开关必须靠优先级最高的环境变量层。
            "MAA_APP_ACCESS_TOKEN": TOKEN,
        }
        log_path = serve_dir / "server.log"
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "maa_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.args.port),
        ]
        self.step(f"--serve 启动：{' '.join(command)}（cwd={serve_dir}）")
        with log_path.open("w", encoding="utf8") as log_handle:
            self.proc = subprocess.Popen(
                command,
                cwd=serve_dir,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

        health_url = f"http://127.0.0.1:{self.args.port}/api/system/health"
        deadline = time.monotonic() + SERVE_READY_TIMEOUT
        status: Optional[int] = None
        while time.monotonic() < deadline:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise SmokeFailure(
                    f"uvicorn 提前退出（exit={self.proc.returncode}）；日志：\n{self._serve_log_tail()}"
                )
            status, _, _ = _http_get(health_url, timeout=2.0)
            if status == 200:
                break
            time.sleep(0.25)
        if status != 200:
            raise SmokeFailure(
                f"{SERVE_READY_TIMEOUT:.0f}s 内 {health_url} 未就绪（最后状态 {status}）；"
                f"日志：\n{self._serve_log_tail()}"
            )
        self.step(f"--serve 就绪：{health_url} → 200")

    def _serve_health(self) -> str:
        status, _, body = _http_get(f"http://127.0.0.1:{self.args.port}/api/system/health")
        self.require(status == 200, f"health 状态 {status}")
        payload = json.loads(body)
        self.require(
            payload.get("auth_enabled") is True,
            f"真实进程 auth_enabled 应为 true，实际 {payload.get('auth_enabled')!r}",
        )
        self.require(payload.get("started_at") is not None, "lifespan 未写入 started_at")
        return f"200，auth_enabled=true，version={payload.get('version')!r}"

    def _serve_docs(self) -> str:
        status, _, _ = _http_get(f"http://127.0.0.1:{self.args.port}/docs")
        self.require(status == 200, f"/docs 状态 {status}")
        return "/docs → 200（真实进程）"

    def _serve_unauthorized(self) -> str:
        status, headers, body = _http_get(
            f"http://127.0.0.1:{self.args.port}/api/tasks/types"
        )
        self.require(status == 401, f"无 token 应为 401，实际 {status}")
        self.require(
            headers.get("www-authenticate") == "Bearer",
            f"401 必须带 WWW-Authenticate: Bearer，实际 {headers.get('www-authenticate')!r}",
        )
        code = json.loads(body)["error"]["code"]
        self.require(code == "UNAUTHORIZED", f"错误码应为 UNAUTHORIZED，实际 {code!r}")
        return "无 token → 401 + WWW-Authenticate: Bearer + code=UNAUTHORIZED"

    def _serve_x_token(self) -> str:
        status, _, body = _http_get(
            f"http://127.0.0.1:{self.args.port}/api/tasks/types",
            headers={"X-Token": TOKEN},
        )
        self.require(status == 200, f"X-Token 应为 200，实际 {status}: {body[:200]!r}")
        self.require(
            json.loads(body).get("total") == 9, "带 token 的 /api/tasks/types 应返回 9 个类型"
        )
        return "X-Token → 200（total=9）"

    def _serve_temp_artifacts(self) -> str:
        assert self.serve_dir is not None
        db_path = self.serve_dir / DB_RELATIVE
        self.require(db_path.exists(), f"--serve 临时目录里没有生成库文件：{db_path}")
        entries = sorted(path.name for path in self.serve_dir.iterdir())
        allowed = {"config.yaml", "maa_api", "resource", "server.log", "__pycache__"}
        unexpected = sorted(set(entries) - allowed)
        self.require(
            not unexpected,
            f"--serve 临时目录出现意外产物 {unexpected}（目录内容 {entries}）",
        )
        return f"临时库 {db_path.name} 已生成；临时目录内容 {entries}"

    def _serve_stop(self) -> None:
        """terminate + wait，确认进程真的退出（并记录 [ok] 项）。"""
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=SERVER_STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=SERVER_STOP_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - 收尾失败也必须给出结论
            self.fail(f"--serve 进程收尾异常：{type(exc).__name__}: {exc}")
            return
        self.ok(
            "--serve 进程退出",
            f"terminate 后 exit={proc.returncode}，端口 {self.args.port} 已释放",
        )

    def _run_serve_mode(self) -> None:
        """默认模式之后再跑一段真实 uvicorn（advisory）。"""
        self.step(f"=== --serve 模式：真实 uvicorn + 127.0.0.1:{self.args.port}")
        try:
            self._serve_start()
        except SmokeFailure as exc:
            self.fail(f"--serve 启动：{exc}")
            self._serve_stop()
            return
        except Exception as exc:  # noqa: BLE001 - 启动失败也要收尾
            self.fail(f"--serve 启动：未预期异常 {type(exc).__name__}: {exc}")
            traceback.print_exc()
            self._serve_stop()
            return

        try:
            self._check("--serve health", self._serve_health)
            self._check("--serve /docs", self._serve_docs)
            self._check("--serve 无 token 401", self._serve_unauthorized)
            self._check("--serve X-Token 200", self._serve_x_token)
            self._check("--serve 临时产物", self._serve_temp_artifacts)
        finally:
            self._serve_stop()

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def _dispose_engine(self) -> None:
        """dispose 临时异步引擎（同步接口，不依赖事件循环；失败不掩盖检查结论）。"""
        if self.engine is None:
            return
        with contextlib.suppress(Exception):
            self.engine.sync_engine.dispose()
        self.engine = None

    def _cleanup_tempdirs(self) -> None:
        """退出时清理临时目录（``--keep-temp`` 明确要求保留时不删）。"""
        for attr in ("tmp_dir", "serve_dir"):
            path: Optional[Path] = getattr(self, attr)
            if path is None:
                continue
            if self.args.keep_temp:
                self.step(f"--keep-temp：保留临时目录 {path}")
            else:
                shutil.rmtree(path, ignore_errors=True)
                self.step(f"已清理临时目录：{path}")
            setattr(self, attr, None)

    def _print_checklist(self) -> None:
        total = len(self.checks) + len(self.failures)
        elapsed = time.monotonic() - self.started
        self.step(f"检查清单：{len(self.checks)}/{total} 项通过（耗时 {elapsed:.1f}s）")
        for name, detail in self.checks:
            print(f"  [ok]   {name}：{detail}", flush=True)
        for item in self.failures:
            print(f"  [FAIL] {item}", flush=True)

    def run(self) -> int:
        try:
            try:
                self._prepare_tempdir()
                self._bind_isolated_env()
            except SmokeFailure as exc:
                self.fail(f"准备阶段：{exc}")
            except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须自己收尾并给出结论
                self.fail(f"准备阶段：未预期异常 {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                self._run_default_mode()
                if self.args.serve:
                    self._run_serve_mode()

            # 仓库零写入自查放在最后：默认模式与 --serve 子进程都已经收尾。
            self._check("仓库零残留", self._check_repo_untouched)
        finally:
            self._dispose_engine()
            self._cleanup_tempdirs()

        self._print_checklist()
        if self.failures:
            print(f"[api_smoke] 失败 {len(self.failures)} 项，退出码 1", flush=True)
            return 1
        self.step("全部检查通过")
        print("SMOKE OK", flush=True)
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    """解析参数并跑一次冒烟；``--help`` 在此直接返回，不 import maa_api。"""
    args = build_parser().parse_args(argv)
    try:
        return ApiSmoke(args).run()
    except KeyboardInterrupt:
        print("[api_smoke] 收到中断（Ctrl-C），退出", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
