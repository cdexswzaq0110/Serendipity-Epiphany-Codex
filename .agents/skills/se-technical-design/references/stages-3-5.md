# Stage 3–5：實體、API、High-Level Design

走到 Stage 3 才讀這一份。Stage 0–2 與 Stage 6 之後留在 `SKILL.md`。

---

## Stage 3 — 核心實體與不變量

**先資料結構，再寫程式。用好的資料結構消掉特殊情況。**

### 挑 3–6 個 Core Entity

判準：有獨立生命週期 ∧ 會被獨立查詢 ∧ 有自己的不變量。只是另一個 Entity 的屬性 → 變成欄位。

### 每個 Entity 回答九問

```
1 資料是什麼        欄位、型別
2 從哪裡來          使用者輸入 / 外部系統 / 系統產生
3 流到哪裡          哪些下游會消費
4 誰擁有            哪個服務是 source of truth
5 誰修改            單一 writer 還是多方競爭
6 生命週期          建立 → … → 終止；保留多久
7 狀態轉換          畫出最小狀態機
8 有沒有重複資料    是否反正規化，代價是什麼
9 有沒有可以消掉的特殊情況
```

### 寫出不變量 ← 這一步決定後面的一致性設計

格式：`<在什麼範圍內>，<什麼條件>必須永遠成立`

```
同一個 home_id + date，最多只能有一筆 confirmed booking
同一台 av_id，最多只能有一筆 status IN ('ASSIGNED','IN_PROGRESS') 的 ride
同一個 chat_id 內，message_id 必須單調遞增
帳戶餘額必須 >= 0
同一個 (alert_id, version, device_id) 最多只能推播一次
```

**每一條 invariant 都要在 Stage 7 對應到一個具體的保護機制**：unique constraint / CHECK / atomic update / row lock / single-writer ownership。

### 有狀態的 Entity 一定要畫最小狀態機

```
Booking:  CREATED → RESERVED(+expiration) → PAID → COMPLETED
                         ↓                    ↓
                     EXPIRED              CANCELLED
```

**Terminal state 一定要明確定義**——polling、重試、DLQ 都依賴它。

---

## Stage 4 — API 契約

### 先分清楚 caller

| Caller | 重視什麼 | 建議風格 |
|---|---|---|
| External client | 穩定、相容、好 debug | **REST**（預設） |
| 前端需組合多資源、欄位需求變動快 | 減少 over／under-fetching | GraphQL（+ N+1、complexity、field auth 的成本） |
| Internal service | 型別契約、低延遲、吞吐 | **gRPC** |
| Admin／operator | 權限與 audit trail | REST + 嚴格 RBAC + audit log |
| Webhook | 重試、冪等、簽章 | REST + HMAC signature + event_id |

清楚的資源生命週期 → REST。否則（`MatchDriver()`、`ScoreFeed()`）→ RPC 或明確的 command endpoint，**不要硬塞成奇怪的 resource**。

### 九項 Checklist（掃過一遍就很完整）

```
□ Resource       主要資源是什麼？URL 反映 domain model 嗎？
□ Action         CRUD 還是 command？
□ Consistency    建立後立即可讀嗎？查詢是 eventually consistent 嗎？
□ Idempotency    重試 POST 會重複建立嗎？需要 Idempotency-Key 嗎？
□ Pagination     offset 還是 cursor？（持續新增／排序會變 → cursor）
□ Versioning     破壞性變更怎麼處理？
□ Security       誰可以呼叫？token scope、RBAC、ABAC、mTLS？
□ Rate limit     per user / IP / API key / tenant / endpoint？
□ Observability  request id、latency、error rate、audit log？
```

### 長任務一定用 202 模式

```http
POST /v1/qr-codes  → 202 Accepted, Location: /v1/jobs/job_123
GET  /v1/jobs/job_123 → { status, progress, result_ref }
```

---

## Stage 5 — High-Level Design

**目標：畫出一條 request 的完整旅程，先能跑再說。不要在這裡追求完美。**

從 Client-Server 那條線開始逐步加層：

```
① Client → API Server → Database        （先能跑）
② + API Gateway / Load Balancer         （入口治理）
③ + Cache                               （若 S2 判斷瓶頸在讀）
④ + Queue + Worker                      （若有可延遲的工作）
⑤ + Object Storage + CDN                （若有大型二進位）
⑥ + Search / Analytics read model       （若有搜尋或分析需求）
```

**一條 FR = 一段編號流程。** 這是最容易講清楚的格式。

**每一層標註「為什麼在這裡」**，而且產出表格的第三欄不能空：

| 元件 | 解決什麼問題 | **引入什麼新問題** |
|---|---|---|
| Cache | redirect path 每次打 DB 的 latency | stale data、stampede、hot key、cache down 時 DB 沒保護 |
| Queue | matching 失敗不該影響主服務 availability | 重複消費、積壓、DLQ、順序 |
| Sharding | 單機容量上限 | cross-shard query、resharding、hot shard |

> **第三欄是資深與初級的分野。**

---

