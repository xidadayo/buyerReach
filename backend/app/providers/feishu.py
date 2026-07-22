"""Feishu (Lark) webhook notification provider."""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.providers.base import ProviderResult


class FeishuNotificationProvider:
    """Sends notification cards to a Feishu/Lark webhook URL."""

    name: str = "feishu"

    def __init__(self, webhook_url: str, secret: str | None = None) -> None:
        self.webhook_url = webhook_url
        self.secret = secret

    def send(self, event: str, recipients: list[str], payload: dict) -> ProviderResult:
        card = _build_card(event, payload)
        body = json.dumps({"msg_type": "interactive", "card": card}).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                raw = json.loads(response.read().decode("utf-8"))
            ok = raw.get("code") == 0 or raw.get("StatusCode") == 0
            return ProviderResult(
                ok=ok,
                provider=self.name,
                data={"event": event},
                raw=raw,
                error_message=raw.get("msg") if not ok else None,
            )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return ProviderResult(False, self.name, error_code=f"http_{exc.code}", error_message=detail)
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            return ProviderResult(False, self.name, error_code="request_failed", error_message=str(exc))


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------


_EVENT_LABELS: dict[str, dict[str, str]] = {
    "task.completed": {
        "title": "\U0001f680 任务已完成",
        "color": "green",
    },
    "task.failed": {
        "title": "❌ 任务执行失败",
        "color": "red",
    },
    "task.created": {
        "title": "\U0001f4cb 新任务已创建",
        "color": "blue",
    },
    "brand.created": {
        "title": "\U0001f3ed 品牌已录入",
        "color": "blue",
    },
    "contact.discovered": {
        "title": "\U0001f464 联系人已发现",
        "color": "blue",
    },
    "email.discovered": {
        "title": "✉️ 邮箱已发现",
        "color": "blue",
    },
    "email.verified": {
        "title": "✅ 邮箱已验证",
        "color": "green",
    },
    "duplicate.detected": {
        "title": "⚠️ 发现重复数据",
        "color": "yellow",
    },
    "duplicate.merged": {
        "title": "\U0001f4e6 重复数据已合并",
        "color": "green",
    },
    "provider.quota_low": {
        "title": "\U0001f4ca Provider 配额不足",
        "color": "red",
    },
    "import.completed": {
        "title": "\U0001f4e5 导入完成",
        "color": "green",
    },
    "export.complete": {
        "title": "\U0001f4e4 导出完成",
        "color": "blue",
    },
}


def _build_card(event: str, payload: dict) -> dict:
    label = _EVENT_LABELS.get(event, {"title": f"\U0001f514 {event}", "color": "blue"})
    fields = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        fields.append({"is_short": len(str(value)) < 40, "text": {"tag": "lark_md", "content": f"**{key}**\n{value}"}})

    return {
        "header": {
            "title": {"tag": "plain_text", "content": label["title"]},
            "template": label["color"],
        },
        "elements": [
            {
                "tag": "div",
                "fields": fields[:6],
            },
            {
                "tag": "hr",
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"BuyerReach · {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    }
                ],
            },
        ],
    }
