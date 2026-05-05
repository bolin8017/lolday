# GPU Scheduling & OOM Defense — Design Specification

## 1. Overview

Lolday 目前的多用戶 GPU 排程與資源隔離設計**只在 pod cgroup 層做隔離**。`scripts/setup-k3s.sh` 是裸裝 default K3s（沒設 `--kube-reserved` / `--system-reserved` / 沒設 memory eviction），整個集群與所有 lolday infrastructure 與 detector job pod 都跑在同一個 `lolday` namespace 裡，沒有 `ResourceQuota`、沒有 `LimitRange`、Volcano `lolday-training` queue 沒有 `capability` cap，更沒有 per-user 子 queue。

這份 spec 記錄當前缺陷、列出真實場景下會發生什麼、決定一個**只用 K8s/K3s/Volcano/NVIDIA-DCGM 內建功能（不引入新軟體棧）**的多層防禦設計，並把整個方案拆成 5 個獨立可上線的 phase（PR 級別）。

> **這份 spec 主要回答的問題**：當不同 user、或單一 user 送出大量 job 時，系統如何避免被打掛、如何公平排程、如何在 OOM 發生前先發出告警並主動 evict、如何讓多 user 真的能並行使用兩顆 GPU。

## 2. Authorization

破壞性變更獲明確授權（user 在 brainstorming 階段確認）：

- **Namespace 拆分**：`JOB_NAMESPACE` 從 `lolday` 改為 `lolday-jobs`（新名）。同步 `BUILD_NAMESPACE`。Helm chart 結構新增專屬 namespace、移動 NetworkPolicy / ServiceAccount / Secret。
- **K3s kubelet args 變更**：在現有的 systemd unit 加 `--kubelet-arg=...`，需要 `systemctl restart k3s`。預期會短暫中斷 pod；操作流程強制 SSH dry-run。
- **`ResourceProfile` enum 擴充**：新增 `GPU1`，需要 alembic migration（`ALTER TYPE resource_profile_enum ADD VALUE 'gpu1'`）。
- **Volcano queue 重設計**：原 `lolday-training` queue 加 `capability` cap；新增 per-user 子 queue（動態建立）；scheduler config 啟用 `drf` + `proportion` plugin。
- **`backend/app/config.py` 新增 envar**：`JOB_PER_USER_OPEN_LIMIT`（預設 10），擋住 pending 累積。

不做向後相容包裝。`docs/architecture.md` §9 對應條目（無 ResourceQuota、Volcano single-queue）整段重寫。

## 3. Scope

### 3.1 In scope

1. **Phase 0** — host 層 kubelet args 設定（kube-reserved + system-reserved + eviction-hard + eviction-soft）。
2. **Phase 1** — namespace 拆分（`lolday-jobs`）+ `ResourceQuota` + `LimitRange`。
3. **Phase 2** — Volcano per-user queue + queue.capability + scheduler `drf`+`proportion` plugin。
4. **Phase 3** — `ResourceProfile.GPU1` 加入 + UI 選項 + alembic migration。
5. **Phase 4** — Prometheus 告警規則（VRAM、Memory/DiskPressure、queue stale 已存在補強）+ `JOB_PER_USER_OPEN_LIMIT` 含 pending 的上限。
6. **Phase 5** — `Job.active_deadline_seconds` 可 per-job 覆寫（admin 才能繞過全域上限）。
7. 所有上述 phase 的 acceptance test、rollback 步驟、`docs/architecture.md` § 9 / § 10 更新、`.claude/rules/charts-and-helm.md` 補規則。

### 3.2 Out of scope

- **NVIDIA k8s-device-plugin time-slicing**：rejected — 共享 VRAM、無記憶體隔離，**會放大 user 擔心的 OOM 風險**而不是緩解（§ 6.4）。
- **NVIDIA MPS**：rejected — 同樣無 VRAM 隔離（§ 6.4）。
- **NVIDIA MIG**：rejected — RTX 2080 Ti 硬體不支援 MIG（要 A100 / H100 / A30）。
- **HAMi (Heterogeneous AI Manager)**：rejected — 由 4Paradigm + Tencent 主導，違反 `CLAUDE.md` 「Avoid China-origin software」hard rule（§ 6.4）。
- **Apache YuniKorn / Kueue (替換 Volcano)**：rejected — Volcano 已部署且功能 superset。換 scheduler 的工程量遠大於補設定。
- **multi-node distributed training (跨 pod gang scheduling)**：deferred。當前單節點，`replicas=1, minAvailable=1` 已涵蓋。
- **Karpenter / Cluster Autoscaler**：rejected — 單節點不適用。
- **PriorityClass + preemption**（短任務搶佔長 train job）：deferred 到後續 phase。Train 中途被 preempt 會丟 progress，要先在 maldet 層支援 checkpoint resume 才能安全啟用。

### 3.3 Authorization for breaking changes (recap)

§ 2 已列；此處再次強調沒有對 detector / maldet `>=1.1,<2` API 做任何要求變動，user 提交 job 的 API 端點 / payload schema **保持不變**（除了 `resource_profile` 多一個合法值、`active_deadline_seconds` 多一個 optional 欄位）。

## 4. Current state audit (verified 2026-05-05)

直接從 server30 拉到的事實，所有 phase 的設計參數都基於此：

### 4.1 Hardware / OS

| 項目                  | 數值                                                       |
| --------------------- | ---------------------------------------------------------- |
| CPU                   | 12 cores (Capacity = Allocatable，無 reservation)          |
| RAM                   | 65,748,876 Ki ≈ **62 GB**                                  |
| Swap                  | **8 GB**（已啟用，K3s `failSwapOn=false` 容許）            |
| 磁碟 root LV          | 98 GB (used 35 GB / 38%)                                   |
| GPU                   | 2× NVIDIA RTX 2080 Ti, 11 GB VRAM each                     |
| NVIDIA driver         | 560.35.03, CUDA 12.6                                       |
| K3s                   | v1.34.6+k3s1, cgroupDriver=systemd                         |
| Allocatable (current) | 12 CPU, 65,748,876 Ki memory, 2 GPU, 110 pods (= Capacity) |

### 4.2 K8s state（已驗證 — 為設計提供 baseline）

```
$ kubectl get --raw /api/v1/nodes/server30/proxy/configz | jq .kubeletconfig
kubeReserved:    None
systemReserved:  None
evictionHard:    {imagefs.available: 5%, nodefs.available: 5%}
evictionSoft:    None
maxPods:         110
failSwapOn:      false
cgroupDriver:    systemd

$ kubectl get resourcequota --all-namespaces
No resources found

$ kubectl get limitrange --all-namespaces
No resources found

$ kubectl get queues.scheduling.volcano.sh
NAME              PARENT
default           root
lolday-training   root
root

$ kubectl -n lolday get deploy backend -o jsonpath='{...env...}'
JOB_NAMESPACE=lolday
BUILD_NAMESPACE=lolday
JOB_NODE_SELECTOR_HOSTNAME=server30
JOB_PER_USER_CONCURRENCY=2

$ kubectl describe node server30 | grep -A 3 "^Allocated resources:"
cpu:    2155m (17%) requests, 9 (75%) limits
memory: 5386Mi (8%) requests, 24520Mi (38%) limits
```

### 4.3 關鍵觀察

1. **kubelet 沒有 memory.available eviction 規則** — default K3s 只配 disk pressure eviction。RAM 真的吃光時，不會 evict pod，**直接讓 Linux global OOM Killer 動作**，可能殺掉 kubelet/sshd/postgres 任意 process。這是 user 最擔憂場景的根因。
2. **`kubeReserved` / `systemReserved` = 0** — kubelet、containerd、sshd 跟 user pod 共享 root cgroup，沒有任何記憶體保留。
3. **整個集群 0 個 ResourceQuota / 0 個 LimitRange** — namespace 級配額完全空白。
4. **`JOB_NAMESPACE = lolday`** — jobs 與 backend / postgres / harbor / prometheus / mlflow 全部同 namespace。對 ResourceQuota 設計造成困擾：在 `lolday` 上設 quota 會同時限制 infrastructure pods，無法只針對 jobs。
5. **Volcano `lolday-training` queue `weight=1, reclaimable=true`、無 `capability`** — 沒有資源上限。
6. **`evictionHard nodefs.available<5%`** — 對於 98GB root LV 大概剩 4.9GB 才 evict，已經太晚。建議 `<10%` (≈9.8GB headroom)。
7. **swap 8GB 已啟用** — `failSwapOn=false` 是 K3s 預設，容許 swap 存在但 kubelet 自身不使用。意義是 OOM 來臨前 process 會先 swap，造成不可預測的延遲（thrashing），但不會立即 OOM。決定保留 swap（不建議停用，因為實驗室共用機需要保留 fallback）。

## 5. Failure scenarios（依嚴重度排序，每個都標 root cause + 對應 phase 解）

### 5.1 場景 A — Host RAM OOM Killer 殺掉 kubelet（💀 災難級）

**前提**：

- 多個 detector pod 加 prometheus / mlflow 同時在工作，aggregate working set 接近 62 GB。
- kubelet 沒設 reserved，與 user pod 共享 root cgroup。
- evictionHard 沒設 memory.available。

**會發生什麼**：

```
RAM 接近耗盡 → kubelet 不主動 evict（沒設 memory.available 規則）
            → kernel global OOM Killer 啟動
            → 按 oom_score 殺「最胖 process」
            → 可能命中 kubelet（千兆等級記憶體）/ postgres / prometheus
            → kubelet 死 → 所有 pod 變孤兒、API server 失聯
            → SSH 還活著但集群斷頭（與 2026-03-31 Cilium SSH incident 同等級）
```

**Root cause**：kubelet 沒有 memory partition、global OOM Killer 不分對象。

**解**：Phase 0 — `kube-reserved` + `system-reserved` + `eviction-hard memory.available<1Gi` + `eviction-soft memory.available<2Gi grace 2m`。

### 5.2 場景 B — Pending vcjob 累積 runaway（🔥 高）

> **Amendment 2026-05-05 (during Phase 4 implementation):** the original premise of this scenario was wrong. `JOB_PER_USER_CONCURRENCY=2` already counts pending — `routers/jobs.py:262` filters by `NON_TERMINAL_STATUSES = {PENDING, PREPARING, RUNNING}`, all three statuses. A single user therefore cannot accumulate >2 open jobs regardless of POST rate. The runaway path described below is closed by the existing cap. Phase 4 drops `JOB_PER_USER_OPEN_LIMIT` and reduces to instrumentation + alerts only.

**前提**：

- POST /jobs rate limit = 30 reqs / 60s per user。
- `JOB_PER_USER_CONCURRENCY=2` 只擋 in-flight，**不擋 pending**。
- ResourceQuota 沒設、`count/pods` 沒上限。

**會發生什麼**：

```
User A 連送 30 個 train job → 第 31 個被 429 擋下
等 60s 又送 30 個（沒人擋累積總量）
10 分鐘後 DB job table 有 ~150 個 row、Volcano queue ~150 個 vcjob 物件
reconciler 每 10s 掃 non-terminal job → 越掃越慢
backend 兩 replica 競爭 reconcile（未做 leader-elect 確認）
```

**Root cause**：rate limit 是 instantaneous，沒有 stock 累積上限。`JOB_PER_USER_CONCURRENCY=2` 不含 pending。

**解**：Phase 4 — backend 加 `JOB_PER_USER_OPEN_LIMIT`（含 pending），ResourceQuota `count/pods` 上限作為 K8s 層雙保險。

### 5.3 場景 C — 單 Volcano queue 沒 fair-share（🔥 高）

**前提**：

- 全集群只有 `lolday-training` 一個 queue，weight=1。
- 所有 user 的 job 都進這個 queue。
- Volcano 在單 queue 內預設 FIFO（除非 PriorityClass 介入；目前沒設）。

**會發生什麼**：

```
User A 早 1 秒送 → A 的 job 排前面
A 的 in-flight 永遠占 2 個（user 自己 cap 在 2）
A 第一個跑完，第二個立刻就位
B 的第一個 job 永遠在 A 後面
GPU2 + 6h active deadline 之下，B 最壞等 12h
體感「我送不出 job、永遠在排隊」
```

**Root cause**：queue 拓樸是單 root + 兩 leaf（default + lolday-training），沒有 per-user 結構，沒有 drf plugin 啟用。

**解**：Phase 2 — 改為 per-user 子 queue (`lolday-u-<id12>`, weight=1, capability=`{nvidia.com/gpu=1}`) + 啟用 `drf` + `proportion` plugin。

### 5.4 場景 D — CUDA OOM (VRAM)（⚠️ 中）

**前提**：

- detector code batch_size 設太大 / model 太大，VRAM 11 GB 不夠。
- nvidia.com/gpu 是設備配給，**不限制 VRAM 用量**。

**會發生什麼**：

```
detector pod 啟動 → torch.cuda.malloc(>11GB) → CUDA OutOfMemory
process exit RuntimeError → pod fail → AbortJob
集群不受影響
但：user 沒有 early warning，直到 fail 才知道。重跑同 config 還是會 fail。
```

**Root cause**：K8s 沒有 GPU memory primitive；NVIDIA 在沒 MIG 的硬體上沒有原生 K8s 整合的 VRAM 限額。

**解**：

- 不採 time-slicing / MPS（這兩個會加重，不會改善）。
- Phase 4 — DCGM `DCGM_FI_DEV_FB_USED / (FB_USED+FB_FREE) > 0.9 持續 3m` Prometheus 告警，提早讓 user / admin 知道 VRAM 高水位。
- 文件層 — 在 detector dev guide 建議使用 `torch.cuda.set_per_process_memory_fraction(0.95)` 或 maldet `MALDET_GPU_MEMORY_FRACTION` env（後者需要 maldet 1.2+ 支援，**這份 spec 不要求**，僅作為文件建議）。

### 5.5 場景 E — Disk pressure 把 pod evict（⚠️ 中）

**前提**：

- 每個 detector pod `output emptyDir 10Gi + source-model 2Gi + tmp 1Gi(RAM)` = 12+1Gi 上限
- root LV 98GB；prometheus retention 預設 15d（可吃到 50GB）；Harbor image 累積；postgres data
- evictionHard nodefs.available<5% ≈ 4.9GB 才 evict

**會發生什麼**：

```
disk free 跌到 5GB → kubelet 上 DiskPressure taint → evict pod
pod 突然消失 → reconciler 標 failed
Prometheus 自身被 evict 也常見 → 觀測中斷
```

**Root cause**：disk eviction 閾值偏低，且沒有 Prometheus alert 預警。

**解**：

- Phase 0 — `eviction-hard nodefs.available<10%`、`imagefs.available<10%`、`eviction-soft nodefs.available<15% grace 2m`。
- Phase 4 — Prometheus alert: `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.15`。

### 5.6 場景 F — 單 detector 把自己吃爆（✅ 已處理）

cgroup memory limit=16Gi → cgroup OOM-kill detector process → pod fail → AbortJob。集群不受影響。**不需新動作**。

### 5.7 場景 G — CPU 暴衝（✅ 已處理）

cgroup CPU limit=4 → CFS throttling。**不需新動作**。

## 6. Architecture

### 6.1 多層防禦疊加

防禦從外層往內層，每層處理不同 blast radius：

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 0: Linux kernel + K3s kubelet                         │
│   • kube-reserved + system-reserved (cgroup memory partition) │
│   • eviction-hard memory.available<1Gi                      │
│   • eviction-soft memory.available<2Gi grace 2m             │
│   • eviction-hard nodefs.available<10%, imagefs<10%         │
│   ↓ 防 host-level OOM Killer 殺到 kubelet                  │
├──────────────────────────────────────────────────────────────┤
│ Layer 1: K8s namespace `lolday-jobs`                        │
│   • ResourceQuota: total cpu/mem/gpu/pods cap               │
│   • LimitRange: per-pod default + max                       │
│   ↓ 防 jobs namespace 整體吃爆                              │
├──────────────────────────────────────────────────────────────┤
│ Layer 2: Volcano queue                                      │
│   • lolday-training queue.capability (cluster-level cap)    │
│   • per-user queue lolday-u-<id12> capability gpu=1         │
│   • scheduler drf+proportion plugin → fair-share between users│
│   ↓ 防 user 互相搶 GPU、同 user 累積                       │
├──────────────────────────────────────────────────────────────┤
│ Layer 3: Pod cgroup                                         │
│   • detector container limits: cpu=4, mem=16Gi, gpu=N        │
│   • init/sidecar 資源各自設定                                 │
│   ↓ 防單 detector 吃爆                                      │
├──────────────────────────────────────────────────────────────┤
│ Layer 4: Application (maldet)                               │
│   • detector code 自己決定 batch_size / VRAM 使用            │
│   • CUDA OOM exception → pod fail (cgroup隔離)               │
│   ↓ user 自己負責的範圍                                     │
└──────────────────────────────────────────────────────────────┘

Layer 5: Observability (橫切)
   • Prometheus alerts on Memory/DiskPressure, VRAM > 90%
   • DCGM exporter 已部署
   • alertmanager Discord routing 已部署
```

每一層失敗時下一層仍能撐住。**這是業界 mainstream 的多層防禦（defense in depth），所有 layer 都用 K8s/K3s/Volcano/NVIDIA 內建功能，沒引入新軟體**。

### 6.2 為什麼 namespace 拆分是必要的

`lolday` 目前 namespace 同時容納：

- 平台 infrastructure（postgres, redis, mlflow, harbor, backend, frontend, cloudflared, kps, loki, alloy, trivy）
- 用戶 jobs（detector vcjob pods）
- 用戶 builds（buildkit pods）

ResourceQuota / LimitRange 的作用對象是 namespace。如果在 `lolday` 上設：

- `requests.memory: 30Gi` → 也會限制 prometheus / grafana / postgres 等 infra
- LimitRange `defaultRequest: 1Gi` → 也會 default-inject 到 infra pods

**結論：必須把 jobs 與 infra 拆到不同 namespace**，這是業界 mainstream 多租戶設計。新 namespace `lolday-jobs` 容納 jobs（vcjob）+ builds（buildkit Job）。`lolday` 維持 infra-only。

NetworkPolicy 加 cross-namespace 規則：

- backend in `lolday` → 接收 `lolday-jobs` pod 的 `POST /internal/jobs/{id}/events`（既有路由）。
- jobs 拉 image from `harbor.lolday.svc` — 走 host-level 設定（K3s containerd registry mirror）+ K8s in-cluster DNS（`harbor.harbor.svc`）— 兩者都是 cross-namespace，已可行。
- jobs 連 `mlflow.lolday.svc` — cross-namespace egress 需在 NetworkPolicy 開白名單。

### 6.3 為什麼 per-user Volcano queue 是必要的

當前 `lolday-training` 單 queue：

- weight=1（無對手 queue 比較）
- 全 user 的 job 都進去
- Volcano 內部排序：FIFO（CreationTimestamp）+ priority (沒設)

這 = **雜湊 FIFO，不公平**。User A 早送一秒就排在前面，A 的 in-flight 永遠占 2 個 → B 永遠排隊。

**解**：每個 user 一個 queue，全部都掛在 root 之下，`drf` (Dominant Resource Fairness) plugin 讓 scheduler 在每個 cycle 選「目前 dominant share 最低的 queue」。queue 之間因為 weight 都=1 → fair-share。配合 `capability nvidia.com/gpu=1` 確保任一 user 同時只能用 1 顆 GPU → 兩 user 各 1 GPU 真的並行。

實作層面：

- Helm 不負責建立 per-user queue（user 是動態建立 entity）。
- Backend `services/k8s.py::ensure_user_queue(user_id) -> str` 在第一次 POST /jobs 時 idempotent 建立。
- User 軟刪 / 硬刪 → reconciler 連動刪 queue（後續 phase；本期先不刪，留作 admin task）。

### 6.4 為什麼拒絕 GPU sharing（time-slicing / MPS / MIG / HAMi）

User 的擔憂直接核心是「OOM crash」。所有「軟切 GPU」方案的 trade-off：

| 方案                                  | 來源                       | VRAM 隔離？ | 違反 China-origin？      | 對 OOM 風險？        |
| ------------------------------------- | -------------------------- | ----------- | ------------------------ | -------------------- |
| NVIDIA k8s-device-plugin time-slicing | NVIDIA 官方                | ❌ 共享     | 否                       | **加重**             |
| NVIDIA MPS                            | NVIDIA 官方                | ❌ 共享     | 否                       | **加重**             |
| NVIDIA MIG                            | NVIDIA HW                  | ✅ 真隔離   | 否                       | 解決，**但需新硬體** |
| NVIDIA vGPU                           | NVIDIA 商用                | ✅ 真隔離   | 否                       | 解決，**但需授權**   |
| HAMi                                  | 4Paradigm + Tencent (中國) | ✅ 軟隔離   | **是**（hard rule 拒絕） | 解決，但被 ban       |
| Tencent GPUManager                    | Tencent (中國)             | 部分        | **是**                   | 同上                 |

**結論**：在 RTX 2080 Ti + 拒絕中國原生軟體 + 不購置新硬體的前提下，**「1 GPU 1 pod 獨佔」是唯一安全的取捨**。`GPU1` profile + per-user queue capability=1 GPU 等同於：

- 兩 user 各佔一張 GPU → 真並行
- 每張 GPU 只給一個 detector pod → VRAM 不互踩
- 用「並行度=2」交換「不會 VRAM OOM 互踩」的確定性

Trade-off 顯式記錄：放棄「1 GPU 同時跑 2 個 evaluate」的彈性，換取「兩個 user 同時各跑 1 個 train」的確定性。對實驗室規模而言這是合理且符合主流 K8s + Volcano 設計的選擇。

### 6.5 Phase 拓樸與依賴

```
Phase 0 (host kubelet args)
    │ 獨立、純 systemd / K3s, 不動 helm
    │ 操作員手動執行 + SSH safety dry-run
    ▼
Phase 1 (lolday-jobs namespace + ResourceQuota + LimitRange)
    │ helm chart 變更，需要 deploy.sh
    │ 把 JOB_NAMESPACE / BUILD_NAMESPACE 從 lolday 改 lolday-jobs
    ▼
Phase 2 (Volcano per-user queue + capability + drf plugin)
    │ helm chart + backend services/k8s.py 變更
    │ 依賴 Phase 1 完成（queue 內 capability 才有意義）
    ▼
Phase 3 (ResourceProfile.GPU1)
    │ alembic migration + backend enum + UI 選項
    │ 依賴 Phase 2（GPU1 profile 與 per-user queue 配合）
    ▼
Phase 4 (Prometheus alerts + JOB_PER_USER_OPEN_LIMIT)
    │ chart monitoring + backend routers/jobs.py
    │ 獨立，可與 Phase 3 並行
    ▼
Phase 5 (per-job active_deadline_seconds override)
    │ alembic + backend + UI
    │ 純 UX，最後做
```

可獨立 PR 化的順序：0 → 1 → 2 → 3 (與 4 並行) → 4 → 5。

## 7. Per-phase design

### Phase 0 — Host kubelet args

**目標**：解 § 5.1（OOM Killer 殺 kubelet）+ § 5.5（disk pressure evict 太晚）。

**改動**：

1. `scripts/setup-k3s.sh`（**新裝叢集用**）— 加 `INSTALL_K3S_EXEC` 環境變數帶 kubelet args：

   ```bash
   curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
     --kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi \
     --kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi \
     --kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10% \
     --kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15% \
     --kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m \
     --kubelet-arg=eviction-max-pod-grace-period=60" \
     sh -
   ```

2. `scripts/patch-k3s-kubelet-args.sh`（**新檔，在 server30 套用到既有叢集**）— 不重裝、改 systemd unit、`daemon-reload`、`systemctl restart k3s`。**全程 SSH safety dry-run + 操作員確認**。

3. `docs/runbooks/deploy.md` 補一節「升級 kubelet args」。

**參數選擇依據**：

| 參數                   | 值   | 為什麼                                                                                                 |
| ---------------------- | ---- | ------------------------------------------------------------------------------------------------------ |
| kube-reserved memory   | 2Gi  | kubelet + containerd 在實務上 working set 1–1.5Gi；保 2Gi 留 safety margin。GKE 同等級 node 的預設值。 |
| kube-reserved cpu      | 1    | kubelet/containerd 高峰用量 ~500m；保 1 core。                                                         |
| kube-reserved disk     | 10Gi | containerd image cache + kubelet root state。                                                          |
| system-reserved memory | 4Gi  | systemd + sshd + kernel buffers + dmesg + docker shim 殘留。62Gi RAM 約 6.5%。                         |
| system-reserved cpu    | 1    | sshd 多 session + dmesg flushing 不擋 user。                                                           |
| system-reserved disk   | 10Gi | journald 日誌 + apt cache + 系統工具。                                                                 |
| eviction-hard memory   | <1Gi | 真的快爆才硬 evict。                                                                                   |
| eviction-soft memory   | <2Gi | 預警，grace 2m 給 pod 完成當前 task。                                                                  |
| eviction-hard nodefs   | <10% | 98GB × 10% = 9.8GB，比現在 5% (4.9GB) 安全。                                                           |
| eviction-soft nodefs   | <15% | 預警 14.7GB headroom。                                                                                 |
| eviction-max-pod-grace | 60   | 給 pod 60 秒清理（sigterm → sigkill）。                                                                |

**結果（Allocatable 變化）**：

| 維度      | 改前                  | 改後                       | 差額      |
| --------- | --------------------- | -------------------------- | --------- |
| CPU       | 12                    | 10                         | -2        |
| memory    | 65,748,876 Ki ≈ 62 GB | ≈ 55 GB（扣 7Gi reserved） | -7 GB     |
| ephemeral | ~466 GB               | ~446 GB                    | -20 GB    |
| pods      | 110                   | 110                        | unchanged |

當前 system 級總 limits 用量 = 24.5 Gi（38%）→ 55 Gi 仍有 30 Gi 空間給 jobs。

**Acceptance**：

- `kubectl get --raw /api/v1/nodes/server30/proxy/configz | jq .kubeletconfig.kubeReserved` 返回非 None
- `kubectl describe node server30` Allocatable.memory 變小
- 模擬：在 lolday-jobs ns 跑 `kubectl run oom-test --image=python:3.12 --restart=Never -- python -c "x=[1]*int(1e10)"` → kubelet 回應 MemoryPressure taint 而非整機掛掉

**Rollback**：手動編輯 systemd unit 移除 `--kubelet-arg=...`、`systemctl restart k3s`。Allocatable 回復原狀。

### Phase 1 — `lolday-jobs` namespace + ResourceQuota + LimitRange

**目標**：解 § 6.2（namespace 隔離）+ 為 ResourceQuota 提供作用域。

**改動**：

1. **新檔** `charts/lolday/templates/jobs-namespace.yaml`：

   ```yaml
   apiVersion: v1
   kind: Namespace
   metadata:
     name: {{ .Values.jobsNamespace | default "lolday-jobs" }}
     labels:
       {{- include "lolday.labels" . | nindent 4 }}
       lolday.role: workload
   ```

2. **新檔** `charts/lolday/templates/jobs-quota.yaml`：

   ```yaml
   apiVersion: v1
   kind: ResourceQuota
   metadata:
     name: lolday-jobs-quota
     namespace: { { .Values.jobsNamespace } }
   spec:
     hard:
       requests.cpu: "8"
       requests.memory: 30Gi
       limits.cpu: "24"
       limits.memory: 50Gi
       requests.nvidia.com/gpu: "2"
       count/pods: "16"
       count/jobs.batch: "10"
       count/jobs.batch.volcano.sh: "20"
   ```

   **數字依據**：當前 detector pod limits.memory=16Gi，最多 2 顆 GPU 都用 → 2× 16Gi = 32Gi 主要負載 + init/sidecar < 1Gi × 4 = 4Gi → 36Gi 上限。設 50Gi limits 留 14Gi headroom。requests=30Gi 是 cgroup 預先預留，allocatable 55Gi 扣 30Gi 留 25Gi 給 lolday infra ns。`count/pods=16` 含 init + sidecar：以 2 GPU2 並跑為例 = 2 pods × 5 containers = 10 個 pod-equivalent，留 6 個 slot 給 build pods + pending。

3. **新檔** `charts/lolday/templates/jobs-limitrange.yaml`：

   ```yaml
   apiVersion: v1
   kind: LimitRange
   metadata:
     name: lolday-jobs-limits
     namespace: {{ .Values.jobsNamespace }}
   spec:
     limits:
       - type: Container
         max:           { cpu: "4",   memory: "16Gi" }
         default:       { cpu: "2",   memory: "4Gi"  }
         defaultRequest:{ cpu: "500m", memory: "1Gi" }
   ```

4. **新檔** `charts/lolday/templates/lolday-quota.yaml` — 在 `lolday` infra namespace 設 ResourceQuota（防 infra 自身失控）：

   ```yaml
   apiVersion: v1
   kind: ResourceQuota
   metadata:
     name: lolday-infra-quota
     namespace: { { .Release.Namespace } }
   spec:
     hard:
       requests.memory: "20Gi"
       limits.memory: "40Gi"
   ```

5. **修改** `charts/lolday/values.yaml`：

   ```yaml
   global:
     jobsNamespace: lolday-jobs
   backend:
     env:
       JOB_NAMESPACE: lolday-jobs
       BUILD_NAMESPACE: lolday-jobs
   ```

6. **修改** `charts/lolday/templates/job-networkpolicy.yaml` + `build-networkpolicy.yaml`：作用域改為 `lolday-jobs`，加 cross-namespace egress to `lolday`（backend, mlflow）。

7. **修改** `charts/lolday/templates/network-policy.yaml`（lolday infra ns）：ingress 開放從 `lolday-jobs` 進入 backend 的 `POST /internal/jobs/{id}/events`。

8. **新檔** `scripts/migrate-jobs-namespace.sh`：
   - `kubectl create ns lolday-jobs` (idempotent)
   - 等下一輪 `deploy.sh` 走完
   - 確認舊 `lolday` ns 沒有 vcjob 殘留（`kubectl get vcjobs -n lolday` 應為空）
   - 沒殘留就完成；有殘留就提示 operator 等到完成再 migrate

   **重要**：當前 `lolday` ns 沒有 active vcjob（驗證過），所以這個 migration 是 zero-downtime 的（新 vcjob 建在新 ns，舊 ns 自然乾淨）。

**Acceptance**：

- `kubectl get ns lolday-jobs` 存在
- `kubectl get resourcequota -n lolday-jobs` 列出 quota
- 模擬：`kubectl -n lolday-jobs run too-big --image=busybox --requests=memory=40Gi -- sleep 60` 應被 K8s admission 拒絕（超過 LimitRange.max 16Gi）
- Backend POST /jobs 後新 vcjob 出現在 `lolday-jobs` ns

**Rollback**：

- 把 `JOB_NAMESPACE` env 改回 `lolday`、`deploy.sh`
- 刪除 `lolday-jobs` ns 內 ResourceQuota + LimitRange
- ns 本身保留無妨（空 ns 不消耗）

### Phase 2 — Volcano per-user queue + capability cap + drf plugin

**目標**：解 § 5.3（單 queue 沒 fair-share）。

**改動**：

1. **修改** `charts/lolday/templates/volcano-queue.yaml` — 把 `lolday-training` 改成 cluster-level cap（不是 per-user 的，但是 jobs 用 queue 的 root parent）：

   ```yaml
   apiVersion: scheduling.volcano.sh/v1beta1
   kind: Queue
   metadata:
     name: lolday-training
   spec:
     weight: 1
     reclaimable: true
     capability:
       cpu: "8"
       memory: 30Gi
       nvidia.com/gpu: "2"
   ```

   這個 queue 不再被 backend 直接寫入 vcjob，而是作為 hierarchy 中的「lolday-jobs 工作的總上限」。但 Volcano queue hierarchy 在 1.14 仍然 flat（只支援 root → leaf 兩層），所以實際上 `lolday-training` 與 per-user queue 並列為 root 的 leaf。

2. **新檔** `charts/lolday/templates/volcano-scheduler-config.yaml`：

   ```yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: volcano-scheduler-configmap
     namespace: volcano-system
   data:
     volcano-scheduler.conf: |
       actions: "enqueue, allocate, backfill"
       tiers:
         - plugins:
             - name: priority
             - name: gang
             - name: conformance
         - plugins:
             - name: drf
             - name: predicates
             - name: proportion
             - name: nodeorder
             - name: binpack
   ```

   啟用 `drf` (Dominant Resource Fairness) 與 `proportion` 是業界主流 fair-share 配置。`backfill` 讓 GPU2 大 job 在前面、後面排小 job 也能在 GPU 空檔時跑進去。**移除 preempt action**（後續 phase 再評估）。

3. **修改** `backend/app/services/k8s.py` — 加 `ensure_user_queue(user_id: UUID) -> str`：

   ```python
   def queue_name_for_user(user_id: UUID) -> str:
       return f"lolday-u-{user_id.hex[:12]}"

   async def ensure_user_queue(user_id: UUID) -> str:
       name = queue_name_for_user(user_id)
       try:
           # idempotent — 用 server-side apply
           await _api.create_namespaced_custom_object(
               group="scheduling.volcano.sh", version="v1beta1",
               namespace="", plural="queues",  # cluster-scoped
               body={
                   "apiVersion": "scheduling.volcano.sh/v1beta1",
                   "kind": "Queue",
                   "metadata": {"name": name, "labels": {
                       "lolday.role": "user-queue",
                       "lolday.user-id": str(user_id),
                   }},
                   "spec": {
                       "weight": 1,
                       "reclaimable": True,
                       "capability": {"nvidia.com/gpu": "1"},
                   },
               },
           )
       except ApiException as e:
           if e.status != 409:  # 409 = already exists, ok
               raise
       return name
   ```

4. **修改** `backend/app/services/job_spec.py::build_volcano_job_manifest` — `spec.queue` 從 hardcode `lolday-training` 改為傳入 `queue_name`，由 `routers/jobs.py` 在 submit 時呼叫 `ensure_user_queue(user.id)` 得到。

5. **修改** `backend/app/routers/jobs.py::create_job` — 在 build manifest 之前呼叫 `await ensure_user_queue(user.id)`。

6. **新單元測試** `backend/tests/services/test_k8s_user_queue.py`：
   - `ensure_user_queue` 第一次呼叫建立 Queue
   - 第二次呼叫遇 409 不 raise
   - queue name 格式 `lolday-u-{12hex}`

**為什麼 capability=`nvidia.com/gpu: "1"`**：每個 user 同時最多只能用 1 顆 GPU。**這配合 Phase 3 的 GPU1 profile**，兩 user 各拿一顆 → 真並行。如果 user 想跑 GPU2，capability 會擋住 → user 看到 vcjob 卡 pending until 他自己另一個 job 結束。設計上保守但可預期。

**Acceptance**：

- 兩個 user 各送 1 個 GPU1 train → 都能 running（不再排隊）
- 同一 user 連送兩個 GPU1 → 第二個 pending（capability 擋）
- 一個 user 送 1 個 GPU2 train → 正常 running（占滿自己的 capability=1 不算違反，因為 capability 是 limit，一個 vcjob 申請 2 個 GPU 仍然 fit 在 1 user 範圍 — 確認 Volcano 行為，可能要改 capability 設 `nvidia.com/gpu: "2"`）

  > **設計開放問題 OQ-1**：capability `nvidia.com/gpu: "1"` 是對「該 queue 所有 vcjob 的累加」還是「單 vcjob 的 max」？Volcano docs §queue.capability：「the upper limit of resources the queue uses」是累加。所以 GPU2 vcjob 會超過 capability=1 而 pending。需要：(a) capability 設為 `"2"`（user 可以單個 GPU2 但不能兩個 GPU1） 或 (b) 強制 user 單 user 只允許 GPU1（與 Phase 3 配合，GPU2 留給特殊 admin role）。**建議採 (b)**，理由：mainstream multi-tenant cluster 都把 user-level cap 設為 share-fair 而非 burst。GPU2 設成 admin-only profile，UI 對非 admin user 不顯示。

### Phase 3 — `ResourceProfile.GPU1`

**目標**：與 Phase 2 配合，讓兩 user 真的能並行各占 1 GPU。

**改動**：

1. `backend/app/models/job.py`：

   ```python
   class ResourceProfile(StrEnum):
       STANDARD = "standard"
       GPU1 = "gpu1"      # 新增
       GPU2 = "gpu2"

   _RESOURCE_PROFILE_GPU_COUNT = MappingProxyType({
       ResourceProfile.STANDARD: 0,
       ResourceProfile.GPU1: 1,    # 新增
       ResourceProfile.GPU2: 2,
   })
   # 既有 assertion 自動覆蓋新 enum
   ```

2. **新 alembic migration** `backend/migrations/versions/<rev>_add_gpu1_profile.py`：

   ```python
   def upgrade():
       op.execute("ALTER TYPE resource_profile_enum ADD VALUE IF NOT EXISTS 'gpu1' BEFORE 'gpu2'")
   def downgrade():
       # postgres ALTER TYPE … DROP VALUE 不被支援；用 type swap
       op.execute("ALTER TYPE resource_profile_enum RENAME TO resource_profile_enum_old")
       op.execute("CREATE TYPE resource_profile_enum AS ENUM ('standard','gpu2')")
       op.execute("ALTER TABLE job ALTER COLUMN resource_profile TYPE resource_profile_enum "
                  "USING resource_profile::text::resource_profile_enum")
       op.execute("DROP TYPE resource_profile_enum_old")
   ```

   **注意 alembic-migrations.md rules**：手寫，不用 autogenerate（autogenerate 對 enum 不可靠）。

3. `backend/app/services/job_spec.py::_detector_container` — `gpu_count=1` 時 `gpu_strategy` 改為 `"none"`（DDP 在 1 GPU 沒意義）：

   ```python
   def _detector_container(..., gpu_count, gpu_strategy):
       effective_strategy = "none" if gpu_count <= 1 else gpu_strategy
       ...
       env=[{"name": "MALDET_DISTRIBUTED_STRATEGY", "value": effective_strategy}, ...]
   ```

4. `frontend/src/api/schema.gen.ts` 重生（順手解 architecture.md §9 #14）— 用 `pnpm gen-api-types` 對 dev backend 跑。

5. `frontend/src/components/jobs/ResourceProfileSelect.tsx`（如有）— 加 `GPU1` 選項。**`GPU2` 標 admin-only**，依 user.role 顯示。

6. **新單元測試**：
   - `test_resource_profile_gpu_count` covers GPU1 = 1
   - `test_job_spec_gpu1_strategy` GPU1 → `MALDET_DISTRIBUTED_STRATEGY=none`

**Acceptance**：

- POST /jobs 帶 `resource_profile: "gpu1"` 成功建立 vcjob，pod 申請 1 GPU
- 兩 user 各送 GPU1 → 都 running
- GPU2 仍可建立（admin-only UI 規則由 frontend 處理；backend 不限）

### Phase 4 — Prometheus alerts + queue depth cap

**目標**：解 § 5.2（pending runaway）+ § 5.4（VRAM 預警）+ § 5.5（disk pressure 預警）+ § 5.1 的觀測補強（NodeMemoryPressure alert）。

**改動**：

1. **修改** `charts/lolday/templates/monitoring/alertmanager-rules.yaml` — 新增 PrometheusRule 群組 `lolday-resource-pressure`：

   ```yaml
   groups:
     - name: lolday-resource-pressure
       interval: 30s
       rules:
         - alert: LoldayNodeMemoryPressure
           expr: kube_node_status_condition{condition="MemoryPressure",status="true"} == 1
           for: 1m
           labels: { severity: critical }
           annotations:
             summary: "{{ $labels.node }} 進入 MemoryPressure，kubelet 主動 evict pod"
             runbook: "docs/runbooks/troubleshooting.md#node-memory-pressure"

         - alert: LoldayNodeDiskPressure
           expr: kube_node_status_condition{condition="DiskPressure",status="true"} == 1
           for: 1m
           labels: { severity: critical }

         - alert: LoldayGPUVRAMHigh
           expr: |
             max by (gpu, hostname) (
               DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE)
             ) > 0.9
           for: 3m
           labels: { severity: warning }
           annotations:
             summary: "GPU {{ $labels.gpu }} VRAM > 90% 持續 3m"

         - alert: LoldayGPUTempCritical
           expr: DCGM_FI_DEV_GPU_TEMP > 85
           for: 5m
           labels: { severity: warning }

         - alert: LoldayJobsQuotaCPUNearLimit
           expr: |
             sum by (namespace) (kube_resourcequota{resource="requests.cpu",type="used",namespace="lolday-jobs"})
             / sum by (namespace) (kube_resourcequota{resource="requests.cpu",type="hard",namespace="lolday-jobs"}) > 0.85
           for: 5m
           labels: { severity: warning }

         - alert: LoldayJobsQuotaMemoryNearLimit
           expr: |
             sum by (namespace) (kube_resourcequota{resource="requests.memory",type="used",namespace="lolday-jobs"})
             / sum by (namespace) (kube_resourcequota{resource="requests.memory",type="hard",namespace="lolday-jobs"}) > 0.85
           for: 5m
           labels: { severity: warning }

         - alert: LoldayPendingJobsHigh
           expr: lolday_jobs_pending_total > 30
           for: 10m
           labels: { severity: warning }
   ```

2. **修改** `backend/app/config.py` — 新增 `JOB_PER_USER_OPEN_LIMIT: int = 10`。

3. **修改** `backend/app/routers/jobs.py::create_job` — 在 in-flight check 前加：

   ```python
   open_count = await session.scalar(
       select(func.count(Job.id)).where(
           Job.owner_id == user.id,
           Job.status.in_(NON_TERMINAL_STATUSES),
       )
   )
   if open_count >= settings.JOB_PER_USER_OPEN_LIMIT:
       raise HTTPException(429, detail=f"open jobs limit ({settings.JOB_PER_USER_OPEN_LIMIT}) reached")
   ```

4. **新 metric** `backend/app/services/jobs.py` — `lolday_jobs_pending_total` Gauge：

   ```python
   from prometheus_client import Gauge
   JOBS_PENDING = Gauge("lolday_jobs_pending_total", "Pending jobs total")
   # reconciler 每輪更新
   JOBS_PENDING.set(await session.scalar(
       select(func.count(Job.id)).where(Job.status == JobStatus.PENDING)
   ))
   ```

5. `docs/runbooks/troubleshooting.md` 新增 §node-memory-pressure 說明。

**Acceptance**：

- 模擬 Memory pressure（kubectl drain or stress-ng） → LoldayNodeMemoryPressure alert 在 Discord 出現
- POST /jobs 第 11 個（同 user 已 10 open）→ 429 with "open jobs limit"

### Phase 5 — `Job.active_deadline_seconds` per-job override

**目標**：解 user 痛點「6h train 不夠」場景。

**改動**：

1. **新 alembic migration**：

   ```python
   op.add_column('job', sa.Column('active_deadline_seconds', sa.Integer(), nullable=True))
   ```

2. `backend/app/models/job.py::Job` 加欄位 `active_deadline_seconds: Mapped[int | None]`。

3. `backend/app/schemas/jobs.py::JobCreate` 加 `active_deadline_seconds: int | None = None`，validator：
   - admin role：上限 `JOB_ACTIVE_DEADLINE_TRAIN_MAX_SECONDS`（預設 86400 = 24h）
   - 非 admin：受 `JOB_ACTIVE_DEADLINE_TRAIN_SECONDS`（預設 21600 = 6h）限制

4. `backend/app/services/job_spec.py::_active_deadline` 改為：

   ```python
   def _active_deadline(job_type, override):
       return override or {  # type-based default
           JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
           JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
           JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
       }[job_type]
   ```

5. `frontend` 表單加 optional 欄位（admin 才顯示「>=24h」option）。

**Acceptance**：

- admin POST `active_deadline_seconds: 86400` → vcjob `activeDeadlineSeconds: 86400`
- 非 admin 嘗試 > 21600 → 422 validation error

## 8. Numbers (concrete values for server30)

集中表，方便交叉驗證：

| 維度                                        | 改前      | 改後            |
| ------------------------------------------- | --------- | --------------- |
| Node Allocatable CPU                        | 12        | 10              |
| Node Allocatable memory                     | 62 GB     | 55 GB           |
| Node Allocatable disk                       | 466 GB    | 446 GB          |
| Node Allocatable GPU                        | 2         | 2 (no change)   |
| `lolday-jobs` ResourceQuota requests.memory | n/a       | 30 GB           |
| `lolday-jobs` ResourceQuota limits.memory   | n/a       | 50 GB           |
| `lolday-jobs` ResourceQuota requests.gpu    | n/a       | 2               |
| `lolday-jobs` ResourceQuota count/pods      | n/a       | 16              |
| `lolday-jobs` LimitRange max memory         | n/a       | 16 GB           |
| `lolday-jobs` LimitRange default memory     | n/a       | 4 GB (req 1 GB) |
| `lolday` infra ResourceQuota limits.memory  | n/a       | 40 GB           |
| Volcano lolday-training queue capability    | unlimited | gpu=2, mem=30Gi |
| Volcano per-user queue capability gpu       | n/a       | 1               |
| `JOB_PER_USER_CONCURRENCY`                  | 2         | 2 (unchanged)   |
| `JOB_PER_USER_OPEN_LIMIT` (new)             | n/a       | 10              |
| `eviction-hard memory.available`            | unset     | < 1Gi           |
| `eviction-soft memory.available`            | unset     | < 2Gi grace 2m  |
| `eviction-hard nodefs.available`            | < 5%      | < 10%           |
| `eviction-hard imagefs.available`           | < 5%      | < 10%           |
| `kube-reserved memory`                      | 0         | 2 GB            |
| `system-reserved memory`                    | 0         | 4 GB            |

## 9. Migration path

- **Phase 0**：在 server30 操作員手動 patch K3s systemd unit。**全程要 SSH safety dry-run**（CLAUDE.md hard rule）。預期 `systemctl restart k3s` 觸發 ~30 秒 control plane downtime；workload pods 不受影響（K3s server restart 不重啟 worker container runtime）。
- **Phase 1**：透過 `bash scripts/deploy.sh` 跑（helm upgrade）。新建 ns + ResourceQuota + LimitRange 全是 idempotent。**JOB_NAMESPACE env 從 lolday → lolday-jobs 後，新 vcjob 會落到新 ns；舊 ns 內 finished vcjob 因 `ttlSecondsAfterFinished=604800` (7d) 自然清理**。確認 zero-downtime：跑 phase1 之前先 `kubectl get vcjobs -n lolday | grep -v Completed` 應為空。
- **Phase 2**：deploy.sh 同上。Volcano scheduler config 改變需要 `kubectl rollout restart deploy -n volcano-system volcano-scheduler`，~10 秒中斷新 vcjob 排程。已執行 vcjob 不受影響。
- **Phase 3**：alembic migration 由 `templates/alembic-upgrade-hook.yaml` (helm pre-upgrade) 自動跑。`ALTER TYPE … ADD VALUE` 是 transactional 安全操作。downgrade 路徑使用 type swap pattern（postgres `DROP VALUE` 不支援）。
- **Phase 4**：deploy.sh，純配置與 router 行為。
- **Phase 5**：alembic + helm。

## 10. Testing strategy

每 phase 必含：

1. **Pre-flight verification** — script 印出當前狀態（Allocatable, Quota usage, Queue list）→ operator 確認。
2. **Apply** — 觸發改動（systemd / helm upgrade / deploy.sh）。
3. **Post-apply verification** — script 重新印狀態 + 跑 acceptance test。
4. **Negative test** — 故意違反新限制，確認被擋。
5. **Rollback drill**（前 3 phase 必做） — 在 dev 模擬 rollback、確認回復原狀。

backend 改動配套 pytest unit test（`backend/tests/services/test_k8s_user_queue.py`、`backend/tests/services/test_job_spec_gpu1.py` 等）。

`tests/phase7/` 風格的 shell-based smoke 加：

- `tests/2026-05-05-resource-quotas-smoke.sh` — 確認 ResourceQuota 物件存在、值正確
- `tests/2026-05-05-volcano-fair-share-smoke.sh` — 跑 2 個 user 模擬 job，確認都進 running

## 11. Rollback strategy

| Phase | Rollback 手法                                                                                                        | 風險                     |
| ----- | -------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| 0     | 編輯 systemd unit 移 `--kubelet-arg=...` + `systemctl restart k3s`                                                   | 與正向同等               |
| 1     | helm rollback + `kubectl delete resourcequota -n lolday-jobs`；舊 ns 內任何殘留 vcjob 由 backend reconciler 自然清理 | 低                       |
| 2     | helm rollback；per-user queue (`lolday-u-*`) 由 batch script 清除（cluster-scoped 物件）                             | 中（user queue cleanup） |
| 3     | alembic downgrade（type swap，需 brief downtime）；frontend 重 deploy                                                | 中（DB type swap）       |
| 4     | helm rollback；backend env 移除 `JOB_PER_USER_OPEN_LIMIT` 即回 default                                               | 低                       |
| 5     | alembic downgrade（drop column）+ helm rollback                                                                      | 低                       |

## 12. Why not (alternatives considered & rejected)

### 12.1 NVIDIA k8s-device-plugin time-slicing

替代設計：把每張 GPU 暴露為 `replicas=4` 個虛擬 GPU，4 pod 共享 1 GPU。

**拒絕理由**：

- VRAM 是共享的，不是切割的。4 個 detector pod 各 alloc 4GB → 16GB > 11GB → 全部 CUDA OOM。
- **直接放大 user 擔憂的 OOM 風險**。
- 沒有強制 batch_size 收斂的 K8s primitive。
- 適用於只跑 inference（VRAM 用量已知小）的場景，不適合 training。

### 12.2 NVIDIA MPS (Multi-Process Service)

替代設計：在 host 啟用 MPS daemon，多 process 共用 GPU。

**拒絕理由**：

- 同樣無 VRAM 隔離。
- MPS daemon 是 SPOF — daemon 死整張 GPU 不可用。
- 對 lolday 規模沒有實質好處。

### 12.3 NVIDIA MIG

**拒絕理由**：RTX 2080 Ti 不支援。MIG 需要 A100/H100/A30 硬體。沒有路徑可採。

### 12.4 HAMi (Heterogeneous AI Manager)

替代設計：軟切 GPU，提供 K8s-native VRAM cap，是當前 OSS 最完整的「software-defined VRAM partition」。

**拒絕理由**：4Paradigm（中國第四範式）+ Tencent 主導開發，違反 `CLAUDE.md` 「Avoid China-origin software」hard rule。Trade-off 顯式記錄：放棄 1 GPU 多 pod 並行的彈性，換實驗室合規與供應鏈安全。

### 12.5 Apache YuniKorn / Kueue (替換 Volcano)

**拒絕理由**：Volcano 已部署、運作中，per-user queue + capability + drf 是 Volcano 自家 mainstream feature。換 scheduler 工程量遠大於補設定，且 Kueue 的 ClusterQueue + LocalQueue 模型不比 Volcano 多 queue + drf 簡潔。

### 12.6 PriorityClass + Volcano preempt plugin（短任務搶佔）

**deferred 而非 rejected**。技術上可行，但：

- Train job 中途被 preempt → 丟訓練 progress（model state in VRAM 沒 checkpoint）。
- 要先在 maldet 層支援 checkpoint resume 才能安全啟用。
- 後續 phase 再評估，當前不在 spec scope。

## 13. Open questions

| ID   | 問題                                                                                        | 決策路徑                                                                                                                |
| ---- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| OQ-1 | per-user queue capability `nvidia.com/gpu` 設 1 還是 2？                                    | 建議設 1（嚴格 fair-share），GPU2 留給 admin role；Phase 2 落地前在 dev 跑 Volcano docs 確認 capability 是 sum 還是 max |
| OQ-2 | swap 8GB 是否關閉？                                                                         | 不關（保留 fallback）。`failSwapOn=false` 已是 K3s 預設。Prometheus 監測 swap usage > 1GB 時 alert（後續 phase）        |
| OQ-3 | `lolday` infra ResourceQuota limits.memory=40Gi 是否會擠到 prometheus（kps 自帶大 limit）？ | Phase 1 deploy 前 dry-run `helm template` 看 kps 子 chart 各 deployment 的 limit，加總後微調                            |
| OQ-4 | per-user queue 何時刪？                                                                     | 後續 phase。當前 user 軟刪保留 queue（無資源消耗）；硬刪走 admin script 連動清                                          |
| OQ-5 | dev / staging 環境是否要做？                                                                | server30 是唯一環境。dev/staging 無對應，phase 全在 server30 直接做                                                     |
| OQ-6 | tests/phase7/ 風格 smoke 是否要加進 pre-commit / CI？                                       | 不進 — 它們需要 K8s 集群連線。維持手動執行慣例                                                                          |

---

**Spec 完。Phase 0 plan 另存於** `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase0-kubelet-args.md`。
