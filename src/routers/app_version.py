"""App 端新版本提醒 —— 公開查詢端點(docs/superpowers/specs/
2026-09-08-app-update-reminder-design.md §4)。

無需鑑權:版本號不敏感,且比對邏輯在 App 端跑,任何客戶端都該能查。這支只讀
DB 目前存的 `latest_version`,**不會**即時去打 NAS —— 偵測(見
`services/app_version_check.py` + 排程)跟查詢的責任分離,避免每個 App 使用者
打開 App 都間接連一次 NAS。

用獨立 router(而非直接掛在 `main.py` 上,像 `/version` 那樣)是為了能走
`Depends(get_db)`,讓測試可以用 `app.dependency_overrides[get_db]` 注入
隔離的測試資料庫——`main.py` 裡少數幾個直接用 `SessionLocal()` 的端點
(`/ready` 等)繞過了這層,對只讀 `SELECT 1` 無妨,但這支端點需要讀取真正
的設定資料,值得多一個 router 檔案換取可測試性。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AppVersionCheckConfig
from ..schemas import PublicAppVersionOut

router = APIRouter()


@router.get("/latest", response_model=PublicAppVersionOut)
def get_latest_app_version(db: Session = Depends(get_db)) -> PublicAppVersionOut:
    config = db.get(AppVersionCheckConfig, 1)
    if config is None:
        return PublicAppVersionOut(version=None, updated_at=None)
    return PublicAppVersionOut(version=config.latest_version, updated_at=config.updated_at)
