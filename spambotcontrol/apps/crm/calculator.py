"""Pure business rules for the seven-slot advertising calculator."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING


DAYS_PER_WEEK = 7
SLOTS_PER_DAY = 7


@dataclass(frozen=True)
class AdSlotCalculation:
    daily_target: Decimal
    actual_daily: Decimal
    actual_weekly: Decimal
    deviation: Decimal
    required_paid_slots: int | None
    maximum_weekly: Decimal
    achievable: bool


def calculate_ad_slots(*, weekly_target: Decimal, average_price: Decimal, paid_slots: int) -> AdSlotCalculation:
    """Calculate revenue capacity for one fixed seven-slot day."""
    if weekly_target < 0 or average_price < 0:
        raise ValueError("Money values cannot be negative.")
    if not 0 <= paid_slots <= SLOTS_PER_DAY:
        raise ValueError("Paid slots must be between 0 and 7.")

    daily_target = weekly_target / Decimal(DAYS_PER_WEEK)
    actual_daily = Decimal(paid_slots) * average_price
    actual_weekly = actual_daily * Decimal(DAYS_PER_WEEK)
    maximum_weekly = Decimal(SLOTS_PER_DAY * DAYS_PER_WEEK) * average_price

    if average_price == 0:
        required_paid_slots = 0 if weekly_target == 0 else None
    else:
        required_paid_slots = int((daily_target / average_price).to_integral_value(rounding=ROUND_CEILING))

    return AdSlotCalculation(
        daily_target=daily_target,
        actual_daily=actual_daily,
        actual_weekly=actual_weekly,
        deviation=actual_weekly - weekly_target,
        required_paid_slots=required_paid_slots,
        maximum_weekly=maximum_weekly,
        achievable=weekly_target <= maximum_weekly,
    )
