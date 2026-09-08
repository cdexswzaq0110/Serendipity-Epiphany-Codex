# 驗證紀錄與交付界線

本文件保留 v0.1 的歷史結果。v0.2 執行 harness、目標 Agent 與真實任務評測見 [本輪實測](PILOT_RESULTS.md) 和 [執行手冊](EXECUTION.md)。

日期：2026-09-07。本輪交付為本機可執行版本 0.1.0；此文件區分程式測試、原生發現、沙箱探測與尚未驗證的模型推論。

## 環境

- Windows、PowerShell；Python 3.11.15、Node 24.16.0、Codex CLI 0.147.0。
- 開發分支 `feature/codex-agent-mvp`。來源桌面專案 `main...origin/main` 在最後檢查仍為乾淨，未修改。
- 使用 Python 標準庫，不安裝新的 runtime dependencies；沒有全域 Codex 設定修改或正式部署。
- 本輪曾以指定 Sol、Terra、Luna 子 Agent 分工；Sol/Terra 後续工作遇使用量限制，kernel 接手完成與驗證。這不是新安裝專案中完整模型推論驗收的替代證據。

## 已執行結果

| 驗證 | 結果 | 能證明什麼 |
|---|---|---|
| `python se.py test` | 46 tests；44 passed、2 skipped | 記憶治理、來源追溯、恢復、路由、MCP、hooks、設定產生與安裝整合 |
| `python se.py demo` | 5 個檢查全部 true | 候選不召回、receipt 先持久化、下游產物被辨識、舊快照保留撤銷、舊 run 失效 |
| `python se.py safety-probe` | exit 0、verified true | 拋棄式工作區可寫；vault 讀/寫被拒；`.codex` 修改被拒；外部核對測試檔未改變 |
| 手冊 5 段 PowerShell 合併執行 | exit 0 | 在暫存狀態目錄完成 propose / activate / recall / snapshot / revoke / restore / release |
| 真正暫存 Git 專案安裝測試 | passed | CLI 可執行、技能相對連結存在、子目錄可呼叫已安裝 hook、重複安裝不重寫 |
| MCP subprocess stdio | passed | UTF-8 中文資料、initialize/tools/list/tools/call、通知不回應、operator 方法拒絕 |
| 原生 `skills/list` | 3 個 se skills 被發現且 enabled | Codex 可發現技能；不代表模型已選用它 |
| 原生 `hooks/list` | 2 個專案 hooks 被發現；untrusted | 文件被解析；Codex 信任閘門仍未解除，不能聲稱 hook 已在模型工作中執行 |
| 原生 `model/list` | Sol/Terra/Luna 的設定等級支援；無 Astra | 不自動猜 alias；Astra 完整推論尚未驗證 |

兩個 skipped 測試需要建立真正 symlink，Windows 回 WinError 1314。已另外測試大小寫路徑衝突、越界字串、檔案作父目錄、mocked reparse ancestor 與 rollback 路徑重新驗證；這些不等於真實 junction/symlink 攻擊測試已通過。

`doctor --live` 的 `ok:false` / exit 3 是預期的當前可用性報告。`readiness` 分角色回報，`live_verified` 只指必要發現條件，`inference_verified` 保持 false。目前不把模型清單、TOML 存在或 mocked 測試當成推論成功。

## 重要回歸案例

- 未核准候選、過期內容、其他 scope 都不能被當有效記憶召回。
- 候選修訂不能提前取代已核准版本；核准後更新 epoch。
- 注入 SQLite receipt 寫入故障後，沒有記憶內容回傳，也沒有半筆 receipt。
- 並發 recall/revoke 由 SQLite 交易排序；撤銷後不接受下一個舊 run 召回。
- source → memory → run → artifact → run → derived memory / effect 的記錄依賴可被追溯；僅觀察来源列為 exposed。
- 內容 hash 不符時停止召回，仍允許操作者隔離該修訂。
- 恢復可在 frozen scope 執行，保留 revoked memory、revoked policy、下游隔離與其他 open incidents。
- 快照重新計算 payload hash 也不能冒充原 store 註冊檢查點；跨 store 匯入與過期 restore plan 被拒。
- 已撤銷來源的衍生候選不能透過重新核准繞過來源檢查；獨立查證為誤報的原始 quarantined 記憶可以由操作者以新證據重驗。永久 revoked 不可以。
- 外部 effect 只有計畫/狀態/證據紀錄，沒有偽造「已執行」。compensated 需要操作者及已 applied 的對應補償 effect。
- 安裝遇任一檔案衝突整批停止；沒有覆蓋使用者 AGENTS/config 的模式。

## 本輪修正與教訓

1. 原候選修訂建立流程會提前 supersede active；改在 operator 核准後才切换。
2. 原恢復程式要求先解除隔離；改為隔離內恢復，並維持多事件凍結，避免恢復窗口繼續工作。
3. 原下游隔離只改狀態；補安全事件，讓狀態與撤銷/隔離重播一致。
4. 快照不是模型上下文，也不是完整備份；綁原 store 與保留的安全日誌，拒絕不完整災難重建。
5. 原宿主診斷假設 skills/hooks response 是扁平陣列；實測為每 cwd 下巢狀資料，已修正並補回歸。
6. Hook 的 cwd 可能在子目錄；改用 Git root + runpy launcher，且以安裝後實際 shell 驗證。
7. 規劃器輸出保留原始寫入路徑、目標與驗收條件；另用解析後路徑判斷衝突，避免規劃時丟失 worker 所需資訊。

## 還不能宣稱的能力

- 不能偵測所有語意 prompt injection，也不能保證「模型認知已清洗」。記憶仍是資料，來源與獨立查核不可省略。
- 不可從本次沙箱 canary 推導所有 MCP/連接器/瀏覽器/核准執行都受同一權限限制。當前工作任務也沒有被自動切換為受保護模式。
- Worker 與 broker 在同一 OS 身分且可直接修改 DB/程式時，Python role 不是安全邊界。生成的配置需要在新的受限任務實際驗收。
- 沒有網路拒絕的端到端測試、跨 OS 身分部署、完整 DB 損毀恢復、語意檢索品質基準或大型負載測試。
- 沒有查詢或修改歷史 Codex 對話、全域記憶、使用者既有外部系統。工具不清空聊天，也不自動回收已送出的資料。
- 完整 Astra kernel 與原生多模型工作流仍需要模型在主機可用、額度足夠，以及該新任務的實際驗收。

## 重現

從來源根目錄執行 `python se.py test`；不需 API key 或私有測試資料。失敗時先查看測試名稱與錯誤，再定位 `tests/test_memory.py`、`test_integration.py`、`test_routing.py`、`test_runtime_hooks.py` 或 `test_install.py`。

能力與隔離檢查分別執行 `python se.py doctor --live` 和 `python se.py safety-probe`。模型清單、權限與信任狀態可能隨主機更新，請保留每次驗證的日期，不能把本紀錄當成永久保證。

## 回復與後續

本專案為獨立新增版本，可使用本地 Git 檢查點比對程式；未修改原始桌面版本。資料治理恢復走操作手冊，不以 Git reset 或舊 SQLite 主檔直接覆蓋安全日誌。

下一輪按順序完成：Astra 可用主機上的多模型推論驗收 → 實際 worker 的 MCP/權限整合 → 真正外部工具的結果與補償接入 → 依返工率與召回品質調整模型路由與檢索。
