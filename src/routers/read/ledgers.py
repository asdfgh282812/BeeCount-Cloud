"""账本维度读端点:/ledgers, /ledgers/{id}, /ledgers/{id}/stats,
及 /ledgers/{id}/{transactions,accounts,categories,budgets,tags} 的列表查询。

都是以账本为主键的 projection 查询,不做跨账本聚合。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import false as sa_false

from ._shared import *  # noqa: F401,F403 — imports + helpers + router


def _dedupe_by_sync_id(rows):
    """跨 ledger 同 sync_id 取一份。用 dict 顺序保留:第一次见到 sync_id 时
    收下,后续重复跳过 —— 上游 SQL 已经按 `source_change_id DESC` 排序,所以
    第一份就是最新的。"""
    seen: dict[str, object] = {}
    for r in rows:
        if r.sync_id not in seen:
            seen[r.sync_id] = r
    return list(seen.values())


@router.get("/ledgers", response_model=list[ReadLedgerOut])
def list_ledgers(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadLedgerOut]:
    # 共享账本 Phase 1:走 LedgerMember 表拿 caller 能访问的全部 ledger(含
    # 自己 owner 的 + 加入的共享账本)。admin 用户直接看所有(管理后台需求)。
    from ...ledger_access import list_accessible_memberships, count_ledger_members

    if _is_admin(current_user):
        rows = list(db.scalars(select(Ledger).order_by(Ledger.created_at.desc())).all())
        memberships: list[tuple[Ledger, str | None]] = [(lg, None) for lg in rows]
    else:
        memberships = list_accessible_memberships(db, user_id=current_user.id)

    out: list[ReadLedgerOut] = []
    for ledger, role in memberships:
        # Hide soft-deleted ledgers.
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            continue
        # currency 暂不做 projection 化 —— 顶层元数据非热点,snapshot_cache 命中
        # 后 ~1ms,偶发 cold miss 50ms 可接受;list_ledgers 本身调用频率低。
        currency = ledger.currency or "CNY"
        ledger_name = _resolve_ledger_name(db, ledger=ledger)
        tx_count, income_total, expense_total, balance_all, _ = _projection_totals(db, ledger.id)
        now = datetime.now(timezone.utc)
        member_count = count_ledger_members(db, ledger_id=ledger.id)
        effective_role = role or ("owner" if ledger.user_id == current_user.id else "viewer")
        out.append(
            ReadLedgerOut(
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                currency=currency,
                month_start_day=ledger.month_start_day or 1,
                transaction_count=tx_count,
                income_total=income_total,
                expense_total=expense_total,
                balance=balance_all,
                exported_at=now,
                updated_at=now,
                role=cast("Any", effective_role),
                is_shared=member_count > 1,
                member_count=member_count,
            )
        )
    return out


@router.get("/ledgers/{ledger_external_id}/stats")
def get_ledger_stats(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """给 mobile 的"深度同步检测"用。返回 server 实际的 tx / attachment / budget
    数,mobile 拉下来跟本地 Drift 对比,检测到差异就触发自动 sync。

    tx_count 从最新 snapshot 的 items 长度算(和 /read/ledgers 保持一致)。
    attachment_count 从 attachment_files 表按 ledger_id 直接 COUNT。
    budget_count 从 snapshot.budgets 长度算(Feature 3b 后生效,materializer
    已经把 budget 写进 snapshot 了)。
    """
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )

    # per-ledger count:单 SQL COUNT,不再 parse snapshot
    def _count(model) -> int:
        return int(db.scalar(
            select(func.count()).select_from(model).where(model.ledger_id == ledger.id)
        ) or 0)

    tx_count = _count(ReadTxProjection)
    budget_count = _count(ReadBudgetProjection)
    # account / category / tag 是 user-global —— "per-ledger count" 在这里
    # 没意义,跟 total 同口径:COUNT DISTINCT sync_id WHERE user_id。下面的
    # _count_distinct_sync 之后会复用同一份。
    def _count_distinct_sync_for(model) -> int:
        return int(db.scalar(
            select(func.count(func.distinct(model.sync_id)))
            .where(model.user_id == current_user.id)
        ) or 0)

    # user-global tables 都用 user_id PK,count distinct 就是 count rows。
    account_count = _count_distinct_sync_for(UserAccountProjection)
    category_count = _count_distinct_sync_for(UserCategoryProjection)
    tag_count = _count_distinct_sync_for(UserTagProjection)

    # 附件计数按 attachment_kind 区分:
    #   - attachment_count / attachment_total: tx 附件(挂在 ledger 上)
    #   - category_attachment_total: 分类自定义图标(user-global,无 ledger)
    # 老数据(0006 migration 前)已经在 migration 里按 read_category_projection
    # 的引用反向标记到 category_icon kind,这里直接按 kind 过滤即可。
    attachment_count = db.scalar(
        select(func.count(AttachmentFile.id)).where(
            AttachmentFile.ledger_id == ledger.id,
            AttachmentFile.attachment_kind == "transaction",
        )
    ) or 0

    # 全局口径:跨当前用户所有账本。projection 的 user_id 列已经 denormalized,
    # 一次 SQL COUNT + COUNT DISTINCT 就出全量。比原来循环 parse 每个 snapshot
    # 快 N 倍。
    # 共享账本:走 LedgerMember 维度,Editor 也算上 Owner 的账本(否则附件
    # 总数等指标在 Editor 视角里少算)。
    user_ledger_ids_subq = (
        select(LedgerMember.ledger_id)
        .where(LedgerMember.user_id == current_user.id)
        .scalar_subquery()
    )

    def _count_distinct_sync(model) -> int:
        return int(db.scalar(
            select(func.count(func.distinct(model.sync_id)))
            .where(model.user_id == current_user.id)
        ) or 0)

    # tx / budget 是 ledger-scoped projection。Editor 视角下,Owner 创建的
    # tx 在 ReadTxProjection.user_id 是 Owner,不是 Editor — 用 user_id
    # 过滤会把共享账本的 tx 全漏掉。改走 LedgerMember 维度,跟 attachment_total
    # 已有的口径一致 + 对齐 mobile 本地 db.transactions 全表统计(那边包含
    # 同步下来的共享账本 tx)。
    def _count_ledger_scoped(model) -> int:
        return int(db.scalar(
            select(func.count()).select_from(model)
            .where(model.ledger_id.in_(user_ledger_ids_subq))
        ) or 0)

    tx_total = _count_ledger_scoped(ReadTxProjection)
    budget_total = _count_ledger_scoped(ReadBudgetProjection)
    account_total = _count_distinct_sync(UserAccountProjection)
    category_total = _count_distinct_sync(UserCategoryProjection)
    tag_total = _count_distinct_sync(UserTagProjection)

    attachment_total = int(
        db.scalar(
            select(func.count(AttachmentFile.id)).where(
                AttachmentFile.ledger_id.in_(user_ledger_ids_subq),
                AttachmentFile.attachment_kind == "transaction",
            )
        )
        or 0
    )

    # 分类自定义图标是 user-global,按 user_id + kind 算总数(不分账本)。
    category_attachment_total = int(
        db.scalar(
            select(func.count(AttachmentFile.id)).where(
                AttachmentFile.user_id == current_user.id,
                AttachmentFile.attachment_kind == "category_icon",
            )
        )
        or 0
    )

    return {
        "transaction_count": tx_count,
        "transaction_total": tx_total,
        "attachment_count": int(attachment_count),
        "attachment_total": attachment_total,
        "category_attachment_total": category_attachment_total,
        "budget_count": budget_count,
        "budget_total": budget_total,
        "account_count": account_count,
        "account_total": account_total,
        "category_count": category_count,
        "category_total": category_total,
        "tag_count": tag_count,
        "tag_total": tag_total,
    }


@router.get("/ledgers/{ledger_external_id}", response_model=ReadLedgerDetailOut)
def get_ledger(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadLedgerDetailOut:
    ledger, role = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )
    currency = ledger.currency or "CNY"
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    tx_count, income_total, expense_total, balance_all, _ = _projection_totals(db, ledger.id)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    now = datetime.now(timezone.utc)
    # 共享账本 Phase 1:member_count 从 ledger_members 表实时数。is_shared = count > 1。
    from ...ledger_access import count_ledger_members
    member_count = count_ledger_members(db, ledger_id=ledger.id)
    return ReadLedgerDetailOut(
        ledger_id=ledger.external_id,
        ledger_name=ledger_name,
        currency=currency,
        month_start_day=ledger.month_start_day or 1,
        transaction_count=tx_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=balance_all,
        exported_at=now,
        updated_at=now,
        source_change_id=source_change_id,
        role=cast("Any", role or "viewer"),
        is_shared=member_count > 1,
        member_count=member_count,
    )


@router.get("/ledgers/{ledger_external_id}/transactions", response_model=list[ReadTransactionOut])
def list_transactions(
    ledger_external_id: str,
    tx_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTransactionOut]:
    # CQRS 读路径:不再 parse snapshot,直接查 read_tx_projection + index。
    # account/category/tag 的 name 已在写入时 denormalized 到 projection 列,
    # rename 时同事务级联更新(见 projection.rename_cascade_*)。
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    owner_id, owner_email, owner_display, owner_avatar, owner_avatar_ver = (
        _load_owner_identity(db, ledger=ledger)
    )

    query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger.id)
    if tx_type:
        query = query.where(ReadTxProjection.tx_type == tx_type)
    if start_at:
        query = query.where(ReadTxProjection.happened_at >= _to_utc(start_at))
    if end_at:
        query = query.where(ReadTxProjection.happened_at <= _to_utc(end_at))
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(
            ReadTxProjection.note.ilike(pattern),
            ReadTxProjection.category_name.ilike(pattern),
            ReadTxProjection.account_name.ilike(pattern),
            ReadTxProjection.from_account_name.ilike(pattern),
            ReadTxProjection.to_account_name.ilike(pattern),
            ReadTxProjection.tags_csv.ilike(pattern),
        ))
    query = query.order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    ).offset(offset).limit(limit)
    rows = db.scalars(query).all()

    # 退款反查(§2.12.3):批次查这一页交易里,谁被退过款(refund_of_sync_id
    # 指向当页 sync_id 集合),group by 原交易 id 组成 refunds 列表。只看当页
    # 已知限制:refund_of 指向不在当页的旧交易时,不会显示出来(交易列表本身
    # 就是分页的,这是既有限制的延伸,不是本次改动引入的新问题)。
    page_sync_ids = [row.sync_id for row in rows]
    refunds_by_target: dict[str, list[ReadTxRefundSummaryOut]] = {}
    if page_sync_ids:
        refund_rows = db.execute(
            select(
                ReadTxProjection.refund_of_sync_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.refund_of_sync_id.in_(page_sync_ids),
            )
        ).all()
        for target_id, refund_sync_id, refund_amount, refund_happened_at in refund_rows:
            refunds_by_target.setdefault(target_id, []).append(
                ReadTxRefundSummaryOut(
                    id=refund_sync_id,
                    amount=refund_amount,
                    happened_at=_to_utc(refund_happened_at),
                )
            )

    # 借還款追蹤(§2.5 體驗補強):批次查這一頁交易關聯到的欠款,建
    # sync_id -> (counterparty_name, direction) 字典,讓前端不用額外查表
    # 就能直接顯示欠款資訊(跟上面 refund 的批次 join 同一套模式)。
    debt_sync_ids = {row.debt_sync_id for row in rows if row.debt_sync_id}
    debt_info_by_id: dict[str, tuple[str | None, str | None]] = {}
    if debt_sync_ids:
        debt_info_rows = db.execute(
            select(
                ReadDebtProjection.sync_id,
                ReadDebtProjection.counterparty_name,
                ReadDebtProjection.direction,
            ).where(
                ReadDebtProjection.ledger_id == ledger.id,
                ReadDebtProjection.sync_id.in_(debt_sync_ids),
            )
        ).all()
        for debt_sid, debt_counterparty_name, debt_direction in debt_info_rows:
            debt_info_by_id[debt_sid] = (debt_counterparty_name, debt_direction)

    results: list[ReadTransactionOut] = []
    for row in rows:
        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                maybe = json.loads(row.tag_sync_ids_json)
                if isinstance(maybe, list):
                    tag_ids = [str(t) for t in maybe]
            except json.JSONDecodeError:
                tag_ids = []
        attachments: list[dict[str, Any]] | None = None
        if row.attachments_json:
            try:
                maybe_att = json.loads(row.attachments_json)
                if isinstance(maybe_att, list):
                    attachments = maybe_att
            except json.JSONDecodeError:
                attachments = None
        debt_info = debt_info_by_id.get(row.debt_sync_id) if row.debt_sync_id else None
        results.append(
            ReadTransactionOut(
                id=row.sync_id,
                tx_index=row.tx_index,
                tx_type=row.tx_type,
                amount=row.amount,
                happened_at=_to_utc(row.happened_at),
                note=row.note,
                category_name=row.category_name,
                category_kind=row.category_kind,
                account_name=row.account_name,
                from_account_name=row.from_account_name,
                to_account_name=row.to_account_name,
                category_id=row.category_sync_id,
                account_id=row.account_sync_id,
                from_account_id=row.from_account_sync_id,
                to_account_id=row.to_account_sync_id,
                tags=row.tags_csv or None,
                tags_list=_tags_list(row.tags_csv),
                tag_ids=tag_ids,
                attachments=attachments,
                exclude_from_stats=bool(row.exclude_from_stats),
                exclude_from_budget=bool(row.exclude_from_budget),
                currency_code=row.currency_code,
                native_amount=row.native_amount,
                refund_of_id=row.refund_of_sync_id,
                installment_plan_id=row.installment_plan_sync_id,
                recurring_rule_id=row.recurring_rule_sync_id,
                recurring_occurrence_overridden=bool(row.recurring_occurrence_overridden),
                refunds=refunds_by_target.get(row.sync_id, []),
                has_splits=bool(row.has_splits),
                splits=_tx_splits_list(row.splits_json),
                debt_id=row.debt_sync_id,
                debt_counterparty_name=debt_info[0] if debt_info else None,
                debt_direction=cast("Any", debt_info[1]) if debt_info else None,
                reward_rule_ids=_reward_rule_ids_list(row.reward_rule_sync_ids_json),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                created_by_user_id=owner_id,
                created_by_email=owner_email,
                created_by_display_name=owner_display,
                created_by_avatar_url=owner_avatar,
                created_by_avatar_version=owner_avatar_ver,
            )
        )
    return results


@router.get("/ledgers/{ledger_external_id}/accounts", response_model=list[ReadAccountOut])
def list_accounts(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadAccountOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    # account / category / tag 是 user-global,读端按 user_id 列出该用户的
    # **唯一一份**(同 sync_id 在不同 ledger 的 projection 中可能有多行残留 —
    # snapshot fullPush 时按 ledger fanout,delete 已修复为跨 ledger 删,
    # 但存量数据可能仍有重复)。这里用 _dedupe_by_sync_id 去重,优先取
    # source_change_id 最大(最新)的一份。
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op,但保留
     # 调用以兼容历史 helper 签名,不影响行为。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserAccountProjection)
            .where(UserAccountProjection.user_id == current_user.id)
            .order_by(UserAccountProjection.sync_id.asc())
        ).all()
    )
    rows.sort(key=lambda r: (r.name or "").lower())
    return [
        ReadAccountOut(
            id=row.sync_id,
            name=row.name or "",
            account_type=row.account_type or "",
            currency=row.currency or "",
            initial_balance=float(row.initial_balance or 0.0),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
            note=row.note,
            credit_limit=row.credit_limit,
            billing_day=row.billing_day,
            payment_due_day=row.payment_due_day,
            bank_name=row.bank_name,
            card_last_four=row.card_last_four,
            parent_account_id=row.parent_account_id,
            hidden=row.hidden,
            auto_pay_enabled=row.auto_pay_enabled,
            auto_pay_from_account_id=row.auto_pay_from_account_id,
            avatar_cloud_file_id=row.avatar_cloud_file_id,
            avatar_cloud_sha256=row.avatar_cloud_sha256,
        )
        for row in rows
    ]


def _date_to_utc_dt(d: date, *, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _require_credit_card_schedule(account: UserAccountProjection) -> tuple[int, int]:
    if account.billing_day is None or account.payment_due_day is None:
        raise HTTPException(
            status_code=400,
            detail="account has no billing_day/payment_due_day configured",
        )
    return account.billing_day, account.payment_due_day


def _require_billing_root(account: UserAccountProjection) -> None:
    """主帳戶(§2.9 Phase 4,2026-08-02 改版 + 2026-08-02 第二輪單卡放寬)。
    合併帳單只能對「群組」或「沒有掛靠任何群組的獨立信用卡」查詢 ——
    見 `credit_card_billing.is_billing_root` 的完整說明。已經掛靠某個群組
    的子卡不能被直接查(要透過它的群組)。"""
    if not credit_card_billing.is_billing_root(account):
        raise HTTPException(
            status_code=400,
            detail=(
                "account is not billable directly; use an account_group, or a "
                "credit_card with no parent_account_id"
            ),
        )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/billing-summary",
    response_model=ReadAccountBillingSummaryOut,
)
def get_account_billing_summary(
    ledger_external_id: str,
    account_id: str,
    cycle_offset: int = Query(default=0, ge=-60, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountBillingSummaryOut:
    """信用卡合併帳單(§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md,2026-08-02 改版
    為群組模型,同日第二輪放寬到單卡)。`account_id` 必須通過
    `credit_card_billing.is_billing_root` 檢查:要嘛是 `account_type ==
    "account_group"` 的純管理容器帳戶(`billing_day`/`payment_due_day`/
    `credit_limit` 設在群組自己身上,代表發卡行給這個群組共用的額度/結帳
    週期,實際刷卡消費/收付款的是掛在它底下的子帳戶,群組自己不是成員、
    不計入消費),要嘛是一張**沒有掛靠任何群組**的獨立信用卡(這時它自己
    就是唯一成員,`billing_day` 等設定直接設在它自己身上)。

    `remaining_due` 用「終身跑動餘額」計算(累計至最近一次已結束帳單週期
    為止的所有子帳戶消費,減掉所有子帳戶+群組自己收到的還款轉帳,不分
    週期窗口):這樣任何一期的溢繳都會自動結轉到未來各期,不會在下一期
    結帳日一過就從計算窗口裡消失(原本按「結帳日之後」窗口查 paid_amount
    的寫法有這個 carry-forward 遺失問題,這次順便修掉)。`statement_amount`
    維持「僅本期」窗口,單純做資訊性顯示用。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    billing_day, payment_due_day = _require_credit_card_schedule(account)

    children = credit_card_billing.resolve_billing_children(db, account=account)
    member_ids = [row.sync_id for row in children]

    now = datetime.now(timezone.utc)
    billing = credit_card_billing.compute_group_billing(
        db, ledger_id=ledger.id, group=account, children=children, now=now,
    )

    members_out = [
        ReadAccountBillingMemberOut(
            account_id=row.sync_id,
            account_name=row.name or "",
            cycle_spend=round(billing["per_child_cycle_spend"].get(row.sync_id, 0.0), 2),
        )
        for row in children
    ]

    remaining_due = billing["remaining_due"]
    # 2026-08-03 使用者反饋 #2:轉分期不算已繳,額度不該恢復 —— 用不扣分期
    # 沖銷的 credit_used 算可用額度,remaining_due(當期應繳)維持扣沖銷後
    # 的數字不變。
    credit_used = billing["credit_used"]
    available_credit = (account.credit_limit - credit_used) if account.credit_limit is not None else None

    period = credit_card_billing.compute_cycle_period_billing(
        db, ledger_id=ledger.id, group=account, children=children, now=now, cycle_offset=cycle_offset,
    )

    return ReadAccountBillingSummaryOut(
        account_id=account.sync_id,
        account_name=account.name or "",
        billing_day=billing_day,
        payment_due_day=payment_due_day,
        member_account_ids=member_ids,
        members=members_out,
        cycle_start=_date_to_utc_dt(billing["cycle_start"]),
        cycle_end=_date_to_utc_dt(billing["cycle_end"]),
        due_date=_date_to_utc_dt(billing["due_date"]),
        statement_amount=round(billing["statement_amount"], 2),
        paid_amount=round(billing["paid_amount"], 2),
        remaining_due=round(remaining_due, 2),
        open_cycle_start=_date_to_utc_dt(billing["open_cycle_start"]),
        open_cycle_end=_date_to_utc_dt(billing["open_cycle_end"]),
        open_cycle_due_date=_date_to_utc_dt(billing["open_cycle_due_date"]),
        open_cycle_spend=round(billing["open_cycle_spend"], 2),
        credit_limit=account.credit_limit,
        available_credit=round(available_credit, 2) if available_credit is not None else None,
        period_cycle_start=_date_to_utc_dt(period["cycle_start"]),
        period_cycle_end=_date_to_utc_dt(period["cycle_end"]),
        period_due_date=_date_to_utc_dt(period["due_date"]),
        period_new_spend=period["new_spend"],
        period_carryover_due=period["carryover_due"],
        period_total_due=period["total_due"],
        period_paid_in_cycle=period["paid_in_cycle"],
        period_remaining_due=period["remaining_due"],
        period_has_older=period["has_older"],
        period_has_newer=period["has_newer"],
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/interest-free-suggestion",
    response_model=ReadInterestFreeSuggestionOut,
)
def get_account_interest_free_suggestion(
    ledger_external_id: str,
    account_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadInterestFreeSuggestionOut:
    """信用卡免息期推薦(§2.9 Phase 4,2026-08-02 改版)。純計算,不查交易,
    只依賴群組帳戶自己的 `billing_day`/`payment_due_day`(跟 billing-summary
    同一個群組模型:結帳週期設在 `account_type == "account_group"` 上)。"""
    is_admin = _is_admin(current_user)
    _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    billing_day, payment_due_day = _require_credit_card_schedule(account)

    now = datetime.now(timezone.utc)
    suggestion = credit_card.interest_free_suggestion(now.date(), billing_day, payment_due_day)
    return ReadInterestFreeSuggestionOut(
        account_id=account.sync_id,
        as_of=now,
        billing_day=suggestion["billing_day"],
        payment_due_day=suggestion["payment_due_day"],
        current_cycle_start=_date_to_utc_dt(suggestion["current_cycle_start"]),
        current_cycle_end=_date_to_utc_dt(suggestion["current_cycle_end"]),
        current_cycle_due_date=_date_to_utc_dt(suggestion["current_cycle_due_date"]),
        next_cycle_start=_date_to_utc_dt(suggestion["next_cycle_start"]),
        next_cycle_end=_date_to_utc_dt(suggestion["next_cycle_end"]),
        next_cycle_due_date=_date_to_utc_dt(suggestion["next_cycle_due_date"]),
        recommended_purchase_after=_date_to_utc_dt(suggestion["recommended_purchase_after"]),
        min_interest_free_days=suggestion["min_interest_free_days"],
        max_interest_free_days=suggestion["max_interest_free_days"],
    )


def _get_own_credit_card_account(
    db: Session, *, user_id: str, account_id: str,
) -> UserAccountProjection:
    """信用卡紅利回饋(§2.9.5)規則/計算都綁在一張真實的 `credit_card` 帳戶
    上,不是 account_group(群組純管理容器自己不會被刷卡)。"""
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    if account.account_type != "credit_card":
        raise HTTPException(status_code=400, detail="account is not a credit_card")
    return account


def _card_reward_rule_to_out(row: ReadCardRewardRuleProjection, *, last_change_id: int) -> ReadCardRewardRuleOut:
    category_ids: list[str] | None = None
    if row.category_sync_ids_json:
        try:
            parsed = json.loads(row.category_sync_ids_json)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            category_ids = [str(c) for c in parsed]
    return ReadCardRewardRuleOut(
        id=row.sync_id,
        account_id=row.account_sync_id,
        label=row.label or "",
        category_ids=category_ids,
        rate_type=cast("Any", row.rate_type or "percentage"),
        rate_value=row.rate_value,
        rounding=cast("Any", row.rounding or "round"),
        calc_basis=cast("Any", row.calc_basis or "transaction_date"),
        interval=cast("Any", row.interval or "billing_cycle"),
        min_spend_threshold=row.min_spend_threshold,
        min_tx_amount=row.min_tx_amount,
        cap_amount=row.cap_amount,
        cap_shared_key=row.cap_shared_key,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        settlement_type=cast("Any", row.settlement_type or "manual"),
        settlement_days=row.settlement_days,
        reward_account_id=row.reward_account_id,
        note=row.note,
        enabled=row.enabled,
        last_change_id=last_change_id,
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-reward-rules",
    response_model=list[ReadCardRewardRuleOut],
)
def list_card_reward_rules(
    ledger_external_id: str,
    account_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCardRewardRuleOut]:
    """信用卡紅利回饋規則只读列表(§2.9.5 Phase 4.5)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.account_sync_id == account_id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    return [_card_reward_rule_to_out(row, last_change_id=source_change_id) for row in rows]


@router.get(
    "/ledgers/{ledger_external_id}/card-reward-rules",
    response_model=list[ReadCardRewardRuleOut],
)
def list_all_card_reward_rules(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCardRewardRuleOut]:
    """信用卡紅利回饋規則跨卡列表(§2.9.5.4):回傳目前使用者名下**所有**
    信用卡的所有回饋規則(不限某一張卡),給共用上限群組跨卡挑選 UI 用
    (見 `CardRewardRuleFormDialog`)。`ledger_external_id` 只是拿來過既有
    的帳本成員校驗(`card_reward_rule` 是 user-global 實體,不挂任何
    ledger,同 `list_card_reward_rules`)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    rows = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    return [_card_reward_rule_to_out(row, last_change_id=source_change_id) for row in rows]


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-rewards",
    response_model=ReadCardRewardsOut,
)
def get_account_card_rewards(
    ledger_external_id: str,
    account_id: str,
    period_offset: int = Query(default=0, ge=-120, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCardRewardsOut:
    """信用卡紅利回饋當期計算(§2.9.5 Phase 4.5)。規則本身不落庫回饋金額,
    這裡即時從交易加總算出,見 `services.card_rewards` docstring。
    `period_offset` 跟 billing-summary 的 `cycle_offset` 同款語意:`0` 是
    目前這一期(`billing_cycle` 規則是「還在累積、尚未結束」的那期,對齊
    使用者「現在刷卡能拿多少回饋」的直覺;`calendar_month` 規則是本月),
    負數往回看歷史期別。下界比 billing-summary 的 `cycle_offset`(`-60`)
    寬,因為前端(`AccountDetailDialog.tsx`)換算成
    `period_offset = cycleOffset - 1` 傳進來(§2.9.5.4 修正兩者過去各自
    獨立實作、對「0」定義差一期的 bug)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)

    own_rules = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.account_sync_id == account_id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    # 共用上限群組是跨卡的(§2.9.5.4),把同組的其它卡規則也一起丟進去算
    # cap_shared_key 分攤,但只把這張卡自己的規則回給前端。
    all_rules = card_rewards.fetch_cap_group_rules(db, user_id=current_user.id, base_rules=own_rules)

    now = datetime.now(timezone.utc)
    results = card_rewards.compute_account_card_rewards(
        db, ledger_id=ledger.id, account=account, rules=all_rules, now=now, period_offset=period_offset,
    )
    card_rewards.apply_caps(results)

    own_rule_ids = {r.sync_id for r in own_rules}
    items = [
        ReadCardRewardRuleUsageOut(
            rule_id=r["rule"].sync_id,
            label=r["rule"].label or "",
            period_start=_date_to_utc_dt(r["period_start"]),
            period_end=_date_to_utc_dt(r["period_end"]),
            qualifying_spend=r["qualifying_spend"],
            threshold_met=r["threshold_met"],
            raw_reward=r["raw_reward"],
            capped_reward=r["capped_reward"],
            cap_amount=r["rule"].cap_amount,
            cap_shared_key=r["rule"].cap_shared_key,
            status=cast("Any", r["status"]),
        )
        for r in results
        if r["rule"].sync_id in own_rule_ids
    ]
    return ReadCardRewardsOut(
        account_id=account.sync_id,
        as_of=now,
        items=items,
        total_reward=round(sum(i.capped_reward for i in items), 2),
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-reward-rules/{rule_id}/transactions",
    response_model=ReadCardRewardRuleTransactionsOut,
)
def get_card_reward_rule_transactions(
    ledger_external_id: str,
    account_id: str,
    rule_id: str,
    period_offset: int = Query(default=0, ge=-120, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCardRewardRuleTransactionsOut:
    """單一規則的交易明細彈窗(§2.9.5.3):命中哪些交易 + 剩餘回饋額度。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)
    rule = db.scalar(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.sync_id == rule_id,
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="card reward rule not found")
    if rule.account_sync_id != account_id:
        raise HTTPException(status_code=400, detail="rule_id does not belong to this account")

    now = datetime.now(timezone.utc)
    detail = card_rewards.list_rule_qualifying_transactions(
        db, ledger_id=ledger.id, account=account, rule=rule, now=now, period_offset=period_offset,
    )
    items = [
        ReadCardRewardQualifyingTxOut(
            tx_id=item["tx"].sync_id,
            happened_at=item["tx"].happened_at,
            amount=item["tx"].amount,
            note=item["tx"].note,
            category_name=item["tx"].category_name,
            reward_amount=item["reward_amount"],
            settlement_date=(
                _date_to_utc_dt(settlement_date)
                if (settlement_date := card_rewards.compute_settlement_date(
                    rule, tx_happened_at=item["tx"].happened_at, period_end=detail["period_end"],
                )) is not None
                else None
            ),
        )
        for item in detail["items"]
    ]
    return ReadCardRewardRuleTransactionsOut(
        rule_id=rule.sync_id,
        label=rule.label or "",
        period_start=_date_to_utc_dt(detail["period_start"]),
        period_end=_date_to_utc_dt(detail["period_end"]),
        status=cast("Any", detail["status"]),
        qualifying_spend=detail["qualifying_spend"],
        raw_reward=detail["raw_reward"],
        capped_reward=detail["capped_reward"],
        cap_amount=rule.cap_amount,
        cap_shared_key=rule.cap_shared_key,
        remaining_reward_room=detail["remaining_reward_room"],
        items=items,
    )


@router.get("/ledgers/{ledger_external_id}/categories", response_model=list[ReadCategoryOut])
def list_categories(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCategoryOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserCategoryProjection)
            .where(UserCategoryProjection.user_id == current_user.id)
            .order_by(UserCategoryProjection.sync_id.asc())
        ).all()
    )
    rows.sort(key=lambda r: (
        r.kind or "",
        r.sort_order or 0,
        (r.name or "").lower(),
    ))
    return [
        ReadCategoryOut(
            id=row.sync_id,
            name=row.name or "",
            kind=row.kind or "",
            level=int(row.level or 0),
            sort_order=int(row.sort_order or 0),
            icon=row.icon,
            icon_type=row.icon_type,
            custom_icon_path=row.custom_icon_path,
            icon_cloud_file_id=row.icon_cloud_file_id,
            icon_cloud_sha256=row.icon_cloud_sha256,
            parent_name=row.parent_name,
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


@router.get("/ledgers/{ledger_external_id}/budgets", response_model=list[ReadBudgetOut])
def list_budgets(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadBudgetOut]:
    """预算只读列表。mobile Feature 3b 之后,snapshot.budgets 由 server
    materializer 维护,这里按 categoryId syncId 反查 category name 填上,
    跟 tx/tag 接口同一套 id→name 映射思路。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    # category name 来自 projection,user-global 维度查询(同 sync_id 跨 ledger
    # 重复时取最新一份 —— SQL 按 source_change_id DESC 排,字典写入用第一个胜出)
    cat_rows = db.execute(
        select(
            UserCategoryProjection.sync_id,
            UserCategoryProjection.name,
            UserCategoryProjection.source_change_id,
        )
        .where(UserCategoryProjection.user_id == current_user.id)
        .order_by(UserCategoryProjection.sync_id.asc())
    ).all()
    cat_name_by_sync: dict[str, str] = {}
    for r in cat_rows:
        if r.sync_id not in cat_name_by_sync:
            cat_name_by_sync[r.sync_id] = (r.name or "").strip()

    # 展示前做两步脏数据过滤(来自早期同步 bug 遗留):
    #   1) 分类预算但 category_sync_id 为空 —— 孤儿
    #   2) (type, category_sync_id) 维度去重 —— 按 sync_id 字典序最大的留
    raw = db.scalars(
        select(ReadBudgetProjection).where(ReadBudgetProjection.ledger_id == ledger.id)
    ).all()
    dedup: dict[tuple[str, str], ReadBudgetProjection] = {}
    for b in raw:
        btype = b.budget_type or "total"
        if btype == "category" and not b.category_sync_id:
            continue
        key = (btype, b.category_sync_id or "")
        current = dedup.get(key)
        if current is None or current.sync_id < b.sync_id:
            dedup[key] = b

    results: list[ReadBudgetOut] = []
    for b in dedup.values():
        results.append(
            ReadBudgetOut(
                id=b.sync_id,
                type=b.budget_type or "total",
                category_id=b.category_sync_id,
                category_name=cat_name_by_sync.get(b.category_sync_id) if b.category_sync_id else None,
                amount=float(b.amount or 0),
                period=b.period or "monthly",
                start_day=int(b.start_day or 1),
                enabled=bool(b.enabled),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return results


@router.get(
    "/ledgers/{ledger_external_id}/budgets/usage",
    response_model=ReadBudgetUsageOut,
)
def list_budgets_usage(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadBudgetUsageOut:
    """每个 enabled budget 当前周期已用金额(后端 SQL 聚合)。

    跟手机端 `local_budget_repository.getBudgetUsage` 同语义:
    - total 预算: 该 ledger 当周期内全部 expense SUM
    - category 预算: 预算关联分类自身 + 所有 parent_sync_id 指向它的子分类的
      expense SUM(父分类预算自动覆盖子分类支出)

    取代"前端循环 fetch /workspace/transactions + reduce"的旧路径:
    - N 次 HTTP → 1 次
    - 计算下沉到 SQL,不受 limit=1000 截断
    - 子分类展开在 server 完成,前端无需感知 parent_sync_id
    """
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )

    # 跟 list_budgets 一致:不 filter enabled,以便前端 join 时不丢 budget。
    raw = db.scalars(
        select(ReadBudgetProjection).where(
            ReadBudgetProjection.ledger_id == ledger.id,
        )
    ).all()

    # 跟 list_budgets 同款脏数据去重: (type, category_sync_id) 维度,sync_id
    # 字典序最大胜出。usage 跟 list 必须用同一份 budget 才一致。
    dedup: dict[tuple[str, str], ReadBudgetProjection] = {}
    for b in raw:
        btype = b.budget_type or "total"
        if btype == "category" and not b.category_sync_id:
            continue
        key = (btype, b.category_sync_id or "")
        current = dedup.get(key)
        if current is None or current.sync_id < b.sync_id:
            dedup[key] = b

    now = datetime.now(timezone.utc)

    # 预算周期跟随账本 month_start_day(设计 D5:budget.start_day 弃用,
    # 与 mobile local_budget_repository 同口径)
    period_day = ledger.month_start_day or 1

    start, end = _current_period_range(period_day, now)

    items: list[ReadBudgetUsageItemOut] = []
    for b in dedup.values():
        # 预算金额本身是账本本位币,用量必须同计量单位:
        # 折本位币口径(0018)读 native_amount,NULL 回退 amount。
        base_q = select(func.coalesce(func.sum(
            func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
        ), 0.0)).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.happened_at >= start,
            ReadTxProjection.happened_at < end,
            # D2: 预算用量仅看 exclude_from_budget,与 exclude_from_stats 独立。
            # 标记排除预算的交易不计入用量(total + category 共用此 base_q)。
            ReadTxProjection.exclude_from_budget == sa_false(),
        )
        if (b.budget_type or "total") == "category" and b.category_sync_id:
            # parent + 所有 parent_sync_id 指向它的子分类
            child_ids = list(db.scalars(
                select(UserCategoryProjection.sync_id).where(
                    UserCategoryProjection.user_id == ledger.user_id,
                    UserCategoryProjection.parent_sync_id == b.category_sync_id,
                )
            ).all())
            ids = [b.category_sync_id, *child_ids]
            # 拆帳(§2.4):has_splits=True 的父行 category_sync_id 是 NULL,
            # 下面这个条件天然排除拆帳交易(NULL IN (...) 不成立)—— 分到这个
            # 分类的那部分金额要另外从 read_tx_split_projection 查,按整笔的
            # native/amount 折算比例缩放后累加,两边加总才是这个分类的真实用量。
            non_split_used = float(db.scalar(
                base_q.where(ReadTxProjection.category_sync_id.in_(ids))
            ) or 0.0)
            scale_expr = (
                func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
                / func.nullif(ReadTxProjection.amount, 0.0)
            )
            split_used = float(db.scalar(
                select(func.coalesce(func.sum(ReadTxSplitProjection.amount * scale_expr), 0.0))
                .select_from(ReadTxSplitProjection)
                .join(
                    ReadTxProjection,
                    (ReadTxProjection.ledger_id == ReadTxSplitProjection.ledger_id)
                    & (ReadTxProjection.sync_id == ReadTxSplitProjection.tx_sync_id),
                )
                .where(
                    ReadTxSplitProjection.ledger_id == ledger.id,
                    ReadTxSplitProjection.category_sync_id.in_(ids),
                    ReadTxProjection.tx_type == "expense",
                    ReadTxProjection.happened_at >= start,
                    ReadTxProjection.happened_at < end,
                    ReadTxProjection.exclude_from_budget == sa_false(),
                )
            ) or 0.0)
            used = non_split_used + split_used
        else:
            used = float(db.scalar(base_q) or 0.0)
        items.append(ReadBudgetUsageItemOut(budget_id=b.sync_id, used=abs(used)))

    return ReadBudgetUsageOut(items=items)


@router.get(
    "/ledgers/{ledger_external_id}/recurring-rules",
    response_model=list[ReadRecurringRuleOut],
)
def list_recurring_rules(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadRecurringRuleOut]:
    """週期性收支规则只读列表(§2.2)。到期后的实际生成交易走
    services.recurring_materializer,不在这个端点里处理。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    cat_rows = db.execute(
        select(UserCategoryProjection.sync_id, UserCategoryProjection.name)
        .where(UserCategoryProjection.user_id == current_user.id)
    ).all()
    cat_name_by_sync = {r.sync_id: (r.name or "").strip() for r in cat_rows}

    rows = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.ledger_id == ledger.id,
        ).order_by(ReadRecurringRuleProjection.next_run_at.asc())
    ).all()
    return [
        ReadRecurringRuleOut(
            id=row.sync_id,
            tx_type=row.tx_type,
            amount=float(row.amount or 0),
            note=row.note,
            category_id=row.category_sync_id,
            category_name=cat_name_by_sync.get(row.category_sync_id) if row.category_sync_id else None,
            account_id=row.account_sync_id,
            from_account_id=row.from_account_sync_id,
            to_account_id=row.to_account_sync_id,
            frequency=cast("Any", row.frequency or "monthly"),
            interval=int(row.interval or 1),
            next_run_at=row.next_run_at,
            end_at=row.end_at,
            enabled=bool(row.enabled),
            generated_until_at=row.generated_until_at,
            advanced_rule_json=_parse_advanced_rule_json(row.advanced_rule_json),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
        )
        for row in rows
    ]


def _parse_advanced_rule_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get(
    "/ledgers/{ledger_external_id}/installment-plans",
    response_model=list[ReadInstallmentPlanOut],
)
def list_installment_plans(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadInstallmentPlanOut]:
    """分期付款计划只读列表(§2.3)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadInstallmentPlanProjection).where(
            ReadInstallmentPlanProjection.ledger_id == ledger.id,
        ).order_by(ReadInstallmentPlanProjection.first_period_at.desc())
    ).all()
    now = datetime.now(timezone.utc)
    # paid_periods/next_period_at 不再信任 projection 里那两个不被排程更新的
    # 历史相容字段(见 ReadInstallmentPlanProjection docstring),改成从
    # read_installment_period_projection 即时算出,跟 snapshot_builder.build
    # 的口径保持一致。
    period_rows = db.execute(
        select(
            ReadInstallmentPeriodProjection.plan_sync_id,
            ReadInstallmentPeriodProjection.due_at,
            ReadInstallmentPeriodProjection.total_amount,
        ).where(ReadInstallmentPeriodProjection.ledger_id == ledger.id)
    ).all()
    periods_by_plan: dict[str, list[tuple[datetime, float]]] = {}
    for plan_sid, due_at, total_amount in period_rows:
        periods_by_plan.setdefault(plan_sid, []).append((_to_utc(due_at), float(total_amount or 0)))

    out: list[ReadInstallmentPlanOut] = []
    for row in rows:
        periods_sorted = sorted(periods_by_plan.get(row.sync_id) or [])
        due_dates = [d for d, _ in periods_sorted]
        paid_periods = sum(1 for d in due_dates if d <= now)
        future_periods = [(d, amt) for d, amt in periods_sorted if d > now]
        if future_periods:
            next_period_at, current_period_amount = future_periods[0]
        elif periods_sorted:
            next_period_at, current_period_amount = periods_sorted[-1]
        else:
            next_period_at, current_period_amount = _to_utc(row.first_period_at), float(row.period_amount or 0)
        out.append(
            ReadInstallmentPlanOut(
                id=row.sync_id,
                total_amount=float(row.total_amount or 0),
                periods=int(row.periods or 1),
                period_amount=current_period_amount,
                first_period_at=row.first_period_at,
                next_period_at=next_period_at,
                paid_periods=paid_periods,
                account_id=row.account_sync_id,
                category_id=row.category_sync_id,
                note=row.note,
                status=cast("Any", row.status or "active"),
                repayment_method=cast("Any", row.repayment_method or "equal_principal"),
                interest_period=cast("Any", row.interest_period or "monthly"),
                interest_rate=float(row.interest_rate or 0.0),
                round_amounts=bool(row.round_amounts),
                remainder_position=cast("Any", row.remainder_position or "last"),
                grace_period_months=int(row.grace_period_months or 0),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/installment-plans/{plan_id}/periods",
    response_model=list[ReadInstallmentPeriodOut],
)
def list_installment_periods(
    ledger_external_id: str,
    plan_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadInstallmentPeriodOut]:
    """分期计划的每期明细(§2.12.1 Phase 1.5)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    rows = db.scalars(
        select(ReadInstallmentPeriodProjection).where(
            ReadInstallmentPeriodProjection.ledger_id == ledger.id,
            ReadInstallmentPeriodProjection.plan_sync_id == plan_id,
        ).order_by(ReadInstallmentPeriodProjection.period_no.asc())
    ).all()
    # 单期退款(§2.6/§2.12.1)反查:哪期收到过退款,同 ledgers.py 里给普通交易
    # 用的 refunds_by_target 是同一个模式,只是 join key 换成 period.tx_sync_id
    # (退款交易的 refund_of_sync_id 指向的是"原本那期的 tx",不是 period 自己
    # 的 sync_id)。一期理论上只会被退一次,多笔情况下取最新一笔。
    period_tx_ids = [row.tx_sync_id for row in rows if row.tx_sync_id]
    refund_by_tx_id: dict[str, tuple[str, float, datetime]] = {}
    if period_tx_ids:
        refund_rows = db.execute(
            select(
                ReadTxProjection.refund_of_sync_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.refund_of_sync_id.in_(period_tx_ids),
            )
        ).all()
        for target_tx_id, refund_sync_id, refund_amount, refund_happened_at in refund_rows:
            happened_at_utc = _to_utc(refund_happened_at)
            existing = refund_by_tx_id.get(target_tx_id)
            if existing is None or happened_at_utc > existing[2]:
                refund_by_tx_id[target_tx_id] = (refund_sync_id, float(refund_amount), happened_at_utc)

    out: list[ReadInstallmentPeriodOut] = []
    for row in rows:
        refund_info = refund_by_tx_id.get(row.tx_sync_id) if row.tx_sync_id else None
        out.append(
            ReadInstallmentPeriodOut(
                id=row.sync_id,
                plan_id=row.plan_sync_id,
                period_no=row.period_no,
                due_at=row.due_at,
                principal_amount=float(row.principal_amount or 0.0),
                interest_amount=float(row.interest_amount or 0.0),
                total_amount=float(row.total_amount or 0.0),
                status=cast("Any", row.status or "generated"),
                tx_id=row.tx_sync_id,
                refund_tx_id=refund_info[0] if refund_info else None,
                refund_amount=refund_info[1] if refund_info else None,
                refunded_at=refund_info[2] if refund_info else None,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/debts",
    response_model=list[ReadDebtOut],
)
def list_debts(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadDebtOut]:
    """借還款追蹤只读列表(§2.5)。`remaining_amount`/`status` 不落库,这里
    从 `read_tx_projection.debt_sync_id` 反查交易即时汇总算出(见
    `ReadDebtProjection` docstring)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadDebtProjection).where(
            ReadDebtProjection.ledger_id == ledger.id,
        ).order_by(ReadDebtProjection.due_at.asc().nulls_last())
    ).all()
    if not rows:
        return []

    debt_ids = [row.sync_id for row in rows]
    repayment_rows = db.execute(
        select(
            ReadTxProjection.debt_sync_id,
            ReadTxProjection.sync_id,
            ReadTxProjection.amount,
            ReadTxProjection.happened_at,
        ).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.debt_sync_id.in_(debt_ids),
        ).order_by(ReadTxProjection.happened_at.desc())
    ).all()
    repayments_by_debt: dict[str, list[ReadDebtRepaymentOut]] = {}
    repaid_by_debt: dict[str, float] = {}
    for debt_sid, tx_sid, amount, happened_at in repayment_rows:
        repayments_by_debt.setdefault(debt_sid, []).append(
            ReadDebtRepaymentOut(id=tx_sid, amount=float(amount or 0), happened_at=happened_at)
        )
        repaid_by_debt[debt_sid] = repaid_by_debt.get(debt_sid, 0.0) + abs(float(amount or 0))

    out: list[ReadDebtOut] = []
    for row in rows:
        principal = float(row.principal_amount or 0)
        repaid = repaid_by_debt.get(row.sync_id, 0.0)
        remaining = max(principal - repaid, 0.0)
        if row.closed_at is not None:
            debt_status = "closed"
        elif remaining <= 0.01:
            debt_status = "settled"
        elif repaid > 0:
            debt_status = "partial"
        else:
            debt_status = "open"
        out.append(
            ReadDebtOut(
                id=row.sync_id,
                direction=cast("Any", row.direction or "payable"),
                counterparty_name=row.counterparty_name or "",
                principal_amount=principal,
                remaining_amount=remaining,
                status=cast("Any", debt_status),
                due_at=row.due_at,
                note=row.note,
                repayments=repayments_by_debt.get(row.sync_id, []),
                closed_at=row.closed_at,
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/tx-templates",
    response_model=list[ReadTxTemplateOut],
)
def list_tx_templates(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTxTemplateOut]:
    """交易範本只读列表(§2.7)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    cat_rows = db.execute(
        select(UserCategoryProjection.sync_id, UserCategoryProjection.name)
        .where(UserCategoryProjection.user_id == current_user.id)
    ).all()
    cat_name_by_sync = {r.sync_id: (r.name or "").strip() for r in cat_rows}
    acc_rows = db.execute(
        select(UserAccountProjection.sync_id, UserAccountProjection.name)
        .where(UserAccountProjection.user_id == current_user.id)
    ).all()
    acc_name_by_sync = {r.sync_id: (r.name or "").strip() for r in acc_rows}

    rows = db.scalars(
        select(ReadTxTemplateProjection).where(
            ReadTxTemplateProjection.ledger_id == ledger.id,
        ).order_by(ReadTxTemplateProjection.sort_order.asc(), ReadTxTemplateProjection.name.asc())
    ).all()
    out: list[ReadTxTemplateOut] = []
    for row in rows:
        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                parsed = json.loads(row.tag_sync_ids_json)
                if isinstance(parsed, list):
                    tag_ids = [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
        out.append(
            ReadTxTemplateOut(
                id=row.sync_id,
                name=row.name or "",
                tx_type=row.tx_type or "expense",
                amount=float(row.amount or 0),
                note=row.note,
                category_id=row.category_sync_id,
                category_name=cat_name_by_sync.get(row.category_sync_id) if row.category_sync_id else None,
                account_id=row.account_sync_id,
                account_name=acc_name_by_sync.get(row.account_sync_id) if row.account_sync_id else None,
                from_account_id=row.from_account_sync_id,
                from_account_name=acc_name_by_sync.get(row.from_account_sync_id) if row.from_account_sync_id else None,
                to_account_id=row.to_account_sync_id,
                to_account_name=acc_name_by_sync.get(row.to_account_sync_id) if row.to_account_sync_id else None,
                tag_ids=tag_ids,
                sort_order=int(row.sort_order or 0),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


def _current_period_range(
    start_day: int, now: datetime
) -> tuple[datetime, datetime]:
    """跟手机端 `local_budget_repository.getBudgetUsage` 同款月周期算法:
    - 当天 >= start_day → 本月 start_day 起,下月 start_day 止
    - 当天 < start_day → 上月 start_day 起,本月 start_day 止
    边界统一到 [1, 28],避免 29/30/31 在 2 月翻车。
    调用方现统一传账本 month_start_day(设计 D5),不再传 budget.start_day。
    """
    day = max(1, min(28, start_day or 1))
    if now.day >= day:
        start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
        # 下月同 day —— year/month 进位
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1)
        else:
            end = start.replace(month=now.month + 1)
    else:
        # 上月 day —— 借位
        if now.month == 1:
            start = now.replace(year=now.year - 1, month=12, day=day,
                                hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(month=now.month - 1, day=day,
                                hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    return start, end


@router.get("/ledgers/{ledger_external_id}/tags", response_model=list[ReadTagOut])
def list_tags(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTagOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserTagProjection)
            .where(UserTagProjection.user_id == current_user.id)
            .order_by(UserTagProjection.sync_id.asc())
        ).all()
    )
    rows.sort(key=lambda r: (r.name or "").lower())
    return [
        ReadTagOut(
            id=row.sync_id,
            name=row.name or "",
            color=row.color,
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


