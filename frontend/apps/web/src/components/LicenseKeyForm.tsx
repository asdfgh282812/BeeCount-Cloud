import { type FormEvent, useState } from 'react'
import { KeyRound, Loader2 } from 'lucide-react'

import { activateLicense, type LicenseStatus } from '@beecount/api-client'
import { Button, Input, useT } from '@beecount/ui'

import { localizeError } from '../i18n/errors'

interface Props {
  token: string
  /** 按鈕文字,預設「啟用」;設定頁延長授權時傳「延長授權」。 */
  submitLabel?: string
  onActivated: (status: LicenseStatus) => void
  autoFocus?: boolean
}

/**
 * 輸入授權金鑰 + 啟用按鈕(docs/LICENSE_KEYS.md)。授權閘門頁跟設定頁授權卡片
 * 共用。錯誤直接顯示在輸入框下方(不用 toast)—— 使用者要對照錯誤訊息修正
 * 輸入,toast 幾秒就消失不好對照。格式正規化(小寫、空白、少連字號)交給
 * server,前端只擋空字串。
 */
export function LicenseKeyForm({ token, submitLabel, onActivated, autoFocus }: Props) {
  const t = useT()
  const [key, setKey] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const trimmed = key.trim()
    if (!trimmed) {
      setError(t('license.error.keyRequired'))
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const status = await activateLicense(token, trimmed)
      setKey('')
      onActivated(status)
    } catch (err) {
      setError(localizeError(err, t))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="space-y-2" onSubmit={(event) => void handleSubmit(event)}>
      <label className="text-xs text-muted-foreground" htmlFor="license-key-input">
        {t('license.field.key')}
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id="license-key-input"
          className="h-10 font-mono tracking-wide"
          value={key}
          onChange={(event) => {
            setKey(event.target.value)
            if (error) setError(null)
          }}
          placeholder="BC-XXXXX-XXXXX-XXXXX-XXXXX"
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          disabled={submitting}
          maxLength={64}
          aria-invalid={error ? true : undefined}
        />
        <Button type="submit" className="h-10 shrink-0" disabled={submitting}>
          {submitting ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
          ) : (
            <KeyRound className="mr-1.5 h-4 w-4" />
          )}
          {submitting ? t('license.activating') : submitLabel || t('license.activate')}
        </Button>
      </div>
      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  )
}
