"""Provider-safe error messages for API responses."""

import json
import re


def format_http_error(status_code: int, body: str, retry_after: str | None = None) -> str:
    """Keep gateway HTML out of the UI while retaining useful API JSON errors."""
    message = _json_error_message(body)
    if not message:
        message = _plain_error_message(status_code, body)
    if retry_after:
        message = f"{message}; retry after {retry_after}"
    return message


def _json_error_message(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    details = [
        str(payload[key]).strip()
        for key in ("error_code", "error", "message", "detail", "filter_error")
        if payload.get(key) not in (None, "", False)
    ]
    return ": ".join(dict.fromkeys(details))[:500] or None


def _plain_error_message(status_code: int, body: str) -> str:
    if "<html" in body.lower() or "<body" in body.lower():
        if status_code == 403:
            return "Provider returned HTTP 403. The provider gateway rejected the request; check API key permissions and provider-side access policy"
        if status_code == 429:
            return "Provider returned HTTP 429. The provider rate limit was reached"
        return f"Provider returned HTTP {status_code}. The provider gateway rejected the request"
    normalized = re.sub(r"\s+", " ", body).strip()
    return normalized[:500] or f"Provider returned HTTP {status_code}"
