"""帳戶級聯刪除(2026-08 新增)契约测试。

背景:DELETE .../accounts/{id} 原本只要帳戶還有任何關聯交易就整體拒絕,
使用者必須先手動搬走/刪除交易才能刪帳戶。這次改成 `cascade=true` 時連同
關聯交易一併刪除;但「結構性設定」(週期性收支規則/分期付款/交易範本/
信用卡回饋規則/自動扣繳來源帳戶)不論 cascade 與否一律照舊擋下,且如果
關聯交易裡有分期付款生成的那種(installmentPlanId 非空),整個級聯刪除
中止、不留部分刪除的中間狀態。

覆盖:
1. cascade=true 成功刪除帳戶 + 其關聯交易(單次 commit)
2. cascade=false(現況預設)仍然擋下,行為不變
3. cascade=true 但命中分期關聯交易 → 中止,帳戶與交易都原封不動
4. 結構性引用(週期性收支/分期付款/交易範本/信用卡回饋規則/自動扣繳來源)
   不論 cascade 與否一律擋下
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection, User, UserAccountProjection


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": device_id,
            "client_type": client_type, "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _account_exists(TS, email, sync_id) -> bool:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        return row is not None


def _tx_exists(TS, sync_id) -> bool:
    with TS() as db:
        row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
        return row is not None


def _setup(email, ledger_id="lgc1"):
    client, TS = _make_client()
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-target",
          {"syncId": "acc-target", "name": "測試帳戶", "type": "cash", "currency": "CNY"},
          device_id="d-app")
    return client, TS, hdr_app, hdr_web, ledger_id


def _delete(client, hdr_web, ledger_id, account_id, *, cascade=None, base_change_id=0):
    body = {"base_change_id": base_change_id}
    if cascade is not None:
        body["cascade"] = cascade
    return client.request(
        "DELETE", f"/api/v1/write/ledgers/{ledger_id}/accounts/{account_id}",
        headers=hdr_web, json=body,
    )


def test_web_delete_account_cascade_removes_linked_transactions():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade1@t.com")
    try:
        _push(client, hdr_app, lg, "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 10.0, "happenedAt": _iso(),
               "accountId": "acc-target", "accountName": "測試帳戶"}, device_id="d-app")
        _push(client, hdr_app, lg, "transaction", "tx-2",
              {"syncId": "tx-2", "type": "income", "amount": 20.0, "happenedAt": _iso(),
               "accountId": "acc-target", "accountName": "測試帳戶"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 200, r.text

        assert not _account_exists(TS, "cascade1@t.com", "acc-target")
        assert not _tx_exists(TS, "tx-1")
        assert not _tx_exists(TS, "tx-2")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_without_cascade_still_blocks_on_transactions():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade2@t.com")
    try:
        _push(client, hdr_app, lg, "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 10.0, "happenedAt": _iso(),
               "accountId": "acc-target", "accountName": "測試帳戶"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target")  # cascade 缺省 = False
        assert r.status_code == 400, r.text

        assert _account_exists(TS, "cascade2@t.com", "acc-target")
        assert _tx_exists(TS, "tx-1")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_cascade_aborts_on_installment_linked_transaction():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade3@t.com")
    try:
        _push(client, hdr_app, lg, "installment_plan", "ins-1",
              {"syncId": "ins-1", "totalAmount": 600.0, "periods": 6, "periodAmount": 100.0,
               "firstPeriodAt": _iso(), "nextPeriodAt": _iso(), "paidPeriods": 0,
               "status": "active", "repaymentMethod": "equal_installment",
               "interestPeriod": "monthly", "interestRate": 0.0}, device_id="d-app")
        _push(client, hdr_app, lg, "transaction", "tx-plain",
              {"syncId": "tx-plain", "type": "expense", "amount": 10.0, "happenedAt": _iso(),
               "accountId": "acc-target", "accountName": "測試帳戶"}, device_id="d-app")
        _push(client, hdr_app, lg, "transaction", "tx-installment",
              {"syncId": "tx-installment", "type": "expense", "amount": 100.0, "happenedAt": _iso(),
               "accountId": "acc-target", "accountName": "測試帳戶",
               "installmentPlanId": "ins-1"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 400, r.text

        # 中止时不留部分刪除的中間狀態:帳戶跟兩筆交易都还在。
        assert _account_exists(TS, "cascade3@t.com", "acc-target")
        assert _tx_exists(TS, "tx-plain")
        assert _tx_exists(TS, "tx-installment")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_blocked_by_recurring_rule_reference_even_with_cascade():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade4@t.com")
    try:
        _push(client, hdr_app, lg, "recurring_rule", "rec-1",
              {"syncId": "rec-1", "txType": "expense", "amount": 50.0, "frequency": "monthly",
               "interval": 1, "nextRunAt": _iso(), "enabled": True,
               "accountId": "acc-target"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 400, r.text
        assert _account_exists(TS, "cascade4@t.com", "acc-target")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_blocked_by_installment_plan_reference_even_with_cascade():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade5@t.com")
    try:
        _push(client, hdr_app, lg, "installment_plan", "ins-1",
              {"syncId": "ins-1", "totalAmount": 600.0, "periods": 6, "periodAmount": 100.0,
               "firstPeriodAt": _iso(), "nextPeriodAt": _iso(), "paidPeriods": 0,
               "status": "active", "repaymentMethod": "equal_installment",
               "interestPeriod": "monthly", "interestRate": 0.0,
               "accountId": "acc-target"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 400, r.text
        assert _account_exists(TS, "cascade5@t.com", "acc-target")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_blocked_by_tx_template_reference_even_with_cascade():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade6@t.com")
    try:
        _push(client, hdr_app, lg, "tx_template", "tpl-1",
              {"syncId": "tpl-1", "name": "常用範本", "txType": "expense", "amount": 30.0,
               "sortOrder": 0, "accountId": "acc-target"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 400, r.text
        assert _account_exists(TS, "cascade6@t.com", "acc-target")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_blocked_by_card_reward_rule_reference_even_with_cascade():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade7@t.com")
    try:
        # 回饋規則綁定的帳戶必須是信用卡,另外重建一個 credit_card 帳戶。
        _push(client, hdr_app, lg, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, lg, "card_reward_rule", "crr-1",
              {"syncId": "crr-1", "accountId": "acc-card", "label": "网购",
               "rateType": "percentage", "rateValue": 2.0, "rounding": "round",
               "calcBasis": "transaction_date", "interval": "billing_cycle"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-card", cascade=True)
        assert r.status_code == 400, r.text
        assert _account_exists(TS, "cascade7@t.com", "acc-card")
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_blocked_by_auto_pay_source_reference_even_with_cascade():
    client, TS, hdr_app, hdr_web, lg = _setup("cascade8@t.com")
    try:
        _push(client, hdr_app, lg, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "autoPayEnabled": True, "autoPayFromAccountId": "acc-target"}, device_id="d-app")

        r = _delete(client, hdr_web, lg, "acc-target", cascade=True)
        assert r.status_code == 400, r.text
        assert _account_exists(TS, "cascade8@t.com", "acc-target")
    finally:
        app.dependency_overrides.clear()
