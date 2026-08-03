export type LoginResponse = {
  /** 2FA 已启用且未验证时为 true,其余字段除 challenge_token / available_methods 外都 undefined */
  requires_2fa?: boolean
  // 2FA 未启用 / 已验证时填这些(后端 AuthLoginResponse 见 .docs/2fa-design.md):
  access_token?: string
  refresh_token?: string
  expires_in?: number
  device_id?: string
  scopes?: string[]
  user?: { id: string; email: string; is_admin?: boolean }
  // 2FA 启用且未验证时填这些:
  challenge_token?: string
  available_methods?: Array<'totp' | 'recovery_code'>
}

export type TwoFASetupResponse = {
  secret: string
  qr_code_uri: string
  expires_in: number
}

export type TwoFAConfirmResponse = {
  enabled: boolean
  recovery_codes: string[]
}

export type TwoFAStatusResponse = {
  enabled: boolean
  enabled_at: string | null
}

export type TwoFARegenerateResponse = {
  recovery_codes: string[]
}

export type ProfileAppearance = {
  /** 顶部皮肤 id:'none' | 'aurora' | 'mountains' | … 详见 mobile kHeaderSkins */
  header_skin?: string
  /** 紧凑金额显示(万/亿) */
  compact_amount?: boolean
  /** 交易行是否显示时间 */
  show_transaction_time?: boolean
  /** 明细行第一行显示方式:'category'(默认,分类+备注括号) | 'note'(备注优先) */
  note_display_mode?: 'category' | 'note'
}

/**
 * AI 服务商单条配置 —— 字段命名严格对齐 mobile `AIServiceProviderConfig.toJson()`,
 * server 是透传 JSON,**不要** snake_case 化(mobile 期待 `textProviderId` /
 * `apiKey` / `isBuiltIn` 这种命名)。
 */
export type AIProvider = {
  id: string
  name: string
  isBuiltIn?: boolean
  apiKey?: string
  baseUrl?: string
  textModel?: string
  visionModel?: string
  audioModel?: string
  createdAt?: string // ISO 8601
}

export type AICapabilityBinding = {
  textProviderId?: string | null
  visionProviderId?: string | null
  speechProviderId?: string | null
}

/**
 * 完整 AI 配置 snapshot —— 跟 mobile `AIProviderManager.snapshotForSync()` 对齐。
 * server 的 `ai_config_json` 列存的就是这个 shape 序列化后的字符串。
 */
export type AIConfig = {
  providers?: AIProvider[]
  binding?: AICapabilityBinding
  custom_prompt?: string
  strategy?: string
  bill_extraction_enabled?: boolean
  use_vision?: boolean
}

/** 内置「智谱GLM」provider id —— 跟 mobile `zhipuDefault.id` 对齐,删除 fallback 用。 */
export const BUILTIN_PROVIDER_ID = 'zhipu_glm'

export type ProfileMe = {
  user_id: string
  email: string
  display_name?: string | null
  avatar_url?: string | null
  avatar_version: number
  /** mobile `incomeExpenseColorSchemeProvider` 同步过来的配色偏好：
   *  true  = 红色收入 / 绿色支出（mobile 默认）
   *  false = 红色支出 / 绿色收入
   *  null  = 未设置过，web 视为 true */
  income_is_red?: boolean | null
  /** mobile 推过来的主题色（`#RRGGBB`）。web 端用作"初始偏好"：
   *  - 用户在 web 本地改过主题色（localStorage 有值）→ 本地优先，忽略 server
   *  - 否则 apply server 值到 CSS var（不写 localStorage，保持 server 作权威） */
  theme_primary_color?: string | null
  /** mobile 推过来的外观偏好(打包的 JSON)。web 目前只读展示,不编辑。 */
  appearance?: ProfileAppearance | null
  /** mobile 推过来的 AI 配置(providers / binding / custom_prompt / strategy …)。
   *  API key 存在这里面,只读展示时要脱敏。shape 由 mobile 的 snapshotForSync
   *  定义,这里用 Record 宽松接收,避免 web 跟 mobile 的实现耦合。 */
  ai_config?: Record<string, any> | null
  /** 主币种(本位币),资产折算目标。mobile prefs `baseCurrency` 同步而来。 */
  primary_currency?: string | null
}

export type WriteCommitMeta = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  idempotency_replayed: boolean
  entity_id: string | null
}

export type AttachmentRef = {
  fileName: string
  originalName?: string | null
  fileSize?: number | null
  width?: number | null
  height?: number | null
  sortOrder?: number | null
  cloudFileId?: string | null
  cloudSha256?: string | null
}

export type LedgerCreatePayload = {
  ledger_id?: string | null
  ledger_name: string
  currency?: string | null
  month_start_day?: number | null
}

export type LedgerMetaPayload = {
  ledger_name?: string | null
  currency?: string | null
  month_start_day?: number | null
}

export type ReadLedger = {
  ledger_id: string
  ledger_name: string
  currency: string
  month_start_day?: number
  transaction_count: number
  income_total: number
  expense_total: number
  balance: number
  exported_at: string | null
  updated_at: string
  role: 'owner' | 'editor' | 'viewer'
  is_shared?: boolean
  member_count?: number
}

export type ReadLedgerDetail = ReadLedger & {
  source_change_id: number
}

export type ReadTransaction = {
  id: string
  tx_index: number
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  note: string | null
  category_name: string | null
  category_kind: string | null
  category_id?: string | null
  account_name: string | null
  account_id?: string | null
  from_account_name: string | null
  from_account_id?: string | null
  to_account_name: string | null
  to_account_id?: string | null
  tags: string | null
  tags_list: string[]
  tag_ids?: string[]
  attachments: AttachmentRef[] | null
  /** 不计入收支统计(仍计入账户余额/净资产)。历史交易默认 false。 */
  exclude_from_stats?: boolean
  /** 不计入预算用量(仅 expense 有意义)。历史交易默认 false。 */
  exclude_from_budget?: boolean
  /** 交易原币种(ISO)。历史交易可能为 null,视作账本本位币。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(记账时汇率,保存即定)。null 时 fallback 用 amount。 */
  native_amount?: number | null
  /** 退款(§2.6):这笔交易是对哪笔支出的退款。null = 普通交易。 */
  refund_of_id?: string | null
  /** 分期付款(§2.3):所属分期计划的 id。null = 非分期生成的交易。 */
  installment_plan_id?: string | null
  /** 週期性收支(§2.12.2 Phase 1.5):所属规则的 id。null = 非规则生成的交易。 */
  recurring_rule_id?: string | null
  /** 该笔 occurrence 是否被单独编辑过(之后规则批次更新/视窗续产生会跳过它)。 */
  recurring_occurrence_overridden?: boolean
  /** 退款反查(§2.12.3):这笔支出收到过哪些退款,空数组 = 没有退款。 */
  refunds?: ReadTxRefundSummary[]
  /** 拆帳(§2.4):true = 这笔交易拆到多个分类,category_id/category_name 为 null,明细在 splits。 */
  has_splits?: boolean
  /** 拆帳(§2.4):has_splits=true 时的分类明细,空数组 = 没有拆帳。 */
  splits?: ReadTxSplit[]
  /** 借還款追蹤(§2.5 Phase 3):这笔交易关联的欠款 id。null = 普通交易。
   *  debt_counterparty_name/debt_direction 是反查这笔欠款拿到的展示字段
   *  (体验补强,对齐 category_id+category_name 的既有惯例)。 */
  debt_id?: string | null
  debt_counterparty_name?: string | null
  debt_direction?: DebtDirection | null
  /** 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走
   *  哪幾條回饋規則的 id 列表,空数组 = 没有勾选任何规则。 */
  reward_rule_ids?: string[]
  /** 信用卡紅利回饋自動入帳(§2.9.5.4 補強):有值 = 这笔交易是逐笔结算
   *  规则自动产生的回饋 income,反查它对应的原始消费交易 id;null = 普通
   *  交易,或 period_end/manual 這種不對應單一原始交易的回饋。 */
  reward_source_tx_id?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
  created_by_display_name?: string | null
  created_by_avatar_url?: string | null
  created_by_avatar_version?: number | null
  // §7 共享账本 — server projection 的 last_edited_by_user_id 加上 user 信息回填,
  // tx 列表显示"创建 / 编辑"双角色。
  last_edited_by_user_id?: string | null
  last_edited_by_email?: string | null
  last_edited_by_display_name?: string | null
  last_edited_by_avatar_url?: string | null
  last_edited_by_avatar_version?: number | null
}

/** §2.12.3:交易明细页"已退款金额 + 退款交易清单"用的单笔退款摘要。 */
export type ReadTxRefundSummary = {
  id: string
  amount: number
  happened_at: string
}

/** 拆帳(§2.4):一笔交易拆到某个分类下的明细行。 */
export type ReadTxSplit = {
  category_id: string | null
  category_name: string | null
  amount: number
  note: string | null
  sort_order: number
}

export type ReadAccount = {
  id: string
  name: string
  account_type: string | null
  currency: string | null
  initial_balance: number | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
  /** 备注,所有类型可填。null = 未填。 */
  note?: string | null
  /** 信用额度,仅 credit_card。 */
  credit_limit?: number | null
  /** 账单日(1-31),仅 credit_card。 */
  billing_day?: number | null
  /** 还款日(1-31),仅 credit_card。 */
  payment_due_day?: number | null
  /** 开户行,bank_card / credit_card 元信息。 */
  bank_name?: string | null
  /** 卡号后四位,bank_card / credit_card。 */
  card_last_four?: string | null
  /** 主帳戶(合併帳單,§2.9 Phase 4):子卡的 sync_id 指向主卡,null=沒有掛靠。 */
  parent_account_id?: string | null
  /** 账户隐藏(issue #240):true = 已隐藏 —— 记账/转账选择器不再出现,主列表
   *  退场收进「已隐藏」分区;但仍计入净资产/资产/收支(D1,服务端不做统计过滤)。
   *  缺省 false(旧接口未提供该字段时视为未隐藏)。 */
  hidden?: boolean
  /** 自動扣繳(§2.9,2026-08-04 改版):開關。只在 account_group,或沒有
   *  掛靠任何群組的獨立信用卡上生效。 */
  auto_pay_enabled?: boolean
  /** 自動扣繳來源帳戶 sync_id,null = 未設定。 */
  auto_pay_from_account_id?: string | null
  /** 「可繳款」提醒(§2.9 補強,2026-08-02):只在 billing-root(account_group
   *  或沒有掛靠任何群組的獨立信用卡)且真的欠款(> 0)時才有值。 */
  billing_due_date?: string | null
  billing_remaining_due?: number | null
  /** 帳戶頭像(2026-08-02 補強):`AttachmentFile.id`,null = 沒有自訂頭像。 */
  avatar_cloud_file_id?: string | null
  avatar_cloud_sha256?: string | null
}

export type ReadCategory = {
  id: string
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level: number | null
  sort_order: number | null
  icon: string | null
  icon_type: string | null
  custom_icon_path?: string | null
  icon_cloud_file_id?: string | null
  icon_cloud_sha256?: string | null
  parent_name: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
}

export type ReadTag = {
  id: string
  name: string
  color: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
}

export type ReadBudget = {
  id: string
  /** `total` = 整账本总预算 / `category` = 分类预算 */
  type: 'total' | 'category' | string
  category_id?: string | null
  category_name?: string | null
  amount: number
  period: 'monthly' | 'weekly' | 'yearly' | string
  start_day: number
  enabled: boolean
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type WorkspaceTransaction = ReadTransaction & {
  ledger_id: string
  ledger_name: string
  created_by_user_id: string | null
  created_by_email: string | null
  created_by_display_name?: string | null
  created_by_avatar_url?: string | null
  created_by_avatar_version?: number | null
}

export type WorkspaceTransactionPage = {
  items: WorkspaceTransaction[]
  total: number
  limit: number
  offset: number
}

export type WorkspaceAccount = ReadAccount & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

export type WorkspaceCategory = ReadCategory & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  // 服务端按 category_sync_id 聚合的笔数,跨所有账本累加(跟 dedup 后的展
  // 示口径一致)。None = 历史接口未提供。
  tx_count?: number | null
}

export type WorkspaceTag = ReadTag & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  // 服务端一次性算好，跨全账本全期。前端不再需要自己从分页 tx 里聚合。
  tx_count?: number | null
  expense_total?: number | null
  income_total?: number | null
}

export type AnalyticsScope = 'month' | 'year' | 'all'
export type AnalyticsMetric = 'expense' | 'income' | 'balance'

export type WorkspaceLedgerCounts = {
  tx_count: number
  /** 首次记账到今天（含当天）。对齐 mobile `getCountsForLedger` 的 dayCount。 */
  days_since_first_tx: number
  /** 有数据的日期数（distinct DATE）。备用字段，首页不用。 */
  distinct_days: number
  first_tx_at?: string | null
}

export type WorkspaceAnalyticsSummary = {
  transaction_count: number
  income_total: number
  expense_total: number
  balance: number
  distinct_days?: number
  first_tx_at?: string | null
  last_tx_at?: string | null
}

export type WorkspaceAnalyticsSeriesItem = {
  bucket: string
  expense: number
  income: number
  balance: number
}

export type WorkspaceAnalyticsCategoryRank = {
  category_name: string
  total: number
  tx_count: number
}

export type WorkspaceAnalyticsAnomalyAttribution = {
  category_name: string
  amount: number
  /** 该分类在其他月份的中位数;本月独有(其他月都 0)时为 0。 */
  median_others: number
  /** amount / median_others;本月独有时为 null,前端显示"本月独有"。 */
  multiplier: number | null
}

export type WorkspaceAnalyticsAnomalyMonth = {
  /** "YYYY-MM" */
  bucket: string
  expense: number
  /** median(已发生月份的 expense),见 .docs/dashboard-anomaly-budget/plan.md §2.1 */
  baseline: number
  /** (expense - baseline) / baseline */
  deviation_pct: number
  /** 归因到的 top 1-2 分类(按 diff 降序) */
  top_attributions: WorkspaceAnalyticsAnomalyAttribution[]
}

export type WorkspaceAnalyticsRange = {
  scope: AnalyticsScope
  metric: AnalyticsMetric
  period: string | null
  start_at: string | null
  end_at: string | null
}

export type WorkspaceAnalytics = {
  summary: WorkspaceAnalyticsSummary
  series: WorkspaceAnalyticsSeriesItem[]
  category_ranks: WorkspaceAnalyticsCategoryRank[]
  /** 仅 scope=year 时填,已发生月份 < 3 时为空。 */
  anomaly_months: WorkspaceAnalyticsAnomalyMonth[]
  range: WorkspaceAnalyticsRange
}

export type UserAdmin = {
  id: string
  email: string
  is_admin: boolean
  is_enabled: boolean
  created_at: string
  display_name?: string | null
  avatar_url?: string | null
  avatar_version?: number
}

export type UserAdminCreatePayload = {
  email: string
  password: string
  is_admin?: boolean
  is_enabled?: boolean
}

export type UserAdminList = {
  total: number
  items: UserAdmin[]
}

export type AdminOverview = {
  users_total: number
  users_enabled_total: number
  ledgers_total: number
  transactions_total: number
  accounts_total: number
  categories_total: number
  tags_total: number
}

export type AdminHealth = {
  status: string
  db: string
  online_ws_users: number
  time: string
}

// ────────── 数据清理(替代旧 IntegrityScan)──────────

export type DataCleanupOrphanType =
  | 'tx_missing_category'
  | 'tx_missing_account'
  | 'tx_missing_from_account'
  | 'tx_missing_to_account'
  | 'budget_missing_category'
  | 'sync_change_missing_entity'
  | 'attachment_no_ref'
  | 'attachment_file_missing'
  | 'disk_file_no_ref'
  | 'tx_ref_broken_attachment'

export type DataCleanupRecord = {
  type: DataCleanupOrphanType | string
  title: string
  subtitle: string
  user_id?: string | null
  row_id?: string | null
  sync_id?: string | null
  file_path?: string | null
  size_bytes?: number | null
  extra?: Record<string, unknown> | null
}

export type DataCleanupScanReport = {
  db_orphans: DataCleanupRecord[]
  file_orphans: DataCleanupRecord[]
  sync_orphans: DataCleanupRecord[]
  total_count: number
  total_size_bytes: number
}

export type DataCleanupFailure = {
  record_key: string
  error: string
}

export type DataCleanupResult = {
  success_count: number
  failures: DataCleanupFailure[]
}

export type AdminSyncErrorItem = {
  id: number
  action: string
  metadata: Record<string, unknown> | null
  createdAt: string
}

export type AdminSyncErrors = {
  count: number
  items: AdminSyncErrorItem[]
}

export type AdminLogEntry = {
  seq: number
  ts: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | string
  logger: string
  message: string
  ledger_id?: string | null
  user_id?: string | null
  device_id?: string | null
}

export type AdminLogList = {
  items: AdminLogEntry[]
  capacity: number
  latest_seq: number
}

export type AdminBackupArtifact = {
  id: string
  ledger_id: string
  kind: 'db' | 'snapshot'
  file_name: string
  content_type: string | null
  checksum: string
  size: number
  created_at: string
  created_by: string
  note: string | null
  metadata: Record<string, unknown>
}

export type AdminBackupCreateResponse = {
  snapshot_id: string
  ledger_id: string
  created_at: string
}

export type AdminBackupRestoreResponse = {
  restored: boolean
  ledger_id: string
  change_id: number
}

export type TxPayload = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  /** 交易级多币种(0018):原币种;不传 = 账本本位币(不产生字段)。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(前端按 server 汇率算好传入)。 */
  native_amount?: number | null
  note?: string | null
  category_name?: string | null
  category_kind?: 'expense' | 'income' | 'transfer' | null
  category_id?: string | null
  account_name?: string | null
  account_id?: string | null
  from_account_name?: string | null
  from_account_id?: string | null
  to_account_name?: string | null
  to_account_id?: string | null
  tags?: string | string[] | null
  tag_ids?: string[] | null
  attachments?: AttachmentRef[] | null
  /** 不计入收支统计(income/expense 可填,transfer 无意义)。 */
  exclude_from_stats?: boolean | null
  /** 不计入预算用量(仅 expense 有意义)。 */
  exclude_from_budget?: boolean | null
  /** 退款(§2.6):这笔交易是对 refund_of_id 那笔支出的退款。null = 普通交易/不改。 */
  refund_of_id?: string | null
  /**
   * Phase 1.5(§2.12.2):建交易当下顺便把它设成週期性收支的起点。只在
   * create 有效(update 会被忽略)。
   */
  recurring?: RecurringInlineCreatePayload | null
  /**
   * 拆帳(§2.4):不传/undefined = 维持现行单一 category(向下相容);传入
   * 至少 2 笔、tx_type 只能是 expense/income、金额加总须等于 amount ——
   * server 端校验,详见 src/routers/write/_shared.py `_validate_tx_splits`。
   * update 传空数组 [] = 清空 splits,交易变回单一 category。
   */
  splits?: TxSplitPayload[] | null
  /**
   * 借還款追蹤(§2.5 Phase 3):这笔交易是对 debt_id 那笔欠款的一次还款/
   * 收款。必须指向该账本下已存在的欠款,允许多笔部分还款。null = 普通
   * 交易/不改。
   */
  debt_id?: string | null
  /**
   * 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走
   * 哪幾條回饋規則(可複選),每個 id 必須是 `account_id` 這張信用卡自己
   * 名下的規則。undefined = 不改(update);[] = 清空;null 等同 []。
   */
  reward_rule_ids?: string[] | null
}

/** 拆帳(§2.4):挂在 `TxPayload.splits` 上的单个分类明细。 */
export type TxSplitPayload = {
  category_id: string
  category_name?: string | null
  amount: number
  note?: string | null
}

export type BudgetCreatePayload = {
  type: 'total' | 'category'
  /** category 预算必填,total 可省略;后端校验。 */
  category_id?: string | null
  amount: number
  period?: 'monthly' | 'weekly' | 'yearly'
  /** 起始日(1-28),默认 1。 */
  start_day?: number
  enabled?: boolean
}

export type BudgetUpdatePayload = {
  amount?: number
  period?: 'monthly' | 'weekly' | 'yearly'
  start_day?: number
  enabled?: boolean
}

export type AccountPayload = {
  name: string
  account_type?: string | null
  currency?: string | null
  initial_balance?: number | null
  note?: string | null
  credit_limit?: number | null
  billing_day?: number | null
  payment_due_day?: number | null
  bank_name?: string | null
  card_last_four?: string | null
  /** 主帳戶(合併帳單,§2.9 Phase 4):子卡挂靠的主卡 id。update 传空字串
   *  解除掛靠;不传 = 不改。 */
  parent_account_id?: string | null
  /** 账户隐藏(issue #240)。create 缺省 false;update 不传 = 不改(服务端
   *  merge 缺键保留,见 snapshot_mutator._apply_account_optional_fields)。 */
  hidden?: boolean | null
  /** 自動扣繳(§2.9,2026-08-04 改版):開關,不传 = 不改。 */
  auto_pay_enabled?: boolean | null
  /** 自動扣繳來源帳戶 sync_id;update 传空字串解除;不传 = 不改。 */
  auto_pay_from_account_id?: string | null
  /** 帳戶頭像(2026-08-02 補強):update 传空字串移除頭像;不传 = 不改。 */
  avatar_cloud_file_id?: string | null
  avatar_cloud_sha256?: string | null
}

export type AccountBillingMember = {
  account_id: string
  account_name: string
  cycle_spend: number
}

export type AccountBillingSummary = {
  account_id: string
  account_name: string
  billing_day: number
  payment_due_day: number
  member_account_ids: string[]
  members: AccountBillingMember[]
  cycle_start: string
  cycle_end: string
  due_date: string
  statement_amount: number
  paid_amount: number
  remaining_due: number
  open_cycle_start: string
  open_cycle_end: string
  open_cycle_due_date: string
  open_cycle_spend: number
  credit_limit: number | null
  available_credit: number | null
  period_cycle_start: string
  period_cycle_end: string
  period_due_date: string
  period_new_spend: number
  period_carryover_due: number
  period_total_due: number
  period_paid_in_cycle: number
  period_remaining_due: number
  period_has_older: boolean
  period_has_newer: boolean
}

export type AccountInterestFreeSuggestion = {
  account_id: string
  as_of: string
  billing_day: number
  payment_due_day: number
  current_cycle_start: string
  current_cycle_end: string
  current_cycle_due_date: string
  next_cycle_start: string
  next_cycle_end: string
  next_cycle_due_date: string
  recommended_purchase_after: string
  min_interest_free_days: number
  max_interest_free_days: number
}

export type CardPaymentPayload = {
  amount: number
  from_account_id: string
  happened_at?: string | null
  note?: string | null
}

// ────────── 信用卡紅利回饋 (Card Rewards，MOZE_FEATURE_GAP_SD.md §2.9.5 Phase 4.5）──────────

export type CardRewardRateType = 'percentage' | 'fixed_amount'
export type CardRewardRounding = 'floor' | 'round' | 'ceil'
export type CardRewardCalcBasis = 'transaction_date' | 'settlement_date'
export type CardRewardInterval = 'billing_cycle' | 'calendar_month'
export type CardRewardRuleStatus = 'ok' | 'no_billing_schedule' | 'expired'
/** 自動入帳(§2.9.5.4):manual = 純顯示不自動化;immediate_after_tx/
 *  after_posting_date 逐筆結算;period_end 整期結束後一次結算。 */
export type CardRewardSettlementType =
  | 'immediate_after_tx'
  | 'after_posting_date'
  | 'period_end'
  | 'manual'

export type ReadCardRewardRule = {
  id: string
  account_id: string
  label: string
  category_ids?: string[] | null
  rate_type: CardRewardRateType
  rate_value: number
  rounding: CardRewardRounding
  calc_basis: CardRewardCalcBasis
  interval: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type: CardRewardSettlementType
  settlement_days?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled: boolean
  last_change_id: number
}

export type CardRewardRuleCreatePayload = {
  label: string
  category_ids?: string[] | null
  rate_type?: CardRewardRateType
  rate_value: number
  rounding?: CardRewardRounding
  calc_basis?: CardRewardCalcBasis
  interval?: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type?: CardRewardSettlementType
  settlement_days?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled?: boolean
}

/** `account_id` 建立後不可改(綁定的信用卡帳戶不能改)。 */
export type CardRewardRuleUpdatePayload = {
  label?: string
  category_ids?: string[] | null
  rate_type?: CardRewardRateType
  rate_value?: number
  rounding?: CardRewardRounding
  calc_basis?: CardRewardCalcBasis
  interval?: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type?: CardRewardSettlementType
  settlement_days?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled?: boolean
}

/** §2.9.5.4 補強(2026-08-03):手動入帳,`settlement_type == 'manual'`
 *  的規則用這個端點自己按一下記一筆,`amount`/`reward_account_id` 每次
 *  臨時指定。 */
export type CardRewardManualPayoutPayload = {
  amount: number
  reward_account_id: string
  happened_at?: string | null
  note?: string | null
}

export type ReadCardRewardRuleUsage = {
  rule_id: string
  label: string
  period_start: string
  period_end: string
  qualifying_spend: number
  threshold_met: boolean
  raw_reward: number
  capped_reward: number
  cap_amount?: number | null
  cap_shared_key?: string | null
  status: CardRewardRuleStatus
}

export type ReadCardRewards = {
  account_id: string
  as_of: string
  items: ReadCardRewardRuleUsage[]
  total_reward: number
}

/** §2.9.5.3 交易明細彈窗:單一規則命中哪些交易 + 各自回饋金額。 */
export type ReadCardRewardQualifyingTx = {
  tx_id: string
  happened_at: string
  amount: number
  note?: string | null
  category_name?: string | null
  reward_amount: number
  settlement_date?: string | null
}

export type ReadCardRewardRuleTransactions = {
  rule_id: string
  label: string
  period_start: string
  period_end: string
  status: CardRewardRuleStatus
  qualifying_spend: number
  raw_reward: number
  capped_reward: number
  cap_amount?: number | null
  cap_shared_key?: string | null
  remaining_reward_room?: number | null
  items: ReadCardRewardQualifyingTx[]
}

export type CategoryPayload = {
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level?: number | null
  sort_order?: number | null
  icon?: string | null
  icon_type?: string | null
  custom_icon_path?: string | null
  icon_cloud_file_id?: string | null
  icon_cloud_sha256?: string | null
  parent_name?: string | null
}

export type TagPayload = {
  name: string
  color?: string | null
}

export type AdminDevice = {
  id: string
  name: string
  platform: string
  app_version: string | null
  os_version: string | null
  device_model: string | null
  last_ip: string | null
  created_at: string
  last_seen_at: string
  is_online: boolean
  user_id: string
  user_email: string
}

export type AdminDeviceList = {
  total: number
  items: AdminDevice[]
}

export type AttachmentUploadOut = {
  file_id: string
  ledger_id: string
  sha256: string
  size: number
  mime_type: string | null
  file_name: string
  created_at: string
}

export type AttachmentExistsItem = {
  sha256: string
  exists: boolean
  file_id: string | null
  size: number | null
  mime_type: string | null
}

export type AttachmentBatchExistsResponse = {
  items: AttachmentExistsItem[]
}

// === 共享账本 Editor 视角资源 ===
// 对应 server src/routers/shared_resources.py — Editor 进共享账本后通过
// /ledgers/{external_id}/shared-resources 拉一次 Owner 的 user-global 资源
// 快照,前端缓存到独立 state(Map<ledgerId, SharedResourcesBundle>),
// picker / tile / icon lookup 在共享账本场景下走这套数据,不污染用户
// 自己的 user-global state。effacing mobile 端 SharedLedger{Categories,
// Accounts,Tags} 镜像表的思路。
export type SharedCategoryItem = {
  sync_id: string
  name: string | null
  kind: string | null
  icon: string | null
  icon_type: string | null
  icon_cloud_file_id: string | null
  icon_cloud_sha256: string | null
  sort_order: number | null
  level: number | null
  parent_name: string | null
  // 二级分类父子关系的稳定 FK(parent 的 sync_id)。client 优先用它建父子链,
  // parent_name 是显示 / 兜底。
  parent_sync_id: string | null
}

export type SharedAccountItem = {
  sync_id: string
  name: string | null
  account_type: string | null
  currency: string | null
  initial_balance: number | null
  note: string | null
  credit_limit: number | null
  billing_day: number | null
  payment_due_day: number | null
  bank_name: string | null
  card_last_four: string | null
}

export type SharedTagItem = {
  sync_id: string
  name: string | null
  color: string | null
}

export type SharedResourcesBundle = {
  owner_user_id: string
  categories: SharedCategoryItem[]
  accounts: SharedAccountItem[]
  tags: SharedTagItem[]
}

export type ExchangeRatesResponse = {
  base: string
  rate_date: string
  source: string
  fetched_at: string
  stale: boolean
  /** 方向:1 base = x quote(展示折算前需取倒数,与 App 同规则)。 */
  rates: Record<string, string>
}

export type ExchangeRateOverride = {
  sync_id: string
  base_currency: string
  quote_currency: string
  /** 方向:1 quote = rate base。 */
  rate: string
  updated_at: string
}

export type NetWorthHistorySeriesItem = {
  bucket: string
  net_worth: number
  assets: number
  liabilities: number
}

export type NetWorthHistory = {
  series: NetWorthHistorySeriesItem[]
  multi_currency: boolean
}

// ────────── 通知中心(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0）──────────
// user-global，非 sync 实体，走普通 REST（GET /notifications 等），跟其余
// ledger-scoped read/write 契约不是一回事。

export type NotificationItem = {
  id: number
  /** 'reminder' | 'budget_alert' | 'card_due' | 'system'，服务端未做枚举校验。 */
  category: string
  title: string
  body: string | null
  payload: Record<string, unknown> | null
  read_at: string | null
  created_at: string
}

export type NotificationListResponse = {
  total: number
  /** 始终是当前用户全部未读数，不受 limit/offset/category/unread_only 影响。 */
  unread_count: number
  items: NotificationItem[]
}

// ────────── 週期性收支 (Recurring Rules，MOZE_FEATURE_GAP_SD.md §2.2 /
// Phase 1.5 修正版 §2.12.2）──────────

export type RecurringFrequency = 'daily' | 'weekly' | 'monthly' | 'yearly'

/**
 * 简单 frequency+interval 表达不了的进阶规则(§2.12.2)。`weekly_days.days`
 * 用 **Python `datetime.weekday()` 惯例(Monday=0…Sunday=6)**,跟 JS
 * `Date.getDay()`(Sunday=0)不同 —— 组装这个字段时务必换算,不要直接塞
 * JS 原生 weekday。
 */
export type RecurringAdvancedRule =
  | { type: 'weekly_days'; days: number[] }
  | { type: 'monthly_day'; day: number }

export type ReadRecurringRule = {
  id: string
  tx_type: 'expense' | 'income' | 'transfer' | string
  amount: number
  note?: string | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  frequency: RecurringFrequency
  interval: number
  next_run_at: string
  end_at?: string | null
  enabled: boolean
  /** 视窗续产生进度(Phase 1.5)。 */
  generated_until_at?: string | null
  advanced_rule_json?: RecurringAdvancedRule | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type RecurringRuleCreatePayload = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  frequency: RecurringFrequency
  interval: number
  next_run_at: string
  end_at?: string | null
  enabled?: boolean
  advanced_rule_json?: RecurringAdvancedRule | null
}

export type RecurringRuleUpdatePayload = {
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  frequency?: RecurringFrequency
  interval?: number
  next_run_at?: string
  end_at?: string | null
  enabled?: boolean
  advanced_rule_json?: RecurringAdvancedRule | null
}

/** §2.12.2:挂在 `TxPayload.recurring` 上,建交易当下顺便设週期起点。 */
export type RecurringInlineCreatePayload = {
  frequency: RecurringFrequency
  interval: number
  end_at?: string | null
  advanced_rule_json?: RecurringAdvancedRule | null
}

/** §2.12.2:单独编辑某一期已生成的 occurrence 交易(会被标记 overridden)。 */
export type RecurringOccurrenceUpdatePayload = {
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  happened_at?: string
}

/** §2.12.2:修改連同未來 —— 更新规则本身字段 + 该期以后所有未 overridden
 * 的已生成交易(不动 happened_at)。 */
export type RecurringUpdateFromPayload = {
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  frequency?: RecurringFrequency
  interval?: number
  advanced_rule_json?: RecurringAdvancedRule | null
}

// ────────── 分期付款 (Installment Plans，MOZE_FEATURE_GAP_SD.md §2.3 /
// Phase 1.5 修正版 §2.12.1）──────────

export type InstallmentPlanStatus = 'active' | 'settled' | 'terminated'
export type InstallmentRepaymentMethod = 'equal_installment' | 'equal_principal' | 'fixed_interest'
export type InstallmentInterestPeriod = 'monthly' | 'daily'
export type InstallmentRemainderPosition = 'first' | 'last'

export type ReadInstallmentPlan = {
  id: string
  total_amount: number
  periods: number
  period_amount: number
  first_period_at: string
  next_period_at: string
  paid_periods: number
  account_id?: string | null
  category_id?: string | null
  note?: string | null
  status: InstallmentPlanStatus
  repayment_method: InstallmentRepaymentMethod
  interest_period: InstallmentInterestPeriod
  interest_rate: number
  round_amounts: boolean
  remainder_position: InstallmentRemainderPosition
  grace_period_months: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type InstallmentPlanCreatePayload = {
  total_amount: number
  periods: number
  first_period_at: string
  account_id?: string | null
  category_id?: string | null
  note?: string | null
  repayment_method?: InstallmentRepaymentMethod
  interest_period?: InstallmentInterestPeriod
  interest_rate?: number
  round_amounts?: boolean
  remainder_position?: InstallmentRemainderPosition
  grace_period_months?: number
  /** 帳單分期沖銷(§2.9,2026-08-02):把信用卡已欠下的帳單轉成分期時,額外
   *  生成一筆 income 沖銷交易清空原本的應繳金額,避免同一筆錢被算兩次
   *  (原消費 + 分期各期新 expense)。要求 account_id 已設定。 */
  offset_existing_balance?: boolean
}

/** 只支持提前结清(status='settled')/改备注，攤還参数/期数/金额不可改
 * （要调利率/提前还本走下面的差异化端点，不是这个 PATCH）。 */
export type InstallmentPlanUpdatePayload = {
  note?: string | null
  status?: InstallmentPlanStatus
}

/** §2.12.1:分期单期明细(每期本金/利息/合计)。 */
export type ReadInstallmentPeriod = {
  id: string
  plan_id: string
  period_no: number
  due_at: string
  principal_amount: number
  interest_amount: number
  total_amount: number
  status: 'pending' | 'generated' | 'overridden' | 'refunded'
  tx_id?: string | null
  // 单期退款(§2.6/§2.12.1):status === 'refunded' 时才有值。
  refund_tx_id?: string | null
  refund_amount?: number | null
  refunded_at?: string | null
}

/** §2.6/§2.12.1:单期退款 —— 按该期的 tx_id 定位,建一笔 income 退款交易。 */
export type InstallmentPeriodRefundPayload = {
  tx_id: string
  amount?: number | null
  note?: string | null
  happened_at?: string | null
}

/** §2.12.1:编辑单期(金额/日期/备注)，`overridden=true`。 */
export type InstallmentPeriodUpdatePayload = {
  amount?: number
  due_at?: string
  note?: string | null
}

/** §2.12.1:调利率(可选换攤還方式)，连同未来重算未 overridden 的期数。 */
export type InstallmentRebalancePayload = {
  interest_rate: number
  repayment_method?: InstallmentRepaymentMethod
}

/** §2.12.1:部分还本，重算未 overridden 的未来期数。 */
export type InstallmentEarlyRepayPayload = {
  payment_amount: number
  account_id?: string | null
  happened_at?: string
}

/** §2.12.1:提前结清，生成一笔结清交易并删除未到期的未来期。 */
export type InstallmentPayoffPayload = {
  account_id?: string | null
  happened_at?: string
}

// ────────── 借還款追蹤 (Debts，MOZE_FEATURE_GAP_SD.md §2.5 Phase 3）──────────

export type DebtDirection = 'payable' | 'receivable'
/** 'closed' = 手動結案(體驗補強),優先權蓋過其它三種從 remaining_amount
 *  算出來的狀態 —— 不代表已還清全額。 */
export type DebtStatus = 'open' | 'partial' | 'settled' | 'closed'

/** 某笔欠款收到的一笔还款/收款摘要,给詳情頁「還款記錄」清單用。 */
export type ReadDebtRepayment = {
  id: string
  amount: number
  happened_at: string
}

/**
 * `remaining_amount`/`status` 不落库,是 server 读路径从反查交易即时算出
 * 的 derived 字段(见 server `ReadDebtProjection` docstring)。
 */
export type ReadDebt = {
  id: string
  direction: DebtDirection
  counterparty_name: string
  principal_amount: number
  remaining_amount: number
  status: DebtStatus
  due_at?: string | null
  note?: string | null
  repayments: ReadDebtRepayment[]
  /** 結案(體驗補強):非空 = 已手動標記結束。 */
  closed_at?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type DebtCreatePayload = {
  direction: DebtDirection
  counterparty_name: string
  principal_amount: number
  due_at?: string | null
  note?: string | null
}

/** `principal_amount`/`direction` 建立后不可改,只暴露
 *  counterparty_name/due_at/note/closed_at。closed_at 傳 ISO 時間 = 結案,
 *  傳 `null` = 重新開啟,不傳這個 key = 不變。 */
export type DebtUpdatePayload = {
  counterparty_name?: string
  due_at?: string | null
  note?: string | null
  closed_at?: string | null
}

// ────────── 交易範本 (Templates，MOZE_FEATURE_GAP_SD.md §2.7 Phase 3）──────────

export type ReadTxTemplate = {
  id: string
  name: string
  tx_type: 'expense' | 'income' | 'transfer' | string
  amount: number
  note?: string | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  account_name?: string | null
  from_account_id?: string | null
  from_account_name?: string | null
  to_account_id?: string | null
  to_account_name?: string | null
  tag_ids: string[]
  sort_order: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type TxTemplateCreatePayload = {
  name: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  tag_ids?: string[] | null
  sort_order?: number | null
}

export type TxTemplateUpdatePayload = {
  name?: string
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  tag_ids?: string[] | null
  sort_order?: number | null
}

/** 把範本內容套成一筆新交易;`amount`/`note` 可選擇性覆蓋範本预设值。 */
export type TxTemplateApplyPayload = {
  happened_at: string
  amount?: number | null
  note?: string | null
}
