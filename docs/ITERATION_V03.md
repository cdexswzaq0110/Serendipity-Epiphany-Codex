# v0.3：開發閉環與事故恢復補強

日期：2026-09-22。起點：Codex 版 `641ead8`。原版 GitHub main 仍為 `015be491e07cde866ce0ca62251d49e7eff26d8d`（MIT），本輪沒有重寫原專案。

## 需求與完成邊界

保留 Astra kernel 與 Sol/Terra/Luna 任務分級、真正 Codex 派工、獨立驗收與有限修復。這輪解決已找到的故障：停止／epoch 檢查的空隙、舊 run 的副作用狀態更新、未確認副作用的提前解封，以及缺少可執行的一致備份入口。既有工程技能按需載入，沒有新增常駐提示大綱、向量庫或重型框架。

流程：使用者目標 → Astra 分解 → 按難度選 worker → 獨立副本執行 → 固定檢查 → 有限修復／升級 → epoch 再驗證 → 整合。事故旁路：quarantine → epoch 失效 + 指定任務 STOP → SQLite backup + 證據副本 → 核對／補償外部效果 → 審閱恢復 → 驗收／解封 → 新上下文。

## 分級與授權是兩個決策

| 工作 | 模型路由 | 執行邊界 |
|---|---|---|
| 簡單且驗收明確的工作 | Luna | 授權路徑、固定驗收與用量上限 |
| 一般開發／測試 | Terra | 獨立副本，保護驗收與設定 |
| 複雜或高風險實作／除錯 | Sol | 同樣受界線限制，不能自行提升權限 |
| 架構、核心調度、重大衝突 | Astra | 最終驗收；不以較便宜模型偷偷取代 |

模型能力高不代表可以發布、刪除資料或改權限。參考圖片中的金融操作只是風險分層示例，沒有把金融功能加入開發工具。

| 動作風險 | 本工具的處理 |
|---|---|
| 低：讀取、分析 | Kernel read-only；資料與契約分開 |
| 中：可逆程式碼修改 | Worker 副本 + diff 範圍檢查；`--apply` 才套回未變動目標 |
| 高：發布、刪除重要資料 | 本版沒有自動發布／刪除適配器；停在可審閱產物，由已授權操作者執行 |
| 關鍵：敏感權限、跨系統不可逆操作 | 本版不授予 worker 此能力；另接工具時需目的地／參數白名單與可核對的授權，不可由記憶或模型自批 |

這張表描述現有產品界線，不是能攔截所有任意 shell 行為的通用風險分類器。驗收 argv 是操作者信任的本機程式，具有啟動者權限；不接收外部文件所提供的任意命令。

## 五層防護對照

| 圖片層級 | 已實作 | 保證界線 |
|---|---|---|
| 模型行為 | 角色、目標、完成條件、有限修復 | prompt 不構成安全隔離 |
| 上下文邊界 | 未驗證記憶不可召回、來源／修訂／receipt、epoch；事故後新 task | 無法清洗既有模型上下文；未接 gateway 的接觸未知 |
| 工具權限 | 固定 checks／write scope；worker MCP 與 operator 操作分離；阻擋舊 run 紀錄效果 | 同 OS 使用者能直接改 DB／broker 時只能稱 governance |
| 運行時阻斷 | Codex sandbox、STOP、epoch、時間／turn／觀測 token 上限；跨 kernel-worker 固定 epoch | token 為觀測停止門檻，可能超出；STOP 需確認原生任務終止 |
| 監控治理 | 模型／thread／turn 報告、journal、effects、未決效果阻止解封、consistent backup | hash chain 不抵抗同時篡改整條日誌；operator evidence 仍是人工查核聲明 |

官方 [Codex App Server](https://learn.chatgpt.com/docs/app-server) 是沿用的原生執行協議；[模型目錄](https://developers.openai.com/api/docs/models) 提供模型描述。實際可用性以本機能力查詢為準。本輪查詢 Sol/Terra/Luna 可用，Astra 未列；hooks 為 untrusted。沒有將設定存在當成全模型驗收成功，也沒有替目前桌面任務切換全域權限。

## 參考取捨

- 原版 [Serendipity-Epiphany](https://github.com/cdexswzaq0110/Serendipity-Epiphany)：沿用已移植的需求探索、系統設計、debug、雙軸 review、Git 與記憶准入；來源本轮未變動。
- `D:/Agent System Design/src/agentkernel/{router,dispatch,reliability,context}.py`：借鑑資源互斥在派發時重查、有限升級、完整記錄失敗成本、明示 context 遺失。它的 mock 模型、記憶體冪等表與 demo 自動核准不直接移植。
- `D:/Agent開發/i-have-adhd/evals`：採同任務／同模型／同 effort 的比較思路；不以目前測試通過宣稱比原版完成率更高。
- `D:/Agent開發/milktea-agents-skills-for-claude`：參考每張 ticket 固定環境／快照／寫入範圍的抽象流程；未確認其根目錄授權，不複製文字或程式。

上述外部資料只作設計證據。本輪新增實作用 Python 標準庫自行撰寫，不匯入參考專案的執行設定、隱藏指令或額外依賴。

## 驗證與限制

以 `python se.py test` 跑離線回歸，包含真正 Git 副本／argv 驗收、記憶資料庫狀態轉移、CLI 事故保全與 SQLite integrity_check；模型輸出由測試替身控制。`python se.py demo` 提供無模型費用的記憶污染演練。`python se.py doctor --live` 只查宿主能力；缺 Astra／hook 信任時應回報未就緒。

本輪本機實際結果：`python se.py test` 共 90 項、88 通過、2 項因 Windows symlink 權限跳過，耗時 44.283 秒。`demo` 五項檢查通過；`recover --help`、文件本地連結、diff whitespace 檢查通過。完整測試包括安裝到另一個 Git 專案再執行 runtime。原始本地測試輸出保留於忽略上傳的 `.se-eval/regression-v03.txt`。

新增回歸涵蓋：驗收期間 epoch 改變、kernel 派工間隙 epoch 改變、缺 DB／scope 不建立空白記憶、未知 schema 拒絕、舊 worker 不可更新 effect、未完成補償不可解封、真實 Git 執行期間 recover 阻止套用、WAL 備份完整性、備份失敗／日誌異常／並行解封皆不可誤報成功。Windows 驗證也確認 SQLite 連線須顯式關閉；其 connection context manager 只處理交易，不能當成釋放檔案控制代碼。

本輪未重跑付費模型評測；既有 [v0.2 試跑](PILOT_RESULTS.md) 不代表 v0.3 的效率。已改善的是可重現的正確性與事故處理能力，尚未證明真實任務完成率或速度勝過原版。後續比較需固定任務、基線 SHA、模型及 effort，分別量完成率、缺陷、人工介入、時間與用量。

沒有自動清理聊天記憶、備份覆蓋 live DB、Git reset、外部效果重播、正式部署。工程修改留在獨立分支；回復程式版本不等於回復記憶庫，安全事件及撤銷紀錄應另外保留。
