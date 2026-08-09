# SwipeSmart 外部系統整合規格書

**適用對象**：任何想要呼叫 SwipeSmart 信用卡回饋推薦引擎的外部系統（例如 BeeCount，但本文件
不綁定特定呼叫方）。

**狀態**：本文件描述的所有端點與行為皆已實作並可用（對應
[`PERSONAL_API_KEY_SD.md`](./PERSONAL_API_KEY_SD.md) 的實作成果）。文末 §9 列出的項目**尚未實作**，
僅供未來規劃參考，串接前請勿假設它們存在。

**參考文件**：本文件是給外部系統開發者看的整合規格；如果你是在改 SwipeSmart 自己的程式碼，
設計脈絡與取捨理由請看 [`PERSONAL_API_KEY_SD.md`](./PERSONAL_API_KEY_SD.md)；手把手的 Postman
操作步驟請看 [`PERSONAL_API_KEY_POSTMAN_GUIDE.md`](./PERSONAL_API_KEY_POSTMAN_GUIDE.md)。

---

## 1. 整合概觀

SwipeSmart 是一套獨立部署的信用卡回饋推薦引擎（ASP.NET Core + MariaDB），維護「銀行卡片 ×
消費分類 → 回饋規則」的資料庫。外部系統可以：

1. 傳入「金額 + 商家名稱」，取得排序後的建議刷卡清單（含預估回饋金額）。
2. 讀取 SwipeSmart 使用者自己的常用卡片、卡片目錄、分類/規則。
3. 把外部系統算出的消費明細回填給 SwipeSmart，讓 SwipeSmart 重新計算「這一期已用掉多少回饋
   上限額度」，讓下一次推薦更準確。

**這套整合不做的事**：不提供新增/修改 SwipeSmart 卡片或規則庫的外部寫入管道（維護權限集中在
SwipeSmart 自己的 Admin 後台）；不做外部系統與 SwipeSmart 之間的單一登入（見 §9）。

---

## 2. 認證：個人 API Key

### 2.1 取得方式

SwipeSmart 目前**沒有**匿名或全域服務金鑰。每一把 Key 都綁定「SwipeSmart 裡的某一個真實使用者」，
代表呼叫方是**代表這個使用者**在操作，而不是以服務身分匿名呼叫。取得方式：

1. 該使用者用瀏覽器登入 SwipeSmart。
2. 於網頁右上角「API 金鑰」分頁，輸入名稱後建立一把新 Key。
3. 系統只在建立當下回傳一次完整明文（格式 `ssm_` 開頭 + 48 碼十六進位亂數），之後永久看不到，
   請立刻複製、妥善保管，比照密碼規格對待。
4. 使用者把這把 Key 交給外部系統的維運人員，貼進外部系統的設定裡（外部系統必須**加密儲存**，
   不能明文落地、不能記錄進一般日誌）。

Key 的建立/列出/撤銷**只能透過瀏覽器 Cookie 登入操作**，外部系統無法、也不應該嘗試自動化這個
流程——這是刻意的安全邊界，防止一把外流的 Key 被拿去幫自己加開更多 Key。

### 2.2 使用方式

每個請求帶入 HTTP Header：

```
X-Service-Api-Key: ssm_8f3a1c2d9e4b7a6f0d1e2c3b4a5f6e7d8c9b0a1f2e3d4c5b
```

帶了這個 header 的請求，會被視為「該 Key 綁定的使用者」發出的請求，等同該使用者本人操作——
**除了 Admin 管理端點**（見 §2.3）。

### 2.3 權限範圍

一把 Personal API Key **等同該使用者本人的非管理權限，全有或全無**，目前不支援細粒度的
唯讀/唯寫範圍限制（scope）。具體來說：

- ✅ 可以：呼叫推薦引擎、讀寫該使用者自己的常用卡片/使用額度/卡片機敏資訊、讀取卡片/分類/
  規則目錄。
- ❌ 不行：即使這把 Key 綁定的使用者在 SwipeSmart 裡是 Admin，也**絕對拿不到**任何管理端點
  的權限（卡片/分類/規則庫的新增/修改/刪除、操作紀錄查詢）——這幾支端點永遠只認瀏覽器 Cookie
  登入，這是系統刻意的雙層防禦設計。
- ❌ 不行：管理自己或他人的 API Key（建立/列出/撤銷）——同樣只認 Cookie。

### 2.4 撤銷與生命週期

- 使用者可以隨時在網頁「API 金鑰」分頁撤銷任何一把自己的 Key，撤銷立即生效（下一個請求就會
  收到 `401`）。
- 單一使用者最多同時存在 10 把未撤銷的 Key。
- Key 目前**沒有到期日**，只能手動撤銷（v2 規劃項目，見 §9）。
- 每次驗證成功會盡力更新「上次使用時間」，使用者可以在網頁上看到，方便他自己判斷有沒有異常
  使用、要不要撤銷。

### 2.5 安全責任分工

| 責任 | 由誰負責 |
|---|---|
| Key 只在 HTTPS 連線上傳輸 | 外部系統 |
| Key 在外部系統端加密儲存、不明文落地、不寫進一般日誌 | 外部系統 |
| Key 外洩時儘速撤銷 | SwipeSmart 使用者本人（在網頁操作） |
| Key 驗證、雜湊比對、Admin 權限隔離 | SwipeSmart |

---

## 3. Base URL

由部署方式決定，例如：

- 本機開發：`http://localhost:5037` 或 `https://localhost:7234`
- NAS 正式環境：向你的 SwipeSmart 維運窗口索取實際網址

本文件之後所有路徑都是相對於這個 Base URL。

---

## 4. 端點參考

除非特別註明，所有請求都必須帶 §2.2 的 `X-Service-Api-Key` header。有 Body 的請求請帶
`Content-Type: application/json`。

### 4.1 推薦引擎

#### `POST /api/recommend`

依「金額 + 商家名稱」計算並排序推薦卡片。**無狀態設計**：不會去讀呼叫方在 SwipeSmart 裡的
個人資料，完全依 Request Body 傳入的參數計算——`userUsages`/`favoriteCardIds` 需要外部系統
自己組出來一併傳入（見 §5.1 的建議做法）。

**Request Body**

```jsonc
{
  "amount": 1000,               // number，消費金額
  "merchantName": "全聯",        // string，商家名稱（自由文字，SwipeSmart 內部會做別名/模糊比對）
  "userUsages": [                // 可省略（預設空陣列）
    { "cardId": "CARD_A", "usedCapAmount": 150.00 }
  ],
  "favoriteCardIds": ["CARD_A"] // 可省略（預設空陣列），常用卡片會在同分排序時優先
}
```

**Response `200`**：依預估回饋由高到低排序的陣列，最多 10 筆。

```jsonc
[
  {
    "card": { "cardId": "CARD_A", "bankName": "測試銀行", "cardName": "測試卡" },
    "ruleName": "餐飲",                 // 命中的分類顯示名稱
    "estimatedReward": 30.0000,          // 這筆消費預估總回饋金額（基本 + 加碼）
    "effectiveRate": 0.0300,             // 有效回饋率 = estimatedReward / amount
    "baseRate": 0.0100,                  // 實際套用的基本回饋率
    "bonusRate": 0.0200,                 // 實際套用的加碼回饋率（已扣掉超過上限的部分）
    "alertMessages": [],                 // string[]，提醒訊息（例如「加碼回饋已達上限」「需完成登錄」）
    "note": null,                        // string?，規則備註
    "isFavorite": false,
    "matchedCategoryName": "餐飲",        // 實際比對到的分類名稱
    "matchedAlias": "Restaurant"          // 實際比對到的別名/關鍵字
  }
]
```

若沒有任何卡片/規則資料，或完全沒有命中任何分類（連保底的「一般消費」規則都沒有），會回傳
`[]`，不是錯誤。

### 4.2 目錄查詢（唯讀，全域共用）

| 方法 | 路徑 | 回傳 |
|---|---|---|
| GET | `/api/cards` | `Card[]` —— `{ cardId, bankName, cardName }` |
| GET | `/api/categories` | `Category[]` —— `{ categoryId, name, aliases: string[], isExclusive }` |
| GET | `/api/rules` | `RewardRule[]` —— `{ cardId, categoryId, baseRate, bonusRate, capAmount, minSpendThreshold, requiresRegistration, expiryDate, note }`（`bonusRate`/`capAmount`/`minSpendThreshold`/`expiryDate`/`note` 皆可能為 `null`） |

這三支資料變動不頻繁（由 SwipeSmart 使用者自行維護），建議外部系統做**記憶體快取 + TTL**
（例如 1 小時），不需要每次都重打。

### 4.3 使用者個人資料（讀寫，綁定 Key 對應的使用者）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/user/favorites` | 回傳 `string[]`（常用卡片 ID 清單） |
| POST | `/api/user/favorites` | Body 直接是 `string[]`（卡片 ID 陣列，最多 10 個，**整批覆蓋**） |
| GET | `/api/user/usages` | 回傳 `[{ cardId, usedCapAmount }]`（該使用者所有卡片目前的已用回饋額度） |
| POST | `/api/user/usages` | Body `{ "CARD_A": 100.00, "CARD_B": 200.00 }`，**整戶覆蓋**——會取代該使用者名下**所有**卡片的額度紀錄，不是只改其中一張。一般不建議外部系統呼叫這支，改用下面 4.4 的 `recompute`。 |
| GET | `/api/user/cards-info` | 回傳 `[{ cardId, cardNumber, securityCode, expiryDate }]`（解密後明文，**高敏感資料**，非必要不要呼叫/記錄） |
| POST | `/api/user/cards-info` | Body `{ cardId, cardNumber, securityCode, expiryDate }`，新增/覆蓋單張卡片的機敏資訊 |

### 4.4 使用額度回填（單卡重算，推薦用這支）

#### `POST /api/user/usages/recompute`

把「這一期截至目前為止的完整消費明細」傳給 SwipeSmart，由 SwipeSmart 自己的規則庫換算成
加碼回饋金額並加總，**整批覆蓋（不是累加）**這一張卡片的 `usedCapAmount`，不影響同一使用者
名下其他卡片的額度。

**Request Body**

```jsonc
{
  "cardId": "CARD_A",
  "transactions": [
    { "amount": 1200.00, "merchantName": "全聯" },
    { "amount": 350.00,  "merchantName": "星巴克" }
  ]
}
```

- `transactions` 上限 500 筆，超過回 `400`。
- `cardId` 若不存在於 SwipeSmart 卡片目錄，回 `400`。

**Response `200`**

```json
{ "cardId": "CARD_A", "usedCapAmount": 30.0000 }
```

**語意重點（務必理解才不會用錯）**：

1. **這支不是「消費金額」，是「換算後的回饋金額」**。SwipeSmart 的 `usedCapAmount` 代表「這一期
   已經用掉多少加碼回饋上限額度」，跟消費金額本身是兩個不同的數字（只有回饋率剛好 100% 時才會
   相等）。外部系統只要老實傳「金額 + 商家名稱」，換算成回饋金額的責任完全在 SwipeSmart 這邊，
   不需要、也不應該自己重做一份回饋公式。
2. **每次都要傳完整明細，不是只傳新增的那幾筆**。這是刻意的幂等設計：外部系統那邊的消費紀錄
   可能被使用者事後修改或刪除，如果只傳「這次新增的」，SwipeSmart 沒辦法知道要「減掉」被刪除
   的那筆。改成「每次都從零重算」，同樣的輸入永遠得到同樣的結果，外部系統也不需要自己維護
   「上次回填了多少」的狀態。
3. **帳單週期的切割責任在外部系統**。SwipeSmart 不知道、也不會自動判斷「這一期」的起訖區間，
   `transactions` 應該只包含外部系統自己認定的當期消費（例如信用卡帳單週期內的消費）。
4. **卡片粒度，不分消費類別**。即使一張卡有多條不同類別的回饋規則，SwipeSmart 的
   `usedCapAmount` 是以「卡片」為單位加總，不分類別，跟 `transactions` 裡每筆消費各自比對到
   哪個分類無關，最終都加總進同一個數字。
5. **建議呼叫時機**：外部系統的使用者對某張已對應好的信用卡完成一筆記帳（新增/修改/刪除）後，
   非同步觸發（例如背景任務或短暫防抖批次），不要卡在使用者的記帳操作熱路徑上；SwipeSmart 暫時
   不可用時應該優雅降級（記錄失敗、不重試阻塞），下次觸發會用最新金額自然覆蓋。

---

## 5. 整合建議

### 5.1 `/api/recommend` 呼叫前的資料組裝

因為 `/api/recommend` 是無狀態設計，外部系統呼叫前通常需要自己組出 `userUsages`：

1. 先呼叫一次 `GET /api/user/usages`，取得該使用者在 SwipeSmart 裡所有卡片的目前額度，直接
   透傳進 `userUsages`；或
2. 如果外部系統自己就有更即時的「當期消費」資料（例如透過 §4.4 定期回填），也可以直接沿用
   §4.4 算出來的 `usedCapAmount` 自己組 `userUsages`，不必每次都多打一次
   `GET /api/user/usages`。

兩種做法皆可，取決於外部系統的資料新鮮度需求，SwipeSmart 端沒有偏好。

### 5.2 逾時與容錯

SwipeSmart 目前**沒有** rate limiting。這不代表可以無限制呼叫——外部系統應該自行對高頻操作
（例如使用者打字即時觸發的推薦查詢）做 debounce（建議 500ms 左右），避免不必要的請求量。

呼叫 SwipeSmart 逾時或服務不可用時，外部系統應該**優雅降級**（例如推薦功能暫時不顯示），
**絕不能讓 SwipeSmart 的可用性影響外部系統自己的核心功能**（例如記帳）。

### 5.3 錯誤處理

| 狀態碼 | 情境 | 建議處理 |
|---|---|---|
| `200` | 成功 | 正常處理回應 |
| `400` | 請求格式錯誤、或違反業務規則（例如 `cardId` 不存在、`transactions` 超過 500 筆） | 記錄錯誤內容（Response Body 通常是純文字錯誤訊息），修正請求，不要重試同樣的請求 |
| `401` | 沒帶 Key、Key 格式錯誤、Key 不存在、Key 已撤銷——**這四種情況回應完全相同**，故意不告訴你是哪一種，避免被拿來列舉 Key 是否存在 | 提示該使用者需要重新到 SwipeSmart 網頁建立/更新 Key |
| `403` | 目前設計下正常呼叫不會遇到（Admin 端點對 API Key 一律回 401 而非 403，因為在驗證層就被擋下，不會走到授權層） | 若真的遇到，回報 SwipeSmart 維運方 |
| `404` | 操作了不存在或不屬於自己的資源（例如撤銷別人的 Key、查詢不存在的資源） | — |
| `5xx` | SwipeSmart 服務端錯誤 | 依 §5.2 優雅降級，可視情況重試 |

**目前所有錯誤回應都是純文字或簡單 JSON 字串**，沒有統一的結構化錯誤格式（例如
`{ "error": { "code": ..., "message": ... } }`），解析錯誤訊息時請勿假設固定的 JSON 結構，
以狀態碼為主要判斷依據。

### 5.4 版本與相容性

SwipeSmart 目前**沒有** API 版本號機制（沒有 `/v1/`、`Accept-Version` header 之類的設計）。
如果之後端點的請求/回應格式需要 breaking change，會在
[`README.md`](../README.md) 與本文件中明確記錄，並建議事先跟你的 SwipeSmart 維運窗口確認
升級時程。目前的建議做法：呼叫方應該用「已知欄位」而非「完整比對」的方式解析回應 JSON
（多數 JSON 反序列化器預設行為即是如此），對未來新增欄位保持容忍。

---

## 6. 資料型別對照表

JSON 欄位一律採 camelCase（C# 端是 PascalCase record，由 ASP.NET Core 預設的
`JsonNamingPolicy.CamelCase` 轉換而來）。金額欄位型別為 .NET `decimal`，序列化後是 JSON
number，精度固定到小數點後 4 位（例如 `30.0000`），解析時請用支援任意精度的 decimal 型別
（避免用 `float`/`double` 累加誤差影響金額計算）。

| C# 型別 | JSON 範例 |
|---|---|
| `decimal` | `30.0000` |
| `decimal?` | `null` 或數字 |
| `string?` | `null` 或字串 |
| `DateTime` | ISO 8601 字串，例如 `"2026-08-09T07:27:35.63186Z"` |
| `IReadOnlyList<string>` / `List<string>` | JSON 字串陣列 |
| `bool` | `true` / `false` |

---

## 7. 安全與合規檢查清單（外部系統上線前自我檢查）

- [ ] Personal API Key 只存在加密欄位，不落地明文（比照密碼/機敏憑證規格）。
- [ ] Key 不會出現在應用程式日誌、錯誤回報、監控系統的請求記錄裡。
- [ ] 所有對 SwipeSmart 的呼叫都走 HTTPS。
- [ ] 呼叫 SwipeSmart 逾時/失敗時有優雅降級路徑，不阻塞自己系統的核心功能。
- [ ] 高頻觸發的呼叫（例如使用者輸入時即時查詢）有做 debounce。
- [ ] 有規劃「使用者撤銷/更換 Key 後怎麼處理」的流程（例如下次呼叫收到連續 `401` 時，提示
      使用者重新設定 Key，而不是無限重試）。

---

## 8. 快速測試

參考 [`PERSONAL_API_KEY_POSTMAN_GUIDE.md`](./PERSONAL_API_KEY_POSTMAN_GUIDE.md) 與同目錄的
[`SwipeSmart_PersonalApiKey.postman_collection.json`](./SwipeSmart_PersonalApiKey.postman_collection.json)。

---

## 9. 尚未實作（規劃中，勿假設存在）

以下項目目前**不存在**，串接時請勿依賴：

- **Key 細粒度權限範圍（scope）**：目前一把 Key 就是全有或全無，沒有唯讀 Key / 可寫 Key 之分。
- **Rate limiting**：沒有官方的請求頻率限制機制或回應 header（例如 `X-RateLimit-*`）。
- **Key 到期日**：Key 只能手動撤銷，沒有自動過期機制。
- **單一登入 / Token Exchange（SSO 互通）**：外部系統與 SwipeSmart 之間目前沒有共用身分提供者
  的機制，使用者必須手動在 SwipeSmart 產生 Key 並貼給外部系統，無法透過 SSO 自動代表使用者。
- **結構化錯誤回應格式**：見 §5.3，目前錯誤內容是純文字，沒有統一的錯誤碼系統。
- **API 版本號**：見 §5.4。
