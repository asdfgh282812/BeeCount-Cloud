"""管理者系統公告(`routers/admin_broadcasts.py`)契約測試。

覆蓋:
1. 非 admin 一律 403。
2. 發送:對所有啟用中使用者各寫一筆 system 通知(停用帳號不收),
   priority=3 排在欠款提醒之前,使用者從 `GET /notifications` 拿得到。
3. 列表:已讀數統計、建立者 email。
4. 撤回:刪掉所有人名下那一筆、紀錄保留並標 retracted_at、重複撤回冪等、
   不存在的 id 404;其它通知不受影響。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Notification, User
from src.services.notifications import create_notification

_TEST_SESSION: sessionmaker | None = None


@pytest.fixture()
def client() -> TestClient:
    global _TEST_SESSION
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _TEST_SESSION = TS

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> str:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _login(client: TestClient, email: str) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _admin_token(client: TestClient, email: str = "admin@t.com") -> str:
    _register(client, email)
    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_admin = True
        db.commit()
    return _login(client, email)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _disable(email: str) -> None:
    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_enabled = False
        db.commit()


def test_non_admin_forbidden(client: TestClient) -> None:
    token = _register(client, "u1@t.com")
    assert client.get("/api/v1/admin/broadcasts", headers=_h(token)).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/broadcasts", headers=_h(token), json={"title": "hi"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/admin/broadcasts/recipient-count", headers=_h(token)).status_code
        == 403
    )


def test_create_broadcast_fans_out_to_enabled_users(client: TestClient) -> None:
    admin = _admin_token(client)
    u1 = _register(client, "u1@t.com")
    _register(client, "u2@t.com")
    _register(client, "off@t.com")
    _disable("off@t.com")

    count = client.get("/api/v1/admin/broadcasts/recipient-count", headers=_h(admin))
    assert count.status_code == 200
    assert count.json()["count"] == 3  # admin + u1 + u2

    res = client.post(
        "/api/v1/admin/broadcasts",
        headers=_h(admin),
        json={"title": "  系統維護公告  ", "body": "今晚 23:00 維護 30 分鐘"},
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["title"] == "系統維護公告"
    assert out["recipient_count"] == 3
    assert out["read_count"] == 0
    assert out["created_by_email"] == "admin@t.com"

    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        rows = db.scalars(
            select(Notification).where(Notification.broadcast_id == out["id"])
        ).all()
        emails = {db.get(User, r.user_id).email for r in rows}
        assert emails == {"admin@t.com", "u1@t.com", "u2@t.com"}
        assert all(r.category == "system" and r.priority == 3 for r in rows)

    listed = client.get("/api/v1/notifications", headers=_h(u1)).json()
    assert listed["unread_count"] == 1
    assert listed["items"][0]["title"] == "系統維護公告"
    assert listed["items"][0]["body"] == "今晚 23:00 維護 30 分鐘"


def test_broadcast_sorts_before_debt_reminders(client: TestClient) -> None:
    admin = _admin_token(client)
    u1 = _register(client, "u1@t.com")
    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        uid = db.query(User).filter(User.email == "u1@t.com").one().id
        # priority=2 但 payload 不帶 debtId,_effective_priority 會維持 2
        for i in range(3):
            create_notification(db, user_id=uid, category="card_due", title=f"card{i}", priority=2)
        db.commit()

    client.post("/api/v1/admin/broadcasts", headers=_h(admin), json={"title": "公告"})
    items = client.get("/api/v1/notifications?limit=1", headers=_h(u1)).json()["items"]
    assert items[0]["title"] == "公告"


def test_list_includes_read_count(client: TestClient) -> None:
    admin = _admin_token(client)
    u1 = _register(client, "u1@t.com")
    _register(client, "u2@t.com")
    bid = client.post(
        "/api/v1/admin/broadcasts", headers=_h(admin), json={"title": "公告"}
    ).json()["id"]

    notif_id = client.get("/api/v1/notifications", headers=_h(u1)).json()["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{notif_id}/read", headers=_h(u1)).status_code == 200

    listed = client.get("/api/v1/admin/broadcasts", headers=_h(admin)).json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["id"] == bid
    assert item["read_count"] == 1
    assert item["recipient_count"] == 3


def test_retract_removes_notifications_and_keeps_record(client: TestClient) -> None:
    admin = _admin_token(client)
    u1 = _register(client, "u1@t.com")
    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        uid = db.query(User).filter(User.email == "u1@t.com").one().id
        create_notification(db, user_id=uid, category="reminder", title="其它通知")
        db.commit()

    bid = client.post(
        "/api/v1/admin/broadcasts", headers=_h(admin), json={"title": "發錯了"}
    ).json()["id"]
    assert client.get("/api/v1/notifications", headers=_h(u1)).json()["total"] == 2

    res = client.post(f"/api/v1/admin/broadcasts/{bid}/retract", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["retracted_at"] is not None
    assert res.json()["read_count"] == 0

    listed = client.get("/api/v1/notifications", headers=_h(u1)).json()
    assert [i["title"] for i in listed["items"]] == ["其它通知"]

    with _TEST_SESSION() as db:
        assert db.scalars(
            select(Notification).where(Notification.broadcast_id == bid)
        ).all() == []

    again = client.post(f"/api/v1/admin/broadcasts/{bid}/retract", headers=_h(admin))
    assert again.status_code == 200
    assert again.json()["retracted_at"] == res.json()["retracted_at"]

    history = client.get("/api/v1/admin/broadcasts", headers=_h(admin)).json()
    assert history["items"][0]["retracted_at"] is not None


def test_retract_unknown_404(client: TestClient) -> None:
    admin = _admin_token(client)
    res = client.post("/api/v1/admin/broadcasts/nope/retract", headers=_h(admin))
    assert res.status_code == 404


def test_create_requires_title(client: TestClient) -> None:
    admin = _admin_token(client)
    assert (
        client.post("/api/v1/admin/broadcasts", headers=_h(admin), json={"title": "   "}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/admin/broadcasts", headers=_h(admin), json={"title": ""}).status_code
        == 422
    )
