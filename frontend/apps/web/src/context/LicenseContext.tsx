import { createContext, useContext, type ReactNode } from 'react'

import type { LicenseStatus } from '@beecount/api-client'

/**
 * 授權狀態上下文(docs/LICENSE_KEYS.md)。由 `app/LicenseGate.tsx` 提供 ——
 * 閘門已經 fetch 過 `/license/status`,設定頁的授權卡片直接讀這份,不用再打
 * 一次;在設定頁輸入新金鑰延長授權後呼叫 `applyStatus` 把新狀態寫回閘門。
 */
export interface LicenseContextValue {
  status: LicenseStatus | null
  /** 用 activate 回傳的最新狀態覆蓋;licensed=false 時閘門會切回金鑰輸入頁。 */
  applyStatus: (next: LicenseStatus) => void
  /** 重新 GET /license/status。 */
  refresh: () => Promise<void>
}

const LicenseContext = createContext<LicenseContextValue | null>(null)

export function LicenseProvider({ value, children }: { value: LicenseContextValue; children: ReactNode }) {
  return <LicenseContext.Provider value={value}>{children}</LicenseContext.Provider>
}

/** 在 LicenseGate 外(理論上不會發生)回 null,呼叫方自行隱藏 UI。 */
export function useLicense(): LicenseContextValue | null {
  return useContext(LicenseContext)
}
