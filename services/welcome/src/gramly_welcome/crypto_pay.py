from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


class CryptoPayError(RuntimeError):
    pass


@dataclass(frozen=True)
class CryptoInvoice:
    invoice_id: str
    status: str
    amount: Decimal
    currency_type: str
    fiat: str | None
    paid_asset: str | None
    paid_amount: Decimal | None
    paid_fiat_rate: Decimal | None
    payload: str
    bot_invoice_url: str
    mini_app_invoice_url: str
    raw: dict[str, Any]


class CryptoPayClient:
    def __init__(self, token: str, base_url: str, *, timeout: float = 15.0) -> None:
        if not token:
            raise CryptoPayError("Crypto Pay token is not configured")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _call(self, method: str, payload: dict[str, object]) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/{method}",
                    headers={"Crypto-Pay-API-Token": self.token},
                    json=payload,
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CryptoPayError(f"Crypto Pay {method} request failed") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            error = body.get("error") if isinstance(body, dict) else "invalid response"
            raise CryptoPayError(f"Crypto Pay rejected request: {error}")
        return body.get("result")

    async def create_invoice(
        self, *, amount_rub: Decimal, payload: str, description: str
    ) -> CryptoInvoice:
        result = await self._call(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": format(amount_rub, ".2f"),
                "accepted_assets": "USDT,TON",
                "payload": payload,
                "description": description[:1024],
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )
        if not isinstance(result, dict):
            raise CryptoPayError("Crypto Pay returned an invalid invoice")
        return parse_invoice(result)

    async def get_invoice(self, invoice_id: str) -> CryptoInvoice:
        result = await self._call("getInvoices", {"invoice_ids": invoice_id, "count": 1})
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise CryptoPayError("Crypto Pay invoice was not found")
        return parse_invoice(items[0])

    async def exchange_rate(self, source: str, target: str = "RUB") -> Decimal:
        result = await self._call("getExchangeRates", {})
        if not isinstance(result, list):
            raise CryptoPayError("Crypto Pay returned invalid exchange rates")
        for row in result:
            if (
                isinstance(row, dict)
                and row.get("source") == source
                and row.get("target") == target
                and row.get("is_valid") is not False
            ):
                return Decimal(str(row["rate"]))
        raise CryptoPayError(f"Exchange rate {source}/{target} is unavailable")

    async def transfer(
        self, *, user_id: int, amount: Decimal, spend_id: str, comment: str
    ) -> dict[str, Any]:
        result = await self._call(
            "transfer",
            {
                "user_id": user_id,
                "asset": "USDT",
                "amount": format(amount, "f"),
                "spend_id": spend_id,
                "comment": comment[:1024],
            },
        )
        if not isinstance(result, dict):
            raise CryptoPayError("Crypto Pay returned an invalid transfer")
        return result

    async def get_transfer(self, spend_id: str) -> dict[str, Any] | None:
        result = await self._call("getTransfers", {"spend_id": spend_id, "count": 1})
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or not items:
            return None
        transfer = items[0]
        return transfer if isinstance(transfer, dict) else None


def webhook_signature_valid(token: str, body: bytes, signature: str) -> bool:
    if not token or not signature:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_invoice(raw: dict[str, Any]) -> CryptoInvoice:
    def optional_decimal(key: str) -> Decimal | None:
        value = raw.get(key)
        return Decimal(str(value)) if value not in (None, "") else None

    return CryptoInvoice(
        invoice_id=str(raw["invoice_id"]),
        status=str(raw.get("status", "")),
        amount=Decimal(str(raw["amount"])),
        currency_type=str(raw.get("currency_type", "")),
        fiat=str(raw["fiat"]) if raw.get("fiat") else None,
        paid_asset=str(raw["paid_asset"]) if raw.get("paid_asset") else None,
        paid_amount=optional_decimal("paid_amount"),
        paid_fiat_rate=optional_decimal("paid_fiat_rate"),
        payload=str(raw.get("payload", "")),
        bot_invoice_url=str(raw.get("bot_invoice_url", "")),
        mini_app_invoice_url=str(raw.get("mini_app_invoice_url", "")),
        raw=raw,
    )
