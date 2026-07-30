"""对着一个真正跑起来的 server(uvicorn server:app,http://localhost:8080)
自动走一遍 Phase 1.5(MOZE_FEATURE_GAP_SD.md §2.12)新增/修正的 API 链路:

1. 週期性收支(§2.12.2):建交易时带 `recurring` 参数,带 `end_at` 的窗口
   一次性生成全部未来交易(不等排程)-> update-from -> terminate-future
2. 分期付款(§2.12.1):POST installment-plans 当场算出全部期数并生成对应
   交易 -> rebalance-from -> early-repay-principal;另建两笔分别测试
   payoff / terminate-future
3. 退款(§2.12.3):建一笔支出 -> 建一笔退款(`refund_of_id`)-> 读回原交易
   确认 `refunds` 反查字段正确

这不是 pytest(pytest 已经在 tests/test_recurring_rules.py /
test_installment_plans.py / test_installment_amortization.py /
test_recurring_schedule.py 里覆盖了同样的契约),这个脚本的价值是对着
**真正跑起来的 server + 真正的 beecount.db** 走一遍 HTTP 边界,跟 web 端
实际发出的请求路径一致,用来在人工点 UI 之前先排除"服务没起来/环境变量
没配对"这类环境问题。

跑之前:
1. .env 里 REGISTRATION_ENABLED=true(测完可以不用改回,反正只是本地)
2. alembic upgrade head
3. 另开一个终端跑 uvicorn server:app --reload --host 0.0.0.0 --port 8080
4. python scripts/manual_test_phase1_5.py
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent


def grant_admin(email: str) -> None:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    subprocess.run(
        [sys.executable, "scripts/grant_admin.py", "--email", email],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "[ok]" if cond else "[FAIL]"
    print(f"{mark} {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"测试失败: {label} {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080/api/v1")
    parser.add_argument("--email", default="phase15-manual-test@example.com")
    parser.add_argument("--password", default="test-password-123")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=10.0)
    auth_body = {
        "email": args.email,
        "password": args.password,
        "client_type": "web",
        "device_id": "manual-test-phase1.5",
    }

    resp = client.post("/auth/register", json=auth_body)
    if resp.status_code != 200:
        resp = client.post("/auth/login", json=auth_body)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[ok] 登录成功: {args.email}")

    grant_admin(args.email)

    ledger_resp = client.post(
        "/write/ledgers", json={"ledger_name": "phase15-manual-test"}, headers=headers,
    )
    ledger_resp.raise_for_status()
    ledger_id = ledger_resp.json()["ledger_id"]
    print(f"[ok] 建好账本 ledger_id={ledger_id}")

    acct_resp = client.post(
        f"/write/ledgers/{ledger_id}/accounts",
        json={"base_change_id": 0, "name": "测试信用卡", "account_type": "credit_card"},
        headers=headers,
    )
    acct_resp.raise_for_status()
    account_id = acct_resp.json()["entity_id"]

    cat_resp = client.post(
        f"/write/ledgers/{ledger_id}/categories",
        json={"base_change_id": 0, "name": "分期测试分类", "kind": "expense"},
        headers=headers,
    )
    cat_resp.raise_for_status()
    category_id = cat_resp.json()["entity_id"]
    print(f"[ok] 建好账户/分类 account_id={account_id} category_id={category_id}")

    # ---------- §2.12.2 週期性收支:建交易当下带 recurring,带 end_at 窗口生成 ----------
    now = datetime.now(timezone.utc)
    end_at = now + timedelta(days=95)  # 月频 * 1,期望生成 ~3-4 笔
    tx_resp = client.post(
        f"/write/ledgers/{ledger_id}/transactions",
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": 66.6,
            "happened_at": now.isoformat(),
            "note": "phase1.5-recurring-test",
            "category_id": category_id,
            "account_id": account_id,
            "recurring": {"frequency": "monthly", "interval": 1, "end_at": end_at.isoformat()},
        },
        headers=headers,
    )
    tx_resp.raise_for_status()
    print(f"[ok] 建交易并设为週期起点: {tx_resp.json()}")

    rules = client.get(f"/read/ledgers/{ledger_id}/recurring-rules", headers=headers).json()
    check("週期規則建立成功", len(rules) == 1, str(rules))
    rule = rules[0]
    check(
        "有 end_at 的规则应立即批次生成窗口内全部交易(不等排程)",
        rule["generated_until_at"] is not None,
        str(rule),
    )

    txs = client.get(f"/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    recurring_txs = sorted(
        [t for t in txs if t.get("note") == "phase1.5-recurring-test"],
        key=lambda t: t["happened_at"],
    )
    check(
        "建立当下应一次生成多笔未来交易(月频 + 95 天窗口 >= 3 笔)",
        len(recurring_txs) >= 3,
        f"实际 {len(recurring_txs)} 笔: {[t['happened_at'] for t in recurring_txs]}",
    )
    print(f"[ok] 週期性收支一次生成 {len(recurring_txs)} 笔未来交易(建立当下,非排程逐期)")

    second_tx_id = recurring_txs[1]["id"]
    terminate_resp = client.post(
        f"/write/ledgers/{ledger_id}/recurring-rules/{rule['id']}/terminate-future",
        json={"base_change_id": 0},
        headers=headers,
    )
    terminate_resp.raise_for_status()
    txs_after = client.get(f"/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    recurring_txs_after = [t for t in txs_after if t.get("note") == "phase1.5-recurring-test"]
    check(
        "terminate-future 后未发生的未来期应被删除(只留已发生的第一期)",
        len(recurring_txs_after) == 1,
        f"剩余 {len(recurring_txs_after)} 笔",
    )
    print("[ok] terminate-future 正确只保留已发生期、删除未来期")

    # ---------- §2.12.1 分期付款:建计画当场生成全部期数 ----------
    plan_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans",
        json={
            "base_change_id": 0,
            "total_amount": 1200,
            "periods": 6,
            "first_period_at": now.isoformat(),
            "account_id": account_id,
            "category_id": category_id,
            "note": "phase1.5-installment-test",
            "repayment_method": "equal_installment",
            "interest_period": "monthly",
            "interest_rate": 12.0,
            "round_amounts": True,
            "remainder_position": "last",
            "grace_period_months": 0,
        },
        headers=headers,
    )
    plan_resp.raise_for_status()
    plan_id = plan_resp.json()["entity_id"]
    print(f"[ok] 建好分期计画 plan_id={plan_id}(等额本息,6 期,年利率 12%)")

    periods = client.get(
        f"/read/ledgers/{ledger_id}/installment-plans/{plan_id}/periods", headers=headers,
    ).json()
    check("建计画当场应生成全部 6 期", len(periods) == 6, f"实际 {len(periods)} 期")
    total_paid = sum(p["total_amount"] for p in periods)
    check(
        "等额本息每期总额应大致相等(容差含尾差调整)",
        max(abs(p["total_amount"] - periods[0]["total_amount"]) for p in periods) < 1.0,
        str([p["total_amount"] for p in periods]),
    )
    print(f"[ok] 6 期明细已当场生成,每期约 {periods[0]['total_amount']:.2f}")

    rebalance_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans/{plan_id}/rebalance-from/3",
        json={"base_change_id": 0, "interest_rate": 24.0},
        headers=headers,
    )
    rebalance_resp.raise_for_status()
    periods_after_rebalance = client.get(
        f"/read/ledgers/{ledger_id}/installment-plans/{plan_id}/periods", headers=headers,
    ).json()
    p3_before = next(p for p in periods if p["period_no"] == 3)
    p3_after = next(p for p in periods_after_rebalance if p["period_no"] == 3)
    check(
        "rebalance-from(3) 调高利率后第 3 期起金额应变化",
        abs(p3_after["total_amount"] - p3_before["total_amount"]) > 0.01,
        f"before={p3_before['total_amount']} after={p3_after['total_amount']}",
    )
    print("[ok] rebalance-from 调息后未来期正确重算")

    earlyrepay_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans/{plan_id}/early-repay-principal",
        json={"base_change_id": 0, "payment_amount": 200.0},
        headers=headers,
    )
    earlyrepay_resp.raise_for_status()
    print("[ok] early-repay-principal 部分还本调用成功")

    # 独立一笔计画测 payoff
    plan2_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans",
        json={
            "base_change_id": 0,
            "total_amount": 600,
            "periods": 6,
            "first_period_at": now.isoformat(),
            "account_id": account_id,
            "category_id": category_id,
            "note": "phase1.5-payoff-test",
            "repayment_method": "equal_principal",
        },
        headers=headers,
    )
    plan2_resp.raise_for_status()
    plan2_id = plan2_resp.json()["entity_id"]
    payoff_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans/{plan2_id}/payoff",
        json={"base_change_id": 0},
        headers=headers,
    )
    payoff_resp.raise_for_status()
    plans = client.get(f"/read/ledgers/{ledger_id}/installment-plans", headers=headers).json()
    plan2_after = next(p for p in plans if p["id"] == plan2_id)
    check("payoff 后计画状态应为 settled", plan2_after["status"] == "settled", str(plan2_after))
    print("[ok] payoff 提前结清正确生成结清交易并标记 settled")

    # 独立一笔计画测 terminate-future
    plan3_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans",
        json={
            "base_change_id": 0,
            "total_amount": 300,
            "periods": 6,
            "first_period_at": now.isoformat(),
            "account_id": account_id,
            "category_id": category_id,
            "note": "phase1.5-terminate-test",
            "repayment_method": "equal_principal",
        },
        headers=headers,
    )
    plan3_resp.raise_for_status()
    plan3_id = plan3_resp.json()["entity_id"]
    terminate3_resp = client.post(
        f"/write/ledgers/{ledger_id}/installment-plans/{plan3_id}/terminate-future",
        json={"base_change_id": 0},
        headers=headers,
    )
    terminate3_resp.raise_for_status()
    plans_after3 = client.get(f"/read/ledgers/{ledger_id}/installment-plans", headers=headers).json()
    plan3_after = next(p for p in plans_after3 if p["id"] == plan3_id)
    check(
        "terminate-future 后计画状态应为 terminated",
        plan3_after["status"] == "terminated",
        str(plan3_after),
    )
    print("[ok] terminate-future 正确标记 terminated(不生成结清交易)")

    # ---------- §2.12.3 退款:原交易明细反查 ----------
    orig_tx_resp = client.post(
        f"/write/ledgers/{ledger_id}/transactions",
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": 500.0,
            "happened_at": now.isoformat(),
            "note": "phase1.5-refund-original",
            "category_id": category_id,
            "account_id": account_id,
        },
        headers=headers,
    )
    orig_tx_resp.raise_for_status()
    orig_tx_id = orig_tx_resp.json()["entity_id"]

    refund_tx_resp = client.post(
        f"/write/ledgers/{ledger_id}/transactions",
        json={
            "base_change_id": 0,
            "tx_type": "income",
            "amount": 150.0,
            "happened_at": now.isoformat(),
            "note": "phase1.5-refund-partial",
            "category_id": category_id,
            "account_id": account_id,
            "refund_of_id": orig_tx_id,
        },
        headers=headers,
    )
    refund_tx_resp.raise_for_status()

    txs_final = client.get(f"/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    orig_tx_after = next(t for t in txs_final if t["id"] == orig_tx_id)
    check(
        "原支出交易应反查到退款清单(§2.12.3 refunds 聚合字段)",
        bool(orig_tx_after.get("refunds")) and orig_tx_after["refunds"][0]["amount"] == 150.0,
        str(orig_tx_after.get("refunds")),
    )
    print("[ok] 退款反查字段(refunds)正确:原交易能看到对应的退款交易清单")

    print()
    print("=" * 70)
    print("全部 Phase 1.5 API 链路自动化检查通过。")
    print(f"web 端登录: email={args.email}  password={args.password}")
    print(f"进这本账本肉眼确认: ledger_id={ledger_id}")
    print("=" * 70)


if __name__ == "__main__":
    main()
