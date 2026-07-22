import json

from app.modules import ai_coordinator
from app.modules.schemas import AISettingsUpdate


def test_chat_completion_uses_configured_timeout(monkeypatch) -> None:
    captured: dict[str, int] = {}
    response_body = {
        "choices": [{"message": {"content": json.dumps({"task": {}, "steps": [], "warnings": []})}}]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(response_body).encode("utf-8")

    def fake_urlopen(_request, timeout: int):
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_coordinator, "urlopen", fake_urlopen)

    ai_coordinator._request_chat_completion(
        "Find handbag brands",
        {
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-chat",
            "api_key": "secret",
            "request_timeout_seconds": 75,
        },
        [],
    )

    assert captured["timeout"] == 75


def test_ai_timeout_defaults_to_sixty_seconds() -> None:
    assert AISettingsUpdate().request_timeout_seconds == 60
