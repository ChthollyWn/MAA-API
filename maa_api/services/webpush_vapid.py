"""VAPID（RFC 8292）请求签名与密钥生成。

Web Push 的 ``Authorization`` 头需要一份 ES256 签名的 JWT：

.. code-block:: text

    Authorization: vapid t=<JWT>, k=<base64url 公钥>
    JWT payload: {"aud": <endpoint origin>, "exp": <now+12h>, "sub": "mailto:..."}

与 :mod:`maa_api.services.webpush_crypto` 一样只用 ``cryptography``：``ec`` 的
ECDSA 签名给的是 DER 编码，而 JWS 要的是 64 octet 的 ``R || S`` 定长拼接，
两者必须显式转换：直接把 DER 结果塞进 JWT 会得到一个签名永远验不过的 token。
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from maa_api.services.webpush_crypto import b64url_decode, b64url_encode

__all__ = [
    "VAPID_TOKEN_TTL_SECONDS",
    "build_vapid_header",
    "generate_vapid_key_pair",
    "load_vapid_private_key",
    "public_key_from_private",
    "serialize_vapid_private_key",
    "sign_vapid_token",
]

#: JWT 有效期。RFC 8292 §2 要求不超过 24 小时；12 小时留出充分余量。
VAPID_TOKEN_TTL_SECONDS = 12 * 60 * 60

#: P-256 的 r / s 各 32 octet，JWS 里固定拼接成 64 octet。
_JWS_SIGNATURE_BYTES = 64


def serialize_vapid_private_key(private_key: ec.EllipticCurvePrivateKey) -> str:
    """导出可写进 ``config.yaml`` 的私钥形式（PKCS#8 PEM 单行转义由调用方决定）。"""
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def load_vapid_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """从 PEM 文本载入 VAPID 私钥，并校验它是 P-256（Web Push 只接受 P-256）。"""
    try:
        key = serialization.load_pem_private_key(value.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"VAPID 私钥不是合法的 PEM 私钥: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("VAPID 私钥必须是 P-256（secp256r1）EC 私钥")
    return key


def public_key_from_private(private_key: ec.EllipticCurvePrivateKey) -> str:
    """取未压缩点形式的公钥（base64url 无填充），即前端 ``applicationServerKey``。"""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64url_encode(raw)


def generate_vapid_key_pair() -> tuple[str, str]:
    """生成一对 VAPID 密钥，返回 ``(private_pem, public_base64url)``。

    只在显式调用时生成（``scripts/generate_vapid_keys.py``）。**不在服务启动时
    自动生成**：每次启动换密钥会让所有既有订阅静默失效。
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    return serialize_vapid_private_key(private_key), public_key_from_private(private_key)


def sign_vapid_token(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    audience: str,
    subject: str,
    expires_at: int | None = None,
) -> str:
    """为指定 endpoint origin 签一份 ES256 JWT。

    ``audience`` 必须是推送服务的 origin（``scheme://host[:port]``）而不是完整
    endpoint：RFC 8292 §2 规定 ``aud`` 取 origin，取错会被推送服务以 401/403 拒绝。
    """
    if not audience:
        raise ValueError("VAPID 的 aud 不能为空")
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": audience, "exp": expires_at or int(time.time()) + VAPID_TOKEN_TTL_SECONDS}
    if subject:
        claims["sub"] = subject
    payload = b64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header}.{payload}".encode()

    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    assert len(raw_signature) == _JWS_SIGNATURE_BYTES
    return f"{header}.{payload}.{b64url_encode(raw_signature)}"


def endpoint_audience(endpoint: str) -> str:
    """从订阅 endpoint 取出 JWT 的 ``aud``。"""
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"订阅 endpoint 不是绝对 URL: {endpoint!r}")
    return f"{parts.scheme}://{parts.netloc}"


def build_vapid_header(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    endpoint: str,
    subject: str,
    expires_at: int | None = None,
) -> str:
    """构造 ``Authorization`` 头的值。"""
    token = sign_vapid_token(
        private_key,
        audience=endpoint_audience(endpoint),
        subject=subject,
        expires_at=expires_at,
    )
    return f"vapid t={token}, k={public_key_from_private(private_key)}"


def verify_vapid_token(token: str, public_key_b64: str) -> dict[str, object]:
    """仅用于测试：用公钥验签并返回 payload。"""
    header_b64, payload_b64, signature_b64 = token.split(".")
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b64url_decode(public_key_b64)
    )
    raw = b64url_decode(signature_b64)
    if len(raw) != _JWS_SIGNATURE_BYTES:
        raise ValueError("JWS 签名长度不是 64 octet")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    public_key.verify(
        encode_dss_signature(r, s),
        f"{header_b64}.{payload_b64}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    return json.loads(b64url_decode(payload_b64))
