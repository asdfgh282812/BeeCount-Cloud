"""分類 / 帳戶匯入 + 範本下載(2026-08 新增)。

跟 `endpoints.py`(交易匯入)的差異:範本格式固定,不需要欄位對應(field
mapping)這層 UI/邏輯,upload 直接回傳可執行的 preview(有效筆數 + 逐行
錯誤),使用者確認無誤後呼叫 execute 即可 —— 少了一個 preview 步驟。

分類沒有 parent 存在性校驗(`snapshot_mutator.create_category` 的
`parent_name` 只是存字串,不像帳戶的 `parent_account_id` 會走
`_assert_valid_account_parent` 檢查),所以分類匯入不用管檔案內列的順序;
帳戶則需要「主帳戶(群組)」那一列先出現、子帳戶才能引用到它的 syncId,
在 `_do_execute_accounts` 裡逐行累積 name→syncId 表來解決。

`_do_execute_categories` 會自動幫「檔案裡引用到、但帳本裡不存在」的
`父分類` 名字先建一個 level=1 分類(比照 `endpoints.py::
_collect_new_categories` 交易匯入同款做法)—— 分類清單頁(`CategoriesPanel.
tsx`)只把「無父」的分類當作可以展開子類的父卡片,`parent_name` 若沒有對應
的實體 level=1 分類存在,子分類會建立成功但在清單頁完全不可見(2026-08-05
使用者實測發現的 bug:匯入回報成功,但分類頁只看到數字、看不到任何分類)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ... import snapshot_builder
from ...concurrency import lock_ledger_for_materialize
from ...database import get_db
from ...deps import get_current_user
from ...ledger_access import get_accessible_ledger_by_external_id
from ...models import AuditLog, Ledger, User
from ...services.import_data.schema import ImportAccount, ImportCategory
from ...services.import_data.schema import ImportError as ImpError
from ...services.import_data.simple_cache import (
    cancel_simple_token as _cancel_simple_token,
    consume_simple_token,
    get_simple_token,
    save_simple_token,
)
from ...services.import_data.simple_parser import parse_accounts_file, parse_categories_file
from ...services.import_data.templates import build_template
from ...snapshot_mutator import create_account, create_category
from ..write._shared import _OWNER_ONLY_ROLES, _WRITE_SCOPE_DEP, _emit_entity_diffs, _payload_with_actor

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_ROW_COUNT = 50_000


# ──────────── schemas ────────────


class SimpleImportRowError(BaseModel):
    code: str
    row_number: int
    message: str
    field_name: str | None = None


class SimpleImportSummary(BaseModel):
    import_token: str
    entity_type: str
    target_ledger_id: str
    total_rows: int
    valid_rows: int
    errors: list[SimpleImportRowError]
    sample: list[dict]


def _to_row_errors(errors: list[ImpError]) -> list[SimpleImportRowError]:
    return [
        SimpleImportRowError(code=e.code, row_number=e.row_number, message=e.message, field_name=e.field_name)
        for e in errors
    ]


# ──────────── helpers ────────────


class _SimpleImportFailed(Exception):
    def __init__(self, *, code: str, row_number: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.row_number = row_number
        self.message = message


def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _resolve_owner_ledger(db: Session, user: User, ledger_external_id: str) -> Ledger:
    """分類/帳戶匯入等同直接呼叫 `POST .../categories`/`accounts`,兩者都是
    owner-only(`src/routers/write/categories.py`/`accounts.py`),匯入端點
    沿用同一個角色限制。"""
    row = get_accessible_ledger_by_external_id(
        db, user_id=user.id, ledger_external_id=ledger_external_id, roles=_OWNER_ONLY_ROLES,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ledger not found")
    ledger, _role = row
    return ledger


async def _read_upload(file: UploadFile) -> bytes:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "IMPORT_EMPTY_FILE"})
    if len(payload) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "IMPORT_FILE_TOO_LARGE", "limit_bytes": _MAX_FILE_BYTES},
        )
    return payload


def _user_stub(user_id: str):
    class _Stub:
        def __init__(self, uid: str) -> None:
            self.id = uid
            self.email = ""
            self.is_admin = False

    return _Stub(user_id)


def _deep_copy_snapshot(snapshot: dict) -> dict:
    out = {**snapshot}
    for key in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(key)
        if isinstance(arr, list):
            out[key] = [dict(e) if isinstance(e, dict) else e for e in arr]
    return out


# ──────────── categories ────────────


@router.post("/categories/upload", response_model=SimpleImportSummary)
async def upload_categories_import(
    request: Request,
    file: UploadFile = File(...),
    target_ledger_id: str = Form(...),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimpleImportSummary:
    payload = await _read_upload(file)
    _resolve_owner_ledger(db, current_user, target_ledger_id)

    rows, errors = parse_categories_file(payload, file.filename or "")
    if len(rows) + len(errors) > _MAX_ROW_COUNT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "IMPORT_TOO_MANY_ROWS", "limit_rows": _MAX_ROW_COUNT},
        )

    token = save_simple_token(
        user_id=current_user.id, entity_type="categories", rows=rows, errors=errors,
        target_ledger_id=target_ledger_id,
    )
    logger.info(
        "import.simple.upload user=%s entity=categories token=%s rows=%d errors=%d",
        current_user.id, token, len(rows), len(errors),
    )
    return SimpleImportSummary(
        import_token=token, entity_type="categories", target_ledger_id=target_ledger_id,
        total_rows=len(rows) + len(errors), valid_rows=len(rows),
        errors=_to_row_errors(errors),
        sample=[{"name": c.name, "kind": c.kind, "parent_name": c.parent_name} for c in rows[:10]],
    )


@router.post("/categories/{token}/execute")
async def execute_categories_import(
    token: str,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_simple_token(token=token, user_id=current_user.id)
    if entry is None or entry.entity_type != "categories":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail={"error_code": "IMPORT_TOKEN_EXPIRED"})
    if entry.errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "IMPORT_HAS_ERRORS"})
    ledger = _resolve_owner_ledger(db, current_user, entry.target_ledger_id)
    rows: list[ImportCategory] = entry.rows
    user_id = current_user.id

    async def stream():
        try:
            async for evt in _do_execute_categories(db=db, user_id=user_id, ledger=ledger, rows=rows):
                yield evt
            consume_simple_token(token=token, user_id=user_id)
            try:
                await request.app.state.ws_manager.broadcast_to_user(
                    ledger.user_id,
                    {
                        "type": "sync_change", "ledgerId": ledger.external_id,
                        "serverTimestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("import.simple.broadcast failed: %s", exc)
        except _SimpleImportFailed as exc:
            yield _sse_event("error", {"code": exc.code, "row_number": exc.row_number, "message": exc.message})

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _do_execute_categories(*, db: Session, user_id: str, ledger: Ledger, rows: list[ImportCategory]):
    lock_ledger_for_materialize(db, ledger.id)
    snapshot = snapshot_builder.build(db, ledger)
    prev = _deep_copy_snapshot(snapshot)
    actor_base = _payload_with_actor({}, _user_stub(user_id))

    # `parent_name` 只是存字串,分類清單頁只把「level=1(無父)」的分類當作可以
    # 挂载子类的父卡片 —— 如果 `父分類` 填了一個在此帳本裡不存在的名字(常見於
    # 使用者用「分組標籤」而不是「已存在的一級分類名稱」填這欄),對應的子分類
    # 會建立成功但永遠不會出現在分類清單裡(孤兒子分類,UI 沒有任何錯誤提示)。
    # 比照交易匯入 `endpoints.py::_collect_new_categories` 同款做法:父分類
    # 若尚不存在(帳本裡沒有,檔案裡也沒有其它列以此名字當一級分類),自動先
    # 建立成一級分類。同名自我参照(名稱等於父分類,使用者多半是想留白卻誤填
    # 了自己的名字)視為無父的一級分類,不建立自我挂靠的父列。
    existing_top_level: set[tuple[str, str]] = {
        (str(c.get("name") or "").strip().lower(), str(c.get("kind") or "").strip())
        for c in (snapshot.get("categories") or [])
        if isinstance(c, dict)
    }
    # 檔案裡自己就沒填 `父分類`、或自我参照(視為無父)的列,本身就會建立成
    # level=1 —— 這些名字也算「已存在」,其它列引用到它們當父分類時不用再
    # 自動多建一個。
    for row in rows:
        parent_of_row = (row.parent_name or "").strip()
        if not parent_of_row or parent_of_row.lower() == row.name.strip().lower():
            existing_top_level.add((row.name.strip().lower(), row.kind))

    auto_parents: list[tuple[str, str]] = []
    for row in rows:
        parent = (row.parent_name or "").strip()
        if not parent or parent.lower() == row.name.strip().lower():
            continue
        key = (parent.lower(), row.kind)
        if key not in existing_top_level:
            auto_parents.append((parent, row.kind))
            existing_top_level.add(key)

    try:
        total = len(rows)
        created_count = 0
        for name, kind in auto_parents:
            try:
                snapshot, _ = create_category(
                    snapshot, {**actor_base, "name": name, "kind": kind, "level": 1, "parent_name": None},
                )
            except (KeyError, ValueError, PermissionError) as exc:
                raise _SimpleImportFailed(
                    code="WRITE_CATEGORY_FAILED", row_number=0,
                    message=f"建立父分類「{name}」失敗:{exc}",
                )
            created_count += 1

        for i, row in enumerate(rows, 1):
            parent_name = row.parent_name
            if parent_name and parent_name.strip().lower() == row.name.strip().lower():
                parent_name = None
            try:
                snapshot, _ = create_category(
                    snapshot,
                    {
                        **actor_base, "name": row.name, "kind": row.kind,
                        "level": 2 if parent_name else 1, "parent_name": parent_name,
                    },
                )
            except (KeyError, ValueError, PermissionError) as exc:
                raise _SimpleImportFailed(
                    code="WRITE_CATEGORY_FAILED", row_number=row.source_row_number,
                    message=f"建立分類「{row.name}」失敗:{exc}",
                )
            created_count += 1
            if i % 20 == 0 or i == total:
                yield _sse_event("stage", {"stage": "categories", "done": i, "total": total})
                await asyncio.sleep(0)

        now = datetime.now(timezone.utc)
        emitted = _emit_entity_diffs(
            db, ledger=ledger, current_user=_user_stub(user_id), device_id="web-import",
            prev=prev, next_snapshot=snapshot, now=now,
        )
        new_change_id = max(emitted) if emitted else snapshot_builder.latest_change_id(db, ledger.id)

        db.add(AuditLog(
            user_id=user_id, ledger_id=ledger.id, action="web_import_categories",
            metadata_json={
                "ledgerId": ledger.external_id, "newChangeId": new_change_id, "createdCount": created_count,
            },
        ))
        db.commit()
        yield _sse_event("complete", {"created_count": total, "new_change_id": new_change_id})
        logger.info(
            "import.simple.execute.complete user=%s ledger=%s entity=categories created=%d "
            "auto_parents=%d change_id=%d",
            user_id, ledger.external_id, total, len(auto_parents), new_change_id,
        )
    except _SimpleImportFailed:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("import.simple.execute.unknown_error user=%s ledger=%s", user_id, ledger.external_id)
        raise _SimpleImportFailed(code="WRITE_UNKNOWN", row_number=0, message=str(exc))


# ──────────── accounts ────────────


@router.post("/accounts/upload", response_model=SimpleImportSummary)
async def upload_accounts_import(
    request: Request,
    file: UploadFile = File(...),
    target_ledger_id: str = Form(...),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimpleImportSummary:
    payload = await _read_upload(file)
    _resolve_owner_ledger(db, current_user, target_ledger_id)

    rows, errors = parse_accounts_file(payload, file.filename or "")
    if len(rows) + len(errors) > _MAX_ROW_COUNT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "IMPORT_TOO_MANY_ROWS", "limit_rows": _MAX_ROW_COUNT},
        )

    token = save_simple_token(
        user_id=current_user.id, entity_type="accounts", rows=rows, errors=errors,
        target_ledger_id=target_ledger_id,
    )
    logger.info(
        "import.simple.upload user=%s entity=accounts token=%s rows=%d errors=%d",
        current_user.id, token, len(rows), len(errors),
    )
    return SimpleImportSummary(
        import_token=token, entity_type="accounts", target_ledger_id=target_ledger_id,
        total_rows=len(rows) + len(errors), valid_rows=len(rows),
        errors=_to_row_errors(errors),
        sample=[
            {
                "name": a.name, "type": a.type, "currency": a.currency,
                "initial_balance": a.initial_balance, "parent_account_name": a.parent_account_name,
            }
            for a in rows[:10]
        ],
    )


@router.post("/accounts/{token}/execute")
async def execute_accounts_import(
    token: str,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_simple_token(token=token, user_id=current_user.id)
    if entry is None or entry.entity_type != "accounts":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail={"error_code": "IMPORT_TOKEN_EXPIRED"})
    if entry.errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "IMPORT_HAS_ERRORS"})
    ledger = _resolve_owner_ledger(db, current_user, entry.target_ledger_id)
    rows: list[ImportAccount] = entry.rows
    user_id = current_user.id

    async def stream():
        try:
            async for evt in _do_execute_accounts(db=db, user_id=user_id, ledger=ledger, rows=rows):
                yield evt
            consume_simple_token(token=token, user_id=user_id)
            try:
                await request.app.state.ws_manager.broadcast_to_user(
                    ledger.user_id,
                    {
                        "type": "sync_change", "ledgerId": ledger.external_id,
                        "serverTimestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("import.simple.broadcast failed: %s", exc)
        except _SimpleImportFailed as exc:
            yield _sse_event("error", {"code": exc.code, "row_number": exc.row_number, "message": exc.message})

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _do_execute_accounts(*, db: Session, user_id: str, ledger: Ledger, rows: list[ImportAccount]):
    lock_ledger_for_materialize(db, ledger.id)
    snapshot = snapshot_builder.build(db, ledger)
    prev = _deep_copy_snapshot(snapshot)
    actor_base = _payload_with_actor({}, _user_stub(user_id))

    # 主帳戶(群組)可能是這份檔案裡更早的一列,也可能是帳本裡已存在的帳戶 ——
    # 用 name(小寫)→syncId 表統一解析,邊建立邊往裡面加新帳戶。
    name_to_id: dict[str, str] = {
        str(a.get("name") or "").strip().lower(): str(a.get("syncId"))
        for a in (snapshot.get("accounts") or [])
        if isinstance(a, dict) and a.get("name")
    }

    try:
        total = len(rows)
        for i, row in enumerate(rows, 1):
            payload: dict[str, object] = {
                **actor_base,
                "name": row.name,
                "account_type": row.type,
                "currency": row.currency,
                "initial_balance": row.initial_balance if row.initial_balance is not None else 0.0,
            }
            if row.credit_limit is not None:
                payload["credit_limit"] = row.credit_limit
            if row.billing_day is not None:
                payload["billing_day"] = row.billing_day
            if row.payment_due_day is not None:
                payload["payment_due_day"] = row.payment_due_day
            if row.parent_account_name:
                parent_id = name_to_id.get(row.parent_account_name.strip().lower())
                if not parent_id:
                    raise _SimpleImportFailed(
                        code="IMPORT_PARENT_ACCOUNT_NOT_FOUND", row_number=row.source_row_number,
                        message=(
                            f"帳戶「{row.name}」指定的主帳戶「{row.parent_account_name}」找不到,"
                            "請確認主帳戶名稱正確、且在檔案中排在更前面(或帳本裡已存在)"
                        ),
                    )
                payload["parent_account_id"] = parent_id

            try:
                snapshot, new_id = create_account(snapshot, payload)
            except (KeyError, ValueError, PermissionError) as exc:
                raise _SimpleImportFailed(
                    code="WRITE_ACCOUNT_FAILED", row_number=row.source_row_number,
                    message=f"建立帳戶「{row.name}」失敗:{exc}",
                )
            name_to_id[row.name.strip().lower()] = new_id

            if i % 20 == 0 or i == total:
                yield _sse_event("stage", {"stage": "accounts", "done": i, "total": total})
                await asyncio.sleep(0)

        now = datetime.now(timezone.utc)
        emitted = _emit_entity_diffs(
            db, ledger=ledger, current_user=_user_stub(user_id), device_id="web-import",
            prev=prev, next_snapshot=snapshot, now=now,
        )
        new_change_id = max(emitted) if emitted else snapshot_builder.latest_change_id(db, ledger.id)

        db.add(AuditLog(
            user_id=user_id, ledger_id=ledger.id, action="web_import_accounts",
            metadata_json={"ledgerId": ledger.external_id, "newChangeId": new_change_id, "createdCount": total},
        ))
        db.commit()
        yield _sse_event("complete", {"created_count": total, "new_change_id": new_change_id})
        logger.info(
            "import.simple.execute.complete user=%s ledger=%s entity=accounts created=%d change_id=%d",
            user_id, ledger.external_id, total, new_change_id,
        )
    except _SimpleImportFailed:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("import.simple.execute.unknown_error user=%s ledger=%s", user_id, ledger.external_id)
        raise _SimpleImportFailed(code="WRITE_UNKNOWN", row_number=0, message=str(exc))


# ──────────── cancel(分類/帳戶各自 token) ────────────


@router.delete("/simple/{token}")
async def cancel_simple_import(
    token: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
):
    ok = _cancel_simple_token(token=token, user_id=current_user.id)
    return {"cancelled": ok}


# ──────────── template ────────────


@router.get("/template")
async def download_import_template(
    entity_type: Literal["transactions", "categories", "accounts"] = Query(...),
    format: Literal["csv", "xlsx"] = Query(default="csv"),  # noqa: A002
    lang: str | None = Query(default=None),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
):
    body, filename = build_template(entity_type=entity_type, fmt=format, lang=lang)
    media_type = (
        "text/csv"
        if format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )
