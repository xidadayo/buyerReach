import pytest
from pydantic import ValidationError

from app.modules.schemas import UserCreate


def test_user_create_accepts_internal_local_email() -> None:
    payload = UserCreate(
        name="Sales",
        email=" Sale1@BuyerReach.Local ",
        password="zihe123456",
    )
    assert payload.email == "sale1@buyerreach.local"


def test_user_create_accepts_and_normalizes_public_email() -> None:
    payload = UserCreate(name="Sales", email="SALE@EXAMPLE.COM", password="zihe123456")
    assert payload.email == "sale@example.com"


@pytest.mark.parametrize("email", ["missing-at", "user@localhost", "user@.local"])
def test_user_create_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError):
        UserCreate(name="Sales", email=email, password="zihe123456")
