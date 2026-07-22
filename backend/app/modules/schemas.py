import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.modules.brand_discovery import COUNTRY_ALIASES


def _country_code(value: str) -> str | None:
    normalized = re.sub(r"[^\w]+", "", value.strip().casefold())
    return COUNTRY_ALIASES.get(normalized)


def _split_list_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        for term in re.split(r"[,，、;；\n]+", value):
            cleaned = term.strip()
            if cleaned and cleaned.casefold() not in {item.casefold() for item in terms}:
                terms.append(cleaned)
    return terms


class SearchTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mode: Literal["exact_brand", "brand_discovery", "excel_import"]
    brand_keywords: list[str] = Field(default_factory=list)
    official_domains: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    category_match_mode: Literal["all", "any"] = "any"
    company_types: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(
        default_factory=lambda: ["Buyer", "Head of Buying", "Sourcing Manager", "Procurement Manager"]
    )
    contacts_limit_per_brand: int = Field(default=5, ge=1, le=50)
    require_website: bool = True
    min_relevance: int = Field(default=45, ge=0, le=100)
    email_requirement: str = "valid_and_risky"
    brand_limit: int = Field(default=100, ge=1, le=5000)
    budget_limit: float | None = Field(default=None, ge=0)
    discovery_candidate_id: UUID | None = None
    original_prompt: str | None = Field(default=None, max_length=2000)
    search_intent: dict[str, Any] | None = None
    selected_vendors: list[Literal["apollo", "hunter"]] | None = Field(
        default=None, min_length=1, max_length=2
    )

    @model_validator(mode="after")
    def validate_selected_vendors(self) -> "SearchTaskCreate":
        if self.selected_vendors is not None:
            if len(set(self.selected_vendors)) != len(self.selected_vendors):
                raise ValueError("selected_vendors contains duplicates")
            for v in self.selected_vendors:
                if v not in {"apollo", "hunter"}:
                    raise ValueError(f"Unknown vendor: {v}")
        return self

    @model_validator(mode="after")
    def require_categories_for_brand_discovery(self) -> "SearchTaskCreate":
        seen: set[str] = set()
        unique_keywords: list[str] = []
        for keyword in self.brand_keywords:
            cleaned = keyword.strip()
            normalized = cleaned.casefold()
            if cleaned and normalized not in seen:
                seen.add(normalized)
                unique_keywords.append(cleaned)
        self.brand_keywords = unique_keywords
        if self.mode == "brand_discovery":
            countries = [value.strip() for value in self.countries if value.strip()]
            categories: list[str] = []
            for cleaned in _split_list_terms(self.categories):
                if not cleaned:
                    continue
                country_code = _country_code(cleaned)
                if country_code:
                    canonical = country_code.upper()
                    if canonical.casefold() not in {item.casefold() for item in countries}:
                        countries.append(canonical)
                    continue
                if cleaned.casefold() not in {item.casefold() for item in categories}:
                    categories.append(cleaned)
            self.countries = countries
            self.categories = categories
            self.brand_keywords = []
            # Company discovery returns companies only. Contact roles belong to
            # a later, explicitly requested contact-enrichment task.
            self.target_titles = []
            if not categories:
                raise ValueError("Brand discovery tasks require at least one target category")
            if not countries:
                raise ValueError("Brand discovery tasks require at least one target country")
        elif self.mode == "exact_brand" and not self.brand_keywords:
            raise ValueError("Exact brand tasks require at least one target brand name")
        return self


class SearchTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    mode: str
    status: str
    filters: dict
    progress: dict
    error_message: str | None = None
    configuration_version: str | None = None
    pipeline_version: str | None = None
    trace_id: str | None = None
    search_intent: dict[str, Any] = Field(default_factory=dict)
    intent_schema_version: str | None = None
    intent_prompt_version: str | None = None
    knowledge_snapshot: dict[str, Any] = Field(default_factory=dict)


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    company_name: str | None = None
    website: str | None = None
    country: str | None = None
    category: str | None = None


class BrandBatchRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class BrandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    company_name: str | None = None
    website: str | None = None
    country: str | None = None
    category: str | None = None
    status: str | None = None


class DiscoveryCandidateReject(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class DiscoveryCandidateApprove(BaseModel):
    task_id: UUID
    target_titles: list[str] = Field(min_length=1, max_length=100)
    contacts_limit_per_brand: int = Field(default=5, ge=1, le=50)


class DiscoveryCandidateBatchRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=50)


class DiscoveryCandidateBulkApprove(BaseModel):
    task_id: UUID
    candidate_ids: list[UUID] = Field(min_length=1, max_length=200)
    target_titles: list[str] | None = Field(default=None, max_length=100)
    contacts_limit_per_brand: int = Field(default=5, ge=1, le=50)


class ContactCreate(BaseModel):
    brand_id: UUID | None = None
    company_id: UUID | None = None
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = ""
    title: str = Field(min_length=1, max_length=255)
    linkedin_url: str | None = None


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    linkedin_url: str | None = None
    status: str | None = None


class EmailCreate(BaseModel):
    contact_id: UUID | None = None
    brand_id: UUID | None = None
    address: EmailStr
    type: str = "personal"


class EmailUpdate(BaseModel):
    type: str | None = None
    is_primary: bool | None = None


class EmailVerifyRequest(BaseModel):
    email_id: UUID | None = None
    address: EmailStr | None = None


class EmailReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "suppress"]
    reason: str | None = None


class EmailBatchRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class ContactBatchRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class ProviderConfigCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    type: Literal["company_search", "contact_search", "brand_email_search", "email_finder", "email_verifier", "notification"]
    priority: int = Field(default=100, ge=0)
    quota: int | None = Field(default=None, ge=0)
    enabled: bool = False
    config: dict = Field(default_factory=dict)


class ProviderConfigUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    type: Literal["company_search", "contact_search", "brand_email_search", "email_finder", "email_verifier", "notification"] | None = None
    priority: int | None = Field(default=None, ge=0)
    quota: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    config: dict | None = None


class VendorCredentialUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class VendorStrategyUpdate(BaseModel):
    primary_vendor: Literal["apollo", "hunter", "prospeo"]
    fallback_vendors: list[Literal["apollo", "hunter", "prospeo"]] = Field(default_factory=list, max_length=2)
    verification_vendor: Literal["aftership_local", "zerobounce", "hunter"] | None = None
    local_verification_mode: Literal["disabled", "shadow", "active"] = "disabled"
    local_verification_rollout: int = Field(default=0, ge=0, le=100)
    local_verification_sample: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_vendor_order(self) -> "VendorStrategyUpdate":
        if self.primary_vendor in self.fallback_vendors:
            raise ValueError("Primary Vendor cannot also be a fallback Vendor")
        if len(set(self.fallback_vendors)) != len(self.fallback_vendors):
            raise ValueError("Fallback Vendors cannot contain duplicates")
        return self


class DedupMergeRequest(BaseModel):
    entity_type: Literal["brand", "contact", "email"]
    primary_id: UUID
    duplicate_ids: list[UUID] = Field(min_length=1)


class ExportRequest(BaseModel):
    entity_type: Literal["brands", "contacts", "emails", "tasks", "audit_logs"]
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class BlacklistCreate(BaseModel):
    type: Literal["email", "domain"]
    value: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class SystemSettingsUpdate(BaseModel):
    title_dictionary: dict[str, list[str]] = Field(default_factory=dict)
    email_rules: dict[str, int] = Field(default_factory=dict)
    task_rules: dict[str, int] = Field(default_factory=dict)
    ai: "AISettingsUpdate" = Field(default_factory=lambda: AISettingsUpdate())


class AISettingsUpdate(BaseModel):
    enabled: bool = False
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    model_name: str = Field(default="gpt-4o-mini", max_length=120)
    request_timeout_seconds: int = Field(default=60, ge=10, le=180)
    api_key: str = Field(default="", max_length=500)


class AITaskPlanRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=2000)


class AITaskPlanRead(BaseModel):
    task: SearchTaskCreate
    steps: list[str]
    warnings: list[str] = Field(default_factory=list)
    source: Literal["ai", "local_rules"]
    search_intent: dict[str, Any]
    requires_confirmation: bool = False
    ai_attempted: bool = False
    fallback_reason: str | None = None


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    permissions: dict[str, list[str]] = Field(default_factory=dict)


class RoleUpdate(BaseModel):
    permissions: dict[str, list[str]] = Field(default_factory=dict)


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role_id: UUID | None = None
    department_id: UUID | None = None
    status: Literal["active", "disabled"] = "active"


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role_id: UUID | None = None
    department_id: UUID | None = None
    status: Literal["active", "disabled"] | None = None


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    module: Literal["brands", "contacts", "emails"]


class TagUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CustomFieldCreate(BaseModel):
    module: Literal["brands", "contacts", "emails"]
    name: str = Field(min_length=1, max_length=120)
    type: Literal["text", "number", "date", "single_select", "multi_select", "boolean", "url", "email", "phone"] = "text"
    is_required: bool = False
    is_searchable: bool = True
    show_in_list: bool = True


class CustomFieldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    type: Literal[
        "text",
        "number",
        "date",
        "single_select",
        "multi_select",
        "boolean",
        "url",
        "email",
        "phone",
    ] | None = None
    is_required: bool | None = None
    is_searchable: bool | None = None
    show_in_list: bool | None = None


class CustomValueUpsert(BaseModel):
    value: Any
