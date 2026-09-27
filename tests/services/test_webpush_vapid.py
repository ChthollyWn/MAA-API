"""VAPID（RFC 8292）签名与密钥生成的测试。"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from maa_api.services import webpush_vapid as vapid

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
SUBJECT = "mailto:admin@example.com"


def test_generated_key_pair_is_consistent() -> None:
    private_pem, public_b64 = vapid.generate_vapid_key_pair()
    loaded = vapid.load_vapid_private_key(private_pem)
    assert vapid.public_key_from_private(loaded) == public_b64
    assert len(public_b64) == 87  # 65 octet 未压缩点，无填充 base64url


def test_generated_key_pairs_are_unique() -> None:
    first = vapid.generate_vapid_key_pair()
    second = vapid.generate_vapid_key_pair()
    assert first != second


def test_token_signature_verifies_with_public_key() -> None:
    private_pem, public_b64 = vapid.generate_vapid_key_pair()
    private_key = vapid.load_vapid_private_key(private_pem)
    token = vapid.sign_vapid_token(
        private_key, audience="https://fcm.googleapis.com", subject=SUBJECT
    )
    claims = vapid.verify_vapid_token(token, public_b64)
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == SUBJECT
    assert int(claims["exp"]) > time.time()


def test_token_default_ttl_is_twelve_hours() -> None:
    private_pem, public_b64 = vapid.generate_vapid_key_pair()
    token = vapid.sign_vapid_token(
        vapid.load_vapid_private_key(private_pem),
        audience="https://push.example.net",
        subject=SUBJECT,
    )
    claims = vapid.verify_vapid_token(token, public_b64)
    assert int(claims["exp"]) - int(time.time()) == pytest.approx(
        vapid.VAPID_TOKEN_TTL_SECONDS, abs=5
    )


def test_audience_is_endpoint_origin_not_full_path() -> None:
    assert vapid.endpoint_audience(ENDPOINT) == "https://fcm.googleapis.com"
    assert vapid.endpoint_audience("https://push.example.net:8443/x/y") == (
        "https://push.example.net:8443"
    )


def test_audience_rejects_relative_url() -> None:
    with pytest.raises(ValueError):
        vapid.endpoint_audience("/fcm/send/abc")


def test_header_shape_matches_rfc_8292() -> None:
    private_pem, public_b64 = vapid.generate_vapid_key_pair()
    header = vapid.build_vapid_header(
        vapid.load_vapid_private_key(private_pem), endpoint=ENDPOINT, subject=SUBJECT
    )
    assert header.startswith("vapid t=")
    assert f", k={public_b64}" in header
    token = header.split("t=", 1)[1].split(",", 1)[0]
    assert vapid.verify_vapid_token(token, public_b64)["aud"] == "https://fcm.googleapis.com"


def test_signature_is_raw_jws_not_der() -> None:
    """JWS 的 ES256 签名必须是 64 octet 的 R||S，不是 DER。"""
    private_pem, public_b64 = vapid.generate_vapid_key_pair()
    token = vapid.sign_vapid_token(
        vapid.load_vapid_private_key(private_pem), audience="https://x.test", subject=SUBJECT
    )
    from maa_api.services.webpush_crypto import b64url_decode  # noqa: PLC0415

    signature = b64url_decode(token.split(".")[2])
    assert len(signature) == 64
    assert not signature.startswith(b"\x30")  # DER SEQUENCE 头，出现即说明没转换


def test_rejects_non_p256_key() -> None:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    pem = (
        ec.generate_private_key(ec.SECP384R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(ValueError, match="P-256"):
        vapid.load_vapid_private_key(pem)


def test_rejects_malformed_pem() -> None:
    with pytest.raises(ValueError):
        vapid.load_vapid_private_key("not a pem")


def test_rejects_empty_audience() -> None:
    private_pem, _ = vapid.generate_vapid_key_pair()
    with pytest.raises(ValueError):
        vapid.sign_vapid_token(
            vapid.load_vapid_private_key(private_pem), audience="", subject=SUBJECT
        )
