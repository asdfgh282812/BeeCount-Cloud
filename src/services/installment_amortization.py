"""分期付款攤還演算法(§2.12.1 Phase 1.5,MOZE_FEATURE_GAP_SD.md)。

纯函式,无 DB 依赖 —— 给定分期参数,一次算出全部期数的本金/利息/合计明细。
`src/routers/write/installment_plans.py` 的 POST(建立計畫)/
rebalance-from(調利率連同未來)/ early-repay-principal(部分還本連同未來)
都调这里的 `compute_periods`。

**几个文件本身没写死、这里明确采用的解读约定**(需求文件只给了方法名跟
参数名,没给公式,落地时必须挑一个具体算法,这里记录选择跟理由):

- `interest_rate` 是**年利率**(如 0.06 = 6%/年)。`interest_period` 只决定
  "每期利息按月计(rate/12,每期固定)还是按日计(rate/365 * 该期实际天数,
  月份长短会让每期利息略有差异)",**不影响还款週期本身**——週期一律按月
  (`add_months`),这是"計息方式"跟"還款頻率"两个独立维度里,只有前者被
  这个字段控制。
- 寬限期(grace_period_months)对三种攤還方式一视同仁:前 g 期只還息不還本
  (principal=0,interest=按当期利率乘当前余额),从第 g+1 期才开始摊本金。
- 取整(round_amounts)+ 余数位置(remainder_position):取整后每期本金加总
  可能跟 total_amount 有 1 分以内的尾差,全部塞进 remainder_position 指定
  的那一期(first=第一个摊还期,last=最后一期),该期利息不变,合计
  (total_amount)重新算 principal+interest。round_amounts=False 时完全跳过
  取整/尾差调整,保留浮点数原始精度。
- `equal_installment`(等額本息)+ `interest_period="daily"` 时,标准年金公式
  假设每期利率相同,但按日计息下每期天数不同(28/30/31 天),利率会略有差异,
  没有闭式解可以直接套。这里用折现因子法广义化:对每个摊还期 k 的利率 r_k,
  折现因子 D_k = Π_{j<=k}(1+r_j),固定总付款 A 满足
  `sum(A / D_k for k) == 剩余本金`,即 `A = 剩余本金 / sum(1/D_k)`,算出 A 后
  逐期反推本金/利息(interest_k = balance_{k-1} * r_k,principal_k = A -
  interest_k)。这是整份计算里风险最高的一段数学延伸,已在单元测试里锁
  对照值,若跟未来实际业务需求（例如银行等额本息按月计息不管日历天数）
  有出入,以本模块 docstring 记录的假设为准去调整测试,不要反过来改这里的
  数学去凑测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..snapshot_mutator import add_months

REPAYMENT_METHODS = frozenset({"equal_installment", "equal_principal", "fixed_interest"})
INTEREST_PERIODS = frozenset({"monthly", "daily"})
REMAINDER_POSITIONS = frozenset({"first", "last"})


@dataclass(frozen=True)
class PeriodPlan:
    period_no: int
    due_at: datetime
    principal_amount: float
    interest_amount: float
    total_amount: float


def _round2(x: float) -> float:
    return round(x, 2)


def _period_rates(
    first_period_at: datetime,
    due_dates: list[datetime],
    interest_period: str,
    interest_rate: float,
) -> list[float]:
    """每期利率(monthly:每期固定 rate/12;daily:按当期实际天数 rate/365*days)。

    daily 的"当期天数"= 该期到期日跟上一期到期日之间的天数;第一期没有
    "上一期",用 `first_period_at` 往前推一个月当作虚拟的上一期到期日,
    让第一期也有一个跟其它期一致、可预期的天数(约 28-31 天)。
    """
    if interest_period == "monthly":
        r = interest_rate / 12
        return [r] * len(due_dates)
    rates: list[float] = []
    prev = add_months(first_period_at, -1)
    for d in due_dates:
        days = (d - prev).days
        rates.append(interest_rate / 365 * days)
        prev = d
    return rates


def compute_periods(
    *,
    total_amount: float,
    periods: int,
    first_period_at: datetime,
    repayment_method: str = "equal_principal",
    interest_period: str = "monthly",
    interest_rate: float = 0.0,
    round_amounts: bool = True,
    remainder_position: str = "last",
    grace_period_months: int = 0,
) -> list[PeriodPlan]:
    if repayment_method not in REPAYMENT_METHODS:
        raise ValueError(f"invalid repayment_method: {repayment_method}")
    if interest_period not in INTEREST_PERIODS:
        raise ValueError(f"invalid interest_period: {interest_period}")
    if remainder_position not in REMAINDER_POSITIONS:
        raise ValueError(f"invalid remainder_position: {remainder_position}")
    if periods < 1:
        raise ValueError("periods must be >= 1")
    if total_amount <= 0:
        raise ValueError("total_amount must be > 0")
    if interest_rate < 0:
        raise ValueError("interest_rate must be >= 0")
    if not (0 <= grace_period_months < periods):
        raise ValueError("grace_period_months must be >= 0 and < periods")

    due_dates = [first_period_at] + [add_months(first_period_at, k) for k in range(1, periods)]
    rates = _period_rates(first_period_at, due_dates, interest_period, interest_rate)

    m = periods - grace_period_months  # 摊还期数(不含宽限期)
    balance = total_amount
    results: list[PeriodPlan] = []

    for k in range(grace_period_months):
        interest = balance * rates[k]
        results.append(PeriodPlan(k + 1, due_dates[k], 0.0, interest, interest))

    if repayment_method in ("equal_principal", "fixed_interest"):
        flat_principal = total_amount / m
        for i in range(m):
            k = grace_period_months + i
            interest = (
                total_amount * rates[k]
                if repayment_method == "fixed_interest"
                else balance * rates[k]
            )
            principal = flat_principal
            results.append(PeriodPlan(k + 1, due_dates[k], principal, interest, principal + interest))
            balance -= principal
    else:  # equal_installment
        amort_rates = rates[grace_period_months:grace_period_months + m]
        if interest_period == "monthly":
            r = amort_rates[0] if amort_rates else 0.0
            payment = (
                balance / m
                if r == 0
                else balance * r * (1 + r) ** m / ((1 + r) ** m - 1)
            )
        else:
            discount_factors: list[float] = []
            cum = 1.0
            for r in amort_rates:
                cum *= 1 + r
                discount_factors.append(cum)
            sum_inv = sum(1.0 / d for d in discount_factors)
            payment = balance / sum_inv if sum_inv else balance / m
        for i in range(m):
            k = grace_period_months + i
            r = rates[k]
            interest = balance * r
            principal = payment - interest
            results.append(PeriodPlan(k + 1, due_dates[k], principal, interest, principal + interest))
            balance -= principal

    if round_amounts:
        results = _apply_rounding(results, total_amount, grace_period_months, remainder_position)

    return results


def _apply_rounding(
    results: list[PeriodPlan],
    total_amount: float,
    grace_period_months: int,
    remainder_position: str,
) -> list[PeriodPlan]:
    rounded = [
        PeriodPlan(r.period_no, r.due_at, _round2(r.principal_amount), _round2(r.interest_amount), 0.0)
        for r in results
    ]
    principal_sum = round(sum(r.principal_amount for r in rounded), 2)
    diff = round(total_amount - principal_sum, 2)
    if diff:
        amort_indices = [i for i, r in enumerate(rounded) if r.period_no > grace_period_months]
        if amort_indices:
            idx = amort_indices[0] if remainder_position == "first" else amort_indices[-1]
            adjusted = rounded[idx]
            rounded[idx] = PeriodPlan(
                adjusted.period_no, adjusted.due_at,
                _round2(adjusted.principal_amount + diff), adjusted.interest_amount, 0.0,
            )
    return [
        PeriodPlan(r.period_no, r.due_at, r.principal_amount, r.interest_amount,
                   _round2(r.principal_amount + r.interest_amount))
        for r in rounded
    ]
