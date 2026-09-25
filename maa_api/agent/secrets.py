"""Encrypted storage helpers for LLM API keys."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["LLMEncryptionKeyInvalid", "decrypt_api_key", "encrypt_api_key"]

_CIPHERTEXT_PREFIX = "enc:v1:"


class LLMEncryptionKeyInvalid(ValueError):
    """The configured master key is missing, malformed, or cannot decrypt a key."""


def _fernet(key: str) -> Fernet:
    if not isinstance(key, str) or not key:
        raise LLMEncryptionKeyInvalid("LLM encryption key is missing or invalid")
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, TypeError, ValueError):
        raise LLMEncryptionKeyInvalid("LLM encryption key is missing or invalid") from None


def encrypt_api_key(api_key: str, key: str) -> str:
    """Encrypt a plaintext API key using a URL-safe Fernet key."""
    if not isinstance(api_key, str):
        raise TypeError("LLM API key must be a string")
    token = _fernet(key).encrypt(api_key.encode("utf8")).decode("ascii")
    return _CIPHERTEXT_PREFIX + token


def decrypt_api_key(value: str, key: str) -> str:
    """Decrypt a versioned value or return a pre-versioning plaintext value.

    Returning legacy plaintext supports the SettingService's one-time migration on read.
    Unknown ``enc:`` versions fail closed rather than being treated as API keys.
    """
    if not isinstance(value, str):
        raise LLMEncryptionKeyInvalid("Stored LLM API key is invalid")
    if not value.startswith(_CIPHERTEXT_PREFIX):
        if value.startswith("enc:"):
            raise LLMEncryptionKeyInvalid("Stored LLM API key uses an unsupported version")
        return value
    try:
        token = value.removeprefix(_CIPHERTEXT_PREFIX).encode("ascii")
        return _fernet(key).decrypt(token).decode("utf8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, ValueError):
        raise LLMEncryptionKeyInvalid("Stored LLM API key cannot be decrypted") from None
