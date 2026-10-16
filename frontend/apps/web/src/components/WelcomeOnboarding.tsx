import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Check,
  Layers,
  Monitor,
  Moon,
  Palette,
  SlidersHorizontal,
  Sparkles,
  Sun,
  type LucideIcon,
} from 'lucide-react'

import { patchProfileMe, type ProfileAppearance } from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  PrimaryColorPicker,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useLocale,
  usePrimaryColor,
  useT,
  useTheme,
  useToast,
  type Locale,
  type ThemeMode,
} from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { localizeError } from '../i18n/errors'
import { mergeAiConfig, normalizeAiConfig } from '../lib/aiConfigMerge'
import { applyIncomeColorScheme, applyStockColorScheme } from '../lib/colorScheme'
import { applyGlassStyle, resolveGlassStyle, type GlassStyle } from '../lib/glassStyle'
import { WELCOME_LOCALES, WELCOME_STEP_COUNT, resolveWeekStartsMonday } from '../lib/welcomeOnboarding'

/**
 * 全新帳號第一次進 Web 的歡迎引導(對齊 App 的首次登入個人化引導,見 App repo
 * docs/changes/2026-10-17-onboarding-personalize.md)。
 *
 * 四步:外觀(語言/主題色/外觀模式)→ 質感與造型(Liquid Glass/頁首造型)→
 * 顯示偏好(每週起始日/金額格式/收支顏色/股票漲跌色)→ AI 智慧記帳。
 *
 * - 語言、主題色、外觀模式是本機偏好,選了立刻套用(主題色另外在完成時推上雲端,
 *   讓 App 之後登入拿到同一個顏色)。
 * - 其餘存在 profile 的 appearance / income_is_red,選了先當預覽立即套到畫面,
 *   完成(或略過)時一次 PATCH 上去,並寫入 `onboarding_done: true`,App 就不會再問。
 *   略過只是不再往下走,已選的仍會儲存;沒碰過的欄位不會被寫入。
 * - appearance 是整體替換語意,所以送出時一定先展開現有的 appearance。
 */
export function WelcomeOnboarding({ onFinished }: { onFinished: () => void }) {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token, profileMe, refreshProfile } = useAuth()
  const { locale, setLocale } = useLocale()
  const { mode: themeMode, setMode: setThemeMode } = useTheme()
  const { color: primaryColor } = usePrimaryColor()

  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)

  // 沒碰過 = undefined,完成時不寫入;顯示時退回雲端現值。
  const [draft, setDraft] = useState<{
    glass?: GlassStyle
    skin?: string
    compact?: boolean
    stockUpIsRed?: boolean
    weekStartsMonday?: boolean
    incomeIsRed?: boolean
  }>({})
  const initialColor = useRef(primaryColor)
  const overlayRef = useRef<HTMLDivElement | null>(null)

  // 引導蓋滿整個畫面:鎖住底下頁面的捲動(否則滾輪捲到底會連帶捲動後面的頁面),
  // 換步驟時捲回頂端。
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [])
  useEffect(() => {
    overlayRef.current?.scrollTo({ top: 0 })
  }, [step])

  const appearance: ProfileAppearance = profileMe?.appearance ?? {}
  const glass = draft.glass ?? resolveGlassStyle(appearance.glass_style)
  const skin = draft.skin ?? appearance.header_skin ?? 'none'
  const compact = draft.compact ?? appearance.compact_amount ?? false
  const stockUpIsRed = draft.stockUpIsRed ?? appearance.stock_up_is_red ?? true
  const weekStartsMonday =
    draft.weekStartsMonday ??
    resolveWeekStartsMonday(appearance, typeof navigator !== 'undefined' ? navigator.language : 'zh')
  const incomeIsRed = draft.incomeIsRed ?? profileMe?.income_is_red ?? true

  // ---- AI(步驟 4):跟「智慧記帳」設定頁同一條存法 ----
  const aiConfig = profileMe?.ai_config ? normalizeAiConfig(profileMe.ai_config) : null
  const aiEnabled = !!aiConfig?.bill_extraction_enabled
  const [consentOpen, setConsentOpen] = useState(false)
  const [aiSaving, setAiSaving] = useState(false)

  const saveAiEnabled = async (enabled: boolean) => {
    if (aiSaving) return
    setAiSaving(true)
    try {
      await patchProfileMe(token, {
        ai_config: mergeAiConfig(aiConfig, { bill_extraction_enabled: enabled }),
      })
      await refreshProfile()
      setConsentOpen(false)
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setAiSaving(false)
    }
  }

  const finish = async (goToAi = false) => {
    if (saving) return
    setSaving(true)
    try {
      const nextAppearance: ProfileAppearance = {
        ...appearance,
        ...(draft.glass !== undefined ? { glass_style: draft.glass } : {}),
        ...(draft.skin !== undefined ? { header_skin: draft.skin } : {}),
        ...(draft.compact !== undefined ? { compact_amount: draft.compact } : {}),
        ...(draft.stockUpIsRed !== undefined ? { stock_up_is_red: draft.stockUpIsRed } : {}),
        ...(draft.weekStartsMonday !== undefined
          ? { week_starts_monday: draft.weekStartsMonday }
          : {}),
        onboarding_done: true,
      }
      await patchProfileMe(token, {
        appearance: nextAppearance,
        ...(draft.incomeIsRed !== undefined ? { income_is_red: draft.incomeIsRed } : {}),
        ...(primaryColor !== initialColor.current ? { theme_primary_color: primaryColor } : {}),
      })
      await refreshProfile()
      onFinished()
      if (goToAi) navigate('/app/settings/ai/manage')
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setSaving(false)
    }
  }

  const isLast = step === WELCOME_STEP_COUNT - 1

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[60] overflow-y-auto overscroll-contain bg-background"
      role="dialog"
      aria-modal="true"
    >
      <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-4 py-6 sm:py-10">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-1.5" aria-hidden>
            {Array.from({ length: WELCOME_STEP_COUNT }, (_, i) => (
              <span
                key={i}
                className={`h-2 rounded-full transition-all ${
                  i === step ? 'w-6' : 'w-2'
                } ${i <= step ? 'bg-primary' : 'bg-muted-foreground/30'}`}
              />
            ))}
          </div>
          <Button variant="ghost" size="sm" disabled={saving} onClick={() => void finish()}>
            {t('welcome.skip')}
          </Button>
        </div>

        <div className="flex-1">
          {step === 0 ? (
            <StepShell icon={Palette} title={t('welcome.look.title')} desc={t('welcome.look.desc')}>
              <Group title={t('welcome.language')}>
                <div className="flex flex-wrap gap-2">
                  {WELCOME_LOCALES.map((code) => (
                    <Choice
                      key={code}
                      selected={locale === code}
                      onClick={() => setLocale(code as Locale)}
                    >
                      {code === 'zh-TW' ? '繁體中文' : 'English'}
                    </Choice>
                  ))}
                </div>
              </Group>
              <Group title={t('profile.theme.title')}>
                <Card className="bc-panel">
                  <CardContent className="p-4">
                    <PrimaryColorPicker />
                  </CardContent>
                </Card>
              </Group>
              <Group title={t('welcome.themeMode')}>
                <div className="flex flex-wrap gap-2">
                  {(
                    [
                      ['system', Monitor, 'theme.system'],
                      ['light', Sun, 'theme.light'],
                      ['dark', Moon, 'theme.dark'],
                    ] as Array<[ThemeMode, LucideIcon, string]>
                  ).map(([value, Icon, key]) => (
                    <Choice key={value} selected={themeMode === value} onClick={() => setThemeMode(value)}>
                      <Icon className="mr-1.5 inline h-4 w-4 align-[-3px]" aria-hidden />
                      {t(key)}
                    </Choice>
                  ))}
                </div>
              </Group>
            </StepShell>
          ) : null}

          {step === 1 ? (
            <StepShell icon={Layers} title={t('welcome.glass.title')} desc={t('welcome.glass.desc')}>
              <Group title={t('profile.glass.title')}>
                <div className="space-y-2">
                  {(['clear', 'balanced', 'tinted'] as GlassStyle[]).map((value) => (
                    <OptionCard
                      key={value}
                      selected={glass === value}
                      title={t(`profile.glass.${value}`)}
                      desc={t(`welcome.glass.${value}Desc`)}
                      onClick={() => {
                        setDraft((d) => ({ ...d, glass: value }))
                        applyGlassStyle(value)
                      }}
                    />
                  ))}
                </div>
              </Group>
              <Group title={t('profile.sync.headerSkin')}>
                <Select value={skin} onValueChange={(value) => setDraft((d) => ({ ...d, skin: value }))}>
                  <SelectTrigger aria-label={t('profile.sync.headerSkin')}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">{t('profile.sync.headerSkin.none')}</SelectItem>
                    {HEADER_SKINS.map((id) => (
                      <SelectItem key={id} value={id}>
                        {t(`profile.sync.headerSkin.${id}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="mt-2 text-xs text-muted-foreground">{t('welcome.skin.hint')}</p>
              </Group>
            </StepShell>
          ) : null}

          {step === 2 ? (
            <StepShell
              icon={SlidersHorizontal}
              title={t('welcome.display.title')}
              desc={t('welcome.display.desc')}
            >
              <Group title={t('welcome.weekStart')}>
                <div className="space-y-2">
                  <OptionCard
                    selected={!weekStartsMonday}
                    title={t('welcome.weekStart.sunday')}
                    onClick={() => setDraft((d) => ({ ...d, weekStartsMonday: false }))}
                  />
                  <OptionCard
                    selected={weekStartsMonday}
                    title={t('welcome.weekStart.monday')}
                    onClick={() => setDraft((d) => ({ ...d, weekStartsMonday: true }))}
                  />
                </div>
              </Group>
              <Group title={t('profile.sync.compactAmount')}>
                <div className="space-y-2">
                  <OptionCard
                    selected={!compact}
                    title={t('profile.sync.compactAmount.full')}
                    desc={t('welcome.compact.fullDesc')}
                    onClick={() => setDraft((d) => ({ ...d, compact: false }))}
                  />
                  <OptionCard
                    selected={compact}
                    title={t('profile.sync.compactAmount.compact')}
                    desc={t('welcome.compact.compactDesc')}
                    onClick={() => setDraft((d) => ({ ...d, compact: true }))}
                  />
                </div>
              </Group>
              <Group title={t('welcome.incomeColor')}>
                <div className="space-y-2">
                  <OptionCard
                    selected={incomeIsRed}
                    title={t('profile.sync.incomeScheme.red')}
                    desc={t('welcome.incomeColor.redDesc')}
                    onClick={() => {
                      setDraft((d) => ({ ...d, incomeIsRed: true }))
                      applyIncomeColorScheme(true)
                    }}
                  />
                  <OptionCard
                    selected={!incomeIsRed}
                    title={t('profile.sync.incomeScheme.green')}
                    desc={t('welcome.incomeColor.greenDesc')}
                    onClick={() => {
                      setDraft((d) => ({ ...d, incomeIsRed: false }))
                      applyIncomeColorScheme(false)
                    }}
                  />
                </div>
              </Group>
              <Group title={t('welcome.stockColor')}>
                <div className="space-y-2">
                  <OptionCard
                    selected={stockUpIsRed}
                    title={t('profile.sync.stockScheme.red')}
                    desc={t('welcome.stockColor.redDesc')}
                    onClick={() => {
                      setDraft((d) => ({ ...d, stockUpIsRed: true }))
                      applyStockColorScheme(true)
                    }}
                  />
                  <OptionCard
                    selected={!stockUpIsRed}
                    title={t('profile.sync.stockScheme.green')}
                    desc={t('welcome.stockColor.greenDesc')}
                    onClick={() => {
                      setDraft((d) => ({ ...d, stockUpIsRed: false }))
                      applyStockColorScheme(false)
                    }}
                  />
                </div>
              </Group>
            </StepShell>
          ) : null}

          {step === 3 ? (
            <StepShell icon={Sparkles} title={t('welcome.ai.title')} desc={t('welcome.ai.desc')}>
              <Card className="bc-panel">
                <CardContent className="divide-y divide-border/60 p-0">
                  <label className="flex cursor-pointer items-center justify-between gap-4 p-4">
                    <span>
                      <span className="block text-sm font-medium">{t('ai.preferences.enabled')}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {t('ai.preferences.enabledHint')}
                      </span>
                    </span>
                    <input
                      className="h-5 w-5 accent-primary"
                      type="checkbox"
                      role="switch"
                      checked={aiEnabled}
                      disabled={aiSaving || !profileMe}
                      onChange={(e) =>
                        e.target.checked ? setConsentOpen(true) : void saveAiEnabled(false)
                      }
                    />
                  </label>
                  {consentOpen ? (
                    <div className="space-y-3 bg-muted/30 p-4">
                      <p className="text-sm font-semibold">{t('ai.preferences.consentTitle')}</p>
                      <p className="whitespace-pre-line text-sm leading-relaxed">
                        {t('ai.preferences.consentBody')}
                      </p>
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" disabled={aiSaving} onClick={() => setConsentOpen(false)}>
                          {t('common.cancel')}
                        </Button>
                        <Button disabled={aiSaving} onClick={() => void saveAiEnabled(true)}>
                          {t('ai.preferences.consentAgree')}
                        </Button>
                      </div>
                    </div>
                  ) : null}
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => void finish(true)}
                    className="flex w-full items-center justify-between gap-4 p-4 text-left transition-colors hover:bg-muted/30 disabled:opacity-60"
                  >
                    <span>
                      <span className="block text-sm font-medium">{t('welcome.ai.configure')}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {t('welcome.ai.configureDesc')}
                      </span>
                    </span>
                    <span aria-hidden className="text-muted-foreground">
                      →
                    </span>
                  </button>
                </CardContent>
              </Card>
              <p className="mt-3 text-xs text-muted-foreground">{t('welcome.laterHint')}</p>
            </StepShell>
          ) : null}
        </div>

        <div className="mt-8 flex items-center justify-between">
          {step > 0 ? (
            <Button variant="ghost" disabled={saving} onClick={() => setStep(step - 1)}>
              {t('welcome.back')}
            </Button>
          ) : (
            <span />
          )}
          <Button disabled={saving} onClick={() => (isLast ? void finish() : setStep(step + 1))}>
            {isLast ? t('welcome.start') : t('welcome.next')}
          </Button>
        </div>
      </div>
    </div>
  )
}

// 與「外觀」設定頁(SettingsProfileAppearanceSection)的造型清單一致。
const HEADER_SKINS = [
  'aurora',
  'mountains',
  'bokeh',
  'waves',
  'sunset',
  'clouds',
  'skyline',
  'honeycomb',
  'starry',
  'stripes',
  'sakura',
  'meteor',
  'memphis',
]

function StepShell({
  icon: Icon,
  title,
  desc,
  children,
}: {
  icon: LucideIcon
  title: string
  desc: string
  children: ReactNode
}) {
  return (
    <div>
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/15 text-primary">
        <Icon className="h-7 w-7" />
      </div>
      <h1 className="text-xl font-bold">{title}</h1>
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{desc}</p>
      <div className="mt-6 space-y-6">{children}</div>
    </div>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 px-1 text-xs font-semibold tracking-wide text-muted-foreground">{title}</h2>
      {children}
    </section>
  )
}

/** 圓角膠囊式單選(語言、外觀模式)。 */
function Choice({
  selected,
  onClick,
  children,
}: {
  selected: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={`rounded-full border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        selected
          ? 'border-primary bg-primary/15 text-primary'
          : 'border-border/60 bg-muted/20 hover:bg-muted/40'
      }`}
    >
      {children}
    </button>
  )
}

/** 整列單選卡片:標題 + 選填說明 + 勾勾。 */
function OptionCard({
  selected,
  title,
  desc,
  onClick,
}: {
  selected: boolean
  title: string
  desc?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={`bc-panel flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        selected ? 'border-primary bg-primary/10' : 'border-border/60 hover:bg-muted/30'
      }`}
    >
      <span className="min-w-0">
        <span className={`block text-sm ${selected ? 'font-semibold' : 'font-medium'}`}>{title}</span>
        {desc ? <span className="mt-0.5 block text-xs text-muted-foreground">{desc}</span> : null}
      </span>
      <span className="flex h-5 w-5 shrink-0 items-center justify-center">
        {selected ? <Check className="h-5 w-5 text-primary" aria-hidden /> : null}
      </span>
    </button>
  )
}
