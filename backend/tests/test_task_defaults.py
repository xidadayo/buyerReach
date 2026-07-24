from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import SystemSetting
from app.modules.services import get_task_defaults


def test_task_defaults_use_configured_p1_titles_and_contact_limit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(
            SystemSetting(
                key="system",
                value={
                    "title_dictionary": {"p1": ["Purchasing Director", "Head of Buying"]},
                    "task_rules": {"default_contact_limit": 8},
                },
            )
        )
        db.flush()

        assert get_task_defaults(db) == {
            "target_titles": ["Purchasing Director", "Head of Buying"],
            "contacts_limit_per_brand": 8,
        }


def test_task_defaults_do_not_replace_an_explicitly_empty_p1_list() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(SystemSetting(key="system", value={"title_dictionary": {"p1": []}}))
        db.flush()

        assert get_task_defaults(db)["target_titles"] == []
