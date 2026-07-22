import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.models import ProviderConfig, SystemSetting, VendorCredential, VendorStrategy
from app.pipeline.definition import PIPELINE_V1

SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token", "encrypted_api_key"}


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if key.casefold() not in SENSITIVE_KEYS
            and not any(
                fragment in key.casefold()
                for fragment in (
                    "api_key",
                    "authorization",
                    "password",
                    "secret",
                    "token",
                    "credential",
                )
            )
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def capture_configuration(db: Session) -> tuple[str, dict[str, Any]]:
    strategy = db.scalar(select(VendorStrategy).order_by(VendorStrategy.updated_at.desc()))
    providers = db.scalars(select(ProviderConfig).where(ProviderConfig.enabled.is_(True))).all()
    credentials = db.scalars(
        select(VendorCredential).where(VendorCredential.enabled.is_(True))
    ).all()
    settings = db.scalars(select(SystemSetting)).all()
    snapshot = {
        "pipeline": PIPELINE_V1.__dict__,
        "vendor_strategy": (
            _safe({c.name: getattr(strategy, c.name) for c in strategy.__table__.columns})
            if strategy
            else {}
        ),
        "providers": [
            _safe({c.name: getattr(p, c.name) for c in p.__table__.columns}) for p in providers
        ],
        "credential_refs": [
            {"vendor": item.vendor, "credential_id": str(item.id)} for item in credentials
        ],
        "settings": {item.key: _safe(item.value) for item in settings},
    }
    canonical = json.dumps(snapshot, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16], json.loads(
        json.dumps(snapshot, default=str)
    )
