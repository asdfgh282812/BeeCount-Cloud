"""对着一个真正跑起来的 server(python server.py)手动跑一遍週期性收支
(recurring_rule)的完整链路:注册/登录 -> 建账本 -> 建一条到期的 recurring
rule -> 立即触发物化 -> 读回确认生成了交易。

跑之前:
1. .env 里加一行 REGISTRATION_ENABLED=true(测完可以删掉/改回 false)
2. alembic upgrade head
3. 另开一个终端跑 `python server.py`,让它一直挂着
4. 跑本脚本: python scripts/manual_test_recurring.py

跑完之后可以直接打开 web 端(pnpm dev-web),用脚本打印出来的
email/password 登录,进对应 ledger_id 肉眼确认——因为这条链路走的是真正
的 server 进程 + 真正的 beecount.db,跟 web 端看到的是同一份数据。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent


def grant_admin(email: str) -> None:
    subprocess.run(
        [sys.executable, "scripts/grant_admin.py", "--email", email],
        cwd=REPO_ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080/api/v1")
    parser.add_argument("--email", default="recurring-manual-test@example.com")
    parser.add_argument("--password", default="test-password-123")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=10.0)
    auth_body = {
        "email": args.email,
        "password": args.password,
        "client_type": "web",
        "device_id": "manual-test-script",
    }

    resp = client.post("/auth/register", json=auth_body)
    if resp.status_code != 200:
        print(f"[info] register failed ({resp.status_code}: {resp.text[:200]}), trying login...")
        resp = client.post("/auth/login", json=auth_body)
    resp.raise_for_status()
    data = resp.json()
    if data.get("requires_2fa"):
        raise SystemExit("该测试账号开了 2FA,换个 --email 重跑")
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[ok] 登录成功: {args.email}")

    grant_admin(args.email)
    print("[ok] 已提升为 admin(DB 直改,当前 token 免重新登录即可用)")

    ledger_resp = client.post(
        "/write/ledgers",
        json={"ledger_name": "recurring-manual-test"},
        headers=headers,
    )
    ledger_resp.raise_for_status()
    ledger_id = ledger_resp.json()["ledger_id"]
    print(f"[ok] 建好账本 ledger_id={ledger_id}")

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    rule_resp = client.post(
        f"/write/ledgers/{ledger_id}/recurring-rules",
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": 88.8,
            "note": "manual-test-recurring",
            "frequency": "monthly",
            "interval": 1,
            "next_run_at": past.isoformat(),
        },
        headers=headers,
    )
    rule_resp.raise_for_status()
    rule_id = rule_resp.json()["entity_id"]
    print(f"[ok] 建好 recurring rule {rule_id},next_run_at={past.isoformat()}(已过期,应立即到期)")

    materialize_resp = client.post("/internal/tasks/materialize-recurring", headers=headers)
    materialize_resp.raise_for_status()
    print(f"[ok] 触发物化,返回: {materialize_resp.json()}")

    rules_after = client.get(f"/read/ledgers/{ledger_id}/recurring-rules", headers=headers).json()
    print("[check] 物化后的 recurring rules:")
    for r in rules_after:
        print(f"  id={r['id']} next_run_at={r['next_run_at']} enabled={r['enabled']}")

    txs = client.get(f"/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    print(f"[check] 账本里的交易(共 {len(txs)} 条):")
    for t in txs:
        print(f"  amount={t['amount']} note={t['note']} happened_at={t['happened_at']}")

    print()
    print("=" * 60)
    print(f"web 端登录: email={args.email}  password={args.password}")
    print(f"进这本账本肉眼确认: ledger_id={ledger_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
