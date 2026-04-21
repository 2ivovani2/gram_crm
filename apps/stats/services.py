"""
MetricsService — product analytics for /stats dashboard.

Metrics computed here:
  1. Conversion Rate  — /start → reached 60 applications
  2. Retention Rate   — weekly cohort retention from first activity to deactivation
  3. CPA / CAC / Activation Conversion — tied to WeeklyAdSpend

All queries use indexed fields and avoid N+1 patterns.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from django.db.models import Count, Q
from django.utils import timezone


# ── Helpers ────────────────────────────────────────────────────────────────────

def _monday(d: datetime.date) -> datetime.date:
    """Return the Monday of the ISO week containing date d."""
    return d - datetime.timedelta(days=d.weekday())


def _week_range(week_start: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Return (start_dt, end_dt) for the week beginning on Monday week_start."""
    tz = timezone.get_current_timezone()
    start = datetime.datetime.combine(week_start, datetime.time.min, tzinfo=tz)
    end = datetime.datetime.combine(week_start + datetime.timedelta(days=7), datetime.time.min, tzinfo=tz)
    return start, end


def _period_bounds(period: str) -> tuple[datetime.datetime, datetime.datetime]:
    """Return (start, end) datetimes for 'day', 'week', 'month', or 'all'."""
    now = timezone.now()
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    if period == "day":
        start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
        end = now
    elif period == "week":
        start = datetime.datetime.combine(_monday(today), datetime.time.min, tzinfo=tz)
        end = now
    elif period == "month":
        start = datetime.datetime.combine(today.replace(day=1), datetime.time.min, tzinfo=tz)
        end = now
    else:  # 'all'
        start = datetime.datetime(2020, 1, 1, tzinfo=tz)
        end = now
    return start, end


# ── MetricsService ─────────────────────────────────────────────────────────────

class MetricsService:

    CONVERSION_THRESHOLD = 60  # applications

    # ── 1. Conversion Rate ────────────────────────────────────────────────────

    @staticmethod
    def conversion_rate(period: str = "all") -> dict:
        """
        Returns:
          starts       — unique users who sent /start in the period
                         (approximated by User.created_at — each user is created exactly once)
          converted    — users who reached CONVERSION_THRESHOLD applications
                         and whose reached_60_at falls in the period
          rate_pct     — converted / starts * 100 (float, rounded to 1 decimal)
          threshold    — the threshold value
          period       — period label
        """
        from apps.users.models import User
        start, end = _period_bounds(period)

        starts = User.objects.filter(created_at__gte=start, created_at__lte=end).count()

        # Users who first crossed the threshold within the same period
        converted = User.objects.filter(
            reached_60_at__gte=start,
            reached_60_at__lte=end,
        ).count()

        rate = round(converted / starts * 100, 1) if starts > 0 else 0.0

        return {
            "starts": starts,
            "converted": converted,
            "rate_pct": rate,
            "threshold": MetricsService.CONVERSION_THRESHOLD,
            "period": period,
        }

    @staticmethod
    def conversion_by_periods() -> list[dict]:
        """Return conversion stats for day / week / month / all for the dashboard."""
        return [MetricsService.conversion_rate(p) for p in ("day", "week", "month", "all")]

    # ── 2. Retention Rate ─────────────────────────────────────────────────────

    @staticmethod
    def retention_cohorts(num_weeks: int = 8) -> list[dict]:
        """
        Weekly retention cohorts.

        Cohort definition: workers grouped by the ISO week of their first_activity_at
        (or activated_at as fallback). Only WORKER/CURATOR roles counted.

        For each cohort:
          week_0  = all workers in cohort
          week_N  = workers still active at cohort_start + N weeks
                    (i.e. status=active OR deactivated_at > cohort_start + N*7 days)

        Returns list of dicts sorted by cohort_start desc, limited to last `num_cohorts` weeks:
          cohort_start: date
          cohort_label: "dd.mm"
          cohort_size:  int
          retention:    [100.0, pct_w1, pct_w2, ...]  (length = available weeks)
        """
        from apps.users.models import User, UserRole

        today = timezone.localdate()
        tz = timezone.get_current_timezone()

        # Look back num_weeks * 2 calendar weeks to get enough cohort history
        lookback_start = _monday(today) - datetime.timedelta(weeks=num_weeks * 2)
        lookback_start_dt = datetime.datetime.combine(lookback_start, datetime.time.min, tzinfo=tz)

        # Workers with any activity in our window
        workers = list(
            User.objects
            .filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
            )
            .filter(
                Q(first_activity_at__gte=lookback_start_dt) |
                Q(activated_at__gte=lookback_start_dt)
            )
            .values("pk", "first_activity_at", "activated_at", "status", "deactivated_at")
        )

        if not workers:
            return []

        # Group into cohorts by week_start of their first activity
        cohort_map: dict[datetime.date, list[dict]] = {}
        for w in workers:
            anchor = w["first_activity_at"] or w["activated_at"]
            if not anchor:
                continue
            cohort_week = _monday(anchor.date())
            cohort_map.setdefault(cohort_week, []).append(w)

        results = []
        cohort_weeks = sorted(cohort_map.keys(), reverse=True)[:num_weeks]

        for cohort_start in cohort_weeks:
            cohort = cohort_map[cohort_start]
            cohort_size = len(cohort)
            if cohort_size == 0:
                continue

            # How many weeks have passed since this cohort started
            weeks_passed = (today - cohort_start).days // 7
            max_weeks = min(weeks_passed + 1, num_weeks)

            retention = []
            for w_n in range(max_weeks):
                check_date = cohort_start + datetime.timedelta(weeks=w_n)
                check_dt = datetime.datetime.combine(
                    check_date + datetime.timedelta(days=7),  # end of that week
                    datetime.time.min, tzinfo=tz,
                )
                still_active = sum(
                    1 for w in cohort
                    if _worker_active_at(w, check_dt)
                )
                pct = round(still_active / cohort_size * 100, 1) if cohort_size > 0 else 0.0
                retention.append(pct)

            results.append({
                "cohort_start": cohort_start,
                "cohort_label": cohort_start.strftime("%d.%m"),
                "cohort_size": cohort_size,
                "retention": retention,  # [100.0, pct_w1, ...]
            })

        return results

    # ── 3. CPA / CAC / Activation Conversion ─────────────────────────────────

    @staticmethod
    def acquisition_metrics(num_weeks: int = 8) -> list[dict]:
        """
        Per-week acquisition funnel + ad spend metrics.

        For each of the last num_weeks calendar weeks returns:
          week_start        : date (Monday)
          week_label        : "dd.mm"
          ad_spend          : Decimal (0 if not entered)
          starts            : users created in this week (/start)
          activated         : users activated in this week
          converted         : users who reached 60 apps in this week
          activation_conv_pct : activated / starts * 100
          cpa               : ad_spend / activated  (None if 0)
          cac               : ad_spend / converted  (None if 0)
        """
        from apps.users.models import User
        from apps.stats.models import WeeklyAdSpend

        today = timezone.localdate()
        tz = timezone.get_current_timezone()

        # Collect ad spend records for fast lookup
        spend_lookup: dict[datetime.date, Decimal] = {
            r.week_start: r.amount
            for r in WeeklyAdSpend.objects.all()
        }

        results = []
        for i in range(num_weeks - 1, -1, -1):  # oldest first
            week_start = _monday(today) - datetime.timedelta(weeks=i)
            week_end = week_start + datetime.timedelta(days=7)
            s_dt = datetime.datetime.combine(week_start, datetime.time.min, tzinfo=tz)
            e_dt = datetime.datetime.combine(week_end, datetime.time.min, tzinfo=tz)

            starts = User.objects.filter(created_at__gte=s_dt, created_at__lt=e_dt).count()
            activated = User.objects.filter(activated_at__gte=s_dt, activated_at__lt=e_dt).count()
            converted = User.objects.filter(reached_60_at__gte=s_dt, reached_60_at__lt=e_dt).count()

            ad_spend = spend_lookup.get(week_start, Decimal("0"))

            activation_conv = round(activated / starts * 100, 1) if starts > 0 else 0.0
            cpa = (ad_spend / activated).quantize(Decimal("0.01")) if activated > 0 else None
            cac = (ad_spend / converted).quantize(Decimal("0.01")) if converted > 0 else None

            results.append({
                "week_start": week_start,
                "week_label": week_start.strftime("%d.%m"),
                "ad_spend": ad_spend,
                "starts": starts,
                "activated": activated,
                "converted": converted,
                "activation_conv_pct": activation_conv,
                "cpa": cpa,
                "cac": cac,
            })

        return results

    @staticmethod
    def upsert_ad_spend(week_start: datetime.date, amount: Decimal, notes: str = "") -> None:
        """Save or update advertising spend for the given week."""
        from apps.stats.models import WeeklyAdSpend
        # Ensure week_start is Monday
        week_start = _monday(week_start)
        obj, created = WeeklyAdSpend.objects.get_or_create(
            week_start=week_start,
            defaults={"amount": amount, "notes": notes},
        )
        if not created:
            obj.amount = amount
            obj.notes = notes
            obj.save(update_fields=["amount", "notes", "updated_at"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _worker_active_at(worker_dict: dict, at_dt: datetime.datetime) -> bool:
    """
    Returns True if the worker was still active at the given datetime.
    Logic:
      - if status == 'active' and deactivated_at is None → always active
      - if deactivated_at is not None → active iff deactivated_at > at_dt
      - if status in ('banned', 'inactive') and no deactivated_at → treat as active
        (deactivated_at=None means we don't know when; assume active for safety)
    """
    deactivated_at = worker_dict.get("deactivated_at")
    if deactivated_at is None:
        # No deactivation timestamp recorded; treat as still active
        return True
    return deactivated_at > at_dt


# ── Track metric fields in User ────────────────────────────────────────────────

CONVERSION_THRESHOLD = MetricsService.CONVERSION_THRESHOLD


def update_user_metrics(user_pk: int, new_total: int) -> None:
    """
    Called after attracted_count changes. Updates first_activity_at and reached_60_at
    if the corresponding thresholds are crossed for the first time.
    Uses update() to avoid race conditions on concurrent writes.
    """
    from apps.users.models import User

    now = timezone.now()
    fields_to_set: dict = {}

    user = User.objects.filter(pk=user_pk).values(
        "first_activity_at", "reached_60_at"
    ).first()
    if not user:
        return

    if new_total > 0 and user["first_activity_at"] is None:
        fields_to_set["first_activity_at"] = now

    if new_total >= CONVERSION_THRESHOLD and user["reached_60_at"] is None:
        fields_to_set["reached_60_at"] = now

    if fields_to_set:
        User.objects.filter(pk=user_pk).update(**fields_to_set)
