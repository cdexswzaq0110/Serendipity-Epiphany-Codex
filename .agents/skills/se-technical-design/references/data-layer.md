# Stage 7：資料層決策

四個問題**必須全部回答**。走到 Stage 7 才讀這一份。

---

### ① 存哪裡

對每一類資料問：OLTP 還是 OLAP？資料形狀？存取模式？

| 資料 | 存在哪 | 為什麼 | Access pattern |
|---|---|---|---|

### ② 怎麼分

**先問：真的需要 shard 嗎？** 資料量 < 幾 TB、QPS 幾千 → 先調 index／cache／read replica。逼近 ~50TiB、寫入 > 10k TPS、未快取讀取要求 < 5ms、需跨區佈署 → 才考慮。

Shard key 三條件 + 一個額外檢查：高基數 · 分布均勻 · 對齊查詢模式 · **強一致性操作能不能留在單 shard**（最重要）。

⚠️ 不同資料模型可以有不同 shard key。

### ③ 怎麼複製

讀多寫少 → Single-Leader + read replica。多 DC 寫入／協作編輯 → Multi-Leader + 衝突解決。高可用寫入、可接受最終一致 → Leaderless quorum。

金融／庫存 → 半同步。一般 → 非同步 + 處理 replication lag 三問題。

**必答：Read-After-Write 怎麼處理？** 自己的資料讀 primary／追蹤 LSN／短暫讀 primary 視窗。

### ④ 怎麼一致

**把系統拆成 path，逐一決定**——這是七條鐵律第 3 條的落地：

| Path | 讀到舊資料的後果 | 選擇 | 保護機制 |
|---|---|---|---|
| Booking | 超賣、不可逆 | CP | transaction + unique constraint |
| Browse | 短暫看到舊價格 | AP | cache + 短 TTL |
| Search | 剛下架的還看得到 | AP | 但 booking 前回 source of truth 驗證 |

跨服務時：❌ 不要硬套 2PC ✅ Transactional Outbox + Saga + Idempotency。

**產出必含 Invariant → 保護機制對照表。**

---

