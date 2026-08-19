from __future__ import annotations

from decimal import Decimal

import pytest

from gramly_welcome.commercial import payment_method_ready, public_plan_payload
from gramly_welcome.idempotency import IdempotencyConflictError, request_digest
from gramly_welcome.models import Plan


def plan(**overrides: object) -> Plan:
    values: dict[str, object] = {
        "slug": "pro",
        "display_name": "Pro",
        "entitlements": {"welcome_chains": True},
        "max_bots": 3,
        "max_channels": 25,
        "monthly_delivery_operations": 50_000,
        "media_storage_bytes": 5_368_709_120,
        "price_rub": None,
        "price_xtr": None,
        "referral_base_rub": None,
        "crypto_pay_enabled": False,
        "stars_enabled": False,
        "is_sellable": True,
        "is_active": True,
    }
    values.update(overrides)
    return Plan(**values)


def test_incomplete_prices_are_not_exposed() -> None:
    item = plan(crypto_pay_enabled=True, stars_enabled=True, price_xtr=500)

    assert not payment_method_ready(item, "crypto_pay")
    assert not payment_method_ready(item, "telegram_stars")
    assert public_plan_payload(item)["prices"] == {"rub": None, "xtr": None}


def test_complete_prices_are_exposed() -> None:
    item = plan(
        crypto_pay_enabled=True,
        stars_enabled=True,
        price_rub=Decimal("1990.00"),
        price_xtr=999,
        referral_base_rub=Decimal("1990.00"),
    )

    assert payment_method_ready(item, "crypto_pay")
    assert payment_method_ready(item, "telegram_stars")
    assert public_plan_payload(item)["prices"] == {"rub": "1990.00", "xtr": 999}


def test_unknown_payment_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        payment_method_ready(plan(), "cash")


def test_idempotency_digest_is_canonical() -> None:
    assert request_digest({"a": 1, "b": [2, 3]}) == request_digest(
        {"b": [2, 3], "a": 1}
    )
    assert request_digest({"a": 1}) != request_digest({"a": 2})


def test_idempotency_conflict_is_a_validation_error() -> None:
    assert issubclass(IdempotencyConflictError, ValueError)
