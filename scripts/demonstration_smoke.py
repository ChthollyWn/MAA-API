#!/usr/bin/env python3
"""示教端到端冒烟：开始、采集五个动作、停止、预览、注册与提交 ``Custom``。

使用生产 ``create_app()`` 装配与应用 lifespan、真实 REST routers、真实
``DemonstrationService``、``ResourceService`` 与临时 SQLite。MaaCore、ADB 与外部
网络不启动：``install_fake_lifespan`` 提供内核与设备边界，本脚本只把设备工厂换成
``SmokeDemoDevice``（在 ``tests.fakes.lifespan.SmokeDeviceManager`` 上补截图、
分辨率与滑动/长按记录）。

成功时最后一行严格为 ``SMOKE OK``；失败输出 ``[FAIL]`` 并返回非零。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOKEN = "m89-demonstration-smoke-token"
TEMP_PREFIX = "maa-api-demonstration-smoke-"

CLICKS = ((320, 240), (640, 360), (960, 480))
SWIPE = (200, 300, 1000, 300, 450)
LONG_PRESS = (640, 560, 900)


class SmokeFailure(RuntimeError):
    """An acceptance assertion failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _check(response: Any, status_code: int, label: str) -> dict[str, Any]:
    _require(
        response.status_code == status_code,
        f"{label} 应返回 {status_code}，实际 {response.status_code}: {response.text[:400]}",
    )
    return response.json() if response.content else {}


def _headers() -> dict[str, str]:
    return {"X-Token": TOKEN}


def _count_screenshot_rows() -> int:
    async def count() -> int:
        from sqlalchemy import func, select

        from maa_api.db import session as db_session
        from maa_api.db.models import Screenshot

        async with db_session.session_factory() as session:
            result = await session.execute(select(func.count()).select_from(Screenshot))
            return int(result.scalar_one())

    return asyncio.run(count())


def _wait_for_custom_task(core_client: Any, entry: str, *, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for name, args, _kwargs in core_client.asst.calls_of("append_task"):
            del name
            if args and args[0] == "Custom":
                params = args[1] if len(args) > 1 else {}
                if isinstance(params, dict):
                    return params
        time.sleep(0.05)
    raise SmokeFailure(f"假内核没有在 {timeout:.0f}s 内收到 Custom 任务（入口 {entry}）")


def run(*, keep_temp: bool = False) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
    failure: BaseException | None = None
    client = None

    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool

    from maa_api.db import session as db_session
    from maa_api.services.device_service import ScreenshotResult
    from tests.fakes.lifespan import (
        FAKE_LIFESPAN_STATE_NAMES,
        SmokeDeviceManager,
        install_fake_lifespan,
    )

    class SmokeDemoDevice(SmokeDeviceManager):
        """永远「已连接、1280×720」，并记录滑动与长按的假设备。"""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.swipes: list[tuple[int, int, int, int, int]] = []
            self.long_presses: list[tuple[int, int, int]] = []

        def snapshot(self) -> dict[str, Any]:
            return {
                **super().snapshot(),
                "state": "connected",
                "resolution": {"width": 1280, "height": 720},
            }

        async def screenshot(self, backend: str = "adb") -> ScreenshotResult:
            del backend
            return ScreenshotResult(Image.new("RGB", (1280, 720), "white"), "adb")

        async def swipe(
            self, x1: int, y1: int, x2: int, y2: int, duration_ms: int
        ) -> None:
            self.swipes.append((x1, y1, x2, y2, duration_ms))

        async def long_press(self, x: int, y: int, duration_ms: int) -> None:
            self.long_presses.append((x, y, duration_ms))

    db_path = temp_dir / "resource" / "maa_api.db"
    config_path = temp_dir / "config.yaml"
    app_root = temp_dir / "app-root"
    core_path = app_root / "core"
    core_path.mkdir(parents=True)
    app_root.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "app:\n"
        f"  access_token: {TOKEN}\n"
        f"  maa_core_path: {core_path}\n"
        "llm:\n"
        "  base_url: https://llm.invalid/v1\n"
        "  api_key: smoke-only\n"
        "  model: smoke-model\n",
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
        main_module.app.state.device_manager_factory = SmokeDemoDevice

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()

        device = main_module.app.state.device_manager
        lease = main_module.app.state.device_lease_service
        core_client = main_module.app.state.core_client
        _require(isinstance(device, SmokeDemoDevice), "设备工厂没有被替换成 SmokeDemoDevice")

        # 1. 开始录制：201、recording、screen、闸门占用。
        started = _check(
            client.post("/api/agent/demonstrations", json={}, headers=_headers()),
            201,
            "开始示教",
        )
        demonstration_id = started.get("id")
        _require(isinstance(demonstration_id, str) and demonstration_id, f"开始响应缺少 id：{started!r}")
        _require(started.get("status") == "recording", f"开始后状态应为 recording：{started!r}")
        _require(started.get("screen") == [1280, 720], f"screen 应为 [1280, 720]：{started!r}")
        _require(lease.recording() is True, "开始示教后设备闸门没有占用")
        print("[ok] 开始示教：201、recording、screen=[1280,720]、设备闸门占用")

        # 2. 三次点击、一次滑动、一次长按：五步、十个截图 id、时区与截图行数。
        for x, y in CLICKS:
            _check(
                client.post(
                    "/api/device/click",
                    json={"x": x, "y": y, "force": False},
                    headers=_headers(),
                ),
                200,
                f"点击 ({x}, {y})",
            )
        _check(
            client.post(
                "/api/device/swipe",
                json={
                    "x1": SWIPE[0],
                    "y1": SWIPE[1],
                    "x2": SWIPE[2],
                    "y2": SWIPE[3],
                    "duration_ms": SWIPE[4],
                    "force": False,
                },
                headers=_headers(),
            ),
            200,
            "滑动",
        )
        _check(
            client.post(
                "/api/device/long_press",
                json={"x": LONG_PRESS[0], "y": LONG_PRESS[1], "duration_ms": LONG_PRESS[2], "force": False},
                headers=_headers(),
            ),
            200,
            "长按",
        )
        _require(device.clicks == list(CLICKS), f"设备点击记录不一致：{device.clicks!r}")
        _require(device.swipes == [SWIPE], f"设备滑动记录不一致：{device.swipes!r}")
        _require(device.long_presses == [LONG_PRESS], f"设备长按记录不一致：{device.long_presses!r}")

        page = _check(
            client.get(
                "/api/agent/demonstrations",
                params={"page": 1, "size": 20},
                headers=_headers(),
            ),
            200,
            "示教列表",
        )
        rows = [item for item in page.get("items", []) if item.get("id") == demonstration_id]
        _require(len(rows) == 1, f"列表里没有本次示教：{page!r}")
        steps = rows[0].get("steps") or []
        _require(len(steps) == 5, f"应有 5 条步骤，实际 {len(steps)}：{steps!r}")
        for step in steps:
            _require(
                isinstance(step.get("screenshot_id"), str) and step["screenshot_id"]
                and isinstance(step.get("result_screenshot_id"), str) and step["result_screenshot_id"],
                f"步骤缺少截图 id：{step!r}",
            )
            at = step.get("at")
            _require(isinstance(at, str) and at.endswith("Z"), f"步骤 at 应以 Z 结尾：{step!r}")
            try:
                datetime.fromisoformat(at)
            except ValueError as exc:
                raise SmokeFailure(f"步骤 at 不能解析：{at}") from exc
        screenshot_rows = _count_screenshot_rows()
        _require(screenshot_rows == 10, f"应有 10 行 screenshot，实际 {screenshot_rows}")
        print("[ok] 采集：3 次点击 + 1 次滑动 + 1 次长按 = 5 步、10 张截图、at 为 UTC")

        # 3. 停止：recorded、闸门释放。
        stopped = _check(
            client.post(
                f"/api/agent/demonstrations/{demonstration_id}/stop",
                json={"discard": False},
                headers=_headers(),
            ),
            200,
            "停止示教",
        )
        _require(stopped.get("status") == "recorded", f"停止后状态应为 recorded：{stopped!r}")
        _require(lease.recording() is False, "停止示教后设备闸门没有释放")
        print("[ok] 停止示教：recorded，设备闸门释放")

        # 4. 预览：valid、chain 六节点、状态不变。
        preview = _check(
            client.post(
                f"/api/agent/demonstrations/{demonstration_id}/convert",
                json={"register": False},
                headers=_headers(),
            ),
            200,
            "转换预览",
        )
        compile_result = preview.get("compile") or {}
        _require(compile_result.get("valid") is True, f"预览应校验通过：{preview!r}")
        _require(
            len(compile_result.get("chain") or []) == 6,
            f"chain 应有 6 个节点（5 步 + Done）：{compile_result.get('chain')!r}",
        )
        _require(preview.get("status") == "recorded", f"预览不应改状态：{preview!r}")
        print("[ok] 转换预览：valid=true、chain 6 个节点、状态仍为 recorded")

        # 5. 注册：converted、入口前缀与 tasks.json。
        registered = _check(
            client.post(
                f"/api/agent/demonstrations/{demonstration_id}/convert",
                json={"register": True},
                headers=_headers(),
            ),
            200,
            "注册示教任务",
        )
        entry = str((registered.get("converted_to") or {}).get("entry") or "")
        _require(registered.get("status") == "converted", f"注册后状态应为 converted：{registered!r}")
        _require(entry.startswith("Custom_Demo_"), f"入口应以 Custom_Demo_ 开头：{entry!r}")
        tasks_file = (
            app_root / "resource" / "maa-layers" / "custom" / "resource" / "tasks.json"
        )
        _require(tasks_file.is_file(), f"注册后没有写出 {tasks_file}")
        tasks_payload = json.loads(tasks_file.read_text(encoding="utf8"))
        _require(entry in tasks_payload, f"tasks.json 缺少入口 {entry}")
        _require(
            any(name.endswith("_Done") for name in tasks_payload),
            f"tasks.json 缺少 _Done 出口：{sorted(tasks_payload)}",
        )
        print("[ok] 注册：converted、converted_to.entry、tasks.json 含入口与 _Done")

        # 6. 提交 Custom 流水线：假内核收到一次 Custom 任务。
        submitted = _check(
            client.post(
                "/api/pipelines",
                json={
                    "title": "M89 示教冒烟",
                    "tasks": [{"name": "Custom", "task_names": [entry]}],
                },
                headers=_headers(),
            ),
            202,
            "提交 Custom 流水线",
        )
        params = _wait_for_custom_task(core_client, entry)
        _require(
            params.get("task_names") == [entry],
            f"假内核收到的 Custom 参数不一致：{params!r}",
        )
        _require(
            isinstance(submitted.get("pipeline_id"), str) and submitted["pipeline_id"],
            f"提交响应缺少 pipeline_id：{submitted!r}",
        )
        print("[ok] 提交流水线：假内核收到一次 Custom 任务")
    except BaseException as exc:
        failure = exc
        print(f"[demonstration_smoke][FAIL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                print(f"[demonstration_smoke][FAIL] 退出 app lifespan 异常：{type(exc).__name__}: {exc}", flush=True)

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

        if keep_temp or failure is not None:
            print(f"[demonstration_smoke] 临时现场：{temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if failure is not None:
        raise SmokeFailure(str(failure)) from failure


def build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="demonstration_smoke.py",
        description="M89 示教开始、采集、停止、预览、注册与 Custom 流水线硬验收。",
    )
    parser.add_argument("--keep-temp", action="store_true", help="通过时保留临时库与资源目录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run(keep_temp=args.keep_temp)
    except SmokeFailure as exc:
        print(f"[demonstration_smoke][FAIL] {exc}", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
