# 韌性與可觀測性檢查表（Stage 8）

跑一遍就好，不需要每項深入。**跑過但判定不適用，要寫出不適用的理由**——空白與「不適用」是兩回事。

---

## 韌性

### 資源競爭

```
□ 有哪些操作會競爭同一份狀態？用什麼保護？
   （atomic update → row lock → OCC → 預留機制，複雜度由低到高）
□ 用了鎖 → 有序加鎖防 deadlock 了嗎？
□ 有沒有 hot partition / celebrity problem？
   能不能改變問題本身？（counter striping、預先聚合、把熱點拆成 N 個 key）
```

**複雜度階梯：先用單節點解法，撐不住才上多節點。** 能用一句 `UPDATE ... WHERE status='available'` 的 conditional update 解掉的，不要先上分散式鎖。

### 過載保護（六層）

```
□ Rate limiting        per user / IP / tenant / endpoint
□ Concurrency limiting 保護執行緒池與連線池（比 rate limit 更貼近真實資源）
□ Queue 削峰           哪些工作可以非同步？
□ Auto-scaling         觸發指標是什麼？優先用 queue depth 或 P99 latency，不是只看 CPU
□ Load shedding        過載時先丟什麼？重試請求 → 低優先級 → 非核心
□ Request prioritization / Bulkhead / Backpressure
   哪些依賴需要隔離？（3–5 個開始）下游能不能告訴上游放慢？
```

**Queue 吸收 burst，但不能掩蓋永久過載。** 佇列一直長就是容量不足，不是佇列不夠大。

### 可靠傳遞（六道防線）

```
□ Timeout        每個外部呼叫都有嗎？值怎麼定的？（下游 P99 的 2–3 倍）
□ Retry          哪些該重試？5xx / timeout 該；4xx / 業務錯誤不該
□ Idempotency    會改變狀態的操作有冪等鍵嗎？
□ Backoff+Jitter 有指數退避加抖動嗎？（沒 jitter 會同步重試造成第二波尖峰）
□ Failover       切換時會不會丟資料？同步 / 半同步 / consensus / fencing token
□ Fallback       降級回應是什麼？circuit breaker 打開後回什麼？
```

**Timeout 是最基本、也最常被遺忘的一道。** 沒有 timeout 的呼叫會把上游的執行緒池吃光，一個慢下游拖垮整條鏈。

### 資料保護

```
□ Queue 是 at-least-once → consumer 冪等了嗎？（去重表 + 同事務寫入）
□ DLQ 有告警嗎？誰處理？
□ Reconciliation job：DB 與外部系統（storage / 第三方）的對帳
□ 部分失敗的清理：上傳成功但 metadata 寫入失敗，誰負責回收？
```

---

## 可觀測性

### Metrics — 四個黃金信號

```
□ Latency     區分成功與失敗請求（失敗常常很快，會拉低平均）
□ Traffic     RPS
□ Errors      區分顯性 5xx 與隱性錯誤（回 200 但內容是錯的）
□ Saturation  CPU / 記憶體 / 連線池 / queue depth
```

**對症狀警告，不對原因警告**：

- ❌ CPU > 80%（警告疲勞，而且 CPU 高不一定有人受影響）
- ✅ 錯誤率 > 1%、P99 延遲 > 1 秒（使用者真的感受到了）

### 依系統類型的專屬指標

| 系統 | 專屬指標 |
|---|---|
| Cache | hit rate、eviction rate、hot key 分布、DB fallback QPS、origin offload |
| Queue | queue depth、oldest message age、consumer lag、retry rate、DLQ count |
| Sharded | per-shard QPS/CPU/latency、traffic skew、cross-shard query 次數 |
| 即時 | active connections、reconnect rate、pub/sub lag、per-topic subscriber |
| 搜尋 | search latency、indexing latency、sync lag、segment count、shard size |
| 長任務 | scheduled delay、processing time by job type、duplicate execution count |

> **全域平均會騙人。** 看 p95/p99、per-key、per-shard、per-tenant。一個 shard 燒到 100% 而其他九個閒著，平均只有 10%。

### Logs

結構化 JSON + correlation ID · INFO 取樣、ERROR 100% 保留 · TTL 30–90 天。

```
❌ logger.info("User 123 created order 456 for $100")
✅ logger.info("order_created", user_id=123, order_id=456, amount=100)
```

### Traces

OpenTelemetry + 跨服務 context 傳遞。

**三者的排查流程**：Metrics 告訴你**有問題**（P99 跳了）→ Traces 告訴你**在哪一段**（下游某服務慢）→ Logs 告訴你**為什麼**（那個 request 的實際錯誤）。

### SLI / SLO / Error Budget

**SLO 不應該是 100%。** 99.9% → 每月 43 分鐘預算。預算的用途是決定「現在該衝功能還是該修穩定性」。
