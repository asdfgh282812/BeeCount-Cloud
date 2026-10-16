import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import {
  listenOpenSharedJoin,
  listenOpenSharedManage,
  type OpenSharedManageDetail,
} from '../lib/sharedLedgerEvents'
import { JoinSharedLedgerDialog } from './JoinSharedLedgerDialog'
import { SharedLedgerManageDialog } from './SharedLedgerManageDialog'

/**
 * §7 共享账本 dialog 全局挂载点 — 监听 sharedLedgerEvents,任何地方
 * (CommandPalette、LedgersSection、AppHeader 等)触发都从这里弹。
 *
 * 跟 LedgersSection 本身的 dialog 互不冲突:它走的是 setState 本地控制,
 * 一个时间点只有一个 open 的 dialog,关掉后 reset。
 */
export function GlobalSharedLedgerDialogs() {
  const [joinOpen, setJoinOpen] = useState(false)
  const [manage, setManage] = useState<OpenSharedManageDetail | null>(null)
  const [inviteCode, setInviteCode] = useState('')
  const location = useLocation()
  const navigate = useNavigate()

  // /invite/<碼> 登入後會導到 /app/overview?invite=<碼>:開加入對話框並把碼從網址拿掉,
  // 避免重新整理又彈一次。
  useEffect(() => {
    const code = new URLSearchParams(location.search).get('invite')
    if (!code) return
    setInviteCode(code)
    setJoinOpen(true)
    const next = new URLSearchParams(location.search)
    next.delete('invite')
    const qs = next.toString()
    navigate(`${location.pathname}${qs ? `?${qs}` : ''}`, { replace: true })
  }, [location.pathname, location.search, navigate])

  useEffect(() => listenOpenSharedJoin(() => setJoinOpen(true)), [])
  useEffect(() => listenOpenSharedManage((d) => setManage(d)), [])

  return (
    <>
      <JoinSharedLedgerDialog
        open={joinOpen}
        onOpenChange={(o) => {
          setJoinOpen(o)
          if (!o) setInviteCode('')
        }}
        initialCode={inviteCode || undefined}
      />
      <SharedLedgerManageDialog
        open={manage != null}
        onOpenChange={(o) => { if (!o) setManage(null) }}
        ledgerId={manage?.ledgerId || ''}
        ledgerName={manage?.ledgerName || ''}
        isOwner={manage?.isOwner || false}
      />
    </>
  )
}
