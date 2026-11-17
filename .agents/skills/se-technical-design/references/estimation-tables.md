# Stage 2 估算查表

判斷第一個瓶頸、以及決定擴展手段時查這兩張。

---

**Step 4：同樣是 10k QPS，不同系統痛點完全不同**

| 系統形狀 | 第一個瓶頸 |
|---|---|
| 讀多寫少的 redirect／內容頁 | cache hit rate、hot key、network bandwidth |
| 有限資源競爭（訂房、搶 Process） | row lock、transaction duration、overbooking |
| 撮合／排序敏感（交易、聊天） | 同一 key 的 ordering、fan-out、hot partition |
| 長任務（轉碼、LLM 推論） | worker capacity、queue lag、retry、timeout |
| 高頻位置／訊號上報 | 寫入吞吐（>10k/s DB 撐不住）、geo 查詢效率 |
| 海量聚合分析 | 全表掃描、OLTP 與分析爭資源 |

**Step 5：症狀 → 先想什麼，不要太早跳到什麼**

| 症狀 | 先想 | 不要太早跳到 |
|---|---|---|
| 熱門 read 很多 | Cache、read replica、materialized view | Sharding |
| 單一 row／key 被搶 | Lock、conditional update、queue by key | 更多 app servers |
| 寫入尖峰但可延遲 | Queue、batch、backpressure | 同步寫入所有下游 |
| 資料量影響維運 | Partition、archive、backup | 一開始就多 shard |
| 跨 region latency 高 | Regional cache、read replica、async replication | 每個操作都強同步跨區 |

