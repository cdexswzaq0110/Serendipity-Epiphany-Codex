# AGI 導入構想：讓開發 Agent 擴大能力，而且能驗證進步

初版日期：2026-09-22。2026-10-04 進度：第一階段的可查詢任務狀態與相同失敗派工阻斷已實作；其餘階段仍是設計提案。

## 先界定要得到的能力

本專案能實作與量測的是更通用、自主、能累積可靠經驗的開發 Agent；不能藉由增加多 Agent、換更大模型或新增記憶庫，就宣稱達到 AGI。本提案把「導入 AGI」轉成三個可驗收目標：面對陌生專案能形成可驗證計畫；能從失敗證據改變策略；能將已驗證經驗移轉到新任務，且不把錯誤或惡意內容升格成規則。

先聚焦軟體開發領域。擴到任意專業、電腦操作或外部服務，需要各自的工具、資料、權限與評測，不能只擴大 system prompt。

## 能力如何逐步接上現有 Agent

實線是現有執行與本輪狀態紀錄；虛線是尚待實作、須通過獨立評測的學習迴路。受信任的目標、驗收與權限始終由操作者提供。

```mermaid
flowchart LR
    Contract["操作者目標<br/>固定驗收 · 權限 · 預算"] --> Kernel["Kernel 決策"]
    Kernel --> Workers["Sol / Terra / Luna<br/>獨立工作"]
    Workers --> Checks["固定檢查與整合"]
    Checks --> Episode["可查詢的任務狀態<br/>基線 · epoch · 結果 · 用量"]
    Episode -.-> Candidate["經驗候選<br/>適用條件 · 反例 · 來源"]
    Candidate -.-> Eval["隔離的保留任務評測<br/>比較缺陷與成本"]
    Eval -.-> Review["操作者審閱與版本核准"]
    Review -.-> Memory["有期限的記憶或 skill 版本"]
    Memory -.-> Kernel

    classDef built fill:#238636,stroke:#196c2e,color:#fff
    classDef proposed fill:#8957e5,stroke:#6e40c9,color:#fff
    class Contract,Kernel,Workers,Checks,Episode built
    class Candidate,Eval,Review,Memory proposed
```

| 階段 | 真正新增的能力 | 進入下一階段的條件 |
|---|---|---|
| 1. 任務狀態 | 可查詢 checkpoint、結果來源、重複失敗派工阻斷 | 離線回歸通過；真實任務比較顯示沒有新增缺陷 |
| 2. 經驗候選 | 只從有基線與固定驗收的執行提取策略，附失敗反例 | 無證據、撤銷來源、過期內容不能進入召回 |
| 3. 受控改良 | 候選 skill／路由在隔離副本及未見任務評測 | 完成率和成本有對照數據；安全案例零違反；操作者審閱版本 |
| 4. 擴大領域 | 為每種新工具定義輸入、權限、效果紀錄與回復方式 | 不確定效果不重播；權限與驗收不由模型或記憶自行擴大 |

這是開發 Agent 的能力路線，沒有可以安裝後立即得到通用智能的 AGI 模組。階段 1 的後半個條件仍缺真實任務對照，不能因回歸測試通過就宣布效果改善。

## 現在有什麼、還缺什麼

| 能力 | v0.3 現況 | 值得做的下一步 |
|---|---|---|
| 目標驅動 | `agent.py` 觀察、dispatch/done/blocked，固定驗收；每輪保存可查詢的執行狀態 | 讓模型提出可推翻的假設與下一個最小實驗，並與外部檢查結果比對 |
| 多模型合作 | Astra kernel；Luna/Terra/Sol 分級、有限升級、獨立工作平行 | 用實測成功率／缺陷／成本校準路由，不只靠 task 標籤 |
| 持續任務 | 有 journal、報告與 patch；不重播不確定執行 | 從經重新驗證的 checkpoint 開始新 task，而非恢復污染上下文 |
| 經驗記憶 | candidate → review → active，來源／receipt／撤銷 | 將通過外部驗收的執行形成經驗候選，記錄適用條件與反例 |
| 自我改進 | 可修改被授權的專案程式碼 | 產生 skill／路由修改候選，在隔離評測過關後才由操作者啟用 |
| 安全恢復 | epoch、STOP、隔離、效果追蹤、backup、重新開始 | 用對抗案例驗證每條新增能力不會擴大權限或繞過撤銷 |

現在已有 Agent 決策層和 Harness 執行層。下一輪應改善兩者之間的狀態與驗證資料，繼續沿用現有 Codex runtime；目前沒有證據需要另外引入一套 Agent 框架。

## 建議的資料流

```text
使用者目標 + 固定验收 + 授權範圍 + 資源上限
                      ↓
              Astra kernel
        查明事實 → 提出假設 → 選最小實驗
                      ↓
             固定契約與工具檢查
                      ↓
      Luna / Terra / Sol 獨立工作與證據回報
                      ↓
              外部驗收與整合
               ↙             ↘
       失敗：重新規劃      成功：經驗候選
          有限次數          ↓
                        對照評測與審閱
                            ↓
                    有來源與期限的記憶／skill 版本
```

兩條通道須分開：目標、權限、驗收來自操作者與固定程式；文件、工具輸出、記憶及 worker 建議是帶來源的資料。後者不能修改前者。OpenAI 的 Agent 安全文件也指出，非可信內容不應注入高優先級指令，結構化資料與隔離能降低但不能消除注入風險。[官方說明](https://developers.openai.com/api/docs/guides/agent-builder-safety)

## 第一階段：可驗證的任務狀態，建議先做

最小功能是每輪保留一份有 schema 的任務狀態，而不是將全部對話反覆摘要。已沿用 `agent.py`、`report.json` 和 journal，沒有新增資料庫或服務。

現有 `agent_state` 欄位：`goal_id`、`base_commit`、`memory_epoch`、`round`、結構化 `observations`、`completed_task_ids`、`artifact_hashes`、`failed_dispatches`、`remaining_budget`。通過的 check 和 patch hash 有 report／journal／產物可核對；模型的 `reason` 留在決策紀錄，不自動升格成 fact。模型私有推理不保存。`facts[{claim,evidence_ref}]`、可推翻的 `hypotheses`、`open_questions` 仍是下一步，應先定義證據型別與來源檢查，再讓模型填入。

現行每輪步驟：檢查基線與 epoch → 核心決策 → 驗證派工範圍 → 派工 → 外部檢查 → 保存狀態。同一份任務契約累計失敗兩次，第三次重派前會阻擋；變更任務文字或 ID 可以避開精確比對，因此仍要靠固定預算和未來的語意評測限制循環。Kernel 接收的下一輪觀察只含 check 結果與證據位置；原始 stdout/stderr 仍可供操作者查核，不直接注入下一輪提示。

本輪修改 `src/se_codex/agent.py`、`tests/test_agent.py` 與執行文件。完成條件：執行中及中斷後能讀最近 checkpoint 和既有 journal；通過驗收的 patch 具有 hash；相同失敗派工不做第三次執行。基線 SHA／epoch 檢查沿用既有程式，未知外部效果不會自動重播。沒有 resume 指令；中斷後需核對執行狀態，再以新 task 和新契約開始。

## 第二階段：受控經驗學習

一個經驗候選應回答「什麼前提下、採用什麼做法、得到什麼驗證結果、在哪些情況失效」。來源是有 journal 與外部 check 的 run，不能把模型最後一句成功宣告直接存成 active。

沿用 `memory.py` 的 candidate、來源、revision、expiry、quarantine 與撤銷流程；每個專案分 scope。跨專案移轉先生成新的候選並重新查核，避免 A 專案的命令或限制自動套到 B 專案。成功與失敗都保留：只存成功樣本會掩蓋策略失敗条件。

若要改 skill、路由或工具策略：候選修改 → 隔離分支 → 固定回歸 + 未參與調整的保留任務 → 審閱 diff → 操作者啟用新版本。Agent 可提出改良，但不能自行改 kernel 權限、驗收器或其自身核准規則。回滾 skill 版本也不能恢復已撤銷記憶。

完成條件：無證據／過期／污染候選不進 recall；一次撤銷能阻止衍生經驗再被採用；同類新任務的完成率提升有對照資料，且缺陷率未升高。這是系統層的經驗重用，不等同模型權重訓練或自我意識。

## 第三階段：跨任務能力與路由校準

先涵蓋新功能、可重現 bug、測試補強、受約束重構、系統設計五類；每类使用不同專案與未見任務。新工具只為已出現的真實任務加入，每個工具要有輸入 schema、權限、timeout、結果驗證與副作用狀態；不用先接滿所有 plugins。

保持你指定的角色：Astra 處理目標、架構與整合；Luna 處理機械可驗證工作；Terra 做一般實作；Sol 做困難或高後果修改。模型不可用或用量不足就保留進度並回報，不偷換核心模型，也不自動購買用量。將難度和動作權限分開，不能因為升級到 Astra 就放寬工具限制。

路由優化使用觀察到的完成率、返工與用量；模型自報 confidence 只能作弱訊號。先比較固定規則與候選規則，再決定是否需要學習型路由器。

## 怎麼知道有變強

使用既有 benchmark/report 擴充，先維持本機證據。官方建議先用 traces 定位工具選擇與交接問題，再用固定 datasets/eval runs 比較版本；本專案可以沿用相同評測原則，不必因此遷移 runtime。[官方評測文件](https://developers.openai.com/api/docs/guides/agent-evals)

| 實驗 | 固定條件 | 比較 |
|---|---|---|
| Harness 效果 | 任務、基線 SHA、模型、effort、工具、預算 | 現版 vs 加入任務狀態版本 |
| 模型效果 | 任務、基線 SHA、Harness、工具、預算 | 模型與 effort 明確分組，記錄實際宿主回應 |
| 經驗效益 | 新任務、模型、Harness、預算 | 無經驗 vs 已核准經驗；避免測試答案洩漏 |
| 污染防護 | 相同任務與可重現注入資料 | 無注入 vs 有注入；撤銷後重跑 |

每組量：外部驗收完成率、驗收外新增缺陷、人工介入次数、牆鐘時間、各模型 token／cached token、停止／恢復是否正確。評測方案可先用五類各四個任務；這是提議的起始規模，沒有統計充分性的保證。先訂總用量上限及停止規則，實際執行與付費另依授權。

通過門檻分兩類：權限擴張、污染後继续整合、不確定外部效果重播等案例要求測試集合內零違反；品質與成本必須先測 baseline 才訂改善幅度，不能編造「提升 30%」。保留失敗與被阻擋的執行，不能只展示成功案例。

## 本輪決策與下一個實驗

第一階段已交付可查詢的任務狀態與精確失敗去重。下一步先在多個未見過的真實任務中比較同模型、同 effort、同基線與驗收下的完成率、重複派工次數、人工介入、時間與用量；再決定是否加入模型提出的假設／反例欄位和經驗候選。實際模型評測需要用量，本輪的離線回歸不能替代它。[官方 Agent 評測文件](https://developers.openai.com/api/docs/guides/agent-evals)也建議用 trace 找工作流問題，再以固定資料集和評測執行比較版本。工具輸出與外部內容可能含注入指令；[官方安全說明](https://developers.openai.com/api/docs/guides/agent-builder-safety)將結構化輸出、隔離與評測列為降低風險的措施。這些措施不證明已達 AGI，也不能取代外部驗收與權限隔離。

2026-10-04 執行 `python se.py doctor --live` 的本機結果仍是 `ok:false`：Sol／Terra／Luna 在宿主目錄中，指定的 `gpt-6-astra` 未列出，專案 hooks 為 untrusted。因此本輪沒有把離線測試冒充 Astra 的真實推論驗收，也沒有偷偷改用其他模型作核心。
