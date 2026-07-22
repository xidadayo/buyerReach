from enum import StrEnum


class TaskStatus(StrEnum):
    draft = "draft"
    queued = "queued"
    running = "running"
    paused = "paused"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class EmailStatus(StrEnum):
    raw = "raw"
    pending = "pending"
    valid = "valid"
    risky = "risky"
    unknown = "unknown"
    invalid = "invalid"
    disposable = "disposable"
    do_not_contact = "do_not_contact"


class EmailPool(StrEnum):
    raw = "raw"
    pending_verification = "pending_verification"
    manual_review = "manual_review"
    valid = "valid"
    invalid = "invalid"
    suppressed = "suppressed"


class SourceType(StrEnum):
    official_website = "official_website"
    search_engine = "search_engine"
    commercial_api = "commercial_api"
    public_directory = "public_directory"
    manual_import = "manual_import"
    manual_entry = "manual_entry"
