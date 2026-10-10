"""Multi-channel notification delivery and channel configuration service.

Transport setup is lazy: importing this module does not open sockets or SMTP
connections. HTTP and SMTP callables can be injected to keep tests offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import re
import smtplib
import time
from base64 import b64encode
from collections.abc import Callable, Mapping
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError

from maa_api.db import session as db_session
from maa_api.db.models import NotifyChannel
from maa_api.db.repositories.notify import NotifyChannelRepository
from maa_api.domain.enums import NotifyChannelType, NotifyEvent
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)
SEND_TIMEOUT_SECONDS = 10.0
_SECRET_NAME = re.compile(
    r"(authorization|password|passwd|secret|token|api[_-]?key|device[_-]?key|(?:^|[_-])key(?:$|[_-]))",
    re.I,
)
_TEMPLATE_FIELD = re.compile(r"\{([A-Za-z0-9_.]+)\}")


class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _EmailConfig(_StrictConfig):
    server: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    use_tls: bool = True
    username: str = ""
    password: str = ""
    to: list[str] = Field(min_length=1)

    @field_validator("to")
    @classmethod
    def valid_recipients(cls, values: list[str]) -> list[str]:
        if not values or any("@" not in value or "\n" in value for value in values):
            raise ValueError("to 必须至少包含一个有效邮箱地址")
        return values


class _WebhookConfig(_StrictConfig):
    url: str = Field(min_length=1, max_length=2048)
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: str = "{title}\n{body}"

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise ValueError("url 必须是 http 或 https 地址")
        return value

    @field_validator("method")
    @classmethod
    def valid_method(cls, value: str) -> str:
        value = value.upper()
        if value not in {"POST", "PUT"}:
            raise ValueError("method 只支持 POST 或 PUT")
        return value

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if not key or "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                raise ValueError("headers 包含无效的 HTTP 头")
        return values


class _BarkConfig(_StrictConfig):
    server: str = "https://api.day.app"
    device_key: str = Field(min_length=1, max_length=256)
    sound: str | None = None
    group: str | None = None

    @field_validator("server")
    @classmethod
    def valid_server(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise ValueError("server 必须是 http 或 https 地址")
        return value.rstrip("/")


class _DingTalkConfig(_StrictConfig):
    webhook: str = Field(min_length=1, max_length=2048)
    secret: str = ""
    at_mobiles: list[str] = Field(default_factory=list)

    @field_validator("webhook")
    @classmethod
    def valid_webhook(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
            raise ValueError("webhook 必须是 https 地址")
        return value


class _WeComConfig(_StrictConfig):
    webhook: str = Field(min_length=1, max_length=2048)
    mentioned_list: list[str] = Field(default_factory=list)

    @field_validator("webhook")
    @classmethod
    def valid_webhook(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
            raise ValueError("webhook 必须是 https 地址")
        return value


class _FeishuConfig(_StrictConfig):
    """飞书自定义机器人；加签算法与钉钉不同，见 :meth:`NotifyService._send_feishu`。"""

    webhook: str = Field(min_length=1, max_length=2048)
    secret: str = ""
    at_user_ids: list[str] = Field(default_factory=list)

    @field_validator("webhook")
    @classmethod
    def valid_webhook(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
            raise ValueError("webhook 必须是 https 地址")
        return value

    @field_validator("at_user_ids")
    @classmethod
    def valid_at_user_ids(cls, values: list[str]) -> list[str]:
        # 这些值会被原样拼进文本消息的 <at user_id="...">，引号或尖括号会破坏消息。
        if any(not value or re.search(r"[\"'<>\s]", value) for value in values):
            raise ValueError("at_user_ids 只能是不含引号、尖括号与空白的用户 ID")
        return values


_CONFIG_MODELS: dict[NotifyChannelType, type[BaseModel]] = {
    NotifyChannelType.EMAIL: _EmailConfig,
    NotifyChannelType.WEBHOOK: _WebhookConfig,
    NotifyChannelType.BARK: _BarkConfig,
    NotifyChannelType.DINGTALK: _DingTalkConfig,
    NotifyChannelType.WECOM: _WeComConfig,
    NotifyChannelType.FEISHU: _FeishuConfig,
}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class NotifyService:
    """CRUD and delivery for configured notification channels."""

    def __init__(
        self,
        session_factory=None,
        *,
        http_sender: Any = None,
        smtp_sender: Callable[..., Any] | None = None,
    ) -> None:
        self._session_factory = session_factory or db_session.session_factory
        self._http_sender = http_sender
        self._smtp_sender = smtp_sender

    async def list_channels(
        self,
        *,
        type: NotifyChannelType | str | None = None,
        enabled: bool | None = None,
    ) -> list[NotifyChannel]:
        async with self._session_factory() as session:
            return await NotifyChannelRepository(session).list(type=type, enabled=enabled)

    async def get_channel(self, channel_id: str) -> NotifyChannel:
        async with self._session_factory() as session:
            channel = await NotifyChannelRepository(session).get(channel_id)
        if channel is None:
            raise self._not_found(channel_id)
        return channel

    async def create_channel(
        self,
        *,
        type: NotifyChannelType,
        name: str,
        config: Mapping[str, Any],
        events: list[NotifyEvent],
        enabled: bool = True,
    ) -> NotifyChannel:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 64:
            raise self._config_error("name 必须为 1 到 64 个字符")
        checked = self._validate_config(type, config)
        channel = NotifyChannel(
            type=type.value,
            name=clean_name,
            config=checked,
            events=[event.value for event in events],
            enabled=enabled,
        )
        async with self._session_factory() as session:
            try:
                stored = await NotifyChannelRepository(session).create(channel)
                await session.commit()
                return stored
            except IntegrityError as exc:
                await session.rollback()
                raise self._conflict(type, clean_name) from exc

    async def update_channel(
        self,
        channel_id: str,
        *,
        type: NotifyChannelType,
        name: str,
        config: Mapping[str, Any],
        events: list[NotifyEvent],
        enabled: bool = True,
    ) -> NotifyChannel:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 64:
            raise self._config_error("name 必须为 1 到 64 个字符")
        async with self._session_factory() as session:
            repo = NotifyChannelRepository(session)
            channel = await repo.get(channel_id)
            if channel is None:
                raise self._not_found(channel_id)
            checked_input = dict(config)
            checked_input = _restore_masked_secrets(checked_input, channel.config)
            checked = self._validate_config(type, checked_input)
            channel.type = type.value
            channel.name = clean_name
            channel.config = checked
            channel.events = [event.value for event in events]
            channel.enabled = enabled
            try:
                await session.flush()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise self._conflict(type, clean_name) from exc
            return channel

    async def delete_channel(self, channel_id: str) -> None:
        async with self._session_factory() as session:
            deleted = await NotifyChannelRepository(session).delete(channel_id)
            if not deleted:
                raise self._not_found(channel_id)
            await session.commit()

    async def test_channel(
        self,
        channel_id: str,
        *,
        event: NotifyEvent = NotifyEvent.PIPELINE_COMPLETED,
    ) -> dict[str, Any]:
        channel = await self.get_channel(channel_id)
        payload = {
            "title": "MAA-API 测试通知",
            "message": "这是一条测试通知，通道配置可正常发送",
            "event": event.value,
            "sample": True,
        }
        try:
            await self._send_channel(channel, event, payload)
        except Exception as exc:
            safe_error = _safe_error(exc, channel.config)
            await self._record_send(channel_id, "failed", safe_error)
            raise AppError(
                ErrorCode.NOTIFY_SEND_FAILED,
                "通知发送失败，请检查通道配置；详情见日志页",
                {"channel_id": channel_id, "upstream": safe_error},
            ) from exc
        await self._record_send(channel_id, "success", None)
        return {"sent": True, "event": event.value}

    async def send_event(
        self,
        event: NotifyEvent | str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        """Send once to each enabled channel subscribed to ``event``.

        Payloads such as ``update_available`` already contain all updates from a
        single check, so they render as one message per subscribed channel.
        Individual transport errors are recorded and do not disable channels.
        """
        event_value = _event_value(event)
        data = dict(payload or {})
        channels = await self.list_channels(enabled=True)
        selected = [channel for channel in channels if event_value in channel.events]
        sent = failed = 0
        for channel in selected:
            try:
                await self._send_channel(channel, event_value, data)
            except Exception as exc:
                failed += 1
                await self._record_send(channel.id, "failed", _safe_error(exc, channel.config))
                logger.warning("通知投递失败（通道 %s）", channel.id)
            else:
                sent += 1
                await self._record_send(channel.id, "success", None)
        return {"matched": len(selected), "sent": sent, "failed": failed}

    async def _record_send(self, channel_id: str, status: str, error: str | None) -> None:
        async with self._session_factory() as session:
            await NotifyChannelRepository(session).record_send(
                channel_id, status=status, error=error
            )
            await session.commit()

    async def _send_channel(
        self, channel: NotifyChannel, event: NotifyEvent | str, payload: Mapping[str, Any]
    ) -> None:
        channel_type = NotifyChannelType(channel.type)
        config = self._validate_config(channel_type, channel.config)
        title, body = _message(event, payload)
        if channel_type is NotifyChannelType.EMAIL:
            await self._send_email(config, title, body)
        elif channel_type is NotifyChannelType.WEBHOOK:
            rendered = _render_template(config["body_template"], {**payload, "title": title, "body": body})
            await self._http_request(
                config["method"], config["url"],
                content=rendered.encode("utf-8"), headers=config["headers"],
            )
        elif channel_type is NotifyChannelType.BARK:
            values = {"title": title, "body": body}
            if config.get("sound"):
                values["sound"] = config["sound"]
            if config.get("group"):
                values["group"] = config["group"]
            url = f"{config['server']}/{config['device_key']}"
            await self._http_request("POST", url, json=values)
        elif channel_type is NotifyChannelType.DINGTALK:
            await self._send_dingtalk(config, title, body)
        elif channel_type is NotifyChannelType.FEISHU:
            await self._send_feishu(config, title, body)
        else:
            await self._http_request(
                "POST", config["webhook"],
                json={
                    "msgtype": "text",
                    "text": {"content": f"{title}\n{body}", "mentioned_list": config["mentioned_list"]},
                },
            )

    async def _send_dingtalk(self, config: dict[str, Any], title: str, body: str) -> None:
        url = config["webhook"]
        if config.get("secret"):
            timestamp = str(int(time.time() * 1000))
            raw = f"{timestamp}\n{config['secret']}".encode("utf-8")
            digest = hmac.new(config["secret"].encode("utf-8"), raw, hashlib.sha256).digest()
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query.update(timestamp=timestamp, sign=b64encode(digest).decode("ascii"))
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        text: dict[str, Any] = {"content": f"{title}\n{body}"}
        if config["at_mobiles"]:
            text["atMobiles"] = config["at_mobiles"]
        await self._http_request("POST", url, json={"msgtype": "text", "text": text})

    async def _send_feishu(self, config: dict[str, Any], title: str, body: str) -> None:
        """飞书自定义机器人（``/open-apis/bot/v2/hook/<token>``）。

        加签与钉钉是**两套**算法，不能混用：飞书把 ``"{timestamp}\\n{secret}"`` 当作
        HMAC 的**密钥**、消息体为空，时间戳用**秒**，签名放在请求体里；钉钉则把
        secret 当密钥、``"{timestamp}\\n{secret}"`` 当消息，时间戳用毫秒、签名放 URL。
        """
        mentions = "".join(
            f'<at user_id="{user_id}">{"所有人" if user_id == "all" else ""}</at>'
            for user_id in config["at_user_ids"]
        )
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": f"{title}\n{body}{mentions}"},
        }
        if config.get("secret"):
            timestamp = str(int(time.time()))
            digest = hmac.new(
                f"{timestamp}\n{config['secret']}".encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = b64encode(digest).decode("ascii")
        await self._http_request("POST", config["webhook"], json=payload)

    async def _http_request(self, method: str, url: str, **kwargs: Any) -> None:
        transport = self._http_sender
        if transport is None:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
                response = await client.request(method, url, timeout=SEND_TIMEOUT_SECONDS, **kwargs)
        elif hasattr(transport, "request"):
            response = await _maybe_await(
                transport.request(method, url, timeout=SEND_TIMEOUT_SECONDS, **kwargs)
            )
        else:
            response = await _maybe_await(
                transport(method, url, timeout=SEND_TIMEOUT_SECONDS, **kwargs)
            )
        status = getattr(response, "status_code", None)
        if isinstance(response, Mapping):
            status = response.get("status_code", response.get("status", 200))
        if status is not None and not 200 <= int(status) < 300:
            text = getattr(response, "text", None)
            if isinstance(response, Mapping):
                text = response.get("text", response.get("body", text))
            raise RuntimeError(f"HTTP {status}: {_truncate(str(text or 'upstream request failed'))}")

    async def _send_email(self, config: dict[str, Any], title: str, body: str) -> None:
        sender = self._smtp_sender or _smtp_send_sync
        kwargs = {"timeout": SEND_TIMEOUT_SECONDS}
        async def invoke() -> Any:
            if inspect.iscoroutinefunction(sender):
                return await sender(config, title, body, **kwargs)
            result = await asyncio.to_thread(sender, config, title, body, **kwargs)
            return await _maybe_await(result)

        await asyncio.wait_for(invoke(), timeout=SEND_TIMEOUT_SECONDS)

    @staticmethod
    def _validate_config(type: NotifyChannelType, config: Mapping[str, Any]) -> dict[str, Any]:
        try:
            model = _CONFIG_MODELS[NotifyChannelType(type)]
            return model.model_validate(dict(config)).model_dump(exclude_none=True)
        except (ValidationError, TypeError, ValueError) as exc:
            raise AppError(
                ErrorCode.NOTIFY_CONFIG_INVALID,
                "通知通道配置不合法，请检查必填项后重新保存",
                {
                    "reason": json.dumps(
                        [
                            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                            for error in exc.errors(include_input=False)
                        ],
                        ensure_ascii=False,
                    ) if isinstance(exc, ValidationError) else _truncate(str(exc), 1000)
                },
            ) from exc

    @staticmethod
    def _not_found(channel_id: str) -> AppError:
        return AppError(
            ErrorCode.NOTIFY_CHANNEL_NOT_FOUND,
            "通知通道不存在，请刷新列表",
            {"channel_id": channel_id},
        )

    @staticmethod
    def _conflict(type: NotifyChannelType, name: str) -> AppError:
        return AppError(
            ErrorCode.NOTIFY_CHANNEL_CONFLICT,
            "已有同类型的同名通道，请换一个名称",
            {"type": type.value, "name": name},
        )

    @staticmethod
    def _config_error(message: str) -> AppError:
        return AppError(ErrorCode.NOTIFY_CONFIG_INVALID, message)


def channel_wire(channel: NotifyChannel) -> dict[str, Any]:
    """Public channel representation; recursively mask credentials and tokens."""
    return {
        "id": channel.id,
        "type": channel.type,
        "name": channel.name,
        "enabled": channel.enabled,
        "config": _mask_config(channel.config),
        "events": list(channel.events),
        "last_sent_at": channel.last_sent_at.isoformat() if channel.last_sent_at else None,
        "last_status": channel.last_status,
        "last_error": channel.last_error,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "updated_at": channel.updated_at.isoformat() if channel.updated_at else None,
    }


def _mask_config(config: Mapping[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if _is_sensitive_key(key) and value:
            masked[key] = "***"
        elif key.lower() == "webhook" and isinstance(value, str):
            masked[key] = _mask_webhook_url(value)
        elif key.lower() in {"webhook", "url", "server"} and isinstance(value, str):
            masked[key] = _mask_url(value)
        elif key == "headers" and isinstance(value, Mapping):
            masked[key] = {
                name: ("***" if _is_sensitive_key(name) and item else item)
                for name, item in value.items()
            }
        elif isinstance(value, Mapping):
            masked[key] = _mask_config(value)
        else:
            masked[key] = value
    return masked


def _mask_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        query = [(key, "***" if _is_sensitive_key(key) and item else item) for key, item in parse_qsl(parts.query, keep_blank_values=True)]
        path = parts.path
        # WeCom keys are path credentials. Keep the stable prefix for display.
        if "qyapi.weixin.qq.com" in parts.netloc and "/key/" in path:
            prefix, _key = path.rsplit("/key/", 1)
            path = f"{prefix}/key/***"
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query, safe="*"), parts.fragment))
    except Exception:
        return value


def _is_sensitive_key(key: str) -> bool:
    return bool(_SECRET_NAME.search(str(key)))


def _restore_masked_secrets(new: dict[str, Any], old: Mapping[str, Any]) -> dict[str, Any]:
    """Allow clients to round-trip masked fields without erasing credentials."""
    for key, value in tuple(new.items()):
        previous = old.get(key)
        if _is_sensitive_key(key) and value in ("***", "") and previous:
            new[key] = previous
        elif key.lower() == "webhook" and isinstance(value, str) and isinstance(previous, str):
            new[key] = _restore_masked_webhook(value, previous)
        elif key.lower() in {"url", "server"} and isinstance(value, str) and isinstance(previous, str):
            new[key] = _restore_masked_url(value, previous)
        elif isinstance(value, dict) and isinstance(previous, Mapping):
            new[key] = _restore_masked_secrets(value, previous)
    return new


def _restore_masked_url(masked: str, original: str) -> str:
    try:
        new_parts = urlsplit(masked)
        old_parts = urlsplit(original)
        old_query = dict(parse_qsl(old_parts.query, keep_blank_values=True))
        query = []
        for key, value in parse_qsl(new_parts.query, keep_blank_values=True):
            if value == "***" and _is_sensitive_key(key) and key in old_query:
                value = old_query[key]
            query.append((key, value))
        path = new_parts.path
        if "/key/***" in path and "/key/" in old_parts.path:
            path = path.replace("/key/***", old_parts.path[old_parts.path.rfind("/key/"):])
        return urlunsplit((new_parts.scheme, new_parts.netloc, path, urlencode(query), new_parts.fragment))
    except Exception:
        return masked


def _mask_webhook_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme and parts.netloc:
            return urlunsplit((parts.scheme, parts.netloc, "/***", "", ""))
    except Exception:
        pass
    return "***"


def _restore_masked_webhook(masked: str, original: str) -> str:
    if masked.endswith("/***"):
        try:
            masked_parts = urlsplit(masked)
            original_parts = urlsplit(original)
            if (masked_parts.scheme, masked_parts.netloc) == (original_parts.scheme, original_parts.netloc):
                return original
        except Exception:
            pass
    return masked


def _event_value(event: NotifyEvent | str) -> NotifyEvent | str:
    try:
        return NotifyEvent(event)
    except ValueError:
        return str(event)


def _message(event: NotifyEvent | str, payload: Mapping[str, Any]) -> tuple[str, str]:
    labels = {
        NotifyEvent.PIPELINE_COMPLETED: "流水线完成",
        NotifyEvent.PIPELINE_FAILED: "流水线失败",
        NotifyEvent.CORE_CRASHED: "内核异常退出",
        NotifyEvent.DEVICE_DISCONNECTED: "设备连接断开",
        NotifyEvent.UPDATE_AVAILABLE: "发现可用更新",
        NotifyEvent.UPDATE_FINISHED: "更新完成",
        NotifyEvent.CONFIRMATION_REQUIRED: "需要人工确认",
    NotifyEvent.UPGRADE_PLAN_FINISHED: "养成计划完成",
    }
    title = str(payload.get("title") or labels.get(event, "MAA-API 有一条新通知"))
    message = payload.get("message") or payload.get("body")
    if message is None:
        # 缺正文时给一句通用文案：原始 payload 是内部结构，不能当通知正文推给用户。
        logger.debug("通知缺少正文，已使用缺省文案（event=%s）", event)
        message = "点开 MAA-API 查看详情"
    return title, str(message)


def _render_template(template: str, values: Mapping[str, Any]) -> str:
    """Substitute simple named fields only; never evaluate Python/Jinja code."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        current: Any = values
        for part in key.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                return ""
        if isinstance(current, (dict, list)):
            return json.dumps(current, ensure_ascii=False, default=str)
        return str(current)

    if "{" in _TEMPLATE_FIELD.sub("", template) or "}" in _TEMPLATE_FIELD.sub("", template):
        raise ValueError("body_template 只支持 {field} 形式的字段替换")
    return _TEMPLATE_FIELD.sub(replace, template)


def _safe_error(exc: BaseException, config: Mapping[str, Any] | None = None) -> str:
    value = str(exc)
    for secret in _secret_values(config or {}):
        if secret and secret != "***":
            value = value.replace(secret, "***")
    value = _SECRET_NAME.sub("credential", value)
    return _truncate(value, 500)


def _secret_values(config: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in config.items():
        if _is_sensitive_key(key) and isinstance(value, str) and value:
            values.append(value)
        elif key.lower() in {"url", "webhook", "server"} and isinstance(value, str):
            values.append(value)
            try:
                values.extend(
                    item for name, item in parse_qsl(urlsplit(value).query, keep_blank_values=True)
                    if _is_sensitive_key(name) and item
                )
            except Exception:
                pass
        elif isinstance(value, Mapping):
            values.extend(_secret_values(value))
    return values


def _truncate(value: str, limit: int = 500) -> str:
    value = str(value).replace("\r", " ").replace("\n", " ")
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _smtp_send_sync(
    config: Mapping[str, Any], subject: str, body: str, *, timeout: float
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.get("username") or config["to"][0]
    message["To"] = ", ".join(config["to"])
    message.set_content(body)
    smtp_class = smtplib.SMTP_SSL if config.get("use_tls", True) else smtplib.SMTP
    with smtp_class(config["server"], config["port"], timeout=timeout) as client:
        if not config.get("use_tls", True):
            client.starttls()
        if config.get("username"):
            client.login(config["username"], config.get("password", ""))
        client.send_message(message)


__all__ = ["NotifyService", "channel_wire"]
