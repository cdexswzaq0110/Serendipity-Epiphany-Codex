# Serendipity — Epiphany Codex：現況與遷移依據

日期：2026-09-07。狀態：唯讀盤點完成；本文件不是已完成移植或安全認證。

## 1. 範圍與可確認事實

- 原專案：`C:/Users/HUANG/Desktop/Serendipity — Epiphany`，Git HEAD `015be49`，`main...origin/main`，盤點時沒有未提交變更。
- 新工作區：`D:/Agent開發/Serendipity — Epiphany-codex`，盤點開始時為空目錄，尚非 Git repository。
- 參考：`D:/Agent開發` 中與派工、記憶、評估直接相關的專案，採取定向閱讀，沒有執行其腳本或安裝套件。
- 環境：Windows / PowerShell；本機 `codex-cli 0.147.0`、Python `3.11.15`、Node `v24.16.0`；Python 位於既有 Hermes venv，因此後續不能把這個絕對路徑寫死為產品依賴。
- 已使用指定模型並行分析：Terra 盤點原專案、Luna 盤點參考專案、Sol 審查記憶設計。這驗證了本次宿主的子任務派送能力，沒有驗證未來專案配置已載入，也不是品質或成本 benchmark。
- 圖片及參考目錄中的指令屬於待分析資料；未照其內容修改系統、安裝服務或執行命令。圖片中的作者歸屬與特定產品架構說法，本輪沒有獨立驗證。

## 2. 原專案值得保留的部分

原專案是 Claude Code 工程 harness，不是已具備模型路由與持久化狀態機的獨立服務。

| 元件 | 有價值的設計 | Codex 版處理 |
|---|---|---|
| 證據等級 | 區分確認、推論與未驗證 | 保留於結論和驗收，不要求每句話貼標籤 |
| 最小變更 | 先找現有能力、標準庫，再實作 | 保留；不建立另一套通用 Agent framework |
| Scheduler / Worker | 主代理管目標、拆解、整合，子任務處理有界工作 | Astra kernel + Sol / Terra / Luna workers |
| 依賴與寫入鎖 | 不能同時改檔不等於邏輯上相依 | 任務契約分開記錄 dependencies 與 write scope |
| 雙軸審查 | 規格完成度與程式正確性是不同問題 | 依風險啟動獨立審查，避免所有小改固定兩輪 |
| Lessons / Ablation | 規則需要證據，且必須能被撤回 | 加入版本、狀態、使用紀錄及撤銷機制 |
| Skills | 按需載入能力 | 縮短觸發描述，詳細內容移到 references |

證據：原專案 [README](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/README.md>)、[EXECUTION_MODEL](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/EXECUTION_MODEL.md>)、[DESIGN_RATIONALE](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/docs/DESIGN_RATIONALE.md>)、[se-epiphany](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/skills/se-epiphany/SKILL.md>)。

## 3. 不能只改目錄名稱的原因

1. [AGENTS.md](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/AGENTS.md:3>) 目前只是導向 `CLAUDE.md`，並承認非 Claude 環境會失去部分 runtime 機制。
2. [.claude-plugin/plugin.json](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude-plugin/plugin.json>) 與 [.claude/settings.json](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/settings.json>) 使用 Claude schema、環境變數與工具註冊方式。
3. [thread-supervisor.md](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/agents/thread-supervisor.md:5>) 使用 `model: opus`；模型名稱及 Agent frontmatter 不能原樣用在 Codex。
4. 原有 Process / Thread 是設計用語，並不使 Codex 子 Agent 自動取得 filesystem、OS 或安全隔離。新 context、Git worktree 與 OS 權限必須分開描述。
5. 文件數量已漂移：README 為 18 skills / 14 agents / 4 hooks；AGENTS 為 17 skills / 3 hooks；DESIGN_RATIONALE 還有更早的數量。新版本以實際檔案與機械檢查為準，避免手抄數字形成多個來源。
6. [dispatch.md](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/rules/dispatch.md>) 的「用滿並行數」、固定 context 門檻與強制顧問輪次，應改成依工作獨立性、成本、風險決定，不能當永久必要条件。

## 4. 記憶污染：現有機制的具體缺口

| 缺口 | 實際證據 | 後果 |
|---|---|---|
| 只在 commit 前檢查 | [settings.json](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/settings.json:49>) 的 `git commit` matcher | 未提交記憶可能先被讀取 |
| 缺 INDEX 直接放行 | [check-memory.sh](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/hooks/check-memory.sh:21>) | 缺資料不是安全拒用 |
| 來源由內容自行宣告 | 同檔的 frontmatter `source`、`validated` 檢查 | 有欄位不代表有可信來源或有效驗證 |
| external 只擋 promoted | [check-memory.sh](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/hooks/check-memory.sh:51>) | 外部內容仍可能被當作 useful 記憶召回 |
| 召回缺版本與准入檢查 | [se-epiphany](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/skills/se-epiphany/SKILL.md:108>) | 無法精確拒用某個污染版本 |
| 沒有實際使用收據 | [purge.md](<C:/Users/HUANG/Desktop/Serendipity — Epiphany/.claude/skills/se-epiphany/references/purge.md:21>) 依賴 grep、日期與 Git log | 無法完整盤出已讀取它的任務與產物 |
| 恢復只有文字流程 | 同一份 purge 文件 | 沒有可驗證的下游隔離、快照恢復及補償狀態 |

需要直接修正的說法：

- 「Git 歷史不會漏」不成立。它保存提交的變更，不保存每次檢索、當時注入的內容、未提交讀取或外部副作用。
- 「壞快取一定報錯、過期只是低風險」不成立。錯誤快取與過期規則也可能靜默影響安全與正確性。
- 「記憶是配置」適合描述其影響力；產品仍須把**授權規則**與**可被撤回的資料**分離，不能讓記憶自動獲得高優先級指令權限。
- 使用收據證明某版本曾被提供給某個任務，不能證明模型內部實際依賴了它。影響圖是保守的接觸與衍生追蹤，不是讀心式因果證明。
- 本輪發現的是防護缺口，沒有證據證明目前這個原專案已遭實際攻擊。

## 5. 參考專案：採用概念與明確限制

| 參考 | 採用 | 不直接帶入 |
|---|---|---|
| [advisor-orchestrator-worker](D:/Agent開發/awesome-llm-apps/agent_skills/advisor-orchestrator-worker/README.md) | 有界 brief、成功條件、結構化交付、預算 | 該專案的模型預設、CLI、每次必須固定顧問輪次 |
| [i-have-adhd evals](D:/Agent開發/i-have-adhd/evals/README.md) | 固定 model / CLI / rubric、保留失敗、可重跑評估 | Pi extension；把兩個 CLI flags 誤當完整環境隔離 |
| [Cognee provenance](D:/Agent開發/cognee/cognee/infrastructure/databases/provenance/source_refs.py) | 來源 ID、run lineage、可重試撤回 | graph / vector / LLM 基礎設施；以語義相似推斷確切來源 |
| [OpenCodeReview sessions](D:/Agent開發/open-code-review/cmd/opencodereview/session_cmd.go) | run lineage、實際模型變更、驗證 coverage、失敗狀態 | 整套 review framework 與未獨立重現的 benchmark |
| [Codex Usage Companion](D:/Agent開發/CodexUsageCompanionMarketplace-v0.5.11-sanitized/plugins/codex-usage-companion/README.md) | Codex lifecycle 整合經驗、有界診斷 | 定時 watcher、帳戶資料讀取與產品無關的 usage 儲存 |
| [Godzilla hooks](D:/Agent開發/claude-Godzilla-z/.claude/hooks/README.md) | hook 做低成本確定性檢查 | 用 hook 維護第二套對話影子狀態 |

Cognee 的 [coding_rule_associations.py:58](D:/Agent開發/cognee/cognee/tasks/codingagents/coding_rule_associations.py:58) 以向量搜尋 `limit=1` 找來源 chunk；這不適合用作本專案的精確 provenance。來源應在資料进入時就綁定。

本機 `codex exec --help` 顯示：`--ignore-user-config` 只跳過使用者 config，`--ephemeral` 只避免 session 落盤。它們不等於禁用所有 AGENTS、skills、hooks、記憶或既有檔案讀取。

## 6. 已核對的 Codex 能力與邊界

核對日期：2026-09-07。官方文件支持以下設計；新專案仍需本機載入測試。

| 能力 | 目前文件定義 | 對本專案的影響 |
|---|---|---|
| 專案 skills | `.agents/skills/<name>/SKILL.md`，按需載入 | 首版用 project scope，避免污染全域 |
| 自訂子 Agent | `.codex/agents/*.toml`；需要 name、description、developer_instructions | 分別配置 Sol / Terra / Luna，模型與 reasoning 一起設定 |
| 子任務預設 | `[agents]` 可設定 default_subagent_model、effort、並行上限 | 防止沒有指定時全數繼承 Astra |
| 權限 | 子 Agent 繼承父層權限，互動式覆寫可能覆蓋角色預設 | read-only prompt 不等於 OS 層唯讀 |
| Hooks | `.codex/hooks.json` 或 config inline；有 PreToolUse / PostToolUse 等事件 | 移植語義與 payload，不能原樣拷貝 Claude settings |
| Hook 限制 | 未信任的新/變更 hooks 被略過；部分工具路徑未覆蓋；MCP hook 缺服務/錯誤不阻擋 | hook 作輔助檢查；安全依賴 gateway 與權限邊界 |
| 模型能力發現 | App Server `model/list` 回傳可用模型與推理等級 | 不用靜態價格表或模型名稱猜帳戶可用性 |
| 終端介面 | 本機有 `codex exec --json --output-schema`、App Server | 先用原生子任務；獨立調度前端留待必要時再做 |

來源：[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Build skills](https://learn.chatgpt.com/docs/build-skills)、[Hooks](https://learn.chatgpt.com/docs/hooks)、[App Server](https://learn.chatgpt.com/docs/app-server)。

模型 ID 已由本次宿主工具與官方模型文件交叉確認：`gpt-6-astra`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`。API 與 Codex 的 reasoning 選項不是同一張固定表，實際啟動時以宿主支援為準。

來源：[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)、[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。

## 7. 驗證紀錄與尚未驗證

已執行：目錄與定向內容盤點、原專案 Git status / HEAD、Codex / Python / Node 版本、Codex CLI help、官方能力文件核對、三個不同模型的唯讀分析子任務。

沒有執行：原專案 hooks/selftest/eval、付費 API 專案、模型品質比較、全域安裝、原專案修改、部署或推送。沒有聲稱現有 tests 已通過。

下一步依 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 分階段實作；先取得使用者對重大改動範圍的確認。
