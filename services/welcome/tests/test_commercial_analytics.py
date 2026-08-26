from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gramly_welcome.business_metrics import weekly_revenue_run_rate
from gramly_welcome.campaigns import campaign_accepts_touch
from gramly_welcome.models import MarketingCampaign


def campaign(*, starts_at: datetime, ends_at: datetime | None, active: bool = True) -> MarketingCampaign:
    return MarketingCampaign(
        name="Placement",
        starts_at=starts_at,
        ends_at=ends_at,
        attribution_window_days=30,
        is_active=active,
    )


def test_weekly_revenue_run_rate_uses_fixed_formula() -> None:
    assert weekly_revenue_run_rate(Decimal("500.00"), 13) == Decimal("1500.00")
    assert weekly_revenue_run_rate(Decimal("5.00"), 1) == Decimal("1.15")


def test_campaign_accepts_touch_only_inside_active_period() -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    current = campaign(starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=1))
    assert campaign_accepts_touch(current, now)
    assert not campaign_accepts_touch(current, now + timedelta(days=1))
    assert not campaign_accepts_touch(
        campaign(starts_at=now - timedelta(days=1), ends_at=None, active=False), now
    )
