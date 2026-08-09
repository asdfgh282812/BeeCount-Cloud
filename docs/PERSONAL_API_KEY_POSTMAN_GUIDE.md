# 個人 API Key —— Postman 手動測試手冊

本文件對應 [`PERSONAL_API_KEY_SD.md`](./PERSONAL_API_KEY_SD.md) 的實作結果，教你怎麼從零開始
拿到一把個人 API Key，並用 Postman 實際打打看。同目錄下有一份可以直接匯入 Postman 的收藏集
[`SwipeSmart_PersonalApiKey.postman_collection.json`](./SwipeSmart_PersonalApiKey.postman_collection.json)，
省去手動一支一支新增請求的麻煩。

---

## 0. 前置需求

- 一個可以連得到的 SwipeSmart 服務位址（NAS 正式環境，或你自己本機 `dotnet run` 起來的環境）。
- 一個 SwipeSmart 帳號（正式環境走 Synology SSO 登入；本機開發環境可以用 `/dev-login` 走捷徑，
  不需要真的接 SSO）。
- Postman（或任何能自訂 Header 的 HTTP 工具）。

> 正式環境如果是自簽憑證或內網憑證，Postman 可能會擋 SSL 驗證——如果打 API 時看到
> `SELF_SIGNED_CERT_IN_CHAIN` 之類的錯誤，到 Postman 設定 `Settings → SSL certificate verification`
> 關掉即可（僅限你信任的內網服務）。

---

## 1. 第一步：登入網頁，建立你的第一把 API Key

Personal API Key 的建立/列出/撤銷**只能透過瀏覽器 Cookie 登入操作**，不能用另一把 API Key
建立新的 Key（防止一把外流的 Key 被拿去幫自己加開更多 Key）。所以第一步一定要先在瀏覽器操作：

1. 用瀏覽器打開 SwipeSmart 網站，正常登入（正式環境會走 SSO；本機開發可以直接打
   `http://localhost:5037/dev-login` 建立一個本機測試帳號並自動登入）。
2. 登入後，畫面右上角（手機版在漢堡選單抽屜裡）會多一個「**API 金鑰**」分頁，點進去。
3. 在輸入框填一個好辨識的名稱（例如 `Postman 測試`、`BeeCount`），按「建立新 Key」。
4. 畫面會彈出一段**只會顯示這一次**的完整明文 Key，長得像：

   ```
   ssm_8f3a1c2d9e4b7a6f0d1e2c3b4a5f6e7d8c9b0a1f2e3d4c5b
   ```

   立刻複製起來，關閉這個提示之後就**永久看不到完整內容**了（畫面上之後只會顯示前 8 碼遮罩，
   例如 `ssm_8f3a••••`），如果忘記複製，只能撤銷這把重新建一把。

這把 Key 代表**你本人**的身分（除了 Admin 管理端點以外的完整權限），請當成密碼一樣保管，
不要貼到公開的地方。

---

## 2. 第二步：Postman 基本設定

### 方式 A：直接匯入現成的收藏集（推薦）

1. Postman → `Import` → 選擇本目錄的
   `SwipeSmart_PersonalApiKey.postman_collection.json`。
2. 匯入後在收藏集的 `Variables` 分頁，把：
   - `base_url` 改成你的 SwipeSmart 服務位址（例如 `https://localhost:7234` 或你 NAS 的網址，
     **結尾不要加斜線**）。
   - `api_key` 貼上第 1 步拿到的完整明文 Key。
3. 收藏集裡每一支請求都已經帶好 `X-Service-Api-Key: {{api_key}}` header，直接點 `Send` 就能測。

### 方式 B：手動新增請求

如果想自己手動建立請求，記得每一支都要帶這個 Header：

| Header | 值 |
|---|---|
| `X-Service-Api-Key` | 你的完整明文 Key（`ssm_` 開頭那一長串） |
| `Content-Type` | `application/json`（有 Body 的請求才需要） |

---

## 3. 第三步：逐一測試端點

以下每支都可以直接在收藏集裡找到對應請求。**注意**：`/api/cards`、`/api/categories`、
`/api/rules` 是**全域共用**資料（不分使用者），如果你的 SwipeSmart 資料庫裡還沒有任何卡片/
分類/規則，前三支查詢會回傳空陣列 `[]`，這是正常的，不代表 Key 失效——先用網頁的管理後台
（Admin 帳號）建一張測試卡片/分類/規則再測 `/api/recommend` 會更有感覺。

### 3.1 查詢類（唯讀）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/cards` | 卡片目錄 |
| GET | `/api/categories` | 商家分類與別名 |
| GET | `/api/rules` | 回饋規則（卡片 × 分類） |
| GET | `/api/user/favorites` | 你自己的常用卡片 ID 清單 |
| GET | `/api/user/usages` | 你自己各卡片目前的已用回饋額度 |
| GET | `/api/user/cards-info` | 你自己登錄過的卡號/安全碼（回傳解密後明文，注意保密） |

**預期**：全部回 `200`，內容是對應的 JSON 陣列（沒有資料就是 `[]`）。

### 3.2 推薦引擎（核心功能）

```
POST /api/recommend
Content-Type: application/json

{
  "amount": 1000,
  "merchantName": "全聯",
  "userUsages": [],
  "favoriteCardIds": []
}
```

**預期**：`200`，回傳依預估回饋排序的卡片清單（陣列）。如果資料庫裡沒有任何卡片/規則會是
`[]`——先建一些測試資料再試。

### 3.3 使用額度回填（本次新增的重點端點）

```
POST /api/user/usages/recompute
Content-Type: application/json

{
  "cardId": "TEST_CARD",
  "transactions": [
    { "amount": 1200.00, "merchantName": "全聯" },
    { "amount": 350.00,  "merchantName": "星巴克" }
  ]
}
```

**預期**：`200`，回傳 `{ "cardId": "TEST_CARD", "usedCapAmount": <數字> }`——這個數字是
SwipeSmart 用規則庫把這幾筆消費換算成的加碼回饋金額**總和**，且是**整批覆蓋**這張卡的
`UsedCapAmount`（不是累加），所以每次呼叫都要傳「這一期截至目前為止的完整明細」，不是只傳
這次新增的那幾筆。呼叫後可以立刻用 `GET /api/user/usages` 驗證確實有寫入。

若 `cardId` 在 SwipeSmart 卡片目錄裡不存在，會收到 `400 Bad Request`。

### 3.4 帶入卡片使用資訊（寫入）

```
POST /api/user/favorites
Content-Type: application/json

["TEST_CARD"]
```

```
POST /api/user/usages
Content-Type: application/json

{ "TEST_CARD": 100.00 }
```

> 這支跟 3.3 的 `recompute` 不同：這支是**整戶覆蓋**——傳進去的 dict 會取代你名下**所有**
> 卡片的已用額度紀錄，不是只改其中一張。測試時如果你名下已經有其他卡片的額度資料，用這支會
> 把它們全部清空，測試前請注意。

---

## 4. 第四步：驗證安全邊界（確認「沒問題」的關鍵）

Personal API Key **刻意做不到**以下幾件事，這是設計上的安全防線，用 Postman 驗證它們
**應該要失敗**才是「沒問題」：

| 測試 | 方法/路徑 | 預期結果 |
|---|---|---|
| Key 不能拿去做 Admin 管理操作 | `POST /api/cards`（帶 Key） | `401 Unauthorized` |
| Key 不能拿去看操作紀錄 | `GET /api/admin/logs`（帶 Key） | `401 Unauthorized` |
| Key 不能拿去管理 Key 本身 | `GET /api/user/api-keys`（帶 Key，不是瀏覽器 Cookie） | `401 Unauthorized` |
| 沒帶 Key、也沒登入 Cookie | 任一 `/api/*` 端點 | `401 Unauthorized`（乾淨的 401，不會被轉址到 SSO 登入頁） |
| 帶一把亂打的假 Key | 任一端點 | `401 Unauthorized`（跟「Key 不存在」與「Key 已撤銷」回應完全一樣，不會洩漏差異） |
| 撤銷後的 Key | 撤銷後再打任一端點 | `401 Unauthorized`（立即失效） |

收藏集裡的 `Security Boundary Checks (expect 401)` 資料夾已經幫你準備好前 3 項請求，
直接送出確認狀態碼是 `401` 即可。

**撤銷測試**：回到網頁「API 金鑰」分頁，把測試用的 Key 按撤銷（會要求二次確認），撤銷後
立刻回 Postman 重新送出任一支帶 Key 的請求，應該變成 `401`。

---

## 5. 常見狀況排解

| 現象 | 可能原因 |
|---|---|
| 所有請求都是 `401` | Header 名稱打錯（必須完全是 `X-Service-Api-Key`）、Key 貼錯/多了空白、Key 已被撤銷 |
| `/api/recommend`、`/api/cards` 等回 `[]` | 資料庫裡還沒有卡片/分類/規則資料，屬正常，先用 Admin 帳號的網頁後台建幾筆測試資料 |
| `POST /api/user/usages/recompute` 回 `400` | `cardId` 在卡片目錄裡不存在，或 `transactions` 超過 500 筆 |
| Postman 顯示憑證錯誤 | 內網/自簽憑證，關閉 Postman 的 SSL certificate verification（僅限你信任的內網服務） |
| 建立第 11 把 Key 時被拒絕 | 單一使用者最多同時存在 10 把未撤銷的 Key，先到網頁撤銷幾把舊的 |

---

## 6. 這次連帶修好的一個小問題

實作過程中發現一個既有（非本次新增）的行為：所有沒有指定管理員權限的 `/api/*` 端點，先前
在「完全沒帶登入憑證」的情況下，會回傳 `302` 轉址到 SSO 登入頁，而不是乾淨的 `401`——對瀏覽器
使用者沒差（反正就是被導去登入），但對 Postman/程式呼叫來說，`302` 轉址容易被誤判成別的錯誤，
不容易判斷「是不是我的 Key 失效了」。這次一併修正成回傳標準的 `401`，上面第 4 節的測試結果
已經反映這個修正後的行為。
