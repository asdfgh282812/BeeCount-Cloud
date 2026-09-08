"""App 端新版本提醒 —— NAS WebDAV 版本檔偵測。

docs/superpowers/specs/2026-09-08-app-update-reminder-design.md §3:對管理者
在 `AppVersionCheckConfig` 設定的 `nas_webdav_url`(直接指向 NAS 上的版本標記
檔本體,內容就一行版本號,例如 `3.2.0`)發一次帶 Basic Auth 的 GET,取回內容
當作候選版本號,簡單格式檢查通過才寫回 `latest_version`。

被兩處呼叫:
1. `services/scheduled_jobs.py` 的 `check_latest_app_version` job(定期輪詢)。
2. `routers/admin_app_version.py` 的 `POST .../check-now`(管理者手動觸發,
   同步執行、直接回傳這次偵測結果,不用等排程)。

兩處共用同一個函式,確保「立即偵測」按鈕跟排程走的是完全一樣的邏輯。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..models import AppVersionCheckConfig

# `x.y` 或 `x.y.z` 數字加點——只是防呆(避免把 NAS 回傳的 HTML 錯誤頁之類
# 的髒資料當成版本號生效),不需要比照 semver 做更嚴格的解析。
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}$")
_REQUEST_TIMEOUT_SECONDS = 10.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_config(db: Session) -> AppVersionCheckConfig:
    """全表只有一列(id=1)。「get or create」寫法讓讀寫兩側都不需要另外判斷
    「有沒有列」——第一次呼叫(全新部署)自動補上這一列。"""
    config = db.get(AppVersionCheckConfig, 1)
    if config is None:
        config = AppVersionCheckConfig(id=1, updated_at=_now())
        db.add(config)
        db.flush()
    return config


def check_latest_app_version(db: Session) -> dict:
    """讀 config,`nas_webdav_url` 為空則 skip(不算錯誤、不寫
    `last_check_error`)。請求失敗或格式不合法時只更新 `last_check_error`,
    保留舊的 `latest_version` 不動,避免髒資料污染已生效的版本號。"""
    config = get_or_create_config(db)
    now = _now()

    if not config.nas_webdav_url:
        return {"status": "skipped", "reason": "nas_webdav_url not configured"}

    auth = None
    if config.nas_webdav_user or config.nas_webdav_password:
        auth = (config.nas_webdav_user or "", config.nas_webdav_password or "")

    try:
        resp = httpx.get(
            config.nas_webdav_url, auth=auth, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        candidate = resp.text.strip()
    except Exception as exc:  # noqa: BLE001 — 偵測失敗要記錄,不能讓排程掛掉
        config.last_check_error = str(exc)[:1000]
        config.last_checked_at = now
        config.updated_at = now
        db.commit()
        return {"status": "error", "error": config.last_check_error}

    if not _VERSION_RE.match(candidate):
        config.last_check_error = f"invalid version format: {candidate[:200]!r}"
        config.last_checked_at = now
        config.updated_at = now
        db.commit()
        return {"status": "error", "error": config.last_check_error}

    config.latest_version = candidate
    config.last_check_error = None
    config.last_checked_at = now
    config.updated_at = now
    db.commit()
    return {"status": "ok", "latest_version": candidate}
