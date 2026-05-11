# MLflow 資料模型重設計 — Design Specification

> **建立於 2026-05-11。** 觸發點：使用者在閱讀 `https://lolday.connlabai.com/mlflow/#/experiments/28` 時詢問如何解讀資料，過程中暴露了「`start_time = 0`」這個明顯 bug，以及一連串「resource 寫到錯位置」的結構性問題。本 spec 跨 3 個 repo（lolday / maldet / detectors），把 MLflow 整合做一次徹底校正。

> **這份 spec 主要回答**：在 maldet 框架 + lolday 平台 + detector repos 的三層架構下，如何讓 MLflow 從一個「能進資料但解讀困難」的後備儲存，升級成「符合 MLflow 慣例、可被 Model Registry / Models / Dataset Lineage / System Metrics API 直接消費」的 first-class observability surface。

## 1. Overview

當前的 MLflow 整合不是「資料不足」，而是**「資料模型錯了 + 生命週期沒有閉環」**。從線上 experiment 28 (`bolin8017/elf-rf/v4.1.0`) 撈下的實際資料顯示：

1. **`info.start_time` 一律是 `0`** — Unix epoch，UI 上的 duration 全部錯。
2. **存在 orphan RUNNING runs** — `train-0eaa2f0f` 早就 terminate 但 MLflow 不知道。
3. **Confusion matrix 被字串化塞進 tag** — `maldet.confusion_matrix.matrix = "[[90, 0], [1, 77]]"`，是 Python `__repr__()` 不是 JSON。
4. **Per-class metrics 是嵌套 dict 字串化** — 同樣的編碼錯誤。
5. **Warning 被多次覆寫** — `maldet.warning.message` 是 tag，後一筆會吃掉前一筆，資料遺失。
6. **Model 沒走 MLflow Models spec** — `model/model.joblib` 是裸 pickle，沒 MLmodel YAML，沒 signature，沒 conda.yaml。
7. **沒有 dataset / git / image lineage** — 連 commit SHA、image digest、dataset hash 都沒記。
8. **完全沒有 system metrics** — GPU 利用率 / VRAM / CPU 一無所知。

問題的共同根源是：**maldet 的 `MlflowEventLogger.log_event()` 對所有 EventKind 一律用 `mlflow.set_tag(f"maldet.{kind}.{k}", str(v))`**。tag 本就是 K-V 字串標籤，硬塞結構化資料就是不對。lolday backend 又獨立踩了「raw REST 沒傳 start_time」與「orphan run 未閉環」兩個生命週期問題。

本 spec 的解法：

| 層                  | 改動                                                                                                                                 | Root-cause 對應 |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | --------------- |
| **maldet**          | `MlflowEventLogger` 改成 **kind-aware routing** — 結構化 payload 走 `log_dict`，多筆事件走 buffer + `log_text`，純標籤才走 `set_tag` | 1–5             |
| **maldet**          | Trainer `save()` / `load()` 改走 `mlflow.sklearn.save_model` / `mlflow.pytorch.save_model` 的 **MLflow Models flavor API**           | 6               |
| **maldet**          | 加上 `mlflow.log_input()` for dataset lineage                                                                                        | 7 (一半)        |
| **lolday backend**  | `create_run` 傳 `start_time`；`reconciler` 在 terminal transition 補 `update_run("FAILED")`                                          | 1, 2            |
| **lolday backend**  | 在 create_run 時注入 `mlflow.source.*` + `lolday.detector_image_digest` + `lolday.train_dataset_id` 等 tag                           | 7 (另一半)      |
| **lolday backend**  | Detector container 加 `MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING=true`；base image 帶 `psutil` + `pynvml`                                 | 8               |
| **lolday frontend** | Duration 欄改從 lolday `Job.active_at`/`completed_at` 計算（不再依賴 MLflow `info.start_time`）                                      | UI 信息正確性   |
| **detectors**       | bump `min_maldet = 2.2`、bump 自己版本、重建 image                                                                                   | upstream 升級   |

> **Breaking change 是被授權的**（見 §2）。不留 backward-compat shim、不留遷移 toggle、不分階段保留舊路徑。一次到位。

## 2. Authorization

使用者於 2026-05-11 brainstorming 階段明示授權：

- **「從根本解決問題」為最高原則** — 不接受「workaround」、「先 patch 一下」、「等下版再做」。Trade-off 必須在 spec 內明文化。
- **不需考慮向後相容性** — 既有 run（experiment 28 等）裡的字串化 tag、`start_time=0`、orphan RUNNING 都接受作為歷史殘骸（可選擇性 backfill；見 §7.4），但新版程式碼**不出**這些格式。
- **必須基於該領域主流且被驗證的實踐** — 設計決策的每一條都要對應到 MLflow / Kubeflow / W&B / sklearn / PyTorch Lightning 官方文件或社群共識。冷門解法要明文說明理由與 trade-off。
- **允許動 detector 基底模板** — `/home/bolin8017/Documents/repositories/maldet`、`elfrfdet`、`elfcnndet` 都可改。`UV_PUBLISH_TOKEN` 已在 `~/.zshrc`，可推 PyPI。
- **允許動 `DetectorVersion` schema** — 新增 `git_commit_sha`、`image_digest`、`maldet_version` 欄位 + Alembic migration。

## 3. Scope

### 3.1 In scope

**maldet 框架**（`/home/bolin8017/Documents/repositories/maldet`）

1. `src/maldet/events/mlflow_logger.py` — kind-aware routing 重寫
2. `src/maldet/trainers/sklearn_trainer.py` — `save/load` 走 `mlflow.sklearn.save_model/load_model`
3. `src/maldet/trainers/lightning_trainer.py` — `save/load` 走 `mlflow.pytorch.save_model/load_model`
4. `src/maldet/runner.py` — dataset lineage 注入 (`mlflow.log_input`)，extend `EventLogger` protocol with `log_model`
5. `src/maldet/protocols.py` — `EventLogger` protocol 加 `log_model`
6. `src/maldet/_version.py` — bump 至 `2.2.0`
7. `CHANGELOG.md` — 完整 migration note
8. PyPI publish 新版

**lolday 平台**（`/home/bolin8017/Documents/repositories/lolday`）

9. `backend/app/services/mlflow_client.py` — `create_run` 接受 `start_time_ms`、新增 `update_run` finalize、新增 `set_experiment_tag`
10. `backend/app/routers/jobs.py` — create_run call site 補 `start_time` + provenance tags
11. `backend/app/reconciler/jobs.py` — terminal transition 補 `_finalize_mlflow_run()`
12. `backend/app/services/job_spec.py` — detector container 加 system_metrics env vars
13. `backend/app/models/detector.py` (or wherever DetectorVersion lives) — 加 `git_commit_sha`、`image_digest`、`maldet_version` 欄位
14. `backend/migrations/versions/<new>_detectorversion_provenance.py` — Alembic migration
15. `backend/app/services/build.py` — build 完抓 digest + commit SHA 寫回 DB
16. `frontend/src/routes/_authed.runs.$expId.tsx` — duration 改用 lolday Job 時間
17. `frontend/src/api/queries/runs.ts` + `experiments_proxy.py` — enrich runs with `lolday_active_at` / `lolday_completed_at`
18. 對應 backend / frontend 測試
19. `charts/lolday/helpers/pytorch-cu12-base/Dockerfile` — 加 `psutil`、`pynvml`
20. `docs/architecture.md`、`docs/runbooks/troubleshooting.md`、`CLAUDE.md` 對應更新

**Detector 升級**（`elfrfdet`、`elfcnndet`）

21. `maldet.toml` — `compat.min_maldet = "2.2"`
22. `pyproject.toml` — `maldet>=2.2,<3` 依賴
23. `CHANGELOG.md` + 版本 bump（elfrfdet → 4.2.0、elfcnndet → 對應 minor）
24. Docker image 重建 + 推 Harbor

### 3.2 Out of scope

- **既有 run 的 backfill** — experiment 28 等的字串化 tag、`start_time=0`、orphan RUNNING run 不溯及更新；保留作為歷史殘骸。提供 **optional** 一次性掃描腳本（§7.4），讓 operator 自行決定要不要跑。
- **MLflow Model Registry 的 stage transition workflow 改動** — 維持現狀（None / Staging / Production / Archived 四段）。
- **MLflow autologging** — 不啟用 `mlflow.autolog()`。原因：autolog 跨 framework 行為差異大、會跟 maldet 的 typed event 流互搶 metric 命名空間。**主流大型平台（Databricks、Vertex AI Pipelines）對 autolog 都採選擇性啟用**，本平台維持手動 instrumentation 的明確性。
- **MLflow `mlflow.evaluate()` API 整合** — 該 API 假設 sklearn-like model，跟 maldet `Evaluator` 抽象重疊。下個 spec 再評估是否取代 `BinaryClassification`。
- **MLflow Serving** — 雖然 Models flavor enable 了 `mlflow models serve`，但 lolday 目前無 serving runtime。改用 `Predict` job 即可（這也是 ISLab use case）。
- **MLflow Tracking server 內部資料 migration / cleanup** — 不動 PostgreSQL / artifact store 的歷史資料。
- **Run lifecycle 改 lazy creation（A1-α）** — §5.1 評估過後否決。

### 3.3 已被排除的替代解法（為 trade-off 留紀錄）

- **A1-α：backend 不創 run，由 maldet 在 pod 內 `mlflow.start_run()`** — 否決原因：前端在 job submit 完成前無法跳轉 MLflow（沒 run_id），UX 倒退；MLflow run 命名與 tag 完全交給 maldet 也削弱平台對命名空間的控制。採 **A1-β**（backend 創 run + `start_time=submit_time`，UI duration 從 Job timestamps 取）。
- **不擴 EventLogger protocol，改在 runner 內 hardcode `mlflow.sklearn.log_model`** — 否決原因：runner 不該知道下游 sink 是 MLflow（jsonl / stdout 也是 sink）。`log_model` 是 EventLogger 自然 API surface 的一部分。
- **保留 stringified tag 同時新增 artifact** — 否決原因：dual-write 是 tech-debt 載體，consumer 不知道該信哪邊；違反「一次到位」原則。

## 4. Background — 為什麼會出現這些 bug

### 4.1 maldet `MlflowEventLogger` 為何把 confusion_matrix 字串化

當前的實作（`src/maldet/events/mlflow_logger.py:48-57`）：

```python
def log_event(self, kind: str, **payload: Any) -> None:
    if not self._available() or kind == "metric":
        return
    for k, v in payload.items():
        self._mlflow.set_tag(f"maldet.{kind}.{k}", str(v))
```

對所有 event payload 一視同仁，**沒有 kind-by-kind dispatch**。後果：

- `confusion_matrix` 的 payload 是 `{labels: list, matrix: list[list]}` → `str()` 成 Python repr
- `per_class` 的 payload 是 `{per_class: dict[str, dict[str, float|int]]}` → 同上
- `warning` 的 payload 是 `{message: str, sample_sha256: str}` → 第 N 個 warning 蓋掉前 N-1 個（tag 是 idempotent overwrite）

這設計選擇追溯到 maldet 1.0：當時只支援 scalar event payload，stringify-to-tag 是合理的；1.1.0+ 加了 `CONFUSION_MATRIX` 與 `PER_CLASS` 後忘了改 sink layer。**這是 evolution debt，不是「設計太保守」**。

### 4.2 lolday backend `create_run` 為何沒傳 start_time

`backend/app/services/mlflow_client.py:99-106`：

```python
async def create_run(self, experiment_id: str, tags=None) -> str:
    payload = {"experiment_id": experiment_id}
    if tags:
        payload["tags"] = tags
    resp = await self._request("POST", "/runs/create", json=payload)
    return resp["run"]["info"]["run_id"]
```

我們用 raw REST 而不是 mlflow-skinny SDK，**理由是 backend 走 async（httpx），mlflow-skinny 的 client 是 sync**（見 mlflow-client.py module docstring）。

MLflow REST API `/runs/create` 規定 `start_time` 預設 0（**不是** 自動補 `now()`）— 這跟 mlflow-skinny `MlflowClient.create_run()` 的 client-side 預設 `int(time.time() * 1000)` 不同。我們繞過 SDK 用 REST 但沒模擬該 client-side 預設行為，於是 start_time 永遠是 0。

**這是「SDK 與 REST 行為不對稱」的典型踩坑** — Databricks、Kubeflow 的 issue tracker 都有人踩過（搜「mlflow REST create-run start_time zero」）。MLflow 文件 v2.20 在 [REST API §start_time](https://mlflow.org/docs/2.20/rest-api.html#mlflowservicecreaterun) 沒明寫該預設值，只能從 server source 反推。**主流解法是 wrapper 在 client-side 補上**。

### 4.3 reconciler 為何沒 update MLflow run status

`backend/app/reconciler/jobs.py` 的 terminal transition handler（`reconcile_job` line 80、204、365）只更新 lolday DB 的 `Job.status`，沒呼叫 `MlflowClient.update_run(status="FAILED")`。

實作上 `MlflowClient.update_run()` 早就存在（`mlflow_client.py:127-138`），**但目前沒有任何 caller**。 唯一用到 mlflow client 的地方是 `_register_model_version` — 那條路徑只在 `JobStatus.SUCCEEDED` 走（model registration），於是 success 路徑 MLflow 端隱式 OK（因為 maldet 結束前會 `mlflow.end_run()`），但 **fail / timeout / pod-killed 路徑全部變 orphan**。

這是「外部資源 lifecycle 沒閉環」的經典案例 — DB transaction 跟 external state 該綁同一個 commit boundary，但只 commit 一邊。**Kubeflow Pipelines、Airflow + MLflow integration 都會在 controller 端維護 external state 對齊**（Airflow `MLflowOperator` 的 `on_kill` callback、Kubeflow `pipelinerun` 的 `garbage-collect-mlflow` reconciler）。

## 5. Architecture decisions

### 5.1 Run 生命週期語意 — A1-β（backend 創 + start_time = submit time）

**選定**：backend 在 `routers/jobs.py` 創建 run 時傳 `start_time = int(time.time() * 1000)`（submit 時刻）。

**MLflow 原生 UI 上看到的 duration = submit→完成的 wall-clock**（含 queue 時間，可能誤導）。但 **lolday 自己的 `/runs/{expId}` 與 `/jobs/{id}` 頁面顯示的 duration 改用 `Job.active_at`〜`Job.completed_at`**，這兩個 timestamp 由 reconciler 在 PREPARING→RUNNING 與 RUNNING→terminal 邊界寫入。

**為什麼不用 lazy creation（α）：**

- α 的優勢是 `start_time` 自然等於 compute start，符合 MLflow 原生 UI 預期
- α 的代價：前端在 job 還沒被 K8s 排到之前，無法跳轉 MLflow（無 run_id）— UX 倒退
- α 也削弱平台對 run 命名 / tag 注入的控制（要靠 maldet 與 backend 用 sidecar 同步）

α 在「pure orchestration」場景（Airflow + per-task MLflow）是主流，但在「平台型 + 強 UX 期望」場景（W&B + sweep agent、Databricks Jobs + Notebook run）是 β 主流。Lolday 偏後者，選 β。

> **主流參考**：W&B `wandb.init()` 也採類似策略 — run 是 launcher 創建（不是 worker），launcher-side `created_at` 之後 worker 才接管。

### 5.2 EventLogger `log_event` kind-aware routing

**選定**：`MlflowEventLogger.log_event` 按 `EventKind` dispatch：

| EventKind                   | 路由到的 MLflow API                                                                                                                  | 落地位置                   |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| `STAGE_BEGIN`               | `set_tag("maldet.stage", stage)` + `set_tag("maldet.stage_begin_ts", ts)`                                                            | tags（標籤性質、唯一）     |
| `STAGE_END`                 | `set_tag("maldet.status", status)` + `log_metric("maldet/stage_duration_seconds", elapsed)`                                          | tag + metric               |
| `DATA_LOADED`               | `log_metric("maldet/n_train", n)` + 各 `n_*` 都 metric                                                                               | metrics（數值可繪圖）      |
| `EPOCH_BEGIN` / `EPOCH_END` | 已由 `MaldetLightningLogger` 透過 `log_metric` 處理；本路徑無 op                                                                     | —                          |
| `METRIC`                    | 已直接走 `log_metric`，無 op                                                                                                         | —                          |
| `ARTIFACT_WRITTEN`          | `log_metric("maldet/artifact_bytes/{name}", size)` + `set_tag("maldet.artifact.{name}", path)`                                       | metric (size) + tag (path) |
| `CHECKPOINT_SAVED`          | 同 `ARTIFACT_WRITTEN`                                                                                                                | —                          |
| `WARNING`                   | append into in-memory buffer，run end 時 `log_text(buf.getvalue(), "warnings.jsonl")` + `log_metric("maldet/warnings_total", count)` | artifact + metric          |
| `ERROR`                     | 同 `WARNING` 但檔名 `errors.jsonl`                                                                                                   | —                          |
| `CONFUSION_MATRIX`          | `log_dict({"labels": ..., "matrix": ...}, "confusion_matrix.json")`                                                                  | artifact                   |
| `PER_CLASS`                 | `log_dict({"per_class": ...}, "per_class_metrics.json")` + 每 class 每 metric `log_metric(f"per_class/{cls}/{metric}", v)`           | artifact + metrics         |

**主流參考**：MLflow 官方文件 [Tags vs Params vs Metrics](https://mlflow.org/docs/2.20/tracking.html#tags) 明寫 tags 是 K-V 用於 filter／grouping，structured payload 用 `log_dict`（[mlflow.client § log_dict](https://mlflow.org/docs/2.20/python_api/mlflow.client.html#mlflow.client.MlflowClient.log_dict)）。`log_text` for append-only line streams 是 PyTorch Lightning、HuggingFace Trainer 處理 training log 的標準路徑（[Lightning MLflowLogger source](https://github.com/Lightning-AI/pytorch-lightning/blob/master/src/lightning/pytorch/loggers/mlflow.py)）。

### 5.3 EventLogger protocol 擴 `log_model`

**選定**：`EventLogger` protocol 新增

```python
def log_model(
    self,
    model: Any,
    flavor: str,                      # "sklearn" | "pytorch" | "pyfunc"
    artifact_path: str = "model",
    signature: Any = None,             # mlflow.models.ModelSignature | None
    input_example: Any = None,
    pip_requirements: list[str] | None = None,
) -> None: ...
```

`MlflowEventLogger.log_model` 按 `flavor` dispatch 到 `mlflow.sklearn.log_model` / `mlflow.pytorch.log_model` / `mlflow.pyfunc.log_model`。`JsonlEventLogger.log_model` 寫 `{"kind": "model_logged", "flavor": ..., "artifact_path": ...}` 一行（不存 model 本身）。`StdoutEventLogger.log_model` 印一行。

Trainer `save()` 改 signature：

```python
def save(self, result: TrainResult, out_dir: Path, *, logger: EventLogger, signature_input_sample: np.ndarray | None = None) -> None:
    """Save model locally for next-stage init container AND log to MLflow."""
```

`SklearnTrainer.save` 用 `mlflow.sklearn.save_model(model, out_dir, signature=..., input_example=...)` 同時寫 local 與生成 MLmodel 結構，再 `logger.log_model(..., flavor="sklearn", ...)` 上傳。`LightningTrainer.save` 對應 `mlflow.pytorch`。

**主流參考**：MLflow 文件 [Models § Built-in Flavors](https://mlflow.org/docs/2.20/models.html#built-in-model-flavors) 列出每個 flavor 的 `save_model` 與 `log_model` 對稱性。產生的 `MLmodel` YAML + `python_env.yaml` + `requirements.txt` 是 MLflow Models Registry / Models Serving 的最低門檻。

### 5.4 Dataset lineage 透過 `mlflow.log_input`

**選定**：`StageRunner._run_stage` 在 `train` / `evaluate` / `predict` 三個分支裡，於讀完 CSV 後呼叫 `mlflow.data.from_pandas()` + `mlflow.log_input()`：

```python
import mlflow.data
ds = mlflow.data.from_pandas(
    df=train_df,
    source=str(cfg.data.train_csv),                # 在 lolday 環境會是 "/mnt/config/train.csv"
    name=f"train_{cfg.lolday.train_dataset_id}",    # backend 透過 cfg.lolday.* 注入
    digest=hashlib.sha256(train_df.to_csv().encode()).hexdigest()[:8],
)
mlflow.log_input(ds, context="training")
```

`context` 取值：`"training"` / `"evaluation"` / `"prediction"`。lolday backend 透過 `cfg.lolday.{train,test,predict}_dataset_id` 把 lolday DB 的 `Dataset.id` 注入 — 這支援未來「從 MLflow run 反查 lolday dataset」的需求。

**主流參考**：MLflow 2.4+ 官方 [Dataset Tracking](https://mlflow.org/docs/2.20/python_api/mlflow.data.html) 即此模式。Databricks Feature Store、Vertex AI Pipelines 都以類似抽象記錄 dataset lineage。

### 5.5 Reconciler MLflow finalize

**選定**：在 `reconciler/jobs.py` terminal transition 點補 finalize：

```python
async def _finalize_mlflow_run(j: Job, status: str, *, end_time: int | None = None) -> None:
    """Idempotent — safe to call multiple times. maldet may have already
    called mlflow.end_run() with FINISHED; calling update_run("FAILED") after
    is a controller correction. MLflow API is overwrite-style."""
    if not j.mlflow_run_id:
        return
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    try:
        await client.update_run(
            j.mlflow_run_id,
            status=status,
            end_time=end_time or int(time.time() * 1000),
        )
    except MlflowError as e:
        logger.warning("mlflow finalize failed for job %s: %s", j.id, e)
        BACKEND_ERRORS.labels(stage="mlflow_finalize").inc()
```

呼叫點：

- `j.status = JobStatus.FAILED` → `await _finalize_mlflow_run(j, "FAILED")`
- `j.status = JobStatus.SUCCEEDED` → `await _finalize_mlflow_run(j, "FINISHED")` （冪等；maldet 通常已寫過）
- `j.status = JobStatus.TIMEOUT` → `await _finalize_mlflow_run(j, "KILLED")`

**MLflow run.status enum**：`FINISHED` | `FAILED` | `KILLED` | `SCHEDULED` | `RUNNING`（MLflow 2.20 source `mlflow.entities.RunStatus`）。lolday `JobStatus.FAILED` → MLflow `FAILED`，`JobStatus.TIMEOUT` / lolday-killed → `KILLED`。

### 5.6 System metrics via MLflow built-in

**選定**：在 `job_spec.py` detector container env 加：

```python
{"name": "MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING", "value": "true"},
{"name": "MLFLOW_SYSTEM_METRICS_SAMPLING_INTERVAL", "value": "10"},
```

前置：`charts/lolday/helpers/pytorch-cu12-base/Dockerfile` 加 `pip install psutil pynvml`。

MLflow 2.8+ 內建 system metrics logging（[官方文件](https://mlflow.org/docs/2.20/system-metrics/index.html)），會以 10s 為間隔自動 log：

- `system/cpu_utilization_percentage`
- `system/system_memory_usage_megabytes`
- `system/gpu_<N>_utilization_percentage`
- `system/gpu_<N>_memory_usage_megabytes`
- `system/gpu_<N>_memory_usage_percentage`
- `system/gpu_<N>_power_usage_watts`

**這個 telemetry 與 lolday host-aware GPU signal（v0.20.9）是互補的**：

- `host-aware GPU signal` = cluster-wide GPU 排程決策（read DCGM via Prom，給 FIFO scheduler 用）
- `MLflow system metrics` = per-run 後驗 profiling（給 detector 作者 / 平台 admin 看「這個 detector 在 v4.1.0 用了多少 VRAM」）

### 5.7 Provenance tag schema

**選定**：backend `create_run` 時注入下列 tag（在原本的 `lolday.*` 基礎上擴）：

```python
tags = [
    # 既有：mlflow.runName / maldet.action / lolday.job_id / lolday.user{,_id} / lolday.detector_version{,_id}
    ...,

    # MLflow convention（Python SDK 會自動填，REST 沒填，現在補）
    {"key": "mlflow.source.name", "value": detector_version_label},
    {"key": "mlflow.source.type", "value": "JOB"},
    {"key": "mlflow.source.git.commit", "value": dv.git_commit_sha},
    {"key": "mlflow.source.git.repoURL", "value": det.git_repo_url or ""},

    # lolday 平台 provenance
    {"key": "lolday.detector_image_digest", "value": dv.image_digest},
    {"key": "lolday.maldet_version", "value": dv.maldet_version},
    {"key": "lolday.resource_profile", "value": body.resource_profile.value},
    {"key": "lolday.gpu_count", "value": str(gpu_count)},

    # Dataset lineage（補一份在 tag、另外也由 maldet.log_input 寫 artifact）
    {"key": "lolday.train_dataset_id", "value": str(train_ds.id) if train_ds else ""},
    {"key": "lolday.test_dataset_id", "value": str(test_ds.id) if test_ds else ""},
    {"key": "lolday.predict_dataset_id", "value": str(predict_ds.id) if predict_ds else ""},
    {"key": "lolday.source_model_version_id", "value": str(source_model.id) if source_model else ""},
]
```

**為何 tag 重複資訊（既已寫 log_input）**：tag 是 lightweight queryable（`search_runs(filter="tags.lolday.train_dataset_id = 'xxx'")`），artifact 是 detail view。兩者並存是 MLflow 官方推薦 pattern。

### 5.8 DetectorVersion schema 擴充

**選定**：`backend/app/models/detector.py` 的 `DetectorVersion` 新增三個欄位：

| 欄位             | 型別                                  | 寫入時機                                                    | 用途                                  |
| ---------------- | ------------------------------------- | ----------------------------------------------------------- | ------------------------------------- |
| `git_commit_sha` | `str(40)` nullable                    | `services/build.py` build 階段，從 `git rev-parse HEAD`     | tag 補 `mlflow.source.git.commit`     |
| `image_digest`   | `str(72)` nullable (`sha256:` prefix) | `services/build.py` push 後 `docker manifest inspect`       | tag 補 `lolday.detector_image_digest` |
| `maldet_version` | `str(16)` nullable                    | `services/build.py` 從 build container `pip show maldet` 撈 | tag 補 `lolday.maldet_version`        |

Alembic migration：`backend/migrations/versions/<rev>_detectorversion_provenance.py`，三個 `add_column` + 既有 row 全部填 `NULL`（後續 build 自動 backfill）。

> **這是 schema breaking change** — `DetectorVersionRead` schema 同步擴。Spec authorization §2 允許。

### 5.9 Experiment-level metadata

**選定**：backend 第一次建 experiment 時透過 `set_experiment_tag` 補 `mlflow.note.content`（MLflow UI 上方顯示的描述）+ 結構化 tag：

```python
await client.set_experiment_tag(exp_id, "mlflow.note.content",
    f"**Detector**: `{det.name}` @ `{dv.git_tag}`\n\n"
    f"**Owner**: `{user.handle}`\n\n"
    f"**Description**: {det.description or '_no description_'}\n\n"
    f"**Maldet framework**: `{dv.maldet_version}`\n"
)
await client.set_experiment_tag(exp_id, "lolday.detector_id", str(det.id))
await client.set_experiment_tag(exp_id, "lolday.detector_version_id", str(dv.id))
await client.set_experiment_tag(exp_id, "lolday.owner_id", str(user.id))
await client.set_experiment_tag(exp_id, "lolday.owner_handle", user.handle)
```

`mlflow.note.content` 走 Markdown 渲染（MLflow UI 原生支援）。

## 6. Implementation details

### 6.1 maldet `MlflowEventLogger` 改寫

新 module structure（`src/maldet/events/mlflow_logger.py`）：

```python
class MlflowEventLogger:
    def __init__(self, mlflow: Any = None) -> None:
        self._mlflow = mlflow if mlflow is not None else _try_import_mlflow()
        # in-memory buffers for line-stream events; flushed on close()
        self._warning_buf: list[dict[str, Any]] = []
        self._error_buf: list[dict[str, Any]] = []

    # log_metric / log_params / log_artifact / set_tags — 不變

    def log_event(self, kind: str, **payload: Any) -> None:
        if not self._available():
            return
        handler = _EVENT_HANDLERS.get(kind, _handle_generic_tag)
        handler(self._mlflow, kind, payload, self)

    def log_model(self, model: Any, flavor: str, artifact_path: str = "model",
                  signature: Any = None, input_example: Any = None,
                  pip_requirements: list[str] | None = None) -> None:
        if not self._available():
            return
        if flavor == "sklearn":
            self._mlflow.sklearn.log_model(
                model, artifact_path=artifact_path,
                signature=signature, input_example=input_example,
                pip_requirements=pip_requirements,
            )
        elif flavor == "pytorch":
            self._mlflow.pytorch.log_model(
                model, artifact_path=artifact_path,
                signature=signature, input_example=input_example,
                pip_requirements=pip_requirements,
            )
        elif flavor == "pyfunc":
            self._mlflow.pyfunc.log_model(
                python_model=model, artifact_path=artifact_path,
                signature=signature, input_example=input_example,
                pip_requirements=pip_requirements,
            )
        else:
            raise ValueError(f"unknown mlflow flavor: {flavor!r}")

    def close(self) -> None:
        """Called by runner at stage end — flush buffers."""
        if not self._available():
            return
        if self._warning_buf:
            buf = "\n".join(json.dumps(w) for w in self._warning_buf)
            self._mlflow.log_text(buf, "warnings.jsonl")
            self._mlflow.log_metric("maldet/warnings_total", len(self._warning_buf))
        if self._error_buf:
            buf = "\n".join(json.dumps(e) for e in self._error_buf)
            self._mlflow.log_text(buf, "errors.jsonl")
            self._mlflow.log_metric("maldet/errors_total", len(self._error_buf))


# Event handlers (module-level functions for easy testability)

def _handle_stage_begin(mlflow, kind, payload, logger):
    if "stage" in payload:
        mlflow.set_tag("maldet.stage", str(payload["stage"]))
    mlflow.set_tag("maldet.stage_begin_ts", str(time.time()))


def _handle_stage_end(mlflow, kind, payload, logger):
    if "stage" in payload:
        mlflow.set_tag("maldet.stage_end", str(payload["stage"]))
    if "status" in payload:
        mlflow.set_tag("maldet.status", str(payload["status"]))


def _handle_data_loaded(mlflow, kind, payload, logger):
    for k, v in payload.items():
        try:
            mlflow.log_metric(f"maldet/{k}", float(v))
        except (TypeError, ValueError):
            mlflow.set_tag(f"maldet.data.{k}", str(v))


def _handle_warning(mlflow, kind, payload, logger):
    logger._warning_buf.append({"ts": time.time(), **payload})


def _handle_error(mlflow, kind, payload, logger):
    logger._error_buf.append({"ts": time.time(), **payload})


def _handle_confusion_matrix(mlflow, kind, payload, logger):
    mlflow.log_dict({"labels": payload["labels"], "matrix": payload["matrix"]},
                    "confusion_matrix.json")


def _handle_per_class(mlflow, kind, payload, logger):
    per_class = payload["per_class"]
    mlflow.log_dict(per_class, "per_class_metrics.json")
    for cls, metrics in per_class.items():
        for name, v in metrics.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(f"per_class/{cls}/{name}", float(v))


def _handle_artifact_written(mlflow, kind, payload, logger):
    path = payload.get("path", "")
    name = Path(path).name if path else "unknown"
    mlflow.set_tag(f"maldet.artifact.{name}", str(path))
    if "size_bytes" in payload:
        try:
            mlflow.log_metric(f"maldet/artifact_bytes/{name}", float(payload["size_bytes"]))
        except (TypeError, ValueError):
            pass


def _handle_checkpoint_saved(mlflow, kind, payload, logger):
    _handle_artifact_written(mlflow, kind, payload, logger)


def _handle_generic_tag(mlflow, kind, payload, logger):
    """Fallback for any not-explicitly-handled kind — preserve original behavior
    for forward compat, but scope tag namespace strictly under maldet.{kind}.{k}."""
    for k, v in payload.items():
        if isinstance(v, (str, int, float, bool)):
            mlflow.set_tag(f"maldet.{kind}.{k}", str(v))


_EVENT_HANDLERS: dict[str, Callable] = {
    "stage_begin": _handle_stage_begin,
    "stage_end": _handle_stage_end,
    "data_loaded": _handle_data_loaded,
    "warning": _handle_warning,
    "error": _handle_error,
    "confusion_matrix": _handle_confusion_matrix,
    "per_class": _handle_per_class,
    "artifact_written": _handle_artifact_written,
    "checkpoint_saved": _handle_checkpoint_saved,
    "epoch_begin": _handle_generic_tag,   # already covered by lightning logger
    "epoch_end": _handle_generic_tag,
}
```

### 6.2 maldet `StageRunner._pinned_mlflow_run` 加 `logger.close()`

`runner.py` 的 context manager 在 `finally` block 加上：

```python
finally:
    # Flush buffered events before ending the run.
    for d in delegates_if_composite:  # iterate composite
        if hasattr(d, "close"):
            try:
                d.close()
            except Exception as e:
                _log.warning("event_logger_close_failed: %s", e)
    if mlflow.active_run() is not None:
        mlflow.end_run()
```

實作上 runner 不知道 logger 是 composite — 加一個 protocol method `close()` 給 logger，CompositeEventLogger 的 `close()` fanout 到所有 delegates。

### 6.3 maldet `SklearnTrainer.save / load`

```python
def save(self, result: TrainResult, out_dir: Path, *, logger: EventLogger,
         signature_input_sample: np.ndarray | None = None) -> None:
    import mlflow.sklearn
    from mlflow.models import infer_signature
    out_dir.mkdir(parents=True, exist_ok=True)

    signature = None
    input_example = None
    if signature_input_sample is not None and len(signature_input_sample) > 0:
        sample_X = signature_input_sample[:5]
        sample_y = result.model.predict(sample_X)
        signature = infer_signature(sample_X, sample_y)
        input_example = sample_X

    # Write MLflow Model layout to local out_dir AND upload to MLflow run.
    mlflow.sklearn.save_model(
        sk_model=result.model,
        path=str(out_dir),
        signature=signature,
        input_example=input_example,
    )
    # log_artifacts uploads the local dir under run.artifacts/model/
    logger.log_artifact(out_dir, artifact_path="model")

def load(self, model_dir: Path) -> Any:
    import mlflow.sklearn
    return mlflow.sklearn.load_model(str(model_dir))
```

> **Note on `mlflow.sklearn.save_model` + `log_artifact` 重複**：`mlflow.sklearn.log_model()` 會直接寫到 run（不留 local），但我們的下游 init container（model-fetcher）需要從 `runs:/<id>/model` 拉回 local。`save_model` 寫 local + `log_artifact` 上傳是兩個動作但結果一致。**主流參考**：MLflow 文件 [§ logging a local-saved model](https://mlflow.org/docs/2.20/models.html#saving-and-loading-models) 提到「save then log」是處理 distributed / multi-step pipeline 的標準做法。

### 6.4 maldet `LightningTrainer.save / load`

`mlflow.pytorch.save_model` 接受 `nn.Module` 並 pickle 整個 class（不只是 state_dict），因此 load 不需要 factory：

```python
def save(self, result: TrainResult, out_dir: Path, *, logger: EventLogger,
         signature_input_sample: torch.Tensor | None = None) -> None:
    import mlflow.pytorch
    from mlflow.models import infer_signature
    out_dir.mkdir(parents=True, exist_ok=True)

    signature = None
    input_example = None
    if signature_input_sample is not None and len(signature_input_sample) > 0:
        with torch.no_grad():
            sample_in = signature_input_sample[:5]
            sample_out = result.model(sample_in)
        signature = infer_signature(sample_in.cpu().numpy(), sample_out.cpu().numpy())
        input_example = sample_in.cpu().numpy()

    # If best_checkpoint exists, load it back into result.model first
    # so the saved model is the best-epoch state.
    if result.best_checkpoint is not None and result.best_checkpoint.exists():
        state = torch.load(result.best_checkpoint, map_location="cpu")
        if "state_dict" in state:
            result.model.load_state_dict(state["state_dict"])

    mlflow.pytorch.save_model(
        pytorch_model=result.model,
        path=str(out_dir),
        signature=signature,
        input_example=input_example,
    )
    logger.log_artifact(out_dir, artifact_path="model")

def load(self, model_dir: Path) -> pl.LightningModule:
    import mlflow.pytorch
    return mlflow.pytorch.load_model(str(model_dir))  # factory not needed
```

`runner.py` 的 `_load_with_optional_factory` 可保留作為 fallback，但 maldet 2.2 內建 trainer 都不再依賴 factory。

### 6.5 maldet runner — dataset lineage

`runner.py` 在 train/evaluate/predict 三個 branch 開頭加：

```python
def _log_dataset_input(cfg: DictConfig, stage: str, df_or_csv: Path) -> None:
    try:
        import mlflow
        import mlflow.data
        import pandas as pd
    except ImportError:
        return
    if mlflow.active_run() is None:
        return
    try:
        df = pd.read_csv(df_or_csv)
    except Exception:
        return
    dataset_id_key = {"train": "train_dataset_id", "evaluate": "test_dataset_id",
                      "predict": "predict_dataset_id"}[stage]
    lolday_meta = cfg.get("lolday") or {}
    ds_id = lolday_meta.get(dataset_id_key, "unknown")
    digest = hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]
    ds = mlflow.data.from_pandas(
        df=df,
        source=str(df_or_csv),
        name=f"{stage}_{ds_id}",
        digest=digest,
    )
    context = {"train": "training", "evaluate": "evaluation", "predict": "prediction"}[stage]
    mlflow.log_input(ds, context=context)
```

對應 lolday backend 在 `JobConfigRenderer.render_config_yaml` 注入：

```yaml
lolday:
  train_dataset_id: "..."
  test_dataset_id: "..."
  predict_dataset_id: "..."
  source_model_version_id: "..."
```

### 6.6 lolday backend — `MlflowClient` API surface

```python
class MlflowClient:
    async def create_run(
        self,
        experiment_id: str,
        *,
        start_time_ms: int,                     # REQUIRED (was optional/missing)
        tags: list[dict[str, str]] | None = None,
    ) -> str: ...

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        end_time_ms: int | None = None,
    ) -> None: ...

    async def set_experiment_tag(
        self, experiment_id: str, key: str, value: str
    ) -> None: ...
```

### 6.7 lolday backend — `routers/jobs.py` create-run call site

```python
import time
now_ms = int(time.time() * 1000)

run_id = await client.create_run(
    dv.mlflow_experiment_id,
    start_time_ms=now_ms,
    tags=[
        {"key": "mlflow.runName", "value": run_name},
        {"key": "mlflow.source.name", "value": detector_version_label},
        {"key": "mlflow.source.type", "value": "JOB"},
        {"key": "mlflow.source.git.commit", "value": dv.git_commit_sha or ""},
        {"key": "maldet.action", "value": body.type.value},
        # lolday namespace
        {"key": "lolday.job_id", "value": str(job_id)},
        {"key": "lolday.user", "value": user.handle},
        {"key": "lolday.user_id", "value": str(user.id)},
        {"key": "lolday.detector_version", "value": detector_version_label},
        {"key": "lolday.detector_version_id", "value": str(dv.id)},
        {"key": "lolday.detector_image_digest", "value": dv.image_digest or ""},
        {"key": "lolday.maldet_version", "value": dv.maldet_version or ""},
        {"key": "lolday.resource_profile", "value": body.resource_profile.value},
        {"key": "lolday.gpu_count", "value": str(RESOURCE_PROFILE_GPU_COUNT[body.resource_profile])},
        {"key": "lolday.train_dataset_id", "value": str(train_ds.id) if train_ds else ""},
        {"key": "lolday.test_dataset_id", "value": str(test_ds.id) if test_ds else ""},
        {"key": "lolday.predict_dataset_id", "value": str(predict_ds.id) if predict_ds else ""},
        {"key": "lolday.source_model_version_id", "value": str(source_model.id) if source_model else ""},
    ],
)
```

### 6.8 lolday frontend — duration source switch

`frontend/src/routes/_authed.runs.$expId.tsx`:

```tsx
import { useLoldayJobByMlflowRunId } from "@/api/queries/jobs";
// ...

{
  id: "duration",
  header: "Compute time",
  cell: ({ row }) => {
    const jobId = row.original.tags?.["lolday.job_id"];
    const { data: job } = useLoldayJobByMlflowRunId(jobId);  // batched fetch
    if (!job?.active_at || !job?.completed_at) return "—";
    return formatDuration(job.active_at, job.completed_at);
  },
}
```

對應 backend `experiments_proxy.py` 加一個 batched enrichment endpoint：在 `_flatten_run` 後從 lolday DB 透過 `tags.lolday.job_id` 撈出對應 Job 的 `active_at` / `completed_at` 拼進去。

更簡便的做法：直接在 backend 把 lolday 時間注進 `_flatten_run` 結果（避免前端多一個 query）：

```python
def _flatten_run(r, *, lolday_job_meta: dict[str, dict] | None = None):
    ...
    job_id = tags.get("lolday.job_id")
    if lolday_job_meta and job_id and job_id in lolday_job_meta:
        result["lolday_active_at"] = lolday_job_meta[job_id]["active_at"]
        result["lolday_completed_at"] = lolday_job_meta[job_id]["completed_at"]
    return result
```

`list_runs` 與 `get_run` 都先撈一次 lolday Job 表 by `id IN (...job_ids)`、build `lolday_job_meta`、再傳給 `_flatten_run`。前端直接取 `row.lolday_active_at` / `row.lolday_completed_at`。

## 7. Migration

### 7.1 Cross-repo dependency order

```
maldet 2.2.0 ─┬─► PyPI publish ─► elfrfdet / elfcnndet 升級依賴 ─► image rebuild ─► Harbor push
              │
              └─► lolday backend / frontend changes (independent of maldet release timing)
```

`lolday` 端的 changes 可在 maldet 還沒 release 前先進 — 它們不依賴 maldet 2.2.0 的新事件 routing（會 graceful fallback）。但 e2e smoke 必須在 maldet 2.2 + detectors 升完之後跑。

### 7.2 deployment cut-over 順序

1. **maldet 2.2.0 publish**：merge maldet PR → tag `v2.2.0` → `uv publish` 用 `UV_PUBLISH_TOKEN`
2. **detector bump**：elfrfdet / elfcnndet 改 `min_maldet = "2.2"` + 版本 bump（rf 4.2.0 / cnn 對應）→ merge → CI build image → push Harbor
3. **lolday backend / frontend changes merge** → image build → Harbor push
4. **deploy.sh** 一次性帶 `BUILD_IMAGE`、`FRONTEND_IMAGE` 升級 → `kubectl rollout status`
5. **手動 trigger 一個 train run** 驗證 §8 中所有觀測點

順序 1 → 2 是強相依（detector 沒有 maldet 2.2 起不來 — 因為 pyproject.toml 寫了 `maldet>=2.2,<3`）。1 → 3 也是強相依（lolday backend 的 frontend duration enrichment 依賴 mlflow_run_id tag，這個本來就有；但 e2e 驗證 confusion_matrix 走 artifact 需要 maldet 2.2 在 detector image 內）。

### 7.3 Rollback

每個 PR 單獨 revert。maldet 2.2.0 已 publish 到 PyPI 後**不能 revert**（PyPI 政策），但可 publish 2.2.1 hotfix 回退。detector 若想 rollback 到 maldet 2.1，bump version 重 build 一次即可。

### 7.4 Optional：對既有 RUNNING orphan run 一次性收尾

提供 `scripts/oneshot-mlflow-orphan-sweep.sh`：

```bash
# 找出所有 status=RUNNING 但 lolday Job.status terminal 的 run，update_run 到對應 status
# 帶 --dry-run 模式
```

不在主流程裡，operator 自行決定要不要跑。Experiment 28 的 `train-0eaa2f0f` 是已知的一筆。

## 8. Test plan

### 8.1 maldet unit tests

| Test                                                   | Path                                       | 驗證                                                                       |
| ------------------------------------------------------ | ------------------------------------------ | -------------------------------------------------------------------------- |
| `test_log_event_confusion_matrix_writes_artifact`      | `tests/events/test_mlflow_logger.py`       | confusion_matrix payload → `mlflow.log_dict(..., "confusion_matrix.json")` |
| `test_log_event_per_class_writes_artifact_and_metrics` | 同上                                       | per_class → log_dict + per-class log_metric                                |
| `test_log_event_warning_buffered_flushed_on_close`     | 同上                                       | 多筆 warning 不互蓋；close() 寫 warnings.jsonl                             |
| `test_log_event_data_loaded_writes_metric`             | 同上                                       | n_train 變 `maldet/n_train` metric                                         |
| `test_log_model_sklearn_dispatches`                    | 同上                                       | flavor="sklearn" → mlflow.sklearn.log_model called                         |
| `test_log_model_pytorch_dispatches`                    | 同上                                       | flavor="pytorch" → mlflow.pytorch.log_model called                         |
| `test_sklearn_trainer_save_uses_mlflow_flavor`         | `tests/trainers/test_sklearn_trainer.py`   | save 後 out_dir 有 MLmodel + python_env.yaml                               |
| `test_lightning_trainer_save_uses_mlflow_flavor`       | `tests/trainers/test_lightning_trainer.py` | 同上                                                                       |
| `test_runner_emits_log_input_in_train`                 | `tests/integration/test_runner_mlflow.py`  | mlflow.log_input called 1x with `context="training"`                       |

### 8.2 lolday backend unit tests

| Test                                                     | Path                                      | 驗證                                               |
| -------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------- |
| `test_create_run_passes_start_time`                      | `tests/test_services_mlflow_client.py`    | payload 帶 `start_time = ms_int`                   |
| `test_jobs_create_run_call_site_provenance_tags`         | `tests/test_routers_jobs.py`              | 14 個新 tag 都進到 create_run 的 tags              |
| `test_reconcile_failed_calls_finalize_mlflow`            | `tests/test_reconciler_jobs.py`           | `j.status = FAILED` 後 update_run("FAILED") 被呼叫 |
| `test_reconcile_timeout_calls_finalize_killed`           | 同上                                      | timeout → "KILLED"                                 |
| `test_reconcile_succeeded_calls_finalize_finished`       | 同上                                      | succeeded → "FINISHED"（冪等 OK）                  |
| `test_detector_container_has_system_metrics_env`         | `tests/test_services_job_spec.py`         | env 含 MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING        |
| `test_experiments_proxy_enriches_with_lolday_timestamps` | `tests/test_routers_experiments_proxy.py` | `_flatten_run` 結果有 `lolday_active_at`           |

### 8.3 Live smoke (server30)

`tests/2026-05-11-mlflow-redesign-smoke.sh`:

1. Submit 一個 train job (elfrfdet)，wait 完成
2. Query `experiment_id` 對應的 latest run，斷言：
   - `info.start_time != 0`
   - `info.status == "FINISHED"`
   - `info.end_time != 0`
3. Artifact list 含 `confusion_matrix.json`（evaluate run）+ `per_class_metrics.json` + `MLmodel`（train run）
4. Tag 含 `mlflow.source.git.commit`、`lolday.detector_image_digest`、`lolday.train_dataset_id`
5. Metric 含 `system/gpu_0_utilization_percentage`、`system/cpu_utilization_percentage`
6. Run 的 `data.inputs` 含一筆 dataset
7. lolday `/runs/{exp_id}` 頁面 duration 欄顯示 compute time（非 wall-clock）
8. Negative test：手動 `kubectl delete vcjob` 一個 train job → reconciler 應 update MLflow run 到 FAILED 而非 stuck RUNNING

### 8.4 Manual UI 驗證

- MLflow 原生 UI 上 experiment 頁面顯示 `mlflow.note.content` Markdown
- Run 頁面 confusion_matrix.json artifact 可預覽（JSON viewer）
- Run 頁面 system metrics 自動畫成時序圖
- lolday `/runs/{exp_id}` Duration 欄顯示 compute time，非 0、非超大數
- lolday `/jobs/{id}` 頁面照舊（不應壞）

## 9. Open questions / future work

1. **MLflow `mlflow.evaluate()` 整合** — 取代 maldet `BinaryClassification`？跨 sklearn/pytorch flavor 行為差異需評估。下一份 spec 處理。
2. **Per-sample drift 監測** — log_input + 統計 quartile / KS test。需 dataset registry 升級配合。
3. **Cross-detector comparison view** — frontend 新頁面對齊不同 detector_version 的 metric。需 backend aggregate API。
4. **MLflow Serving via lolday** — 把 train 好的 model 用 `mlflow models serve` 起 inference endpoint。需 K8s deployment 抽象。
5. **maldet 2.2 之後的 schema_version 1 → 2 migration**：`MetricReport.to_json_dict()` 的 `schema_version` 若改變 lolday `_project_summary_metrics` 需同步。本 spec 不變該值。

## 10. References

- MLflow Tracking [§ Tags vs Params vs Metrics](https://mlflow.org/docs/2.20/tracking.html#tags)
- MLflow Models [§ Built-in Flavors](https://mlflow.org/docs/2.20/models.html#built-in-model-flavors)
- MLflow Data [§ Dataset Tracking](https://mlflow.org/docs/2.20/python_api/mlflow.data.html)
- MLflow System Metrics [§ Configuration](https://mlflow.org/docs/2.20/system-metrics/index.html)
- MLflow REST API [§ CreateRun](https://mlflow.org/docs/2.20/rest-api.html#mlflowservicecreaterun)
- W&B Run Lifecycle — <https://docs.wandb.ai/guides/runs/lifecycle>
- Kubeflow Pipelines MLflow integration — <https://www.kubeflow.org/docs/components/pipelines/v2/components/lightweight-python-components/>
- maldet repo (private)：`/home/bolin8017/Documents/repositories/maldet`
- elfrfdet repo (private)：`/home/bolin8017/Documents/repositories/elfrfdet`
- elfcnndet repo (private)：`/home/bolin8017/Documents/repositories/elfcnndet`
- 相關 lolday spec：
  - `2026-05-10-host-aware-gpu-signal-design.md`（GPU telemetry 並行軌）
  - `2026-05-07-model-registry-namespace-and-visibility-design.md`（Model Registry naming）
  - `2026-04-24-phase11-detector-framework-v1-design.md`（maldet 框架初始 design）
