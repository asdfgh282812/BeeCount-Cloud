/**
 * AI 配置 merge / delete-fallback helper —— 跟 mobile `AIProviderManager`
 * 行为对齐。
 *
 * server 的 PATCH /profile/me 对 ai_config 是**整体替换**语义:web 任何
 * 修改都得先读当前 ai_config + 改动 + 整体推,否则未改动的字段
 * (custom_prompt / strategy / bill_extraction_enabled / use_vision)会被
 * 默认值覆盖。
 *
 * 设计:.docs/web-ai-config-edit.md §3.3 / §3.4
 */
import type { AICapabilityBinding, AIConfig, AIProvider } from '@beecount/api-client'
import { BUILTIN_PROVIDER_ID } from '@beecount/api-client'

/** 移除未設定的歷史內建預設，保留已設定者為可編輯／刪除的一般服務商。 */
export function normalizeAiConfig(current: Record<string, any> | null | undefined): Record<string, any> {
  const config = { ...(current ?? {}) }
  if (!Array.isArray(config.providers)) return config
  config.providers = config.providers
    .filter((p: AIProvider) => !(p.id === BUILTIN_PROVIDER_ID && p.isBuiltIn && !p.apiKey?.trim()))
    .map((p: AIProvider) => p.id === BUILTIN_PROVIDER_ID && p.isBuiltIn ? { ...p, isBuiltIn: false } : p)
  if (config.binding && typeof config.binding === 'object') {
    config.binding = { ...config.binding }
    if (!config.providers.some((p: AIProvider) => p.id === BUILTIN_PROVIDER_ID)) {
      for (const key of ['textProviderId', 'visionProviderId', 'speechProviderId']) {
        if (config.binding[key] === BUILTIN_PROVIDER_ID) config.binding[key] = null
      }
    }
  }
  return config
}

/**
 * Merge 当前 ai_config 和增量 patch,返回整体替换用的 snapshot。
 *
 * 关键点:
 *  - `...(current ?? {})` spread 让所有现有字段(含未来 mobile 加的)透传保留
 *  - 只对 providers / binding 给结构默认值兜底
 *  - **不**给其余字段(custom_prompt / strategy / bill_extraction_enabled
 *    / use_vision)写默认值 —— 否则 current 没有这字段时,我们写 `''` 或 `false`
 *    会通过 PATCH 推到 server,server 广播给 mobile,**mobile applyFromServer
 *    会把本地真实值覆盖成空**。曾经触发过 mobile 端用户 prompt 被 web 端清掉
 *    的回归。
 *
 *    判断规则:仅在 patch 显式传入时才写;否则交给 ...current spread 透传(有
 *    就保留,没有就保持没有)。
 */
export function mergeAiConfig(
  current: Record<string, any> | null | undefined,
  patch: Partial<AIConfig>
): AIConfig {
  const safeCurrent = normalizeAiConfig(current)
  const result: Record<string, any> = {
    ...safeCurrent,
    providers: patch.providers ?? safeCurrent.providers ?? [],
    binding: patch.binding ?? safeCurrent.binding ?? {},
  }
  // 共用偏好:仅在 patch 明确指定时覆盖;否则 ...safeCurrent spread 已透传
  if (patch.custom_prompt !== undefined) result.custom_prompt = patch.custom_prompt
  if (patch.strategy !== undefined) result.strategy = patch.strategy
  if (patch.bill_extraction_enabled !== undefined) {
    result.bill_extraction_enabled = patch.bill_extraction_enabled
  }
  if (patch.use_vision !== undefined) result.use_vision = patch.use_vision
  for (const key of ['project_assign_mode', 'tag_assign_mode', 'voice_trigger_mode', 'voice_silence_timeout_ms'] as const) {
    if (patch[key] !== undefined) result[key] = patch[key]
  }
  return normalizeAiConfig(result) as AIConfig
}

/**
 * 删除一个 provider，并将使用它的能力设为未绑定。
 *
 * 返回 `null` = 拒绝删除(目标是内置 / 不存在)。这是跟 mobile
 * `AIProviderManager.deleteProvider` 第 170-173 行的硬约束对齐 —— UI 层
 * 不应该暴露删除内置的路径,helper 这里再做一次防御性检查。
 */
export function applyDeleteFallback(
  providers: AIProvider[],
  binding: AICapabilityBinding,
  deletedId: string
): { providers: AIProvider[]; binding: AICapabilityBinding } | null {
  providers = normalizeAiConfig({ providers }).providers
  const target = providers.find((p) => p.id === deletedId)
  if (!target) return null
  if (target.isBuiltIn) return null

  const nextProviders = providers.filter((p) => p.id !== deletedId)
  const next: AICapabilityBinding = { ...binding }
  if (next.textProviderId === deletedId) next.textProviderId = null
  if (next.visionProviderId === deletedId) next.visionProviderId = null
  if (next.speechProviderId === deletedId) next.speechProviderId = null
  return { providers: nextProviders, binding: next }
}

/** 当前 binding 里被指定 providerId 占用的能力名,用于 delete confirm 时显示。 */
export function capabilitiesBoundTo(
  binding: AICapabilityBinding | null | undefined,
  providerId: string
): Array<'text' | 'vision' | 'speech'> {
  const result: Array<'text' | 'vision' | 'speech'> = []
  if (!binding) return result
  if (binding.textProviderId === providerId) result.push('text')
  if (binding.visionProviderId === providerId) result.push('vision')
  if (binding.speechProviderId === providerId) result.push('speech')
  return result
}
