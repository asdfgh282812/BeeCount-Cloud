import {
  createDepositBalance,
  deleteDepositBalance,
  fetchReadDepositBalances,
  updateDepositBalance,
  type WriteCommitMeta,
} from '@beecount/api-client'
import {
  depositDateToWire,
  depositFormPatchFromLink,
  depositOriginalKey,
  findDepositBalanceLink,
  isDepositFormActive,
  parseDepositAmount,
  type TxForm,
} from '@beecount/web-features'

/**
 * 訂金尾款追蹤:交易表單存檔後,把「尾款」這個獨立實體同步到 server。
 * TransactionsPage 與 GlobalEditDialogs 兩份存檔邏輯共用(尾款不是 TxPayload 的
 * 欄位,是交易存好之後的旁支寫入,非原子,v1 可接受——失敗只提示、不回滾交易)。
 */

type RetryOnConflict = <T>(
  ledgerId: string,
  fn: (baseChangeId: number) => Promise<T>,
) => Promise<T>

/** 編輯開啟時,查這筆交易在尾款追蹤裡的角色,回傳要覆蓋進 TxForm 的欄位。
 *  只在 expense/transfer、非週期/分期產生的交易才查;查詢失敗當作沒有。 */
export async function loadDepositFormPatch(args: {
  token: string
  ledgerId: string
  txId: string
  txType: string
}): Promise<Partial<TxForm>> {
  if (!args.ledgerId || !args.txId) return {}
  if (args.txType !== 'expense' && args.txType !== 'transfer') return {}
  try {
    const list = await fetchReadDepositBalances(args.token, args.ledgerId)
    return depositFormPatchFromLink(findDepositBalanceLink(list, args.txId))
  } catch {
    return {}
  }
}

/** 依表單狀態決定要 create / update / delete 哪個尾款。
 *  - 表單開著 + 還沒有尾款 → create(掛在 txId 上)
 *  - 表單開著 + 已有 pending 尾款且金額/日期有改 → update(空值 = null = 清空)
 *  - 表單關掉 + 已有 pending 尾款 → delete(訂金交易保留)
 *  - 已補上(completed)→ 完全不動
 *  成功回傳 null,失敗回傳 error(呼叫端自行提示)。 */
export async function syncDepositBalanceAfterSave(args: {
  token: string
  ledgerId: string
  /** 剛建立的交易 id(新建)或正在編輯的交易 id。 */
  txId: string | null | undefined
  form: TxForm
  retryOnConflict: RetryOnConflict
}): Promise<unknown | null> {
  const { token, ledgerId, txId, form, retryOnConflict } = args
  if (form.deposit_status === 'completed') return null
  const active = isDepositFormActive(form)
  const amount = parseDepositAmount(form.deposit_amount)
  const dueWire = depositDateToWire(form.deposit_due_at)
  try {
    if (form.deposit_balance_id) {
      if (!active) {
        await retryOnConflict(ledgerId, (base) =>
          deleteDepositBalance(token, ledgerId, form.deposit_balance_id, base),
        )
        return null
      }
      if (depositOriginalKey(form.deposit_amount, form.deposit_due_at) === form.deposit_original) {
        return null
      }
      // 兩個 key 都送:空值 = null = 清空(改回未定)。
      await retryOnConflict(ledgerId, (base) =>
        updateDepositBalance(token, ledgerId, form.deposit_balance_id, base, {
          amount,
          due_at: dueWire,
        }),
      )
      return null
    }
    if (!active || !txId) return null
    await retryOnConflict(ledgerId, (base) =>
      createDepositBalance(token, ledgerId, base, {
        source_tx_id: txId,
        amount,
        due_at: dueWire,
      }),
    )
    return null
  } catch (err) {
    return err
  }
}

/** 交易存檔回傳的 meta 取新交易 id(新建);編輯時用 form.editingId。 */
export function savedTxId(form: TxForm, meta: Pick<WriteCommitMeta, 'entity_id'> | null): string | null {
  return form.editingId || meta?.entity_id || null
}
