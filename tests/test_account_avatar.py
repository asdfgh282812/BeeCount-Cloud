"""帳戶頭像(2026-08-02 補強)—— 使用者反饋光靠 bank_name 文字看不出是哪張卡,
加一張自訂圖片。跟 category icon 同一套模式:user-global(跨帳本共享),走
`AttachmentFile` 共用池 + sha256 去重,`avatar_cloud_file_id` 是唯一權威值。

覆盖:
- `POST /attachments/account-avatars/upload`:上傳成功、同一用户重复上传同图
  走 dedup(不产生第二个 AttachmentFile 行)、超过大小上限拒绝。
- web create/update 帳戶帶 `avatar_cloud_file_id`/`avatar_cloud_sha256` →
  落投影 + 读端点可见。
- **merge 契约(CLAUDE.md 硬门槛)**:partial update 不带 avatar 键时保留旧值。
- 换头像 / 清空头像时旧 blob 走 GC(不是本文件重点,已在
  test_attachment_gc.py 覆盖,这里只测 API 层面的可达性)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import AttachmentFile, User, UserAccountProjection


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


def test_upload_account_avatar_succeeds_and_dedups():
    client, _TS = _make_client()
    try:
        tok = _login(client, "avatar1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        r1 = client.post(
            "/api/v1/attachments/account-avatars/upload",
            headers=hdr,
            files={"file": ("card.png", b"fake-image-bytes", "image/png")},
        )
        assert r1.status_code == 200, r1.text
        file_id_1 = r1.json()["file_id"]
        assert file_id_1
        assert r1.json()["ledger_id"] == ""

        # 同一用户再传一次同样的字节 —— dedup,回同一个 file_id,不产生第二行。
        r2 = client.post(
            "/api/v1/attachments/account-avatars/upload",
            headers=hdr,
            files={"file": ("card-again.png", b"fake-image-bytes", "image/png")},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["file_id"] == file_id_1

        with _TS() as db:
            count = db.scalar(
                select(AttachmentFile).where(AttachmentFile.attachment_kind == "account_avatar")
            )
            all_rows = db.scalars(
                select(AttachmentFile).where(AttachmentFile.attachment_kind == "account_avatar")
            ).all()
            assert len(all_rows) == 1
            assert all_rows[0].ledger_id is None
    finally:
        app.dependency_overrides.clear()


def test_upload_account_avatar_rejects_empty_file():
    client, _TS = _make_client()
    try:
        tok = _login(client, "avatar2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        r = client.post(
            "/api/v1/attachments/account-avatars/upload",
            headers=hdr,
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_web_create_account_with_avatar_roundtrips_via_read():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "avataracc1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "avataracc1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lga1", "ledger", "lga1",
              {"syncId": "lga1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        up = client.post(
            "/api/v1/attachments/account-avatars/upload",
            headers=hdr_web,
            files={"file": ("card.png", b"card-bytes", "image/png")},
        )
        assert up.status_code == 200, up.text
        file_id = up.json()["file_id"]
        sha256 = up.json()["sha256"]

        r = client.post(
            "/api/v1/write/ledgers/lga1/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0, "name": "信用卡",
                "avatar_cloud_file_id": file_id, "avatar_cloud_sha256": sha256,
            },
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]

        row = _account_row(TS, "avataracc1@t.com", account_id)
        assert row.avatar_cloud_file_id == file_id
        assert row.avatar_cloud_sha256 == sha256

        r2 = client.get("/api/v1/read/ledgers/lga1/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        acc = next(x for x in r2.json() if x["id"] == account_id)
        assert acc["avatar_cloud_file_id"] == file_id
        assert acc["avatar_cloud_sha256"] == sha256

        r3 = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r3.status_code == 200, r3.text
        acc3 = next(x for x in r3.json() if x["id"] == account_id)
        assert acc3["avatar_cloud_file_id"] == file_id
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_account_partial_update_keeps_avatar():
    """**merge 契约(CLAUDE.md L74-80 硬门槛)**:先 push 一条带 avatar 的账户,
    再 push 一条只改 name、不带 avatar 键的 partial update —— avatar 必须保留,
    不能被静默冲成 null。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "avatarmerge1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "旧卡", "type": "credit_card", "currency": "CNY",
               "avatarCloudFileId": "file-abc", "avatarCloudSha256": "sha-abc"})
        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "旧卡改名"})

        row = _account_row(TS, "avatarmerge1@t.com", "acc-1")
        assert row.name == "旧卡改名"
        assert row.avatar_cloud_file_id == "file-abc", "partial update 不带 avatar 键时不能冲掉已有头像"
        assert row.avatar_cloud_sha256 == "sha-abc"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_account_partial_update_can_explicitly_clear_avatar():
    """反面情形:partial update **显式**带 avatarCloudFileId="" (用户主动移除头像)
    时,必须正常清空 —— 跟"缺键保留"的契约不冲突。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "avatarmerge2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "旧卡", "type": "credit_card", "currency": "CNY",
               "avatarCloudFileId": "file-xyz", "avatarCloudSha256": "sha-xyz"})
        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "旧卡", "avatarCloudFileId": "", "avatarCloudSha256": ""})

        row = _account_row(TS, "avatarmerge2@t.com", "acc-2")
        assert row.avatar_cloud_file_id is None
        assert row.avatar_cloud_sha256 is None
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_avatar_omitted_keeps_existing():
    """web PATCH 只改 note、不带 avatar 键 → avatar 保持不变。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "avatarw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "avatarw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw1", "ledger", "lgw1",
              {"syncId": "lgw1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw1", "account", "acc-w1",
              {"syncId": "acc-w1", "name": "旧卡", "type": "credit_card", "currency": "CNY",
               "avatarCloudFileId": "file-keep", "avatarCloudSha256": "sha-keep"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw1/accounts/acc-w1",
            headers=hdr_web,
            json={"base_change_id": 0, "note": "备注"},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "avatarw1@t.com", "acc-w1")
        assert row.avatar_cloud_file_id == "file-keep", "web update 不带 avatar 键时不能冲掉已有头像"
        assert row.note == "备注"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_can_remove_avatar():
    """web PATCH 显式带 avatar_cloud_file_id="" (用户点「移除头像」)→ 正常清空。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "avatarw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "avatarw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw2", "ledger", "lgw2",
              {"syncId": "lgw2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw2", "account", "acc-w2",
              {"syncId": "acc-w2", "name": "旧卡", "type": "credit_card", "currency": "CNY",
               "avatarCloudFileId": "file-remove", "avatarCloudSha256": "sha-remove"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-w2",
            headers=hdr_web,
            json={"base_change_id": 0, "avatar_cloud_file_id": "", "avatar_cloud_sha256": ""},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "avatarw2@t.com", "acc-w2")
        assert row.avatar_cloud_file_id is None
        assert row.avatar_cloud_sha256 is None
    finally:
        app.dependency_overrides.clear()
