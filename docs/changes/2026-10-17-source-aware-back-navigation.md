# Web 返回實際來源

對齊 App 的本次返回修正:從 A 開啟詳情 B,再開編輯或關聯功能,關閉或瀏覽器
返回應逐層還原本次來源,不能先把詳情清掉再開下一層。

- `frontend/apps/web/src/lib/useHistoryDialog.ts` / `dialogHistory.ts`:使用既有
  React Router 的 location state 與瀏覽器歷史保存全域彈窗層次,不另外維護一份
  和瀏覽器脫節的返回堆疊。普通頁面保留原路由,沒有來源時不虛構固定父頁。
- `GlobalEntityDialogs.tsx`:交易、帳戶、分類、標籤詳情與週期/分期選擇、尾款
  完成、刪除確認各自保留來源。關聯交易以新歷史項目開啟,不覆寫原交易。
  交易詳情返回後重讀資料,以 replace 更新同一歷史項目。刪除成功才跳過已刪
  交易的詳情;取消確認回到原詳情。
- `GlobalEditDialogs.tsx`:交易與分類編輯走相同歷史機制,取消/關閉/儲存後
  回到原詳情,瀏覽器返回也能關閉目前的編輯層。
- `AccountDetailDialog.tsx` / `AccountsPage.tsx` / `AccountsPanel.tsx`:從信用卡
  詳情進帳戶設定時,直接帶同一份編輯器的路由狀態;帳戶表單接受受控開啟狀態,
  避免先關來源、跳帳戶頁、再送可能沒接到的編輯事件。返回回到本次來源。

入口沿用各交易/帳戶/分類/標籤列表的詳情,以及詳情內的編輯、複製、退款、
關聯交易、還款/分期與信用卡設定等按鈕,不新增另一份功能頁。

App 的功能說明、跨分頁共用主殼、iOS/Android 手勢修正詳見 App repo 的
`docs/changes/2026-10-17-source-aware-back-navigation.md`;Web 以瀏覽器歷史
對齊同一返回原則,沒有手機主殼的實作。

驗證:新增 `dialogHistory.test.ts` 覆蓋實際 React Router 的巢狀返回與跨頁返回,
執行完整 Web 單元測試與 production build。沒有修改後端、資料結構或同步契約。
