from decimal import Decimal

import pytest
from django.urls import reverse

from apps.crm.calculator import calculate_ad_slots
from apps.crm.forms import AdSlotCalculatorForm
from apps.users.models import User, UserRole


def test_calculator_matches_product_example():
    result = calculate_ad_slots(
        weekly_target=Decimal("125000"), average_price=Decimal("5000"), paid_slots=4
    )
    assert result.actual_daily == Decimal("20000")
    assert result.actual_weekly == Decimal("140000")
    assert result.deviation == Decimal("15000")
    assert result.required_paid_slots == 4
    assert result.maximum_weekly == Decimal("245000")
    assert result.achievable is True


def test_calculator_marks_target_above_capacity():
    result = calculate_ad_slots(
        weekly_target=Decimal("300000"), average_price=Decimal("5000"), paid_slots=7
    )
    assert result.required_paid_slots == 9
    assert result.maximum_weekly == Decimal("245000")
    assert result.achievable is False


@pytest.mark.parametrize(
    ("target", "expected_required", "achievable"),
    [("0", 0, True), ("1", None, False)],
)
def test_zero_price_is_handled_without_division_error(target, expected_required, achievable):
    result = calculate_ad_slots(
        weekly_target=Decimal(target), average_price=Decimal("0"), paid_slots=0
    )
    assert result.required_paid_slots == expected_required
    assert result.achievable is achievable


@pytest.mark.parametrize(
    "overrides",
    [
        {"weekly_target": "-1"},
        {"average_price": "-1"},
        {"paid_slots": "1.5"},
        {"paid_slots": "8", "vp_slots": "0", "repayment_slots": "0"},
        {"paid_slots": "3", "vp_slots": "2", "repayment_slots": "1"},
    ],
)
def test_form_rejects_invalid_money_or_distribution(overrides):
    data = {
        "weekly_target": "125000",
        "average_price": "5000",
        "paid_slots": "4",
        "vp_slots": "2",
        "repayment_slots": "1",
        **overrides,
    }
    assert not AdSlotCalculatorForm(data).is_valid()


@pytest.mark.django_db
def test_authenticated_employee_can_open_calculator(client):
    user = User.objects.create_user(
        telegram_id=99112233, username="calculator-worker", role=UserRole.WORKER
    )
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    response = client.get(reverse("crm:ad_calculator"), HTTP_HOST="localhost")
    content = response.content.decode().replace(" ", "").replace(" ", "")
    assert response.status_code == 200
    assert "140000" in content
    assert "Калькуляторрекламныхслотов" in content


@pytest.mark.django_db
def test_calculator_requires_crm_session(client):
    response = client.get(reverse("crm:ad_calculator"), HTTP_HOST="localhost")
    assert response.status_code == 302
    assert response.url == "https://crm.gramly.tech/crm/login/"
