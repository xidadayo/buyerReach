from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.security import authenticate_user, hash_password
from app.modules.models import User


def test_five_failed_logins_lock_the_user() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(email="lockout@example.test", name="Lockout", password_hash=hash_password("correct-password"))
        db.add(user)
        db.commit()

        for _ in range(5):
            assert authenticate_user(db, user.email, "incorrect-password") is None

        assert user.locked_until is not None
        assert authenticate_user(db, user.email, "correct-password") is None
