"""欠款分類 kind(receivable / payable)。

App 記帳頁「應收」「應付」分頁上方是欠款分類網格(借出/代付/報帳、借入/信貸/
車貸/房貸),分類 kind = receivable / payable,欠款起點交易 tx_type 仍是
expense / income。這裡確認:
  - Web 寫入 API 可以建立/改成欠款 kind 的分類(以前 Literal 只收三種,會 422)
  - 交易可以掛 category_kind=receivable 的分類(tx_type 仍是 expense)
  - mobile push 上來的欠款分類照存、讀得到
  - AI 記帳的候選分類不含欠款分類
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.database import get_db
from src.main import app
from src.models import User
from src.routers.ai.parse_tx_image import _load_ledger_context
from tests.test_category_delete_validation import (
    _create_category,
    _latest_change_id,
    _login_web,
    _make_client,
    _register,
    _seed_ledger,
)


def _push_category(client, token, device, *, sync_id, name, kind) -> None:
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device,
            "changes": [
                {
                    "ledger_id": "0",
                    "entity_type": "category",
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload": {
                        "syncId": sync_id,
                        "name": name,
                        "kind": kind,
                        "level": 1,
                        "sortOrder": 0,
                        "icon": "paid",
                        "iconType": "material",
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def test_web_can_create_debt_kind_categories_and_tx_can_use_them() -> None:
    client = _make_client()
    try:
        owner = _register(client, "debt-cat@example.com")
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_DC")
        token = _login_web(client, "debt-cat@example.com")["access_token"]

        lend_id = _create_category(
            client, token, "L_DC", name="借出", kind="receivable"
        )
        _create_category(client, token, "L_DC", name="房貸", kind="payable")
        # 同名不同 kind 不衝突(支出也可以有一個「借出」)。
        _create_category(client, token, "L_DC", name="借出", kind="expense")

        base = _latest_change_id(client, token, "L_DC")
        res = client.post(
            "/api/v1/write/ledgers/L_DC/transactions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 500,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "category_id": lend_id,
                "category_name": "借出",
                "category_kind": "receivable",
                "exclude_from_stats": True,
                "exclude_from_budget": True,
            },
        )
        assert res.status_code == 200, res.text

        txs = client.get(
            "/api/v1/read/ledgers/L_DC/transactions",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        tx = next(t for t in txs if t["amount"] == 500)
        assert tx["tx_type"] == "expense"
        assert tx["category_kind"] == "receivable"
        assert tx["category_name"] == "借出"
        assert tx["category_id"] == lend_id

        # 改 kind 也接受欠款 kind。
        base = _latest_change_id(client, token, "L_DC")
        cats = client.get(
            "/api/v1/read/ledgers/L_DC/categories",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        house = next(c for c in cats if c["name"] == "房貸")
        res = client.patch(
            f"/api/v1/write/ledgers/L_DC/categories/{house['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "kind": "receivable"},
        )
        assert res.status_code == 200, res.text

        # 不認得的 kind 仍然擋。
        base = _latest_change_id(client, token, "L_DC")
        res = client.post(
            "/api/v1/write/ledgers/L_DC/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "name": "X", "kind": "loan"},
        )
        assert res.status_code == 422, res.text
    finally:
        app.dependency_overrides.clear()


def test_mobile_pushed_debt_categories_are_kept_and_hidden_from_ai() -> None:
    client = _make_client()
    try:
        owner = _register(client, "debt-cat-push@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_DP")
        _push_category(client, token, device, sync_id="c-lend", name="借出", kind="receivable")
        _push_category(client, token, device, sync_id="c-car", name="車貸", kind="payable")
        _push_category(client, token, device, sync_id="c-food", name="餐飲", kind="expense")

        web_token = _login_web(client, "debt-cat-push@example.com")["access_token"]
        cats = client.get(
            "/api/v1/read/ledgers/L_DP/categories",
            headers={"Authorization": f"Bearer {web_token}"},
        ).json()
        kinds = {c["name"]: c["kind"] for c in cats}
        assert kinds["借出"] == "receivable"
        assert kinds["車貸"] == "payable"

        db = next(app.dependency_overrides[get_db]())
        try:
            user = db.query(User).filter(User.email == "debt-cat-push@example.com").one()
            names, _accounts = _load_ledger_context(db, "L_DP", user.id)
        finally:
            db.close()
        assert "餐飲" in names
        assert "借出" not in names
        assert "車貸" not in names
    finally:
        app.dependency_overrides.clear()
