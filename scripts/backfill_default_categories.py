"""一次性回填腳本:幫「目前一個分類都沒有」的既有使用者補上預設分類。

背景
====
`src/routers/write/ledgers.py::create_ledger` 新增了「建帳本時若使用者目前
一個分類都沒有,自動種一批預設分類」的邏輯(見 `src/services/
default_categories.py`),但這只對**之後新建**的帳本生效。上線前就已經
存在、分類清單是空的既有使用者(例如自己一路手動建帳本、從沒建過分類的
帳號)不會被這條新邏輯自動修好,需要這支腳本補一次。

**分類是 user-global**(`UserCategoryProjection`,PK=`user_id`+`sync_id`,
跨帳本共享,見 `sync_applier.USER_GLOBAL_ENTITY_TYPES`)——回填目標是
「使用者」而不是「帳本」:找出目前 `user_category_projection` 一列都沒有、
但名下至少有一本(未被軟刪除的)帳本的使用者,幫他種上跟
`create_ledger` 完全同一份預設分類。

寫入方式跟一般 write endpoint 相同:透過 `snapshot_mutator.create_category`
+ `src/routers/write/_shared.py::_emit_entity_diffs` 逐筆產生
`SyncChange` + 同事務寫入 `user_category_projection`(user-global entity 用
`apply_change_to_projection` 同一套 merge 邏輯),不是直接改 projection 表
——這樣其它裝置下次同步也能正確拉到這批新分類。

用法:
    cd /path/to/BeeCount-Cloud
    python -m scripts.backfill_default_categories
    python -m scripts.backfill_default_categories --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from src import snapshot_builder
from src.database import SessionLocal
from src.models import Ledger, User, UserCategoryProjection
from src.routers.read._shared import _is_ledger_deleted
from src.routers.write._shared import _emit_entity_diffs, _payload_with_actor
from src.services.default_categories import build_default_category_payloads
from src.snapshot_mutator import create_category

_DEVICE_ID = "server-default-categories-backfill"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Seed default categories for existing users who have none yet"
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be seeded, do not write")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        users_with_categories = set(
            db.scalars(select(UserCategoryProjection.user_id).distinct()).all()
        )
        all_users = db.scalars(select(User)).all()
        candidates = [u for u in all_users if u.id not in users_with_categories]

        seeded_users = 0
        skipped_no_ledger = 0
        total_categories = 0

        for user in candidates:
            ledgers = db.scalars(
                select(Ledger)
                .where(Ledger.user_id == user.id)
                .order_by(Ledger.created_at.asc())
            ).all()
            ledger = next(
                (row for row in ledgers if not _is_ledger_deleted(db, ledger_id=row.id)), None
            )
            if ledger is None:
                skipped_no_ledger += 1
                continue

            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} user={user.id} "
                f"email={user.email} ledger={ledger.external_id}"
            )
            if args.dry_run:
                seeded_users += 1
                continue

            real_snapshot = snapshot_builder.build(db, ledger)
            if real_snapshot.get("categories"):
                # 极端竞态:两次跑之间使用者自己建了分类,双重保险不覆盖。
                continue

            category_snapshot = real_snapshot
            actor_base = _payload_with_actor({}, user, ledger=ledger)
            for cat_payload in build_default_category_payloads():
                category_snapshot, _ = create_category(
                    category_snapshot, {**actor_base, **cat_payload}
                )
            emitted = _emit_entity_diffs(
                db,
                ledger=ledger,
                current_user=user,
                device_id=_DEVICE_ID,
                prev=real_snapshot,
                next_snapshot=category_snapshot,
                now=datetime.now(timezone.utc),
            )
            db.commit()
            seeded_users += 1
            total_categories += len(emitted)

        print(
            f"\nDone. seeded_users={seeded_users} skipped_no_ledger={skipped_no_ledger} "
            f"total_categories_created={total_categories}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
