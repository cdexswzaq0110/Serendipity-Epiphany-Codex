# 執行工具與目標 Agent

需要 Python 3.11+、Git、已登入且模型可用的 Codex CLI。全部 Python 程式只使用標準庫。`run`、`agent`、`benchmark` 會使用 Codex 帳號用量；`plan`、`test`、`report` 不會呼叫模型。

## 兩個入口

- **Harness：`run`** 接受已拆好的 task manifest，執行依賴排程、真實派工、範圍檢查、固定驗收、最多三次嘗試、串行整合與整合後驗收。
- **Agent：`agent`** 接受目標、授權範圍與固定驗收。Kernel 實際讀取工作樹並由模型決定 `dispatch / done / blocked`；工具執行結果成為下一輪觀察。`done` 仍要通過外部驗證。它是建立在 harness 上的模型決策迴圈，不是把固定腳本重新命名為 Agent。

Codex 原生執行能力由 [app-server thread/turn API](https://learn.chatgpt.com/docs/app-server) 提供。Python 端協調多個原生工作 task，每個工作有自己的本地 Git 副本；不需要把每個內部工作變成 Codex app 側欄中的獨立專案。

## 開始執行

目標必須是乾淨且至少有一個 commit 的本地 Git repository。這讓初始狀態、依賴輸入與 patch 都能重現。既有未提交變更會被保留，指令回報阻擋。先將自己的既有變更用正常工作流保存後再執行。

```powershell
# Windows PowerShell：在工具來源根目錄，針對指定乾淨 Git 專案
python se.py run --project C:/work/my-project --json C:/work/task.json
python se.py agent --project C:/work/my-project --json C:/work/goal.json
```

預設產出位於 `.se-state/run-<id>/` 或 `agent-<id>/`，內含輸入契約、`events.jsonl`、`report.json`、隔離副本、各任務 patch 和最終 `result.patch`。執行成功表示副本完成整合且固定檢查通過。需要套回目標時，在命令加 `--apply`；仍須目標保持原 HEAD、乾淨狀態且沒有收到停止信號。套用後保留未提交 diff，沒有自動 push 或 deploy。

安裝至另一專案時入口是 `python .se-codex/se.py`，並加 `--project .`。來源版本有 [run 範例](../examples/run.json) 和 [goal 範例](../examples/goal.json)；範例使用本專案的模組路徑和固定驗證器，適用於此專案的基線副本。

## 輸入契約

Task 保留 `plan` 的 `id / goal / kind / complexity / risk / uncertainty / write_paths / depends_on / acceptance`，再加 `verify`（check ID 清單）。Manifest 的 `checks` 定義 `{id, argv, timeout_seconds}`，`integration_checks` 定義整合時執行哪些 check。命令以 argv 直接執行，不經 shell；`{workspace}` 和 `{python}` 會替換為實際根目錄與目前 Python。

這些驗證命令是操作者授權的本地程式，具有啟動本工具的 OS 權限；不要執行來自不明文件的 manifest。Worker 可以看到驗收命令，但不能改寫主程式持有的驗收清單。預設保護 `tests/`；若任務要新增測試，可在已審閱 manifest 中精確指定 `protected_paths`，保留獨立的驗收腳本。沒有測試能完整證明不存在缺陷，驗收覆蓋範圍仍由操作者決定。

Agent 的 goal 契約使用 `goal / acceptance / write_paths / checks / integration_checks`。Kernel 只能選既有 check ID 與授權範圍內的子路徑，不能新增驗收命令或擴張權限。重要狀態保存在外層，不依赖長期記憶中的指令。

## 模型與限額

一般派工讀取 `.codex` 原生角色設定。首次失敗後以全新上下文帶入已觀察的驗證錯誤；第三次嘗試可升級 Luna → Terra、Terra → Sol、Sol → Astra。最多三次，且不可用、限額、需核准、越界或 Git 歷史改寫不當成程式錯誤無限重試。升級事件與實際宿主回應分開記錄。

Agent kernel 預設 Astra。若 host catalog 未列出它，入口回傳 `blocked`，沒有自動 alias 或降級。可對簡單示範或明確模型對照指定 `--kernel-model gpt-5.6-luna --kernel-effort low`；報告會明列此設定，不能稱為 Astra 驗收。

`max_model_calls` 是外層可計數的 turn 啟動上限；每個 Codex turn 內仍可能有多次底層模型／工具往返。`max_seconds` 在 RPC 等待、工具等待與排程边界檢查；中斷與程序清理會有短暫延遲。`max_tokens` 是 **收到用量後的停止門檻**，並非精確付費上限。進行中的推論、並行 worker 與最後一次回報可能超出門檻。沒有用量回報時不接受成功，不啟動下一次呼叫。Cached input 另列，不能把累計 token 直接當成金額。

## 紀錄能證明什麼

每次執行留 requested model/effort、宿主 thread 設定、reroute、thread/turn ID、原生用量、工具項目類型及 exit code、外部檢查輸出、修復、耗時和整合 patch hash。沒有保存模型私有推理；工具命令只記摘要 hash，最終文字和驗證輸出可能包含專案內容，需依專案資料規則保護本地 state。

`provider_inference_model: null` 表示原生協議未提供獨立的底層推論身分。宿主模型設定加 reroute 事件是可用證據，不能冒充底層權重的證明。Journal hash chain 可偵測意外編輯；同一 OS 使用者可改程式／整條日誌時，不是不可竄改的外部稽核服務。

## 停止與污染

```powershell
# Windows PowerShell；以實際回報的 run directory 替換路徑
python se.py report --run C:/work/state/run-id
python se.py stop --run C:/work/state/run-id
```

`stop` 寫入 STOP 信號；transport 會要求中斷原生 turn，阻止新派工與套用。中斷、逾時、輸入請求或失聯的執行不能自動重播。`report` 只讀紀錄；若主程序被強制終止，最後的 running 紀錄代表未完成，必須先检查副本和原生執行狀態，再以新契約建立全新執行。

Manifest 可附 `memory: {db, scope}` 綁定既有 memory store。Supervisor 在開始、工具等待、派工和整合邊界檢查 frozen/epoch；污染、撤銷或記憶變更時丟棄舊上下文並停止整合。此入口目前不自動召回或寫入長期記憶，避免未知記憶被偷偷灌入 kernel。來源／候選／receipt／回復仍走 [記憶操作手冊](MEMORY_RUNBOOK.md) 的 gateway。被污染的原生聊天不會被程式「洗乾淨」。

Worker 用 workspace-write、kernel 用 read-only，拒絕互動核准；關閉可枚舉 MCP、apps、web 與 remote plugins 的本次呼叫配置，不修改帳號全域設定。這些是本機程式的防護層，不是對惡意程式的通用隔離保證。尤其寫入範圍以 diff 驗收，不是 OS 路徑 ACL；高敏感資料仍須專用權限 profile 或独立 OS 身分。

## 驗證

`python se.py test` 只跑 deterministic 回歸。執行器測試使用真正 Git 副本和獨立 Python 檢查，模型替身只控制結果；原生推論驗收另見 [評測说明](BENCHMARK.md) 與實際結果文件。測試失敗看具名的 test、固定 check 的 stderr 與 run report，不靠重新提示模型猜原因。
