"""信用卡管理整組功能(§2.9,MOZE_FEATURE_GAP_SD.md Phase 4)契约测试。

2026-08-02 改版为「群組」模型:主帳戶是 `account_type == "account_group"`
的純管理容器(不能自己記交易),實體信用卡都是掛在它底下的子帳戶。

覆盖:
1. `src/services/credit_card.py` 帳單週期純函式(月底夾斷/繳款日跨月/免息期)
2. 群組模型:parent_account_id 必須指向 account_group、group 不能自我掛靠/
   循環/巢狀、group 不能被一般交易/週期性收支/分期付款拿來當帳戶用、
   group 有子帳戶掛靠時不能刪除
3. `GET .../accounts/{id}/billing-summary`:合併子卡當期帳單金額 + 終身
   餘額結轉(溢繳跨週期依然算數)+ 可用額度
4. `GET .../accounts/{id}/interest-free-suggestion`:純計算端點
5. `POST .../accounts/{id}/card-payment`:群組分攤繳款(足額付清 vs 比例
   分攤 vs 溢繳結轉到群組)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection, User, UserAccountProjection
from src.services import credit_card


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


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def _tx_row(TS, sync_id) -> ReadTxProjection:
    with TS() as db:
        row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# 1. src/services/credit_card.py 純函式                                       #
# --------------------------------------------------------------------------- #


def test_billing_cycle_containing_before_and_after_billing_day():
    # billing_day = 15,在結帳日之前消費 → 落在本月這期(還沒結束)
    start, end = credit_card.billing_cycle_containing(date(2026, 3, 10), 15)
    assert end == date(2026, 3, 15)
    assert start == date(2026, 2, 15)
    # 結帳日之後消費 → 落在下個月才結的那期
    start2, end2 = credit_card.billing_cycle_containing(date(2026, 3, 20), 15)
    assert end2 == date(2026, 4, 15)
    assert start2 == date(2026, 3, 15)


def test_billing_cycle_containing_on_billing_day_itself():
    start, end = credit_card.billing_cycle_containing(date(2026, 3, 15), 15)
    assert end == date(2026, 3, 15)
    assert start == date(2026, 2, 15)


def test_billing_cycle_month_end_clamping():
    # billing_day=31,二月沒有 31 號 → 夾斷到月底(28 或 29 號)
    start, end = credit_card.billing_cycle_containing(date(2026, 2, 20), 31)
    assert end == date(2026, 2, 28)  # 2026 非閏年
    assert start == date(2026, 1, 31)


def test_most_recently_closed_cycle():
    # as_of 在结帳日之後 → 已经结束的是本月这期
    start, end = credit_card.most_recently_closed_cycle(date(2026, 3, 20), 15)
    assert end == date(2026, 3, 15)
    assert start == date(2026, 2, 15)
    # as_of 在结帳日之前 → 已经结束的是上个月那期
    start2, end2 = credit_card.most_recently_closed_cycle(date(2026, 3, 10), 15)
    assert end2 == date(2026, 2, 15)
    assert start2 == date(2026, 1, 15)
    # as_of 剛好是结帳日 → 视为当天已结束
    start3, end3 = credit_card.most_recently_closed_cycle(date(2026, 3, 15), 15)
    assert end3 == date(2026, 3, 15)


def test_due_date_same_month_vs_next_month():
    # payment_due_day(25) > billing_day(15) 的场景 → 繳款日跟結帳日同月
    due = credit_card.due_date_for_cycle_end(date(2026, 3, 15), 25)
    assert due == date(2026, 3, 25)
    # payment_due_day(5) < 結帳日(15) → 繳款日落在下個月
    due2 = credit_card.due_date_for_cycle_end(date(2026, 3, 15), 5)
    assert due2 == date(2026, 4, 5)


def test_due_date_end_of_month_clamp():
    # payment_due_day=31,結帳日在四月(30 天)→ 下個月沒有 31 號要夾斷
    due = credit_card.due_date_for_cycle_end(date(2026, 4, 1), 31)
    assert due == date(2026, 4, 30)


def test_interest_free_suggestion_structure_and_ordering():
    s = credit_card.interest_free_suggestion(date(2026, 3, 10), 15, 25)
    assert s["current_cycle_end"] == date(2026, 3, 15)
    assert s["current_cycle_due_date"] == date(2026, 3, 25)
    assert s["next_cycle_start"] == date(2026, 3, 16)
    assert s["next_cycle_end"] == date(2026, 4, 15)
    assert s["next_cycle_due_date"] == date(2026, 4, 25)
    # 免息天数:越晚消费(等到下一期)拿到的免息天数应该 >= 现在马上消费
    assert s["max_interest_free_days"] >= s["min_interest_free_days"]


# --------------------------------------------------------------------------- #
# 2. 群組模型:parent_account_id 校验 + account_group 不可交易              #
# --------------------------------------------------------------------------- #


def test_mobile_push_account_partial_update_keeps_parent_account_id():
    """CLAUDE.md 要求的新增字段 merge 契约测试:partial update 不带
    parentAccountId 键时不能把已有掛靠冲掉。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "ccm1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-child",
              {"syncId": "acc-child", "name": "子卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"})
        # partial update:只改 name,不带 parentAccountId 键
        _push(client, hdr, "lg1", "account", "acc-child", {"syncId": "acc-child", "name": "子卡改名"})

        row = _account_row(TS, "ccm1@t.com", "acc-child")
        assert row.name == "子卡改名"
        assert row.parent_account_id == "acc-group"
    finally:
        app.dependency_overrides.clear()


def test_read_ledger_accounts_expose_parent_account_id():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccm2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccm2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-child",
              {"syncId": "acc-child", "name": "子卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        by_id = {a["id"]: a for a in r.json()}
        assert by_id["acc-child"]["parent_account_id"] == "acc-group"
        assert by_id["acc-group"]["parent_account_id"] is None
    finally:
        app.dependency_overrides.clear()


def _seed_group_and_child(client, hdr_app, ledger_id, *, billing_day=None, payment_due_day=None):
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    group_payload = {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY"}
    if billing_day is not None:
        group_payload["billingDay"] = billing_day
    if payment_due_day is not None:
        group_payload["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-group", group_payload, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-child",
          {"syncId": "acc-child", "name": "子卡", "type": "credit_card", "currency": "CNY"},
          device_id="d-app")


def test_web_update_account_parent_account_id_accepts_valid_group_parent():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw1")

        r = client.patch(
            "/api/v1/write/ledgers/lgw1/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, "ccw1@t.com", "acc-child")
        assert row.parent_account_id == "acc-group"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_parent_account_id_accepts_bank_card_child():
    """§2.9 Phase 4 前端擴充(2026-08):銀行帳戶(bank_card)也可以掛靠
    account_group 主帳戶,不再只限信用卡——後端本來就是 type-agnostic,
    這條測試鎖住這個行為避免退化。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccbank1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccbank1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgbank1", "ledger", "lgbank1",
              {"syncId": "lgbank1", "ledgerName": "账本", "currency": "TWD"}, device_id="d-app")
        _push(client, hdr_app, "lgbank1", "account", "acc-group",
              {"syncId": "acc-group", "name": "銀行群組", "type": "account_group", "currency": "TWD"},
              device_id="d-app")
        _push(client, hdr_app, "lgbank1", "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行帳戶", "type": "bank_card", "currency": "TWD"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgbank1/accounts/acc-bank",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, "ccbank1@t.com", "acc-bank")
        assert row.parent_account_id == "acc-group"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_parent_account_id_rejects_non_group_parent():
    """新規則:parent 必須是 account_type == "account_group",隨便一張
    信用卡不能再被拿來當主帳戶(這是這次改版的核心 —— 使用者反馈主帳戶
    不該是可獨立記交易的普通帳戶)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw1b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw1b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgw1b", "ledger", "lgw1b",
              {"syncId": "lgw1b", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw1b", "account", "acc-card-a",
              {"syncId": "acc-card-a", "name": "卡A", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgw1b", "account", "acc-card-b",
              {"syncId": "acc-card-b", "name": "卡B", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw1b/accounts/acc-card-b",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-card-a"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_parent_account_id_rejects_self_reference():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw2")

        r = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-group",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_parent_account_id_rejects_unknown_parent():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw3")

        r = client.patch(
            "/api/v1/write/ledgers/lgw3/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "does-not-exist"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_rejects_nested_group():
    """群組不能巢狀:一個 account_group 自己不能再掛靠另一個 account_group。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgw4", "ledger", "lgw4",
              {"syncId": "lgw4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw4", "account", "acc-group-a",
              {"syncId": "acc-group-a", "name": "主帳戶A", "type": "account_group", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgw4", "account", "acc-group-b",
              {"syncId": "acc-group-b", "name": "主帳戶B", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw4/accounts/acc-group-b",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group-a"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_web_delete_account_group_with_children_rejected():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw5")
        r0 = client.patch(
            "/api/v1/write/ledgers/lgw5/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        assert r0.status_code == 200, r0.text

        r = client.request(
            "DELETE", "/api/v1/write/ledgers/lgw5/accounts/acc-group",
            headers=hdr_web, json={"base_change_id": r0.json()["new_change_id"]},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_regular_transaction_rejects_account_group_target():
    """account_group 是純管理容器,不能被一般交易拿来当 account_id/
    from_account_id/to_account_id。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw6@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw6@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw6")

        r = client.post(
            "/api/v1/write/ledgers/lgw6/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "expense", "amount": 10.0,
                "happened_at": _iso(), "account_id": "acc-group",
            },
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_update_transaction_rejects_account_group_target():
    """2026-08-02 web UI 手测发现:前端「全局编辑」对话框曾经只送
    account_name、不送 account_id,导致 update_tx 完全绕过
    _assert_account_not_group(payload.get("account_id") 恒为 None)——create
    会被挡但 update 会静默"成功"(实际上 accountId 外键没变,只是显示名字
    被改成主帳戶,造成名字/外键脱钩)。前端已修(GlobalEditDialogs.tsx 补
    resolvedAccountId),这里补一条后端契约测试锁住 update 路径本身的校验,
    不依赖前端有没有送对 account_id。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccw6b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccw6b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgw6b")

        r = client.post(
            "/api/v1/write/ledgers/lgw6b/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "expense", "amount": 10.0,
                "happened_at": _iso(), "account_id": "acc-child",
            },
        )
        assert r.status_code == 200, r.text
        tx_id = r.json()["entity_id"]
        base_change_id = r.json()["new_change_id"]

        r2 = client.patch(
            f"/api/v1/write/ledgers/lgw6b/transactions/{tx_id}",
            headers=hdr_web,
            json={"base_change_id": base_change_id, "account_id": "acc-group"},
        )
        assert r2.status_code == 400, r2.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 3. GET .../accounts/{id}/billing-summary                                     #
# --------------------------------------------------------------------------- #


def test_billing_summary_requires_billing_root_or_schedule():
    """acc-nocfg 是沒有掛靠任何群組的獨立信用卡(2026-08-02 第二輪放寬之後
    is_billing_root 本身會過),但沒設 billing_day/payment_due_day,所以還是
    400——只是原因从「不是 account_group」变成「没配置帳單週期」。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb0@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb0@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgb0", "ledger", "lgb0",
              {"syncId": "lgb0", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgb0", "account", "acc-nocfg",
              {"syncId": "acc-nocfg", "name": "卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r = client.get(
            "/api/v1/read/ledgers/lgb0/accounts/acc-nocfg/billing-summary", headers=hdr_web,
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_allows_standalone_credit_card():
    """單卡(沒有掛靠任何群組)配置了 billing_day/payment_due_day 後,應該
    能像群組一樣直接查合併帳單——自己既是查詢根也是唯一成員。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb0c@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb0c@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgb0c", "ledger", "lgb0c",
              {"syncId": "lgb0c", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgb0c", "account", "acc-solo",
              {"syncId": "acc-solo", "name": "獨立卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 10, "paymentDueDay": 25, "creditLimit": 10000.0}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgb0c/transactions",
            headers=hdr_web,
            json={"base_change_id": 0, "tx_type": "expense", "amount": 300.0,
                  "happened_at": _iso(), "account_id": "acc-solo"},
        )
        assert r.status_code == 200, r.text

        r2 = client.get(
            "/api/v1/read/ledgers/lgb0c/accounts/acc-solo/billing-summary", headers=hdr_web,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["member_account_ids"] == ["acc-solo"]
        assert body["open_cycle_spend"] == 300.0
        assert body["credit_limit"] == 10000.0
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_rejects_credit_card_with_parent():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb0d@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb0d@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _seed_group_and_child(client, hdr_app, "lgb0d", billing_day=10, payment_due_day=25)
        client.patch(
            "/api/v1/write/ledgers/lgb0d/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )

        r = client.get(
            "/api/v1/read/ledgers/lgb0d/accounts/acc-child/billing-summary", headers=hdr_web,
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_requires_billing_schedule():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb0b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb0b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgb0b", "ledger", "lgb0b",
              {"syncId": "lgb0b", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgb0b", "account", "acc-group-nocfg",
              {"syncId": "acc-group-nocfg", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r = client.get(
            "/api/v1/read/ledgers/lgb0b/accounts/acc-group-nocfg/billing-summary", headers=hdr_web,
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def _dt(d, hour=12):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).isoformat()


def test_billing_summary_merges_children_and_computes_due_amount():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgb1", "ledger", "lgb1",
              {"syncId": "lgb1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        yesterday = now.date() - timedelta(days=1)
        billing_day = yesterday.day
        payment_due_day = 20
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        assert cycle_end == yesterday

        _push(client, hdr_app, "lgb1", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": payment_due_day, "creditLimit": 1000.0},
              device_id="d-app")
        _push(client, hdr_app, "lgb1", "account", "acc-main",
              {"syncId": "acc-main", "name": "主卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")
        _push(client, hdr_app, "lgb1", "account", "acc-sub",
              {"syncId": "acc-sub", "name": "子卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")
        _push(client, hdr_app, "lgb1", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        _push(client, hdr_app, "lgb1", "transaction", "tx-main-in",
              {"syncId": "tx-main-in", "type": "expense", "amount": 100.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-main", "accountName": "主卡"}, device_id="d-app")
        # 恰好落在上一期结帳日当天 → 应该被排除在这一期之外
        _push(client, hdr_app, "lgb1", "transaction", "tx-main-prev",
              {"syncId": "tx-main-prev", "type": "expense", "amount": 999.0,
               "happenedAt": _dt(cycle_start),
               "accountId": "acc-main", "accountName": "主卡"}, device_id="d-app")
        # 退款(income)冲抵本期支出
        _push(client, hdr_app, "lgb1", "transaction", "tx-main-refund",
              {"syncId": "tx-main-refund", "type": "income", "amount": 20.0,
               "happenedAt": _dt(cycle_end, hour=8),
               "accountId": "acc-main", "accountName": "主卡"}, device_id="d-app")
        # 子卡消费(结帳日当天)→ 合併算进主帳戶帳單
        _push(client, hdr_app, "lgb1", "transaction", "tx-sub-in",
              {"syncId": "tx-sub-in", "type": "expense", "amount": 50.0,
               "happenedAt": _dt(cycle_end, hour=23),
               "accountId": "acc-sub", "accountName": "子卡"}, device_id="d-app")
        # 帳單結束後的繳款,冲抵應繳金額(打到主卡本身)
        _push(client, hdr_app, "lgb1", "transaction", "tx-payment",
              {"syncId": "tx-payment", "type": "transfer", "amount": 30.0,
               "happenedAt": now.isoformat(),
               "fromAccountId": "acc-cash", "fromAccountName": "現金",
               "toAccountId": "acc-main", "toAccountName": "主卡"}, device_id="d-app")

        r = client.get(
            "/api/v1/read/ledgers/lgb1/accounts/acc-group/billing-summary", headers=hdr_web,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data["member_account_ids"]) == {"acc-main", "acc-sub"}
        assert data["statement_amount"] == 130.0  # (100 - 20) + 50,仅本期窗口
        # remaining_due 是終身跑動餘額,tx-main-prev(999,上一期未繳清的舊帳)
        # 依然算在裡面:999(舊帳)+ 100 - 20 + 50(本期)- 30(繳款)= 1099。
        assert data["paid_amount"] == 30.0
        assert data["remaining_due"] == 1099.0
        assert data["credit_limit"] == 1000.0
        assert data["available_credit"] == -99.0
        members_by_id = {m["account_id"]: m for m in data["members"]}
        assert members_by_id["acc-main"]["cycle_spend"] == 80.0
        assert members_by_id["acc-sub"]["cycle_spend"] == 50.0
        # §2.9.6 Phase 7(2026-08-07 使用者反饋):每個 member 附上自己的本期
        # 新增花費(period_new_spend)+ 自己的終身跑動餘額(remaining_due),
        # 讓子卡詳情頁能顯示「自己的」數字,不用借用整組合併金額。period_
        # cycle_start/end 跟上面 cycle_start/end(最近一次已結束的週期)是
        # 同一期(cycle_offset 預設 0),所以主卡的 period_new_spend 應該等於
        # 上面驗證過的 80(100 - 20 退款),子卡是 50。
        assert members_by_id["acc-main"]["period_new_spend"] == 80.0
        assert members_by_id["acc-sub"]["period_new_spend"] == 50.0
        # 主卡自己終身欠款:tx-main-prev(999)+ tx-main-in(100)- tx-main-refund
        # (20)- tx-payment(30,打在主卡身上)= 1049;子卡自己:50(沒有繳款打
        # 在子卡身上)。兩者加總(1099)應該等於上面驗證過的整組 remaining_due。
        assert members_by_id["acc-main"]["remaining_due"] == 1049.0
        assert members_by_id["acc-sub"]["remaining_due"] == 50.0
        assert data["due_date"][:10] == credit_card.due_date_for_cycle_end(
            cycle_end, payment_due_day
        ).isoformat()
        # 沒有建過任何分期計畫,「帳單分期」欄位應該回報空狀態(前端顯示「---」)。
        assert data["period_installment_active_count"] == 0
        assert data["period_installment_paid_periods"] is None
        assert data["period_installment_periods"] is None
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_period_installment_summary_field():
    """帳戶詳情彈窗「帳單分期」欄位(2026-08-04 使用者反饋補上):建立一個
    進行中的分期計畫後,billing-summary 應該回報 active_count=1 + 已到期
    的期數(paid_periods) + 總期數(periods);建第二個計畫後應該只回報
    筆數,不再附帶 paid_periods/periods 明細(彈窗空間有限)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccbinst1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccbinst1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgbi1", "ledger", "lgbi1",
              {"syncId": "lgbi1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgbi1", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 25, "paymentDueDay": 10}, device_id="d-app")

        # 首期已经到期(昨天),之后 11 期还没到 → paid_periods 应该是 1。
        first_period_at = datetime.now(timezone.utc) - timedelta(days=1)
        res = client.post(
            "/api/v1/write/ledgers/lgbi1/installment-plans", headers=hdr_web,
            json={
                "base_change_id": 0, "total_amount": 1200.0, "periods": 12,
                "first_period_at": first_period_at.isoformat(), "account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text

        data = client.get(
            "/api/v1/read/ledgers/lgbi1/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        assert data["period_installment_active_count"] == 1
        assert data["period_installment_paid_periods"] == 1
        assert data["period_installment_periods"] == 12

        # 再建一個進行中的分期計畫 → 應該退化成只顯示筆數。
        res2 = client.post(
            "/api/v1/write/ledgers/lgbi1/installment-plans", headers=hdr_web,
            json={
                "base_change_id": 0, "total_amount": 300.0, "periods": 3,
                "first_period_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
                "account_id": "acc-card",
            },
        )
        assert res2.status_code == 200, res2.text

        data2 = client.get(
            "/api/v1/read/ledgers/lgbi1/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        assert data2["period_installment_active_count"] == 2
        assert data2["period_installment_paid_periods"] is None
        assert data2["period_installment_periods"] is None
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_overpayment_carries_forward_across_cycles():
    """终身跑动余额的核心行为:这一期溢繳之后,下一期结帳日一过,
    remaining_due 依然要把之前的溢繳算进去,不能凭空消失(原本按"结帳日
    之后"窗口查 paid_amount 的写法在这里会漏算)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccb2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccb2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgb2", "ledger", "lgb2",
              {"syncId": "lgb2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        # billing_day 设成两天前,让"上一期"结帳日落在更早,方便塞两期数据。
        two_cycles_ago_anchor = now.date() - timedelta(days=2)
        billing_day = two_cycles_ago_anchor.day

        _push(client, hdr_app, "lgb2", "account", "acc-group2",
              {"syncId": "acc-group2", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 20}, device_id="d-app")
        _push(client, hdr_app, "lgb2", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group2"}, device_id="d-app")
        _push(client, hdr_app, "lgb2", "account", "acc-cash2",
              {"syncId": "acc-cash2", "name": "現金", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        # 上一期消费 100,溢繳 150(多繳 50)。
        _push(client, hdr_app, "lgb2", "transaction", "tx-spend-1",
              {"syncId": "tx-spend-1", "type": "expense", "amount": 100.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card2", "accountName": "卡"}, device_id="d-app")
        _push(client, hdr_app, "lgb2", "transaction", "tx-overpay",
              {"syncId": "tx-overpay", "type": "transfer", "amount": 150.0,
               "happenedAt": _dt(cycle_end, hour=23),
               "fromAccountId": "acc-cash2", "fromAccountName": "現金",
               "toAccountId": "acc-card2", "toAccountName": "卡"}, device_id="d-app")

        r1 = client.get(
            "/api/v1/read/ledgers/lgb2/accounts/acc-group2/billing-summary", headers=hdr_web,
        )
        assert r1.status_code == 200, r1.text
        data1 = r1.json()
        assert data1["remaining_due"] == -50.0  # 溢繳 50

        # 现在这一期(尚未结帳)又花了 30,再查一次:溢繳的 50 应该还在,
        # 抵掉这一期的欠款(30 - 50 = -20)。
        _push(client, hdr_app, "lgb2", "transaction", "tx-spend-2",
              {"syncId": "tx-spend-2", "type": "expense", "amount": 30.0,
               "happenedAt": _dt(cycle_end + timedelta(days=1)),
               "accountId": "acc-card2", "accountName": "卡"}, device_id="d-app")

        # 把 as_of "now" 往前推进一整个billing周期,让上面那笔 30 元消费所在的
        # 週期结帳,才能验证"下一期"remaining_due 是否正确结转溢繳。
        # 这里直接算下一期的 cycle_end,构造一笔刚好落在下一期内、日期晚于
        # 当前 now 的交易不现实(server 用 datetime.now() 当 as_of),所以改为
        # 断言:如果现在(仍在同一个 open cycle 内)查 billing-summary,
        # remaining_due 应该维持 -50(上一期溢繳),因为 tx-spend-2 落在
        # open cycle(未结帳),不计入 remaining_due,只计入 open_cycle_spend。
        r2 = client.get(
            "/api/v1/read/ledgers/lgb2/accounts/acc-group2/billing-summary", headers=hdr_web,
        )
        assert r2.status_code == 200, r2.text
        data2 = r2.json()
        assert data2["remaining_due"] == -50.0
        assert data2["open_cycle_spend"] == 30.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 4. GET .../accounts/{id}/interest-free-suggestion                           #
# --------------------------------------------------------------------------- #


def test_interest_free_suggestion_endpoint():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccs1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccs1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgs1", "ledger", "lgs1",
              {"syncId": "lgs1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgs1", "account", "acc-s1",
              {"syncId": "acc-s1", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": 10, "paymentDueDay": 25}, device_id="d-app")

        r = client.get(
            "/api/v1/read/ledgers/lgs1/accounts/acc-s1/interest-free-suggestion", headers=hdr_web,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        expected = credit_card.interest_free_suggestion(datetime.now(timezone.utc).date(), 10, 25)
        assert data["billing_day"] == 10
        assert data["payment_due_day"] == 25
        assert data["current_cycle_end"][:10] == expected["current_cycle_end"].isoformat()
        assert data["next_cycle_due_date"][:10] == expected["next_cycle_due_date"].isoformat()
        assert data["max_interest_free_days"] == expected["max_interest_free_days"]
        assert data["min_interest_free_days"] == expected["min_interest_free_days"]
    finally:
        app.dependency_overrides.clear()


def test_interest_free_suggestion_allows_standalone_credit_card():
    """2026-08-02 第二輪放寬:沒有掛靠任何群組的獨立信用卡也能直接查
    免息期推薦,不再要求一定要先建一個 account_group(單卡也該有群組的
    全部功能——見 is_billing_root)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccs2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccs2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgs2", "ledger", "lgs2",
              {"syncId": "lgs2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgs2", "account", "acc-s2",
              {"syncId": "acc-s2", "name": "卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 10, "paymentDueDay": 25}, device_id="d-app")

        r = client.get(
            "/api/v1/read/ledgers/lgs2/accounts/acc-s2/interest-free-suggestion", headers=hdr_web,
        )
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()


def test_interest_free_suggestion_rejects_credit_card_with_parent():
    """已经掛靠某个群组的子卡不能被直接查——要透過它的群组查。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccs3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccs3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _seed_group_and_child(client, hdr_app, "lgs3", billing_day=10, payment_due_day=25)
        client.patch(
            "/api/v1/write/ledgers/lgs3/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )

        r = client.get(
            "/api/v1/read/ledgers/lgs3/accounts/acc-child/interest-free-suggestion", headers=hdr_web,
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 5. POST .../accounts/{id}/card-payment                                       #
# --------------------------------------------------------------------------- #


def _setup_payment_ledger(client, hdr_app, ledger_id, *, billing_day=5, payment_due_day=20):
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-group",
          {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY",
           "billingDay": billing_day, "paymentDueDay": payment_due_day}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-cash",
          {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"},
          device_id="d-app")


def test_card_payment_full_amount_pays_children_and_no_leftover():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp1")
        _push(client, hdr_app, "lgp1", "account", "acc-card-a",
              {"syncId": "acc-card-a", "name": "卡A", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")
        _push(client, hdr_app, "lgp1", "account", "acc-card-b",
              {"syncId": "acc-card-b", "name": "卡B", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), 5)
        _push(client, hdr_app, "lgp1", "transaction", "tx-a",
              {"syncId": "tx-a", "type": "expense", "amount": 300.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card-a", "accountName": "卡A"}, device_id="d-app")
        _push(client, hdr_app, "lgp1", "transaction", "tx-b",
              {"syncId": "tx-b", "type": "expense", "amount": 200.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card-b", "accountName": "卡B"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgp1/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 500.0, "from_account_id": "acc-cash"},
        )
        assert r.status_code == 200, r.text

        with TS() as db:
            row_a = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-card-a",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
            row_b = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-card-b",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
            row_group = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-group",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
        assert row_a is not None and row_a.amount == 300.0
        assert row_b is not None and row_b.amount == 200.0
        assert row_group is None  # 恰好付清,没有溢繳,不该有打到群組的交易

        summary = client.get(
            "/api/v1/read/ledgers/lgp1/accounts/acc-group/billing-summary", headers=hdr_web,
        ).json()
        assert summary["remaining_due"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_card_payment_overpayment_leftover_goes_to_group():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp2")
        _push(client, hdr_app, "lgp2", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), 5)
        _push(client, hdr_app, "lgp2", "transaction", "tx-spend",
              {"syncId": "tx-spend", "type": "expense", "amount": 100.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card", "accountName": "卡"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgp2/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 150.0, "from_account_id": "acc-cash"},
        )
        assert r.status_code == 200, r.text

        with TS() as db:
            row_card = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-card",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
            row_group = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-group",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
        assert row_card is not None and row_card.amount == 100.0
        assert row_group is not None and row_group.amount == 50.0  # 溢繳結轉到群組

        summary = client.get(
            "/api/v1/read/ledgers/lgp2/accounts/acc-group/billing-summary", headers=hdr_web,
        ).json()
        assert summary["remaining_due"] == -50.0
    finally:
        app.dependency_overrides.clear()


def test_card_payment_partial_amount_splits_proportionally():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp3")
        _push(client, hdr_app, "lgp3", "account", "acc-card-a",
              {"syncId": "acc-card-a", "name": "卡A", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")
        _push(client, hdr_app, "lgp3", "account", "acc-card-b",
              {"syncId": "acc-card-b", "name": "卡B", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), 5)
        # 卡A欠 300,卡B欠 100,共 400。只付 200(一半),应按 3:1 比例分攤。
        _push(client, hdr_app, "lgp3", "transaction", "tx-a",
              {"syncId": "tx-a", "type": "expense", "amount": 300.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card-a", "accountName": "卡A"}, device_id="d-app")
        _push(client, hdr_app, "lgp3", "transaction", "tx-b",
              {"syncId": "tx-b", "type": "expense", "amount": 100.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card-b", "accountName": "卡B"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgp3/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 200.0, "from_account_id": "acc-cash"},
        )
        assert r.status_code == 200, r.text

        with TS() as db:
            row_a = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-card-a",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
            row_b = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-card-b",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
            row_group = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.to_account_sync_id == "acc-group",
                    ReadTxProjection.tx_type == "transfer",
                )
            )
        assert row_a is not None and row_a.amount == 150.0  # 300/400 * 200
        assert row_b is not None and row_b.amount == 50.0  # 100/400 * 200
        assert row_group is None
        assert row_a.amount + row_b.amount == 200.0
    finally:
        app.dependency_overrides.clear()


def test_card_payment_rejects_non_group_target():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgp4", "ledger", "lgp4",
              {"syncId": "lgp4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgp4", "account", "acc-cash1",
              {"syncId": "acc-cash1", "name": "現金1", "type": "cash", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgp4", "account", "acc-cash2",
              {"syncId": "acc-cash2", "name": "現金2", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgp4/accounts/acc-cash1/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 100.0, "from_account_id": "acc-cash2"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_card_payment_rejects_from_account_that_is_a_group():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp5")
        _push(client, hdr_app, "lgp5", "account", "acc-group-other",
              {"syncId": "acc-group-other", "name": "另一個主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgp5/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 100.0, "from_account_id": "acc-group-other"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_card_payment_rejects_same_account():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp6@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp6@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp6")

        r = client.post(
            "/api/v1/write/ledgers/lgp6/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 100.0, "from_account_id": "acc-group"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_card_payment_rejects_unknown_from_account():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccp7@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccp7@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _setup_payment_ledger(client, hdr_app, "lgp7")

        r = client.post(
            "/api/v1/write/ledgers/lgp7/accounts/acc-group/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 100.0, "from_account_id": "does-not-exist"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# X. GET /workspace/accounts:account_group 自己的卡片顯示子帳戶加總           #
#    (2026-08-02 web UI 手測發現:主帳戶卡片一直顯示 0,因為 group 自己       #
#    永遠沒有交易 —— 見 read/workspace.py::list_workspace_accounts)          #
# --------------------------------------------------------------------------- #


def test_workspace_accounts_group_row_aggregates_children_stats():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccagg1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccagg1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgagg1")
        client.patch(
            "/api/v1/write/ledgers/lgagg1/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        # 第二张子卡,确认多子帳戶也正确加总
        _push(client, hdr_app, "lgagg1", "account", "acc-child2",
              {"syncId": "acc-child2", "name": "子卡2", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"},
              device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgagg1/transactions",
            headers=hdr_web,
            json={"base_change_id": 0, "tx_type": "expense", "amount": 600.0,
                  "happened_at": _iso(), "account_id": "acc-child"},
        )
        assert r.status_code == 200, r.text

        r2 = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        rows = {row["id"]: row for row in r2.json()}
        assert rows["acc-child"]["expense_total"] == 600.0
        assert rows["acc-child"]["balance"] == -600.0
        # 群組自己没有任何交易,但卡片应该显示子帳戶(acc-child + acc-child2)
        # 加总,而不是恒为 0。
        group_row = rows["acc-group"]
        assert group_row["expense_total"] == 600.0, group_row
        assert group_row["income_total"] == 0.0, group_row
        assert group_row["balance"] == -600.0, group_row
        assert group_row["tx_count"] == 1, group_row
    finally:
        app.dependency_overrides.clear()


def test_workspace_accounts_billing_root_shows_due_badge_fields():
    """§2.9 補強(2026-08-02):帳戶列表卡片要能顯示「可繳款 截止日 X/X」,
    不用等使用者點開詳情。只對 billing-root(account_group / 沒有掛靠群組
    的獨立信用卡)且真的欠款(> 0)時才有值,其它帳戶維持 None。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccbadge1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccbadge1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        now = datetime.now(timezone.utc)
        yesterday = now.date() - timedelta(days=1)
        billing_day = yesterday.day
        _seed_group_and_child(
            client, hdr_app, "lgbadge1", billing_day=billing_day, payment_due_day=20,
        )
        client.patch(
            "/api/v1/write/ledgers/lgbadge1/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        r = client.post(
            "/api/v1/write/ledgers/lgbadge1/transactions",
            headers=hdr_web,
            json={"base_change_id": 0, "tx_type": "expense", "amount": 500.0,
                  "happened_at": _dt(cycle_start + timedelta(days=1)),
                  "account_id": "acc-child"},
        )
        assert r.status_code == 200, r.text

        r2 = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        rows = {row["id"]: row for row in r2.json()}

        r3 = client.get(
            "/api/v1/read/ledgers/lgbadge1/accounts/acc-group/billing-summary", headers=hdr_web,
        )
        assert r3.status_code == 200, r3.text
        expected = r3.json()

        group_row = rows["acc-group"]
        assert group_row["billing_remaining_due"] == expected["remaining_due"]
        assert group_row["billing_due_date"][:10] == expected["due_date"][:10]
        # 子卡自己不是 billing-root(掛靠了群組),不该单独出现 badge。
        assert rows["acc-child"]["billing_due_date"] is None
        assert rows["acc-child"]["billing_remaining_due"] is None
    finally:
        app.dependency_overrides.clear()


def test_workspace_accounts_billing_root_without_debt_has_no_due_badge():
    """已繳清/沒有任何欠款的 billing-root 帳戶不該顯示 badge。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccbadge2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccbadge2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _seed_group_and_child(client, hdr_app, "lgbadge2", billing_day=5, payment_due_day=20)
        client.patch(
            "/api/v1/write/ledgers/lgbadge2/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )

        r2 = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        rows = {row["id"]: row for row in r2.json()}
        group_row = rows["acc-group"]
        assert group_row["billing_due_date"] is None, group_row
        assert group_row["billing_remaining_due"] is None, group_row
    finally:
        app.dependency_overrides.clear()


def test_workspace_transactions_account_sync_id_expands_group_children():
    """2026-08-04 修复:打開主帳戶(account_group)的帳戶詳情永遠是空的
    —— 群組自己從不擁有交易,`/read/workspace/transactions?account_sync_id=
    <group>` 原本只精確比對群組自己的 sync_id,現在應該展開成子帳戶一起
    查,回傳它們的交易。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccwtx1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccwtx1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_group_and_child(client, hdr_app, "lgwtx1")
        client.patch(
            "/api/v1/write/ledgers/lgwtx1/accounts/acc-child",
            headers=hdr_web, json={"base_change_id": 0, "parent_account_id": "acc-group"},
        )
        r = client.post(
            "/api/v1/write/ledgers/lgwtx1/transactions",
            headers=hdr_web,
            json={"base_change_id": 0, "tx_type": "expense", "amount": 600.0,
                  "happened_at": _iso(), "account_id": "acc-child"},
        )
        assert r.status_code == 200, r.text

        r2 = client.get(
            "/api/v1/read/workspace/transactions",
            headers=hdr_web,
            params={"account_sync_id": "acc-group"},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["total"] == 1, body
        assert body["items"][0]["account_id"] == "acc-child"

        # 精确比对子帳戶自己的 sync_id 仍然正常工作(没被这次改动破坏)。
        r3 = client.get(
            "/api/v1/read/workspace/transactions",
            headers=hdr_web,
            params={"account_sync_id": "acc-child"},
        )
        assert r3.json()["total"] == 1

        # 一張普通(非群組)帳戶不展开任何东西,行为不变。
        r4 = client.get(
            "/api/v1/read/workspace/transactions",
            headers=hdr_web,
            params={"account_sync_id": "acc-nonexistent"},
        )
        assert r4.json()["total"] == 0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Y. POST .../accounts/{id}/card-payment:單卡(沒有群組)也能繳款、           #
#    溢繳正確結轉成可用額度增加(不會因為 group.sync_id == 唯一子帳戶的      #
#    sync_id 而被 compute_group_billing 重複計算兩次)                       #
# --------------------------------------------------------------------------- #


def test_card_payment_standalone_credit_card_full_and_overpay():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccps1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccps1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgps1", "ledger", "lgps1",
              {"syncId": "lgps1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgps1", "account", "acc-solo",
              {"syncId": "acc-solo", "name": "獨立卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 5, "paymentDueDay": 20, "creditLimit": 10000.0}, device_id="d-app")
        _push(client, hdr_app, "lgps1", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        now = datetime.now(timezone.utc)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), 5)
        _push(client, hdr_app, "lgps1", "transaction", "tx-solo",
              {"syncId": "tx-solo", "type": "expense", "amount": 1000.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-solo", "accountName": "獨立卡"}, device_id="d-app")

        # 溢繳:欠 1000,繳 10000(付清 + 9000 溢繳),不该重複计算导致
        # remaining_due/available_credit 算错。
        r = client.post(
            "/api/v1/write/ledgers/lgps1/accounts/acc-solo/card-payment",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 10000.0, "from_account_id": "acc-cash"},
        )
        assert r.status_code == 200, r.text

        r2 = client.get(
            "/api/v1/read/ledgers/lgps1/accounts/acc-solo/billing-summary", headers=hdr_web,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["remaining_due"] == -9000.0, body
        assert body["available_credit"] == 19000.0, body
        assert body["paid_amount"] == 10000.0, body
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_cycle_offset_navigates_periods():
    """§2.9 補強(2026-08-02):`cycle_offset` 讓使用者按帳單週期(不是序號,
    是日期區間)翻頁瀏覽歷史/本期/當前累積中的帳單,對齊 Moze 參考截圖的
    `< 起日–迄日 >` 互動。上面「現在當下」欄位(cycle_start/remaining_due 等)
    不受 cycle_offset 影響,新增的 period_* 欄位才跟著 query param 變化。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ccpo1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ccpo1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lgpo1", "ledger", "lgpo1",
              {"syncId": "lgpo1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        yesterday = now.date() - timedelta(days=1)
        billing_day = yesterday.day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        prev_start, prev_end = credit_card.shift_cycle(cycle_start, cycle_end, billing_day, -1)

        _push(client, hdr_app, "lgpo1", "account", "acc-groupo",
              {"syncId": "acc-groupo", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 20}, device_id="d-app")
        _push(client, hdr_app, "lgpo1", "account", "acc-cardo",
              {"syncId": "acc-cardo", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-groupo"}, device_id="d-app")

        # 上一期消费 200,从未还款 → 结转成这一期的「上期欠款」。
        _push(client, hdr_app, "lgpo1", "transaction", "tx-prev",
              {"syncId": "tx-prev", "type": "expense", "amount": 200.0,
               "happenedAt": _dt(prev_start + timedelta(days=1)),
               "accountId": "acc-cardo", "accountName": "卡"}, device_id="d-app")
        # 这一期消费 80。
        _push(client, hdr_app, "lgpo1", "transaction", "tx-cur",
              {"syncId": "tx-cur", "type": "expense", "amount": 80.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-cardo", "accountName": "卡"}, device_id="d-app")

        # offset=0(本期,预设,不带 query param)
        r0 = client.get(
            "/api/v1/read/ledgers/lgpo1/accounts/acc-groupo/billing-summary", headers=hdr_web,
        )
        assert r0.status_code == 200, r0.text
        d0 = r0.json()
        assert d0["period_cycle_start"] == d0["cycle_start"]
        assert d0["period_cycle_end"] == d0["cycle_end"]
        assert d0["period_new_spend"] == 80.0
        assert d0["period_carryover_due"] == 200.0
        assert d0["period_total_due"] == 280.0
        assert d0["period_paid_in_cycle"] == 0.0
        assert d0["period_remaining_due"] == 280.0
        assert d0["period_has_older"] is True
        assert d0["period_has_newer"] is True

        # offset=-1(上一期):新增花費是那 200,没有更早的交易,has_older
        # 应该变成 False,has_newer 应该是 True(offset 0 还在后面)。
        r_prev = client.get(
            "/api/v1/read/ledgers/lgpo1/accounts/acc-groupo/billing-summary"
            "?cycle_offset=-1", headers=hdr_web,
        )
        assert r_prev.status_code == 200, r_prev.text
        d_prev = r_prev.json()
        assert d_prev["period_new_spend"] == 200.0
        assert d_prev["period_carryover_due"] == 0.0
        assert d_prev["period_total_due"] == 200.0
        assert d_prev["period_remaining_due"] == 200.0
        assert d_prev["period_has_older"] is False
        assert d_prev["period_has_newer"] is True

        # offset=1(目前还没结束的这期,等同 open_cycle_*):还没有任何这期
        # 的消费,has_newer 应该是 False(没有更后面的了)。
        r_open = client.get(
            "/api/v1/read/ledgers/lgpo1/accounts/acc-groupo/billing-summary"
            "?cycle_offset=1", headers=hdr_web,
        )
        assert r_open.status_code == 200, r_open.text
        d_open = r_open.json()
        assert d_open["period_cycle_start"] == d0["open_cycle_start"]
        assert d_open["period_cycle_end"] == d0["open_cycle_end"]
        assert d_open["period_new_spend"] == 0.0
        assert d_open["period_carryover_due"] == 280.0
        assert d_open["period_has_newer"] is False

        # 超出允许范围(> 1,不能看还没开始的未来週期)应该被 query 参数
        # 校验挡掉,回 422。
        r_bad = client.get(
            "/api/v1/read/ledgers/lgpo1/accounts/acc-groupo/billing-summary"
            "?cycle_offset=2", headers=hdr_web,
        )
        assert r_bad.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_late_payment_settles_older_cycle_not_current():
    """§2.9 補強(2026-08-02 用户实测反馈的归属 bug):6/30~7/30 的帳單直到
    本期(7/30~8/30)已经开始才繳款(比如 8/2 才繳),繳款交易自己的
    `happened_at` 落在本期窗口內,但這筆錢實際上是要清償上一期的帳單 ——
    参照真实信用卡「先进先出」清偿逻辑,回顾上一期时应该显示已繳清
    (paid_in_cycle == 应缴金额,remaining_due 归零),而不是本期无中生有
    地多出一笔"已缴"、上一期却仍然显示分文未还。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "cclate1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "cclate1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lglate1", "ledger", "lglate1",
              {"syncId": "lglate1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        yesterday = now.date() - timedelta(days=1)
        billing_day = yesterday.day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        prev_start, prev_end = credit_card.shift_cycle(cycle_start, cycle_end, billing_day, -1)

        _push(client, hdr_app, "lglate1", "account", "acc-groupl",
              {"syncId": "acc-groupl", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 20}, device_id="d-app")
        _push(client, hdr_app, "lglate1", "account", "acc-cardl",
              {"syncId": "acc-cardl", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-groupl"}, device_id="d-app")
        _push(client, hdr_app, "lglate1", "account", "acc-cashl",
              {"syncId": "acc-cashl", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")

        # 上一期(prev_start~prev_end)消费 1200.50,当时没有还。
        _push(client, hdr_app, "lglate1", "transaction", "tx-prevl",
              {"syncId": "tx-prevl", "type": "expense", "amount": 1200.50,
               "happenedAt": _dt(prev_start + timedelta(days=1)),
               "accountId": "acc-cardl", "accountName": "卡"}, device_id="d-app")

        # 本期(cycle_start~cycle_end)還没有任何消费。now(繳款當下)已经进入
        # 本期窗口——繳款交易的 happened_at 会落在本期,但金額只夠付清上一期。
        _push(client, hdr_app, "lglate1", "transaction", "tx-latepay",
              {"syncId": "tx-latepay", "type": "transfer", "amount": 1200.50,
               "happenedAt": now.isoformat(),
               "fromAccountId": "acc-cashl", "fromAccountName": "現金",
               "toAccountId": "acc-cardl", "toAccountName": "卡"}, device_id="d-app")

        # offset=-1:上一期该显示已缴清。
        r_prev = client.get(
            "/api/v1/read/ledgers/lglate1/accounts/acc-groupl/billing-summary"
            "?cycle_offset=-1", headers=hdr_web,
        )
        assert r_prev.status_code == 200, r_prev.text
        d_prev = r_prev.json()
        assert d_prev["period_new_spend"] == 1200.50
        assert d_prev["period_carryover_due"] == 0.0
        assert d_prev["period_total_due"] == 1200.50
        assert d_prev["period_paid_in_cycle"] == 1200.50
        assert d_prev["period_remaining_due"] == 0.0

        # offset=0(本期):虽然繳款交易的 happened_at 落在这期窗口内,但那笔
        # 钱已经被算进上一期的清偿,这期不应该显示任何新增花費或已繳金額。
        r_cur = client.get(
            "/api/v1/read/ledgers/lglate1/accounts/acc-groupl/billing-summary", headers=hdr_web,
        )
        assert r_cur.status_code == 200, r_cur.text
        d_cur = r_cur.json()
        assert d_cur["period_new_spend"] == 0.0
        assert d_cur["period_carryover_due"] == 0.0
        assert d_cur["period_total_due"] == 0.0
        assert d_cur["period_paid_in_cycle"] == 0.0
        assert d_cur["period_remaining_due"] == 0.0
    finally:
        app.dependency_overrides.clear()
