import { Card, CardContent, useT } from '@beecount/ui'
import type { SimpleImportEntityType, SimpleImportSummary } from '@beecount/api-client'

interface Props {
  entityType: SimpleImportEntityType
  summary: SimpleImportSummary
}

/**
 * 分類 / 帳戶匯入的預覽卡(2026-08 新增)—— 沒有欄位對應這層,upload 回來的
 * `sample` 就是最終要寫入的內容,直接列出來 + 逐行錯誤清單。
 */
export function SimpleImportPreviewCard({ entityType, summary }: Props) {
  const t = useT()

  return (
    <Card className="bc-panel">
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="rounded-full border border-emerald-500/30 bg-emerald-500/5 px-2 py-0.5 text-emerald-600 dark:text-emerald-400">
            {t('import.simple.validRows', { count: summary.valid_rows })}
          </span>
          {summary.errors.length > 0 ? (
            <span className="rounded-full border border-destructive/40 bg-destructive/5 px-2 py-0.5 text-destructive">
              {t('import.simple.errorRows', { count: summary.errors.length })}
            </span>
          ) : null}
        </div>

        {summary.sample.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border/60">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  {entityType === 'categories' ? (
                    <>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.name')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.kind')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.parent')}</th>
                    </>
                  ) : (
                    <>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.name')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.type')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.currency')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.initialBalance')}</th>
                      <th className="px-3 py-2 font-medium">{t('import.simple.col.parentAccount')}</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {summary.sample.map((row, idx) => (
                  <tr key={idx}>
                    {entityType === 'categories' ? (
                      <>
                        <td className="px-3 py-1.5">{String(row.name ?? '')}</td>
                        <td className="px-3 py-1.5">{String(row.kind ?? '')}</td>
                        <td className="px-3 py-1.5 text-muted-foreground">
                          {row.parent_name ? String(row.parent_name) : '—'}
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-3 py-1.5">{String(row.name ?? '')}</td>
                        <td className="px-3 py-1.5">{String(row.type ?? '')}</td>
                        <td className="px-3 py-1.5">{String(row.currency ?? '')}</td>
                        <td className="px-3 py-1.5 text-muted-foreground">
                          {row.initial_balance != null ? String(row.initial_balance) : '—'}
                        </td>
                        <td className="px-3 py-1.5 text-muted-foreground">
                          {row.parent_account_name ? String(row.parent_account_name) : '—'}
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {summary.errors.length > 0 ? (
          <div className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-[11px]">
            {summary.errors.slice(0, 20).map((err, idx) => (
              <p key={idx} className="text-destructive">
                L{err.row_number} · {err.message}
              </p>
            ))}
            {summary.errors.length > 20 ? (
              <p className="text-muted-foreground">
                {t('import.simple.moreErrors', { count: summary.errors.length - 20 })}
              </p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
