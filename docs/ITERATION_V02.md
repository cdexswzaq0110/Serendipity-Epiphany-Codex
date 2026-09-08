# v0.2：執行閉環與目標 Agent

## 目標與完成條件

把已驗證的記憶治理 harness 接成可實際執行的工具。交付五項按需工程技能、Codex app-server 執行器、可核對的任務紀錄、有限修復與整合、模型決策的目標 Agent，以及同任務評測。真實推論、mock 回歸和能力查詢分別標示；模型不可用或額度不足不算成功。

## 分階段範圍

1. `.agents/skills/`：移植 discovery、system-design、debug、two-axis-review、git。保留原版有用的決策準則，移除 Claude 專屬工具名與重複常駐規則。
2. `src/se_codex/execution.py`、`runtime.py`、`cli.py`：Codex 原生 thread/turn 介面；執行紀錄；隔離 Git checkout；依賴與寫入範圍；外部驗證；最多兩輪修復、明確升級；串行整合與整合後驗證。
3. `src/se_codex/agent.py`：使用者目標與驗收 manifest → 模型觀察/決策 → 派工 → 接收工具結果 → 再決策。Kernel 預設 Astra；不以其他模型冒名替代。由操作入口明確指定模型才能做模型對照。
4. `src/se_codex/benchmark.py`、`examples/`、`tests/`、文件：固定任務、初始 commit、驗證器與資源上限；比較相同模型下的流程差異，再固定流程比較模型。保留失敗與不可用結果。

## 資料與流程

```text
goal / task manifest（使用者授權）
→ schema、路徑、DAG、模型能力檢查
→ run journal + 隔離 integration checkout
→ 原生 Codex worker threads（平行、各自 checkout）
→ 宿主模型/用量/工具事件 + 工作結果
→ 寫入範圍、固定驗收命令、diff 檢查
→ 有限修復或顯式升級
→ 串行套用 patch、再次驗證
→ 已驗證 patch；指定 apply 時才套回未變動的目標工作樹
```

Journal 記錄 requested model、host-reported model、reroute、thread/turn ID、用量、驗證結果、時間、修復、人工介入與整合結果。宿主回報不是底層權重身分的密碼學證明。不可把模型最終文字中的「測試通過」當成外部驗證。

Agent 是模型驅動決策迴圈；Harness 是狀態、工具、權限、驗收與資源界限。兩者共用執行器，保留可獨立使用的 harness CLI。

## 驗證與邊界

使用 stdlib unittest 驗證真實 Git 隔離、DAG、平行、失敗依賴阻斷、有限修復、模型不符、逾時、範圍越界、驗收不可被 worker 改寫、整合衝突與 Agent 停止條件。小規模實際 Codex 執行驗收另外留紀錄。評測只報可觀測數據；少量樣本不能推導所有專案更快或缺陷更少。

保留 v0.1 檢查點 `dcb1891`，本輪在 `feature/agent-execution`。不改桌面原版、不改全域 Codex 設定、不推送或部署。執行產物不含憑證，放專用 state 目錄；不自動重播中斷的外部操作。記憶污染時停止新派工並改用新的上下文，舊 journal 保留；不能聲稱清除宿主歷史對話。

## 落地紀錄

五項工程技能、`run / agent / benchmark / report / stop` 已實作。72 項測試中 70 項通過、2 項因 Windows symlink 環境跳過；真實 Luna kernel + Terra worker 的目標 Agent 執行成功。原生 CLI 0.147.0 仍未列出 Astra，hooks 仍未受信任，這些限制維持明示。驗收與工程審查發現的缺陷、原生模型/用量、試跑矩陣與依證據調整的項目都記錄於 [PILOT_RESULTS](PILOT_RESULTS.md)。没有新增第三方依賴。
