"""信用卡紅利回饋自動入帳(§2.9.5.4 MOZE_FEATURE_GAP_SD.md)。跟
`tests/test_credit_card_autopay.py` 同一个模式:直接调用
`services.card_reward_payout.materialize_due_card_reward_payouts(db)`,不经过
HTTP background loop,断言生成的交易 + `CardRewardPayout` 去重台帳 +
notifications 表内容。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import CardRewardPayout, Notification, ReadTxProjection, User
from src.services import card_reward_payout, credit_card


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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text


def _income_tx_to(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.tx_type == "income",
                ReadTxProjection.account_sync_id == account_id,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _all_tx_in(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.account_sync_id == account_id,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _payout_rows(TS, email, rule_id):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(CardRewardPayout).where(
                CardRewardPayout.user_id == user_id, CardRewardPayout.rule_sync_id == rule_id,
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _notifications_for(TS, email):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(Notification).where(
                Notification.user_id == user_id, Notification.category == "card_reward",
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _login_and_seed(client, ledger_id, email, *, billing_day=None, payment_due_day=None):
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    card_payload = {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"}
    if billing_day is not None:
        card_payload["billingDay"] = billing_day
    if payment_due_day is not None:
        card_payload["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card", card_payload, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-wallet",
          {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "CNY"}, device_id="d-app")
    return hdr_app, hdr_web


def _latest_change_id(client, token_hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=token_hdr)
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _create_rule(client, hdr_web, ledger_id, *, base=0, **kwargs):
    payload = {
        "base_change_id": base, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0,
    }
    payload.update(kwargs)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules",
        headers=hdr_web, json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


# --------------------------------------------------------------------------- #
# immediate_after_tx / after_posting_date(逐筆結算)                          #
# --------------------------------------------------------------------------- #


def test_immediate_after_tx_pays_only_after_settlement_days_and_dedups():
    client, TS = _make_client()
    try:
        email = "crp1@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp1", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp1",
            settlement_type="immediate_after_tx", settlement_days=3, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        now = tx_day + timedelta(days=2)  # 還沒到 settlement_days=3
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0

        now2 = tx_day + timedelta(days=3)  # 到期
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result2 == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 10.0  # 100 * 10%

        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1
        assert payouts[0].dedup_key == "tx-1"

        # 重跑不重複入帳。
        with TS() as db:
            result3 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result3 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 1
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_reward_tx_gets_category_and_source_link():
    """§2.9.5.4 補強(2026-08-04 使用者反饋):①回饋 income 交易自動帶固定
    的「回饋金」分類(不然空白分類很奇怪);②反查 `reward_source_tx_id`
    指向原始消費(取代舊版把 tx sync_id 原樣寫進備註文字裡的做法),web 端
    能靠它渲染可點擊的「關聯消費」連結。"""
    client, TS = _make_client()
    try:
        email = "crp-src@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-src", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-src",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-src", "transaction", "tx-src-1",
              {"syncId": "tx-src-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}

        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx = incomes[0]
        assert reward_tx.category_name == "回饋金"
        assert reward_tx.category_kind == "income"
        assert reward_tx.category_sync_id is not None
        assert reward_tx.reward_source_tx_sync_id == "tx-src-1"
        # 舊版備註直接嵌入 tx sync_id,使用者反饋不友善;改版後不再出現在
        # 備註文字裡(改走上面的結構化反查欄位)。
        assert "tx-src-1" not in (reward_tx.note or "")
    finally:
        app.dependency_overrides.clear()


def test_reward_category_reused_not_duplicated_across_payouts():
    """同一個使用者名下,「回饋金」分類只會建一次,之後每次入帳都重用同一個
    sync_id(不管是逐筆結算多次觸發,還是不同規則各自入帳)。"""
    client, TS = _make_client()
    try:
        email = "crp-cat@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-cat", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-cat",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        for i in range(2):
            tx_day = datetime.now(timezone.utc) - timedelta(days=1)
            _push(client, hdr_app, "lgp-cat", "transaction", f"tx-cat-{i}",
                  {"syncId": f"tx-cat-{i}", "type": "expense", "amount": 50.0, "happenedAt": _iso(tx_day),
                   "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
                  device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()

        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 2
        category_ids = {tx.category_sync_id for tx in incomes}
        assert len(category_ids) == 1
        assert None not in category_ids

        from src.models import UserCategoryProjection

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            rows = db.scalars(
                select(UserCategoryProjection).where(
                    UserCategoryProjection.user_id == user_id,
                    UserCategoryProjection.name == "回饋金",
                )
            ).all()
        assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_reward_source_tx_id_partial_update_keeps_existing_value():
    """跟 rewardRuleIds/debtId 同款契约:partial update 只带其它字段时,
    `rewardSourceTxId` 缺键要保留既有值,不能被静默清空(既有 bug 类型,見
    `write/_shared.py::_projection_row_to_tx_dict` 跟
    `snapshot_builder.py` 的漏 SELECT 注释)。"""
    client, TS = _make_client()
    try:
        email = "crp-merge@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-merge", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-merge",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-merge", "transaction", "tx-merge-1",
              {"syncId": "tx-merge-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx_id = incomes[0].sync_id

        # partial update:只带 note,不带 rewardSourceTxId。
        _push(client, hdr_app, "lgp-merge", "transaction", reward_tx_id,
              {"syncId": reward_tx_id, "note": "改個備注"}, device_id="d-app")

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == reward_tx_id)
            )
            assert row is not None
            assert row.note == "改個備注"
            assert row.reward_source_tx_sync_id == "tx-merge-1"
    finally:
        app.dependency_overrides.clear()


def test_web_patch_other_field_keeps_reward_source_tx_id():
    """跟上面 mobile push 那條契約測試對應的 web fast path 版本
    (`_commit_write_fast_tx` → `_projection_row_to_tx_dict`)——PATCH 回饋
    入帳交易的備註時,不帶 `rewardSourceTxId` 不能把它靜默清空(既有 bug
    類型,同 §2.9.5.4 之前踩過的 `settlement_type` 漏 SELECT)。"""
    client, TS = _make_client()
    try:
        email = "crp-fast@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp-fast", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp-fast",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgp-fast", "transaction", "tx-fast-1",
              {"syncId": "tx-fast-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        reward_tx_id = incomes[0].sync_id

        base = _latest_change_id(client, hdr_web, "lgp-fast")
        res = client.patch(
            f"/api/v1/write/ledgers/lgp-fast/transactions/{reward_tx_id}",
            headers=hdr_web,
            json={"base_change_id": base, "note": "web 端改備注"},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == reward_tx_id)
            )
            assert row is not None
            assert row.note == "web 端改備注"
            assert row.reward_source_tx_sync_id == "tx-fast-1"
            assert row.category_name == "回饋金"
    finally:
        app.dependency_overrides.clear()


def test_after_posting_date_currently_behaves_same_as_immediate_after_tx():
    """§2.10 延後入帳(deferred_posting_at)還沒實作,after_posting_date 目前
    行為等同 immediate_after_tx,見 card_rewards.compute_settlement_date
    docstring。這條測試鎖住現況,§2.10 落地後要重跑。"""
    client, TS = _make_client()
    try:
        email = "crp2@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp2", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp2",
            settlement_type="after_posting_date", settlement_days=2, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=2)
        _push(client, hdr_app, "lgp2", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 50.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 5.0
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_cap_amount_clamps_and_dedups_zero_amount():
    client, TS = _make_client()
    try:
        email = "crp3@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp3", email, billing_day=billing_day, payment_due_day=10)
        rule_id = _create_rule(
            client, hdr_web, "lgp3",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet", cap_amount=15.0,
        )
        # 兩筆時間錯開,確保 _qualifying_transactions 依 happened_at 排序後
        # 順序穩定(不依賴同一時間戳下 SQLite 的 tie-break 行為)。
        _push(client, hdr_app, "lgp3", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp3", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 100.0,
               "happenedAt": _iso(now + timedelta(minutes=1)),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 2, "period_payouts": 0}

        incomes = _income_tx_to(TS, "acc-wallet")
        # 第一筆 10(未超上限),第二筆被夾到剩餘 5(15-10),第二筆入帳金額 5。
        assert sorted(t.amount for t in incomes) == [5.0, 10.0]

        payouts = {p.dedup_key: p.amount for p in _payout_rows(TS, email, rule_id)}
        assert payouts == {"tx-1": 10.0, "tx-2": 5.0}

        # 額度用完,第三筆完全不產生入帳交易,但仍記去重(金額 0)。
        _push(client, hdr_app, "lgp3", "transaction", "tx-3",
              {"syncId": "tx-3", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 1, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 2  # 第三筆沒有新增 income 交易
        payouts2 = {p.dedup_key: p.amount for p in _payout_rows(TS, email, rule_id)}
        assert payouts2["tx-3"] == 0.0

        # 再重跑一次,tx-3 已經記過去重,不會被重複評估。
        with TS() as db:
            result3 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result3 == {"tx_payouts": 0, "period_payouts": 0}
    finally:
        app.dependency_overrides.clear()


def test_immediate_after_tx_self_credit_offsets_billing():
    """reward_account_id 指回卡片自己(最常見用例——直接折抵當期帳單)。

    2026-08-04 使用者反饋修復:payout 引擎生成的 income 交易日期必須是
    「規則算出的入帳日」(`tx_happened_at + settlement_days`),不能是
    「排程實際執行的時間點」(`now`)——不然使用者事後補記一筆很久以前的
    消費(比如今天補記上個月的帳),回饋交易會被錯誤地記在「今天」,而不是
    使用者設定的入帳時機該落在的那一天。這裡 `settlement_days=0`,原始消費
    (`tx-1`)刻意放在已結束的週期內,所以回饋應該落在**同一個**已結束週期
    裡,反映在 `remaining_due`(終身跑動餘額)/`statement_amount`(該週期
    窗口)雙雙減少 20,而不是被計入還在累積中的 `open_cycle_spend`(修復前
    的錯誤行為——回饋交易被錯誤地蓋上 `now` 的時間戳,才會落到當下這個還在
    累積的週期)。"""
    client, TS = _make_client()
    try:
        email = "crp4@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp4", email, billing_day=billing_day, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp4",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-card",
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp4", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        billing_before = client.get(
            "/api/v1/read/ledgers/lgp4/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        assert billing_before["open_cycle_spend"] == 0.0
        assert billing_before["remaining_due"] == 200.0
        assert billing_before["statement_amount"] == 200.0

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}

        billing_after = client.get(
            "/api/v1/read/ledgers/lgp4/accounts/acc-card/billing-summary", headers=hdr_web,
        ).json()
        # 20 元回饋(200*10%)日期落在原始消費所屬的那個已結束週期(settlement_
        # days=0),所以計入 remaining_due/statement_amount,不是 open_cycle_
        # spend(那個窗口完全沒有任何交易落在裡面,理當維持 0)。
        assert billing_after["remaining_due"] == 180.0
        assert billing_after["statement_amount"] == 180.0
        assert billing_after["open_cycle_spend"] == 0.0

        # 回饋交易自己的 happened_at 應該等於 tx-1 的消費日期(settlement_
        # days=0),不是 payout 引擎實際跑的時間點 `now`。
        with TS() as db:
            reward_tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == db.scalar(
                        select(ReadTxProjection.ledger_id).where(ReadTxProjection.sync_id == "tx-1")
                    ),
                    ReadTxProjection.tx_type == "income",
                )
            )
            assert reward_tx is not None
            happened = reward_tx.happened_at
            if happened.tzinfo is None:
                happened = happened.replace(tzinfo=timezone.utc)
            assert happened.date() == spend_at.date()
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# period_end(區間結束後整批結算)                                             #
# --------------------------------------------------------------------------- #


def test_period_end_pays_once_after_cycle_closes_and_dedups():
    client, TS = _make_client()
    try:
        email = "crp5@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp5", email, billing_day=billing_day, payment_due_day=25)
        rule_id = _create_rule(
            client, hdr_web, "lgp5",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp5", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 300.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 1}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 30.0  # 300 * 10%

        notifs = _notifications_for(TS, email)
        assert len(notifs) == 1
        assert notifs[0].payload_json["ruleId"] == rule_id

        # 重跑不重複入帳、不重複通知。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 1
        assert len(_notifications_for(TS, email)) == 1
    finally:
        app.dependency_overrides.clear()


def test_period_end_skips_when_no_billing_schedule():
    client, TS = _make_client()
    try:
        email = "crp6@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgp6", email)  # 没设 billing_day
        _create_rule(
            client, hdr_web, "lgp6",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
    finally:
        app.dependency_overrides.clear()


def test_period_end_cross_card_cap_shared_key_pools_rules():
    client, TS = _make_client()
    try:
        email = "crp7@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp7", email, billing_day=billing_day, payment_due_day=25)
        _push(client, hdr_app, "lgp7", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "副卡", "type": "credit_card", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 25}, device_id="d-app")

        r1 = client.post(
            "/api/v1/write/ledgers/lgp7/accounts/acc-card/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "主卡", "rate_type": "percentage", "rate_value": 10.0,
                "cap_amount": 25.0, "cap_shared_key": "grp",
                "settlement_type": "period_end", "reward_account_id": "acc-wallet",
            },
        )
        assert r1.status_code == 200, r1.text
        rule1_id = r1.json()["entity_id"]
        r2 = client.post(
            "/api/v1/write/ledgers/lgp7/accounts/acc-card2/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "副卡",
                "rate_type": "percentage", "rate_value": 10.0,
                "cap_amount": 25.0, "cap_shared_key": "grp",
                "settlement_type": "period_end", "reward_account_id": "acc-wallet",
            },
        )
        assert r2.status_code == 200, r2.text
        rule2_id = r2.json()["entity_id"]

        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        _push(client, hdr_app, "lgp7", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card", "accountName": "主卡", "rewardRuleIds": [rule1_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgp7", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 200.0, "happenedAt": _iso(spend_at),
               "accountId": "acc-card2", "accountName": "副卡", "rewardRuleIds": [rule2_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 2}

        incomes = _income_tx_to(TS, "acc-wallet")
        # raw 各 20,合计 40 > 共享上限 25,各半分攤 20/40*25=12.5。
        assert sorted(t.amount for t in incomes) == [12.5, 12.5]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# manual / enabled / expired 邊界                                            #
# --------------------------------------------------------------------------- #


def test_manual_settlement_type_never_pays_out():
    client, TS = _make_client()
    try:
        email = "crp8@t.com"
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        hdr_app, hdr_web = _login_and_seed(client, "lgp8", email, billing_day=billing_day, payment_due_day=10)
        rule_id = _create_rule(client, hdr_web, "lgp8")  # 預設 settlement_type="manual"
        _push(client, hdr_app, "lgp8", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
        assert len(_income_tx_to(TS, "acc-card")) == 0
    finally:
        app.dependency_overrides.clear()


def test_disabled_rule_never_pays_out():
    client, TS = _make_client()
    try:
        email = "crp9@t.com"
        now = datetime.now(timezone.utc)
        hdr_app, hdr_web = _login_and_seed(client, "lgp9", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp9",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet", enabled=False,
        )
        _push(client, hdr_app, "lgp9", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
    finally:
        app.dependency_overrides.clear()


def test_expired_rule_still_pays_for_transaction_made_while_active():
    """入帳看交易當下是否符合資格,不因規則事後過期(ends_at 已過)被追回。"""
    client, TS = _make_client()
    try:
        email = "crp10@t.com"
        now = datetime.now(timezone.utc)
        hdr_app, hdr_web = _login_and_seed(client, "lgp10", email)
        rule_id = _create_rule(
            client, hdr_web, "lgp10",
            settlement_type="immediate_after_tx", settlement_days=0,
            reward_account_id="acc-wallet",
            ends_at=_iso(now - timedelta(days=1)),  # 已過期
        )
        tx_at = now - timedelta(days=5)  # 過期前發生的交易
        _push(client, hdr_app, "lgp10", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_at),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _income_tx_to(TS, "acc-wallet")
        assert len(incomes) == 1
        assert incomes[0].amount == 10.0
    finally:
        app.dependency_overrides.clear()


def test_internal_task_endpoint_reports_reward_payout_counts():
    client, TS = _make_client()
    try:
        admin_email = "crp-admin@t.com"
        app_tok = _login(client, admin_email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lgp11", "ledger", "lgp11",
              {"syncId": "lgp11", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgp11", "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgp11", "account", "acc-wallet",
              {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        # is_admin 要在登入拿 token 之前就设好(admin 状态烙在 JWT claim 里,
        # 见 test_backup_admin_api.py::_bootstrap_admin 同款先设 flag 再登入)。
        with TS() as db:
            from src.models import User as UserModel
            user = db.scalar(select(UserModel).where(UserModel.email == admin_email))
            user.is_admin = True
            db.commit()
        web_tok = _login(client, admin_email, device_id="d-web", client_type="web")
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        rule_id = _create_rule(
            client, hdr_web, "lgp11",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _push(client, hdr_app, "lgp11", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        r = client.post("/api/v1/internal/tasks/materialize-recurring", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["card_reward_tx_payouts"] == 1
        assert body["card_reward_period_payouts"] == 0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 退款(2026-08-04 使用者反饋):退款交易自動歸類「退款」分類 + 已入帳的      #
# 信用卡回饋要跟著沖銷,不管排程是退款前還是退款後跑                          #
# --------------------------------------------------------------------------- #


def test_refund_before_payout_excludes_tx_from_future_settlement():
    """退款發生在排程還沒跑到結算日之前 —— 被退款的消費從此排除在
    `_qualifying_transactions` 之外,排程晚點跑到也不會再把它排入結算,
    不需要任何沖銷(因為根本沒有入帳過)。"""
    client, TS = _make_client()
    try:
        email = "crp-ref1@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr1", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr1",
            settlement_type="immediate_after_tx", settlement_days=3, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr1")
        r = client.post(
            "/api/v1/write/ledgers/lgpr1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text

        # 排程晚點才跑到(超過 settlement_days),tx-1 已經被退款排除,不入帳。
        now2 = tx_day + timedelta(days=3)
        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now2)
            db.commit()
        assert result == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_income_tx_to(TS, "acc-wallet")) == 0
        assert len(_payout_rows(TS, email, rule_id)) == 0
    finally:
        app.dependency_overrides.clear()


def test_refund_after_payout_reverses_reward_and_auto_categorizes():
    """退款發生在排程已經跑過、回饋已經入帳之後 —— 退款當下同一個 DB
    transaction 裡補一筆沖銷交易,把已經拿到的回饋沖銷掉;退款交易本身跟
    沖銷交易都自動歸到自建的「退款」分類。"""
    client, TS = _make_client()
    try:
        email = "crp-ref2@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr2", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr2",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr2", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result == {"tx_payouts": 1, "period_payouts": 0}
        assert [t.amount for t in _income_tx_to(TS, "acc-wallet")] == [10.0]

        base = _latest_change_id(client, hdr_web, "lgpr2")
        r = client.post(
            "/api/v1/write/ledgers/lgpr2/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": "tx-1",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]

        with TS() as db:
            refund_row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert refund_row is not None
            assert refund_row.category_name == "退款"
            assert refund_row.category_kind == "income"
            assert refund_row.category_sync_id is not None

        # 沖銷交易:expense,金額等於已入帳的回饋金,落在回饋原本入帳的帳戶。
        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 1
        clawback = expenses[0]
        assert clawback.amount == 10.0
        assert clawback.category_name == "退款"
        assert clawback.category_kind == "expense"
        assert clawback.reward_source_tx_sync_id == "tx-1"

        # 沖銷不影響原本的去重記錄(那筆 payout 歷史還在,不是被撤銷/刪除)。
        payouts = _payout_rows(TS, email, rule_id)
        assert len(payouts) == 1

        # 重跑排程不會重複沖銷(沖銷只在退款建立當下觸發一次,不是排程行為;
        # tx-1 也已經被 `_qualifying_transactions` 排除,不會被重新評估)。
        with TS() as db:
            result2 = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result2 == {"tx_payouts": 0, "period_payouts": 0}
        assert len([t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_plain_refund_without_reward_rule_gets_refund_category():
    """跟信用卡回饋完全無關的普通退款,也要自動歸到「退款」分類——income/
    expense 各自獨立一份(退款可能是任一方向,見 §2.6 反向类型设计)。"""
    client, TS = _make_client()
    try:
        email = "crp-ref3@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr3", email)
        _push(client, hdr_app, "lgpr3", "transaction", "tx-plain-1",
              {"syncId": "tx-plain-1", "type": "expense", "amount": 50.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr3")
        r = client.post(
            "/api/v1/write/ledgers/lgpr3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 50.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-plain-1",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert row.category_name == "退款"
            assert row.category_kind == "income"

        # 反過來:income 交易被退成 expense,分类各自独立一份(不是共用一行)。
        _push(client, hdr_app, "lgpr3", "transaction", "tx-plain-income",
              {"syncId": "tx-plain-income", "type": "income", "amount": 30.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")
        base2 = _latest_change_id(client, hdr_web, "lgpr3")
        r2 = client.post(
            "/api/v1/write/ledgers/lgpr3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base2, "tx_type": "expense", "amount": 30.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-plain-income",
            },
        )
        assert r2.status_code == 200, r2.text
        refund_tx_id2 = r2.json()["entity_id"]
        with TS() as db:
            row2 = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id2))
            assert row2.category_name == "退款"
            assert row2.category_kind == "expense"

            from src.models import UserCategoryProjection

            user_id = db.scalar(select(User.id).where(User.email == email))
            cats = db.scalars(
                select(UserCategoryProjection).where(
                    UserCategoryProjection.user_id == user_id,
                    UserCategoryProjection.name == "退款",
                )
            ).all()
        assert {c.kind for c in cats} == {"income", "expense"}
    finally:
        app.dependency_overrides.clear()


def test_refund_respects_explicit_category_choice():
    """退款表单如果用户自己选了分类,原样尊重,不被自动归类覆盖。"""
    client, TS = _make_client()
    try:
        email = "crp-ref4@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr4", email)
        _push(client, hdr_app, "lgpr4", "transaction", "tx-explicit-1",
              {"syncId": "tx-explicit-1", "type": "expense", "amount": 40.0, "happenedAt": _iso(),
               "accountId": "acc-wallet", "accountName": "點數錢包"},
              device_id="d-app")
        _push(client, hdr_app, "lgpr4", "category", "cat-custom",
              {"syncId": "cat-custom", "name": "自訂分類", "kind": "income"}, device_id="d-app")

        base = _latest_change_id(client, hdr_web, "lgpr4")
        r = client.post(
            "/api/v1/write/ledgers/lgpr4/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 40.0,
                "happened_at": _iso(), "account_id": "acc-wallet", "refund_of_id": "tx-explicit-1",
                "category_id": "cat-custom", "category_name": "自訂分類", "category_kind": "income",
            },
        )
        assert r.status_code == 200, r.text
        refund_tx_id = r.json()["entity_id"]
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == refund_tx_id))
            assert row.category_name == "自訂分類"
    finally:
        app.dependency_overrides.clear()


def test_patch_newly_marking_tx_as_refund_reverses_reward_once():
    """PATCH 把一筆既有交易改成退款(`refund_of_id` 從空變非空)也要觸發
    同一套沖銷 + 自動歸類邏輯;之後再 PATCH 別的欄位(`refund_of_id` 原樣
    帶回)不能重複沖銷。"""
    client, TS = _make_client()
    try:
        email = "crp-ref5@t.com"
        hdr_app, hdr_web = _login_and_seed(client, "lgpr5", email)
        rule_id = _create_rule(
            client, hdr_web, "lgpr5",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgpr5", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert len(_income_tx_to(TS, "acc-wallet")) == 1

        base = _latest_change_id(client, hdr_web, "lgpr5")
        r = client.post(
            "/api/v1/write/ledgers/lgpr5/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base, "tx_type": "income", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card",
            },
        )
        assert r.status_code == 200, r.text
        tx_id = r.json()["entity_id"]
        base2 = r.json()["new_change_id"]

        r2 = client.patch(
            f"/api/v1/write/ledgers/lgpr5/transactions/{tx_id}",
            headers=hdr_web,
            json={"base_change_id": base2, "refund_of_id": "tx-1"},
        )
        assert r2.status_code == 200, r2.text
        base3 = r2.json()["new_change_id"]

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.category_name == "退款"

        expenses = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses) == 1

        # 再 PATCH 一次別的欄位,refund_of_id 原樣帶回,不應該重複沖銷。
        r3 = client.patch(
            f"/api/v1/write/ledgers/lgpr5/transactions/{tx_id}",
            headers=hdr_web,
            json={"base_change_id": base3, "refund_of_id": "tx-1", "note": "改個備注"},
        )
        assert r3.status_code == 200, r3.text
        expenses2 = [t for t in _all_tx_in(TS, "acc-wallet") if t.tx_type == "expense"]
        assert len(expenses2) == 1
    finally:
        app.dependency_overrides.clear()
