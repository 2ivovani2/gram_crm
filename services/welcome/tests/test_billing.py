from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gramly_welcome.billing import payment_token_from_payload, safe_invoice_snapshot
from gramly_welcome.crypto_pay import (
    CryptoInvoice,
    CryptoPayClient,
    parse_invoice,
    webhook_signature_valid,
)
from gramly_welcome.finance import (
    PAYMENT_SETTLEMENT_GRACE,
    PAYMENT_TTL,
    FinanceError,
    add_calendar_year,
    commission_rate,
)


def test_commission_tiers_are_deterministic() -> None:
    assert commission_rate(0) == Decimal("0")
    assert commission_rate(1) == Decimal("10")
    assert commission_rate(5) == Decimal("10")
    assert commission_rate(6) == Decimal("15")
    assert commission_rate(20) == Decimal("15")
    assert commission_rate(21) == Decimal("20")


def test_commission_term_handles_leap_day() -> None:
    assert add_calendar_year(datetime(2024, 2, 29, 12, tzinfo=UTC)) == datetime(
        2025, 2, 28, 12, tzinfo=UTC
    )


def test_crypto_pay_signature_uses_raw_body() -> None:
    token = "123:secret"
    body = b'{"update_id":42,"update_type":"invoice_paid"}'
    secret = hashlib.sha256(token.encode()).digest()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert webhook_signature_valid(token, body, signature)
    assert not webhook_signature_valid(token, body + b" ", signature)


def test_crypto_invoice_parses_fiat_snapshot() -> None:
    invoice = parse_invoice(
        {
            "invoice_id": 77,
            "status": "paid",
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": "1990.00",
            "paid_asset": "USDT",
            "paid_amount": "21.12",
            "paid_fiat_rate": "94.223484",
            "payload": "a434909f-3f5e-4995-af1a-f16856774351",
            "bot_invoice_url": "https://t.me/CryptoBot?start=invoice",
            "mini_app_invoice_url": "https://t.me/CryptoBot/app?startapp=invoice",
        }
    )
    assert invoice.invoice_id == "77"
    assert invoice.amount == Decimal("1990.00")
    assert invoice.paid_amount == Decimal("21.12")


def test_safe_invoice_snapshot_excludes_urls_and_comments() -> None:
    invoice = CryptoInvoice(
        invoice_id="1",
        status="paid",
        amount=Decimal("100"),
        currency_type="fiat",
        fiat="RUB",
        paid_asset="USDT",
        paid_amount=Decimal("1"),
        paid_fiat_rate=Decimal("100"),
        payload="token",
        bot_invoice_url="secret-url",
        mini_app_invoice_url="secret-mini-url",
        raw={"invoice_id": 1, "comment": "PII"},
    )
    snapshot = safe_invoice_snapshot(invoice)
    assert "comment" not in snapshot
    assert "bot_invoice_url" not in snapshot
    assert snapshot["snapshot_hash"]


def test_invalid_checkout_payload_is_rejected() -> None:
    try:
        payment_token_from_payload("not-a-uuid")
    except FinanceError:
        pass
    else:
        raise AssertionError("invalid payload was accepted")


def test_payment_lifetime_contract_is_fifteen_minutes_with_safe_grace() -> None:
    assert PAYMENT_TTL.total_seconds() == 900
    assert PAYMENT_SETTLEMENT_GRACE.total_seconds() == 86_400


@pytest.mark.asyncio
async def test_crypto_pay_invoice_expires_after_fifteen_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_call(_method: str, payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {
            "invoice_id": 1,
            "status": "active",
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": "419.00",
            "payload": "checkout",
        }

    client = CryptoPayClient("token", "https://pay.example")
    monkeypatch.setattr(client, "_call", fake_call)

    await client.create_invoice(
        amount_rub=Decimal("419.00"),
        payload="checkout",
        description="Business",
    )

    assert captured["expires_in"] == 900
