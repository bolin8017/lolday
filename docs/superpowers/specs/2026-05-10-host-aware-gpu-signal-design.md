# Host-aware GPU signal — Design Specification

> **建立於 2026-05-10。** 本 spec 銜接 `2026-05-05-gpu-fifo-anti-starvation-design.md`（Phase 6 backend FIFO scheduler）。Phase 6 解決了 lolday job 之間的 leapfrog 問題，但**沒解決「非-lolday 用途使用 GPU 時 lolday FIFO 仍會 over-allocate」這個 root cause**。本 spec 把 scheduler 與 UI 的「real free GPU」signal 接到 DCGM exporter，作為 single source of truth。

## 1. Overview

server30 是 ISLab shared lab server。它**不只跑 lolday 平台**——其他 ISLab 成員可能：

1. SSH 進機器，直接用 `python train.py` 在 host 上跑 GPU 訓練（K8s 完全看不到）
2. 在其他 K8s namespace 跑 GPU pod（lolday backend 目前只看 `lolday-jobs` ns）
3. 用 Docker 在 K8s 之外跑 GPU container

Phase 6 backend FIFO 的 `compute_cluster_free_gpu()` 在這三個場景下都會**錯誤地以為 GPU 空閒**，把新的 lolday vcjob dispatch 上去，造成：

- CUDA OOM（lolday job 跟非-lolday process 搶 VRAM）
- 訓練速度劣化（GPU compute time-share contention）
- vcjob 卡 ContainerCreating（K8s nvidia device plugin 視為 free，但 NVML 該 GPU 已被外部佔用）

K8s nvidia device plugin **依設計只 track K8s allocation**，host-level 用 GPU 的 process 它看不到——這是 K8s + non-K8s 混用 GPU 的固有 design gap，不是某個版本的 bug。Mainstream batch scheduler（Slurm、AWS Batch）解這個問題的方式都是**把 host metric 當 ground truth**。

本 spec 的解法：以已部署的 DCGM exporter（gpu-operator 預設啟用、帶 `--kubernetes` flag）為 host-level GPU signal source，新模組 `backend/app/services/gpu_signal.py` 把 DCGM metric 與 K8s allocation 結合計算「真實可用 GPU 數」，**讓 Phase 6 FIFO scheduler 與 `/cluster/gpu-status` UI 都讀同一份 signal**。

> **這份 spec 主要回答**：怎麼在 K8s + non-K8s 混用 GPU 的 ISLab shared server 場景下，讓 lolday 的 scheduler 與 UI 對 GPU 真實使用狀態有 root-cause 正確的判定，避免 over-allocate 造成 CUDA OOM。

## 2. Authorization

User 在 brainstorming 階段（2026-05-10）明確授權：

- **Breaking change OK**：不需考慮向後相容性。`/cluster/gpu-status` 既有 response schema (`{total, in_use, idle}`) 直接擴展，前端 caller 一併更新
- **以根本解決問題為原則**：不接受「DCGM 看不到就先當 free」這種 workaround
- **基於主流實踐**：採 DCGM + Prometheus query 的 NVIDIA 官方推薦模式（[gpu-operator DCGM Exporter doc](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dcgm-exporter.html)），不自寫 NVML / pynvml binding

User 同時偏好**保守 threshold**：DCGM `util > 5%` OR `VRAM > 500MB` 即視為 in-use（容忍偽陽性、絕不偽陰性）。

`scripts/setup-k3s.sh` 不變。chart 大致不變（DCGM exporter 已隨 gpu-operator 安裝，不需改設定）。

## 3. Scope

### 3.1 In scope

1. **新增 `backend/app/services/gpu_signal.py`** — 封裝 Prom 查詢 + DCGM signal 解讀的單一模組
2. **改寫 `cluster_status.py:get_gpu_allocation()`** — 改成讀 `gpu_signal`，UI 拿到的是 host-aware free GPU
3. **改寫 `reconciler/fifo_scheduler.py:compute_cluster_free_gpu()`** — 改成讀 `gpu_signal`，dispatch 決策用 host-aware 數字
4. **`/cluster/gpu-status` API schema 擴展** — 新增 `per_gpu`、`in_use_by_external`、`fail_safe_active` 欄位
5. **Frontend cluster banner / job submit form** — 顯示 per-GPU 細節 + external use / fail-safe 警示
6. **新增 backend `Settings` env vars** — Prom URL、threshold、cache TTL、fail-safe behavior
7. **Unit + integration + live smoke test**
8. **Documentation 更新**：`docs/architecture.md` 加 host-aware GPU signal 條目；`docs/runbooks/troubleshooting.md` 加 fail-safe 與 external-use 排查

### 3.2 Out of scope

- **lolday 「zombie job」偵測**：lolday vcjob allocated GPU 但實際 util=0 的偵測 / kill。沒有真實事故觸發；訓練早期 data-loader 階段 util 自然偏低，誤殺風險高。Phase 7+ 視 production observation 決定
- **跨機器 GPU 排程**：lolday 是 single-node K3s，本 spec 假設 server30 上的 GPU 是唯一資源池
- **GPU 使用稽核**（log 誰用了 GPU）：跨出 lolday 責任範圍。Host-level tool（`nvidia-smi --query-compute-apps`、`nvtop`、cgroup process accounting）更合適
- **NVIDIA MIG / MPS**：Phase 0 spec 已 reject（2080 Ti 不支援 MIG；MPS 增加複雜度且不解決 host-level visibility）
- **動態調整 K8s ResourceQuota**：以 quota 強制 K8s scheduler 不分配特定 GPU 是另一種解法，但 admission race（已被 Phase 6a 修掉的問題）會復現。本 spec 採 backend 層攔截方案
- **Volcano scheduler plugin override**：Phase 6 已論證 chart-only Volcano 配置走不通；本 spec 不重新嘗試
- **Discord / alerting 設計**：是獨立議題，會在另一份 spec（`2026-05-1X-alerting-redesign-design.md`）處理。本 spec 落地後該 spec 會 reference 進來重新定義 GPU temp / VRAM 的 alert 語意

### 3.3 Authorization for breaking changes (recap)

§ 2 已列。重申不影響：

- maldet contract（detector image / vcjob spec / mlflow integration 都不變）
- Job submission API path、payload schema（job 仍照常入 `queued_backend`，只是 dispatch 時機受 host-aware signal 影響）
- Phase 6 FIFO scheduler 的 strict-FIFO 語意（priority 排序不變、HEAD-of-line 不變）

## 4. Background — 為什麼 K8s nvidia device plugin 看不到 host process

### 4.1 K8s + non-K8s GPU 共用是 design gap

NVIDIA 的 K8s device plugin 假設**整顆 GPU 由 K8s 獨佔**，它不查 `nvidia-smi --query-compute-apps`，不知 host 上有沒有 PID。這在 dedicated K8s GPU cluster 是合理的——但在 lab shared server 上會 over-allocate。

[NVIDIA's gpu-operator FAQ](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/index.html#frequently-asked-questions) 也提到：

> _"The GPU Operator does not coordinate with workloads outside of Kubernetes. If you run GPU workloads outside of Kubernetes on the same host, you must manage isolation yourself."_

### 4.2 為什麼 DCGM 是正確的 source

DCGM 透過 NVML 直接讀 NVIDIA driver 的 telemetry，**不論 GPU 用戶是 K8s pod 還是 host process 都看得到**。它是 NVIDIA 官方為 datacenter monitoring 設計的工具。lolday 已部署 DCGM exporter（隨 gpu-operator）：

- `DCGM_FI_DEV_GPU_UTIL` — compute utilization (%)
- `DCGM_FI_DEV_FB_USED` — VRAM used (bytes)
- `DCGM_FI_DEV_POWER_USAGE` — power draw (W)

DCGM exporter `--kubernetes` flag（gpu-operator 預設啟用）讓每筆 sample 帶 `exported_pod` / `exported_namespace` label——若 GPU 被某個 K8s pod 持有，label 會填那個 pod；若是 host process，label 為空。**這個 label 提供了區分 K8s 與 external 的 ground truth。**

### 4.3 主流參考

| 系統                  | 解 K8s + non-K8s 混用 GPU 的方式                                  |
| --------------------- | ----------------------------------------------------------------- |
| AWS Batch + EC2       | EC2 spot 是 dedicated；不混用                                     |
| Slurm                 | cgroup containment + `gres.conf`，禁 non-Slurm 用 GPU             |
| HPC 學術 cluster      | reservation system + 政策禁止 SSH 直接用                          |
| Kueue + DCGM          | Kueue ResourceFlavor 可帶 host-aware label，但本身仍依賴 K8s view |
| **Lolday（本 spec）** | **DCGM 當 host-level ground truth，backend 層攔截 dispatch**      |

ISLab 因為人少、政策上無法禁止 SSH 直接用 GPU，所以走「scheduler 自己感知並讓步」的路線。這跟 [Yelp clusterman](https://github.com/Yelp/clusterman) 的設計哲學一致——以真實 metric 而非 K8s 宣告為 capacity 決策依據。

## 5. Architecture decisions

### 5.1 為什麼新模組 `gpu_signal.py`，不擴充 `cluster_status.py`

- `cluster_status.py` 的既有責任是「K8s pod-based 計算」，加 DCGM signal 是不同 concern；混在一起會讓 testing 與 future swap 都困難
- 新模組 mock Prom 即可單元測試，不需要 mock K8s API
- 未來若要替換 source（例如改用 NVIDIA dcgm SDK 或新版 metric），只動一個檔案
- 對應 backend 既有的 `services/` 結構慣例（每個檔案專一外部整合）

### 5.2 為什麼 Prom query 而不是 DCGM exporter 直接 scrape

詳見 brainstorm Approach 比較。簡述：

- Prom 是既有投資（kps 已部署、scrape 已配置、ServiceMonitor 已生效）
- Prom 提供歷史查詢能力（未來可加 "持續 X 分鐘高於閾值" 邏輯）
- DCGM exporter pod restart 期間 Prom 還會 serve 最後一筆 sample，buffer 比 backend 直查穩
- httpx 對 Prom HTTP API 的 query 是純 read，不需 mTLS / 額外權限

### 5.3 為什麼 conservative threshold（util > 5%、VRAM > 500MB）

主流參考：

- CUDA context init 通常 1–3% util、200–400MB VRAM
- > 5% 才是「真的在跑東西」（不是 idle process 殘留）
- > 500MB VRAM 排除 stale CUDA context（process 還沒清乾淨但實際空閒）

User 明示要保守（容忍偽陽性，絕不偽陰性）。Threshold 由 env var 暴露，operator 可調。Phase 7+ 視 production observation 決定是否要加「持續 X 秒以上」的時間窗。

### 5.4 為什麼 fail-safe = 「Prom 看不到時不 dispatch」

- Prom 拿不到 → 我們不知道 host-level GPU 狀態 → 對 dispatch 要保守（**fail-safe is fail-closed for scheduler**）
- 若 fail-open（Prom 看不到時退回到 K8s-only），Prom 故障期就是 over-allocate 風險窗
- 提供 `GPU_SIGNAL_FAIL_SAFE_BLOCK=false` escape hatch：Prom 整合萬一壞掉、admin 想暫退到 Phase 6 既有行為時可用
- 這 align Slurm 的 default：node 失聯時不 schedule 新 job

### 5.5 為什麼用 `exported_pod` label 而不是 cross-reference K8s pod GPU UUID

- DCGM exporter `--kubernetes` flag 已自動把 pod info 寫進 metric label——這是 NVIDIA 官方推薦
- Cross-reference K8s pod 的 GPU UUID 需要從 pod env var (`NVIDIA_VISIBLE_DEVICES`) 反查，K8s API 不直接暴露
- 借用 DCGM 已做好的 plumbing 是 mainstream 與 root-cause 的（不重造輪子）

### 5.6 為什麼 ANY external use 就 block all dispatch（不做 per-GPU pinning）

理論上理想：**GPU 0 被外部用 → 只 dispatch 到 GPU 1**。但這需要 lolday 能控制 K8s scheduler **不要把 vcjob 排到 GPU 0**。

K8s nvidia device plugin 沒提供 dynamic pin / unpin API。要做到 per-GPU pinning 需要：

- (a) Fork / 換掉 device plugin（脫離 mainstream）
- (b) 動態改 ResourceQuota（admission race 會復現，這是 Phase 6a 已經修掉的 bug）
- (c) 用 NVIDIA's CDI v0.14+ 動態配置（複雜，且 ISLab 的 gpu-operator 版本未必支援）

ISLab 規模 = 2 GPU。**「ANY external → block all」**是保守但**正確且簡單**的設計。Trade-off：當 1 個 GPU 外部用、1 個 GPU 空閒時，lolday 會等而非用空閒那個——這在 2 GPU server 是少量浪費，可接受。

Phase 7+ 若 ISLab 升級到多 GPU 機器或多機，再評估 per-GPU pinning。

## 6. Detailed design

### 6.1 New module: `backend/app/services/gpu_signal.py`

#### 6.1.1 Public API

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class GPUStatus:
    gpu_id: int
    in_use_by_k8s: bool
    in_use_by_external: bool
    util_percent: float
    vram_used_mb: int

@dataclass(frozen=True)
class GPUState:
    physical_total: int
    per_gpu: list[GPUStatus]
    free_count: int
    in_use_by_lolday_count: int
    in_use_by_external_count: int
    fail_safe_active: bool
    fail_safe_reason: str | None

def compute_real_gpu_state() -> GPUState:
    """Single source of truth for host-aware GPU availability.

    Returns a snapshot reflecting both K8s allocations and host-level
    GPU activity. Cached for `GPU_SIGNAL_CACHE_TTL_S` (default 10s).
    Fail-safe: if Prometheus is unreachable, returns
    GPUState(fail_safe_active=True, free_count=0, ...).
    """
```

#### 6.1.2 Prometheus queries

`gpu_signal` 用 httpx 對 `${GPU_SIGNAL_PROMETHEUS_URL}/api/v1/query` 發兩個 instant query：

**Q1 — per-GPU 是否「有任何 GPU 活動」**：

```promql
(DCGM_FI_DEV_GPU_UTIL > ${GPU_SIGNAL_UTIL_THRESHOLD_PERCENT})
  or
(DCGM_FI_DEV_FB_USED > ${GPU_SIGNAL_VRAM_THRESHOLD_MB} * 1024 * 1024)
```

**Q2 — per-GPU 是否被 lolday-jobs ns pod 持有**：

```promql
DCGM_FI_DEV_GPU_UTIL{exported_namespace="lolday-jobs"}
```

兩個 query 的結果都 group by `gpu` label。Python 端後處理：

```python
def _classify_gpus(q1_result, q2_result, util_q, vram_q) -> list[GPUStatus]:
    busy_ids = {sample.gpu for sample in q1_result}
    k8s_ids = {sample.gpu for sample in q2_result}
    statuses = []
    for gpu_id in range(settings.CLUSTER_PHYSICAL_GPU_COUNT):
        is_active = gpu_id in busy_ids
        is_k8s = gpu_id in k8s_ids
        statuses.append(GPUStatus(
            gpu_id=gpu_id,
            in_use_by_k8s=is_k8s,
            in_use_by_external=is_active and not is_k8s,
            util_percent=util_q.get(gpu_id, 0.0),
            vram_used_mb=int(vram_q.get(gpu_id, 0)),
        ))
    return statuses
```

#### 6.1.3 Caching

`@cached(TTLCache(maxsize=1, ttl=GPU_SIGNAL_CACHE_TTL_S))` 與既有 `cluster_status._gpu_cache` 一致。`/cluster/gpu-status` 在 dashboard polling cycle（每 15s）下會 hit cache，不會把 Prom 打爆。

#### 6.1.4 Thread / async safety

Backend 既有 reconciler thread + async API handlers 都會呼叫 `compute_real_gpu_state()`。httpx 的 sync client 在 `asyncio.to_thread()` wrap 下使用，與 Phase 6 `compute_cluster_free_gpu()` 對 K8s API 的處理方式一致。

### 6.2 修改 `cluster_status.py:get_gpu_allocation()`

```python
@cached(_gpu_cache)
def get_gpu_allocation() -> dict:
    state = gpu_signal.compute_real_gpu_state()
    return {
        "total": state.physical_total,
        "free_count": state.free_count,
        "in_use_by_lolday": state.in_use_by_lolday_count,
        "in_use_by_external": state.in_use_by_external_count,
        "fail_safe_active": state.fail_safe_active,
        "fail_safe_reason": state.fail_safe_reason,
        "per_gpu": [
            {
                "gpu_id": s.gpu_id,
                "state": (
                    "lolday" if s.in_use_by_k8s
                    else "external" if s.in_use_by_external
                    else "free"
                ),
                "util_percent": s.util_percent,
                "vram_used_mb": s.vram_used_mb,
            }
            for s in state.per_gpu
        ],
    }
```

舊 schema (`{total, in_use, idle}`) 直接被新 schema 取代——不向後相容。

### 6.3 修改 `reconciler/fifo_scheduler.py:compute_cluster_free_gpu()`

```python
def compute_cluster_free_gpu() -> int:
    state = gpu_signal.compute_real_gpu_state()

    if state.fail_safe_active:
        if settings.GPU_SIGNAL_FAIL_SAFE_BLOCK:
            return 0  # default: fail-closed
        # escape hatch: 退回 Phase 6 既有 K8s-only 邏輯
        return _compute_free_gpu_k8s_only()

    # Phase 6 既有：對 backend 已 submit 但 vcjob controller 還沒 spawn pod 的 job 預扣
    pending_submitted = _count_backend_submitted_not_visible_in_pods()

    return max(0, state.free_count - pending_submitted)


def _compute_free_gpu_k8s_only() -> int:
    """Phase 6 既有計算（不查 DCGM）。escape-hatch path only。"""
    physical = settings.CLUSTER_PHYSICAL_GPU_COUNT
    running = _sum_k8s_running_pod_gpus()
    submitted = _count_backend_submitted_not_visible_in_pods()
    return max(0, physical - running - submitted)
```

Phase 6 的 strict FIFO loop 不變（HEAD 不 fit 就 break）。`gpu_signal` 拿到 `free_count=0` 自然導致 loop break，新 job 留 `queued_backend`。Escape hatch 路徑（`GPU_SIGNAL_FAIL_SAFE_BLOCK=false`）保留 Phase 6 既有計算邏輯，當 Prom 整合臨時故障時可暫退。

### 6.4 `/cluster/gpu-status` API 變更

**Path / method**：不變（`GET /cluster/gpu-status`）。

**Response schema**：

```json
{
  "total": 2,
  "free_count": 1,
  "in_use_by_lolday": 1,
  "in_use_by_external": 0,
  "fail_safe_active": false,
  "fail_safe_reason": null,
  "per_gpu": [
    {
      "gpu_id": 0,
      "state": "lolday",
      "util_percent": 87.5,
      "vram_used_mb": 9240
    },
    {
      "gpu_id": 1,
      "state": "free",
      "util_percent": 0.0,
      "vram_used_mb": 12
    }
  ]
}
```

`state` 三個值：`"lolday"` / `"external"` / `"free"`。

### 6.5 Frontend changes

#### 6.5.1 Dashboard cluster banner

Per-GPU 狀態用 colored chip 顯示：

- 🔵 lolday — Phase 6 backend FIFO dispatch 上去的 vcjob
- 🟠 external — DCGM 看到活動但無 K8s pod 標籤
- ⚪ free
- 🚫 fail-safe — Prom unreachable

Banner 文案範例見 brainstorm Section 3。Banner 是 dashboard 頂端 plus job submit form 頂端共享 component。

#### 6.5.2 Submit job form

Submit button **不 disable**（job 永遠可以入 queue）。Banner 上方提示 user 預期 dispatch 時間：

- 全 free → "Your job will start immediately"
- 部分 free / lolday 用中 → "Your job will be queued behind ~N other job(s)"
- external → "Cluster is paused due to external GPU activity. Your job will queue."
- fail-safe → "GPU status unavailable. Your job will be queued; dispatch resumes when signal is restored."

### 6.6 Configuration

新增到 `backend/app/config.py:Settings`：

| Var                                 | Type    | Default                                     | 說明                                           |
| ----------------------------------- | ------- | ------------------------------------------- | ---------------------------------------------- |
| `GPU_SIGNAL_PROMETHEUS_URL`         | `str`   | `http://kps-prometheus.monitoring.svc:9090` | Prom endpoint。in-cluster service DNS。        |
| `GPU_SIGNAL_QUERY_TIMEOUT_S`        | `float` | `5.0`                                       | httpx timeout                                  |
| `GPU_SIGNAL_CACHE_TTL_S`            | `int`   | `10`                                        | TTL cache。與 `cluster_status._gpu_cache` 一致 |
| `GPU_SIGNAL_UTIL_THRESHOLD_PERCENT` | `float` | `5.0`                                       | "in use" util 門檻（%）                        |
| `GPU_SIGNAL_VRAM_THRESHOLD_MB`      | `int`   | `500`                                       | "in use" VRAM 門檻（MB）                       |
| `GPU_SIGNAL_FAIL_SAFE_BLOCK`        | `bool`  | `true`                                      | fail-safe 是否 block dispatch                  |

`CLUSTER_PHYSICAL_GPU_COUNT` env var 沿用 Phase 6（不重新定義）。

### 6.7 Documentation 更新

- `docs/architecture.md` §10：新增「Host-aware GPU signal」條目，引用本 spec、解釋與 Phase 6 的關係
- `docs/runbooks/troubleshooting.md`：新增兩個 SOP
  1. **fail-safe banner 出現怎麼辦** — 檢查 kps-prometheus pod、ServiceMonitor、httpx connectivity
  2. **external use detected 但實際沒人在用** — 檢查 DCGM exporter `--kubernetes` flag、host-level zombie process
- `.claude/rules/backend.md`：新模組 `gpu_signal.py` 加進 services 清單
- `CLAUDE.md`「How to navigate this codebase」：加一條 host-aware GPU signal 的 reference

## 7. Failure modes

| 模式                                                | 觸發                                 | 影響                                            | 處理                                                                                                                              |
| --------------------------------------------------- | ------------------------------------ | ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Prometheus unreachable                              | KPS pod restart, network blip        | `fail_safe_active=True`, `free_count=0`         | FIFO 暫停 dispatch；UI 顯示 fail-safe banner；Prom 恢復後自動恢復（next reconcile cycle）                                         |
| Prometheus 回傳空 / malformed JSON                  | DCGM exporter 重啟、metric staleness | 同 fail-safe（保守）                            | 同                                                                                                                                |
| DCGM exporter `--kubernetes` flag 沒打開            | 操作員誤改 chart                     | `exported_pod` label 永遠空 → 全部被當 external | 100% 視為 external、`free_count=0`；UI 顯示 fail-safe banner with reason "K8s pod info missing"；troubleshooting runbook 提供修法 |
| `CLUSTER_PHYSICAL_GPU_COUNT` 與實際 GPU 數不符      | 換硬體未更新 env var                 | per_gpu list 長度與 DCGM 不一致                 | gpu_signal 跳過超出 PHYSICAL_GPU_COUNT 的 sample；BACKEND_ERRORS 加 stage="gpu_signal_count_mismatch"；warning 進 log             |
| Prom query latency > timeout                        | Prom 被 noisy neighbor 打爆          | httpx 5s timeout → fail-safe                    | 同 Prom unreachable                                                                                                               |
| Backend pod 重啟                                    | upgrade / OOM                        | startup 後重新 query Prom                       | 第一次 reconcile cycle 嘗試；失敗 fail-safe；Prom 通常會比 backend 早 ready，cold-start 延遲 < 30s                                |
| `gpu_signal` cache 與 K8s actual state 短時間不一致 | new pod scheduled in last 10s        | over-count `free_count` 1 個 cycle              | Phase 6 既有的 `pending_submitted` 預扣機制處理；不會 over-allocate                                                               |
| 同一個 GPU 被 lolday 與 host 同時用（race）         | 罕見 — 操作員 bypass                 | DCGM `exported_pod` 有值，視為 K8s only         | 不偵測；不在本 spec 處理範圍。Phase 7+ 視 observation                                                                             |

## 8. Testing strategy

### 8.1 Unit tests

`backend/tests/services/test_gpu_signal.py`，mock httpx 對 Prom 的 response：

| 場景                                  | Q1 result                | Q2 result | 期望 free_count                            |
| ------------------------------------- | ------------------------ | --------- | ------------------------------------------ |
| 全空                                  | []                       | []        | 2                                          |
| lolday on GPU0                        | [{gpu=0}]                | [{gpu=0}] | 1                                          |
| external on GPU0                      | [{gpu=0}]                | []        | 1                                          |
| lolday on GPU0 + external on GPU1     | [{gpu=0}, {gpu=1}]       | [{gpu=0}] | 0                                          |
| Prom timeout                          | (httpx.TimeoutException) | —         | 0, fail_safe_active=True                   |
| Prom returns malformed JSON           | (json.JSONDecodeError)   | —         | 0, fail_safe_active=True                   |
| `--kubernetes` flag missing → Q2 全空 | [{gpu=0}]                | []        | 0（GPU0 視為 external，自動進入保守 mode） |

### 8.2 Integration tests

`backend/tests/services/test_cluster_status_integration.py`（aiosqlite + mock Prom + mock K8s API）：

- `GET /cluster/gpu-status` 回傳新 schema、fields 完整
- FIFO scheduler 在 fail-safe 時不 dispatch（DB 留 `queued_backend`）
- FIFO scheduler 在 external use detected 時不 dispatch
- Prom 恢復後 next reconcile cycle 自動 dispatch

### 8.3 Live smoke test

`tests/2026-05-10-host-aware-gpu-signal-smoke.sh`，在 deploy.sh 之後跑：

- **Test A** — cluster 全空：submit gpu=1 lolday job → 確認 dispatch 成功
- **Test B** — host 直接用 GPU：在 host 跑 `python -c "import torch; x=torch.zeros(int(1e9)).cuda(); time.sleep(120)"` → submit lolday job → 確認 lolday 沒 dispatch（保持 `queued_backend` ≥ 60s），UI banner 顯示 external
- **Test C** — kill host process：等下個 reconcile cycle (~30s) → 確認 lolday 自動 dispatch
- **Test D** — kps-prometheus 模擬故障：`kubectl -n monitoring scale --replicas=0 statefulset/kps-prometheus-prometheus` → 等 ~30s → 確認 fail-safe banner 顯示、lolday 不 dispatch；scale back → 等 ~30s → 確認自動恢復

### 8.4 Frontend testing

- **Vitest unit**：cluster banner render 7 個 state（free / lolday only / external only / mixed lolday+external / fail-safe / loading / count mismatch warning）
- **Playwright E2E**：mock backend 回 fail-safe + external，confirm UI 對應訊息出現；submit form 在 external mode 下仍可 submit（按鈕不 disable）

### 8.5 Manual operator validation

- 跑 Test B 時觀察 Discord（如 alerting redesign 已落地）/ logs，確認沒有 false alert
- Test C 之後驗證 lolday job 真的有跑（mlflow run 出現 + GPU util 上來）

## 9. Rollback

完整 rollback 是 single-step（pure backend code change，無 DB schema 改動）：

1. **Backend code rollback**：`helm rollback lolday <prev-revision>`。`gpu_signal` 模組沒了；`cluster_status` / `fifo_scheduler` 退回到 K8s-only 邏輯
2. **Frontend rollback**：同步 frontend 到舊版（cluster banner 退回到只顯示 `total / in_use / idle`）
3. **Settings env vars 殘留**：env vars 留著無害（沒 module 讀就忽略）

部分 rollback：保留 backend 改動 + 設 `GPU_SIGNAL_FAIL_SAFE_BLOCK=false` → 退回 Phase 6 既有的 K8s-only behavior（暫時繞過 host-aware）。這是 escape hatch，預期在 Prom 整合臨時故障、operator 想暫時恢復 dispatch 時用。

## 10. Open questions

1. **Prom query 是否要加「持續 X 秒以上」的時間窗？** 目前 instant query 視瞬時 GPU 活動為 in-use。若 user 反映 false positive 太頻繁（短暫 init 也被 block），Phase 7+ 改成 `max_over_time(... [60s])`
2. **DCGM exporter 重啟期間怎麼平滑？** Prom 通常 serve 最後一筆 sample 直到 metric staleness（5min）才 NaN。實際是否會造成 fail-safe？要在 live smoke 觀察
3. **Per-GPU pinning 何時做？** 等 ISLab 升級到多 GPU 機器或人數成長到 ANY-block 太浪費的程度才動。Phase 7+
4. **`exported_pod` label 在 K8s 1.28 / cgroupv2 是否有變？** 觀察 NVIDIA gpu-operator changelog；本 spec 假設目前 cluster 的 gpu-operator 行為不變
5. **是否要把 `gpu_signal` Prom query 的 latency 寫成 metric？** Phase 7+ alerting redesign 可能會用到
6. **多 user 同時 SSH 用 GPU 怎麼算？** DCGM 看到 GPU 1 處於 `in_use_by_external`（與單一 user 用無差別）；不細分。本 spec 不在乎是「誰」用，只在乎「有沒有人」用

## 11. References

### Mainstream practice

- NVIDIA — [GPU Operator DCGM Exporter](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dcgm-exporter.html)
- NVIDIA — [DCGM User Guide](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/)
- NVIDIA — [Kubernetes Device Plugin design assumptions](https://github.com/NVIDIA/k8s-device-plugin#caveats)
- Yelp — [clusterman: capacity decisions from real metrics, not declarations](https://github.com/Yelp/clusterman)
- Slurm — [gres.conf + cgroup containment](https://slurm.schedmd.com/gres.html)
- Prometheus — [HTTP API: /api/v1/query](https://prometheus.io/docs/prometheus/latest/querying/api/#instant-queries)

### Lolday 內部

- Phase 6 spec: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`（本 spec 的前置）
- Phase 0–5 GPU scheduling spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md`
- 既有 cluster_status: `backend/app/services/cluster_status.py`
- 既有 FIFO scheduler: `backend/app/reconciler/fifo_scheduler.py`
- DCGM ServiceMonitor: `charts/lolday/templates/monitoring/servicemonitor-dcgm.yaml`
- 既有 alert rules referencing DCGM: `charts/lolday/templates/monitoring/alertmanager-rules.yaml`（GPUTemperatureHigh、LoldayGPUVRAMHigh）— 將在後續 alerting-redesign spec 重新定義
