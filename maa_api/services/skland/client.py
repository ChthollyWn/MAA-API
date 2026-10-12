"""森空岛 / 鹰角签名 HTTP 客户端（M42，规格 §2.2、§4.3）。

三条硬口径：

1. **签名**（规格 §2.2，逐字照抄，改动即失效）::

       timestamp = int(now()) - 1
       secret    = path + query_params + str(timestamp) + json.dumps(header_for_sign, separators=(",", ":"))
       sign      = md5(hmac_sha256(token, secret).hexdigest()).hexdigest()

   GET 的 ``query_params`` 是 URL 查询串（没有查询串就是空串），POST 的是 body 的紧凑
   JSON；``header_for_sign`` 是 ``extra_headers`` 加 ``timestamp``。签名用的那份头会**原样
   并进实际发送的头**，不存在「签一份、发另一份」的可能。``platform`` / ``vName`` 的取值
   是可注入的（``extra_headers``），W0 真机要以实测值覆盖（规格 §2.5-10）。
2. **两侧错误形状不同**：鹰角侧（``as.hypergryph.com``）HTTP 恒 200、成败在 body 的
   ``status``；zonai 侧（``zonai.skland.com``）失败是 ``code != 0``（未登录为 HTTP 401 +
   ``code=10002``）。归一分别见 :meth:`SklandClient._hypergryph` / :meth:`SklandClient._zonai`。
3. **日志不写响应正文**：只记端点、状态码、耗时、重试次数与错误码；``cred`` / ``token`` /
   ``sign`` 一律不进日志（口径同 :mod:`maa_api.services.external.http`）。

dId 设备指纹只被签到类接口需要，本模块只读账号数据，不碰（规格 §3.2）。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import httpx

from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "AS_BASE_URL",
    "DEFAULT_EXTRA_HEADERS",
    "MAX_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "SKLAND_APP_CODE",
    "USER_AGENT",
    "ZONAI_BASE_URL",
    "BindingRole",
    "ScanLogin",
    "SklandClient",
    "SklandCredential",
]

logger = logging.getLogger(__name__)

#: 森空岛在鹰角通行证里的 appCode（规格 §2.1 实测）。
SKLAND_APP_CODE = "4ca99fa6b56cc2ba"

#: 鹰角通行证入口（扫码、令牌、grant）。
AS_BASE_URL = "https://as.hypergryph.com"

#: 森空岛入口（凭据、绑定、账号数据）。
ZONAI_BASE_URL = "https://zonai.skland.com"

#: 进签名的固定头；缺省值照抄社区实现，**可注入**，W0 真机以实测值覆盖（规格 §2.5-10）。
DEFAULT_EXTRA_HEADERS: Mapping[str, str] = {"platform": "", "dId": "", "vName": ""}
#: 进签名的头的**固定顺序**（照抄社区现行实现）：``timestamp`` 插在 ``platform`` 之后。
#: 待签串是 JSON，键序进 HMAC——顺序错、键少一个（早期版本漏了 ``dId``）都会让服务端
#: 复核失败：真机实测（2026-10-12 11:59:03Z）表现为 ``binding`` 回 401。

#: 网络异常与 5xx 的退避秒数；测试 monkeypatch 成 0 免等。
RETRY_BACKOFF_SECONDS = 0.5

#: 单次请求的总尝试次数：只重试一次，业务码不重试（规格 §4.3）。
MAX_ATTEMPTS = 2

#: 读不到分发元数据时的回退版本（与 `pyproject.toml`、`system.health` 的取法一致）。
_FALLBACK_VERSION = "0.1.0"

# -- 端点路径（签名里的 path 就是这里的字符串） -------------------------------
_GEN_SCAN_LOGIN_PATH = "/general/v1/gen_scan/login"
_SCAN_STATUS_PATH = "/general/v1/scan_status"
_TOKEN_BY_SCAN_CODE_PATH = "/user/auth/v1/token_by_scan_code"
_GRANT_PATH = "/user/oauth2/v2/grant"
_GENERATE_CRED_PATH = "/api/v1/user/auth/generate_cred_by_code"
_REFRESH_PATH = "/api/v1/auth/refresh"
_BINDING_PATH = "/api/v1/game/player/binding"
_PLAYER_INFO_PATH = "/api/v1/game/player/info"

#: `bindingList` 里只认明日方舟（规格 §2.2 第 6 步）。
_BINDING_APP_CODE = "arknights"


def _app_version() -> str:
    """应用版本；只读 `importlib.metadata`，不 import 主模块（避免循环依赖）。"""
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as distribution_version

        try:
            return distribution_version("MAA-API")
        except PackageNotFoundError:
            return _FALLBACK_VERSION
    except Exception:  # noqa: BLE001 - 版本只影响 UA，取不到不能拖垮出站
        return _FALLBACK_VERSION


#: 与 :data:`maa_api.services.external.http.USER_AGENT` 同一套取法（不 import 它的私有件）。
USER_AGENT = f"MAA-API/{_app_version()} (+https://github.com/ChthollyWn/MAA-API)"


@dataclass(frozen=True, slots=True)
class SklandCredential:
    """森空岛凭据：``cred`` 是账号凭证（发送头），``token`` 是签名密钥。"""

    cred: str
    token: str


@dataclass(frozen=True, slots=True)
class ScanLogin:
    """一次扫码会话：``scan_url`` 原样取自上游（不自己拼 ``hypergryph://``）。"""

    scan_id: str
    scan_url: str


@dataclass(frozen=True, slots=True)
class BindingRole:
    """`bindingList` 里的一个明日方舟角色；除 ``uid`` / ``app_code`` 外都可能缺省。"""

    uid: str
    app_code: str
    channel_master_id: str | None
    nickname: str | None
    level: int | None
    is_default: bool


class SklandClient:
    """带签名的森空岛取数门面（只读）。

    可注入点全给测试用：``transport``（`httpx.MockTransport`）、``now``（签名时间戳）、
    ``extra_headers``（`platform` / `vName` 等进签名的头）。

    :raises AppError: `SKLAND_UPSTREAM_FAILED`（鹰角侧 ``status != 0``、zonai 侧其它
        ``code != 0``、网络异常、5xx、响应形状不符）、`SKLAND_LOGIN_FAILED`（zonai 侧
        ``code == 10002`` 或 HTTP 401/403）。
    """

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], float] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._now = now if now is not None else time.time
        self._extra_headers: dict[str, str] = dict(
            DEFAULT_EXTRA_HEADERS if extra_headers is None else extra_headers
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={
                "User-Agent": USER_AGENT,
                # 社区实现如此：只收 gzip、连接不复用。
                "Accept-Encoding": "gzip",
                "Connection": "close",
            },
        )

    # -- 生命周期 ---------------------------------------------------------
    async def aclose(self) -> None:
        """关闭底层客户端；重复调用安全。"""
        await self._client.aclose()

    # -- 扫码登录链路 -----------------------------------------------------
    async def generate_scan(self) -> ScanLogin:
        """第 1 步：申请一个扫码会话（`gen_scan/login`）。"""
        payload = await self._hypergryph(
            _GEN_SCAN_LOGIN_PATH, method="POST", body={"appCode": SKLAND_APP_CODE}
        )
        data = _data_dict(payload, endpoint=_GEN_SCAN_LOGIN_PATH)
        return ScanLogin(
            scan_id=_required_str(data, "scanId", endpoint=_GEN_SCAN_LOGIN_PATH),
            scan_url=_required_str(data, "scanUrl", endpoint=_GEN_SCAN_LOGIN_PATH),
        )

    async def scan_status(self, scan_id: str) -> tuple[int, str | None, str]:
        """第 2 步：查一次扫码状态，返回 ``(status, scan_code, msg)``。

        ``status`` 是上游原值（``0`` 已确认、``100`` 未扫码、``102`` 已失效，其它原样透出），
        因此本方法**不把 ``status != 0`` 当异常**；只有 HTTP 非 2xx（如参数校验 400）才抛。
        未确认时 ``scan_code`` 是 ``None``。
        """
        payload = await self._hypergryph(
            _SCAN_STATUS_PATH,
            query=urlencode({"scanId": scan_id}),
            allow_status=True,
        )
        status = int(payload["status"])
        data = payload.get("data")
        raw_code = data.get("scanCode") if isinstance(data, dict) else None
        scan_code = raw_code if isinstance(raw_code, str) and raw_code else None
        raw_msg = payload.get("msg")
        return status, scan_code, raw_msg if isinstance(raw_msg, str) else ""

    async def token_by_scan_code(self, scan_code: str) -> str:
        """第 3 步：用确认后的 ``scanCode`` 换鹰角通行证 token。"""
        payload = await self._hypergryph(
            _TOKEN_BY_SCAN_CODE_PATH, method="POST", body={"scanCode": scan_code}
        )
        data = _data_dict(payload, endpoint=_TOKEN_BY_SCAN_CODE_PATH)
        return _required_str(data, "token", endpoint=_TOKEN_BY_SCAN_CODE_PATH)

    async def grant(self, hypergryph_token: str) -> str:
        """第 4 步：用通行证 token 换森空岛的授权 code。"""
        payload = await self._hypergryph(
            _GRANT_PATH,
            method="POST",
            body={"appCode": SKLAND_APP_CODE, "token": hypergryph_token, "type": 0},
        )
        data = _data_dict(payload, endpoint=_GRANT_PATH)
        return _required_str(data, "code", endpoint=_GRANT_PATH)

    async def generate_cred(self, code: str) -> SklandCredential:
        """第 5 步：用授权 code 换凭据（这一步还不能签名，是链路起点）。"""
        payload = await self._zonai(
            _GENERATE_CRED_PATH, method="POST", body={"code": code, "kind": 1}
        )
        data = _data_dict(payload, endpoint=_GENERATE_CRED_PATH)
        return SklandCredential(
            cred=_required_str(data, "cred", endpoint=_GENERATE_CRED_PATH),
            token=_required_str(data, "token", endpoint=_GENERATE_CRED_PATH),
        )

    # -- 已登录后的取数 ---------------------------------------------------
    async def refresh(self, credential: SklandCredential) -> SklandCredential:
        """第 5b 步：续期签名密钥；``cred`` 不变，只换 ``token``（用旧 ``token`` 签名）。"""
        payload = await self._zonai(_REFRESH_PATH, credential=credential)
        data = _data_dict(payload, endpoint=_REFRESH_PATH)
        return SklandCredential(
            cred=credential.cred,
            token=_required_str(data, "token", endpoint=_REFRESH_PATH),
        )

    async def binding(self, credential: SklandCredential) -> list[BindingRole]:
        """第 6 步：取绑定角色，只保留 ``appCode == "arknights"`` 的条目。

        真实形状（社区现行实现与 ``BindingApp`` schema 一致）：``data.list[]`` 是**按应用
        分组**的，每组带 ``appCode`` 与 ``bindingList[]``；角色条目里的昵称键是
        ``nickName``（大写 N），等级在 ``roles[].level``（或 ``defaultRole.level``）。
        早期实现按「顶层就有 ``bindingList``、昵称叫 ``nickname``」解析，真机 401 修好后
        会紧接着在这里解析失败——所以两种形状都认：嵌套优先，平铺作为兜底（并打一条
        INFO 说明用了兜底，方便发现上游回退）。

        ``channelMasterId`` / ``nickName`` / ``level`` 缺省一律容忍；缺 ``uid`` 的条目无法
        使用，跳过并记一条 warning。
        """
        payload = await self._zonai(_BINDING_PATH, credential=credential)
        data = _data_dict(payload, endpoint=_BINDING_PATH)

        nested = data.get("list")
        flat = data.get("bindingList")
        if isinstance(nested, list):
            groups = [item for item in nested if isinstance(item, Mapping)]
            grouped = True
        elif isinstance(flat, list):
            logger.info("森空岛 binding 响应是平铺形状（顶层 bindingList），按兜底路径解析")
            groups = [
                {"appCode": _BINDING_APP_CODE, "bindingList": flat, "defaultUid": None}
            ]
            grouped = False
        else:
            # 空列表是合法的「没有绑定」；两个键都不是列表说明形状变了。
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "森空岛接口响应里没有角色列表（list / bindingList 都不是列表）",
                {"endpoint": _BINDING_PATH},
            )

        roles: list[BindingRole] = []
        for group in groups:
            if group.get("appCode") not in (None, _BINDING_APP_CODE):
                continue
            entries = group.get("bindingList")
            if not isinstance(entries, list):
                continue
            default_uid = _optional_str(group.get("defaultUid"))
            for item in entries:
                if not isinstance(item, Mapping):
                    continue
                # 平铺兜底那条路里，appCode 挂在条目上（嵌套形状挂在分组上）：两处都判。
                if item.get("appCode") not in (None, _BINDING_APP_CODE):
                    continue
                uid = _optional_str(item.get("uid"))
                if uid is None:
                    logger.warning("森空岛绑定条目缺少 uid，已跳过")
                    continue
                roles.append(
                    BindingRole(
                        uid=uid,
                        app_code=_BINDING_APP_CODE,
                        channel_master_id=_optional_str(item.get("channelMasterId")),
                        nickname=_optional_str(item.get("nickName"))
                        or _optional_str(item.get("nickname")),
                        level=_binding_level(item),
                        is_default=bool(item.get("isDefault")) or default_uid == uid,
                    )
                )
        if grouped:
            logger.info("森空岛绑定：解析到 %d 个角色（appCode=%s）", len(roles), _BINDING_APP_CODE)
        return roles

    async def player_info(self, credential: SklandCredential, uid: str) -> dict[str, Any]:
        """第 7 步：取某个角色的账号数据，返回 ``data`` 字段的**原始 dict**。

        归一化（字段对位、越界丢弃）是 :mod:`maa_api.services.skland.mapping` 的事。
        """
        payload = await self._zonai(
            _PLAYER_INFO_PATH, query=urlencode({"uid": uid}), credential=credential
        )
        return _data_dict(payload, endpoint=_PLAYER_INFO_PATH)

    # -- 签名与发送 -------------------------------------------------------
    def _signing_headers(self, timestamp: int) -> dict[str, str]:
        """构造进签名的头：``platform`` → ``timestamp`` → ``dId`` → ``vName``（固定顺序）。

        真机取证与社区现行实现一致（2026-10-12）：四个键**都要在**，``dId`` 在没有设备
        指纹时是空串；多出来的键（调用方显式传的）按插入顺序追加在后面，保证「签名用的
        那份」与「发出去的那份」永远是同一个字典。
        """
        payload: dict[str, str] = {}
        for key in ("platform", "timestamp", "dId", "vName"):
            if key == "timestamp":
                payload[key] = str(timestamp)
            else:
                payload[key] = str(self._extra_headers.get(key, ""))
        for key, value in self._extra_headers.items():
            if key not in payload:
                payload[key] = str(value)
        return payload

    def _signature(
        self, *, path: str, query_params: str, credential: SklandCredential
    ) -> dict[str, str]:
        """按规格 §2.2 算一次签名头。

        :param query_params: GET 是 URL 查询串（无查询串传 ``""``），POST 是 body 的紧凑
            JSON——由 :meth:`_send` 与「实际发出去的字节」取同一份字符串。
        """
        timestamp = int(self._now()) - 1
        header_for_sign = self._signing_headers(timestamp)
        secret = (
            path
            + query_params
            + str(timestamp)
            + json.dumps(header_for_sign, separators=(",", ":"))
        )
        digest = hmac.new(
            credential.token.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        sign = hashlib.md5(digest.encode("utf-8")).hexdigest()
        # 签名用的那份头原样并进发送头：cred / sign + 四个进签名的头。
        return {"cred": credential.cred, **header_for_sign, "sign": sign}

    async def _send(
        self,
        *,
        base_url: str,
        path: str,
        method: str = "GET",
        query: str = "",
        body: Mapping[str, Any] | None = None,
        credential: SklandCredential | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """发一次请求（网络异常与 5xx 各重试一次），返回 ``(HTTP 状态码, JSON 对象)``。

        ``body`` 的紧凑 JSON 既是实际发送的字节，也是 POST 的待签串——同一份字符串。
        错误归一不在这里：两侧形状不同，交给 ``_hypergryph`` / ``_zonai``。
        """
        payload_text = json.dumps(body, separators=(",", ":")) if body is not None else None
        content = payload_text.encode("utf-8") if payload_text is not None else None
        headers: dict[str, str] = {}
        if credential is not None:
            headers = self._signature(
                path=path,
                query_params=payload_text if payload_text is not None else query,
                credential=credential,
            )
        if content is not None:
            headers["Content-Type"] = "application/json"
        url = f"{base_url}{path}" + (f"?{query}" if query else "")

        retries = 0
        while True:
            started = time.monotonic()
            try:
                response = await self._client.request(
                    method, url, content=content, headers=headers
                )
            except httpx.HTTPError as exc:
                retryable = retries + 1 < MAX_ATTEMPTS
                if retryable:
                    retries += 1
                self._log(
                    path, None, started, retries, error=str(ErrorCode.SKLAND_UPSTREAM_FAILED)
                )
                if retryable:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise AppError(
                    ErrorCode.SKLAND_UPSTREAM_FAILED,
                    f"森空岛接口连接失败：{exc}",
                    {"endpoint": path},
                ) from exc
            if response.status_code >= 500 and retries + 1 < MAX_ATTEMPTS:
                retries += 1
                self._log(
                    path,
                    response.status_code,
                    started,
                    retries,
                    error=str(ErrorCode.SKLAND_UPSTREAM_FAILED),
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            self._log(path, response.status_code, started, retries)
            return response.status_code, _json_object(response)

    # -- 错误归一 ---------------------------------------------------------
    async def _hypergryph(
        self,
        path: str,
        *,
        method: str = "GET",
        query: str = "",
        body: Mapping[str, Any] | None = None,
        allow_status: bool = False,
    ) -> dict[str, Any]:
        """鹰角侧：HTTP 非 2xx 或 ``status != 0`` 一律 ``SKLAND_UPSTREAM_FAILED``。

        ``allow_status=True`` 只给 :meth:`scan_status` 用：它的 ``status`` 是**业务枚举**
        （未扫码 / 已失效都算正常回答），要原样透出而不是当异常。
        """
        status_code, payload = await self._send(
            base_url=AS_BASE_URL, path=path, method=method, query=query, body=body
        )
        if not 200 <= status_code < 300:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                _upstream_message(payload, status_code),
                {"endpoint": path, "status": status_code},
            )
        status = payload.get("status")
        if not _is_int(status):
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "森空岛接口响应缺少 status",
                {"endpoint": path},
            )
        if status != 0 and not allow_status:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                _upstream_message(payload, status_code),
                {"endpoint": path, "status": status},
            )
        return payload

    async def _zonai(
        self,
        path: str,
        *,
        method: str = "GET",
        query: str = "",
        body: Mapping[str, Any] | None = None,
        credential: SklandCredential | None = None,
    ) -> dict[str, Any]:
        """zonai 侧：``code == 10002`` 或 HTTP 401/403 是登录失效，其余 ``code != 0`` 是上游失败。"""
        status_code, payload = await self._send(
            base_url=ZONAI_BASE_URL,
            path=path,
            method=method,
            query=query,
            body=body,
            credential=credential,
        )
        code = payload.get("code")
        if status_code in (401, 403) or code == 10002:
            raise AppError(
                ErrorCode.SKLAND_LOGIN_FAILED,
                _upstream_message(payload, status_code),
                {"endpoint": path, "status": status_code},
            )
        if not 200 <= status_code < 300:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                _upstream_message(payload, status_code),
                {"endpoint": path, "status": status_code},
            )
        if not _is_int(code):
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "森空岛接口响应缺少 code",
                {"endpoint": path},
            )
        if code != 0:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                _upstream_message(payload, status_code),
                {"endpoint": path, "code": code},
            )
        return payload

    # -- 日志 -------------------------------------------------------------
    @staticmethod
    def _log(
        path: str,
        status_code: int | None,
        started: float,
        retries: int,
        *,
        error: str | None = None,
    ) -> None:
        """只记端点、状态码、耗时、重试次数与错误码——正文与凭据都不进日志。"""
        logger.info(
            "森空岛取数 endpoint=%s status=%s 耗时=%.3fs 重试=%d 错误=%s",
            path,
            status_code if status_code is not None else "-",
            time.monotonic() - started,
            retries,
            error or "-",
        )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """把响应体当 JSON 对象解析；非法 JSON 或不是对象时返回空 dict（由归一逻辑报形状错）。"""
    try:
        data = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _upstream_message(payload: Mapping[str, Any], status_code: int) -> str:
    """上游文案：``msg`` / ``message`` 优先，取不到退到 ``HTTP <码>``（**不带正文**）。"""
    for key in ("msg", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return f"HTTP {status_code}"


def _data_dict(payload: Mapping[str, Any], *, endpoint: str) -> dict[str, Any]:
    """取 ``data`` 对象；缺了或不是对象都是响应形状不符。"""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AppError(
            ErrorCode.SKLAND_UPSTREAM_FAILED,
            "森空岛接口响应缺少 data",
            {"endpoint": endpoint},
        )
    return data


def _required_str(data: Mapping[str, Any], key: str, *, endpoint: str) -> str:
    """取一个必需的非空字符串字段。"""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise AppError(
            ErrorCode.SKLAND_UPSTREAM_FAILED,
            f"森空岛接口响应缺少 {key}",
            {"endpoint": endpoint},
        )
    return value


def _binding_level(item: Mapping[str, Any]) -> int | None:
    """角色等级：优先 ``defaultRole.level``，其次 ``roles[0].level``，都没有就 ``None``。

    上游把等级放在「角色 → 服务器角色」这一层（``BindingCharacter.roles[]``），顶层没有
    ``level`` 键——早期实现直接读顶层，真机上永远是 ``None``。
    """
    default_role = item.get("defaultRole")
    if isinstance(default_role, Mapping):
        level = _optional_int(default_role.get("level"))
        if level is not None:
            return level
    roles = item.get("roles")
    if isinstance(roles, list):
        for role in roles:
            if isinstance(role, Mapping):
                level = _optional_int(role.get("level"))
                if level is not None:
                    return level
    return _optional_int(item.get("level"))


def _optional_str(value: Any) -> str | None:
    """可缺省字符串字段：字符串直用、数字转字符串、其它算没有（不猜）。"""
    if isinstance(value, str):
        return value or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _optional_int(value: Any) -> int | None:
    """可缺省整数字段：只有真正的整数才算（``True`` 不算）。"""
    if _is_int(value):
        return int(value)
    return None


def _is_int(value: Any) -> bool:
    """``bool`` 是 ``int`` 的子类，但业务码 / 等级里出现 ``True`` 只能算形状不符。"""
    return isinstance(value, int) and not isinstance(value, bool)
