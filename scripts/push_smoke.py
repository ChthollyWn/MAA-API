#!/usr/bin/env python
"""Web Push 端到端冒烟（M14 Task 5）。

覆盖真实生产路径，而不是重实现一遍逻辑：

1. ``scripts/generate_vapid_keys.py`` 产出的 PEM 写进临时 ``config.yaml`` 后，
   服务端能从私钥导出公钥（``GET /api/notifications/vapid-public-key``）；
2. 浏览器形状的订阅经 REST 登记，同 endpoint 重放是幂等更新；
3. 用 REST 触发一个**真正需要人工确认**的 Agent 工具调用，在没有活跃前端连接时
   服务端发起 Web Push；抓到出站请求并断言 ``aes128gcm`` / VAPID 头，再用**浏览器
   私钥解密**载荷，确认能还原出确认 id 与深链；
4. 推送服务返回 410 时订阅被标记失效，后续不再投递；
5. 有活跃前端连接时不推送（用户就在看页面）。

推送传输用内存假客户端替换，不发真实网络请求；MaaCore 用既有 smoke 的替身工厂，
不加载真实内核。

    .venv/bin/python scripts/push_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMP_PREFIX = "maa-api-push-smoke-"
TOKEN = "push-smoke-token"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/push-smoke-endpoint"


class SmokeFailure(RuntimeError):
    """冒烟断言失败。"""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _check_response(response: Any, expected: int, label: str) -> Any:
    _require(
        response.status_code == expected,
        f"{label} 期望 HTTP {expected}，实际 {response.status_code}：{response.text[:400]}",
    )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _browser_keys() -> tuple[str, str, Any]:
    """模拟浏览器的订阅密钥：保留私钥以便在冒烟里解密推送载荷。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(raw), _b64url(b"\x2a" * 16), private_key


def _decrypt(body: bytes, browser_private: Any, auth_secret: bytes) -> bytes:
    """按 RFC 8291 / 8188 解密单条记录（收端视角，独立于服务端实现）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, rest = body[:16], body[16:]
    record_size = int.from_bytes(rest[:4], "big")
    keyid_len = rest[4]
    server_public = rest[5 : 5 + keyid_len]
    ciphertext = rest[5 + keyid_len :]
    _require(record_size == 4096, f"记录大小应为 4096，实际 {record_size}")

    def extract(s: bytes, ikm: bytes) -> bytes:
        return hmac.new(s, ikm, hashlib.sha256).digest()

    def expand(prk: bytes, info: bytes, length: int) -> bytes:
        out, block, counter = b"", b"", 1
        while len(out) < length:
            block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
            out += block
            counter += 1
        return out[:length]

    ua_public = browser_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
    ecdh = browser_private.exchange(ec.ECDH(), peer)
    ikm = expand(
        extract(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + server_public, 32
    )
    prk = extract(salt, ikm)
    cek = expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = expand(prk, b"Content-Encoding: nonce\x00", 12)
    return AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x02")


class _PushTransport:
    """假推送服务：记录出站请求，按脚本返回状态码。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.statuses: list[int] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        self.calls.append({"method": method, "url": url, **kwargs})
        status = self.statuses.pop(0) if self.statuses else 201
        return SimpleNamespace(status_code=status, text="")


def run(*, keep_temp: bool = False) -> int:
    from scripts.agent_smoke import _snapshot_repo_artifacts

    before_artifacts = _snapshot_repo_artifacts()
    temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
    client = None
    prior: dict[str, Any] = {}
    failures: list[str] = []

    def step(message: str) -> None:
        print(message, flush=True)

    try:
        import maa_api.main as main_module
        import maa_api.settings as settings_module
        from fastapi.testclient import TestClient
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.pool import NullPool

        from maa_api.db import session as db_session
        from maa_api.services import webpush_vapid as vapid
        from scripts.agent_smoke import (
            _SmokeCoreClient,
            _SmokeCoreSupervisor,
            _SmokeDeviceManager,
            _SmokePipelineRunner,
            _snapshot_repo_artifacts,
        )

        db_path = temp_dir / "resource" / "maa_api.db"
        config_path = temp_dir / "config.yaml"
        app_root = temp_dir / "app-root"
        core_path = app_root / "core"
        core_path.mkdir(parents=True)

        # 用真实的密钥生成脚本产出 PEM，再写进 config.yaml：验证「脚本产出 → 配置 →
        # 服务端载入」这条链，而不是在测试里自己造一对密钥。
        private_pem, public_key = vapid.generate_vapid_key_pair()
        indented = "\n".join(f"    {line}" for line in private_pem.strip().splitlines())
        config_path.write_text(
            "app:\n"
            f"  access_token: {TOKEN}\n"
            f"  maa_core_path: {core_path}\n"
            "notify:\n"
            "  vapid_private_key: |\n"
            f"{indented}\n"
            "  vapid_subject: mailto:push-smoke@example.com\n",
            encoding="utf8",
        )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        async_url = f"sqlite+aiosqlite:///{db_path}"
        prior["db"] = (
            db_session.DB_PATH,
            db_session.ASYNC_URL,
            db_session.SYNC_URL,
            db_session.engine,
            db_session.session_factory,
        )
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = async_url
        db_session.SYNC_URL = f"sqlite:///{db_path}"
        engine = db_session.make_engine(async_url, poolclass=NullPool)
        db_session.engine = engine
        db_session.session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

        prior["env"] = {name: os.environ.get(name) for name in settings_module.ENV_OVERRIDES.values()}
        for name in settings_module.ENV_OVERRIDES.values():
            os.environ.pop(name, None)
        prior["default_config"] = settings_module.DEFAULT_CONFIG_PATH
        prior["main_root"] = main_module.REPO_ROOT
        settings_module.DEFAULT_CONFIG_PATH = config_path
        main_module.REPO_ROOT = app_root
        settings_module.set_settings(settings_module.load_settings(config_path))

        prior["state"] = {}
        for name, value in {
            "core_supervisor_factory": _SmokeCoreSupervisor,
            "core_client_factory": _SmokeCoreClient,
            "device_manager_factory": _SmokeDeviceManager,
            "pipeline_runner_factory": _SmokePipelineRunner,
        }.items():
            prior["state"][name] = getattr(main_module.app.state, name, None)
            setattr(main_module.app.state, name, value)

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        headers = {"X-Token": TOKEN}

        # --- 1. VAPID 公钥由配置的私钥导出 -------------------------------
        body = _check_response(
            client.get("/api/notifications/vapid-public-key", headers=headers),
            200,
            "VAPID 公钥",
        )
        _require(body["public_key"] == public_key, "服务端公钥与生成脚本产出不一致")
        _require(body["subject"] == "mailto:push-smoke@example.com", "VAPID subject 未生效")
        step("[ok] config.yaml 的 VAPID 私钥可被载入并导出同一把公钥")

        # --- 2. 订阅登记与幂等 -------------------------------------------
        p256dh, auth, browser_private = _browser_keys()
        payload = {"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": auth}}
        created = client.post(
            "/api/notifications/push-subscriptions",
            json=payload,
            headers={**headers, "User-Agent": "push-smoke/1.0"},
        )
        created_body = _check_response(created, 201, "订阅登记")
        _require(
            created.headers.get("Location", "").endswith(created_body["id"]),
            "订阅登记缺少 Location",
        )
        repeated = client.post("/api/notifications/push-subscriptions", json=payload, headers=headers)
        _check_response(repeated, 200, "订阅重放")
        listing = _check_response(
            client.get("/api/notifications/push-subscriptions", headers=headers), 200, "订阅列表"
        )
        _require(listing["total"] == 1, f"幂等登记应只有一行，实际 {listing['total']}")
        _require(ENDPOINT not in json.dumps(listing), "列表回显了 endpoint 全文")
        _require(p256dh not in json.dumps(listing), "列表回显了 p256dh")
        step("[ok] 订阅登记 201、同 endpoint 重放 200 且列表不回显凭据字段")

        # --- 3. 触发真实确认并抓到推送 -----------------------------------
        transport = _PushTransport()
        main_module.app.state.webpush_service._http = transport

        invoke = client.post(
            "/api/agent/tools/click/invoke",
            json={"arguments": {"x": 100, "y": 200}, "mode": "async"},
            headers=headers,
        )
        invoke_body = _check_response(invoke, 202, "点击工具异步调用")
        confirmation_id = invoke_body.get("confirmation_id") or invoke_body.get("confirmation", {}).get(
            "confirmation_id"
        )
        _require(bool(confirmation_id), f"未创建待确认：{invoke_body!r}")

        _require(len(transport.calls) == 1, f"应发出 1 条推送，实际 {len(transport.calls)}")
        call = transport.calls[0]
        _require(call["method"] == "POST" and call["url"] == ENDPOINT, "推送目标不正确")
        call_headers = call["headers"]
        _require(call_headers["Content-Encoding"] == "aes128gcm", "缺少 aes128gcm 内容编码")
        _require(call_headers["TTL"] == "600", "缺少 TTL")
        _require(call_headers["Authorization"].startswith("vapid t="), "缺少 VAPID 授权头")
        _require(f", k={public_key}" in call_headers["Authorization"], "VAPID 公钥不匹配")

        decrypted = json.loads(_decrypt(call["content"], browser_private, _b64url_decode(auth)))
        _require(
            decrypted["data"]["confirmationId"] == confirmation_id,
            f"推送载荷的 confirmationId 不匹配：{decrypted!r}",
        )
        _require(
            decrypted["data"]["url"] == f"/?confirm={confirmation_id}",
            f"推送载荷缺少深链：{decrypted!r}",
        )
        _require(decrypted["tag"] == f"confirm:{confirmation_id}", "推送未用 tag 做同类合并")
        _require("需要确认" in decrypted["title"], f"通知标题未写清操作：{decrypted['title']!r}")
        _require(
            [action["action"] for action in decrypted["actions"]] == ["approve", "reject"],
            "Chromium 的 action 按钮缺失",
        )
        _require(
            TOKEN not in json.dumps(decrypted) and "token" not in json.dumps(decrypted).lower(),
            "推送载荷泄露了凭据",
        )
        step("[ok] 确认请求经 RFC 8291 加密投递，浏览器私钥可解密出确认 id、深链与按钮")

        # 处置掉这条确认，避免留下一个等待中的 worker
        _check_response(
            client.post(
                f"/api/confirmations/{confirmation_id}",
                json={"approved": False},
                headers=headers,
            ),
            200,
            "拒绝确认",
        )

        # --- 4. 有活跃前端连接时不推送 ------------------------------------
        class _ConnectedManager:
            """只用于这一步：让 ws_manager.sessions 非空。"""

            sessions = (object(),)

        original_ws_manager = main_module.app.state.confirmation_service.has_active_client
        main_module.app.state.confirmation_service.has_active_client = lambda: True
        before = len(transport.calls)
        second = client.post(
            "/api/agent/tools/click/invoke",
            json={"arguments": {"x": 101, "y": 201}, "mode": "async"},
            headers=headers,
        )
        _check_response(second, 202, "第二次点击调用")
        _require(
            len(transport.calls) == before,
            "存在活跃前端连接时不应推送（用户就在看页面）",
        )
        main_module.app.state.confirmation_service.has_active_client = original_ws_manager
        step("[ok] 存在活跃前端连接时跳过推送")

        # --- 5. 410 停用订阅 ---------------------------------------------
        transport.statuses = [410]
        third = client.post(
            "/api/agent/tools/click/invoke",
            json={"arguments": {"x": 102, "y": 202}, "mode": "async"},
            headers=headers,
        )
        _check_response(third, 202, "第三次点击调用")
        _require(len(transport.calls) == before + 1, "410 那次应当仍然尝试投递")
        disabled = _check_response(
            client.get(
                "/api/notifications/push-subscriptions",
                params={"include_disabled": True},
                headers=headers,
            ),
            200,
            "含失效的订阅列表",
        )
        _require(
            all(item["disabled"] for item in disabled["items"]),
            f"410 之后订阅应标记失效：{disabled!r}",
        )
        active_listing = _check_response(
            client.get("/api/notifications/push-subscriptions", headers=headers), 200, "有效订阅列表"
        )
        _require(active_listing["total"] == 0, "失效订阅不应出现在默认列表里")
        step("[ok] 推送服务返回 410 后订阅被标记失效且不再出现在有效列表")

        # --- 6. 按 endpoint 注销 ------------------------------------------
        # 重新订阅一台设备，再用 POST delete 精确注销它。
        client.post("/api/notifications/push-subscriptions", json=payload, headers=headers)
        _check_response(
            client.post(
                "/api/notifications/push-subscriptions/delete",
                json={"endpoint": ENDPOINT},
                headers=headers,
            ),
            204,
            "按 endpoint 注销",
        )
        after = _check_response(
            client.get(
                "/api/notifications/push-subscriptions",
                params={"include_disabled": True},
                headers=headers,
            ),
            200,
            "注销后的订阅列表",
        )
        _require(after["total"] == 0, f"按 endpoint 注销后应无订阅：{after!r}")
        step("[ok] 按 endpoint 精确注销（不影响同一推送服务下的其它设备）")

        after_artifacts = _snapshot_repo_artifacts()
        _require(
            after_artifacts == before_artifacts,
            "冒烟改动了仓库内的 DB/config 产物："
            f"{sorted(set(after_artifacts.items()) ^ set(before_artifacts.items()))}",
        )
        step("[ok] 临时 SQLite 隔离；仓库 DB/config 指纹未改变")

        print("SMOKE OK", flush=True)
        return 0
    except SmokeFailure as exc:
        failures.append(str(exc))
        print(f"[push_smoke] 失败：{exc}", flush=True)
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须自己收尾并给出结论
        failures.append(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

        import maa_api.main as main_module
        import maa_api.settings as settings_module

        if prior.get("state"):
            for name, value in prior["state"].items():
                setattr(main_module.app.state, name, value)
        if prior.get("default_config") is not None:
            settings_module.DEFAULT_CONFIG_PATH = prior["default_config"]
        if prior.get("main_root") is not None:
            main_module.REPO_ROOT = prior["main_root"]
        settings_module.set_settings(None)
        for name, value in (prior.get("env") or {}).items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if prior.get("db"):
            (
                db_session.DB_PATH,
                db_session.ASYNC_URL,
                db_session.SYNC_URL,
                db_session.engine,
                db_session.session_factory,
            ) = prior["db"]
        if keep_temp:
            print(f"--keep-temp：保留 {temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"[push_smoke] 失败 {len(failures)} 项，退出码 1", flush=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Web Push 端到端冒烟")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时目录")
    args = parser.parse_args(argv)
    return run(keep_temp=args.keep_temp)


if __name__ == "__main__":
    raise SystemExit(main())
