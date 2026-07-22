from app.core.crypto import decrypt_provider_config, encrypt_provider_config
from app.modules.services import _masked_config, _merge_provider_config


def test_provider_secrets_are_encrypted_and_decrypted() -> None:
    config = {
        "url": "https://example.test/api",
        "api_key": "super-secret",
        "nested": {"webhook_url": "https://hooks.example.test/secret"},
    }

    encrypted = encrypt_provider_config(config)

    assert encrypted["api_key"] != config["api_key"]
    assert encrypted["nested"]["webhook_url"] != config["nested"]["webhook_url"]
    assert decrypt_provider_config(encrypted) == config


def test_provider_config_edit_preserves_masked_api_key() -> None:
    existing = encrypt_provider_config({"adapter": "hunter", "api_key": "super-secret", "test_email": "before@example.com"})

    updated = _merge_provider_config(existing, {"adapter": "hunter", "api_key": "", "test_email": "after@example.com"})

    assert decrypt_provider_config(updated) == {"adapter": "hunter", "api_key": "super-secret", "test_email": "after@example.com"}


def test_provider_auth_metadata_is_not_encrypted_or_masked() -> None:
    config = {
        "api_key": "super-secret",
        "api_key_header": "x-api-key",
        "api_key_prefix": "",
        "quota_api_key_header": "x-api-key",
        "quota_api_key_prefix": "",
    }

    encrypted = encrypt_provider_config(config)

    assert encrypted["api_key"] != config["api_key"]
    assert encrypted["api_key_header"] == "x-api-key"
    assert encrypted["quota_api_key_header"] == "x-api-key"
    assert _masked_config(encrypted)["api_key"] == "********"
    assert _masked_config(encrypted)["api_key_header"] == "x-api-key"


def test_provider_config_decrypts_legacy_encrypted_auth_metadata() -> None:
    legacy_encrypted_header = encrypt_provider_config({"api_key": "x-api-key"})["api_key"]

    decrypted = decrypt_provider_config({"api_key_header": legacy_encrypted_header})

    assert decrypted["api_key_header"] == "x-api-key"


def test_provider_config_edit_removes_legacy_quota_runtime_state() -> None:
    existing = encrypt_provider_config(
        {
            "adapter": "hunter",
            "api_key": "super-secret",
            "quota_remaining": 3,
            "quota_used": 7,
            "circuit_open_until": "2099-01-01T00:00:00Z",
        }
    )

    updated = _merge_provider_config(
        existing,
        {
            "adapter": "hunter",
            "quota_endpoint_url": "https://configured.example/account",
            "quota_remaining_path": "data.remaining",
        },
    )

    assert decrypt_provider_config(updated) == {
        "adapter": "hunter",
        "api_key": "super-secret",
        "quota_endpoint_url": "https://configured.example/account",
        "quota_remaining_path": "data.remaining",
    }
