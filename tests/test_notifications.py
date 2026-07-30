"""通知中心测试(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0 地基）。

覆盖:
- `services.notifications.create_notification()` 落库
- `GET /notifications` 列表:按 created_at 倒序、分页、category / unread_only 过滤、
  unread_count 不受分页影响
- `POST /notifications/{id}/read` 标记单条已读(幂等、404 处理、跨用户隔离)
- `POST /notifications/read-all` 批量已读
- 用户隔离:用户 A 看不到 / 改不到 B 的通知

============================================================================
给明天的你(或人类)的手动检查清单 —— 如果哪天要在真实环境里过一遍而不是
只信任 pytest,按下面顺序点一遍(本文件里对应的自动化测试已覆盖同样场景,
纯人工 sanity check 用):

1. `make migrate` 后确认 `notifications` 表已建(`sqlite3 beecount.db
   '.schema notifications'`)。
2. 用两个不同账号登录 web(或用 curl 带各自的 access_token),分别调:
   `POST /api/v1/notifications` 目前没有对外暴露的创建端点(设计上通知只由
   server 内部功能产生,§2.1 原文如此)—— 手动验证时改用 sqlite3 直接
   INSERT 一行到 notifications 表(user_id 填对应账号 id, category 填
   'system', title 随意),然后:
   - `GET /api/v1/notifications` 能看到自己那条,看不到另一账号那条
   - `unread_count` 等于未读条数,不受 `limit`/`offset` 影响
   - `GET /api/v1/notifications?category=system` 只回 system 分类
   - `GET /api/v1/notifications?unread_only=true` 排除已读
3. `POST /api/v1/notifications/{id}/read`:
   - 用本人 id → 200，返回体 `read_at` 非空
   - 再点一次 → 200（幂等，`read_at` 不变）
   - 用另一账号的 id → 404（不能标记别人的通知已读，也不能借此探测存在性)
4. `POST /api/v1/notifications/read-all` → 200，`updated` 等于当时未读数；
   之后 `unread_count` 归零。
5. 未来接入 §2.2 recurring / budget 超支判断时,确认调用方是
   `services.notifications.create_notification(db, user_id=..., category=...,
   title=..., body=..., payload={...})`，不 commit（由调用方业务事务一起提交）。
============================================================================
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Notification, User
from src.services.notifications import create_notification


def _make_client() -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), testing_session


def _register(client: TestClient, email: str = "owner@example.com") -> dict:
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
    return res.json()


def _auth_headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['access_token']}"}


# ---------------------------------------------------------------------------
# create_notification() 落库
# ---------------------------------------------------------------------------


def test_create_notification_persists_row() -> None:
    client, Session = _make_client()
    try:
        _register(client)
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "owner@example.com"))
            create_notification(
                db,
                user_id=uid,
                category="system",
                title="Hello",
                body="World",
                payload={"foo": "bar"},
            )
            db.commit()

        with Session() as db:
            rows = db.scalars(select(Notification)).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.category == "system"
            assert row.title == "Hello"
            assert row.body == "World"
            assert row.payload_json == {"foo": "bar"}
            assert row.read_at is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /notifications
# ---------------------------------------------------------------------------


def test_list_notifications_ordering_and_pagination() -> None:
    client, Session = _make_client()
    try:
        user = _register(client)
        headers = _auth_headers(user)
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "owner@example.com"))
            base_time = datetime.now(timezone.utc)
            for i in range(5):
                db.add(
                    Notification(
                        user_id=uid,
                        category="system",
                        title=f"n{i}",
                        body=None,
                        payload_json=None,
                        created_at=base_time + timedelta(seconds=i),
                    )
                )
            db.commit()

        res = client.get("/api/v1/notifications", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 5
        assert body["unread_count"] == 5
        # created_at 倒序 → 最新的 n4 在最前面
        assert [it["title"] for it in body["items"]] == ["n4", "n3", "n2", "n1", "n0"]

        res2 = client.get("/api/v1/notifications?limit=2&offset=2", headers=headers)
        body2 = res2.json()
        assert body2["total"] == 5
        # 分页不影响 unread_count(全局未读数)
        assert body2["unread_count"] == 5
        assert [it["title"] for it in body2["items"]] == ["n2", "n1"]

        # created_at 必须带 UTC tz 标记
        for item in body["items"]:
            assert "+00:00" in item["created_at"] or item["created_at"].endswith("Z")
    finally:
        app.dependency_overrides.clear()


def test_list_notifications_category_and_unread_filters() -> None:
    client, Session = _make_client()
    try:
        user = _register(client)
        headers = _auth_headers(user)
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "owner@example.com"))
            db.add(Notification(user_id=uid, category="system", title="sys1", read_at=None))
            db.add(
                Notification(
                    user_id=uid,
                    category="budget_alert",
                    title="budget1",
                    read_at=datetime.now(timezone.utc),
                )
            )
            db.add(Notification(user_id=uid, category="budget_alert", title="budget2", read_at=None))
            db.commit()

        res = client.get("/api/v1/notifications?category=budget_alert", headers=headers)
        items = res.json()["items"]
        assert len(items) == 2
        assert all(it["category"] == "budget_alert" for it in items)

        res2 = client.get("/api/v1/notifications?unread_only=true", headers=headers)
        body2 = res2.json()
        assert body2["total"] == 2
        assert all(it["read_at"] is None for it in body2["items"])
    finally:
        app.dependency_overrides.clear()


def test_list_notifications_user_isolation() -> None:
    client, Session = _make_client()
    try:
        user_a = _register(client, email="alice@example.com")
        user_b = _register(client, email="bob@example.com")
        with Session() as db:
            uid_a = db.scalar(select(User.id).where(User.email == "alice@example.com"))
            db.add(Notification(user_id=uid_a, category="system", title="alice secret"))
            db.commit()

        res = client.get("/api/v1/notifications", headers=_auth_headers(user_b))
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 0
        assert body["unread_count"] == 0

        res2 = client.get("/api/v1/notifications", headers=_auth_headers(user_a))
        assert res2.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /notifications/{id}/read
# ---------------------------------------------------------------------------


def test_mark_notification_read_is_idempotent() -> None:
    client, Session = _make_client()
    try:
        user = _register(client)
        headers = _auth_headers(user)
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "owner@example.com"))
            n = Notification(user_id=uid, category="system", title="n0")
            db.add(n)
            db.commit()
            notif_id = n.id

        res = client.post(f"/api/v1/notifications/{notif_id}/read", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["read_at"] is not None
        first_read_at = body["read_at"]

        # 再点一次:幂等,不报错,read_at 不再变化
        res2 = client.post(f"/api/v1/notifications/{notif_id}/read", headers=headers)
        assert res2.status_code == 200
        assert res2.json()["read_at"] == first_read_at
    finally:
        app.dependency_overrides.clear()


def test_mark_notification_read_missing_returns_404() -> None:
    client, _Session = _make_client()
    try:
        user = _register(client)
        res = client.post("/api/v1/notifications/999999/read", headers=_auth_headers(user))
        assert res.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_mark_notification_read_cross_user_returns_404() -> None:
    """用户 B 不能标记用户 A 的通知已读 —— 404 而非 403,不暴露该 id 是否存在。"""
    client, Session = _make_client()
    try:
        _register(client, email="alice@example.com")
        user_b = _register(client, email="bob@example.com")
        with Session() as db:
            uid_a = db.scalar(select(User.id).where(User.email == "alice@example.com"))
            n = Notification(user_id=uid_a, category="system", title="alice's")
            db.add(n)
            db.commit()
            notif_id = n.id

        res = client.post(f"/api/v1/notifications/{notif_id}/read", headers=_auth_headers(user_b))
        assert res.status_code == 404

        with Session() as db:
            row = db.scalar(select(Notification).where(Notification.id == notif_id))
            assert row.read_at is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /notifications/read-all
# ---------------------------------------------------------------------------


def test_mark_all_notifications_read() -> None:
    client, Session = _make_client()
    try:
        user = _register(client)
        headers = _auth_headers(user)
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "owner@example.com"))
            for i in range(3):
                db.add(Notification(user_id=uid, category="system", title=f"n{i}"))
            # 已经是已读的那条不应计入 updated 数
            db.add(
                Notification(
                    user_id=uid,
                    category="system",
                    title="already-read",
                    read_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

        res = client.post("/api/v1/notifications/read-all", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["updated"] == 3

        res2 = client.get("/api/v1/notifications", headers=headers)
        assert res2.json()["unread_count"] == 0
    finally:
        app.dependency_overrides.clear()
