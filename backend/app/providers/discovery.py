"""Discovery Source Adapter — vendor-neutral contract + bridge to existing Providers.

Every source (Hunter, Apollo, Prospeo, future free sources) exposes the same
protocol so the scheduler never needs to know vendor-specific details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── Capability types ────────────────────────────────────────────────────────


@dataclass
class SourceCapabilities:
    """What a discovery source can and cannot do — declarative and versioned."""

    supported_operations: list[str] = field(default_factory=lambda: ["discover"])
    supports_keyword_tags: bool = False
    supports_keyword_exclude: bool = False
    supports_industry_include: bool = False
    supports_industry_exclude: bool = False
    supports_business_type: bool = False
    supports_country_include: bool = True
    supports_country_exclude: bool = False
    country_semantics: str = "headquarters"  # headquarters | registered | origin
    keyword_tag_semantics: str = "recall"    # exact | recall
    business_type_semantics: str = "recall"  # exact | recall
    pagination_type: str = "offset"           # offset | page | cursor
    page_size: int = 20
    max_pages: int | None = None              # None = unlimited (governed by empty pages)
    max_concurrency: int = 1
    rate_limit_per_minute: int | None = None
    requires_credential: bool = True
    can_save_results: bool = True
    evidence_level: str = "provider_query"


class UnsupportedFilterError(ValueError):
    """Raised when a slice has filters this source cannot satisfy."""
    pass


# ── Source page — the return type of one call ────────────────────────────────


@dataclass
class SourcePage:
    """Normalized result of one adapter call (one page of one slice)."""

    ok: bool
    provider: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0
    next_cursor: dict[str, Any] | None = None  # None = exhausted
    vendor_request_id: str | None = None
    cost: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    unsupported_filters: list[str] = field(default_factory=list)
    page_number: int = 1


# ── Adapter protocol ────────────────────────────────────────────────────────


@runtime_checkable
class DiscoverySourceAdapter(Protocol):
    """Every source implements this protocol; no vendor specifics leak to callers."""

    def capabilities(self) -> SourceCapabilities:
        """Return immutable capability declaration."""
        ...

    def plan(
        self,
        query_slice: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert a vendor-neutral slice + task context into a source request plan.

        Returns a dict with ``payload``, ``operation``, ``filters_applied``,
        and ``unsupported_filters``.
        """
        ...

    def execute(
        self,
        request_plan: dict[str, Any],
        cursor: dict[str, Any] | None,
    ) -> SourcePage:
        """Execute one page of the request plan, optionally resuming from a cursor."""
        ...

    def normalize(self, response: Any) -> list[dict[str, Any]]:
        """Convert raw provider response into a list of SourceCandidate dicts."""
        ...

    def check_availability(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return {ok, error_code, error_message, quota_remaining}."""
        ...


# ── Bridge: wraps existing Provider Waterfall ────────────────────────────────


@dataclass
class ConfiguredProviderDiscoveryAdapter:
    """Reuse the existing Provider Waterfall for company_search operations.

    Converts slice → provider_filter_payload → execute_provider_waterfall.
    """

    provider_name: str
    capabilities_overrides: dict[str, Any] | None = None

    def capabilities(self) -> SourceCapabilities:
        mapping: dict[str, dict[str, Any]] = {
            "hunter": {
                "supports_keyword_tags": True,
                "supports_business_type": False,
                "supports_industry_include": False,
                "country_semantics": "headquarters_or_registered",
                "page_size": 20,
            },
            "apollo": {
                "supports_keyword_tags": True,
                "supports_business_type": True,
                "supports_industry_include": True,
                "country_semantics": "headquarters",
                "page_size": 20,
            },
            "prospeo": {
                "supports_keyword_tags": False,
                "supports_business_type": False,
                "supports_industry_include": False,
                "country_semantics": "headquarters",
                "page_size": 25,
            },
        }
        base = mapping.get(self.provider_name, {})
        if self.capabilities_overrides:
            base = {**base, **self.capabilities_overrides}
        return SourceCapabilities(**base)

    def plan(
        self, query_slice: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a waterfall-compatible payload from a slice."""
        caps = self.capabilities()
        unsupported: list[str] = []

        # Countries
        countries = list(query_slice.get("countries") or [])
        if countries and not caps.supports_country_include:
            unsupported.append("country filter applied post-Provider (strict)")

        # Keywords / target concepts
        concepts = list(query_slice.get("target_concepts") or [])
        include_terms = list(query_slice.get("include_terms") or [])
        keywords = list(dict.fromkeys(concepts + include_terms))

        if keywords and not caps.supports_keyword_tags:
            unsupported.append(f"keyword '{', '.join(keywords[:3])}' applied post-Provider")

        # Business types
        biz_types = list(query_slice.get("business_types") or [])
        if biz_types and not caps.supports_business_type:
            unsupported.append(f"business_type filter ({', '.join(biz_types)}) applied post-Provider")

        payload: dict[str, Any] = {
            "operation": "company_search",
            "countries": countries,
            "categories": keywords,
            "company_types": biz_types,
            "category_match_mode": query_slice.get("match_mode", "any"),
            "limit": caps.page_size,
            "exclude_terms": list(query_slice.get("exclude_terms") or []),
        }

        return {
            "payload": payload,
            "operation": "discover",
            "filters_applied": {
                "countries": countries if caps.supports_country_include else [],
                "keyword_tags": keywords if caps.supports_keyword_tags else [],
                "business_types": biz_types if caps.supports_business_type else [],
            },
            "unsupported_filters": unsupported,
        }

    def execute(
        self,
        request_plan: dict[str, Any],
        cursor: dict[str, Any] | None,
        *,
        db: Any = None,
        task: Any = None,
    ) -> SourcePage:
        """Execute one page using the SPECIFIED provider (not waterfall).

        ``cursor`` should contain ``page`` (int) for offset-based pagination.
        ``db`` and ``task`` are from the caller's transaction when available.
        """
        from app.modules.models import ProviderConfig
        from app.modules.services import (
            enabled_providers,
            execute_provider,
            extract_items,
            record_usage,
        )
        from app.core.crypto import decrypt_provider_config

        page = (cursor or {}).get("page", 1)
        payload = dict(request_plan["payload"])
        payload["page"] = page

        # Find the SPECIFIC named provider, do NOT run waterfall
        provider_config: ProviderConfig | None = None
        if db is not None:
            for p in enabled_providers(db, "company_search", task):
                config = decrypt_provider_config(p.config or {})
                adapter_name = str(config.get("adapter") or "").lower()
                if (
                    p.provider == self.provider_name
                    or p.provider.startswith(f"{self.provider_name}-")
                    or adapter_name == self.provider_name
                ):
                    provider_config = p
                    break

        if provider_config is None:
            return SourcePage(
                ok=False,
                provider=self.provider_name,
                error_code="provider_not_found",
                error_message=f"No enabled provider found for '{self.provider_name}'",
                unsupported_filters=request_plan.get("unsupported_filters", []),
                page_number=page,
            )

        # Execute via the existing single-provider path
        try:
            result = execute_provider(
                provider_config,
                {"operation": "company_search", **payload},
            )
        except Exception as exc:
            return SourcePage(
                ok=False,
                provider=self.provider_name,
                error_code="adapter_error",
                error_message=str(exc)[:2000],
                page_number=page,
            )

        if db is not None and provider_config is not None:
            record_usage(db, provider_config, result.cost)

        if not result.ok:
            return SourcePage(
                ok=False,
                provider=self.provider_name,
                error_code=result.error_code,
                error_message=result.error_message,
                cost=result.cost,
                page_number=page,
            )

        items = extract_items(provider_config, result.data, "companies")
        raw_count = len(items)
        candidates = _normalize_candidates(items, self.provider_name)
        has_more = raw_count >= (self.capabilities().page_size or 20)
        next_cursor = {"page": page + 1} if has_more else None

        return SourcePage(
            ok=True,
            provider=self.provider_name,
            candidates=candidates,
            raw_count=raw_count,
            next_cursor=next_cursor,
            cost=result.cost,
            vendor_request_id=result.raw.get("request_id") if isinstance(result.raw, dict) else None,
            page_number=page,
            unsupported_filters=request_plan.get("unsupported_filters", []),
        )

    def normalize(self, response: Any) -> list[dict[str, Any]]:
        return _normalize_candidates(response, self.provider_name)

    def check_availability(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "quota_remaining": None}


def _normalize_candidates(items: list[dict], provider: str) -> list[dict[str, Any]]:
    """Extract normalized candidate fields from raw Provider items."""
    out: list[dict[str, Any]] = []
    for item in items:
        out.append({
            "brand_name": str(item.get("brand_name") or item.get("name") or "").strip(),
            "domain": str(item.get("domain") or "").strip() or None,
            "website": str(item.get("website") or item.get("url") or "").strip() or None,
            "country": str(item.get("country") or "").strip() or None,
            "headquarters_country": str(item.get("headquarters_country") or "").strip() or None,
            "category": str(item.get("category") or "").strip() or None,
            "business_type": str(item.get("business_type") or "").strip() or None,
            "emails_count": int(item.get("emails_count") or 0),
            "relevance_score": int(item.get("relevance_score") or 0),
            "provider": provider,
            "raw_data": item,
        })
    return out
