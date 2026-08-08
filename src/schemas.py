import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# 6 位 hex，开头必须有 #；字母大小写都接受，validator 会归一化成大写。
_HEX6_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

MemberRole = Literal["owner", "editor", "viewer"]
SyncAction = Literal["upsert", "delete"]


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthLoginRequest(BaseModel):
    email: str
    password: str
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthRefreshRequest(BaseModel):
    refresh_token: str


class AuthLogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool = False


class UserProfileOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0
    # 对齐 mobile `incomeExpenseColorSchemeProvider`。Nullable = 未设置过，web
    # 视为默认（红色收入）。
    income_is_red: bool | None = None
    # 主题色 hex（#RRGGBB），mobile 设置后推上来；web 把它当作"初始偏好"，
    # 用户在 web 本地改色会写 localStorage 优先生效。
    theme_primary_color: str | None = None
    # 外观类设置 JSON 对象（解析后的 dict）。mobile 推上来，web 只读展示。
    # 目前约定的 key：
    #   header_decoration_style (str) / compact_amount (bool) / show_transaction_time (bool)
    # 将来加新 key 不需要加 schema 字段。None = 没设置过。
    appearance: dict | None = None
    # AI 配置 JSON 对象。mobile 推上来,web 只读展示,另一台 mobile 设备也会拉。
    # key: providers (list) / binding (dict) / custom_prompt (str) /
    # strategy (str) / bill_extraction_enabled (bool) / use_vision (bool)
    ai_config: dict | None = None
    # 用户主币种,ISO 4217 大写代码(如 CNY / USD / JPY)。None = 未设置。
    primary_currency: str | None = None


class UserProfilePatchRequest(BaseModel):
    # 所有字段都可选：mobile 改配色时只送 `income_is_red`，web 改昵称时只送
    # `display_name`。handler 只更新非 None 字段。
    display_name: str | None = None
    income_is_red: bool | None = None
    theme_primary_color: str | None = None
    appearance: dict | None = None
    ai_config: dict | None = None
    primary_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3,8}$")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name cannot be empty")
        if len(normalized) > 32:
            raise ValueError("Display name too long")
        return normalized

    @field_validator("theme_primary_color")
    @classmethod
    def validate_theme_primary_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        # 只接受 #RRGGBB 格式；太宽松会被当任意文本写入
        if not _HEX6_PATTERN.match(normalized):
            raise ValueError("theme_primary_color must be #RRGGBB hex")
        return normalized


class UserProfileAvatarUploadOut(BaseModel):
    avatar_url: str
    avatar_version: int


class AuthTokenResponse(BaseModel):
    user: UserOut
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: str
    scopes: list[str] = Field(default_factory=list)


class AuthLoginResponse(BaseModel):
    """统一登录响应:requires_2fa=False 时直接是 token,True 时返回 challenge。

    设计思路:为了兼容老 App / 老 Web 客户端(只读 access_token 等字段),
    所有字段都做成 Optional;新客户端先看 requires_2fa 字段决定走哪条分支。
    """

    requires_2fa: bool = False

    # 2FA 未启用 / 已通过验证时填这些(等价老 AuthTokenResponse):
    user: UserOut | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    device_id: str | None = None
    scopes: list[str] = Field(default_factory=list)

    # 2FA 启用且未通过验证时填这些:
    challenge_token: str | None = None
    available_methods: list[str] = Field(default_factory=list)


class TwoFASetupResponse(BaseModel):
    """启用 2FA 第一步:server 生成 secret,客户端拿去画 QR / 手输。"""

    secret: str  # base32,Web 端可手动输入到 authenticator
    qr_code_uri: str  # otpauth://...,Web 端用 qrcode 库渲染成图片
    expires_in: int = 300  # 5 分钟内未 confirm 则 secret 仍可被覆盖重来


class TwoFAConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)  # 允许带空格


class TwoFAConfirmResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]  # 仅在这一刻明文返回,服务器只存 sha256


class TwoFAStatusResponse(BaseModel):
    enabled: bool
    enabled_at: datetime | None = None


class TwoFAVerifyRequest(BaseModel):
    challenge_token: str
    method: Literal["totp", "recovery_code"] = "totp"
    code: str
    # 登录时这些跟 login 一致,verify 通过后调 _issue_tokens 用
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"


class TwoFADisableRequest(BaseModel):
    password: str
    code: str  # TOTP 6 位码,确认本人操作


class TwoFARegenerateRequest(BaseModel):
    code: str  # 当前 TOTP 6 位码


class TwoFARegenerateResponse(BaseModel):
    recovery_codes: list[str]


class DeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    last_seen_at: datetime
    created_at: datetime
    session_count: int = 1


class SyncChangeIn(BaseModel):
    # user-global change(category/account/tag)在新协议下不依附 ledger,这里
    # 允许 None。老 mobile 会发当前 ledger_id —— server 按 entity_type 强制
    # 路由(参考 .docs/user-global-refactor/plan.md §3.2),不依赖 client 一定
    # 填对。
    ledger_id: str | None = None
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    # 'user' = category/account/tag 等 user-global 资源(server 端 SyncChange.scope)
    # 'ledger' = budget/transaction/ledger 等 ledger-scoped
    # 老 mobile 不发该字段;server 兜底按 entity_type 推断,不依赖 client 一定填对。
    scope: str | None = None


class SyncPushRequest(BaseModel):
    device_id: str
    changes: list[SyncChangeIn]


class SyncPushResponse(BaseModel):
    accepted: int
    rejected: int
    conflict_count: int = 0
    conflict_samples: list[dict[str, Any]] = Field(default_factory=list)
    server_cursor: int
    server_timestamp: datetime


class SyncChangeOut(BaseModel):
    change_id: int
    # user-scope change(scope='user')的 ledger_id 是 sentinel '__user_global__';
    # ledger-scope 是真实账本 external_id。mobile 按 scope 字段决定 apply 路径,
    # ledger_id 仅做日志 / cursor 标识。
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    updated_by_device_id: str | None
    # 'user' / 'ledger'。SyncChange.scope 直接 round-trip。
    scope: str = "ledger"


class SyncPullResponse(BaseModel):
    changes: list[SyncChangeOut]
    server_cursor: int
    has_more: bool


class SyncFullResponse(BaseModel):
    ledger_id: str
    snapshot: SyncChangeOut | None
    latest_cursor: int


class SyncLedgerOut(BaseModel):
    ledger_id: str
    path: str
    updated_at: datetime | None
    size: int
    metadata: dict[str, Any]
    role: MemberRole


BackupArtifactKind = Literal["db", "snapshot"]


class AdminBackupCreateRequest(BaseModel):
    ledger_id: str
    note: str | None = None


class AdminBackupCreateResponse(BaseModel):
    snapshot_id: str
    ledger_id: str
    created_at: datetime


class AdminBackupRestoreRequest(BaseModel):
    snapshot_id: str
    device_id: str | None = None


class AdminBackupRestoreResponse(BaseModel):
    restored: bool
    ledger_id: str
    change_id: int


class AdminBackupUploadSnapshotRequest(BaseModel):
    ledger_id: str
    payload: dict[str, Any]
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactOut(BaseModel):
    id: str
    ledger_id: str
    kind: BackupArtifactKind
    file_name: str
    content_type: str | None
    checksum: str
    size: int
    created_at: datetime
    created_by: str
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactUploadResponse(AdminBackupArtifactOut):
    snapshot_id: str | None = None


class UserAdminOut(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_enabled: bool
    created_at: datetime
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0


class UserAdminListOut(BaseModel):
    total: int
    items: list[UserAdminOut]


class UserAdminPatchRequest(BaseModel):
    # 允许改邮箱 / 启用状态。角色(is_admin)不在这里 —— 建用户时定好后
    # 就不能在 UI 改,想变更只能走 `make grant-admin EMAIL=` 之类的运维路径。
    # 密码改走独立端点 POST /admin/users/{id}/password,需要管理员自己的
    # 当前密码二次验证,避免 PATCH 这种"顺手改一下"造成密码误改。
    email: str | None = None
    is_enabled: bool | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class UserAdminPasswordChangeRequest(BaseModel):
    """修改目标用户密码。admin_password 是**当前操作 admin 自己的**密码,
    用于二次验证 —— 防止 session 被挟持或 UI 误操作把别人密码改掉。
    new_password 至少 6 位,跟 register / create-user 对齐。"""

    admin_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


class UserAdminCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    is_admin: bool = False
    is_enabled: bool = True

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AdminOverviewOut(BaseModel):
    users_total: int
    users_enabled_total: int
    ledgers_total: int
    transactions_total: int
    accounts_total: int
    categories_total: int
    tags_total: int


class AdminLogEntryOut(BaseModel):
    """Ring buffer 一条日志;字段对应 RingBufferLogHandler.emit 的 dict。"""

    seq: int
    ts: str
    level: str
    logger: str
    message: str
    ledger_id: str | None = None
    user_id: str | None = None
    device_id: str | None = None


class AdminLogListOut(BaseModel):
    items: list[AdminLogEntryOut]
    capacity: int
    latest_seq: int


# ────────── 数据清理(替代旧 IntegrityScan)─────────────────────────


class DataCleanupRequest(BaseModel):
    """POST /admin/data-cleanup/clean 请求体 — records 直接来自 scan 接口。"""

    records: list["DataCleanupRecord"]


class DataCleanupResult(BaseModel):
    success_count: int
    failures: list["DataCleanupFailure"] = []


class DataCleanupFailure(BaseModel):
    record_key: str
    error: str


class DataCleanupRecord(BaseModel):
    """单条孤儿数据 — 直接复用 services.data_cleanup.OrphanRecord 形态,但作为
    schema 出现避免 router 依赖 services 类型。"""

    type: str  # OrphanType 枚举字符串值
    title: str
    subtitle: str
    user_id: str | None = None
    row_id: str | None = None
    sync_id: str | None = None
    file_path: str | None = None
    size_bytes: int | None = None
    extra: dict[str, Any] | None = None


class DataCleanupScanReport(BaseModel):
    db_orphans: list[DataCleanupRecord] = []
    file_orphans: list[DataCleanupRecord] = []
    sync_orphans: list[DataCleanupRecord] = []
    total_count: int = 0
    total_size_bytes: int = 0


DataCleanupRequest.model_rebuild()
DataCleanupResult.model_rebuild()


class ReadLedgerOut(BaseModel):
    ledger_id: str
    ledger_name: str
    currency: str
    month_start_day: int = 1
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    exported_at: datetime | None
    updated_at: datetime
    role: MemberRole
    is_shared: bool = False
    member_count: int = 1


class ReadLedgerDetailOut(ReadLedgerOut):
    source_change_id: int


class ReadTxRefundSummaryOut(BaseModel):
    """§2.12.3:某笔支出收到的单笔退款摘要,给交易明细页"已退款金额 + 退款
    交易清单"用。"""
    id: str
    amount: float
    happened_at: datetime


class ReadTxSplitOut(BaseModel):
    """拆帳(§2.4):一笔交易拆到某个分类下的明细行,给交易明细页/编辑表单
    回显用。"""
    category_id: str | None = None
    category_name: str | None = None
    amount: float
    note: str | None = None
    sort_order: int = 0


DebtDirection = Literal["payable", "receivable"]
# "closed" = 手動結案(closed_at 非空),優先權蓋過其它三種從 remaining_amount
# 算出來的狀態 —— 不代表已還清全額,可能少還一點就結案。
DebtStatus = Literal["open", "partial", "settled", "closed"]


class ReadTransactionOut(BaseModel):
    id: str
    tx_index: int
    tx_type: str
    amount: float
    happened_at: datetime
    note: str | None
    # 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。
    merchant: str | None = None
    category_name: str | None
    category_kind: str | None
    account_name: str | None
    from_account_name: str | None
    to_account_name: str | None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | None
    tags_list: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] | None
    # 账单标记(.docs/transaction-flags)。exclude_from_stats=不计入收支统计;
    # exclude_from_budget=不计入预算用量。两者独立,旧数据 default False。
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    # 交易级多币种(0018):currency_code=原币种(null 视作账本本位币);
    # native_amount=折账本本位币快照(null 时前端 fallback 用 amount)。
    currency_code: str | None = None
    native_amount: float | None = None
    # 手續費/折扣(2026-08 使用者需求):base_amount=使用者輸入的原始金額
    # (null=沒用過這個功能,前端 fallback 用 amount);fee_amount/
    # discount_amount=額外調整金額;fee_label/discount_label=自訂名稱
    # (null=前端顯示預設「手續費」「折扣」)。
    base_amount: float | None = None
    fee_amount: float | None = None
    fee_label: str | None = None
    discount_amount: float | None = None
    discount_label: str | None = None
    # 退款(§2.6):指向被退款那笔支出的 sync_id;None = 普通交易。
    refund_of_id: str | None = None
    # 分期付款(§2.3):指向所属分期计划的 sync_id;None = 非分期生成的交易。
    installment_plan_id: str | None = None
    # 週期性收支(§2.12.2 Phase 1.5):指向所属规则的 sync_id;None = 非规则
    # 生成的交易。recurring_occurrence_overridden=True 表示这笔已被单独编辑
    # 过,规则批次更新/视窗续产生不会再覆盖它。
    recurring_rule_id: str | None = None
    recurring_occurrence_overridden: bool = False
    # 退款反查(§2.12.3):这笔支出收到过哪些退款,空列表 = 没有退款。
    refunds: list["ReadTxRefundSummaryOut"] = Field(default_factory=list)
    # 借還款追蹤(§2.5 Phase 3):指向这笔交易关联的欠款 sync_id;None = 普通
    # 交易。debt_counterparty_name/debt_direction 是反查这笔欠款拿到的展示
    # 字段(对齐 category_id+category_name 的既有惯例),讓前端不用額外查表
    # 就能直接顯示欠款資訊。
    debt_id: str | None = None
    debt_counterparty_name: str | None = None
    debt_direction: DebtDirection | None = None
    # 拆帳(§2.4):has_splits=True 时 category_id/category_name 为 None(前端
    # 显示"多分类"),明细在 splits;False 时 splits 是空列表,走原本单分类显示。
    has_splits: bool = False
    splits: list["ReadTxSplitOut"] = Field(default_factory=list)
    # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走哪
    # 幾條回饋規則的 sync_id 列表;空列表 = 没有勾选任何规则。
    reward_rule_ids: list[str] = Field(default_factory=list)
    # 信用卡紅利回饋自動入帳(§2.9.5.4 補強):有值 = 这笔交易是逐笔结算规则
    # 自动产生的回饋 income,反查它对应的原始消费交易 sync_id;None = 普通
    # 交易,或 period_end/manual 這種不對應單一原始交易的回饋。
    reward_source_tx_id: str | None = None
    # 延後入帳(§2.10 Phase 5):有值 = 實際入帳日跟 happened_at 不同,對帳/
    # 信用卡帳單彙總按這個日期歸屬期別;None = 正常交易。
    deferred_posting_at: datetime | None = None
    # 對帳模式(§2.10,2026-08-09 改版):有值 = 這筆交易已經在對帳模式裡被
    # 使用者勾選確認過;None = 尚未核對。
    reconciled_at: datetime | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    created_by_display_name: str | None = None
    created_by_avatar_url: str | None = None
    created_by_avatar_version: int | None = None
    # §7 共享账本:tx 创建/编辑分离显示 — last_edited 跟 created 不同时,UI
    # 显示 "X 创建 · Y 编辑";相同时只显示创建者。
    last_edited_by_user_id: str | None = None
    last_edited_by_email: str | None = None
    last_edited_by_display_name: str | None = None
    last_edited_by_avatar_url: str | None = None
    last_edited_by_avatar_version: int | None = None


class ReadAccountOut(BaseModel):
    id: str
    name: str
    account_type: str | None
    currency: str | None
    initial_balance: float | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    # 扩展字段(mobile sync_engine 一直在 push 这些,server 现在落库 + round-trip,
    # web 编辑也能完整保存):
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = None
    payment_due_day: int | None = None
    bank_name: str | None = None
    card_last_four: str | None = None
    # 主帳戶(合併帳單,§2.9 Phase 4):子卡的 sync_id 指向主卡,None=沒有主卡。
    parent_account_id: str | None = None
    # 账户隐藏(issue #240):只影响前端选择器/列表呈现,服务端不做任何统计
    # 过滤(D1)。WorkspaceAccountOut 继承本字段,不单独重复声明。
    hidden: bool = False
    # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶 sync_id。只在
    # is_billing_root(account_group,或沒有掛靠任何群組的獨立信用卡)且已設
    # billing_day/payment_due_day 時才會被 materializer 實際處理,見
    # `services.credit_card_autopay`。
    auto_pay_enabled: bool = False
    auto_pay_from_account_id: str | None = None
    # 帳戶頭像(2026-08-02 補強):`AttachmentFile.id`,None = 沒有自訂頭像。
    avatar_cloud_file_id: str | None = None
    avatar_cloud_sha256: str | None = None


class ReadAccountBillingMemberOut(BaseModel):
    """合併帳單(§2.9 Phase 4)裡單一成員帳戶(主卡自己或某張子卡)在當期
    帳單裡的貢獻金額。`period_new_spend`/`remaining_due`(§2.9.6 Phase 7,
    2026-08-07 使用者反饋補上)是這個成員**自己**的數字,供子卡詳情頁顯示
    「自己的」金額,不用再借用整組合併數字——`period_new_spend` 對齊
    `period_cycle_start`~`period_cycle_end` 這期窗口,`remaining_due` 是這
    張卡自己的終身跑動餘額(下限 0,溢繳算在整組層級,不會讓單卡顯示負數)。"""
    account_id: str
    account_name: str
    cycle_spend: float
    period_new_spend: float = 0.0
    remaining_due: float = 0.0


class ReadAccountBillingSummaryOut(BaseModel):
    """信用卡合併帳單摘要(§2.9 Phase 4)。`account_id` 是主卡,`members`
    包含主卡自己 + 所有 `parent_account_id == account_id` 的子卡。`billing_
    day`/`payment_due_day` 一律使用主卡自己的設定 —— 子卡沿用主卡的結帳
    週期,不落庫,每次讀取即時計算(跟 §2.5 借還款 remaining_amount 同一個
    "不落表、讀路徑即時加總"取捨)。"""
    account_id: str
    account_name: str
    billing_day: int
    payment_due_day: int
    member_account_ids: list[str]
    members: list[ReadAccountBillingMemberOut]
    # 最近一次已結束的帳單週期(目前應繳金額對應的那一期)。
    cycle_start: datetime
    cycle_end: datetime
    due_date: datetime
    statement_amount: float
    paid_amount: float
    remaining_due: float
    # 目前還在累積、尚未結束的下一期帳單。
    open_cycle_start: datetime
    open_cycle_end: datetime
    open_cycle_due_date: datetime
    open_cycle_spend: float
    # 可用額度(2026-08-02 補):純顯示計算,不落庫。溢繳時 remaining_due 可
    # 為負,available_credit 會自然超過 credit_limit 本身,不需要另外調整
    # credit_limit 欄位。credit_limit 未設定時整組為 None。
    credit_limit: float | None = None
    available_credit: float | None = None
    # 帳單週期瀏覽(§2.9 補強,2026-08-02):對應 request 的 `cycle_offset`
    # query param,`0` 是「最近一次已結束」的週期(跟上面 cycle_start/
    # cycle_end 是同一期),負數是更早的歷史週期,`+1` 是目前還在累積的那期
    # (跟 open_cycle_* 是同一期)。跟上面「現在當下」欄位是兩組獨立資訊,
    # 上面那組永遠反映「此刻」,不受 cycle_offset 影響。
    period_cycle_start: datetime
    period_cycle_end: datetime
    period_due_date: datetime
    period_new_spend: float
    period_carryover_due: float
    period_total_due: float
    period_paid_in_cycle: float
    period_remaining_due: float
    period_has_older: bool
    period_has_newer: bool
    # 帳單分期(2026-08-04 使用者反饋補上,對齊 Moze 參考截圖)。0 = 沒有
    # 進行中的分期(前端顯示「---」);1 = 附帶 paid_periods/periods 顯示
    # 進度;>1 = 只顯示筆數,見 credit_card_billing.compute_installment_
    # summary docstring。
    period_installment_active_count: int
    period_installment_paid_periods: int | None = None
    period_installment_periods: int | None = None


class ReadInterestFreeSuggestionOut(BaseModel):
    """信用卡免息期推薦(§2.9 Phase 4)。純計算,不查交易 —— 只依賴帳戶自己
    的 `billing_day`/`payment_due_day`。"""
    account_id: str
    as_of: datetime
    billing_day: int
    payment_due_day: int
    current_cycle_start: datetime
    current_cycle_end: datetime
    current_cycle_due_date: datetime
    next_cycle_start: datetime
    next_cycle_end: datetime
    next_cycle_due_date: datetime
    recommended_purchase_after: datetime
    min_interest_free_days: int
    max_interest_free_days: int


CardRewardRateType = Literal["percentage", "fixed_amount"]
# Phase 8 #4(2026-08 使用者反饋):新增 "keep"(保留小數,不取整)。
# rounding = 單筆取整方式;total_rounding(見下方)= 總額取整方式,兩者共用
# 同一組合法值。
CardRewardRounding = Literal["floor", "round", "ceil", "keep"]
# settlement_date 目前行为等同 transaction_date —— §2.10 延後入帳
# (deferred_posting_at)还没实作,见 services/card_rewards.py 的
# _attribution_date docstring。
CardRewardCalcBasis = Literal["transaction_date", "settlement_date"]
CardRewardInterval = Literal["billing_cycle", "calendar_month"]
CardRewardRuleStatus = Literal["ok", "no_billing_schedule", "expired"]
# 自動入帳(§2.9.5.4):manual = 純顯示不自動化;immediate_after_tx/
# after_posting_date 逐筆結算;period_end 整期結束後一次結算。見
# src/services/card_reward_payout.py。
CardRewardSettlementType = Literal[
    "immediate_after_tx", "after_posting_date", "period_end", "manual"
]


class ReadCardRewardRuleOut(BaseModel):
    """信用卡紅利回饋規則只读视图(§2.9.5 Phase 4.5)。"""
    id: str
    account_id: str
    label: str
    category_ids: list[str] | None = None
    rate_type: CardRewardRateType
    rate_value: float
    rounding: CardRewardRounding
    total_rounding: CardRewardRounding = "round"
    calc_basis: CardRewardCalcBasis
    interval: CardRewardInterval
    min_spend_threshold: float | None = None
    min_tx_amount: float | None = None
    cap_amount: float | None = None
    cap_shared_key: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    settlement_type: CardRewardSettlementType = "manual"
    settlement_days: int | None = None
    settlement_month_offset: int | None = None
    settlement_day_of_month: int | None = None
    reward_account_id: str | None = None
    note: str | None = None
    enabled: bool = True
    # Phase 8 #16(2026-08 使用者反饋):規則已有交易掛著或已有自動入帳紀錄
    # 時鎖定(True)——計算相關欄位不能再改,只能調整 label/note/enabled/
    # starts_at/ends_at,前端據此提前 disable 欄位(不用等 PATCH 422 才知道)。
    locked: bool = False
    last_change_id: int


class ReadCardRewardQualifyingTxOut(BaseModel):
    """單筆符合條件交易明細(§2.9.5.3 交易明細彈窗)。

    2026-08 使用者反饋(對帳明細可編輯回饋金額):`payout_tx_id` 是這筆消費
    實際結算入帳的回饋交易 sync_id——只有逐筆結算(`immediate_after_tx`/
    `after_posting_date`)且已經到期入帳的項目才有值;`period_end`(整期
    一次性入帳,沒有逐筆對應)或還沒到入帳日的項目固定 `None`,前端據此決定
    要不要顯示「編輯這筆回饋金額」的按鈕。有 `payout_tx_id` 時,`reward_amount`
    改用該筆交易目前實際的金額(不是重新按公式算出來的)——一旦使用者透過
    這個欄位編輯過金額,之後每次打開明細都要看到編輯後的實際值,不能被重算
    蓋回去(對齊 `card_reward_payout.reverse_card_reward_payouts_for_refund`
    docstring 同一個「實際落袋的錢才是唯一權威來源」原則)。"""
    tx_id: str
    happened_at: datetime
    amount: float
    note: str | None = None
    category_name: str | None = None
    reward_amount: float
    settlement_date: datetime | None = None
    payout_tx_id: str | None = None


class ReadCardRewardRuleTransactionsOut(BaseModel):
    """`GET .../card-reward-rules/{rule_id}/transactions` 返回(§2.9.5.3)。
    `remaining_reward_room` 是這條規則所屬共用上限群組(跨卡,見
    `services.card_rewards.fetch_cap_group_rules`)的剩餘額度,`None` =
    無上限。"""
    rule_id: str
    label: str
    period_start: datetime
    period_end: datetime
    status: CardRewardRuleStatus = "ok"
    qualifying_spend: float
    raw_reward: float
    capped_reward: float
    cap_amount: float | None = None
    cap_shared_key: str | None = None
    remaining_reward_room: float | None = None
    items: list[ReadCardRewardQualifyingTxOut] = Field(default_factory=list)


class ReadCardRewardRuleUsageOut(BaseModel):
    """單條規則在指定期間的計算結果(§2.9.5)。回饋金額不落庫,每次讀取
    即時從交易加總算出,見 `services.card_rewards` docstring。`status` !=
    "ok" 时 qualifying_spend/raw_reward/capped_reward 固定为 0。"""
    rule_id: str
    label: str
    period_start: datetime
    period_end: datetime
    qualifying_spend: float
    threshold_met: bool
    raw_reward: float
    capped_reward: float
    cap_amount: float | None = None
    cap_shared_key: str | None = None
    status: CardRewardRuleStatus = "ok"


class ReadCardRewardsOut(BaseModel):
    """`GET .../accounts/{account_id}/card-rewards` 返回。"""
    account_id: str
    as_of: datetime
    items: list[ReadCardRewardRuleUsageOut] = Field(default_factory=list)
    total_reward: float


class ReadCategoryOut(BaseModel):
    id: str
    name: str
    kind: str
    level: int | None
    sort_order: int | None
    icon: str | None
    icon_type: str | None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadTagOut(BaseModel):
    id: str
    name: str
    color: str | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadBudgetOut(BaseModel):
    """预算只读视图。mobile 同步上来的 snapshot.budgets 逐条 map 过来。
    category_name 不进 snapshot,这里从 categoryId 反查填上,跟 tx/account
    同一套 id→name 映射思路。"""
    id: str
    """`total` = 总预算(全账本),`category` = 分类预算"""
    type: str
    category_id: str | None = None
    category_name: str | None = None
    amount: float
    """`monthly` / `weekly` / `yearly`"""
    period: str
    start_day: int
    enabled: bool
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadBudgetUsageItemOut(BaseModel):
    """单个 budget 当前周期的已用金额。分类预算的 used 包含该分类自身 + 所有
    parent_sync_id 指向它的子分类支出(跟手机端 local_budget_repository 的
    OR c.parent_id = ? 语义对齐)。"""
    budget_id: str
    used: float


class ReadBudgetUsageOut(BaseModel):
    """`/ledgers/{id}/budgets/usage` 返回。周期窗口统一取账本 month_start_day
    (设计 D5:budget.start_day 弃用,所有 budget 共享同一周期),前端只用 used 数字。"""
    items: list[ReadBudgetUsageItemOut] = Field(default_factory=list)


RecurringFrequency = Literal["daily", "weekly", "monthly", "yearly"]


class ReadRecurringRuleOut(BaseModel):
    """週期性收支规则只读视图(§2.2)。"""
    id: str
    tx_type: str
    amount: float
    note: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    frequency: RecurringFrequency
    interval: int
    next_run_at: datetime
    end_at: datetime | None = None
    enabled: bool
    # Phase 1.5(§2.12.2):视窗续产生进度 + 进阶规则,None = 未设置/用简单
    # frequency+interval。
    generated_until_at: datetime | None = None
    advanced_rule_json: dict[str, Any] | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


InstallmentPlanStatus = Literal["active", "settled", "terminated"]
InstallmentRepaymentMethod = Literal["equal_installment", "equal_principal", "fixed_interest"]
InstallmentInterestPeriod = Literal["monthly", "daily"]
InstallmentRemainderPosition = Literal["first", "last"]


class ReadInstallmentPlanOut(BaseModel):
    """分期付款计划只读视图(§2.3 / §2.12.1)。"""
    id: str
    total_amount: float
    periods: int
    period_amount: float
    first_period_at: datetime
    next_period_at: datetime
    paid_periods: int
    account_id: str | None = None
    category_id: str | None = None
    note: str | None = None
    status: InstallmentPlanStatus
    repayment_method: InstallmentRepaymentMethod = "equal_principal"
    interest_period: InstallmentInterestPeriod = "monthly"
    interest_rate: float = 0.0
    round_amounts: bool = True
    remainder_position: InstallmentRemainderPosition = "last"
    grace_period_months: int = 0
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadInstallmentPeriodOut(BaseModel):
    """分期单期明细只读视图(§2.12.1 Phase 1.5 新增)。"""
    id: str
    plan_id: str
    period_no: int
    due_at: datetime
    principal_amount: float
    interest_amount: float
    total_amount: float
    status: Literal["pending", "generated", "overridden", "refunded"]
    tx_id: str | None = None
    # 单期退款(§2.6/§2.12.1):反查指向本期 tx_id 的退款交易。None = 未退款。
    refund_tx_id: str | None = None
    refund_amount: float | None = None
    refunded_at: datetime | None = None


class ReadDebtRepaymentOut(BaseModel):
    """借還款追蹤(§2.5 Phase 3):某笔欠款收到的一笔还款/收款摘要,给詳情頁
    「還款記錄」清單用,跟 §2.12.3 的 ReadTxRefundSummaryOut 是同一种模式。"""
    id: str
    amount: float
    happened_at: datetime


class ReadDebtOut(BaseModel):
    """借還款追蹤只读视图(§2.5)。`remaining_amount`/`status` 不落库,是
    读路径从反查交易即时算出的 derived 字段(见
    `ReadDebtProjection`/`upsert_debt` docstring)。"""
    id: str
    direction: DebtDirection
    counterparty_name: str
    principal_amount: float
    remaining_amount: float
    status: DebtStatus
    due_at: datetime | None = None
    note: str | None = None
    repayments: list["ReadDebtRepaymentOut"] = Field(default_factory=list)
    # 結案(體驗補強):非空 = 已手動標記結束。
    closed_at: datetime | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadTxTemplateOut(BaseModel):
    """交易範本只读视图(§2.7)。"""
    id: str
    name: str
    tx_type: str
    amount: float
    note: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    from_account_id: str | None = None
    from_account_name: str | None = None
    to_account_id: str | None = None
    to_account_name: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class StatementTransactionOut(BaseModel):
    """對帳模式(§2.10 Phase 5,2026-08-09 改版為 Moze 式逐筆核對清單)裡
    「這期帳單」列表的一行。`account_id`/`account_name` 是這筆交易實際掛的
    那張卡(account_group 場景下用來分組顯示,對齊 Moze 參考文件「依卡分組」
    的行為),不一定等於查詢用的 `account_id`(可能是群組本身)——`tx_type`
    為 `transfer` 時,這兩欄改填「轉入的那張卡」(`to_account_sync_id`/
    `to_account_name`),因為轉帳交易本身沒有 `account_sync_id`(Phase 6,
    docs/PH6_USER_FEEDBACK_2026-08_SD.md)。`is_reward`=True 代表這筆交易的
    分類是系統紅利回饋專屬分類(`services.card_rewards.REWARD_CATEGORY_NAME`),
    供前端顯示「回饋」標籤辨識來源。

    2026-08 使用者反饋(合併回饋方案顯示):同一個回饋方案(rule)在這期帳單
    內的所有回饋入帳交易,合併成一行顯示總金額,不逐筆列出——`reward_rule_id`
    非空時,這一行是合併後的「代表列」,`amount` 是這個方案在這期內所有回饋
    交易的加總,`member_tx_ids` 是被合併的原始回饋交易 sync_id 清單(確認/
    延後入帳要對這清單裡每一筆各自呼叫既有的 `PATCH .../transactions/{id}`,
    不新增批次 write endpoint)。非回饋交易、或回饋交易查不到對應
    `CardRewardPayout`(例如手動記的回饋分類交易,不是系統自動入帳)時,
    `reward_rule_id`/`reward_rule_label` 為 None,`member_tx_ids` 只含自己
    這一筆,行為等同合併前(單筆顯示)。"""
    id: str
    account_id: str
    account_name: str | None = None
    tx_type: str
    amount: float
    category_name: str | None = None
    note: str | None = None
    happened_at: datetime
    deferred_posting_at: datetime | None = None
    reconciled_at: datetime | None = None
    is_reward: bool = False
    reward_rule_id: str | None = None
    reward_rule_label: str | None = None
    member_tx_ids: list[str] = Field(default_factory=list)


class StatementAccountTotalOut(BaseModel):
    """對帳模式:account_group 場景下,依卡分組的筆數/金額小計。"""
    account_id: str
    account_name: str | None = None
    count: int
    total: float


class StatementPeriodOut(BaseModel):
    """`GET .../accounts/{account_id}/statement`(對帳模式,§2.10 Phase 5,
    2026-08-09 改版):對齊 Moze `doc.moze.app/reconciliation/statement-mode`
    —— 進入對帳模式看到的是「這期帳單」的交易清單本身,不是輸入一個對帳單
    餘額數字去比對。每筆交易可以被勾選確認(`reconciled_at`,對應原文右滑
    「完成對帳確認」)或延後入帳(`deferred_posting_at`,對應左滑「延後入帳
    到下期帳單」,web 版用按鈕+日期選擇器取代滑動手勢)。`cycle_offset`
    語意跟 `credit_card_billing.compute_cycle_period_billing` 一致:0 = 最近
    一次已結束的週期,正數往未來翻,負數往過去翻。"""
    account_id: str
    account_name: str | None = None
    cycle_start: date
    cycle_end: date
    due_date: date
    cycle_offset: int
    has_older: bool
    has_newer: bool
    statement_count: int
    statement_total: float
    confirmed_count: int
    confirmed_total: float
    accounts: list[StatementAccountTotalOut]
    transactions: list[StatementTransactionOut]


class ComparisonReportMetricOut(BaseModel):
    """比較報表(§2.10 Phase 5)單一指標(income/expense/balance)的兩期
    對比,`diff = current - previous`,`diff_pct` 是相對前一期的變動百分比
    (前一期為 0 時回 None,避免除以零)。"""
    current: float
    previous: float
    diff: float
    diff_pct: float | None = None


class ComparisonReportOut(BaseModel):
    """`GET /workspace/comparison`:複用 `_analytics_range` 的區間計算邏輯 +
    `workspace_analytics` 核心聚合迴圈同款的 refund netting/拆帳展開規則,
    分別跑「當期」跟「比較期」兩次區間再算 diff,不需要新表——本身就是純
    衍生計算,見 `read/workspace.py::comparison_report` docstring。
    `offset` 決定比較期怎麼選:`scope=month` 時 `offset=1` 是上個月、
    `offset=12` 是去年同月(年比年);`scope=year` 時 `offset=1` 是去年。"""
    scope: Literal["month", "year"]
    offset: int
    current_period_start: datetime
    current_period_end: datetime
    previous_period_start: datetime
    previous_period_end: datetime
    income: ComparisonReportMetricOut
    expense: ComparisonReportMetricOut
    balance: ComparisonReportMetricOut
    category_breakdown: list["ComparisonCategoryBreakdownItemOut"] = Field(default_factory=list)


class ComparisonCategoryBreakdownItemOut(BaseModel):
    category_id: str | None = None
    category_name: str
    category_kind: str
    current: float
    previous: float
    diff: float


class WorkspaceTransactionOut(ReadTransactionOut):
    pass


class WorkspaceTransactionPageOut(BaseModel):
    items: list[WorkspaceTransactionOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class WorkspaceAccountOut(ReadAccountOut):
    # 跨 workspace 对该账户聚合后的统计，列表接口一次性给，无需前端再聚合。
    # balance 包含 initialBalance + (income - expense)；income/expense 只统计本
    # 账户作为 accountId 的收支条目（不含 transfer 的对手方）。
    tx_count: int | None = None
    # 「可繳款」提醒(§2.9 補強,2026-08-02):只在 billing-root(account_group
    # 或沒有掛靠任何群組的獨立信用卡)且有應繳金額(> 0)時才有值,讓帳戶列表
    # 卡片不用等使用者點開詳情就能顯示「可繳款 截止日 X/X」。
    billing_due_date: datetime | None = None
    billing_remaining_due: float | None = None
    income_total: float | None = None
    expense_total: float | None = None
    balance: float | None = None


class WorkspaceCategoryOut(ReadCategoryOut):
    # 跨账本按该分类聚合的笔数。Web 列表展示用,跟 tags 的 tx_count 对齐。
    # 不带 expense/income total — 分类本身已经按 kind 区分(支出/收入),
    # 累计金额可在分类详情页另行查询。None = 历史接口可选不提供。
    tx_count: int | None = None


class WorkspaceTagOut(ReadTagOut):
    # 跨所有账本按该标签聚合的交易统计，列表接口一次性给。
    # 全部 None = list_workspace_tags 可选择不提供（legacy 调用）。
    tx_count: int | None = None
    expense_total: float | None = None
    income_total: float | None = None


AnalyticsScope = Literal["month", "year", "all"]
AnalyticsMetric = Literal["expense", "income", "balance"]


class WorkspaceLedgerCountsOut(BaseModel):
    """单账本全量记账统计，对齐 mobile `getCountsForLedger`：笔数 + 首次记账到
    今天的天数（`julianday(now) - julianday(MIN(happened_at)) + 1`）+ 有数据的天数
    （distinct DATE，备用）。首页"记账笔数 / 记账天数"读这里，不依赖 analytics scope。"""

    tx_count: int
    # "记账天数"：从首次记账那天算到今天（含当天），对应 mobile 的 dayCount。
    days_since_first_tx: int
    # 有数据的天数：只计入有 tx 的日期数，保留给别处使用。
    distinct_days: int
    first_tx_at: datetime | None = None


class WorkspaceAnalyticsSummaryOut(BaseModel):
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    # 记账天数：distinct(DATE(happened_at))。前端首页用来做"已记账 X 天"卡片。
    distinct_days: int = 0
    # 首次记账时间：min(happened_at)。配合 distinct_days 算"持续记账时长"。
    first_tx_at: datetime | None = None
    last_tx_at: datetime | None = None


class WorkspaceAnalyticsSeriesItemOut(BaseModel):
    bucket: str
    expense: float
    income: float
    balance: float


class WorkspaceAnalyticsCategoryRankOut(BaseModel):
    category_name: str
    total: float
    tx_count: int


class WorkspaceAnalyticsAnomalyAttributionOut(BaseModel):
    """异常月份的归因 — 某分类在该月超出"该分类其他月份中位数"的部分。"""

    category_name: str
    amount: float  # 该分类在异常月的总支出
    # 该分类在其他月份的中位数(本月独有时为 0)
    median_others: float
    # amount / median_others;median_others=0(本月独有)时为 None,前端显示"本月独有"。
    multiplier: float | None = None


class WorkspaceAnalyticsAnomalyMonthOut(BaseModel):
    """异常月份 — expense 显著高于已发生月份的 baseline。算法见
    `.docs/dashboard-anomaly-budget/plan.md`:
      baseline = median(已发生月份的 expense)
      异常判定:expense > baseline × 1.2 AND expense - baseline > ¥200
    """

    bucket: str  # "2026-05"
    expense: float
    baseline: float
    # (expense - baseline) / baseline,前端展示百分比
    deviation_pct: float
    # top 1-2 个归因分类,按 diff 降序
    top_attributions: list[WorkspaceAnalyticsAnomalyAttributionOut] = Field(
        default_factory=list
    )


class WorkspaceAnalyticsRangeOut(BaseModel):
    scope: AnalyticsScope
    metric: AnalyticsMetric
    period: str | None
    start_at: datetime | None
    end_at: datetime | None


class WorkspaceAnalyticsOut(BaseModel):
    summary: WorkspaceAnalyticsSummaryOut
    series: list[WorkspaceAnalyticsSeriesItemOut] = Field(default_factory=list)
    category_ranks: list[WorkspaceAnalyticsCategoryRankOut] = Field(default_factory=list)
    # 仅在 scope=year 填;月份数 < 3 时返回空 list(baseline 不稳)。
    anomaly_months: list[WorkspaceAnalyticsAnomalyMonthOut] = Field(default_factory=list)
    range: WorkspaceAnalyticsRangeOut


class ReadSummaryOut(BaseModel):
    ledger_id: str
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    latest_happened_at: datetime | None


class WriteCommitMeta(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    idempotency_replayed: bool = False
    entity_id: str | None = None


class WriteBaseRequest(BaseModel):
    base_change_id: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=128)


class WriteLedgerCreateRequest(BaseModel):
    ledger_id: str | None = Field(default=None, min_length=3, max_length=128)
    ledger_name: str = Field(min_length=1, max_length=255)
    currency: str = Field(default="CNY", min_length=1, max_length=16)
    month_start_day: int = Field(default=1, ge=1, le=28)


class WriteLedgerMetaUpdateRequest(WriteBaseRequest):
    ledger_name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=1, max_length=16)
    month_start_day: int | None = Field(default=None, ge=1, le=28)


class WriteTransactionRecurringInline(BaseModel):
    """§2.12.2:挂在 `WriteTransactionCreateRequest.recurring` 上的週期参数。
    交易本身的 tx_type/amount/category/account 就是規則的內容,不重複填。"""
    frequency: RecurringFrequency = "monthly"
    interval: int = Field(default=1, ge=1, le=365)
    end_at: datetime | None = None
    advanced_rule_json: dict[str, Any] | None = None


class WriteTxSplitItem(BaseModel):
    """拆帳(§2.4):挂在 `WriteTransactionCreateRequest`/`UpdateRequest.splits`
    上的单个分类明细。`amount` 是这个分类分到的金额,所有明细项加总必须等于
    交易本身的 amount(server 端校验,见 write/_shared.py `_validate_tx_splits`)。"""
    category_id: str = Field(min_length=1)
    category_name: str | None = None
    amount: float = Field(gt=0)
    note: str | None = None


class WriteTransactionCreateRequest(WriteBaseRequest):
    # 餘額調整(§2.10 Phase 5):`adjustment` 一般不建议直接传 amount 手填,
    # 而是走 `POST .../accounts/{account_id}/balance-adjustment` 语意化端点
    # (由 server 算出 amount = target_balance - 当下余额)。这里仍然把它
    # 加进 Literal——sync entity 必须支持任意合法值被写入/回放,且未来
    # mobile 端可能有自己的直接创建路径。
    tx_type: Literal["expense", "income", "transfer", "adjustment"] = "expense"
    amount: float
    happened_at: datetime
    note: str | None = None
    # 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。
    merchant: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None
    # 账单标记(.docs/transaction-flags)。新建默认 False。
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    # 交易级多币种(0018):Web 币种录入。currency_code=原币种;native_amount=
    # 折账本本位币快照(前端按汇率算好传入)。不传 → item 不产生字段(旧行为)。
    currency_code: str | None = None
    native_amount: float | None = None
    # 手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
    # base_amount=使用者輸入的原始金額(信用卡回饋計算的權威基準);
    # fee_amount/discount_amount=額外調整金額;fee_label/discount_label=
    # 自訂名稱(None=用預設「手續費」「折扣」顯示)。不傳任一個 → 維持現行
    # 單一 amount 行為(向下相容)。server 端會依 tx_type 用這三者重新算出
    # 權威的 amount(write/_shared.py::_normalize_fee_discount_amount),
    # 傳入的 amount 只在完全沒用這個功能時才是最終值。
    base_amount: float | None = Field(default=None, ge=0)
    fee_amount: float | None = Field(default=None, ge=0)
    fee_label: str | None = None
    discount_amount: float | None = Field(default=None, ge=0)
    discount_label: str | None = None
    # 退款(§2.6):这笔交易是对 refund_of_id 那笔支出的退款。None = 普通交易。
    refund_of_id: str | None = None
    # Phase 1.5(§2.12.2):建交易当下顺便把它设成週期性收支的起点。None =
    # 普通交易。跟独立的 POST /recurring-rules 端点(事后设週期起点)并存。
    recurring: WriteTransactionRecurringInline | None = None
    # 拆帳(§2.4):不传/None = 维持现行单一 category 行为(向下相容)。传入时
    # 至少 2 笔、tx_type 只能是 expense/income、金额加总须等于 amount ——
    # 校验见 write/_shared.py `_validate_tx_splits`。
    splits: list["WriteTxSplitItem"] | None = None
    # 借還款追蹤(§2.5 Phase 3):这笔交易是对 debt_id 那笔欠款的一次还款/
    # 收款。None = 普通交易。debt_id 必须指向该账本下已存在的欠款
    # (write/_shared.py `_assert_debt_exists`),允许多笔部分还款。
    debt_id: str | None = None
    # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易要
    # 走哪幾條回饋規則(可複選)。None/不传 = 不挂任何规则。每个 id 必须指向
    # `account_id` 这张信用卡自己名下的规则(write/_shared.py
    # `_assert_reward_rules_valid`)。
    reward_rule_ids: list[str] | None = None
    # 延後入帳(§2.10 Phase 5):有值 = 這筆交易的實際入帳日跟消費日
    # (happened_at)不同,對帳/信用卡帳單彙總按這個日期歸屬期別。None =
    # 正常交易(不延後)。
    deferred_posting_at: datetime | None = None


class WriteTransactionUpdateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer", "adjustment"] | None = None
    amount: float | None = None
    happened_at: datetime | None = None
    note: str | None = None
    # 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。
    merchant: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None
    # 账单标记(.docs/transaction-flags)。None = 不变(沿用 update 其它字段语义)。
    exclude_from_stats: bool | None = None
    exclude_from_budget: bool | None = None
    # 交易级多币种(0018):显式传入优先(mutator 不再联动);None = 不变。
    currency_code: str | None = None
    native_amount: float | None = None
    # 手續費/折扣(2026-08 使用者需求):key 不出現 = 不變;傳 null = 清除該
    # 分量(關掉手續費/折扣功能);傳值 = 更新。server 端一律用最終三個分量
    # 重算 amount,見 write/_shared.py::_normalize_fee_discount_amount。
    base_amount: float | None = Field(default=None, ge=0)
    fee_amount: float | None = Field(default=None, ge=0)
    fee_label: str | None = None
    discount_amount: float | None = Field(default=None, ge=0)
    discount_label: str | None = None
    # 退款(§2.6):None = 不变。传空字符串清空关联(mutator 按空串处理成 null)。
    refund_of_id: str | None = None
    # 拆帳(§2.4):None(不传该 key)= 不变,沿用既有 splits(或维持无 splits)。
    # 传空列表 [] = 清空 splits,交易变回单一 category。传非空列表 = 整批替换
    # (delete-then-insert),同样要满足 create 的校验规则。
    splits: list["WriteTxSplitItem"] | None = None
    # 借還款追蹤(§2.5 Phase 3):None = 不变。传空字符串清空关联。
    debt_id: str | None = None
    # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):None(不传该 key)= 不变。
    # 传空列表 [] = 清空,传非空列表 = 整批替换。
    reward_rule_ids: list[str] | None = None
    # 延後入帳(§2.10 Phase 5):key 不出現 = 不變;傳 ISO 時間 = 設定/更新;
    # 傳 null = 清除延後入帳標記(跟 debt closed_at 同款「key 是否出現才
    # 決定要不要改」語意,用 exclude_unset dump)。
    deferred_posting_at: datetime | None = None
    # 對帳模式(§2.10,2026-08-09 改版):key 不出現 = 不變;傳 ISO 時間 =
    # 標記已在對帳模式勾選確認;傳 null = 取消確認(對齊 Moze 選單「取消全部
    # 選取」對單筆交易的等價動作)。同款 exclude_unset 語意,web UI 的「確認/
    # 取消確認」按鈕直接呼叫既有的 `PATCH .../transactions/{id}` 帶這個欄位,
    # 不需要為此新增專門的 write endpoint。
    reconciled_at: datetime | None = None



class WriteEntityDeleteRequest(WriteBaseRequest):
    pass


class WriteAccountDeleteRequest(WriteEntityDeleteRequest):
    # 級聯刪除(2026-08-05):True 時,若帳戶還有一般交易引用,連同這些交易
    # 一併刪除(結構性設定引用——週期性收支/分期/範本/回饋規則/自動扣繳
    # 來源——不論這個旗標一律照舊擋下,見 snapshot_mutator.delete_account)。
    cascade: bool = False


class WriteAccountCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None
    # 扩展字段:跟 mobile lib/data/db.dart Account 表对齐,跨端可编辑。
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    bank_name: str | None = None
    card_last_four: str | None = Field(default=None, max_length=8)
    # 主帳戶(合併帳單,§2.9 Phase 4):新建一般不设,子卡后续用 PATCH 挂靠。
    parent_account_id: str | None = None
    # 账户隐藏(issue #240):新建一般为 false,留字段以备批量导入。写路径接线
    # (mutator / write handler)是 Task 4,本字段暂不生效。
    hidden: bool = False
    # 自動扣繳(§2.9,2026-08-04 改版):新建一般不设,建完卡再 PATCH 開啟。
    auto_pay_enabled: bool = False
    auto_pay_from_account_id: str | None = None
    # 帳戶頭像(2026-08-02 補強):新建一般不设,建完卡再 PATCH 上傳。
    avatar_cloud_file_id: str | None = None
    avatar_cloud_sha256: str | None = None


class WriteAccountUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    bank_name: str | None = None
    card_last_four: str | None = Field(default=None, max_length=8)
    # 主帳戶(合併帳單,§2.9 Phase 4):None = 不改;空字串 = 解除掛靠。
    parent_account_id: str | None = None
    # None = 不改(PATCH exclude_unset)。写路径接线是 Task 4,本字段暂不生效。
    hidden: bool | None = None
    # 自動扣繳(§2.9,2026-08-04 改版):None = 不改;空字串來源帳戶 = 解除。
    auto_pay_enabled: bool | None = None
    auto_pay_from_account_id: str | None = None
    # 帳戶頭像(2026-08-02 補強):None = 不改;空字串 = 移除頭像。
    avatar_cloud_file_id: str | None = None
    avatar_cloud_sha256: str | None = None


class WriteBudgetCreateRequest(WriteBaseRequest):
    type: Literal["total", "category"]
    category_id: str | None = None
    amount: float = Field(gt=0)
    period: Literal["monthly", "weekly", "yearly"] = "monthly"
    # deprecated:预算周期已统一跟随账本 month_start_day(D5),该字段仅作兼容保留
    start_day: int = Field(default=1, ge=1, le=28)
    enabled: bool = True


class WriteBudgetUpdateRequest(WriteBaseRequest):
    amount: float | None = Field(default=None, gt=0)
    period: Literal["monthly", "weekly", "yearly"] | None = None
    # deprecated:预算周期已统一跟随账本 month_start_day(D5),该字段仅作兼容保留
    start_day: int | None = Field(default=None, ge=1, le=28)
    enabled: bool | None = None


class WriteRecurringRuleCreateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] = "expense"
    amount: float = Field(gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    frequency: RecurringFrequency = "monthly"
    interval: int = Field(default=1, ge=1, le=365)
    next_run_at: datetime
    end_at: datetime | None = None
    enabled: bool = True
    # Phase 1.5(§2.12.2):简单 frequency+interval 表达不了的进阶规则(每週
    # 六日/每月 N 号),None = 用 frequency+interval。见
    # services.recurring_schedule 的两种 type。
    advanced_rule_json: dict[str, Any] | None = None


class WriteRecurringRuleUpdateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] | None = None
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    frequency: RecurringFrequency | None = None
    interval: int | None = Field(default=None, ge=1, le=365)
    next_run_at: datetime | None = None
    end_at: datetime | None = None
    enabled: bool | None = None
    advanced_rule_json: dict[str, Any] | None = None


class WriteRecurringOccurrenceUpdateRequest(WriteBaseRequest):
    """§2.12.2:单独编辑某一期已生成的 occurrence 交易,强制标记
    `recurring_occurrence_overridden=True`(不暴露成可选字段,呼叫这个端点
    本身就意味着"要跳过之后的批次覆盖")。"""
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    happened_at: datetime | None = None


class WriteRecurringUpdateFromRequest(WriteBaseRequest):
    """§2.12.2:修改連同未來 —— 更新规则本身字段,并套用到该期以后所有未
    `overridden` 的已生成交易(不动 `happened_at`,只改内容)。"""
    tx_type: Literal["expense", "income", "transfer"] | None = None
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    frequency: RecurringFrequency | None = None
    interval: int | None = Field(default=None, ge=1, le=365)
    advanced_rule_json: dict[str, Any] | None = None


class WriteInstallmentPlanCreateRequest(WriteBaseRequest):
    total_amount: float = Field(gt=0)
    # 上限从 120 提高到 600(支援 30-50 年期);§2.12.1 文件本身举了 360 期
    # 的例子(信用卡帐单分期常见到 60,房贷类场景到 360)。
    periods: int = Field(ge=1, le=600)
    first_period_at: datetime
    # 帳單分期沖銷支援主帳戶(§2.3,2026-08-02 第三輪):可以是一張真實帳戶,
    # 也可以是 account_group(主帳戶)本身或沒有掛靠任何群組的獨立信用卡
    # ——見 `credit_card_billing.is_billing_root`。已經掛靠某個群組的子卡
    # 不能被直接傳(要嘛用它的群組 id),write endpoint 會擋。
    account_id: str | None = None
    category_id: str | None = None
    note: str | None = None
    # Phase 1.5(§2.12.1)攤還算法参数,default 对齐 Phase 1 既有行为(等额
    # 本金/无息)。
    repayment_method: InstallmentRepaymentMethod = "equal_principal"
    interest_period: InstallmentInterestPeriod = "monthly"
    interest_rate: float = Field(default=0.0, ge=0)
    round_amounts: bool = True
    remainder_position: InstallmentRemainderPosition = "last"
    grace_period_months: int = Field(default=0, ge=0)
    # 帳單分期沖銷(§2.3,對齊 Moze「Bill Installment」設計,2026-08-02
    # 第三輪改版):把一張信用卡「已經欠下的當期帳單」轉換成分期付款時,如果
    # 不做任何處理,原本那筆消費的 expense 交易還留在卡上繼續計入應繳金額,
    # 而分期計畫又會各期各生成一筆新的 expense —— 同一筆錢在帳單裡被算了
    # 兩次。設 true 時,write endpoint 會驗證 `account_id` 目前確實有欠款
    # (沒有欠款 = 沒有東西可以沖銷,回 400)且 `total_amount` 不超過欠款,
    # 算好每個子帳戶(若是主帳戶/群組)各自的沖銷金額寫進
    # `read_installment_plan_projection.offset_breakdown_json` ——**不**
    # 產生任何真實交易(2026-08-02 使用者反饋:沖銷款不該出現在交易明細,
    # 純內部記帳調整),`services.credit_card_billing` 算應繳金額時直接扣掉
    # 這個值。刪除整個分期計畫時這個沖銷連帶失效,帳單自動變回「尚未沖銷」
    # 狀態。只在 `account_id` 有設定時才能用(沒有帳戶就沒有東西可以沖銷)。
    offset_existing_balance: bool = False


class WriteInstallmentPlanUpdateRequest(WriteBaseRequest):
    """提前结清用:传 `status="settled"`。不允许改期数/金额(语义混乱,
    等同删了重建),跟 budget 的 update 限制同一设计取舍。攤還参数同理不可
    改 —— 要调利率/提前还本走下面的差异化端点,不是打平这个 PATCH。"""
    note: str | None = None
    status: InstallmentPlanStatus | None = None


class WriteInstallmentPeriodUpdateRequest(WriteBaseRequest):
    """§2.12.1:编辑单期(金额/日期/备注),`overridden=true`,之后整批重算
    (rebalance-from/early-repay-principal)会跳过这期。"""
    amount: float | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    note: str | None = None


class WriteInstallmentRebalanceRequest(WriteBaseRequest):
    """§2.12.1:调利率(可选换攤還方式),连同未來 —— 从指定期数起对未
    `overridden` 的期数依攤還演算法重算。"""
    interest_rate: float = Field(ge=0)
    repayment_method: InstallmentRepaymentMethod | None = None


class WriteCardPaymentRequest(WriteBaseRequest):
    """§2.9 Phase 4:信用卡繳款 —— 語意化端點,本質是產生一筆
    `tx_type=transfer`(`from_account_id` → 該信用卡帳戶)。"""
    amount: float = Field(gt=0)
    from_account_id: str
    happened_at: datetime | None = None
    note: str | None = None


class WriteInstallmentEarlyRepayRequest(WriteBaseRequest):
    """§2.12.1:部分还本 —— 减少剩余本金,重算未 overridden 的未来期数。"""
    payment_amount: float = Field(gt=0)
    account_id: str | None = None
    happened_at: datetime | None = None


class WriteInstallmentPayoffRequest(WriteBaseRequest):
    """§2.12.1:提前结清 —— 算剩余本金+当期应计利息,生成一笔结清交易,
    删除所有未到期的未来期。"""
    account_id: str | None = None
    happened_at: datetime | None = None


class WriteInstallmentPeriodRefundRequest(WriteBaseRequest):
    """§2.6/§2.12.1:单期退款 —— 对分期计划里某一期(以其生成的 tx_id 定位)
    建一笔 income 退款交易(refund_of_id 指向该期原本的 expense 交易),并把
    该期状态标成 'refunded'。原交易本身不删除、不改动,在分期总表里仍可见,
    只是多一个"已退款"标记 + 日期。跟"整笔退款"(直接 DELETE 整个计划,见
    `delete_installment_plan_ep`)是互斥的两个选项,由前端在退款发起点先问
    使用者要选哪一种。"""
    tx_id: str
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    happened_at: datetime | None = None


class WriteDebtCreateRequest(WriteBaseRequest):
    direction: DebtDirection
    counterparty_name: str = Field(min_length=1, max_length=255)
    principal_amount: float = Field(gt=0)
    due_at: datetime | None = None
    note: str | None = None


class WriteDebtUpdateRequest(WriteBaseRequest):
    """`principal_amount`/`direction` 建立后不可改(语义混乱,等同删了重建,
    跟 installment_plan 的 total_amount 同一取舍)。"""
    counterparty_name: str | None = Field(default=None, min_length=1, max_length=255)
    due_at: datetime | None = None
    note: str | None = None
    # 結案(體驗補強):key 不出現 = 不變;傳 ISO 時間 = 結案;傳 null = 重新
    # 開啟。跟 due_at/refund_of_id 同款「以 key 是否出現判斷是否要改」語意。
    closed_at: datetime | None = None


class WriteStatementClearConfirmationsRequest(WriteBaseRequest):
    """對帳模式(§2.10 Phase 5,2026-08-09 改版)選單裡的「取消全部選取」
    (對齊 Moze 原文「清除所有已選項目,恢復到未對帳狀態」)——把 `cycle_offset`
    指定週期窗口裡、目前掛在 `account_id` 底下的所有交易的 `reconciled_at`
    一次清空,不影響 `deferred_posting_at`(延後入帳是另一個獨立動作,清除
    確認狀態不等於撤銷延後入帳)。"""
    cycle_offset: int = 0


class WriteBalanceAdjustmentRequest(WriteBaseRequest):
    """餘額調整(§2.10 Phase 5):語意化端點 —— 使用者直接輸入「這個帳戶
    現在應該是多少錢」,server 算出跟目前記帳餘額的差額,寫成一筆
    `tx_type=adjustment` 的交易(`amount` = 差額,可正可負)。`account_id`
    來自 URL path。"""
    target_balance: float
    happened_at: datetime | None = None
    note: str | None = None


class WriteCardRewardRuleCreateRequest(WriteBaseRequest):
    """§2.9.5:`account_id` 來自 URL path(`/accounts/{account_id}/
    card-reward-rules`),不重複放進 body。"""
    label: str = Field(min_length=1, max_length=255)
    category_ids: list[str] | None = None
    rate_type: CardRewardRateType = "percentage"
    rate_value: float = Field(gt=0)
    rounding: CardRewardRounding = "round"
    total_rounding: CardRewardRounding = "round"
    calc_basis: CardRewardCalcBasis = "transaction_date"
    interval: CardRewardInterval = "billing_cycle"
    min_spend_threshold: float | None = Field(default=None, gt=0)
    min_tx_amount: float | None = Field(default=None, gt=0)
    cap_amount: float | None = Field(default=None, gt=0)
    cap_shared_key: str | None = Field(default=None, max_length=64)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    settlement_type: CardRewardSettlementType = "manual"
    settlement_days: int | None = Field(default=None, ge=0, le=365)
    settlement_month_offset: int | None = Field(default=None, ge=0, le=11)
    settlement_day_of_month: int | None = Field(default=None, ge=1, le=28)
    reward_account_id: str | None = None
    note: str | None = None
    enabled: bool = True


class WriteCardRewardRuleUpdateRequest(WriteBaseRequest):
    """`account_id` 建立后不可改(跟其它 entity 的核心綁定欄位同一取舍)。"""
    label: str | None = Field(default=None, min_length=1, max_length=255)
    category_ids: list[str] | None = None
    rate_type: CardRewardRateType | None = None
    rate_value: float | None = Field(default=None, gt=0)
    rounding: CardRewardRounding | None = None
    total_rounding: CardRewardRounding | None = None
    calc_basis: CardRewardCalcBasis | None = None
    interval: CardRewardInterval | None = None
    min_spend_threshold: float | None = Field(default=None, gt=0)
    min_tx_amount: float | None = Field(default=None, gt=0)
    cap_amount: float | None = Field(default=None, gt=0)
    cap_shared_key: str | None = Field(default=None, max_length=64)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    settlement_type: CardRewardSettlementType | None = None
    settlement_days: int | None = Field(default=None, ge=0, le=365)
    settlement_month_offset: int | None = Field(default=None, ge=0, le=11)
    settlement_day_of_month: int | None = Field(default=None, ge=1, le=28)
    reward_account_id: str | None = None
    note: str | None = None
    enabled: bool | None = None


class WriteCardRewardManualPayoutRequest(WriteBaseRequest):
    """§2.9.5.4 補強(2026-08-03):`settlement_type == "manual"` 的規則不進
    自動入帳引擎掃描範圍——這是給使用者自己「按一下就記一筆」的手動入帳
    端點,`amount`/`reward_account_id` 每次呼叫臨時指定,不要求跟規則上的
    欄位一致(manual 規則的 `reward_account_id` 本來就允許是 null)。"""
    amount: float = Field(gt=0)
    reward_account_id: str
    happened_at: datetime | None = None
    note: str | None = None


class WriteTxTemplateCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    tx_type: Literal["expense", "income", "transfer"] = "expense"
    amount: float = Field(gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tag_ids: list[str] | None = None
    sort_order: int | None = None


class WriteTxTemplateUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    tx_type: Literal["expense", "income", "transfer"] | None = None
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tag_ids: list[str] | None = None
    sort_order: int | None = None


class WriteTxTemplateApplyRequest(WriteBaseRequest):
    """§2.7:把範本內容套成一筆新交易。`amount`/`note` 可選擇性覆蓋範本预设值
    (部分場景金額每次略有出入,比如「加油」範本但每次公升數不同)。"""
    happened_at: datetime
    amount: float | None = Field(default=None, gt=0)
    note: str | None = None


class WriteCategoryCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"]
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WriteCategoryUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"] | None = None
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WriteTagCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    color: str | None = None


class WriteTagUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = None


class AdminDeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    created_at: datetime
    last_seen_at: datetime
    is_online: bool
    user_id: str
    user_email: str


class AdminDeviceListOut(BaseModel):
    total: int
    items: list[AdminDeviceOut]


class AttachmentUploadOut(BaseModel):
    file_id: str
    ledger_id: str
    sha256: str
    size: int
    mime_type: str | None = None
    file_name: str | None = None
    created_at: datetime


class AttachmentExistsItem(BaseModel):
    sha256: str
    exists: bool
    file_id: str | None = None
    size: int | None = None
    mime_type: str | None = None


class AttachmentBatchExistsRequest(BaseModel):
    ledger_id: str
    sha256_list: list[str] = Field(default_factory=list)


class AttachmentBatchExistsResponse(BaseModel):
    items: list[AttachmentExistsItem] = Field(default_factory=list)


# ============================================================================
# Backup schemas — Web UI 写入 / 读取请求和响应。详见 .docs/backup-rclone-plan.md
# ============================================================================


class BackupRemoteCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    backend_type: str = Field(min_length=1, max_length=32)
    # rclone 配置字段(类型不同而异):s3 用 access_key_id/secret_access_key/...;
    # gdrive 用 client_id/client_secret/token。server 不在此校验具体字段,直接
    # 交给 rclone — 写完后立刻调 `rclone lsd <name>:` 测连通性,失败回写
    # last_test_error。
    config: dict[str, str] = Field(default_factory=dict)
    # 是否对 backup tarball 做 age passphrase 加密。开启时 age_passphrase 必填
    # (一旦丢失,该 remote 上的所有备份永久不可恢复)。
    encrypted: bool = False
    age_passphrase: str | None = None


class BackupRemoteUpdateRequest(BaseModel):
    config: dict[str, str] | None = None
    age_passphrase: str | None = None
    # 用户在编辑时切换 encrypted 状态(开/关) — 必须能持久化。
    encrypted: bool | None = None


class BackupRemoteOut(BaseModel):
    id: int
    name: str
    backend_type: str
    encrypted: bool
    config_summary: dict | None = None
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None
    created_at: datetime


class BackupRemoteTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    listing: list[str] | None = None


class BackupScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cron_expr: str = Field(min_length=1, max_length=64)
    retention_days: int = Field(ge=1, le=3650, default=30)
    include_attachments: bool = True
    enabled: bool = True
    remote_ids: list[int] = Field(min_length=1)


class BackupScheduleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cron_expr: str | None = Field(default=None, min_length=1, max_length=64)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    include_attachments: bool | None = None
    enabled: bool | None = None
    remote_ids: list[int] | None = None


class BackupScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    retention_days: int
    include_attachments: bool
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    remote_ids: list[int] = Field(default_factory=list)
    created_at: datetime


class ScheduledJobConfigUpdateRequest(BaseModel):
    interval_seconds: int | None = Field(default=None, ge=60, le=7 * 24 * 3600)
    enabled: bool | None = None


class ScheduledJobConfigOut(BaseModel):
    job_key: str
    interval_seconds: int
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_message: str | None = None


class ScheduledJobRunNowOut(BaseModel):
    job_key: str
    status: str
    message: str | None = None
    summary: dict
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class BackupRunTargetOut(BaseModel):
    id: int
    remote_id: int
    remote_name: str | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    bytes_transferred: int | None = None
    error_message: str | None = None


class BackupRunOut(BaseModel):
    id: int
    schedule_id: int | None = None
    schedule_name: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    backup_filename: str | None = None
    bytes_total: int | None = None
    error_message: str | None = None
    log_text: str | None = None
    targets: list[BackupRunTargetOut] = Field(default_factory=list)


class BackupRunListOut(BaseModel):
    items: list[BackupRunOut]
    total: int


# ============================================================================
# Restore schemas (PR3 用)
# ============================================================================


class BackupRestoreOut(BaseModel):
    """`<DATA_DIR>/restore/<run_id>/status.json` 的读视图。"""

    run_id: int
    phase: str  # 'downloading' / 'extracting' / 'done' / 'failed'
    started_at: datetime
    finished_at: datetime | None = None
    bytes_total: int | None = None
    bytes_downloaded: int | None = None
    error_message: str | None = None
    extracted_path: str | None = None
    source_remote_id: int | None = None
    source_remote_name: str | None = None
    backup_filename: str | None = None


class BackupRestoreListOut(BaseModel):
    items: list[BackupRestoreOut]
