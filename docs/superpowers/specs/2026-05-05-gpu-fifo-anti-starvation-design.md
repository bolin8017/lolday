# GPU FIFO + Anti-Starvation — Design Specification (Phase 6)

> **Phase numbering follows on from `2026-05-05-gpu-scheduling-and-oom-defense-design.md`** (Phases 0–5 already shipped in v0.17.0). This spec adds Phase 6.
>
> **Revision history (this spec):** v1 (2026-05-05 morning) tried Volcano `sla` plugin; **live smoke proved it does not prevent leapfrog** (Volcano upstream bug #5044). v1.5 tried `overcommit-factor: 1.0`; live smoke also failed for the same root cause. v2 (this version, 2026-05-05 afternoon) pivots to an **application-layer FIFO scheduler in lolday backend**, since Volcano 1.14 has no mainstream chart-only path to strict FIFO without preemption.

## 1. Overview

Phase 2 上線了 Volcano per-user queue + DRF/proportion plugin。2026-05-05 的活體測試證明這還不夠：**大 GPU job 在持續的小 GPU job 流量下會被永久餓死**（spec §4 四組 live test）。

過程中我們循序試了三種 Volcano 內建解：

1. 拿掉 K8s `ResourceQuota` 的 GPU 軸 → 修掉 admission race（layer 1 leapfrog） → ✅
2. 加 Volcano `sla` plugin → 期望 `JobPipelinedFn` 為 overdue PG reserve 資源 → ❌（live smoke 證明 reservation 沒實作；Volcano upstream issue #5044 是同一個 bug，至今 OPEN）
3. 加 `overcommit-factor: 1.0` → 期望 enqueue 階段擋下小 job 不讓它 Inqueue → ❌（live smoke 證明 enqueue iterator 仍會 leapfrog）

**結論**：Volcano 1.14 沒有純 chart 配置可以表達「strict FIFO without preemption」。 issue #5044 / #4690 / #3095 三個 OPEN issue 都是同樣 design gap。可走的方向只剩三條：preempt（殺 training，被 §3.2 deferred）、fork Volcano、或**在 lolday backend 自己做 application-layer FIFO scheduler**。

Phase 6 v2 選最後一條。Backend 攔住 user-submit 的 job，以整數 priority + creation time sort，每 30 秒檢查 cluster 容量，**HEAD 不 fit 嚴格 stop**（不繼續看後面 job）；只有 admin 可以 bump priority 把 job 排到 next-to-submit。Mainstream 對齊：[AWS Batch Job Queue priority](https://docs.aws.amazon.com/batch/latest/userguide/job_queue_parameters.html) 預設行為、Slurm without backfill 模式。

> **這份 spec 主要回答**：怎麼在 Volcano 1.14 沒辦法表達 strict FIFO 的限制下，仍然 root-cause 解掉 user 的「大 GPU job 被小 job 永遠 leapfrog」痛點，又不犧牲 GPU=2 訓練能力（DDP 仍可用），又不殺 running training。

## 2. Authorization

User 在 brainstorming 階段（2026-05-05）明確授權破壞性變更，並重申 root-cause priority + 主流實踐 兩個原則：

- **`lolday-jobs-quota` 拿掉 GPU 軸**（已於 commit `4c5c729` 完成）。Volcano queue capability 是唯一的 GPU gatekeeper。
- **撤回 chart sla plugin override**（commit `b836958` 將被反向 commit 撤回）。sla plugin 對 multi-GPU leapfrog 沒實際效果，保留它在 chart 中是 dead code。
- **Backend 新增 application-layer FIFO scheduler**：Job 加 status `queued_backend`、priority field；新增 reconciler thread；POST /jobs 改成不直接 submit 到 Volcano；新增 PATCH /jobs/{id} (admin-only) 改 priority。
- **Alembic migration**：新增 `priority INTEGER NOT NULL DEFAULT 0` column；`JobStatus` enum 新增 `queued_backend`。
- **Frontend**：admin 看得到 / 改得到 priority；一般 user 看不到。
- **不向後相容**。改完之後，user 經由 lolday API 提交的 job 不會立刻進 Volcano，會先在 backend queue。舊版假設「POST /jobs 立刻 submit vcjob」的 client / monitoring / e2e test 可能需要更新。
- **不影響 maldet contract**：detector image / vcjob spec / mlflow integration 都不變。Backend FIFO 是 user → backend 之間的攔截層；backend → vcjob → Volcano 之間的協議保持。

`scripts/setup-k3s.sh` 不變。host 層 kubelet args（Phase 0）不影響本 phase。

## 3. Scope

### 3.1 In scope (Phase 6)

1. **6a — `lolday-jobs-quota` 移除 GPU 軸**：✅ 已完成 at `4c5c729`。修掉 admission race。
2. **6b — Revert sla plugin from chart**：撤回 `b836958` 對 `volcano.custom.scheduler_config_override` 的添加。chart 回到 Volcano sub-chart 預設 scheduler config。
3. **6c — Job model + alembic migration**：新增 `priority` column（int, default 0, indexed）；新增 `JobStatus.queued_backend` enum value。
4. **6d — Backend FIFO reconciler**：新模組 `backend/app/reconciler/fifo_scheduler.py`，每 30 秒從 DB 取 `queued_backend` jobs，依 `(priority DESC, created_at ASC)` sort，HEAD `gpu_count <= cluster.free_gpu` 時 submit vcjob 並更新狀態，否則 break（嚴格 FIFO，不 leapfrog）。
5. **6e — Backend API 修改**：
   - `POST /jobs`：改成寫 DB status=`queued_backend`，**不直接 submit vcjob**。Reconciler 會處理。
   - `POST /jobs` 接受 optional `priority`；一般 user 送 `priority != 0` 直接 reject 403。
   - `PATCH /jobs/{id}` (new endpoint)：接受 `priority` field；只 admin 可呼叫；只能改 status=`queued_backend` 的 job。
6. **6f — Frontend**：
   - admin 在 job list / detail 看得到 priority column / field 並可編輯
   - 一般 user 看不到 priority
   - submit form: admin 多一個 priority input（預設 0）
7. **6g — Smoke test 重寫**：`tests/2026-05-05-phase6-fifo-smoke.sh` 改為透過 lolday API 測試 backend FIFO（不再 patch scheduler config），斷言：(i) 一般 user submit gpu=2 後 admin bump priority 能讓它變 next-to-submit；(ii) HEAD 不 fit 時後面的 job 不 leapfrog。
8. **6h — Documentation**：
   - `docs/architecture.md` §10 更新（Phase 1 / Phase 2 / 加 Phase 6 backend FIFO）
   - `docs/runbooks/admin-priority.md`（新）— admin 如何 bump priority、何時用、副作用
   - `.claude/rules/backend.md` 提到新 reconciler thread
   - `CLAUDE.md` 在「How to navigate」加一條 backend FIFO 指引

### 3.2 Out of scope

- **Slurm-style backfill** — Phase 7+ 升級路徑。要等真實看到 「HEAD 卡住、GPU 閒置」造成困擾才做。基底是 Phase 5 的 `active_deadline_seconds`（雖是上界不是 ETA，但夠 conservative backfill）。
- **Aging（自動 priority 調升）** — Phase 7 視 production observation 決定。手動 admin bump 是 MVP。
- **Per-user delegated priority permission / quota** — Phase 7 才需要。MVP 是 admin-only。
- **Volcano upgrade / 等 #5044 上游修復** — 持續追蹤；上游修了之後可考慮把 backend FIFO 簡化或移除。Phase 6 不依賴上游時間表。
- **PriorityClass + preempt action** — 沿用 Phase 0–5 spec §3.2 結論。會殺 training、丟 progress。
- **替換 Volcano**（YuniKorn / Kueue）— 沿用 Phase 0–5 spec §3.2 結論。
- **Per-job priority quota** — 不在 6e API 設計範圍。

### 3.3 Authorization for breaking changes (recap)

§ 2 已列；此處再次強調沒有對 detector / maldet `>=1.1,<2` API 做任何要求變動。User 提交 job 的 API 端點 / payload schema 大致保持，**只是新增 optional `priority` 欄位**。

## 4. Empirical evidence (live cluster, 2026-05-05)

四組 + 兩組失敗案例的 chart-only 實測。所有測試使用 `nvidia/cuda:12.6.3-base-ubuntu22.04`，bypass 後端認證直接送 vcjob 觀察 scheduler 行為。

### 4.1 Test A — sanity: 同一 queue 三個 GPU=1 job

| t (sec) | event                                                         |
| ------- | ------------------------------------------------------------- |
| 0       | submit a-j1 / a-j2 / a-j3 (all gpu=1, sleep 45)               |
| 2       | a-j1 + a-j2 Running on GPU0 + GPU1                            |
| 2–47    | a-j3 stuck `FailedCreate` 14 次 — `lolday-jobs-quota` GPU 2/2 |
| 48      | a-j3 finally schedules (after a-j1 freed quota)               |

→ 確認：**ResourceQuota 在 admission 階段擋了 a-j3**，不是 Volcano 在排隊。

### 4.2 Test B — leapfrog @ admission level

| t (sec) | event                                                     |
| ------- | --------------------------------------------------------- |
| 0       | b-j1, b-j2 Running (quota 2/2)                            |
| 5       | submit b-BIG (gpu=2) — `FailedCreate`（會超過 quota 2）   |
| 9       | submit b-SMALL (gpu=1) — `FailedCreate`（會超過 quota 2） |
| 27      | b-j1, b-j2 finish 同時; quota 0/2                         |
| 31      | **b-SMALL** schedules first                               |
| 51      | b-SMALL finishes                                          |
| 88      | b-BIG finally runs (晚 b-SMALL 一輪)                      |

→ **Layer 1 root cause 確認：admission race**。修法：拿掉 ResourceQuota.GPU 軸（6a 已完成）。

### 4.3 Test C — quota 拿掉、jobs 同時 free → 沒有 leapfrog

| t (sec) | event                                                                |
| ------- | -------------------------------------------------------------------- |
| 0       | c-j1, c-j2 (gpu=1, sleep 25) Running                                 |
| 5       | c-BIG (gpu=2, sleep 20) submitted — Pending                          |
| 10      | c-SMALL (gpu=1, sleep 20) submitted — Pending                        |
| 27      | c-j1 + c-j2 同時 finish — 2 GPUs free                                |
| 29      | **c-BIG schedules first** ✅（priority plugin 認 creationTimestamp） |

→ 拿掉 quota 之後，當 head-of-line 能 fit 就照 FIFO 跑。

### 4.4 Test D — quota 拿掉，但 jobs **錯時** free → leapfrog 仍然發生

| t (sec) | event                                          |
| ------- | ---------------------------------------------- |
| 0       | d-j1 (sleep 30) + d-j2 (sleep 70) Running      |
| 5       | d-BIG (gpu=2, sleep 15) submitted              |
| 10      | d-SMALL (gpu=1, sleep 15) submitted            |
| 33      | d-j1 finishes — 1 GPU free, d-j2 still on GPU1 |
| 35      | **d-SMALL schedules** ❌                       |
| 51      | d-SMALL finishes                               |
| 72      | d-j2 finishes — 2 GPUs free                    |
| 74      | d-BIG finally schedules                        |

→ **Layer 2 root cause 確認**：Volcano `allocate` action 跳過 head-of-line 不 fit 的 PG。

### 4.5 Test E — 加 sla plugin 也失敗

加 sla plugin 到 tier 1，`sla-waiting-time: 20s`（test 用值，runtime default 10m）。重跑 Test D 場景：

- d-BIG age=25s > 20s sla threshold ✓
- d-SMALL age=21s > 20s sla threshold ✓
- 兩個 PG 都 sla-overdue
- sla `JobOrderFn` 排序：d-BIG deadline `07:20:26` < d-SMALL `07:20:30` → d-BIG 應該先
- 觀察結果：**d-SMALL 仍然 leapfrog d-BIG**（07:20:36 vs d-BIG 07:21:15）

讀 [Volcano 1.14 sla.go 原始碼](https://github.com/volcano-sh/volcano/blob/release-1.14/pkg/scheduler/plugins/sla/sla.go) + [allocate.go](https://github.com/volcano-sh/volcano/blob/release-1.14/pkg/scheduler/actions/allocate/allocate.go)：

> sla.go 的 `permitableFn` (同時用作 `JobEnqueueableFn` 和 `JobPipelinedFn`)：年齡 > waiting-time → return `util.Permit`
>
> allocate.go 內部：當 `JobPipelined(job)` 為 true 但資源不足時，`stmt != nil && ssn.JobReady(job)` 為 false（`JobReady` 需要 minAvailable 滿足），stmt 既不 Commit 也不 Discard，**執行流直接繼續到下一個 job**——不會保留 GPU、不會阻止 leapfrog。

[Volcano upstream issue #5044](https://github.com/volcano-sh/volcano/issues/5044)（2025 OPEN）明確這是 bug：

> _"Pipelined job statements are never committed or discarded in allocate action… pipelined jobs may lose their pipelined slots to newly-arriving jobs, defeating the purpose of pipelining."_

Issue #4690 / #3095 是同一個情境的 user reports，三個 issue 都 OPEN，沒有 fix 合入。

→ **sla plugin 對 multi-GPU leapfrog 沒效**。是 Volcano 1.14 的 design gap。

### 4.6 Test F — 加 overcommit-factor=1.0 也失敗

期望：`overcommit-factor: 1.0` 讓 enqueue action 嚴格依 queue capability 擋下小 job。

加 plugin args 後重跑 Test D：

- d-BIG submit at age=0 → enqueue check: 2 (d-j1+d-j2 running) + 2 (d-BIG req) = 4 > cap 2\*1.0 → REJECT, stays Pending
- d-SMALL submit → enqueue check: 2 + 1 = 3 > 2 → REJECT, stays Pending
- d-j1 finish (queue.allocated drops to 1)
- enqueue iterates Pending PGs:
  - d-BIG: 1 + 2 = 3 > 2 → REJECT, stays Pending
  - d-SMALL: 1 + 1 = 2 ≤ 2 → ACCEPT, Inqueue
- allocate 看到 d-SMALL（d-BIG 還 Pending），schedule d-SMALL
- → **d-SMALL 又 leapfrog d-BIG**

問題：Volcano enqueue iterator 不會「stop at first reject」。這跟 sla 是同層 design gap 的不同表現。

→ **chart-only 配置在 Volcano 1.14 跑遍所有可行 mainstream 路徑都失敗**。

### 4.7 結論

| Layer         | Root cause                                                                                                                   | 修法                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 1 — admission | `lolday-jobs-quota.requests.nvidia.com/gpu` 與 vcjob controller 的 retry race                                                | 6a：拿掉 quota GPU 軸 ✅                                                              |
| 2 — scheduler | `allocate` action 跳過 head-of-line 不 fit；`enqueue` action 不 stop at reject；sla plugin 的 reservation 是 broken（#5044） | 6c–6h：在 lolday backend 做 application-layer FIFO，攔住 user submit 不直接送 Volcano |

## 5. Architecture decisions

### 5.1 為什麼 backend-layer FIFO，而不是更多 Volcano 設定

跑過所有 chart-only 主流方案都失敗（§4.5–4.6）。剩三個方向：

- **Preempt** — 殺 training、丟 checkpoint，已被 spec §3.2 deferred 到 maldet 支援 resume 之後。
- **Fork Volcano / custom plugin** — 維護成本高、違反 mainstream practice、和 Volcano 升級節奏脫鉤。
- **等上游修 #5044** — Volcano timeline 不在我們控制；blocked。

對比 backend-layer FIFO：

- 在 application 層做 sub-scheduler 是 mainstream pattern（AWS Batch、Slurm-on-K8s wrappers、HPC frontend）
- 不 fork Volcano，不殺 running，不依賴 upstream timeline
- root-cause（在我們可控的範圍內）= 「Volcano 沒辦法表達我們要的 policy，所以我們在它上層加一個 sub-scheduler 表達」
- 上游 #5044 修了之後可以考慮簡化或移除 backend FIFO，但**不依賴它**

### 5.2 為什麼選 AWS Batch / Slurm-no-backfill 模式

跨業界主流：

| 系統                        | 模式                                                                                                        | 跟我們相容嗎 |
| --------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------ |
| AWS Batch                   | priority + 嚴格 FIFO（[doc](https://docs.aws.amazon.com/batch/latest/userguide/job_queue_parameters.html)） | ✅ 完全      |
| Slurm without backfill      | 同                                                                                                          | ✅           |
| Slurm with backfill         | priority + 用 walltime 做 reservation backfill                                                              | Phase 7+     |
| K8s PriorityClass + preempt | 殺 lower priority running pod                                                                               | ❌ rejected  |
| Kueue                       | WorkloadPriorityClass + cohort preemption                                                                   | ❌ rejected  |
| Volcano `preempt` action    | 同 K8s                                                                                                      | ❌ rejected  |

AWS Batch 預設語意：

> _"jobs are evaluated in priority order ... Active running jobs are not affected by changes in queue priority."_

正是 user 在 brainstorming 階段確認要的：

- Running 不殺
- Pending queue 依 (priority DESC, created_at ASC) 排序
- HEAD 不 fit 時，後面也不 schedule（嚴格 FIFO）
- Admin 才能 bump priority

### 5.3 為什麼整數 priority field（而不是多 queue）

Lolday 規模小（~10 user、2 GPU）。多 queue 設計（高優先 queue / 低優先 queue / 緊急 queue）的開銷 > 收益：

- 多 queue 需要 namespace / quota 設計、UI 多選、permission model 多層
- 整數 priority 是 Slurm `Priority` 與 AWS Batch per-queue priority 的設計
- 整數可以無限多層，未來不限制（Phase 7 加 quota / delegate 都不用改 schema）

### 5.4 為什麼 admin-only priority（initially）

ISLab 環境小、admin 知道誰急。delegated permission / quota / audit log 是 Phase 7+ 才需要：

- ISLab 內部溝通（Discord）成本低；admin bump 一次的開銷比設計 quota 系統低
- Bump priority 的副作用是 **「停掉所有人新 submit 的 priority=0 job 進 Volcano」**（running 不殺）；這是 noticeable 但 graceful 的事件，需要 admin 判斷是否值得做
- delegate 給普通 user 變成 noisy neighbor 風險：A bump 自己、B bump 自己、互相搶
- Phase 7 升級路徑：增加 `priority:write` permission、quota、audit log

### 5.5 為什麼不做 backfill (Phase 6)

Backfill 需要每個 job 的 runtime ETA。Phase 5 加的 `active_deadline_seconds` 是上界不是 ETA（job 可能比 deadline 早結束很多）。但用 deadline 做 conservative backfill 仍然 safe：「保證在 HEAD 預期可開始時間之前跑完」這個判斷用上界仍是嚴格的。

Phase 6 不做的理由：

1. AWS Batch / Slurm-no-backfill 已是 mainstream MVP，加 backfill 是 nice-to-have
2. Backfill 邏輯複雜度 ~3x 於嚴格 FIFO；先讓 Phase 6 嚴格 FIFO 上線觀察 1–2 週
3. 真實看到「GPU 閒置等大 job」的痛點才動 Phase 7

Phase 5 的 `active_deadline_seconds` **不是浪費**：它就是為了未來 backfill 鋪的路。Phase 7 spec 會引用它。

### 5.6 為什麼不做 aging (Phase 6)

Aging（自動 priority 調升）是 Slurm Multifactor Priority 的一個 factor。在 ISLab 規模：

- 手動 bump 的成本 < 維護 aging 演算法 + 調 weight 的成本
- aging 行為不直觀（user 不知道為什麼自己 job 突然被插隊）
- Phase 6 嚴格 FIFO 上線觀察一陣子，看是否真的有「需要自動化」的 starvation event 才動 Phase 7

## 6. Phase 6 detailed design

### 6.1 6a — Remove GPU axis from `lolday-jobs-quota`

**Status: ✅ DONE at commit `4c5c729`** on branch `feat/gpu-fifo-anti-starvation`.

詳見 commit message 與 diff。Plan 不需要再做這件事。

### 6.2 6b — Revert sla plugin from chart

撤回 commit `b836958` 對 `charts/lolday/values.yaml` 的 `volcano.custom.scheduler_config_override` 添加。chart 回到 Volcano sub-chart 預設 scheduler config。

理由：sla plugin 對我們的 multi-GPU leapfrog 沒實際效果（§4.5）。保留它是 dead code，違背 mainstream practice。Phase 7 若 Volcano 上游修 #5044 再評估是否要回來。

### 6.3 6c — Job model + alembic migration

#### 6.3.1 status enum extension

`backend/app/models/job.py` 的 `JobStatus` enum 新增 `queued_backend`：

```
JobStatus.queued_backend
  → user submit 之後、reconciler 還沒送到 Volcano 之前的狀態
  → 從這個狀態 transition 到 (running / submitting / 既有狀態名)
```

具體現有 enum value 名單以 `backend/app/models/job.py` 為準。新 value 插在 normal lifecycle 早期。

#### 6.3.2 priority field

`Job` model 加 column：

| field      | type      | nullable | default | index |
| ---------- | --------- | -------- | ------- | ----- |
| `priority` | `Integer` | No       | `0`     | Yes   |

Index 用於 reconciler 的 `ORDER BY priority DESC, created_at ASC` 查詢。

#### 6.3.3 alembic migration

`backend/migrations/versions/<new>_phase6_priority_and_queued_backend.py`：

1. ADD COLUMN `priority INTEGER NOT NULL DEFAULT 0`
2. ADD INDEX 在 `priority`
3. ALTER TYPE `jobstatus` ADD VALUE 'queued_backend'（Postgres native enum 只能加不能刪；alembic 處理）
4. （aiosqlite 測試）：sqlite enum 是 string 表示，migration 簡化

downgrade：drop column / drop index / 不 drop enum value（Postgres 限制）。

### 6.4 6d — Backend FIFO reconciler

新模組：`backend/app/reconciler/fifo_scheduler.py`

#### 6.4.1 主迴圈

每 30 秒執行一次（可由 env var `FIFO_RECONCILER_PERIOD_SECONDS` 調整，預設 30）：

```python
def reconcile_fifo_queue() -> None:
    # 1. Snapshot capacity
    free_gpu = compute_cluster_free_gpu()

    # 2. Sort
    queued_jobs = db.session.query(Job).filter(
        Job.status == JobStatus.queued_backend
    ).order_by(
        Job.priority.desc(),
        Job.created_at.asc()
    ).all()

    # 3. Process strict FIFO
    for job in queued_jobs:
        if free_gpu >= job.gpu_count:
            try:
                submit_to_volcano(job)
                job.status = JobStatus.running  # or whatever the post-submit state is
                db.session.commit()
                free_gpu -= job.gpu_count
            except Exception as e:
                log.error("submit failed for job %s: %s", job.id, e)
                # leave at queued_backend, retry next cycle
                continue
        else:
            # strict FIFO: stop iteration
            break
```

#### 6.4.2 `compute_cluster_free_gpu()`

```python
def compute_cluster_free_gpu() -> int:
    physical = settings.CLUSTER_PHYSICAL_GPU_COUNT  # 2 for server30

    # GPUs already allocated to running pods in lolday-jobs ns
    running_gpus = sum(
        pod.requests.gpu
        for pod in kubectl_list_pods(ns=settings.JOB_NAMESPACE, phase=["Running", "Pending"])
    )

    # GPUs assigned to vcjobs we've already submitted but not yet visible as Running pods
    submitted_gpus = sum(
        job.gpu_count
        for job in db.session.query(Job).filter(
            Job.status == JobStatus.running,  # backend already submitted
            Job.id.notin_([p.labels["lolday.job-id"] for p in pods if has_label])
        )
    )

    return physical - running_gpus - submitted_gpus
```

精確算法 plan 階段定。要點：**不能 over-commit**，所以已 submit 但 vcjob controller 還沒 spawn pod 的 job 也要扣掉容量。

#### 6.4.3 Concurrency

reconciler thread 與既有 `reconciler.py`（57KB tech debt，Volcano vcjob → DB sync）共存：

- 既有 reconciler 負責**已 submit 的 job 狀態同步**（vcjob → DB）
- 新 reconciler 負責**未 submit 的 job 排程**（DB queued_backend → vcjob create）
- 共用同一個 SQLAlchemy session pattern；row-level 操作用 `SELECT … FOR UPDATE` 或 atomic update 避免競態

**Single-flight 保證**：reconciler 是 lolday backend deployment 的一部分；deployment replicas=1 已是現況（per `charts/lolday/templates/backend.yaml`）。確認 Phase 6 不增加 replicas。

#### 6.4.4 Error handling

| 失敗模式                               | 處理                                                                               |
| -------------------------------------- | ---------------------------------------------------------------------------------- |
| vcjob create 拋例外（API server 拒絕） | log 錯誤；job 留 queued_backend；下個 cycle 重試                                   |
| DB commit 失敗                         | rollback；下個 cycle 重試                                                          |
| 計算 free_gpu 拿到 stale 值            | over-allocate 風險：Volcano 會把多的 vcjob 留 Pending，不會壞；下個 cycle 自然平衡 |
| reconciler thread crash                | k8s probe restart backend pod；重啟後從 DB 恢復                                    |

### 6.5 6e — Backend API

#### 6.5.1 `POST /jobs` (modified)

既有 endpoint，現有 payload：

```json
{
  "type": "train",
  "detector_id": "...",
  "resource_profile": "GPU2",
  "active_deadline_seconds": 3600,
  ...
}
```

Phase 6 改變：

1. 新增 optional `priority: int` field（預設 0）
2. 一般 user 送 `priority != 0` → 403 `priority field is admin-only`
3. Admin 送任何 int 都接受
4. **不再直接 submit vcjob**：而是寫 DB row with status=`queued_backend`，回傳 job id（同既有契約）
5. Reconciler 接手後續

response 不變（仍是 job id + status）。

#### 6.5.2 `PATCH /jobs/{id}` (new)

```json
PATCH /jobs/abc-123
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "priority": 10
}
```

權限：

- 只 `Role.ADMIN` 可呼叫
- 一般 user / service_token 收 403
- 一般 admin 收 200 + 更新後的 job object

限制：

- 只能改 `status=queued_backend` 的 job
- 已 `running` / `completed` / `failed` 的 job → 422 `priority cannot be changed after job has been submitted to Volcano`
- 改完 priority 不會立刻 trigger reconciler；下個 cycle 才生效（最多 30s 延遲，acceptable per design discussion）

response：

```json
{
  "id": "abc-123",
  "status": "queued_backend",
  "priority": 10,
  ...
}
```

#### 6.5.3 不影響的 endpoints

- `GET /jobs/{id}` / `GET /jobs/`：不變，priority 欄位回傳給 admin（一般 user 看到 0）
- `DELETE /jobs/{id}`：不變
- `GET /jobs/{id}/logs` / events：不變

### 6.6 6f — Frontend

#### 6.6.1 Admin view

- Job list 多一個 `Priority` column；可 sort、可 inline edit（呼叫 PATCH）
- Job detail page 顯示 priority；點擊改成 input box 輸入新值
- Submit form：admin 多一個 `priority` numeric input（預設 0）

#### 6.6.2 Regular user view

- Job list 沒有 priority column
- Job detail / submit form 沒有 priority field
- 後端 response 仍含 priority=0；frontend 隱藏

#### 6.6.3 UX 提示

Admin 修改 priority 之前，UI 顯示提示：

> **Bumping priority will pause submission of new lower-priority jobs to Volcano until this job is dispatched. Running jobs are not affected.**

避免 admin 誤用造成「為什麼 user 的 job 都不動了」這種事故。

### 6.7 6g — Smoke test (rewritten)

`tests/2026-05-05-phase6-fifo-smoke.sh` 從「直接 patch scheduler config」改為「透過 lolday API 測試 backend FIFO」。

#### 6.7.1 場景

兩個子測試：

**(a) 嚴格 FIFO**：admin submit gpu=2 + admin submit gpu=1（緊接著），cluster 全空，斷言 gpu=2 先 schedule。

**(b) Priority bump**：admin submit gpu=1 (a) → 之後 admin submit gpu=2 (b) → admin PATCH (b).priority=1。等下個 reconciler cycle，斷言 (b) 是 next-to-submit；當 cluster 容量足夠 (b)，(b) 先 schedule，(a) 之後才 schedule。

#### 6.7.2 認證

smoke 需要 admin token。設計選擇：

- **(i)** 用既有 service-token 機制，給 service token role=admin（用一次性 dev-mode credential）
- **(ii)** 開一個 dev-only API endpoint 接受 secret 來授予 admin 權限，僅在非 prod 啟用
- **(iii)** 用 `kubectl exec` 進 backend pod、直接 call internal CLI

(i) 最 mainstream（其他 phase smoke 也是這樣）。詳細 token 來源在 plan 階段決定。

### 6.8 6h — Documentation

- `docs/architecture.md` §10：
  - Phase 1 entry：「ResourceQuota.GPU 軸已於 Phase 6a 移除」
  - Phase 2 entry：「Phase 6 在 lolday backend 加了 application-layer FIFO scheduler 處理 multi-GPU leapfrog」
  - 新增 Phase 6 entry 描述 backend FIFO + priority + admin permission
- `docs/runbooks/admin-priority.md`（新）：admin 何時 bump priority、副作用、UI 操作
- `.claude/rules/backend.md`：新 reconciler thread 的條目
- `CLAUDE.md` 「How to navigate this codebase」加一條 backend FIFO reference

## 7. Failure modes

| 模式                                          | 觸發                                           | 影響                                                        | 處理                                                                       |
| --------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------- |
| reconciler thread crash                       | bug, OOM                                       | 新 job 卡 queued_backend                                    | k8s probe restart backend pod；重啟後從 DB 恢復                            |
| DB / cluster state 不同步                     | 手動 kubectl 操作 vcjob                        | 計算 free_gpu 出錯，可能 over-allocate                      | 既有 reconciler.py 對齊 DB 與 cluster；reconciler order 設計               |
| Priority bump 之後 race                       | admin bump A 時，B 已被 reconciler 選為 next   | 一個 cycle 後生效（30s 延遲）                               | acceptable per design                                                      |
| Job 卡在 submitted 狀態（vcjob create 失敗）  | API server 拒絕 / 網路錯                       | 浪費容量計算                                                | reconciler 看到後 rollback 回 queued_backend                               |
| 一般 user 嘗試 PATCH priority                 | API misuse                                     | 收 403                                                      | 既有 auth middleware                                                       |
| Admin 把 priority 設超大數字                  | 故意或意外                                     | 該 job 變 next-to-submit；其他 priority < 此值的 job 全部等 | UX 提示警告；audit log（Phase 7）                                          |
| reconciler 沒在跑（startup race）             | backend pod 剛起、reconciler thread 還沒 ready | 新 job 卡 queued_backend 直到 reconciler 起                 | startup 順序：既有 reconciler 起來、API ready 後再起 fifo_scheduler        |
| 手動 kubectl 直接 create vcjob bypass backend | 緊急 debug                                     | backend FIFO 看不到、capacity 計算可能 over                 | 容忍：backend FIFO 計算 cluster.allocated 是看 pods，不是 DB。重啟後不影響 |

## 8. Testing strategy

### 8.1 Unit tests

- `backend/app/reconciler/fifo_scheduler.py` 的 sort logic / fit logic
- `compute_cluster_free_gpu()` 各種 cluster state 的計算
- API endpoint authorization (一般 user 能 / 不能做什麼)

### 8.2 Integration tests (aiosqlite)

- Alembic migration up + down
- POST /jobs creates queued_backend
- PATCH /jobs/{id} priority — admin OK, user 403
- Reconciler 從 DB pull、submit 到 mock K8s

### 8.3 Smoke (live cluster)

- §6.7 兩個子場景
- 跑在 deploy.sh 之後 / staging cluster

### 8.4 Manual operator validation

- Admin bump priority via UI → Discord 內部通知 → 觀察是否其他 user 注意到

## 9. Rollback

完整 rollback 是 multi-step（chart + DB schema + backend code）：

1. **Backend code rollback**：helm rollback to previous backend image。新 reconciler thread 沒了；queued_backend 的 jobs 沒人處理（卡住）。
2. **DB schema rollback**：alembic downgrade。drop priority column + index。enum value 不刪（Postgres 限制）。
3. **Chart rollback**：`helm rollback lolday <prev-revision>`。
4. **Cluster cleanup**：kubectl 清理任何卡住的 vcjobs（手動）。

或者 partial rollback：只撤回 backend code（保留 schema）+ 用既有 reconciler 強制把所有 queued_backend job 直接 submit Volcano。需要 manual SQL 操作。

完整 rollback steps 寫進 plan 與 runbook。

## 10. Open questions

1. **Slurm-style backfill 何時做？** 視 production observation。預期 Phase 7 spec。
2. **Aging 何時做？** 同上。
3. **Per-user priority quota / delegated permission？** 等真實出現「admin bump 太多次造成 noise」才動。
4. **Volcano upstream #5044 何時 merge？** 持續追蹤 GitHub。修了之後可考慮把 backend FIFO 簡化（例如某些 trivial 場景可改回 Volcano 排程）。
5. **Service token role=admin 是否安全？** 若 6g smoke 採用此方法，要設計 token 的 scope（只能 PATCH priority、不能 DELETE / CREATE detector 等）。Plan 階段決定。
6. **30 秒 reconciler period 太慢嗎？** 對 user 體感是 「最多 30s 延遲」。可調 env var。視 observation。

## 11. References

### Mainstream batch scheduler practice

- AWS Batch — [Job Queue priority](https://docs.aws.amazon.com/batch/latest/userguide/job_queue_parameters.html)
- Slurm — [Multifactor Priority](https://slurm.schedmd.com/priority_multifactor.html), [Backfill scheduling](https://slurm.schedmd.com/sched_config.html)
- Kubernetes — [Pod Priority and Preemption](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/)
- Kueue — [WorkloadPriorityClass](https://kueue.sigs.k8s.io/docs/concepts/workload_priority_class/)

### Volcano

- [SLA plugin design doc](https://github.com/volcano-sh/volcano/blob/master/docs/design/sla-plugin.md)
- [Plugins reference](https://volcano.sh/en/docs/plugins/)
- [Actions reference](https://volcano.sh/en/docs/actions/)
- Issue #5044 — [pipelined statement bug](https://github.com/volcano-sh/volcano/issues/5044)（直接造成本 phase 必須走 backend layer 的 root cause）
- Issue #4690 — [strict FIFO request](https://github.com/volcano-sh/volcano/issues/4690)
- Issue #3095 — [small job leapfrog big job since 2023](https://github.com/volcano-sh/volcano/issues/3095)

### Lolday

- Phase 0–5 spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md`
- Phase 1 quota source: `charts/lolday/templates/jobs-quota.yaml`
- Phase 2 queue source: `charts/lolday/templates/volcano-queue.yaml`、`backend/app/services/k8s.py`
- Phase 5 active_deadline_seconds: `backend/app/services/job_spec.py:_active_deadline`
- 活體測試 raw timeline：本 spec §4
