# v0.2 真實執行與迭代結果（2026-09-08）

## 結論

這輪完成了可實際呼叫 Codex 的執行 harness 和模型決策 Agent，並用真實專案任務跑過閉環。**沒有證據可以宣稱它已全面比原版強或更省。** 小任務對照反而揭露了技能索引過多與驗收覆蓋不足；這兩個問題已用實作調整及回歸測試處理。

## 同任務、同起點的開發期試跑

基線固定 `dcb1891d783866a4ab5e64fa08ab9d7c8ee92722`。任務為一般 worker defaults 相容性與安裝後 memory runbook 路徑；每個 arm 各做兩題、每題一次。所有 effort 都是 low。每題獨立 Git clone，固定 Python 驗收，順序由種子交錯。

| 流程 / 模型 | 原契約通過 | 後續工程檢查也通過 | 總秒數 | 原生總 token（含 cached input） | RPC 人工輸入請求 |
|---|---:|---:|---:|---:|---:|
| single-pass / Luna | 2/2 | 1/2 | 141.234 | 400,419 | 0 |
| closed-loop / Luna | 1/2 | 0/2 | 181.655 | 523,128 | 0 |
| closed-loop / Terra | 0/2 | 0/2 | 182.031 | 544,939 | 0 |

closed-loop 的三個未完成 trial 在原生用量回報超過 250,000 的門檻後停止。時間包含外層執行與檢查，token 是 host 累計用量；cached input 已含其中，不能把此數字直接換算金額。人工介入欄只統計執行時的 RPC 輸入請求，不代表開發這套工具時沒有人工／主 agent 修正。

原契約未檢查「沒有 kernel 或 specialist 設定時不得套用一般 worker 預設」。兩份 routing 產物都在事後新增的這項檢查失敗；它們各有一項已確認的工程缺陷，不能因原本驗收成功就當成正確。這是事後審查，不是預先固定的 held-out 測試。正式實作只允許一般 Terra 路由使用完整的 worker defaults，保留缺少 kernel、Sol 等設定時的明確失敗。

同模型的兩組比較可觀察工作流／prompt/context 差異；同流程與 effort 的兩組比較可觀察模型差異。樣本太小、未重複多次、開發中曾修正工具，這些只是描述性試跑。未執行原版 Claude harness，不能以此表決定原版與 Codex 版誰全面更強。

更早一次試跑因案例宣告碰到保護路徑、100,000 token 門檻不足而失敗，仍保存在本地 state；沒有從原始紀錄刪除。完成數據與限制另見 [結構化結果](PILOT_RESULTS.json)。目前範例的驗收已補強，因此不能把未來新結果與舊契約結果直接合併。

## 依結果調整

- 移除一般小任務 worker 的完整 skill index；架構與審查工作、或已觀察的驗證失敗才提供相應 skill。Kernel 保有完整短索引。
- 向 worker 提供固定驗證 argv，減少自行搜尋測試方式的往返。
- 接受結果前檢查用量；原生只提供觀測後停止，沒有硬性 token cap，文件與報告明列此界線。
- 實作 kernel/default 邊界的回歸測試，並縮小安裝文件路徑替換範圍。
- 新 benchmark 保存 manifest 快照與 executor hashes；舊試跑以各 run 已保存的 manifest、事件和 patch 回溯。

這些改動中，worker 索引縮小是在上述對照完成後採用；沒有重跑整個矩陣，因此不能宣稱表中的差距已被消除。

## 真正的目標 Agent

用同一基線的安裝器路徑問題進行完整原生執行：

```text
Luna kernel 讀取目標 → dispatch
→ Terra medium worker 實作 → 外部 Python 檢查 → 隔離整合
→ Luna kernel 讀取結果 → done
→ 最終外部檢查通過 → 產生 result.patch
```

結果 `completed`，三次原生 turn 啟動、155.672 秒、464,013 total tokens，其中 cached input 358,144。每次保留真實 thread／turn ID；底層獨立模型身分仍未知，只有宿主設定與 reroute 事件可核對。這次明確指定 Luna kernel 驗證簡單任務，不是 Astra kernel 驗收。最終 patch 確實修正了 installer；正式版採納後又將字串替換限縮至 inline runbook 路徑。

## 本機可重現驗證

執行 `python se.py test`。模型替身用於確定性的狀態機測試；Git clone、scope、驗收 argv、patch 整合、污染時 epoch 停止、依賴阻擋、並行 barrier、有限升級和 hash chain 檢查都實際執行。上述原生試跑則完全獨立於這些替身測試。

本輪不包含 production 部署、GitHub push、原生 Astra 可用性修復、所有外部工具的隔離、全域聊天清洗或大型效能結論。安全復原仍依 [記憶操作手冊](MEMORY_RUNBOOK.md) 執行。
