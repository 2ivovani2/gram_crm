from __future__ import annotations

from decimal import Decimal

import pytest

from gramly_welcome.commercial import payment_method_ready, public_plan_payload
from gramly_welcome.idempotency import IdempotencyConflictError, request_digest
from gramly_welcome.models import Plan


def plan(**overrides: object) -> Plan:
    values: dict[str, object] = {
        "slug": "business",
        "display_name": "Business",
        "entitlements": {"welcome_chains": True, "rotation": True, "ad_free": True},
        "max_bots": 15,
        "max_channels": 150,
        "monthly_delivery_operations": 250_000,
        "media_storage_bytes": 26_843_545_600,
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


def test_free_and_business_share_product_limits_and_features() -> None:
    business = plan()
    free = plan(
        slug="free",
        display_name="Free",
        entitlements={"welcome_chains": True, "rotation": False, "ad_free": False},
        is_sellable=True,
    )

    assert free.max_bots == business.max_bots
    assert free.max_channels == business.max_channels
    assert free.monthly_delivery_operations == business.monthly_delivery_operations
    assert free.media_storage_bytes == business.media_storage_bytes
    assert free.entitlements.keys() == business.entitlements.keys()
    assert {
        key: value for key, value in free.entitlements.items() if key not in {"rotation", "ad_free"}
    } == {
        key: value
        for key, value in business.entitlements.items()
        if key not in {"rotation", "ad_free"}
    }
    assert free.entitlements["rotation"] is False
    assert free.entitlements["ad_free"] is False
    assert business.entitlements["rotation"] is True
    assert business.entitlements["ad_free"] is True


def test_unknown_payment_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        payment_method_ready(plan(), "cash")


def test_idempotency_digest_is_canonical() -> None:
    assert request_digest({"a": 1, "b": [2, 3]}) == request_digest({"b": [2, 3], "a": 1})
    assert request_digest({"a": 1}) != request_digest({"a": 2})


def test_idempotency_conflict_is_a_validation_error() -> None:
    assert issubclass(IdempotencyConflictError, ValueError)
