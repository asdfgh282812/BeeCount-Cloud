import { authedDelete, authedPatch, authedPost } from './http'
import type {
  AccountPayload,
  BudgetCreatePayload,
  BudgetUpdatePayload,
  CategoryPayload,
  DebtCreatePayload,
  DebtUpdatePayload,
  InstallmentEarlyRepayPayload,
  InstallmentPayoffPayload,
  InstallmentPeriodRefundPayload,
  InstallmentPeriodUpdatePayload,
  InstallmentPlanCreatePayload,
  InstallmentPlanUpdatePayload,
  InstallmentRebalancePayload,
  LedgerCreatePayload,
  LedgerMetaPayload,
  ReadAccount,
  ReadCategory,
  ReadTag,
  RecurringOccurrenceUpdatePayload,
  RecurringRuleCreatePayload,
  RecurringRuleUpdatePayload,
  RecurringUpdateFromPayload,
  TagPayload,
  TxPayload,
  TxTemplateApplyPayload,
  TxTemplateCreatePayload,
  TxTemplateUpdatePayload,
  WriteCommitMeta
} from './types'

export async function createLedger(token: string, payload: LedgerCreatePayload): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>('/write/ledgers', token, payload)
}

export async function updateLedgerMeta(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: LedgerMetaPayload
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/meta`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

/** Soft-delete a ledger. Server writes a tombstone SyncChange; history kept. */
export async function deleteLedger(token: string, ledgerId: string): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}`, token)
}

export async function createTransaction(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TxPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/transactions`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateTransaction(
  token: string,
  ledgerId: string,
  txId: string,
  baseChangeId: number,
  payload: Partial<TxPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/transactions/${encodeURIComponent(txId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteTransaction(
  token: string,
  ledgerId: string,
  txId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/transactions/${encodeURIComponent(txId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

export type BatchDeleteTxFailure = {
  tx_id: string
  reason: 'not_found' | 'permission_denied' | 'conflict' | 'installment_linked'
  message?: string | null
}

export type BatchDeleteTxResponse = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  deleted_tx_ids: string[]
  failed: BatchDeleteTxFailure[]
}

/**
 * POST /write/ledgers/{id}/transactions/batch/delete — 批量删除交易。
 *
 * 设计:.docs/web-tx-batch-actions.md
 * - 单次最多 200 条(server 上限)
 * - 部分失败不阻断:返回 deleted_tx_ids + failed[]
 * - 服务端走 snapshot 锁 + 一次 SyncChange broadcast,跨设备实时更新
 */
export async function batchDeleteTransactions(
  token: string,
  options: {
    ledgerId: string
    txIds: string[]
    baseChangeId?: number
    idempotencyKey?: string
  }
): Promise<BatchDeleteTxResponse> {
  return authedPost<BatchDeleteTxResponse>(
    `/write/ledgers/${encodeURIComponent(options.ledgerId)}/transactions/batch/delete`,
    token,
    {
      tx_ids: options.txIds,
      base_change_id: options.baseChangeId ?? 0,
    },
    options.idempotencyKey
  )
}

export async function createAccount(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: AccountPayload,
  idempotencyKey?: string
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    },
    idempotencyKey
  )
}

export async function updateAccount(
  token: string,
  ledgerId: string,
  accountId: string,
  baseChangeId: number,
  payload: Partial<AccountPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts/${encodeURIComponent(accountId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteAccount(
  token: string,
  ledgerId: string,
  accountId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  // server 端 snapshot_mutator.delete_account 会 raise 如果账户还有任何关联
  // 交易 —— 客户端必须先看 tx_count,>0 时直接拒绝,不要走删除流程。
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts/${encodeURIComponent(accountId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

export async function createBudget(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: BudgetCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateBudget(
  token: string,
  ledgerId: string,
  budgetId: string,
  baseChangeId: number,
  payload: BudgetUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets/${encodeURIComponent(budgetId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteBudget(
  token: string,
  ledgerId: string,
  budgetId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets/${encodeURIComponent(budgetId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** MOZE_FEATURE_GAP_SD.md §2.2 —— 週期性收支规则。仅账本 owner 可写(server _OWNER_ONLY_ROLES)。 */
export async function createRecurringRule(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: RecurringRuleCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

export async function updateRecurringRule(
  token: string,
  ledgerId: string,
  ruleId: string,
  baseChangeId: number,
  payload: RecurringRuleUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

export async function deleteRecurringRule(
  token: string,
  ledgerId: string,
  ruleId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** §2.12.2:单独编辑某一期已生成的 occurrence 交易(标记 overridden)。 */
export async function updateRecurringOccurrence(
  token: string,
  ledgerId: string,
  ruleId: string,
  txId: string,
  baseChangeId: number,
  payload: RecurringOccurrenceUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}/occurrences/${encodeURIComponent(txId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.2:单独删除某一期已生成的 occurrence 交易。 */
export async function deleteRecurringOccurrence(
  token: string,
  ledgerId: string,
  ruleId: string,
  txId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}/occurrences/${encodeURIComponent(txId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** §2.12.2:修改連同未來 —— 更新规则本身字段 + 该期以后所有未 overridden 的已生成交易。 */
export async function updateRecurringRuleFrom(
  token: string,
  ledgerId: string,
  ruleId: string,
  txId: string,
  baseChangeId: number,
  payload: RecurringUpdateFromPayload,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}/update-from/${encodeURIComponent(txId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.2:终止未来週期 —— 删除所有未发生的已生成交易,规则标记 enabled=false。 */
export async function terminateRecurringRuleFuture(
  token: string,
  ledgerId: string,
  ruleId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/recurring-rules/${encodeURIComponent(ruleId)}/terminate-future`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** MOZE_FEATURE_GAP_SD.md §2.3 / Phase 1.5 修正版 §2.12.1 —— 分期付款计划。
 * POST 建计画会依攤還算法同事务一次生成**全部**期数(不再只生成第一期)。 */
export async function createInstallmentPlan(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: InstallmentPlanCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

/** 只用于提前结清(status: 'settled')/ 改备注 —— 期数 / 金额不可改。 */
export async function updateInstallmentPlan(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
  payload: InstallmentPlanUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

export async function deleteInstallmentPlan(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** §2.12.1:编辑单期(金额/日期/备注),标记 overridden。 */
export async function updateInstallmentPeriod(
  token: string,
  ledgerId: string,
  planId: string,
  periodNo: number,
  baseChangeId: number,
  payload: InstallmentPeriodUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/periods/${periodNo}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.1:调利率(可选换攤還方式),连同未来对未 overridden 的期数重算。 */
export async function rebalanceInstallmentPlan(
  token: string,
  ledgerId: string,
  planId: string,
  periodNo: number,
  baseChangeId: number,
  payload: InstallmentRebalancePayload,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/rebalance-from/${periodNo}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.1:部分还本,重算未 overridden 的未来期数。 */
export async function earlyRepayInstallmentPrincipal(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
  payload: InstallmentEarlyRepayPayload,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/early-repay-principal`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.1:提前结清,生成结清交易并删除未到期的未来期。 */
export async function payoffInstallmentPlan(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
  payload: InstallmentPayoffPayload = {},
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/payoff`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** §2.12.1:终止未来分期,删除未到期期,不生成结清交易。 */
export async function terminateInstallmentPlanFuture(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/terminate-future`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** §2.6/§2.12.1:单期退款 —— 建一笔 income 退款交易(refund_of_id 指回该期
 * 原本的 expense 交易),并把该期状态标成 'refunded'。原交易保留不动。跟
 * "整笔退款"(直接 deleteInstallmentPlan)是互斥的两个前端选项。 */
export async function refundInstallmentPeriod(
  token: string,
  ledgerId: string,
  planId: string,
  baseChangeId: number,
  payload: InstallmentPeriodRefundPayload,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/installment-plans/${encodeURIComponent(planId)}/refund-period`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** MOZE_FEATURE_GAP_SD.md §2.5 Phase 3 —— 借還款追蹤。`principal_amount`/
 * `direction` 建立后不可改,还款/收款走一般交易的 `debt_id` 字段。 */
export async function createDebt(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: DebtCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/debts`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

export async function updateDebt(
  token: string,
  ledgerId: string,
  debtId: string,
  baseChangeId: number,
  payload: DebtUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/debts/${encodeURIComponent(debtId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

/** 只允许在这笔欠款还没收到任何还款交易时删除,否则 400。 */
export async function deleteDebt(
  token: string,
  ledgerId: string,
  debtId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/debts/${encodeURIComponent(debtId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** MOZE_FEATURE_GAP_SD.md §2.7 Phase 3 —— 交易範本。 */
export async function createTxTemplate(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TxTemplateCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tx-templates`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

export async function updateTxTemplate(
  token: string,
  ledgerId: string,
  templateId: string,
  baseChangeId: number,
  payload: TxTemplateUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tx-templates/${encodeURIComponent(templateId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

export async function deleteTxTemplate(
  token: string,
  ledgerId: string,
  templateId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tx-templates/${encodeURIComponent(templateId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

/** 把範本內容套成一筆新交易,回傳 `entity_id` 是新交易的 id。 */
export async function applyTxTemplate(
  token: string,
  ledgerId: string,
  templateId: string,
  baseChangeId: number,
  payload: TxTemplateApplyPayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tx-templates/${encodeURIComponent(templateId)}/apply`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

export async function createCategory(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: CategoryPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/categories`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateCategory(
  token: string,
  ledgerId: string,
  categoryId: string,
  baseChangeId: number,
  payload: Partial<CategoryPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/categories/${encodeURIComponent(categoryId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteCategory(
  token: string,
  ledgerId: string,
  categoryId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/categories/${encodeURIComponent(categoryId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

export async function createTag(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TagPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/tags`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateTag(
  token: string,
  ledgerId: string,
  tagId: string,
  baseChangeId: number,
  payload: Partial<TagPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tags/${encodeURIComponent(tagId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteTag(
  token: string,
  ledgerId: string,
  tagId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tags/${encodeURIComponent(tagId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

// NOTE: the old ``createWorkspaceAccount`` / ``updateWorkspaceCategory`` /
// ``deleteWorkspaceTag`` helpers that targeted /write/workspace/* have been
// removed. They were replaced by per-ledger endpoints (createAccount,
// updateCategory, deleteTag above) which carry base_change_id for conflict
// detection. The server-side /write/workspace/* routes were already unwired
// when multi-user collaboration was simplified out.
