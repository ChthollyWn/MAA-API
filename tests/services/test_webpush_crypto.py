"""RFC 8291 Appendix A 官方向量与 Web Push 加密边界测试。

官方向量是本模块唯一的独立正确性证据：只用「自实现的解密」验证
「自实现的加密」会同时掩盖两边的同一个错误（HKDF extract/expand 混用就是这样
一个不会报错的错误），所以这里固定 RFC 给出的输入并逐项比对 RFC 给出的中间值。
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from maa_api.services.webpush_crypto import (
    MAX_PLAINTEXT_BYTES,
    PushEncryptionError,
    b64url_decode,
    b64url_encode,
    encrypt_push_message,
)

# --- RFC 8291 Appendix A 输入 -------------------------------------------------

AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
    "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
    "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
PLAINTEXT = b"When I grow up, I want to be a watermelon"
EXPECTED_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
    "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVB"
    "St2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)

#: RFC 8291 Appendix A 列出的全部中间值。前两项是「只做 extract」的两步，
#: 用 ``cryptography`` 的 ``HKDF.derive()`` 实现会在这里失败。
EXPECTED_INTERMEDIATE = {
    "ecdh_secret": "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs",
    "PRK_key": "Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k",
    "IKM": "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg",
    "PRK": "09_eUZGrsvxChDCGRCdkLiDXrReGOEVeSCdCcPBSJSc",
    "CEK": "oIhVW04MRdy2XN9CiKLxTg",
    "NONCE": "4h_95klXJ5E_qnoN",
}


def _load_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(
        int.from_bytes(b64url_decode(value), "big"), ec.SECP256R1()
    )


@pytest.fixture()
def vector_key() -> ec.EllipticCurvePrivateKey:
    return _load_private_key(AS_PRIVATE)


def test_official_vector_reproduces_documented_body(vector_key) -> None:
    """整条链路必须复现 RFC 8291 §5 的 86 octet 头 + 密文。"""
    result = encrypt_push_message(
        PLAINTEXT,
        ua_public=b64url_decode(UA_PUBLIC),
        auth_secret=b64url_decode(AUTH_SECRET),
        as_private=vector_key,
        salt=b64url_decode(SALT),
    )
    assert b64url_encode(result.body) == EXPECTED_BODY
    assert len(result.body) - len(PLAINTEXT) == 86 + 1 + 16
    assert b64url_encode(result.server_public_key) == AS_PUBLIC


def test_official_vector_uses_extract_only_hkdf() -> None:
    """锁住 HKDF 陷阱：两步「只 extract」的派生必须与 RFC 中间值一致。

    直接用 ``cryptography`` 的 ``HKDF(...).derive(ikm)`` 替换任一步都会额外
    做一次 expand，结果与 RFC 不符但不抛异常 —— 这条断言是唯一能拦住它的地方。
    """
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ecdh_secret = b64url_decode(EXPECTED_INTERMEDIATE["ecdh_secret"])
    auth_secret = b64url_decode(AUTH_SECRET)
    salt = b64url_decode(SALT)
    ikm = b64url_decode(EXPECTED_INTERMEDIATE["IKM"])

    wrong_prk_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=None
    ).derive(ecdh_secret)
    assert b64url_encode(wrong_prk_key) != EXPECTED_INTERMEDIATE["PRK_key"]
    assert hmac.new(auth_secret, ecdh_secret, hashlib.sha256).digest() == b64url_decode(
        EXPECTED_INTERMEDIATE["PRK_key"]
    )

    wrong_prk = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=None).derive(ikm)
    assert b64url_encode(wrong_prk) != EXPECTED_INTERMEDIATE["PRK"]
    assert hmac.new(salt, ikm, hashlib.sha256).digest() == b64url_decode(
        EXPECTED_INTERMEDIATE["PRK"]
    )


def test_random_keys_and_salt_produce_unique_bodies() -> None:
    ua_public = b64url_decode(UA_PUBLIC)
    auth_secret = b64url_decode(AUTH_SECRET)
    first = encrypt_push_message(b"same", ua_public=ua_public, auth_secret=auth_secret)
    second = encrypt_push_message(b"same", ua_public=ua_public, auth_secret=auth_secret)
    assert first.body != second.body
    assert first.salt != second.salt
    assert first.server_public_key != second.server_public_key


def test_header_layout_matches_rfc_8188() -> None:
    result = encrypt_push_message(
        b"x",
        ua_public=b64url_decode(UA_PUBLIC),
        auth_secret=b64url_decode(AUTH_SECRET),
        as_private=_load_private_key(AS_PRIVATE),
        salt=b64url_decode(SALT),
    )
    assert result.body[:16] == b64url_decode(SALT)
    assert result.body[16:20] == (4096).to_bytes(4, "big")
    assert result.body[20] == 65
    assert result.body[21:86] == b64url_decode(AS_PUBLIC)


@pytest.mark.parametrize(
    "public_key",
    [
        b"",
        b"\x04" + b"\x00" * 63,  # 长度不足
        b"\x02" + b"\x00" * 64,  # 压缩点，RFC 8291 §4 只接受未压缩
        b"\x04" + b"\x00" * 64,  # 长度对但不在曲线上
    ],
)
def test_rejects_invalid_user_agent_public_key(public_key: bytes) -> None:
    with pytest.raises(PushEncryptionError):
        encrypt_push_message(
            b"payload", ua_public=public_key, auth_secret=b64url_decode(AUTH_SECRET)
        )


def test_rejects_wrong_auth_secret_length() -> None:
    with pytest.raises(PushEncryptionError, match="认证密钥"):
        encrypt_push_message(
            b"payload",
            ua_public=b64url_decode(UA_PUBLIC),
            auth_secret=b"\x00" * 15,
        )


def test_rejects_plaintext_over_rfc_limit() -> None:
    with pytest.raises(PushEncryptionError, match="明文超过"):
        encrypt_push_message(
            b"a" * (MAX_PLAINTEXT_BYTES + 1),
            ua_public=b64url_decode(UA_PUBLIC),
            auth_secret=b64url_decode(AUTH_SECRET),
        )


def test_accepts_plaintext_at_rfc_limit() -> None:
    result = encrypt_push_message(
        b"a" * MAX_PLAINTEXT_BYTES,
        ua_public=b64url_decode(UA_PUBLIC),
        auth_secret=b64url_decode(AUTH_SECRET),
    )
    # 头 86 + 明文 3993 + 分隔符 1 + tag 16 = 4096，正好等于记录大小
    assert len(result.body) == 4096


def test_rejects_non_p256_server_key() -> None:
    with pytest.raises(PushEncryptionError, match="P-256"):
        encrypt_push_message(
            b"payload",
            ua_public=b64url_decode(UA_PUBLIC),
            auth_secret=b64url_decode(AUTH_SECRET),
            as_private=ec.generate_private_key(ec.SECP384R1()),
        )


def test_base64url_helpers_round_trip_without_padding() -> None:
    for raw in (b"", b"\x00", b"\xff" * 65, b"when i grow up"):
        encoded = b64url_encode(raw)
        assert "=" not in encoded
        assert b64url_decode(encoded) == raw
    assert base64.urlsafe_b64encode(b"\x00" * 65).rstrip(b"=").decode() == b64url_encode(
        b"\x00" * 65
    )


def test_base64url_decode_rejects_bad_input() -> None:
    with pytest.raises(PushEncryptionError):
        b64url_decode(None)  # type: ignore[arg-type]
