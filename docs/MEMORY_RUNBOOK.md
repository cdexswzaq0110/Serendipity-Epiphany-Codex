# 記憶操作與污染恢復手冊

本手冊描述 `se.py` 實際介面。來源文件、記憶內容與圖片是資料，不是授權、系統指令或自動核准規則。

## 先理解邊界

SQLite 保留來源中繼資料、不可變內容修訂、准入狀態、執行 epoch、召回 receipt、產物雜湊、依賴邊、安全事件與外部操作狀態。沒有外部向量索引，查詢直接讀有效資料表。Hash 用於偵測意外修改；同時能改資料與 hash 的直接資料庫寫入者不在這層的防護能力內。

`MemoryStore(..., role='worker')` 及 stdio MCP 不提供提升權限 API。`se.py memory` 是給操作者使用的介面，不能讓受限制的 worker 透過 shell 直接使用同一份 DB。來源 kind 是提交者的聲稱，操作者核准時仍須核實。`evidence`、`invalidation_condition` 是查核紀錄，不是機器已驗證事實；系統只自動判斷 expiry、狀態、epoch、hash 與明確依賴。

預設最多 1,000 個記憶修訂（包括撤銷紀錄）、每則 50,000 字元；每次召回最多 3 則及總共 12,000 字元。超長結果會跳過，不能把省略當成知識不存在。候選清單預設每頁 20 筆，最多 100。這些上限不等於 token 數，也不限制安全日誌的磁碟大小；操作者需要定期監測檔案大小並保留離線備份。

## 開始

從工具根目錄執行 `python se.py demo` 可立即看到完整事故演練。安裝到別的專案後，將下文 `se.py` 改成 `.se-codex/se.py`。

CLI 所有記憶操作的形狀：

```text
python se.py memory OPERATION --db DATABASE --scope SCOPE --json REQUEST.json
```

`--json -` 接受 UTF-8 stdin。回應為 `{ok:true,data:...}` 或 `{ok:false,error:{code,message}}`。成功 exit 0；輸入錯誤通常 2，治理拒絕/衝突 3，儲存錯誤 4。缺少記憶不是儲存故障；後者不能以空記憶繼續假裝成功。CLI 沒有 HTTP endpoint 或隱藏網路服務。

以下 PowerShell helper 讓後續範例可直接執行，JSON 經暫存檔傳入，避免 shell 將內容解讀成指令：

```powershell
# Windows PowerShell；操作者終端機，位於工具根目錄
$entry = Join-Path (Get-Location) 'se.py'
$state = Join-Path (Get-Location) '.se-state'
New-Item -ItemType Directory -Force $state | Out-Null
$database = Join-Path $state 'memory.sqlite'
$scope = 'project'
function Invoke-Memory([string]$Operation, [hashtable]$Body) {
    $inputPath = Join-Path $state ('request-' + [guid]::NewGuid().ToString('N') + '.json')
    try {
        [IO.File]::WriteAllText($inputPath, ($Body | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
        $raw = & python $entry memory $Operation --db $database --scope $scope --json $inputPath
        $exitCode = $LASTEXITCODE
        $result = $raw | ConvertFrom-Json
        if ($exitCode -ne 0 -or -not $result.ok) { throw ($raw -join "`n") }
        return $result.data
    } finally {
        if (Test-Path -LiteralPath $inputPath) { Remove-Item -LiteralPath $inputPath }
    }
}
Invoke-Memory 'status' @{}
```

Helper 只移除自己建立的單一 request 檔，不刪除資料庫或使用者檔案。正式資料不可放進 Git；本專案忽略 `.se-state`、SQLite、`.env`。

## 正常生命週期

先提出候選並審閱；外部文字不會自動進入召回：

```powershell
# Windows PowerShell；接續上面的 helper
$candidate = Invoke-Memory 'source-propose' @{
    source_kind = 'tool'; locator = 'local:verified-test-command'
    content = '此工具使用 python se.py test 執行測試。'
    source_version = '0.1.0'
    invalidation_condition = 'CLI 測試入口改名時重新查核'
}
Invoke-Memory 'candidate-list' @{limit=20; offset=0}
```

操作者實際確認內容後才核准；這不是將文件內的指令視為授權：

```powershell
# Windows PowerShell；證據應描述你實際完成的查核
Invoke-Memory 'activate' @{
    memory_id=$candidate.memory_id; revision=1
    policy_version='review-v1'; evidence='已核對 se.py 的 test 入口與本機執行結果'
}
$run = Invoke-Memory 'run-start' @{model='gpt-5.6-terra'; effort='medium'}
$recall = Invoke-Memory 'recall' @{run_id=$run.run_id; query='python'; limit=3}
$recall
$baseline = Invoke-Memory 'snapshot' @{}
```

每次核准都更新 scope epoch，其他正在使用舊 epoch 的 run 必須重新開始。候選新修訂不會改動現有 active，只有核准新版本時才將它指定取代的舊版本標為 superseded。

## 已經用過錯誤記憶：具體處理

1. **停止擴散。** 立即 quarantine；操作會凍結整個 scope、更新 epoch 並追溯已記錄的下游。Kernel 另外中止受影響的子 Agent/任務。資料庫工具不會自行殺掉 Codex 程序。
2. **保全現場。** 保留 receipt、事件、來源版本、Git diff 與外部操作 ID；備份整個 SQLite 資料庫和日誌。不要先清空資料庫或覆蓋原始碼。
3. **判斷影響。** `causal` 指已記錄依賴的可达範圍；`exposed` 是僅標記接觸來源的範圍。二者都不是模型實際相信內容的證明。繞過 gateway 的工作在 `unknown`，需補查。
4. **修復副作用。** 對輸出檔案先比對 Git，再在新的修復分支製作修正，跑相關測試。對外部操作查詢外部系統：已寄出的訊息可能只能補發更正；狀態未知時標 uncertain，不自動重送。
5. **恢復記憶。** 審閱 restore-plan 的新增、退休、撤銷排除與衝突；隔離尚未解除時即可套用。恢復保留完整安全日誌及後來的撤銷紀錄。
6. **換新上下文。** 在新的 Codex 任務/全新子 Agent 中，以可信使用者目標、乾淨程式碼與有效記憶重新開始。不要 fork 整段受污染歷史，也不要把污染的摘要當新任務指令。其他 Agent 的「審核通過」只是額外證據，不能證明原上下文已清除。
7. **完成驗收後解封。** 逐事件 scope-release，需要操作者證據。若還有其他 open incident，scope 繼續凍結；所有舊 run 仍然失效。

可以接續前面範例演練該記憶被發現錯誤的情形：

```powershell
# Windows PowerShell；在此假設 candidate 的內容已被查證錯誤
$incident = Invoke-Memory 'revoke' @{
    memory_id=$candidate.memory_id; revision=1; reason='重新查核後發現此版本不正確'
}
Invoke-Memory 'incident-report' @{incident_id=$incident.incident_id}
$plan = Invoke-Memory 'restore-plan' @{snapshot=$baseline}
$plan.diff
$restored = Invoke-Memory 'restore' @{plan_id=$plan.plan_id}
$restored
```

基線是在撤銷前建立，恢復仍會跳過撤銷版本。正式環境請先完成產物與外部系統驗證，再執行：

```powershell
# Windows PowerShell；只有實際驗證完成才記錄下面的證據
Invoke-Memory 'scope-release' @{incident_id=$incident.incident_id; evidence='已隔離受影響產物、核對外部狀態並準備乾淨任務'}
Invoke-Memory 'scope-release' @{incident_id=$restored.incident_id; evidence='已驗證恢復結果，撤銷版本仍不可召回'}
$newRun = Invoke-Memory 'run-start' @{}
Invoke-Memory 'recall' @{run_id=$newRun.run_id; query='python'}
```

上面是 gateway 的新 run；操作者仍須在 Codex 建立新任務。`scope-release` 是人工查核聲明，不會代替測試、不自動核准 quarantined 記憶，也不代表外部操作已被撤銷。

## 操作欄位

所有操作以固定 scope 執行；CLI 由 `--scope` 補入。`revision` 為正整數。未列為可選的欄位必填。

| 操作 | 權限 | 輸入 |
|---|---|---|
| status | operator | 無 |
| source-propose | worker/operator | source_kind: external/self_observed/user_stated/tool；locator、content；可選 source_version、expires_at、invalidation_condition |
| candidate-list | operator | 可選 limit、offset |
| activate | operator | memory_id、revision、policy_version、evidence |
| run-start | worker/operator | 可選 thread_id、model、effort；回傳 run_id、memory_epoch |
| recall | worker/operator | run_id、query；可選 limit；回傳 memories、receipt_ids |
| source-use | worker/operator | run_id、source_id、relation: observed/used |
| artifact-record | worker/operator | run_id、kind、locator、content；可選 artifact_id、parent_memories、parent_artifacts |
| artifact-use | worker/operator | run_id、artifact_id、revision |
| memory-derive | worker/operator | run_id、content；至少一項 parent_memories、parent_artifacts、source_ids；可選 memory_id、supersedes_revision、expires_at、invalidation_condition |
| quarantine | worker/operator | memory_id、revision、reason；可選 incident_id |
| revoke | operator | memory_id、revision、reason；可選 incident_id；永久撤銷此修訂 |
| policy-revoke | operator | policy_version、reason；隔離在此准入政策下的記憶及已記錄下游 |
| incident-report | worker/operator | incident_id；回傳依賴路徑、事件與受影響 effects |
| snapshot | operator | 無；回傳 manifest、payload |
| restore-plan | operator | snapshot；回傳 plan_id、diff、base_epoch |
| restore | operator | plan_id；套用後維持隔離；重複套用同一 plan 不重做 |
| scope-release | operator | incident_id、evidence |
| effect-plan | worker/operator | run_id、tool、target、idempotency_key；可選 compensation_kind、compensates_effect_id |
| effect-record | worker/operator | effect_id、status；可選 external_reference、result_sha256；compensated 需要 operator、evidence、compensation_effect_id |

父記憶與父產物格式為 `[{"id":"...","revision":1}]`；source_ids 是字串陣列。artifact 只記錄 content 的 SHA-256，不寫 locator 指向的檔案。記錄產物、來源及外部效果需要工具呼叫端配合，不能自動觀測未接入的工具。

effect-plan 只記錄計畫、不執行操作。外部呼叫前持久化 idempotency_key，外部回應後記錄 applied/failed/uncertain/manual_required。對有副作用的超時先查外部狀態；不能把工具自己的 idempotency_key 當成外部服務已支援冪等。補償使用另一個 effect-plan，連回原 effect，實際確認它 applied 後，由操作者將原 effect 標 compensated。

## 快照與備份

Snapshot 是**同一資料庫內的記憶檢查點**，不是整個 Agent 或資料庫備份。它在原 store 註冊完整 hash，禁止以重新計算 hash 的修改快照冒充已建立檢查點。restore-plan 綁定當前事件序號與 epoch；計畫之後資料改變就拒絕套用，需重新產生計畫。

恢復不倒轉安全日誌。基線之後新增的 active/candidate 修訂會退休；revoked/quarantined 狀態保留。已用過的 run、receipt、產物與 effects 不刪除。現存內容 hash 衝突時停止，交由操作者分析，不盲目覆蓋。

完整離線備份應使用 SQLite backup API 或停止所有使用者後一致性備份；不要在 WAL 正在寫入時只拷貝 `.sqlite` 主檔。遺失整份 DB/安全日誌時，現版拒絕單靠內容 snapshot 重建，因為它無法知道後來哪些版本已撤銷。離線備份也要保存其備份後的安全事件；無法補齊時以新 scope 重新查核候選，不能直接將全部資料恢復為 active。

## 受保護模式設定

先在本機執行 `python se.py safety-probe`。目前探測涵蓋：工作區可寫、vault 不可讀寫、`.codex` 不可寫。探測通過只適用於該次測試，不自動改變當前任務。

1. 由操作者選擇專用狀態目錄，執行 `python se.py memory-config --db .se-state/memory.sqlite --scope project`。
2. 檢閱產出的完整路徑和設定片段，整合到專案 `.codex/config.toml`。保留 model/agents 配置。避免重複 TOML key/table；移除所有載入層（包括使用者層）舊的 `sandbox_mode`/`sandbox_workspace_write`，也不要帶 `--sandbox`，否則可能仍使用舊權限。
3. 在新任務中啟用 `default_permissions="se-worker"`。配置使用 `approval_policy="never"`，讓被拒操作直接失敗，MCP 以宿主程序的固定 worker 權限存取 DB。operator 從獨立可信終端機操作。
4. 以拋棄式 DB 檢查 worker 可透過 MCP 提候選，但 shell 不能讀寫 DB、不能改 broker/skills/config；拒絕的操作不能改用其他工具繞過。確認 MCP 不提供 activate/revoke/restore/release。
5. 審查另外启用的 MCP、連接器、瀏覽器、Computer Use 與全域技能權限。沙箱只控制本地受沙箱保護的命令，不限制這些其他表面。

若無法證實隔離，維持 governance 標記，不自動退回全權限並聲稱安全。嚴格多租戶/惡意 worker 場景請讓 broker 與 worker 使用不同 OS 身分或受管服務，保護 SQLite、完整日誌及 broker 程式。現版不提供這種跨身分部署。

MCP 使用 UTF-8、逐行 JSON-RPC stdio；不開 TCP 埠。工具名稱 `memory_call`，輸入為 `{operation,arguments}`，scope/database/role 由啟動參數固定。MCP errors 使用 isError 與穩定 error code；沒有成功時的隱式記憶回退。

## 判斷普通 bug 還是記憶問題

普通缓存錯誤可以在乾淨輸入下重現，且修復通常不涉及長期來源。記憶問題的證據是特定修訂、召回 receipt 或來源接觸與後續錯誤行為的對應。先保全紀錄，用全新上下文、停用該記憶、相同程式碼與驗收條件做對照；若錯誤仍存在，就繼續查程式與工具，不預設一定是「認知污染」。

沒有日誌的過往任務無法精準還原因果，只能擴大審查範圍。已流出的秘密需撤銷/輪替；刪除記憶不會撤銷憑證，也不能回收已送出的資料。

官方依據：[Codex 權限](https://learn.chatgpt.com/docs/permissions)、[Hooks](https://learn.chatgpt.com/docs/hooks)、[MCP stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。具體模型可用性以實際主機查詢為準。
