"""分類 / 帳戶匯入 + 範本下載端到端測試(2026-08 新增)。

覆盖 `src/routers/import_data/simple_endpoints.py` + `simple_parser.py` +
`templates.py`。跟 `test_import_csv.py`(交易匯入)同款 client/login/ledger
輔助函式風格。
"""
from __future__ import annotations

from sqlalchemy import select

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, LedgerMember, User, UserAccountProjection, UserCategoryProjection
from src.services.import_data.simple_cache import clear_all as clear_simple_cache


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _login(client: TestClient, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": "d-web",
            "client_type": "web", "device_name": "pytest", "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": "d-web",
            "client_type": "web", "device_name": "pytest", "platform": "test",
        },
    )
    return r.json()["access_token"]


def _make_ledger(client: TestClient, token: str) -> str:
    r = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": "imp-simple", "currency": "TWD"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    return r.json()["entity_id"]


def _iter_sse(resp):
    buf = "".join(chunk for chunk in resp.iter_text())
    events = []
    for block in buf.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        import json as _json
        events.append({"event": event, "data": _json.loads(data) if data else {}})
    return events


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_simple_cache()
    yield
    clear_simple_cache()


# ──────────────────── template download ────────────────────


@pytest.mark.parametrize("entity_type", ["transactions", "categories", "accounts"])
@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_download_template(entity_type, fmt):
    client = _make_client()
    try:
        token = _login(client, f"tpl-{entity_type}-{fmt}@t.com")
        r = client.get(
            f"/api/v1/import/template?entity_type={entity_type}&format={fmt}&lang=zh-TW",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert len(r.content) > 0
        if fmt == "csv":
            assert r.headers["content-type"].startswith("text/csv")
        else:
            assert "spreadsheetml" in r.headers["content-type"]
        assert "attachment" in r.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()


# ──────────────────── categories ────────────────────


def _categories_csv() -> bytes:
    # "早茶" 刻意避開 services/default_categories.py 的預設分類名字("早餐"是
    # 預設分類之一)—— 建帳本時會自動種好預設分類,若這裡撞名,import 會因為
    # create_category 的同名同 kind 查重直接失敗。
    return (
        "名稱,類型,父分類\n"
        "餐飲,支出,\n"
        "早茶,支出,餐飲\n"
        "薪資,收入,\n"
    ).encode("utf-8")


def test_categories_upload_and_execute():
    client = _make_client()
    try:
        token = _login(client, "cat1@t.com")
        ledger_id = _make_ledger(client, token)

        r = client.post(
            "/api/v1/import/categories/upload",
            files={"file": ("cats.csv", _categories_csv(), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["valid_rows"] == 3
        assert summary["errors"] == []
        import_token = summary["import_token"]

        with client.stream(
            "POST", f"/api/v1/import/categories/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, resp.read()
            events = _iter_sse(resp)
        complete = next((e for e in events if e["event"] == "complete"), None)
        assert complete is not None, events
        assert complete["data"]["created_count"] == 3

        db = next(app.dependency_overrides[get_db]())
        try:
            rows = db.scalars(select(UserCategoryProjection)).all()
            names = {row.name for row in rows}
            assert {"餐飲", "早茶", "薪資"} <= names
            leaf = next(row for row in rows if row.name == "早茶")
            assert leaf.parent_name == "餐飲"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_categories_upload_invalid_kind_reports_row_error():
    client = _make_client()
    try:
        token = _login(client, "cat2@t.com")
        ledger_id = _make_ledger(client, token)
        bad_csv = "名稱,類型,父分類\n亂填,不知道,\n".encode("utf-8")

        r = client.post(
            "/api/v1/import/categories/upload",
            files={"file": ("cats.csv", bad_csv, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["valid_rows"] == 0
        assert len(summary["errors"]) == 1
        assert summary["errors"][0]["code"] == "IMPORT_INVALID_KIND"

        # execute 应该被挡下(还有错误)
        re = client.post(
            f"/api/v1/import/categories/{summary['import_token']}/execute",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert re.status_code == 400
        assert re.json()["error_code"] == "IMPORT_HAS_ERRORS"
    finally:
        app.dependency_overrides.clear()


def test_categories_duplicate_name_in_file_reported():
    client = _make_client()
    try:
        token = _login(client, "cat3@t.com")
        ledger_id = _make_ledger(client, token)
        csv_bytes = "名稱,類型,父分類\n餐飲,支出,\n餐飲,支出,\n".encode("utf-8")

        r = client.post(
            "/api/v1/import/categories/upload",
            files={"file": ("cats.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        summary = r.json()
        assert summary["valid_rows"] == 1
        assert summary["errors"][0]["code"] == "IMPORT_DUPLICATE_ROW"
    finally:
        app.dependency_overrides.clear()


def test_categories_missing_parent_auto_created_as_top_level():
    """回歸測試:2026-08-05 使用者實測發現的 bug —— `父分類` 欄位填了一個
    帳本裡不存在、檔案裡也沒有其它列以此名字當一級分類的「分組標籤」時,子
    分類原本會建立成功但因為找不到對應的 level=1 父分類,在分類清單頁完全
    不可見(只有 badge 數字會變,列表永遠顯示「該類型下暫無分類」)。"""
    client = _make_client()
    try:
        token = _login(client, "cat4@t.com")
        ledger_id = _make_ledger(client, token)
        # "戶外" 群組從未以自己的名字獨立成一列;"雜費" 自我参照(常見於
        # 使用者誤填成自己的名字,而不是留白代表無父)。名字刻意避開
        # services/default_categories.py 的預設分類名字(建帳本時已自動種好),
        # 否則會撞 create_category 的同名同 kind 查重。
        csv_bytes = (
            "名稱,類型,父分類\n"
            "民宿,支出,戶外\n"
            "露營,支出,戶外\n"
            "雜費,支出,雜費\n"
        ).encode("utf-8")

        r = client.post(
            "/api/v1/import/categories/upload",
            files={"file": ("cats.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["valid_rows"] == 3
        import_token = summary["import_token"]

        with client.stream(
            "POST", f"/api/v1/import/categories/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, resp.read()
            events = _iter_sse(resp)
        complete = next((e for e in events if e["event"] == "complete"), None)
        assert complete is not None, events
        # 使用者上傳的檔案只有 3 列,即使背後多建了一個自動父分類,回報給使用
        # 者的「已匯入筆數」也應該對齊檔案列數,不應該讓使用者誤以為多匯入了
        # 東西。
        assert complete["data"]["created_count"] == 3

        db = next(app.dependency_overrides[get_db]())
        try:
            rows = db.scalars(select(UserCategoryProjection)).all()
            by_name = {row.name: row for row in rows}
            # 建帳本時已自動種了一批預設分類(見 services/default_categories.py),
            # 所以這裡用子集斷言,不能要求整個帳本剛好只有這 4 筆。
            assert {"戶外", "民宿", "露營", "雜費"} <= set(by_name)

            auto_parent = by_name["戶外"]
            assert auto_parent.level == 1
            assert not auto_parent.parent_name

            for child_name in ("民宿", "露營"):
                child = by_name[child_name]
                assert child.level == 2
                assert child.parent_name == "戶外"

            # 自我参照(名稱等於父分類)視為無父的一級分類,不建立自我挂靠。
            self_ref = by_name["雜費"]
            assert self_ref.level == 1
            assert not self_ref.parent_name
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


# ──────────────────── accounts ────────────────────


def test_accounts_upload_and_execute_with_parent_group():
    client = _make_client()
    try:
        token = _login(client, "acc1@t.com")
        ledger_id = _make_ledger(client, token)
        csv_bytes = (
            "名稱,類型,幣別,期初餘額,主帳戶名稱,信用額度,帳單日,繳款日\n"
            "國泰銀行,主帳戶,TWD,,,,,\n"
            "國泰信用卡,信用卡,TWD,0,國泰銀行,50000,5,20\n"
            "現金,現金,TWD,1000,,,,\n"
        ).encode("utf-8")

        r = client.post(
            "/api/v1/import/accounts/upload",
            files={"file": ("accs.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["valid_rows"] == 3, summary
        import_token = summary["import_token"]

        with client.stream(
            "POST", f"/api/v1/import/accounts/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, resp.read()
            events = _iter_sse(resp)
        complete = next((e for e in events if e["event"] == "complete"), None)
        assert complete is not None, events
        assert complete["data"]["created_count"] == 3

        db = next(app.dependency_overrides[get_db]())
        try:
            rows = db.scalars(select(UserAccountProjection)).all()
            by_name = {row.name: row for row in rows}
            assert by_name["國泰銀行"].account_type == "account_group"
            card = by_name["國泰信用卡"]
            assert card.account_type == "credit_card"
            assert card.credit_limit == 50000
            assert card.billing_day == 5
            assert card.payment_due_day == 20
            assert card.parent_account_id == by_name["國泰銀行"].sync_id
            assert by_name["現金"].initial_balance == 1000
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_accounts_parent_before_child_required():
    """主帳戶名稱指向檔案裡「更晚」出現的一列 → execute 整批中止,不留部分建立。"""
    client = _make_client()
    try:
        token = _login(client, "acc2@t.com")
        ledger_id = _make_ledger(client, token)
        csv_bytes = (
            "名稱,類型,幣別,期初餘額,主帳戶名稱,信用額度,帳單日,繳款日\n"
            "國泰信用卡,信用卡,TWD,0,國泰銀行,50000,5,20\n"
            "國泰銀行,主帳戶,TWD,,,,,\n"
        ).encode("utf-8")

        r = client.post(
            "/api/v1/import/accounts/upload",
            files={"file": ("accs.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        import_token = r.json()["import_token"]

        with client.stream(
            "POST", f"/api/v1/import/accounts/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            events = _iter_sse(resp)
        err = next((e for e in events if e["event"] == "error"), None)
        assert err is not None, events
        assert err["data"]["code"] == "IMPORT_PARENT_ACCOUNT_NOT_FOUND"

        db = next(app.dependency_overrides[get_db]())
        try:
            rows = db.scalars(select(UserAccountProjection)).all()
            assert rows == [], [row.name for row in rows]
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_accounts_invalid_type_reported_and_execute_blocked():
    client = _make_client()
    try:
        token = _login(client, "acc3@t.com")
        ledger_id = _make_ledger(client, token)
        csv_bytes = (
            "名稱,類型,幣別,期初餘額,主帳戶名稱,信用額度,帳單日,繳款日\n"
            "神秘帳戶,外星幣包,TWD,0,,,,\n"
        ).encode("utf-8")

        r = client.post(
            "/api/v1/import/accounts/upload",
            files={"file": ("accs.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        summary = r.json()
        assert summary["valid_rows"] == 0
        assert summary["errors"][0]["code"] == "IMPORT_INVALID_ACCOUNT_TYPE"

        re = client.post(
            f"/api/v1/import/accounts/{summary['import_token']}/execute",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert re.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_accounts_accepts_english_internal_type_key():
    """類型欄也接受內部值(不只是中文顯示標籤),方便熟悉舊格式的使用者。"""
    client = _make_client()
    try:
        token = _login(client, "acc4@t.com")
        ledger_id = _make_ledger(client, token)
        csv_bytes = (
            "名稱,類型,幣別,期初餘額,主帳戶名稱,信用額度,帳單日,繳款日\n"
            "Wallet,cash,USD,10,,,,\n"
        ).encode("utf-8")
        r = client.post(
            "/api/v1/import/accounts/upload",
            files={"file": ("accs.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        summary = r.json()
        assert summary["valid_rows"] == 1
        assert summary["errors"] == []
        assert summary["sample"][0]["type"] == "cash"
    finally:
        app.dependency_overrides.clear()


# ──────────────────── permissions / cancel ────────────────────


def test_accounts_import_owner_only():
    """匯入帳戶等同直接建帳戶(owner-only)—— editor 角色應被擋下。"""
    client = _make_client()
    try:
        owner_tok = _login(client, "owner-imp@t.com")
        ledger_id = _make_ledger(client, owner_tok)

        editor_tok = _login(client, "editor-imp@t.com")
        db = next(app.dependency_overrides[get_db]())
        try:
            editor_user = db.scalar(select(User).where(User.email == "editor-imp@t.com"))
            ledger_row = db.scalar(select(Ledger).where(Ledger.external_id == ledger_id))
            db.add(LedgerMember(ledger_id=ledger_row.id, user_id=editor_user.id, role="editor"))
            db.commit()
        finally:
            db.close()

        csv_bytes = "名稱,類型,幣別,期初餘額,主帳戶名稱,信用額度,帳單日,繳款日\n測試,現金,TWD,0,,,,\n".encode("utf-8")
        r = client.post(
            "/api/v1/import/accounts/upload",
            files={"file": ("accs.csv", csv_bytes, "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {editor_tok}"},
        )
        assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()


def test_cancel_simple_token():
    client = _make_client()
    try:
        token = _login(client, "cancel1@t.com")
        ledger_id = _make_ledger(client, token)
        r = client.post(
            "/api/v1/import/categories/upload",
            files={"file": ("cats.csv", _categories_csv(), "text/csv")},
            data={"target_ledger_id": ledger_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        import_token = r.json()["import_token"]

        rd = client.delete(
            f"/api/v1/import/simple/{import_token}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rd.status_code == 200
        assert rd.json()["cancelled"] is True

        re = client.post(
            f"/api/v1/import/categories/{import_token}/execute",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert re.status_code == 410
    finally:
        app.dependency_overrides.clear()
