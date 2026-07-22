from types import SimpleNamespace

from app.modules.services import _provider_test_error_message
from app.providers.base import ProviderResult


def test_apollo_connection_test_explains_master_key_requirement() -> None:
    provider = SimpleNamespace(provider="apollo-contact-search")
    result = ProviderResult(False, "apollo-contact-search", error_code="http_403", error_message="forbidden")

    message = _provider_test_error_message(provider, {"adapter": "apollo"}, result)

    assert message is not None
    assert "Master API Key" in message
    assert "403" in message


def test_apollo_connection_test_explains_connection_interruption() -> None:
    provider = SimpleNamespace(provider="apollo-contact-search")
    result = ProviderResult(
        False,
        "apollo-contact-search",
        error_code="request_failed",
        error_message="<urlopen error Remote end closed connection without response>",
    )

    message = _provider_test_error_message(provider, {"adapter": "apollo"}, result)

    assert message is not None
    assert "无法验证" in message
    assert "网络出口" in message
