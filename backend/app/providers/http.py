import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from app.core.crypto import decrypt_provider_config
from app.modules.brand_discovery import build_discovery_query
from app.modules.models import ProviderConfig
from app.providers.base import ProviderResult
from app.providers.errors import format_http_error
from app.providers.vendors import CONFIGURABLE_CATALOG_ADAPTERS


def execute_provider(provider: ProviderConfig, payload: dict) -> ProviderResult:
    config = decrypt_provider_config(provider.config or {})
    if _is_builtin_adapter(provider):
        from app.providers.builtin import execute_builtin_provider

        return execute_builtin_provider(provider, payload, config)
    adapter = str(config.get("adapter") or "").lower()
    if adapter in {"apollo", "hunter", "zerobounce", "aftership_local"}:
        from app.providers.vendors import execute_vendor_provider

        return execute_vendor_provider(provider, payload, config)
    if adapter in CONFIGURABLE_CATALOG_ADAPTERS:
        from app.providers.vendors import execute_vendor_provider

        return execute_vendor_provider(provider, payload, config)

    request_payload = dict(payload)
    if provider.type == "company_search":
        request_payload["query"] = build_discovery_query(payload, str(config.get("query_template") or ""))
    url = _render_url_template(str(config.get("url") or "").strip(), request_payload)
    if not url:
        return ProviderResult(
            ok=False,
            provider=provider.provider,
            error_code="missing_url",
            error_message="Provider URL is not configured",
        )

    headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items()}
    headers.setdefault("Content-Type", "application/json")
    api_key = config.get("api_key")
    if api_key:
        headers[str(config.get("api_key_header") or "Authorization")] = _format_api_key(
            str(api_key), str(config.get("api_key_prefix") or "Bearer")
        )

    method = str(config.get("method") or "POST").upper()
    body = None if method == "GET" else json.dumps(request_payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=int(config.get("timeout") or 30)) as response:
            raw = json.loads(response.read().decode("utf-8"))
        data = _at_path(raw, str(config.get("response_path") or ""))
        if not isinstance(data, dict):
            data = {"items": data}
        return ProviderResult(ok=True, provider=provider.provider, data=data, raw=raw)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        return ProviderResult(
            False,
            provider.provider,
            error_code=f"http_{exc.code}",
            error_message=format_http_error(exc.code, detail, retry_after),
        )
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return ProviderResult(False, provider.provider, error_code="request_failed", error_message=str(exc))


def extract_items(provider: ProviderConfig, data: dict, default_path: str) -> list[dict]:
    config = decrypt_provider_config(provider.config or {})
    path = str(config.get("items_path") or default_path)
    items = _at_path(data, path)
    if items is None and "items" in data:
        items = data["items"]
    if not isinstance(items, list):
        return []
    field_map = config.get("field_map") or {}
    if not field_map:
        return [item for item in items if isinstance(item, dict)]
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            mapped = {target: _at_path(item, str(source)) for target, source in field_map.items()}
            if provider.type == "company_search" and mapped.get("country"):
                mapped.setdefault("country_scope", str(config.get("country_semantics") or "unknown"))
            normalized.append(mapped)
    return normalized


def _is_builtin_adapter(provider: ProviderConfig) -> bool:
    config = decrypt_provider_config(provider.config or {})
    adapter = str(config.get("adapter") or "").strip().lower()
    return adapter in {"builtin", "local"} or provider.provider.lower().startswith("builtin")


def _at_path(value: object, path: str) -> object:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _format_api_key(api_key: str, prefix: str) -> str:
    return f"{prefix} {api_key}".strip() if prefix else api_key


def _render_url_template(url: str, payload: dict) -> str:
    """Replace {{query}} and list placeholders for GET-based search APIs."""
    for key, value in payload.items():
        rendered = ",".join(str(item) for item in value) if isinstance(value, list) else str(value)
        url = url.replace(f"{{{{{key}}}}}", quote_plus(rendered))
    return url
