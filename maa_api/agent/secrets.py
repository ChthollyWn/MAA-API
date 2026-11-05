"""敏感配置的加密存储（09 §8.2）。

同一个主密钥（``MAA_LLM_ENCRYPTION_KEY``）与同一种 ``enc:v1:`` 密文格式服务
所有落库的敏感项：LLM API 密钥、四个 IM 通道密钥，以及后续里程碑的 MCP 凭据。
通用名是 :func:`encrypt_secret` / :func:`decrypt_secret`；:func:`encrypt_api_key`
与 :func:`decrypt_api_key` 是同实现的旧名，保留给 M67 的模型档案与既有调用方。

``decrypt_secret`` 对不带前缀的旧明文原样返回，供 ``SettingService`` 在读取时
一次性迁移；未知的 ``enc:`` 版本一律 fail closed，不把密文当明文用。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "LLMEncryptionKeyInvalid",
    "decrypt_api_key",
    "decrypt_secret",
    "encrypt_api_key",
    "encrypt_secret",
]

_CIPHERTEXT_PREFIX = "enc:v1:"


class LLMEncryptionKeyInvalid(ValueError):
    """The configured master key is missing, malformed, or cannot decrypt a value."""


def _fernet(key: str) -> Fernet:
    if not isinstance(key, str) or not key:
        raise LLMEncryptionKeyInvalid("LLM encryption key is missing or invalid")
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, TypeError, ValueError):
        raise LLMEncryptionKeyInvalid("LLM encryption key is missing or invalid") from None


def encrypt_secret(secret: str, key: str) -> str:
    """Encrypt a plaintext secret using a URL-safe Fernet key."""
    if not isinstance(secret, str):
        raise TypeError("Secret must be a string")
    token = _fernet(key).encrypt(secret.encode("utf8")).decode("ascii")
    return _CIPHERTEXT_PREFIX + token


def decrypt_secret(value: str, key: str) -> str:
    """Decrypt a versioned value or return a pre-versioning plaintext value.

    Returning legacy plaintext supports the SettingService's one-time migration on read.
    Unknown ``enc:`` versions fail closed rather than being treated as plaintext secrets.
    """
    if not isinstance(value, str):
        raise LLMEncryptionKeyInvalid("Stored secret is invalid")
    if not value.startswith(_CIPHERTEXT_PREFIX):
        if value.startswith("enc:"):
            raise LLMEncryptionKeyInvalid("Stored secret uses an unsupported version")
        return value
    try:
        token = value.removeprefix(_CIPHERTEXT_PREFIX).encode("ascii")
        return _fernet(key).decrypt(token).decode("utf8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, ValueError):
        raise LLMEncryptionKeyInvalid("Stored secret cannot be decrypted") from None


#: M67 模型档案与既有调用方使用的旧名；与通用名同实现（09 §8.2 第 6 条）。
def encrypt_api_key(api_key: str, key: str) -> str:
    """Alias of :func:`encrypt_secret` kept for the model-profile callers."""
    return encrypt_secret(api_key, key)


def decrypt_api_key(value: str, key: str) -> str:
    """Alias of :func:`decrypt_secret` kept for the model-profile callers."""
    return decrypt_secret(value, key)
