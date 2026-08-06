"""背景排程管理後台(§ 排程管理 Phase 5)。

挂载在 `/api/v1/admin/scheduled-jobs`。所有路由要求 admin scope,比照
`admin.py` 裡 `/logs` 端點的保護方式疊 `require_admin_user` +
`require_scopes(SCOPE_OPS_WRITE)`——這是運維層級操作,不是使用者自己擁有
的資源(跟 `admin_backup.py` 的 schedules 不同,那邊是 per-user 的備份計畫)。

設計跟 `admin_backup.py` 同款 boilerplate(依賴、audit log)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user, require_scopes
from ..models import AuditLog, ScheduledJobConfig, User
from ..schemas import (
    ScheduledJobConfigOut,
    ScheduledJobConfigUpdateRequest,
    ScheduledJobRunNowOut,
)
from ..security import SCOPE_OPS_WRITE
from ..services import scheduled_jobs

router = APIRouter()

_SCOPE_DEP = require_scopes(SCOPE_OPS_WRITE)


def _utc(dt: datetime | None) -> datetime | None:
    """跟 `admin_backup.py::_utc` 同款处理:SQLite 读出来的 naive datetime
    统一标 UTC,前端 toLocaleString 才能正确按用户时区转换。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _audit(db: Session, *, user_id: str, action: str, metadata: dict[str, Any]) -> None:
    db.add(AuditLog(user_id=user_id, ledger_id=None, action=action, metadata_json=metadata))


def _build_out(config: ScheduledJobConfig) -> ScheduledJobConfigOut:
    return ScheduledJobConfigOut(
        job_key=config.job_key,
        interval_seconds=config.interval_seconds,
        enabled=config.enabled,
        next_run_at=_utc(config.next_run_at),
        last_run_at=_utc(config.last_run_at),
        last_run_status=config.last_run_status,
        last_run_message=config.last_run_message,
    )


def _get_or_404(db: Session, job_key: str) -> ScheduledJobConfig:
    config = db.scalar(select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == job_key))
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scheduled job not found")
    return config


@router.get("", response_model=list[ScheduledJobConfigOut])
def list_scheduled_jobs(
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> list[ScheduledJobConfigOut]:
    rows = db.scalars(select(ScheduledJobConfig).order_by(ScheduledJobConfig.id.asc())).all()
    return [_build_out(r) for r in rows]


@router.patch("/{job_key}", response_model=ScheduledJobConfigOut)
def update_scheduled_job(
    job_key: str,
    req: ScheduledJobConfigUpdateRequest,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> ScheduledJobConfigOut:
    config = _get_or_404(db, job_key)
    now = datetime.now(timezone.utc)
    if req.interval_seconds is not None:
        config.interval_seconds = req.interval_seconds
        # 改了間隔,以現在為基準重算下一次到期時間,避免沿用舊間隔算出來的
        # next_run_at 造成「改成更短間隔後還要等很久」的錯覺。
        config.next_run_at = now + timedelta(seconds=req.interval_seconds)
    if req.enabled is not None:
        config.enabled = req.enabled
    config.updated_at = now
    _audit(
        db,
        user_id=admin_user.id,
        action="scheduled_job_update",
        metadata={
            "jobKey": job_key,
            "intervalSeconds": req.interval_seconds,
            "enabled": req.enabled,
        },
    )
    db.commit()
    return _build_out(config)


@router.post("/{job_key}/run-now", response_model=ScheduledJobRunNowOut)
def run_scheduled_job_now(
    job_key: str,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> ScheduledJobRunNowOut:
    """同步呼叫 `run_job`,HTTP 回應直接帶結果摘要(不用等下一次 60 秒輪詢)。"""
    _get_or_404(db, job_key)
    try:
        result = scheduled_jobs.run_job(db, job_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _audit(
        db,
        user_id=admin_user.id,
        action="scheduled_job_run_now",
        metadata={"jobKey": job_key, "status": result["status"]},
    )
    db.commit()
    return ScheduledJobRunNowOut(
        job_key=result["job_key"],
        status=result["status"],
        message=result["message"],
        summary=result["summary"],
        last_run_at=_utc(result["last_run_at"]),
        next_run_at=_utc(result["next_run_at"]),
    )
