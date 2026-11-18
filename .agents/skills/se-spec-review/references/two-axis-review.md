> 2.1.0 起 `se-two-axis-review` 併入 `se-spec-review` 與 `se-quality-review`；本檔是原 skill 的完整內文，原樣保留，只修正搬家後的相對連結。

# Two-Axis Review — 雙軸審查

確認目前成果**能不能交付**，不為找問題而找問題。

## 為什麼兩軸不能合併

一份變更可以過一軸卻掛另一軸：

- 遵守了每一條規範，但**做錯東西** → Standards 過，Spec 掛。
- 完全照 Process 做了，但**破壞專案慣例** → Spec 過，Standards 掛。

**「規範全過但做錯東西」是最貴的失敗。** 合併排名會讓 8 個 Standards 小問題把 1 個 Spec 致命問題壓下去。所以：分開報、**不排名、不跨軸 rerank**。

---

## Phase 1 — 釘住基準

1. 使用者指定的固定點（commit SHA、branch、tag、`main`、`HEAD~5`）。沒指定就問。
2. 確認 ref 解得開（`git rev-parse <fixed-point>`）且 diff 非空。**壞 ref 或空 diff 要在這裡失敗，不要在兩個 Thread 裡面才失敗。**
3. 記下 diff 指令（三點式，比的是 merge-base）與 commit 列表：

```bash
git diff <fixed-point>...HEAD
git log <fixed-point>..HEAD --oneline
```

4. **凍結 Snapshot**。兩個 Thread 拿到的必須是同一份。

## Phase 2 — 找兩軸的來源

**Spec 來源**（依序）：commit message 裡的 issue 參照 → 使用者給的路徑 → `docs/`、`specs/`、`.scratch/` 下與分支同名的規格 → 問使用者。都沒有 → Spec Thread 跳過並在報告寫「無規格可比對」。

**Standards 來源**：repo 裡任何規範文件（`CONTRIBUTING.md`、`CODING_STANDARDS.md`、`CLAUDE.md`、`CONTEXT.md`）。

無論 repo 寫了什麼，Standards 軸永遠附帶 `se-quality-review` 的 `references/smell-baseline.md` 的 code smell 基線——**repo 的明文規範永遠優先**，衝突時壓過基線。

## Phase 3 — 派兩個隔離 Thread

**首輪必須互相隔離。** Reviewer A 不得看 Reviewer B 的結論，反之亦然。並行數不足時依序派，但兩人的首輪結論仍必須各自獨立產生。

Thread 契約：

```text
你是本次審查的臨時 Reviewer <A／B>，只執行指定的 <Spec／Standards> 軸。
你不是 Developer，也不管理共識。

只審查提供的固定 Snapshot、核准文件、相關程式碼與 Developer 的證據。
首輪不得查看另一位 Reviewer 的結論，也不得自行擴大到另一軸。

只回報有證據的 Finding；零 Finding 是有效結果。
可讀取程式並執行不修改專案的驗證。
不得修改檔案、Commit、Push、派 Agent，或宣稱三方共識。

回報：Reviewer 身分、指定軸、Snapshot、Findings、驗證指令、退出碼、結論。
```

| 軸 | 檢查什麼 |
|---|---|
| **Spec（Reviewer A）** | 每項交付成果與驗收條件是否成立且有可重現證據；行為是否錯誤、缺漏、只做一半或**加入未核准內容（範圍蔓延）**；錯誤、邊界、失敗路徑是否符合需求；結果能否被使用者直接觀察 |
| **Standards（Reviewer B）** | 驗收與測試證據是否可信、測試是否真抓得到錯誤行為；資料流、命名、模組責任、公開介面、錯誤處理是否清楚；重複、不必要抽象、隱藏耦合、本次變更造成的衍生風險；是否違反專案規範、ADR、安全、效能、相容性；code smell 基線 |

每個 Spec Finding 必須引用對應的規格內容；每個 Standards Finding 必須引用規範出處或點名 smell。

---

## Finding 格式

```text
[阻擋／重要／建議] 檔案:行號或可定位範圍 — 問題
證據：可重現指令、輸出或明確程式路徑     【證據等級】
影響：違反的需求或工程風險
建議：最小修正方向
```

| 嚴重度 | 定義 |
|---|---|
| **阻擋** | 成果不可執行、存在安全或資料風險、核心需求錯誤或缺失 |
| **重要** | 明確錯誤、測試失真，或有證據的架構、維護、效能、相容性風險 |
| **建議** | 不影響交付的局部改善，**不得阻擋** |

嚴重度與**證據等級正交**：一個「建議」也必須是「已確認」。沒有新證據不得提高嚴重度。

### 不得

- 為了證明有審過而製造 Finding。**零 Finding 合法。**
- 把個人偏好包裝成規範。個人偏好不能單獨成立 Finding。
- 重複回報工具已可靠阻擋的格式問題。
- 擴大到無關舊程式，除非本次變更造成回歸。
- 沒有證據就要求重構或擴大範圍。

---

## Phase 4 — Finding 是主張，不是命令

**這是本 skill 最重要的一條。**

Reviewer 的 Finding 是**需要驗證的主張**，不是 Developer 必須直接照做的命令。

1. Developer **必須重現**每個 Finding。
2. 成立 → 修正並提供新證據。
3. 不成立 → 以測試、程式行為、規格或正式文件提出**可重現反證**，或提出能辨別爭議的**最小測試**。
4. Reviewer **只定向複驗自己提出的** Finding：修正成立回報 `closed`；反證成立回報 `withdrawn`；證據不足維持未關閉並指出缺口。
5. **Developer 說已修正不等於 Finding 已關閉**——要原 Reviewer 確認。
6. Reviewer 不得因為 Finding 是自己提的就拒絕撤回。

雙方沒有新證據時**停止來回爭辯**，把爭點與現有證據交回 Scheduler。**不得叫另一個模型來投票**——投票不是證據。

Scheduler 中立，不加入投票，只檢查雙方是否根據同一個 Snapshot 與可重現證據作出結論；證據不足的一方必須補證據或修正結論。

只有缺少**產品選擇、需求範圍、公開行為或不可逆風險**的決定時，才交給人裁決（GIL）。

---

## Phase 5 — 彙整

兩份報告分別放在 `## Spec` 與 `## Standards` 標題下，逐字或輕度整理。

**不得合併、不得跨軸重新排名。**

結尾一行：每軸的 Finding 總數，以及**各軸內部**最嚴重的那一個。**不要跨軸選一個總冠軍**——那正是分軸要防止的事。

存在建議但沒有未關閉的阻擋或重要 Finding → 可判定通過。

## 完成條件

- 固定點已釘住並驗證，兩軸拿到同一個 Snapshot。
- 兩軸首輪結論獨立產生。
- 每個 Finding 有位置、證據、影響、最小修正方向與證據等級。
- 阻擋與重要 Finding 都已經過修正或反證，並由原 Reviewer 複驗關閉或撤回。
- 兩軸分開呈現，沒有合併排名。
