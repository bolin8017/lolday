# GPU FIFO + Anti-Starvation — Design Specification (Phase 6)

> **Phase numbering follows on from `2026-05-05-gpu-scheduling-and-oom-defense-design.md`** (Phases 0–5 already shipped in v0.17.0). This spec adds Phase 6.

## 1. Overview

Phase 2 上線了 Volcano per-user queue + DRF/proportion plugin，目標是「多 user 公平 share GPU」。但 2026-05-05 的活體測試證明：**送出較大的 GPU job 在持續的小 GPU job 流量下會被永久餓死（starvation）**，不是因為 DRF，而是因為兩層獨立的 leapfrog 機制。

這份 spec：

1. 用四組活體測試（b, c, d 系列）證明餓死真的發生、定位兩層真因。
2. 提出兩個 root-cause 修正：移除 K8s `ResourceQuota` 的 GPU 軸、啟用 Volcano `sla` plugin。
3. 對齊 Volcano 官方 mainstream 做法、明寫偏離點。

> **這份 spec 主要回答的問題**：以「FIFO + 老 job 不被卡死」為目標，要把 Phase 2 的 DRF/queue 設計怎麼補完，才能 root-cause 處理「大 GPU job 被小 job 永遠 leapfrog」。

## 2. Authorization

User 在 brainstorming 階段（2026-05-05）明確授權破壞性變更，並重申 root-cause priority + 主流實踐 兩個原則：

- **`lolday-jobs-quota` 拿掉 GPU 軸**（`requests.nvidia.com/gpu` + `limits.nvidia.com/gpu`）。Volcano queue capability 是唯一的 GPU gatekeeper。
- **Volcano scheduler ConfigMap 加 `sla` plugin**。屬於 sub-chart 之外的 in-cluster ConfigMap，沿用 Phase 2 的 patch 路徑（不是 helm value、是 raw configmap.data）。
- **`charts/lolday/values.yaml` 加 `volcano.sla.waitingTime`**（duration string，預設 `10m`），新建的 helm post-install/post-upgrade hook（§6.2）讀這個值 inject 進 scheduler ConfigMap。
- **不需要修 backend**。global default 已涵蓋所有不帶 annotation 的 vcjob，sla plugin 不需要 backend 注入 annotation。
- **不向後相容**。改完之後，舊版「依賴 ResourceQuota 擋 GPU」的部署假設失效——這是 Phase 1 引入時的設計缺陷，不留 compatibility shim。
- **`docs/architecture.md` §10 Phase 1 的描述需要更新**，把「`requests.nvidia.com/gpu: 2` 是防禦線」這條移除。

`scripts/setup-k3s.sh` 不變。host 層 kubelet args（Phase 0）不影響本 phase。

## 3. Scope

### 3.1 In scope (Phase 6)

1. **6a — `lolday-jobs-quota` 移除 GPU 軸**：`charts/lolday/templates/jobs-quota.yaml` 拿掉 `requests.nvidia.com/gpu` + `limits.nvidia.com/gpu`。
2. **6b — Volcano scheduler 加 `sla` plugin**：放 tier 1（與 `priority` / `gang` / `conformance` 同層），帶 global `sla-waiting-time: 10m`。
3. **6c — values.yaml 暴露 `volcano.sla.waitingTime`**：operator 可調，預設 `10m`。
4. **6d — smoke test**：`tests/2026-05-05-phase6-fifo-smoke.sh` 重現 Test D 場景，斷言 d-BIG 在 d-SMALL 之前 schedule。
5. **6e — 文件更新**：`docs/architecture.md` §10 Phase 1 / §10 Phase 2 描述同步；`.claude/rules/charts-and-helm.md` 補 sla plugin 配置位置；`docs/conventions.md` 不變（沒新慣例）。

### 3.2 Out of scope

- **PriorityClass + preempt action**：deferred。preemption 會殺掉跑到一半的 training，丟 checkpoint。要等 maldet 層支援 resume 才能安全啟用。原 Phase 0–5 spec §3.2 已記為 deferred。
- **per-job sla-waiting-time annotation**：deferred。global default 對單一場景已足夠；要 per-user / per-priority 區分時（例：admin 享較短 sla）才打開這條。
- **per-queue `guarantee` 浮動下限**：deferred。research 顯示在 sla 之外、針對極大 job 的「保留容量」可以加，但目前每用戶 cap 已 = 整個 cluster cap，沒有「跨用戶搶資源」的場景需要 guarantee 處理。
- **替換 Volcano**（YuniKorn / Kueue）：rejected，沿用 Phase 0–5 spec §3.2 結論。
- **NVIDIA time-slicing / MPS / MIG / HAMi**：rejected，沿用 §3.2 結論。
- **修改 Phase 2 per-user queue 的 capability**：不動。`gpu=2, mem=30Gi, cpu=8` 等於整個 namespace 與整個 cluster 的上限，剛好讓 sla 在「單一用戶包場 + 大 job 卡」場景也能正確 reserve。

## 4. Empirical evidence (live cluster, 2026-05-05)

四組測試用 `nvidia/cuda:12.6.3-base-ubuntu22.04` 直接送 `batch.volcano.sh/v1alpha1` Job，bypass 後端認證直接打 scheduler。

### 4.1 Test A — sanity: 同一 queue 三個 GPU=1 job

| t (sec) | event                                                         |
| ------- | ------------------------------------------------------------- |
| 0       | submit a-j1 / a-j2 / a-j3 (all gpu=1, sleep 45)               |
| 2       | a-j1 + a-j2 Running on GPU0 + GPU1                            |
| 2–47    | a-j3 stuck `FailedCreate` 14 次 — `lolday-jobs-quota` GPU 2/2 |
| 48      | a-j3 finally schedules (after a-j1 freed quota)               |

→ 確認：**ResourceQuota 在 admission 階段就擋了 a-j3**，不是 Volcano 在排隊。

### 4.2 Test B — leapfrog @ admission level

| t (sec) | event                                                               |
| ------- | ------------------------------------------------------------------- |
| 0       | b-j1, b-j2 Running (quota 2/2)                                      |
| 5       | submit b-BIG (gpu=2) — `FailedCreate`（會超過 quota 2）             |
| 9       | submit b-SMALL (gpu=1) — `FailedCreate`（會超過 quota 2）           |
| 27      | b-j1, b-j2 finish 同時; quota 0/2                                   |
| 31      | **b-SMALL** schedules first (quota 1/2 後，b-BIG 的 retry 仍超過 2) |
| 51      | b-SMALL finishes                                                    |
| 88      | b-BIG finally runs (晚 b-SMALL 一輪)                                |

b-BIG 提交時間早於 b-SMALL **4 秒**，最後晚 **57 秒** 才跑。FailedCreate 計數：b-BIG=14，b-SMALL=12。**Layer 1 root cause 確認：admission race**。

### 4.3 Test C — quota 拿掉、jobs 同時 free → 沒有 leapfrog

把 `lolday-jobs-quota.spec.hard.requests.nvidia.com/gpu` patch 成 `100`（等於拿掉 GPU 軸的 admission cap），重跑 Test B：

| t (sec) | event                                                                            |
| ------- | -------------------------------------------------------------------------------- |
| 0       | c-j1, c-j2 (gpu=1, sleep 25) Running                                             |
| 5       | c-BIG (gpu=2, sleep 20) submitted — Pending（pod 已建立、queue 內等）            |
| 10      | c-SMALL (gpu=1, sleep 20) submitted — Pending                                    |
| 27      | c-j1 + c-j2 同時 finish — 2 GPUs free                                            |
| 29      | **c-BIG schedules first** ✅（priority plugin 認 creationTimestamp，c-BIG 較舊） |
| 50      | c-BIG finishes                                                                   |
| 52      | c-SMALL schedules                                                                |
| 72      | c-SMALL finishes                                                                 |

→ 拿掉 quota 之後，當 head-of-line 能 fit 就照 FIFO 跑。`priority` plugin 的 creationTimestamp 排序是有效的。

### 4.4 Test D — quota 拿掉，但 jobs **錯時** free → leapfrog 仍然發生

| t (sec) | event                                                                       |
| ------- | --------------------------------------------------------------------------- |
| 0       | d-j1 (sleep 30) + d-j2 (sleep 70) Running                                   |
| 5       | d-BIG (gpu=2, sleep 15) submitted                                           |
| 10      | d-SMALL (gpu=1, sleep 15) submitted                                         |
| 33      | d-j1 finishes — 1 GPU free, d-j2 still on GPU1                              |
| 35      | **d-SMALL schedules** ❌（Volcano allocate 試 d-BIG 不 fit → 跳到 d-SMALL） |
| 51      | d-SMALL finishes                                                            |
| 72      | d-j2 finishes — 2 GPUs free                                                 |
| 74      | d-BIG finally schedules                                                     |

d-BIG 比 d-SMALL 早提交 5 秒，被晚送的小 job leapfrog **40 秒**。**Layer 2 root cause 確認：scheduler `allocate` action 跳過 head-of-line 不 fit 的 PG**。

### 4.5 結論

| Layer         | Root cause                                                                    | 何時觸發                                              | 修正                                                                   |
| ------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------- |
| 1 — admission | `lolday-jobs-quota.requests.nvidia.com/gpu` 與 vcjob controller 的 retry race | 大 job 在 quota 滿時被擋；小 job retry 時剛好擠進空隙 | 拿掉 quota 的 GPU 軸                                                   |
| 2 — scheduler | `allocate` action 對 head-of-line 不 fit 時跳到下一個 PG                      | 1 GPU 空、head 要 2 GPU、後面有要 1 GPU 的小 job      | 啟用 `sla` plugin（`JobPipelinedFn` 對 overdue job reserve idle 資源） |

兩層獨立。Layer 1 修了 Layer 2 還會獨立發生（Test D 實證）。所以 Phase 6 必須兩層一起修。

## 5. Architecture decisions

### 5.1 為什麼移 GPU 出 ResourceQuota

K8s `ResourceQuota` 跑在 apiserver 的 admission webhook，**比 Volcano scheduler 早**。一旦 quota 滿，pod 連建立都做不到；vcjob controller 進入 retry loop，重試成功與否取決於 quota 釋放當下哪個 controller goroutine 先動。沒有任何排序語意 — 這是 race，不是 schedule。

Volcano 的 scheduler（包含 `priority` / `drf` / 將要加的 `sla`）只能在 pod 進入 cluster 之後才有發言權。所以 **必須讓 GPU 維度只走 Volcano 的 capability cap，不能走 ResourceQuota**。其他維度（CPU、memory、pod count、vcjob count）保留在 ResourceQuota 是合理的 — 那些是 scheduler-agnostic 的 runaway 防線，沒有跟 Volcano queue 重複 gating。

社群觀察：[volcano-sh/volcano#3426](https://github.com/volcano-sh/volcano/issues/3426) + [NVIDIA/k8s-device-plugin#211](https://github.com/NVIDIA/k8s-device-plugin/issues/211) 都記錄到雙重 gating GPU 會 neutralize Volcano 的 sla / preempt / reclaim 行為。Volcano 官方 docs 沒明寫「不要 dual-gate GPU」，所以這條算「**社群共識，非官方 canonical**」，spec 裡明寫這個 caveat。

### 5.2 為什麼選 `sla` plugin（不是 PriorityClass+preempt）

Volcano 處理 starvation 的官方主流方案有四個：

1. **`sla` plugin** — 設 `sla-waiting-time`，過時 PG 自動 enqueue + reserve。**沒有 preemption，跑中的 job 不被殺**。
2. **`preempt` action + PriorityClass** — 高優先級 PG 殺掉低優先級的 running pod。**會丟 training progress**。
3. **per-queue `guarantee` 浮動下限** — 為大 job tier 預留容量。需要多 queue + reclaim，配置複雜。
4. **`tdm` plugin** — niche，給 revocable task；不適用 ML training。

對 lolday 場景：

- ML training run 中途被 preempt 等於丟 checkpoint。除非 maldet 層先支援 resume，preempt 不能上。
- 我們只有兩個 queue tier（per-user + fallback），沒有「大 job tier」可以 guarantee。
- → **sla plugin 是唯一同時符合 root-cause 修法 + 不丟 progress + 不需要新建 queue 階層** 的官方選項。

來源：Volcano [SLA plugin design doc](https://github.com/volcano-sh/volcano/blob/master/docs/design/sla-plugin.md)、[Plugins reference](https://volcano.sh/en/docs/plugins/)。

### 5.3 sla 的 trade-off（明寫）

- **可短暫超過 queue capability**：`JobEnqueueableFn` overrides `proportion` 的 reject。對 lolday 是可接受的 — per-user cap = 整個 cluster cap，超過也只能 schedule 兩個 GPU 即停。
- **不會 preempt running job**：所以 sla 只能保證「新空出來的 slot」會給老 PG。如果 cluster 完全 packed 沒 idle，sla 等於 no-op，必須等下一個 job 自然結束。對 lolday 場景（每個 job 有 `active_deadline_seconds`）這是上界已知。
- **`sla-waiting-time` 不能設成 `0s`**：等於「立刻 promote」，會打破 priority 的 FIFO 排序。10 分鐘的選擇基於：(a) 一般 detector evaluate/predict job ≈ 5–15 分鐘，10m 確保「跨幾個小 job 的 cycle 結束後」就會輪到大 job；(b) 跟 Phase 4 alert rule 的「Pending > 5m 警示」對齊，sla 在 alert 觸發後 5 分鐘內主動清掉問題。
- **lightly documented**：公開 blog 沒有針對 GPU 大小不均的詳細案例。我們會在 Phase 6 完工後寫 internal runbook 補這個空白。

### 5.4 為什麼 sla plugin 放 tier 1

`sla` 註冊三個 callback（`JobOrderFn` / `JobEnqueueableFn` / `JobPipelinedFn`），都不是 eviction 類。Volcano 官方範例（[design doc §3](https://github.com/volcano-sh/volcano/blob/master/docs/design/sla-plugin.md)）就把 sla 跟 `priority` / `gang` / `conformance` 放同 tier。`priority` 在 sla 之前，PriorityClass 仍主導；`sla` 對「同 priority 的 PG」做 deadline 排序。剛好符合我們需求 — 所有 vcjob 預設 priority=0，sla 接管 tiebreak。

## 6. Phase 6 detailed design

### 6.1 6a — Remove GPU axis from `lolday-jobs-quota`

`charts/lolday/templates/jobs-quota.yaml` diff：

```diff
   spec:
     hard:
       count/jobs.batch: "10"
       count/jobs.batch.volcano.sh: "20"
       count/pods: "16"
       limits.cpu: "24"
       limits.memory: "50Gi"
-      limits.nvidia.com/gpu: "2"
       requests.cpu: "8"
       requests.memory: "30Gi"
-      requests.nvidia.com/gpu: "2"
```

CPU/memory/pod-count/vcjob-count 保留：是 scheduler-agnostic 的 runaway 防線。

對應 Volcano 端的 GPU cap：`charts/lolday/templates/volcano-queue.yaml` 的 `lolday-training` queue 已有 `capability.nvidia.com/gpu: "2"`；`backend/app/services/k8s.py` 的 per-user queue `_USER_QUEUE_CAPABILITY` 也有 `"nvidia.com/gpu": "2"`。**這兩個值在 Phase 6 不變**。

### 6.2 6b — Add `sla` plugin to scheduler ConfigMap

新的 `lolday-scheduler-configmap` 內容（diff）：

```diff
 actions: "enqueue, allocate, backfill"
 tiers:
 - plugins:
   - name: priority
   - name: gang
     enablePreemptable: false
   - name: conformance
+  - name: sla
+    arguments:
+      sla-waiting-time: 10m
 - plugins:
   - name: overcommit
   - name: drf
     enablePreemptable: false
   - name: predicates
   - name: proportion
   - name: nodeorder
   - name: binpack
```

ConfigMap 來自 vendored sub-chart `charts/lolday/charts/volcano-1.14.1.tgz` 的 default value（不是 lolday 自己 template）。`drf` + `proportion` + `priority` 都是 sub-chart 預設 plugin、Phase 2 直接拿來用沒改 ConfigMap；但 `sla` **不在** sub-chart 預設，所以必須**主動 patch ConfigMap**——這是 lolday 第一次需要動 scheduler config。

兩種主流 patch 路徑（選 b）：

a. **fork volcano sub-chart** — 改 vendored chart 內容，重打包 tgz。重，未來升 Volcano 版本要重做。
b. **post-install/post-upgrade hook job** — patch ConfigMap + restart `lolday-scheduler` deployment。輕，與現有 `charts/lolday/templates/volcano-queue.yaml` 的 helm hook 模式同類。

選 **(b)**。新模板：`charts/lolday/templates/volcano-scheduler-config-patch.yaml`，hook=`post-install,post-upgrade`，hook-weight=5（早於 `volcano-queue.yaml` 的 10，確保 scheduler 重啟完成才建立 Queue），實作為 short-lived Job：

1. `kubectl get cm lolday-scheduler-configmap -o yaml`，用 yq / python 解析 `data.volcano-scheduler.conf`。
2. 把 sla plugin block insert 到 tier 1 plugins 末尾（idempotent — 如已存在就 in-place 更新 `sla-waiting-time`）。
3. `kubectl apply` 回去；`kubectl rollout restart deploy lolday-scheduler`；`kubectl rollout status` 等到 Ready。
4. 失敗時 hook fail，整個 helm upgrade 中止。

### 6.3 6c — Expose `volcano.sla.waitingTime` in values.yaml

```yaml
volcano:
  enabled: true
  sla:
    # Anti-starvation: vcjob waiting > waitingTime gets enqueue/pipeline
    # priority boost via Volcano sla plugin (see docs/superpowers/specs/
    # 2026-05-05-gpu-fifo-anti-starvation-design.md §5.3 for tuning).
    waitingTime: 10m
```

Patch hook 從這個 key 讀，注進 ConfigMap。

### 6.4 6d — Smoke test

`tests/2026-05-05-phase6-fifo-smoke.sh`：自動化 Test D 場景。submit d-j1/d-j2/d-BIG/d-SMALL，然後斷言：

```
assert d-BIG.scheduled_at < d-SMALL.scheduled_at
```

如果 sla 沒生效（順序倒過來），smoke 失敗。10 分鐘 sla 對 smoke 很長，所以 smoke 跑時用 `kubectl patch cm` 暫降 `sla-waiting-time` 到 `30s`，跑完還原。**這個降 timer 的動作 smoke 自己處理**，operator 不用手動 patch。

加進 `scripts/deploy.sh` 的 post-deploy verification block。

### 6.5 6e — Documentation updates

- `docs/architecture.md` §10 Phase 1：刪掉「`requests.nvidia.com/gpu: 2` 是防禦線」這條敘述，改成「GPU 軸交由 Volcano queue capability 管理（Phase 6）」並 cross-link。
- `docs/architecture.md` §10 Phase 2：在 Volcano queue 段補一句「sla plugin 提供 anti-starvation aging（Phase 6）」。
- `.claude/rules/charts-and-helm.md`：在 volcano-queue.yaml 的條目下方補 sla plugin patch hook 的位置 + 設計來源連結。
- `docs/runbooks/troubleshooting.md`：補一節「Pending vcjob 超過 sla-waiting-time 仍沒 schedule」的 diagnose 步驟（檢查 scheduler ConfigMap、scheduler pod log、queue allocated/capability）。
- 不寫 user-facing changelog（不影響 user-visible API）。

## 7. Failure modes

| 模式                                 | 觸發                                              | 影響                                                                                       | 處理                                                                                       |
| ------------------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| sla plugin 配置錯（YAML invalid）    | 6b patch 失敗                                     | scheduler crashloop，所有 vcjob 卡 Pending                                                 | helm post-hook job 應 fail-fast；deploy.sh return non-zero；operator 看 events 即時發現    |
| sla 啟用後仍有大 job 卡 > 10m        | cluster 完全 packed、無 job natural finish        | sla 等於 no-op；不丟 job 但 user 看到等很久                                                | Phase 4 alert (`JobsPendingHigh`) 觸發；operator 介入決定 manual cancel 或調 `waitingTime` |
| `lolday-jobs-quota` GPU 軸誤恢復     | 某 phase 升級漏掉 6a 的 chart 改動                | 回退到 Test B 的 admission race；sla 失效                                                  | 6d smoke test 在每次 deploy 跑；smoke 內含「resourcequota 不應有 GPU 軸」斷言              |
| sub-chart Volcano 升級覆蓋 ConfigMap | helm 升 Volcano 1.14 → 1.15                       | sla plugin 配置被覆蓋；leapfrog 復活                                                       | 6b patch hook 是 post-upgrade，每次 helm upgrade 都會重 apply。安全。                      |
| 多 user 同時 → cap 互相打架          | per-user cap = cluster cap，sla 把 quota 短暫超過 | scheduler 看到 over-cap，但 nvidia.com/gpu 是真實硬體限制（physical 2 GPUs），不會「真超」 | 觀察 `volcano-scheduler` log；如果出現「cannot pipeline due to capability」就回頭再 review |

## 8. Testing strategy

### 8.1 Pre-deploy

- `helm template charts/lolday | grep -E 'requests.nvidia.com/gpu' -A 2 -B 2`：確認 `lolday-jobs-quota` 不含 GPU 軸。
- `helm template charts/lolday | grep 'volcano-scheduler-config-patch'`：確認 patch hook 存在。

### 8.2 Post-deploy — smoke

- `bash tests/2026-05-05-phase6-fifo-smoke.sh`：跑 Test D 場景 + 斷言 BIG 先於 SMALL；同時跑 Test B 場景 + 斷言 BIG 先於 SMALL（這時 admission 不再卡，純 sla 救援）。
- `kubectl -n lolday get cm lolday-scheduler-configmap -o yaml | grep -A 2 'name: sla'`：確認 sla 在 tier 1。
- `kubectl -n lolday-jobs get resourcequota lolday-jobs-quota -o jsonpath='{.spec.hard}' | grep -E 'gpu' | wc -l`：應為 0（沒有 GPU 維度）。

### 8.3 Long-running validation

24 小時後跑：

- `kubectl get vcjobs -A --sort-by=.metadata.creationTimestamp` + 比對 `kubectl get pods --sort-by=.status.startTime`：人工驗證沒有「老 job 排在新 job 後面開跑」。
- Prometheus 查 `lolday_jobs_pending_seconds`（Phase 4 metric）的 p99：應該 < 10m + 任何單 job 的 90th percentile 跑時。

## 9. Rollback

如果 Phase 6 上線後發現 scheduler 不穩或測試失敗：

1. **6b 回退**：sla plugin 寫在 ConfigMap 的 `data.volcano-scheduler.conf` string blob 裡，沒辦法用 JSON Patch 抽掉一行。實務做法是 (a) 先 `kubectl get cm lolday-scheduler-configmap -o yaml > /tmp/sched-rollback.yaml`，(b) 手動編輯把 sla 那 3 行刪掉，(c) `kubectl apply -f /tmp/sched-rollback.yaml`，(d) `kubectl -n lolday rollout restart deploy lolday-scheduler`。Rollback 操作流程同樣寫進 runbook。
2. **6a 回退**：`kubectl -n lolday-jobs patch resourcequota lolday-jobs-quota --type=json -p='[{"op":"add","path":"/spec/hard/requests.nvidia.com~1gpu","value":"2"},{"op":"add","path":"/spec/hard/limits.nvidia.com~1gpu","value":"2"}]'`。
3. helm `--reset-values` 重新跑 deploy.sh，把 chart 的 Phase 6 改動 helm-revert 到上一個 release。`helm rollback lolday <prev-revision>` 也是 acceptable 做法；兩者都會把 Phase 6 的 hook job 拿掉，但**不會**自動撤銷 hook 已對 ConfigMap 做出的修改——所以步驟 1 還是必跑。

回退後系統回到 Phase 5 行為（含 Test B 觀察到的 leapfrog）。記在 issue tracker，下個 phase 重做。

## 10. Open questions

1. **`sla-waiting-time` 是否要按 queue 或 user role 分層？** 例：admin 設 `5m`，一般 user `10m`。目前選 global 10m，假設 user 流量沒到「需要保留容量給 admin」的程度。Phase 6 後續觀察 6 週的 Prometheus 數據再決定。
2. **是否同時加 `JobsAgedOver` Prometheus alert？** 預期 sla 上線後不會有 PG `Pending > 30m`；若有就是 cluster 真的在 packed loop，需要人工介入。Phase 4 alert spec 不在本 phase 內，留 Phase 7 追加。
3. **Volcano 升級節奏**：sub-chart 1.14 → 1.15 時，tier 1 plugin order 可能變。每次 sub-chart 升級都要驗證 sla 仍在 tier 1、其他 plugin 沒被踢走。預期 6 週後 1.15 出，到時手動驗證。

## 11. References

### Volcano

- [SLA plugin design doc](https://github.com/volcano-sh/volcano/blob/master/docs/design/sla-plugin.md)
- [SLA plugin source — release-1.14](https://github.com/volcano-sh/volcano/blob/release-1.14/pkg/scheduler/plugins/sla/sla.go)
- [Plugins reference](https://volcano.sh/en/docs/plugins/)
- [Queue Resource Management](https://volcano.sh/en/docs/queue_resource_management/)
- [Actions reference](https://volcano.sh/en/docs/actions/)
- Issue: [SLA plugin behaviour on plain k8s Jobs](https://github.com/volcano-sh/volcano/issues/1901)
- Issue: [GPU quota in queue interaction](https://github.com/volcano-sh/volcano/issues/3426)

### NVIDIA + community

- [NVIDIA — GPU fragmentation tips for Volcano](https://developer.nvidia.com/blog/practical-tips-for-preventing-gpu-fragmentation-for-volcano-scheduler/)
- [NVIDIA/k8s-device-plugin#211 — ResourceQuota interaction](https://github.com/NVIDIA/k8s-device-plugin/issues/211)
- [Huawei CCE — Volcano scheduler add-on（commercial validation）](https://support.huaweicloud.com/eu/usermanual-cce/cce_10_0193.html)

### Lolday

- Phase 0–5 spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md`
- Phase 1 quota source: `charts/lolday/templates/jobs-quota.yaml`
- Phase 2 queue source: `charts/lolday/templates/volcano-queue.yaml`、`backend/app/services/k8s.py:73`
- 活體測試 raw timeline：本 spec §4
