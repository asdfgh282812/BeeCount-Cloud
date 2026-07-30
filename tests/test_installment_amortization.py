"""services.installment_amortization.compute_periods 的纯函式单元测试。

数值全部是脚本手算/交叉验证过的锁定参考值(见开发时用过的验证脚本),
锁住回归 —— 若这些数字变了,先确认是不是攤還公式本身改了(通常不该),
而不是反过来改测试凑数字。
"""
from datetime import datetime, timezone

import pytest

from src.services.installment_amortization import compute_periods

FIRST = datetime(2026, 1, 15, tzinfo=timezone.utc)


def test_equal_principal_no_interest_flat_split():
    periods = compute_periods(
        total_amount=1200, periods=12, first_period_at=FIRST,
        repayment_method="equal_principal", interest_rate=0.0,
    )
    assert len(periods) == 12
    assert all(p.principal_amount == 100.0 for p in periods)
    assert all(p.interest_amount == 0.0 for p in periods)
    assert all(p.total_amount == 100.0 for p in periods)
    assert periods[0].due_at == FIRST
    assert periods[1].due_at == datetime(2026, 2, 15, tzinfo=timezone.utc)
    assert periods[11].due_at == datetime(2026, 12, 15, tzinfo=timezone.utc)


def test_equal_installment_monthly_locked_values():
    periods = compute_periods(
        total_amount=1200, periods=12, first_period_at=FIRST,
        repayment_method="equal_installment", interest_period="monthly",
        interest_rate=0.12,
    )
    assert len(periods) == 12
    assert periods[0].principal_amount == 94.62
    assert periods[0].interest_amount == 12.0
    assert periods[0].total_amount == 106.62
    assert periods[11].principal_amount == 105.56
    assert periods[11].interest_amount == 1.06
    # 取整后本金加总必须精确等于本金(尾差已被 remainder_position 吸收)
    assert round(sum(p.principal_amount for p in periods), 2) == 1200.0


def test_fixed_interest_monthly_flat_principal_and_interest_on_original():
    periods = compute_periods(
        total_amount=1200, periods=12, first_period_at=FIRST,
        repayment_method="fixed_interest", interest_period="monthly",
        interest_rate=0.12,
    )
    assert all(p.principal_amount == 100.0 for p in periods)
    # 固定利率:每期利息都算在原始本金上,不随余额递减而变化
    assert all(p.interest_amount == 12.0 for p in periods)
    assert all(p.total_amount == 112.0 for p in periods)


def test_grace_period_interest_only_then_amortize():
    periods = compute_periods(
        total_amount=1200, periods=12, first_period_at=FIRST,
        repayment_method="equal_principal", interest_period="monthly",
        interest_rate=0.12, grace_period_months=2,
    )
    # 前 2 期只还息,本金为 0
    assert periods[0].principal_amount == 0.0
    assert periods[0].interest_amount == 12.0
    assert periods[1].principal_amount == 0.0
    assert periods[1].interest_amount == 12.0
    # 第 3 期起摊本金:剩余 10 期均摊 1200 → 每期 120
    assert periods[2].principal_amount == 120.0
    assert periods[2].interest_amount == 12.0
    # 余额递减,利息逐期减少
    assert periods[3].interest_amount == 10.8
    assert round(sum(p.principal_amount for p in periods), 2) == 1200.0


def test_equal_installment_daily_interest_period_locked_values():
    periods = compute_periods(
        total_amount=1200, periods=12, first_period_at=FIRST,
        repayment_method="equal_installment", interest_period="daily",
        interest_rate=0.12,
    )
    assert len(periods) == 12
    assert periods[0].principal_amount == 94.38
    assert periods[0].interest_amount == 12.23
    assert periods[0].total_amount == 106.61
    assert round(sum(p.principal_amount for p in periods), 2) == 1200.0


def test_remainder_position_first_vs_last():
    first_variant = compute_periods(
        total_amount=1000, periods=3, first_period_at=FIRST,
        repayment_method="equal_principal", remainder_position="first",
    )
    assert first_variant[0].principal_amount == 333.34
    assert first_variant[1].principal_amount == 333.33
    assert first_variant[2].principal_amount == 333.33

    last_variant = compute_periods(
        total_amount=1000, periods=3, first_period_at=FIRST,
        repayment_method="equal_principal", remainder_position="last",
    )
    assert last_variant[0].principal_amount == 333.33
    assert last_variant[1].principal_amount == 333.33
    assert last_variant[2].principal_amount == 333.34


def test_round_amounts_false_skips_rounding():
    periods = compute_periods(
        total_amount=1000, periods=3, first_period_at=FIRST,
        repayment_method="equal_principal", round_amounts=False,
    )
    # 1000/3 不是精确的两位小数,round_amounts=False 时保留原始浮点精度
    assert periods[0].principal_amount == pytest.approx(1000 / 3)
    assert periods[1].principal_amount == pytest.approx(1000 / 3)
    assert periods[2].principal_amount == pytest.approx(1000 / 3)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(total_amount=0, periods=12, first_period_at=FIRST),
        dict(total_amount=1200, periods=0, first_period_at=FIRST),
        dict(total_amount=1200, periods=12, first_period_at=FIRST, grace_period_months=12),
        dict(total_amount=1200, periods=12, first_period_at=FIRST, repayment_method="bogus"),
        dict(total_amount=1200, periods=12, first_period_at=FIRST, interest_period="bogus"),
        dict(total_amount=1200, periods=12, first_period_at=FIRST, remainder_position="bogus"),
        dict(total_amount=1200, periods=12, first_period_at=FIRST, interest_rate=-0.1),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        compute_periods(**kwargs)
