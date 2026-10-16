# 共享帳本邀請連結改用自己的網址

**問題**:App／Web 的「邀請新成員」顯示的連結是 `https://count.beejz.com/invite/XXXXXX`,那是上游作者的官網。原因是 `INVITE_SHARE_ORIGIN` 預設值寫死成該網域,`_share_url_for` 也以它當 fallback。

**改動**
- `src/config.py`:`invite_share_origin` 預設改成空字串。
- `src/routers/invites.py`:`_share_url_for(code, request)` 優先用 `INVITE_SHARE_ORIGIN`;沒設定時用請求的對外網址(看 `X-Forwarded-Proto`/`X-Forwarded-Host`,再退回 `Host`),組成 `https://<你的 Cloud>/invite/<code>`。`create_invite`、`list_invites` 補上 `request` 參數。
- `tests/test_shared_ledger_e2e.py`:斷言 `share_url` 以 `/invite/<code>` 結尾且不含 `beejz.com`。

**App 端不用改**:App 只是把 `share_url` 原樣放進分享文字(`invite_page.dart`),網域完全由 Cloud 決定。部署 Cloud 後新產生的連結就會正確;舊邀請在列表裡也會用新網域重新計算(連結不是存起來的)。

**後續**:連結點了沒反應的問題,已補上 Web `/invite/<碼>` 路由與 App `beecount://invite/<碼>`,見 App repo `docs/changes/2026-10-16-invite-link-deeplink.md`。

**部署**:反向代理後面若連結的網域不對,在 Cloud 設 `INVITE_SHARE_ORIGIN=https://你的網域`。
