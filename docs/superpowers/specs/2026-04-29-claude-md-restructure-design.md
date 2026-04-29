# Design: Claude Code 文件 / CLAUDE.md / `.claude/rules/` 全面重整

- 日期：2026-04-29
- 作者：PO-LIN LAI（louiskyee）+ Claude
- 狀態：Draft（待 user review，再進入 writing-plans）
- 觸發：每開新 session 都要重新理解專案，浪費 context；現有 CLAUDE.md 引用 stale auto memory；`docs/` 散落、命名不一致；無 CI、無 lint 紀律。

---

## 1. 目標

一次重整所有給「未來 Claude session + 新工程師」讀的文件，讓開新 session 時能在 < 5 分鐘建立完整 mental model，且不再有需要 user 主動補充的盲點。

**範圍內**
- `CLAUDE.md`、`.claude/rules/*.md`、`docs/architecture.md`、`docs/conventions.md`、`docs/runbooks/*.md`
- `docs/` 散落 Phase 文件搬到 `docs/phase-history/`、合併 `docs/ops/` 進 runbooks
- `~/.claude/projects/.../memory/MEMORY.md` 重建 + 移除 CLAUDE.md 對 stale auto memory 的引用
- 新增 `.lolday-secrets.env.example`、`.lolday-cf-svctoken.env.example`
- 新增 `docs/postmortems/2026-03-31-cilium-ssh-incident.md`（占位）
- branch / commit 慣例改為主流（GitHub Flow + Conventional Commits）

**範圍外（後續 phase）**
- 修 `~/.lolday-secrets.env` vs repo-root 路徑 inconsistency（4 個 script）
- 修 `config.py` 內 Harbor URL 三種寫法
- 刪 frontend 重複 config 檔（playwright/vite/vitest/tailwind 的 `.ts`/`.js`/`.d.ts`）
- 設 pre-commit / ruff / mypy / prettier / husky
- bump `charts/lolday/Chart.yaml` appVersion
- bump fastapi-users User 的 hashed_password 欄位移除
- helper image 版號自動化

---

## 2. 設計原則

1. **遵循 Claude Code 官方 memory 文件規範**（https://code.claude.com/docs/en/memory）
   - `CLAUDE.md` 縮為索引 + 全域硬規則 < 100 行
   - 子系統細節走 `.claude/rules/*.md` + `paths:` frontmatter（path-scoped，只在 Claude 讀對應檔時載入）
   - `@import` **同樣在啟動時整份載入**，省組織不省 context — 故僅用於不可避免的全域引用（如 `@README.md`）
   - Skills 為「按需載入」，本次重整不新增 skill
2. **對齊主流大型 OSS 工程實踐**
   - branch：`<type>/<short-kebab-desc>`（kubernetes、grafana、shadcn、Next.js 都是）
   - commit：Conventional Commits（VS Code、Prisma、Tailwind 都是）
   - PR title = 第一個 commit 的 conventional message
   - phase 編號**只**活在 `docs/superpowers/specs|plans/` 與 PR description
3. **You write vs Claude learns 職責清楚**
   - 事實 / 慣例 / 規則 → CLAUDE.md / `.claude/rules/` / `docs/`（user 寫，git 追）
   - 從失敗學到的東西 → auto memory `~/.claude/projects/.../memory/`（Claude 寫，machine-local）
4. **單一 source of truth**
   - 系統架構 → `docs/architecture.md`（不在 CLAUDE.md 重複）
   - 環境變數定義 → `backend/app/config.py` + `charts/lolday/values.yaml`（文件只列摘要 + 指向程式碼）
   - Phase 計畫 → `docs/superpowers/specs|plans/`（不在 architecture.md 重述細節）

---

## 3. 最終檔案結構

```
lolday/
├── CLAUDE.md                              ✏️  改寫為 < 100 行索引 + hard rules
├── README.md                              ✏️  修一個 broken link（phase1 v2）
├── .lolday-secrets.env.example            ✨  新增（committed）
├── .lolday-cf-svctoken.env.example        ✨  新增（committed）
├── .claude/
│   ├── settings.local.json                ⚪  不動
│   └── rules/                             ✨  新目錄
│       ├── backend.md                     ✨  paths: backend/**
│       ├── frontend.md                    ✨  paths: frontend/**
│       ├── charts-and-helm.md             ✨  paths: charts/**
│       ├── scripts-and-ops.md             ✨  paths: scripts/**
│       └── alembic-migrations.md          ✨  paths: backend/migrations/**
├── docs/
│   ├── architecture.md                    ✨  系統架構單檔（350-500 行）
│   ├── conventions.md                     ✨  Phase / branch / commit / migration 慣例
│   ├── runbooks/                          ✨  新目錄
│   │   ├── deploy.md                      ✨  合併 phase3-deploy-runbook + ops/host-prep
│   │   └── troubleshooting.md             ✨  diag-* / recover-* 對照表
│   ├── phase-history/                     ✨  新目錄
│   │   └── (從 docs/ 根 git mv 進來的散檔)
│   ├── postmortems/
│   │   ├── 2026-03-31-cilium-ssh-incident.md  ✨  占位（最少三句）
│   │   └── 2026-04-21-prometheus-wal.md   ⚪  不動
│   └── superpowers/{specs,plans}/         ⚪  不動
└── ~/.claude/projects/<…>/memory/
    └── MEMORY.md                          ✏️  重建 index
```

---

## 4. `CLAUDE.md` 內容大綱（< 100 行）

```markdown
# Lolday — internal ML platform for ISLab malware detector management

@README.md

## How to navigate this codebase
- 系統架構 / 模組責任 / 外部服務 / env vars / 技術債 → docs/architecture.md
- 部署 / 維運 → docs/runbooks/deploy.md, troubleshooting.md
- 命名 / 分支 / commit 慣例 → docs/conventions.md
- 在 backend/ / frontend/ / charts/ / scripts/ / migrations/ 工作 →
  自動載入對應 .claude/rules/<area>.md（path-scoped）
- 過去 Phase 紀錄與 e2e checklist → docs/phase-history/
- 事故 postmortem → docs/postmortems/

## Hard rules（每個 session 都必須記得）

### SSH safety on server30
（保留現有條目，不改）

### Sudo policy
（保留現有條目，不改）

### Avoid China-origin software
（保留現有條目，不改）

### Prefer open-source over custom code
（保留現有條目，不改）

## Quickstart commands
bash scripts/install-tools.sh
sudo bash scripts/setup-k3s.sh
bash scripts/deploy.sh
cd backend && uv run pytest
cd frontend && pnpm test && pnpm playwright test
helm lint charts/lolday

## Project layout（一行一個）
- backend/                  FastAPI + uv          → .claude/rules/backend.md
- frontend/                 Vite + React + TS     → .claude/rules/frontend.md
- charts/lolday/            Helm umbrella chart   → .claude/rules/charts-and-helm.md
- scripts/                  install/deploy/diag   → .claude/rules/scripts-and-ops.md
- backend/migrations/       Alembic              → .claude/rules/alembic-migrations.md
- docs/superpowers/         Phase specs / plans
- docs/                     architecture / conventions / runbooks / phase-history
```

**從現有 CLAUDE.md 移除的內容**
- 「For accumulated project facts ... see auto memory at ...」整段（line 87–88，stale 引用）
- 詳細 Build / test 指令（保留 6 條 quickstart，其餘進 architecture.md §6）
- Project layout 詳細註腳（搬入 .claude/rules/<area>.md）

---

## 5. `.claude/rules/*.md` 內容大綱（每檔 60–150 行）

每檔頂部 YAML frontmatter `paths:` 限制載入範圍。

### 5.1 `backend.md`（paths: `backend/**/*.py`、`backend/pyproject.toml`、`backend/alembic.ini`）

主要章節：
- **App 結構**：main.py 入口、routers / services / models / schemas / auth / deps.py / users.py
- **啟動 fail-fast 行為**（重要 onboarding 陷阱）：
  - `_assert_schema_at_head()` — alembic 沒到 head boot 失敗
  - `validate_sso_config` — production 缺 CF_ACCESS env 或 `AUTH_DEV_MODE=true` 拒絕 boot
- **Auth 設計**：fastapi-users 是薄殼（password flow 已剝），全靠 `cf_access_user`；`Role.SERVICE_TOKEN: -1` 是故意負值
- **Async DB**：SQLAlchemy 2.0 async + asyncpg；test 用 aiosqlite
- **Discord notify pattern**：`asyncio.create_task(notify_*(...))` fire-and-forget；錯誤吞 + Prom counter；service-token job 跳過通知
- **reconciler.py（57KB tech debt）**：修改前先讀 phase11b/12 spec，不要拆檔除非有對應 phase
- **maldet 是外部 PyPI 套件**：lolday consume 它，不要在 lolday repo 實作 detector 邏輯
- **Tests**：cd backend && uv run pytest；MLflow 預設 mock，反向標 `@pytest.mark.no_mock_mlflow`
- **依賴管理**：用 uv add，不要直接編輯 pyproject.toml
- **不要新增**：自寫 OIDC/JWT、自寫 retry — 先看現有套件

### 5.2 `frontend.md`（paths: `frontend/**/*.{ts,tsx,js,jsx,css,json}`）

- Stack：Vite 5 + React 18 + TS 5.5 + Tailwind 3.4 + shadcn/ui + react-router 7 file-based + TanStack Query v5 + react-hook-form + zod + i18next
- File-based routing：`_authed.*` = 需登入；`$param` = path param；`_index` = index page
- API client：所有 call 走 `src/api/client.ts`（openapi-fetch）；型別由 `pnpm gen-api-types` 生 `schema.gen.ts`；不手寫 fetch
- State：server state → TanStack Query；URL → react-router；form → react-hook-form
- 元件庫：shadcn/ui first；不引入 Ant/Naive/Element/Arco/TDesign
- nginx CSP 嚴格 `script-src 'self'`：**不要寫 inline script** 會被擋
- Tests：vitest（unit）+ playwright（E2E）；`pnpm typecheck && pnpm lint` 在 commit 前
- **重複 config 檔不要動**：playwright/vite/vitest/tailwind 各有 `.ts`/`.js`/`.d.ts` 三份是 tech debt（後續 phase 清）— 修改時改 `.ts` 即可

### 5.3 `charts-and-helm.md`（paths: `charts/**/*.{yaml,yml,tpl,json}`）

- Umbrella chart 結構 + sub-charts（harbor / kps / loki / alloy / trivy-operator / volcano）
- Helper images：build-helper（含 maldet_validator.py）/ job-helper（Python module + tests）/ mlflow-server / pytorch-cu12-base
- 重要 templates：backend / frontend / postgresql / redis / mlflow / registry / cloudflared / alembic-upgrade-hook / netpol-* / volcano-queue
- monitoring/ subfolder 完整列表：alertmanager-config-discord / alertmanager-rules / deadmans-switch（CronJob，獨立 Discord webhook） / grafana-admin-secret / grafana-dashboards / postgres-exporter (init+main) / servicemonitor-{backend,dcgm,postgres,traefik,trivy,volcano}
- Workflow：helm lint → helm template diff → helm dependency update（不要 commit `*.tgz`）
- 修改 NetworkPolicy 前看 SSH safety hard rule
- `appVersion` 目前是 `phase12` 但實際在 phase 13 — 後續 phase 修

### 5.4 `scripts-and-ops.md`（paths: `scripts/**`、`*.sh`）

- Script 分類 + 一行說明（install/deploy/diag/recover/migrate/phase-precheck/one-shot Python）
- Sudo 紀律：default 無 sudo；必要 sudo 寫獨立 `# requires sudo` 行
- SSH 紀律：iptables/ufw/CNI/sysctl 改動先 dry-run 印 plan，改後提示 user 開新 ssh session 驗
- Secrets 路徑 fallback pattern（recover-harbor.sh 已是範例）：
  ```
  SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
  [ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
  ```
  目前 4 個 script 還硬寫 `~/.lolday-secrets.env`（diag-backend-401.sh 已切到 repo-root；其他待修）
- 寫新 script：`#!/usr/bin/env bash` + `set -euo pipefail` + `${VAR:?required}`

### 5.5 `alembic-migrations.md`（paths: `backend/migrations/**`）

- 命名：`<rev>_phase<N>(_<minor>)_<short_desc>.py`（既有 phase 對應表）
- Workflow：alembic revision --autogenerate → 手動審 enum/index/server_default → upgrade head 在 dev DB 跑過
- 不在 prod 跑 downgrade；rollback 走新 forward migration 反向
- enum 改動踩雷紀錄（phase 12.1 / 12.3 role_enum lowercase / values_callable）
- NOT NULL column 必附 server_default 或分 2 step migration

---

## 6. `docs/architecture.md` 內容大綱（350–500 行）

10 章：

1. **目的與定位**（30 行）— 不是 ML framework，是 maldet + K8s + MLflow + Harbor 的 glue；server30 單節點 K3s
2. **System diagram**（mermaid C4 Container view）— browser → cloudflared → ingress → frontend/backend → DB/Redis/MLflow/Harbor/K8s API（Volcano）→ vcjob 跑 detector + 寫 MLflow + push image；monitoring 共用 Prom/Loki/Grafana 路徑
3. **元件責任表**（80 行 markdown table）：
   - 平台：backend / frontend / reconciler / Volcano queue / Harbor / MLflow / PostgreSQL / Redis / Cloudflared / kps / Loki+Alloy / Trivy operator / GPU operator
   - Helper：build-helper（含 maldet_validator.py）/ job-helper（Python module）/ mlflow-server / pytorch-cu12-base
   - 監控：alertmanager rules / deadmans-switch CronJob / postgres-exporter / ServiceMonitor × 6
   - 通知：Discord events webhook（`services/discord.py` + `notify.py`）+ deadmans-switch 獨立 webhook
4. **資料流**（60 行）：4.1 建 detector / 4.2 跑 job / 4.3 SSO / 4.4 監控+log / **4.5 通知**（fire-and-forget + Prom counter）
5. **Env vars 與設定來源**（40 行）：
   - Runtime（K8s 內）：列 `backend/app/config.py` 主要 group（DB/Redis/Harbor/Build/Job/MLflow/Discord/CF Access/AUTH_DEV_*/ENVIRONMENT/LOLDAY_UI_BASE_URL）
   - Operator-local env files（repo root，gitignored）：`.lolday-secrets.env`、`.lolday-cf-svctoken.env`、`.lolday-cloudflare-access-backups/`
   - 已知 inconsistency：Harbor URL 三種寫法（待後續 phase 修）
6. **Build / Test / Release**（40 行）— 沒有 GitHub Actions（tech debt）；backend/frontend Dockerfile；helper images 手動 build push；deploy.sh + alembic-upgrade-hook
7. **外部依賴**（30 行）— Cloudflare Access/Tunnel、Discord webhook × 2、GitHub、maldet PyPI、NVIDIA GPU operator
8. **Phase 進程一覽**（30 行）— Phase 1 → 13b 表格 + 連結到 specs/plans
9. **已知技術債**（40 行）— reconciler.py 57KB；無 CI/CD；frontend 三套 config 檔重複；tsconfig.node.tsbuildinfo 被 commit；無 pre-commit/ruff/mypy/prettier；Chart.yaml appVersion 落後；README link broken；fastapi-users hashed_password 殘留；helper image 版號 hardcode；secrets 路徑 inconsistency；Harbor URL 三種寫法
10. **常見陷阱**（30 行）— SSH on server30；alembic autogenerate 對 enum 不可信；Cilium 斷 SSH；Harbor 重裝 robot creds reset；maldet 版號要看外部 repo；MLflow 測試 mock；schema head check fail-fast；AUTH_DEV_MODE production 拒絕；CSP `'self'` only 擋 inline；Volcano 排程慢觸發 alert

---

## 7. `docs/conventions.md` 內容大綱（~120 行）

```
1. 分支命名（主流）
   <type>/<short-kebab-desc>
   - feat/job-detail-tabs
   - fix/role-enum-lowercase
   - chore/bump-deps
   - docs/restructure-claude-md
   - refactor/reconciler-split
   types: feat | fix | chore | docs | refactor | test | perf | build | ci

2. Commit message — Conventional Commits
   <type>(<scope>): <subject>
   - feat(jobs): add detail summary tab
   - fix(auth): align role_enum to values_callable
   scope = 模組（jobs/auth/reconciler/harbor/charts/frontend/backend），不是 phase
   subject = 祈使句、小寫開頭、無句號

3. PR title = 第一個 commit 的 conventional message
   PR description 必附：
     Spec: docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md
     Plan: docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md

4. Phase 編號只活在規劃文件
   - docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md
   - docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md
   - 熱修不掛 phase：fix/<short-desc> + docs/postmortems/YYYY-MM-DD-<topic>.md

5. 切線：2026-04-29 起改主流；舊 commit 維持原樣不改寫

6. Migration 命名 → .claude/rules/alembic-migrations.md
7. 程式碼命名：snake_case（Py）/ kebab-case（K8s 資源）/ camelCase（TS）
8. 三層測試：
   - backend/tests/        pytest（單元 + service + reconciler + migration）
   - frontend/tests/unit   vitest
   - frontend/tests/e2e    playwright
   - tests/phase7/         shell-based（alertmanager / volcano queue / servicemonitor）
9. 寫新 service / router / 元件前先看 .claude/rules/<area>.md
```

---

## 8. `docs/runbooks/deploy.md` 與 `troubleshooting.md`

### 8.1 `deploy.md`（合併 phase3-deploy-runbook + ops/host-prep）

章節：
1. Pre-requisites — host 設定、NVIDIA driver、temp sudo、**建立 `.lolday-secrets.env` 與 `.lolday-cf-svctoken.env`（從 `.example` copy）**
2. K3s 安裝 — `sudo bash scripts/setup-k3s.sh`
3. GPU operator 安裝 — README 既有命令
4. Cloudflare Tunnel & Access App 設定 — 取 token、設 policy
5. `bash scripts/deploy.sh` — 內部會自動 helm dep update + helm upgrade --install
6. Alembic upgrade hook 自動跑（pre-upgrade）
7. Verify — 列檢查項
8. Rollback — helm rollback / alembic 反向 forward migration

### 8.2 `troubleshooting.md`

症狀 → 對應 script / 手動步驟 對照表：
- backend 401 / 無法登入 → diag-backend-401.sh + 檢查 cf_access env
- backend 啟動 schema mismatch → 看 alembic-upgrade-hook log
- harbor 重裝後 image pull 失敗 → recover-harbor.sh + fix-lolday-project-public.sh
- K3s 拉 image 401 → patch-k3s-registries.sh
- PV 資料消失 → diag-pv-data.sh + find-lost-data.sh
- 磁碟爆滿 → disk-diag.sh
- Volcano 排程斷 → 看 `lolday_volcano_pending_stale` Gauge alert
- Discord 通知沒進 → Prom `BACKEND_ERRORS{stage="discord_notify"}` + 看 webhook URL secret

---

## 9. 既有 docs/ 搬移計畫（git mv）

```bash
git mv docs/2026-04-21-phase8-e2e-ux-findings.md  docs/phase-history/phase8-e2e-ux-findings.md
git mv docs/phase11b-e2e-checklist.md             docs/phase-history/
git mv docs/phase11d-retirement-findings.md       docs/phase-history/
git mv docs/phase11d-v0-snapshot.json             docs/phase-history/
git mv docs/phase12.1-role-enum-bug.md            docs/phase-history/
git mv docs/phase3-e2e-checklist.md               docs/phase-history/
git mv docs/phase4-e2e-checklist.md               docs/phase-history/
git mv docs/phase6-e2e-checklist.md               docs/phase-history/
git mv docs/phase7.5-e2e-checklist.md             docs/phase-history/
git mv docs/phase3-deploy-runbook.md              docs/phase-history/
git mv docs/ops/host-prep.md                      docs/phase-history/
rmdir docs/ops
```

`docs/runbooks/deploy.md` 內容由 `phase-history/phase3-deploy-runbook.md` + `phase-history/host-prep.md` 重整而來；原檔保留歷史可追溯，只是移位置。

---

## 10. Auto memory cleanup

**現況**
- `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/MEMORY.md` 是空檔
- `CLAUDE.md` line 11 與 line 87–88 引用了**不存在**的 `project_cilium_ssh_incident.md` 與 6 個其他主題

**計畫**
1. CLAUDE.md line 11 把「See the Cilium incident post-mortem in auto memory ...」改寫成「2026-03-31 因 Cilium 斷過 SSH — 詳 docs/postmortems/2026-03-31-cilium-ssh-incident.md」
2. CLAUDE.md line 87–88 整段移除（散落事實改放 `docs/architecture.md` §8 / §10）
3. 新建 `MEMORY.md` 起始內容：
   ```markdown
   # Auto memory — Lolday

   還沒有任何 auto memory 紀錄。Claude 在後續 session 從 build / debug / 偏好
   學到的東西會自動寫入此目錄；topic 檔案會被 index 在這裡。

   - （目前無 entries）
   ```
4. 不主動把過去 phase 的事實塞入 auto memory — 那些屬 user 寫的範疇

---

## 11. 新增 env example 檔

### 11.1 `.lolday-secrets.env.example`（committed）

從 grep 取得目前實際使用的 key（值留空 + 註解每個來源）：
```bash
# Lolday operator-local secrets — copy to .lolday-secrets.env (gitignored, chmod 600)
# Loaded by: scripts/deploy.sh, recover-harbor.sh, harbor-inventory.sh,
#            fix-lolday-project-public.sh, diag-backend-401.sh, phase6-pre-deploy-check.sh

# Phase 6 monitoring secrets
GRAFANA_ADMIN_PASSWORD=
PG_EXPORTER_PASSWORD=

# Cloudflare tunnel (Phase 6 + Phase 10)
CF_ENABLED=true
CF_TUNNEL_TOKEN=

# Discord events webhook (Phase 7.4) — also used by alertmanager
DISCORD_WEBHOOK_URL_EVENTS=

# Harbor (Phase 3 / Phase 9.6 recovery)
HARBOR_ADMIN_PASSWORD=

# Backend Fernet key for encrypted columns (Phase 3)
FERNET_KEY=

# (其餘 key 待 user 確認後補完)
```

### 11.2 `.lolday-cf-svctoken.env.example`（committed）

```bash
# Cloudflare Access service token (machine principal) — copy to
# .lolday-cf-svctoken.env (gitignored, chmod 600). Used to test svctoken
# auth via /users/me; see docs/phase-history/phase12.1-role-enum-bug.md.

CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

---

## 12. Cilium SSH incident postmortem 占位

`docs/postmortems/2026-03-31-cilium-ssh-incident.md`（最少三句）：

```markdown
# 2026-03-31 — Cilium 安裝斷 SSH

**事件**：嘗試在 server30 安裝 Cilium 取代 K3s 內建 flannel。Cilium agent 啟動後
丟掉所有 host iptables rules，包括維護 SSH 9453 port 的規則，導致無法從
任何遠端進入 server30。實體救援費了相當時間。

**為何今天 CLAUDE.md 有 SSH safety hard rule**：因為這次事件發生後沒有
fallback（lab 沒有 IPMI / KVM），任何 CNI / iptables / sysctl 動作都必須先
驗證 SSH 不會中斷。

**應對 / 後續**：未恢復 Cilium；現役 K3s 仍用 flannel。任何網路層改動先做
dry-run，改後立刻提示 user 開新 ssh session 驗證連線。
```

---

## 13. 實作順序（提供給 writing-plans）

1. 新建 `.claude/rules/{backend,frontend,charts-and-helm,scripts-and-ops,alembic-migrations}.md`
2. 新建 `docs/architecture.md`、`docs/conventions.md`、`docs/runbooks/{deploy,troubleshooting}.md`
3. 新建 `docs/postmortems/2026-03-31-cilium-ssh-incident.md`
4. 新建 `.lolday-secrets.env.example`、`.lolday-cf-svctoken.env.example`
5. `git mv` 搬 docs/ 散檔到 `docs/phase-history/`；`rmdir docs/ops`
6. 改寫 `CLAUDE.md`（縮為索引版本、移除 stale auto memory 引用）
7. 修 `README.md` broken link（phase1 v2）
8. 重建 `~/.claude/projects/<…>/memory/MEMORY.md`
9. Verify：
   - `/memory` 看載入清單包含新 rules
   - `helm lint charts/lolday`
   - `grep -r "project_cilium_ssh_incident" .` 無命中
   - `grep -r "auto memory at" CLAUDE.md` 無命中
10. 多個 atomic commits（rename-only 一個、新增 rules 一個、新增 docs 一個、改 CLAUDE.md 一個、env example 一個、auto memory 一個）

---

## 14. 開放議題 / 後續 phase

下列項目**不在這次範圍**，建議列為新的 phase（例如 Phase 14 — 工程紀律）：

1. 修 4 個 script 的 secrets 路徑 inconsistency（fix-lolday-project-public / harbor-inventory / recover-harbor / phase6-pre-deploy-check 都該走 fallback pattern）
2. 修 `backend/app/config.py` 的 Harbor URL 三種寫法
3. 刪 frontend 重複 config 檔（`playwright/vite/vitest/tailwind.config.{js,d.ts}`，保留 `.ts`）
4. 移除 `frontend/tsconfig.node.tsbuildinfo` + 加入 .gitignore
5. 設定 pre-commit / husky / ruff / mypy / prettier / .editorconfig
6. bump `charts/lolday/Chart.yaml` `appVersion`
7. 移除 fastapi-users User 的 `hashed_password` 欄位（migration）
8. helper image 版號自動化（avoid hardcode `:v3` `:v4`）
9. 補完 `.lolday-secrets.env.example` 剩餘 key（user 確認）
10. 重補完 build-helper/uv.lock 的 commit / gitignore 政策

---

## 15. 驗收標準

- 開新 Claude session 在 `lolday/` 跑：載入 CLAUDE.md（< 100 行）+ 對應 rules（path-scoped 不會在啟動時全載入）
- 在 `backend/app/main.py` 開檔即自動載入 `.claude/rules/backend.md`
- `docs/architecture.md` 可讓人 5 分鐘內理解整個系統
- `grep -r "project_cilium_ssh_incident" .` 無殘留
- `git status` 無 staged untracked secret
- `helm lint charts/lolday` 通過（不應該被影響，但確認 sanity）
