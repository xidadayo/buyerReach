import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_SENSITIVE_TOKENS = ("key", "token", "secret", "password", "webhook")
_NON_SECRET_AUTH_METADATA = {
    "api_key_header",
    "api_key_prefix",
    "api_key_query_param",
    "quota_api_key_header",
    "quota_api_key_prefix",
    "quota_api_key_query_param",
}
_PREFIX = "enc:v1:"


def encrypt_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    return _transform(config, encrypt=True)


def decrypt_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    return _transform(config, encrypt=False)


def encrypt_secret(value: str) -> str:
    """Encrypt a standalone credential using the existing Provider key material."""
    return str(_transform(value, encrypt=True, key_name="api_key"))


def decrypt_secret(value: str) -> str:
    """Decrypt a standalone credential while accepting legacy plaintext during migration."""
    return str(_transform(value, encrypt=False, key_name="api_key"))


def _transform(value: Any, *, encrypt: bool, key_name: str = "") -> Any:
    if isinstance(value, dict):
        return {key: _transform(item, encrypt=encrypt, key_name=key) for key, item in value.items()}
    if isinstance(value, list):
        return [_transform(item, encrypt=encrypt, key_name=key_name) for item in value]
    if not isinstance(value, str):
        return value
    if encrypt:
        if not is_sensitive_config_key(key_name):
            return value
        return value if value.startswith(_PREFIX) else _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    if not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value.removeprefix(_PREFIX).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt Provider configuration") from exc


def is_sensitive_config_key(key_name: object) -> bool:
    normalized = str(key_name).lower()
    if normalized in _NON_SECRET_AUTH_METADATA:
        return False
    return any(token in normalized for token in _SENSITIVE_TOKENS)


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.encryption_key.encode("utf-8")).digest())
    return Fernet(key)
