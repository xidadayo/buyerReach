from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    provider: str
    data: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    cost: float = 0
    error_code: str | None = None
    error_message: str | None = None


class CompanySearchProvider(Protocol):
    name: str

    def search_companies(self, criteria: dict) -> ProviderResult: ...


class ContactSearchProvider(Protocol):
    name: str

    def search_contacts(self, company: dict, titles: list[str], limit: int) -> ProviderResult: ...


class EmailFinderProvider(Protocol):
    name: str

    def find_emails(self, contact: dict, domain: str) -> ProviderResult: ...


class EmailVerifierProvider(Protocol):
    name: str

    def verify_email(self, address: str) -> ProviderResult: ...


class NotificationProvider(Protocol):
    name: str

    def send(self, event: str, recipients: list[str], payload: dict) -> ProviderResult: ...
