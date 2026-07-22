from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import Base
from app.modules import services
from app.modules.models import SearchTask, TaskStageCheckpoint, TaskVendorPlan, VendorCredential, VendorStrategy
from app.modules.schemas import VendorCredentialUpdate
from app.providers.base import ProviderResult


def _database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_standalone_api_key_encryption_and_blank_update_preserves_key() -> None:
    encrypted = encrypt_secret("master-key")
    assert encrypted != "master-key"
    assert decrypt_secret(encrypted) == "master-key"
    with _database() as db:
        credential = VendorCredential(vendor="apollo", encrypted_api_key=encrypted, enabled=True)
        db.add(credential)
        db.flush()
        services.update_vendor_credential(db, credential, VendorCredentialUpdate(api_key="", enabled=False))
        assert credential.encrypted_api_key == encrypted
        assert credential.enabled is False
        assert services.vendor_credential_public(credential)["api_key"] == "********"


def test_task_vendor_plan_is_immutable_after_default_strategy_changes() -> None:
    with _database() as db:
        db.add(VendorStrategy(name="default", primary_vendor="apollo", fallback_vendors=["prospeo"], verification_vendor="zerobounce", adapter_version="v1"))
        task = SearchTask(name="task", mode="exact_brand", filters={}, progress={})
        db.add(task)
        db.flush()
        first = services.ensure_task_vendor_plan(db, task)
        strategy = db.query(VendorStrategy).one()
        strategy.primary_vendor = "hunter"
        strategy.fallback_vendors = ["apollo"]
        second = services.ensure_task_vendor_plan(db, task)
        assert first.id == second.id
        assert second.primary_vendor == "apollo"
        assert second.fallback_vendors == ["prospeo"]


def test_completed_checkpoint_is_reused_without_calling_provider(monkeypatch) -> None:
    with _database() as db:
        db.add(VendorStrategy(name="default", primary_vendor="apollo", fallback_vendors=[], verification_vendor="zerobounce", adapter_version="v1"))
        db.add(VendorCredential(vendor="apollo", encrypted_api_key=encrypt_secret("key"), enabled=True))
        task = SearchTask(name="task", mode="exact_brand", filters={}, progress={})
        db.add(task)
        db.flush()
        calls = 0

        def fake_quota(_provider, _config):
            return ProviderResult(True, "apollo", data={"remaining": 10})

        def fake_execute(provider, _payload):
            nonlocal calls
            calls += 1
            return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

        monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)
        monkeypatch.setattr(services, "execute_provider", fake_execute)
        payload = {"mode": "exact_brand", "brand_keywords": ["Acme"]}
        first = services.execute_provider_waterfall(db, "company_search", payload, "companies", task=task)
        second = services.execute_provider_waterfall(db, "company_search", payload, "companies", task=task)
        checkpoint = db.query(TaskStageCheckpoint).one()
        plan = db.query(TaskVendorPlan).one()
        assert first[1] == second[1] == [{"brand_name": "Acme"}]
        assert calls == 1
        assert checkpoint.status == "completed"
        assert checkpoint.attempts == 1
        assert plan.primary_vendor == "apollo"


def test_failed_stage_records_switch_and_completes_with_fallback(monkeypatch) -> None:
    with _database() as db:
        db.add(VendorStrategy(name="default", primary_vendor="apollo", fallback_vendors=["prospeo"], verification_vendor="zerobounce", adapter_version="v1"))
        db.add_all([
            VendorCredential(vendor="apollo", encrypted_api_key=encrypt_secret("apollo-key"), enabled=True),
            VendorCredential(vendor="prospeo", encrypted_api_key=encrypt_secret("prospeo-key"), enabled=True),
        ])
        task = SearchTask(name="task", mode="exact_brand", filters={}, progress={})
        db.add(task)
        db.flush()

        monkeypatch.setattr(
            services,
            "check_vendor_provider_quota",
            lambda provider, _config: ProviderResult(True, provider.provider, data={"remaining": 10}),
        )

        def fake_execute(provider, _payload):
            if provider.provider.startswith("apollo-"):
                return ProviderResult(False, provider.provider, error_code="http_429", error_message="retry after 60")
            return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

        monkeypatch.setattr(services, "execute_provider", fake_execute)
        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {"mode": "exact_brand", "brand_keywords": ["Acme"]},
            "companies",
            task=task,
        )
        checkpoints = db.query(TaskStageCheckpoint).order_by(TaskStageCheckpoint.vendor).all()
        assert provider is not None and provider.provider.startswith("prospeo-")
        assert items == [{"brand_name": "Acme"}]
        assert errors == ["apollo-company-search: retry after 60"]
        assert [(item.vendor, item.status, item.error_code) for item in checkpoints] == [
            ("apollo", "failed", "http_429"),
            ("prospeo", "completed", None),
        ]
