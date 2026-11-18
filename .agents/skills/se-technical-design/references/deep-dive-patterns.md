# Deep Dive 模式庫

挑好 2–3 個瓶頸之後，查這裡找對應的模式與可直接套的組合。

---

### 症狀 → 模式對照

| 症狀 | 套用模式 |
|---|---|
| 同樣的答案被問太多次 | Scaling Reads：index → query shape → denormalize → replica → cache → CDN |
| 太多狀態變更、每種需要的保證不同 | Scaling Writes：把寫入分四類走不同管線 |
| 一個請求無法在 HTTP timeout 內完成 | Long Running Tasks：Job/JobRun + Watcher + Queue + Worker |
| bytes 很大但查詢的是 metadata | Large Blobs：拆五條路徑 + presigned URL + multipart |
| 資料變了 client 要很快知道 | Real-time Updates：兩個 hop |
| 需要相關性排序而非精確比對 | Search：CDC indexing pipeline + BM25 + boosting |
| 從原始事件提取洞察 | Data Pipeline：batch vs stream + CDC / fan-out / enrichment |
| LLM 需要權威／即時／專有知識 | RAG：ingestion → hybrid retrieval → rerank → grounded generation |
| 多人搶同一個有限資源 | Contention：atomic update → row lock → OCC → 預留機制 |
| 流量超過系統上限 | Overload Protection：六層防護 |
| 外部依賴不穩定 | Reliable Delivery：timeout → retry → idempotency → backoff+jitter → failover → fallback |


### 三組可直接套的組合

| 系統類型 | 三個 Deep Dive |
|---|---|
| **A 讀多** | ① read path 怎麼快（index → cache → CDN + hot key）② cache 一致性（TTL + invalidation + versioned key）③ 怎麼擴展（stateless + replica + 何時才 shard） |
| **B 資源競爭** | ① 怎麼避免 double booking（邏輯可用性／atomic update／unique constraint）② 並發時的使用者體驗（預留機制把競爭窗口從分鐘縮到毫秒）③ 尖峰不丟請求（queue + backpressure） |
| **C 高吞吐事件** | ① 怎麼承受寫入（分四類 + partition key + hot partition）② 怎麼快速聚合（OLAP + batch vs stream + materialized view）③ 怎麼不重不漏（at-least-once + 冪等 + 去重表） |

