# Changelog

## 0.4.0 - 2026-11-17

- Skills 改由 SE 2.1.0 bundle 同步（2.1.0 整合了 SE main 的 PR #6–#15，核心 skill 的完整做法放在各自的 `references/`）；九個核心 skill 與四個 `skills-local/` skill 原子合併到 `.agents/skills/`，並用單一 manifest 驗證缺檔、多檔與正規化 SHA-256。
- 舊名稱對照：`se-discovery` → `se-problem-definition`；`se-system-design` → `se-technical-design`；`se-two-axis-review` → `se-spec-review`＋`se-quality-review`；`se-debug` 保留名稱但改用 SE 2.1.0 canonical 內容。
- live 模型解析以 app-server `model/list` 為準，保留 requested/resolved model、來源與原因；cache 僅作診斷。
- `run`／`agent` 預設採單次執行後固定外部驗收，只有失敗才進入有限修復。
- 說明 Agora 執行模式與無 Agora 時的 `se.py` 獨立模式。
