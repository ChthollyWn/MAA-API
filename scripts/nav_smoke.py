#!/usr/bin/env python3
"""M86 界面导航图端到端冒烟：种子、识别、导航、失败停用与 remember_screen。

用生产 ``create_app()`` 装配与应用 lifespan、真实 SQLite 与工具注册表，MaaCore、ADB
与外部网络都不启动；测试用生命周期适配器只替代内核与设备边界。导航服务照启动装配
取，再把 ``device_factory`` 换成 ``FakeNavDevice``，让三个工具走真实的图服务、执行器
与识别器。

全部读写都落在临时目录：数据库、配置与归档截图。成功时最后一行严格为 ``SMOKE OK``；
断言失败打印 ``[FAIL]`` 并返回 1。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fakes.lifespan import FAKE_LIFESPAN_STATE_NAMES, install_fake_lifespan
from tests.fakes.nav import FakeNavDevice

TEMP_PREFIX = "maa-api-nav-smoke-"
TOKEN = "m86-nav-smoke-token"

#: 首页入口「基建」与返回键在 1280×720 下的设备像素（种子坐标 §4.5.6）。
TAP_INFRAST = "tap:1045,630"
TAP_BACK = "tap:90,40"


class SmokeFailure(RuntimeError):
    """An acceptance assertion failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _repo_artifacts() -> dict[str, tuple[int, int] | None]:
    """仓库里不允许被冒烟读写的两个文件（大小与 mtime）。"""
    artifacts: dict[str, tuple[int, int] | None] = {}
    for relative in ("config.yaml", "resource/maa_api.db"):
        path = REPO_ROOT / relative
        if path.is_file():
            stat = path.stat()
            artifacts[relative] = (stat.st_size, stat.st_mtime_ns)
        else:
            artifacts[relative] = None
    return artifacts


async def _edge(service: Any, from_node: str, to_node: str) -> Any:
    """按起点与终点取一条跳转；种子图里每对界面只有一条。"""
    _nodes, edges = await service.graph()
    for edge in edges:
        if edge.from_node == from_node and edge.to_node == to_node:
            return edge
    raise SmokeFailure(f"导航图里没有 {from_node} → {to_node} 的跳转")


async def _check_navigation(app: Any) -> None:
    """在应用事件循环里依次跑完七段断言。"""
    from maa_api.agent.registry import ToolContext
    from maa_api.agent.tools import build_registry
    from maa_api.db import session as db_session
    from maa_api.domain.enums import CallerType
    from maa_api.domain.errors import AppError, ErrorCode

    service = app.state.nav_service
    # 冒烟不连外部 LLM：去掉视觉依赖，where_am_i 直接走「认不出」分支，也不触发探查。
    app.state.model_router = None

    # 1. 启动已经写过种子；再调一次确认幂等，节点 15、边 36。
    await service.ensure_seed()
    nodes, edges = await service.graph()
    _require(len(nodes) == 15, f"种子节点应为 15，实际 {len(nodes)}")
    _require(len(edges) == 36, f"种子边应为 36，实际 {len(edges)}")
    print("[ok] 种子图：15 个界面、36 条跳转")

    # 2. 注入假设备：首页点「基建」到基建，返回键回首页；其余点击不改变界面。
    device = FakeNavDevice(
        transitions={TAP_INFRAST: "infrast", TAP_BACK: "home"}, start="home"
    )
    service.device_factory = lambda _context: device
    registry = build_registry()

    async with db_session.session_factory() as session:
        context = ToolContext(
            caller=CallerType.INTERNAL,
            session_id="nav-smoke-session",
            request_id="nav-smoke",
            request=SimpleNamespace(app=app),
            db_session=session,
        )

        # 3. 首次 where_am_i：图里还没有任何样本，认不出当前画面。
        state = await registry.execute("where_am_i", {}, context)
        _require(state["node"] is None, f"首次识别应没有结论，实际 {state['node']!r}")
        _require(state["method"] == "none", f"首次识别 method 应为 none，实际 {state['method']!r}")
        print("[ok] 首次 where_am_i 返回未知画面")

        # 4. 导航到基建：先回首页（计一步）再点入口，共两步；成功计数入账。
        result = await registry.execute("navigate_to", {"target": "infrast"}, context)
        _require(result["arrived"] is True, "navigate_to 没有到达基建")
        _require(result["steps"] == 2, f"navigate_to 应为 2 步，实际 {result['steps']}")
        _require(
            result["trail"] and result["trail"][0]["action"] == "home",
            f"第一步应是回首页，实际 {result['trail']!r}",
        )
        home = await service.get_node("home")
        infrast = await service.get_node("infrast")
        _require(bool(home and home.exemplars), "回首页后 home 应该有一个样本")
        _require(bool(infrast and infrast.exemplars), "首次到达后 infrast 应该有一个样本")
        edge = await _edge(service, "home", "infrast")
        _require(edge.success_count == 1, f"home→infrast 成功计数应为 1，实际 {edge.success_count}")
        print("[ok] navigate_to 两步到达基建，样本与成功计数落库")

        # 5. 再识别一次：基建已有样本，哈希直接命中。
        state = await registry.execute("where_am_i", {}, context)
        _require(state["node"] is not None, f"基建上应认出画面，实际 {state['node']!r}")
        _require(state["node"]["id"] == "infrast", f"应认出 infrast，实际 {state['node']!r}")
        _require(state["method"] == "hash", f"应走哈希识别，实际 {state['method']!r}")
        print("[ok] 基建上 where_am_i 命中哈希")

        # 6. 公开招募的点击键不在假设备里：三次都走不通，第三次后该边自动停用。
        for attempt in range(1, 4):
            try:
                await registry.execute("navigate_to", {"target": "recruit"}, context)
            except AppError as exc:
                _require(
                    exc.code == ErrorCode.NAV_PATH_NOT_FOUND,
                    f"第 {attempt} 次导航应报 NAV_PATH_NOT_FOUND，实际 {exc.code}",
                )
            else:
                raise SmokeFailure(f"第 {attempt} 次导航到公开招募不应该成功")
        edge = await _edge(service, "home", "recruit")
        _require(edge.enabled is False, "home→recruit 连续失败三次后应自动停用")
        _require(edge.fail_count >= 3, f"home→recruit 失败计数应 ≥ 3，实际 {edge.fail_count}")
        print("[ok] home→recruit 三次失败后自动停用")

        # 7. 停在 1-7 关卡界面，把它记成 stage:1-7，节点来源 learned、样本来源 agent。
        device.screen = "stage:1-7"
        remembered = await registry.execute(
            "remember_screen", {"node_id": "stage:1-7", "title": "1-7"}, context
        )
        _require(
            remembered["node"] is not None and remembered["node"]["created"] is True,
            f"remember_screen 应新建节点，实际 {remembered!r}",
        )
        _require(remembered["exemplar_added"] is True, "remember_screen 应加一个样本")
        learned = await service.get_node("stage:1-7")
        _require(learned is not None, "remember_screen 之后图里应该有 stage:1-7")
        _require(learned.origin == "learned", f"新节点来源应为 learned，实际 {learned.origin!r}")
        _require(
            bool(learned.exemplars) and learned.exemplars[0]["source"] == "agent",
            f"样本来源应为 agent，实际 {learned.exemplars!r}",
        )
        print("[ok] remember_screen 新建 stage:1-7 与 agent 样本")


def run(*, keep_temp: bool = False) -> None:
    before = _repo_artifacts()
    temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
    failure: BaseException | None = None
    client = None

    # All app-owned paths point outside the checkout before the real lifespan starts.
    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool

    from maa_api.db import session as db_session

    db_path = temp_dir / "resource" / "maa_api.db"
    config_path = temp_dir / "config.yaml"
    app_root = temp_dir / "app-root"
    core_path = app_root / "core"
    resource_path = app_root / "resource"
    core_path.mkdir(parents=True)
    app_root.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "app:\n"
        f"  access_token: {TOKEN}\n"
        f"  maa_core_path: {core_path}\n"
        "llm:\n"
        "  base_url: https://llm.invalid/v1\n"
        "  api_key: smoke-only\n"
        "  model: smoke-model\n"
        "nav:\n"
        "  enabled: true\n"
        "  learn: true\n"
        "  max_steps: 12\n",
        encoding="utf8",
    )

    prior_db = (
        db_session.DB_PATH,
        db_session.ASYNC_URL,
        db_session.SYNC_URL,
        db_session.engine,
        db_session.session_factory,
    )
    old_default_config = settings_module.DEFAULT_CONFIG_PATH
    old_main_root = main_module.REPO_ROOT
    old_state: dict[str, Any] = {}
    old_env: dict[str, str | None] = {}
    temp_engine = None

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async_url = f"sqlite+aiosqlite:///{db_path}"
        sync_url = f"sqlite:///{db_path}"
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = async_url
        db_session.SYNC_URL = sync_url
        temp_engine = db_session.make_engine(async_url, poolclass=NullPool)
        db_session.engine = temp_engine
        db_session.session_factory = async_sessionmaker(
            temp_engine, expire_on_commit=False, class_=AsyncSession
        )

        for env_name in settings_module.ENV_OVERRIDES.values():
            old_env[env_name] = os.environ.get(env_name)
            os.environ.pop(env_name, None)
        settings_module.DEFAULT_CONFIG_PATH = config_path
        main_module.REPO_ROOT = app_root
        settings_module.set_settings(settings_module.load_settings(config_path))

        for name in FAKE_LIFESPAN_STATE_NAMES:
            old_state[name] = getattr(main_module.app.state, name, None)
        install_fake_lifespan(main_module.app)

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        client.portal.call(_check_navigation, main_module.app)
    except BaseException as exc:
        failure = exc
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                print(f"[nav_smoke] 退出 app lifespan 异常：{type(exc).__name__}: {exc}", flush=True)

        if temp_engine is not None:
            try:
                temp_engine.sync_engine.dispose()
            except Exception:
                pass
        (
            db_session.DB_PATH,
            db_session.ASYNC_URL,
            db_session.SYNC_URL,
            db_session.engine,
            db_session.session_factory,
        ) = prior_db
        settings_module.DEFAULT_CONFIG_PATH = old_default_config
        settings_module.set_settings(None)
        main_module.REPO_ROOT = old_main_root
        for name, previous in old_state.items():
            if previous is None:
                if hasattr(main_module.app.state, name):
                    delattr(main_module.app.state, name)
            else:
                setattr(main_module.app.state, name, previous)
        for env_name, value in old_env.items():
            if value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = value

        after = _repo_artifacts()
        if before != after:
            failure = failure or SmokeFailure("冒烟退出时仓库 DB/config 指纹发生变化")
            print(f"[nav_smoke] 仓库 DB/config 产物发生变化：{after!r}", flush=True)
        if keep_temp or failure is not None:
            print(f"[nav_smoke] 临时现场：{temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if failure is not None:
        raise SmokeFailure(str(failure)) from failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nav_smoke.py",
        description="M86 界面导航图的种子、识别、导航、失败停用与 remember_screen 冒烟。",
    )
    parser.add_argument("--keep-temp", action="store_true", help="通过时保留临时库与资源目录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run(keep_temp=args.keep_temp)
    except SmokeFailure as exc:
        print(f"[FAIL] {exc}", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
