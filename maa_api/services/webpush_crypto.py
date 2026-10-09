"""Web Push 消息加密（RFC 8291 + RFC 8188 ``aes128gcm``）。

Web Push 只依赖 ``cryptography``（已是项目直接依赖），不引入 ``pywebpush``：
本机网络对 PyPI 不稳定，而这里需要的 ECDH P-256、HKDF-SHA256 与 AES-128-GCM 原语
``cryptography`` 全部具备。

**关键实现约束：派生必须手写 HKDF 的 extract / expand 两步。**
RFC 8291 §3.4 的 ``PRK_key = HMAC(auth_secret, ecdh_secret)`` 与
``PRK = HMAC(salt, IKM)`` 都是「只做 extract」的语义，而 ``cryptography`` 的
``HKDF.derive()`` 是 RFC 5869 的 extract+expand 组合，用它替换会**额外多做一次
expand**：结果全错，且不抛任何异常。``tests/services/test_webpush_crypto.py`` 用
RFC 8291 Appendix A 的官方中间值锁住这个陷阱。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "MAX_PLAINTEXT_BYTES",
    "PUSH_RECORD_SIZE",
    "EncryptedPush",
    "encrypt_push_message",
]

#: RFC 8188 的记录大小。单条推送只用一个记录，user agent 可忽略该值。
PUSH_RECORD_SIZE = 4096

#: RFC 8291 §4 的明文上限：4096 − 86（头）− 1（填充分隔符）− 16（AEAD tag）。
MAX_PLAINTEXT_BYTES = 3993

#: RFC 8291 §3.1 固定使用 P-256 未压缩点，长度 65。
_PUBLIC_KEY_BYTES = 65

#: RFC 8291 §3.2 要求 16 octet 认证密钥。
_AUTH_SECRET_BYTES = 16

_KEY_INFO_PREFIX = b"WebPush: info\x00"
_CEK_INFO = b"Content-Encoding: aes128gcm\x00"
_NONCE_INFO = b"Content-Encoding: nonce\x00"

#: RFC 8188 §2 的填充分隔符：最后一条记录用 0x02。
_PADDING_DELIMITER = b"\x02"


class PushEncryptionError(ValueError):
    """参数不满足 RFC 8291 约束。

    实现纳入项目后由领域层 ``AppError(PUSH_SUBSCRIPTION_INVALID)`` 取代；
    本阶段保持纯 ``ValueError`` 以便独立验证。
    """


@dataclass(frozen=True)
class EncryptedPush:
    """一条加密后的推送请求体。

    ``body`` 直接作为 HTTP 请求体，配合
    ``Content-Encoding: aes128gcm`` 与 ``Content-Type: application/octet-stream``。
    """

    body: bytes
    salt: bytes
    server_public_key: bytes

    @property
    def content_length(self) -> int:
        return len(self.body)


def _extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 5869 §2.2 HKDF-Extract。"""
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 §2.3 HKDF-Expand。"""
    if length > 255 * hashlib.sha256().digest_size:
        raise PushEncryptionError("HKDF 输出长度超出 SHA-256 上限")
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _load_user_agent_key(public_key: bytes) -> ec.EllipticCurvePublicKey:
    """把用户代理公钥解析成 P-256 公钥对象。

    ``from_encoded_point`` 会做 RFC 8291 §7 要求的合法性检查：点在曲线上、
    不是无穷远点、坐标在合法区间内。
    """
    if len(public_key) != _PUBLIC_KEY_BYTES or public_key[0] != 0x04:
        raise PushEncryptionError(
            f"用户代理公钥必须是 {_PUBLIC_KEY_BYTES} octet 的未压缩 P-256 点"
        )
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    except ValueError as exc:  # 不在曲线上 / 无穷远点 / 非合法点编码
        raise PushEncryptionError(f"用户代理公钥不是合法的 P-256 点: {exc}") from exc


def encrypt_push_message(
    payload: bytes,
    *,
    ua_public: bytes,
    auth_secret: bytes,
    as_private: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> EncryptedPush:
    """按 RFC 8291 加密一条 Web Push 消息。

    ``as_private`` 与 ``salt`` 只应由测试注入（官方向量复现）；生产路径每次
    随机生成，因为 RFC 8291 §2 允许「加密后即丢弃」这对临时密钥。
    """
    if len(auth_secret) != _AUTH_SECRET_BYTES:
        raise PushEncryptionError(
            f"认证密钥必须是 {_AUTH_SECRET_BYTES} octet，收到 {len(auth_secret)}"
        )
    if len(payload) > MAX_PLAINTEXT_BYTES:
        raise PushEncryptionError(
            f"明文超过 {MAX_PLAINTEXT_BYTES} octet 上限（收到 {len(payload)}），"
            "调用方应先按 tag 合并语义截断"
        )

    ua_key = _load_user_agent_key(ua_public)
    if as_private is None:
        as_private = ec.generate_private_key(ec.SECP256R1())
    if not isinstance(as_private.curve, ec.SECP256R1):
        raise PushEncryptionError("应用服务器私钥必须使用 P-256 曲线")

    as_public = as_private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    # RFC 8291 §3.3：ECDH 共享密钥先与认证密钥做一次 extract，再 expand 出 IKM。
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    prk_key = _extract(auth_secret, ecdh_secret)
    key_info = _KEY_INFO_PREFIX + ua_public + as_public
    ikm = _expand(prk_key, key_info, 32)

    # RFC 8188 §2.3：由 salt 与 IKM 派生 CEK 与 NONCE。
    if salt is None:
        salt = os.urandom(16)
    if len(salt) != 16:
        raise PushEncryptionError(f"salt 必须是 16 octet，收到 {len(salt)}")
    prk = _extract(salt, ikm)
    cek = _expand(prk, _CEK_INFO, 16)
    nonce = _expand(prk, _NONCE_INFO, 12)

    header = (
        salt
        + PUSH_RECORD_SIZE.to_bytes(4, "big")
        + bytes([len(as_public)])
        + as_public
    )
    ciphertext = AESGCM(cek).encrypt(nonce, payload + _PADDING_DELIMITER, None)
    return EncryptedPush(body=header + ciphertext, salt=salt, server_public_key=as_public)


def b64url_decode(value: str) -> bytes:
    """解析浏览器 ``PushSubscription.toJSON()`` 里的 base64url 字段（无填充）。"""
    if not isinstance(value, str):
        raise PushEncryptionError("base64url 字段必须是字符串")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError) as exc:
        raise PushEncryptionError(f"不是合法的 base64url 值: {exc}") from exc


def b64url_encode(value: bytes) -> str:
    """编码成无填充的 base64url（VAPID 公钥与向量比对都用这个形式）。"""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
