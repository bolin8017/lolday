# Phase 3: Detector Lifecycle — Design Specification

## Overview

Lab members register their malware detectors (Git repos built on the maldet spec), the platform clones + validates them, builds a container image inside a sandboxed K8s Job, scans the image for CVEs via Harbor's bundled Trivy, and publishes successful versions for later use by Phase 4 train/eval/predict jobs.

**Goal:** A developer enters a Git URL, picks a tag, and gets back a versioned, CVE-scanned container image stored in Harbor — plus the detector's Pydantic config schema extracted and stored for future frontend form rendering.

**Constraints:**
- Must not break SSH on server30 (port 9453)
- No custom code where an open-source tool exists
- Container isolation must be equivalent to runtime sandbox (non-root, non-privileged, no K8s API, no DNS tunneling)
- Single server for now; design must not preclude future multi-node scaling

---

## Scope

Phase 3 covers all four detector-lifecycle sub-areas in the main spec §4, done as one cohesive delivery:

1. **Registration** — Git URL normalization, static spec validation, per-user PAT storage
2. **Build Pipeline** — Sandboxed K8s Job: clone → runtime validation → Kaniko build → Harbor push + scan
3. **Version Management** — Git tag → image tag with immutable commit SHA record, Harbor retention of 3 newest tags per detector
4. **JSON Schema Extraction** — Pydantic `model_json_schema()` extracted during build, stored as JSONB in PostgreSQL. Schema rendering / normalization deferred to Phase 5.

Out of scope (deferred phases): email notifications (Phase 6), real-time log streaming UI (Phase 5), Git push webhooks for auto-rebuild (future), CVE whitelist / severity override (future).

---

## Architecture

```
                    User (Developer/Admin)
                         │
                         ▼ HTTPS (Phase 6 Cloudflare Tunnel)
┌──────────────────────────────────────────────────────┐
│ FastAPI Backend  (namespace: lolday)                 │
│                                                      │
│  POST /detectors                  → 註冊 (clone + 靜態檢查)
│  POST /detectors/{id}/builds      → 建立 Build (K8s Job)
│  GET  /detectors/{id}/builds/{id} → 查 build 歷史 + log
│  GET  /detectors/{id}/versions    → 列可用 tag + schema
│  PUT  /users/me/git-credential    → 加密儲存 PAT
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │ BuildReconciler (asyncio 背景 loop)           │    │
│  │ 每 10 秒輪詢 in-flight K8s Job + Harbor scan │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────┘
          ▲            │ K8s API (namespace-scoped Role)
          │            ▼
          │   ┌────────────────────────────────────┐
          │   │ K8s Job: build-{detector}-{tag}    │
          │   │                                    │
          │   │  init #1: git clone  (alpine/git)  │
          │   │  init #2: validate   (build-helper)│
          │   │           ├─ pip install + import   │
          │   │           ├─ isinstance 檢查         │
          │   │           └─ model_json_schema()    │
          │   │              → POST back to backend│
          │   │  main:    kaniko build + push      │
          │   │                                    │
          │   │  SecurityContext: non-root,        │
          │   │  non-privileged, drop ALL caps,    │
          │   │  SA token 不掛                      │
          │   │  Resource limits + 20 分鐘 hard TO │
          │   └──────────┬─────────────────────────┘
          │              │ push
          │              ▼ HTTP (cluster-internal)
          │   ┌────────────────────────────────────┐
          │   │ Harbor (namespace: harbor)          │
          │   │  Projects: detectors/,              │
          │   │            detectors-cache/,        │
          │   │            lolday/                  │
          │   │  Bundled Trivy → on-push CVE scan   │
          │   │  Retention: 最新 3 個 tag           │
          │   └────────────┬───────────────────────┘
          │                │ scan 結果
          └────────────────┘ (reconciler 輪詢)
                         │
                         ▼
                    PostgreSQL
                    (detector, detector_version,
                     detector_build, user_git_credential)
```

---

## Data Model

Five tables added. Phase 2 `user` table unchanged.

### `detector`

| Field | Type | Description |
|-------|------|-------------|
| id | UUID PK | |
| name | String(100) unique | slug; Harbor image name |
| display_name | String(200) | UI 顯示名稱 |
| description | Text | 來自 `pyproject.toml`，owner 可覆寫 |
| git_url | String(500) | 正規化後 HTTPS URL |
| owner_id | FK → user | 建立者 |
| created_at | Timestamp | |
| deleted_at | Timestamp nullable | soft delete |

**Unique constraint:** `(owner_id, git_url)`，排除 `deleted_at IS NOT NULL` 的列。

### `detector_version`

Build 成功後才建立。一個 Git tag 一列。

| Field | Type | Description |
|-------|------|-------------|
| id | UUID PK | |
| detector_id | FK → detector | |
| git_tag | String(100) | e.g. `v0.4.0` |
| git_sha | String(40) | 當次 build 的 commit SHA（immutable） |
| harbor_image | String(500) | e.g. `harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0` |
| image_digest | String(100) | `sha256:...` Harbor 回傳 |
| config_schema | JSONB | 原生 Pydantic v2 Draft 2020-12 輸出（未經轉換） |
| built_at | Timestamp | |
| status | Enum(active, retention_pruned) | retention 剔除後只改狀態，不刪列 |

**Unique constraint:** `(detector_id, git_tag)`。

### `detector_build`

每次 build 嘗試一列，無論成敗。

| Field | Type | Description |
|-------|------|-------------|
| id | UUID PK | |
| detector_id | FK → detector | |
| git_tag | String(100) | |
| git_sha | String(40) nullable | clone 成功後填 |
| triggered_by_id | FK → user | |
| k8s_job_name | String(100) nullable | `build-{detector}-{tag_slug}-{short_id}` |
| status | Enum | `pending`, `cloning`, `validating`, `building`, `scanning`, `succeeded`, `failed`, `timeout`, `cancelled`, `cve_blocked` |
| failure_reason | Text nullable | 結構化 error code |
| log_tail | Text nullable | build 完成時擷取末 8KB |
| trivy_critical | Int nullable | |
| trivy_high | Int nullable | |
| started_at | Timestamp | |
| finished_at | Timestamp nullable | |

### `user_git_credential`

| Field | Type | Description |
|-------|------|-------------|
| user_id | FK → user PK | 一位使用者一個 PAT |
| provider | Enum(github, gitlab) | v1 只用 github |
| encrypted_token | Bytea | `Fernet.encrypt()` 結果 |
| token_hint | String(10) | e.g. `ghp_...abcd`，UI 顯示用 |
| created_at / updated_at | Timestamp | |

加密金鑰由 K8s Secret `lolday-fernet-key` 注入 env `FERNET_KEY`。

### Alembic Migration

一支 migration 建完上述所有 table。含 soft-delete 的 partial unique index：

```sql
CREATE UNIQUE INDEX detector_owner_git_unique
ON detector (owner_id, git_url)
WHERE deleted_at IS NULL;
```

---

## API Endpoints

All prefixed `/api/v1`。權限用 Phase 2 既有 `require_role()` dep。

### Detector

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/detectors` | Developer+ | body `{git_url, name?}`；同步執行 clone + 靜態檢查 |
| GET | `/detectors` | User+ | 分頁；篩選 `?owner_id=`, `?search=` |
| GET | `/detectors/{id}` | User+ | 含 latest version 摘要 |
| PATCH | `/detectors/{id}` | Owner / Admin | `display_name`, `description` |
| DELETE | `/detectors/{id}` | Owner / Admin | soft delete + 背景清 Harbor |

### Version

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/detectors/{id}/versions` | User+ | 列已成功 build 版本 |
| GET | `/detectors/{id}/versions/{tag}` | User+ | 單版本詳情（含 `config_schema`） |
| GET | `/detectors/{id}/available-tags` | Developer+ | 即時問 GitHub API 列遠端 tag；ETag cache 30 秒 |

### Build

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/detectors/{id}/builds` | Owner / Admin | body `{git_tag}`；同 detector+tag 已 in-flight 回 409 + 現有 build id |
| GET | `/detectors/{id}/builds` | User+ | 分頁歷史 |
| GET | `/detectors/{id}/builds/{bid}` | User+ | 狀態 + failure_reason + log_tail |
| GET | `/detectors/{id}/builds/{bid}/logs` | User+ | 完整 log：Job 存活 → proxy K8s API；TTL 已過 → 回 log_tail + 410 |
| POST | `/detectors/{id}/builds/{bid}/cancel` | Owner / Admin | 刪 K8s Job，reconciler 標記 cancelled |

### Credential

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| PUT | `/users/me/git-credential` | Self | body `{provider, token}`，加密後 upsert |
| GET | `/users/me/git-credential` | Self | 回 `{provider, token_hint, updated_at}`，**不回**完整 token |
| DELETE | `/users/me/git-credential` | Self | 刪除 |

### Internal (build-scoped)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/internal/builds/{bid}/schema` | Build token | validate container 回傳 `model_json_schema()` 結果 |

Build token = build 開始時 backend 生成的 one-time token；Secret 注入 validate container；build 結束即失效（DB rows 自動清）。走獨立 `require_build_token` dep，不經 JWT auth。

---

## Build Pipeline

### K8s Job Structure

一個 Job = 一次 build。Pod 內三個 container：

```yaml
spec:
  activeDeadlineSeconds: 1200        # 20 分鐘硬超時
  ttlSecondsAfterFinished: 604800    # 7 天後自動刪
  backoffLimit: 0                    # 不自動 retry
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
        seccompProfile: { type: RuntimeDefault }

      volumes:
        - name: workspace
          emptyDir: { sizeLimit: 2Gi }
        - name: git-cred
          secret: { secretName: build-git-cred-{bid}, defaultMode: 0400 }
        - name: harbor-docker-cfg
          secret:
            secretName: harbor-push-cred
            items: [{ key: .dockerconfigjson, path: config.json }]
            defaultMode: 0400

      initContainers:
        - name: clone
          image: alpine/git:2.45
          command: [sh, -c, "git clone --depth=1 --recurse-submodules --branch=$GIT_TAG $URL /workspace/src && git -C /workspace/src rev-parse HEAD > /workspace/git-sha"]
          env:
            - { name: GIT_TAG,   value: "{{ tag }}" }
            - { name: URL,       value: "https://$(GIT_USER):$(GIT_TOKEN)@github.com/{{ owner/repo }}.git" }
            - { name: GIT_USER,  valueFrom: { secretKeyRef: { name: build-git-cred-{bid}, key: username } } }
            - { name: GIT_TOKEN, valueFrom: { secretKeyRef: { name: build-git-cred-{bid}, key: token } } }
          volumeMounts:
            - { name: workspace, mountPath: /workspace }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          resources:
            limits: { cpu: 500m, memory: 512Mi }

        - name: validate
          image: harbor.harbor.svc:80/lolday/build-helper:v1
          command: [python, -m, maldet_validator, /workspace/src]
          env:
            - { name: BUILD_ID,    value: "{{ bid }}" }
            - { name: BUILD_TOKEN, valueFrom: { secretKeyRef: { name: build-git-cred-{bid}, key: build_token } } }
            - { name: BACKEND_URL, value: "http://backend.lolday.svc:8000" }
          volumeMounts:
            - { name: workspace, mountPath: /workspace }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          resources:
            limits: { cpu: 1, memory: 1Gi }

      containers:
        - name: kaniko
          image: gcr.io/kaniko-project/executor:latest
          args:
            - --context=dir:///workspace/src
            - --dockerfile=Dockerfile
            - --destination=harbor.harbor.svc:80/detectors/{{ name }}:{{ tag }}
            - --cache=true
            - --cache-repo=harbor.harbor.svc:80/detectors-cache/{{ name }}
            - --cache-ttl=336h
            - --snapshot-mode=redo
            - --log-format=json
          volumeMounts:
            - { name: workspace, mountPath: /workspace, readOnly: true }
            - { name: harbor-docker-cfg, mountPath: /kaniko/.docker, readOnly: true }
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            capabilities: { drop: [ALL] }
          resources:
            requests: { cpu: 1, memory: 2Gi }
            limits:   { cpu: 2, memory: 4Gi }
```

### PAT 注入與洩漏防護

- 每次 build 建立一次性 Secret `build-git-cred-{bid}`（內含 username、token、build_token）
- Reconciler 在 Job 終結時立刻刪除該 Secret
- Clone 用 URL-embedded credential；git 不會把 URL 寫進 stdout，但 shell 保險加 `set +x`
- Log 收集後做 redact：`ghp_[A-Za-z0-9]{36}` pattern → `[REDACTED]`

### Static Validation（註冊階段）

輕量 AST / 檔案檢查，失敗即時回傳錯誤碼（UX 優先）：
- `credential_missing` — 呼叫者未設 PAT 且 repo 看起來為 private（GitHub API 回 404）
- `git_clone_failed` — PAT 錯、repo 不存在、或網路失敗
- `pyproject_missing` — repo 根目錄無 `pyproject.toml`
- `pyproject_unparseable` — `pyproject.toml` 無法解析為 TOML
- `base_detector_import_missing` — AST 掃不到 `from maldet import BaseDetector` 或等效
- `dockerfile_missing` — repo 根目錄無 `Dockerfile`（build 必需，早 fail 而非 build 階段才 fail）
- `repo_too_large` — clone 超過 500MB
- `duplicate_registration` — 同 owner + 同 git_url 已註冊

### Runtime Validation（build 階段）

在 `validate` init container 內：
1. `uv pip install /workspace/src` → 臨時 venv
2. 反射 import detector 類別：`python -c "from detector_pkg import *"` 找 subclass of `BaseDetector`
3. 確認 `issubclass(cls, BaseDetector)` 且 `cls.config_class` 繼承 `BaseDetectorConfig`
4. 呼叫 `cls.config_class.model_json_schema()` 取 schema
5. POST schema 回 backend（`/internal/builds/{bid}/schema`，帶 build token）
6. 失敗以 exit code + stdout 結構化錯誤碼輸出（`validation.missing_base_detector`, `validation.missing_train_method`, ...）

### Reconciler Loop

```python
async def build_reconciler():
    while not shutdown.is_set():
        in_flight = await db.get_builds(
            status__in=[pending, cloning, validating, building, scanning]
        )
        for b in in_flight:
            try:
                job = await k8s.read_job(b.k8s_job_name, namespace="lolday")
                pod = await k8s.get_job_pod(b.k8s_job_name)
                # 根據 initContainer 完成狀態更新 b.status（cloning/validating/building）
                if job.status.succeeded:
                    # Kaniko 完成 → 查 Harbor scan
                    scan = await harbor.get_scan(b.detector.name, b.git_tag)
                    if scan.status == "Success":
                        if scan.critical > 0 or scan.high > 0:
                            await harbor.delete_artifact(b.detector.name, b.git_tag)
                            await db.finalize_cve_blocked(b, scan)
                        else:
                            await db.finalize_success(b, scan)
                    # scan Pending/Running → 保持 scanning 狀態，下輪再查
                elif job.status.failed:
                    await db.finalize_failure(b, reason=extract_failure_reason(pod))
                elif _timeout(b):
                    await k8s.delete_job(b.k8s_job_name)
                    await db.finalize_timeout(b)
            except Exception:
                logger.exception("reconcile failed", build_id=b.id)
        await asyncio.sleep(10)
```

- 由 FastAPI lifespan 啟動 / 停止
- Backend Pod 重啟 → 從 DB 撈 in-flight build 重建追蹤狀態
- 失敗不影響其他 build（per-build try/except）

### NetworkPolicy（kube-router L3/L4）

```yaml
podSelector: { matchLabels: { app: lolday-build } }
policyTypes: [Egress]
egress:
  # DNS
  - to: [{ namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } } }]
    ports: [{ protocol: UDP, port: 53 }]
  # Harbor
  - to: [{ namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: harbor } } }]
  # Backend（validate container 回傳 schema 用）
  - to:
      - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: lolday } }
        podSelector: { matchLabels: { app.kubernetes.io/component: backend } }
    ports: [{ protocol: TCP, port: 8000 }]
  # 外網（GitHub、PyPI），排除 cluster-internal 其他 service
  - to:
      - ipBlock:
          cidr: 0.0.0.0/0
          except:
            - 10.42.0.0/16    # Flannel pod CIDR
            - 10.43.0.0/16    # Service CIDR
            - 192.168.0.0/16
            - 172.16.0.0/12
            - 169.254.0.0/16  # metadata
```

Phase 4 會為 training pod 設計更嚴格的 deny-all-egress policy（惡意 malware 不得出網）；build pod 不碰 malware sample，不需 FQDN filtering。

### Build Concurrency & Rate Limits

- Per-user 同時 in-flight build 上限 2（DB 查 + 前置檢查）
- slowapi 對 `POST /detectors/*/builds` 每分鐘 5 次 per IP

---

## Harbor Deployment

### Helm Sub-chart

```yaml
# charts/lolday/Chart.yaml
dependencies:
  - name: harbor
    version: "1.16.1"   # 2026-04 穩定版
    repository: https://helm.goharbor.io
    condition: harbor.enabled
```

### values.yaml

```yaml
harbor:
  enabled: true
  expose:
    type: clusterIP
    tls: { enabled: false }   # cluster-internal 單節點不需 TLS
  externalURL: http://harbor.harbor.svc.cluster.local:80  # 與 Pod 側 / kubelet 側解析一致
  persistence:
    enabled: true
    persistentVolumeClaim:
      registry:   { size: 100Gi }
      jobservice: { size: 2Gi }
      database:   { size: 5Gi }
      redis:      { size: 2Gi }
      trivy:      { size: 10Gi }
  trivy: { enabled: true, skipUpdate: false }
  notary: { enabled: false }
  chartmuseum: { enabled: false }
  harborAdminPassword: ""     # --set 帶入
  resources:
    core:       { requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 1, memory: 1Gi} }
    jobservice: { requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 1, memory: 1Gi} }
    registry:   { requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 1, memory: 2Gi} }
    portal:     { requests: {cpu: 50m,  memory: 64Mi},  limits: {cpu: 500m, memory: 256Mi} }
    database:   { requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 1, memory: 1Gi} }
    redis:      { requests: {cpu: 50m,  memory: 64Mi},  limits: {cpu: 500m, memory: 256Mi} }
    trivy:      { requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 1, memory: 1Gi} }
```

**總資源預估：requests ≈ 0.6 CPU / 1.5 Gi；limits ≈ 6 CPU / 7.5 Gi。單節點（RTX 2080 Ti × 2）足夠。**

### Post-install Initialization

Backend lifespan 呼叫 Harbor admin API 做 idempotent 初始化：

1. Projects：`detectors`（private）、`detectors-cache`、`lolday`
2. Robot account `robot$build-pusher`：push/pull `detectors/*`、`detectors-cache/*`
   - Token 存 K8s Secret `harbor-push-cred`（docker config.json 格式）
3. Retention policy：`detectors/*` 保留最新 3 個 tag；`detectors-cache/*` 保留 14 天
4. Vulnerability scanner：Trivy（內建，設為預設），auto-scan on push 啟用

### Harbor Hostname 與 Access Path

**所有 image reference 使用 Harbor 真實的 Kubernetes Service DNS：`harbor.harbor.svc:80`**。

前置約束：Helm release 名固定為 `harbor`、namespace 固定為 `harbor`（由 `scripts/deploy.sh` 強制）。Harbor Helm chart 在 `expose.type: clusterIP` 模式下會產生同名 `harbor` 主 Service，其內部再 fan-out 到 `harbor-core` / `harbor-portal`。

兩類來源對應不同解析機制：

| 來源 | 路徑 |
|------|------|
| Pod 內（Kaniko push、validate container 、backend 呼叫 Harbor API）| CoreDNS 解析 `harbor.harbor.svc` → Service ClusterIP → kube-proxy 轉發到 Pod。**開箱即用**，不需額外設定。 |
| Kubelet / containerd（image pull） | node host process 不走 CoreDNS，需 `/etc/rancher/k3s/registries.yaml` mirror 設定將 `harbor.harbor.svc:80` 映射到 Service ClusterIP（kube-proxy 在 node 已設 iptables routing，host 可直連 ClusterIP） |

`/etc/rancher/k3s/registries.yaml`（部署後由 patch 腳本動態填入實際 ClusterIP）：

```yaml
mirrors:
  "harbor.harbor.svc:80":
    endpoint: ["http://<harbor-service-clusterIP>:80"]
```

由 **使用者手動執行** `scripts/patch-k3s-registries.sh`（需 sudo）。腳本行為：
1. `kubectl get svc -n harbor harbor -o jsonpath='{.spec.clusterIP}'` 讀出 ClusterIP
2. 備份 `/etc/rancher/k3s/registries.yaml` → `.bak.{timestamp}`
3. Dry-run 比對差異、提示使用者確認
4. 寫入、重啟 `k3s` 服務（SSH safety：重啟失敗保留 .bak、腳本結尾驗證 `systemctl is-active k3s`）
5. 失敗自動 rollback

符合 Cilium incident memory 的 SSH safety safeguards。

### Harbor Project 可見性

Phase 3 所有 Harbor project **設為 public**：

- **Anonymous pull 允許** — kubelet 拉 image 不需 `imagePullSecrets`，Pod spec 清爽
- **Push 仍需 robot account** — 只有 `robot$build-pusher` 能推（push 才是敏感操作）
- Harbor 外沒有公開 endpoint（clusterIP only），「public」只意味 cluster-internal 匿名可讀

符合「不過度設計」— cluster 內部 image 沒有隔離需求，讓 pull 變簡單。未來若多團隊共用，再改 private + 發 pull secret。

### 遷移既有 backend image

Phase 2 backend 原本在 `localhost:5000/lolday-backend:latest`。Phase 3 部署 Harbor 後：
1. 重新 build + push 到 `harbor.harbor.svc:80/lolday/lolday-backend:latest`
2. 更新 backend Deployment 使用新 image
3. `registry:2` deployment flag 關閉，空間回收

---

## JSON Schema Handling

**Pydantic v2 的 `model_json_schema()` 輸出 JSON Schema Draft 2020-12。**

Phase 3 只負責**提取並儲存**：validate container 呼叫 `MyDetector.config_class.model_json_schema()`，將原生輸出 POST 回 backend，存入 `detector_version.config_schema` JSONB 欄位。不做任何轉換。

**轉換 / 渲染由 Phase 5（Frontend）負責決定。** 屆時的選項：
- RJSF v5 + `Ajv2020` class（無轉換，但官方標註「breaking changes、未完整測試」）
- RJSF v5 + Draft 7 normalizer（lolday 內 60 行轉換）
- JSONForms（AJV 原生支援 Draft 2020-12，較小社群但企業等級維護）
- 等 RJSF v6 穩定（時程不明）

Phase 3 不提前決定，符合 YAGNI。

---

## Backend Code Structure

Phase 2 的單檔 models.py / schemas.py 在 Phase 3 拆開：

```
backend/app/
├── main.py                  # 加 reconciler lifespan + include_router
├── config.py                # 加 HARBOR_URL, FERNET_KEY, GITHUB_API_URL, BUILD_* 設定
├── db.py                    # 不動
├── deps.py                  # 加 require_detector_access, require_build_token
├── users.py                 # 不動
│
├── models/                  # 拆分
│   ├── __init__.py          # re-export
│   ├── user.py              # User, Role
│   ├── detector.py          # Detector, DetectorVersion, DetectorBuild
│   └── credential.py        # UserGitCredential
│
├── schemas/
│   ├── __init__.py
│   ├── user.py
│   ├── detector.py
│   └── credential.py
│
├── routers/
│   ├── admin.py             # 既有
│   ├── detectors.py         # NEW
│   ├── credentials.py       # NEW
│   └── internal.py          # NEW：build token schema callback
│
├── services/                # NEW
│   ├── __init__.py
│   ├── git.py               # URL 正規化、git ls-remote、GitHub API tag 列表
│   ├── validator.py         # 靜態 AST 檢查
│   ├── harbor.py            # Harbor REST client
│   ├── build.py             # K8s Job spec 生成、Secret 建立/清理
│   ├── crypto.py            # Fernet wrapper
│   └── k8s.py               # K8s client 單例
│
└── reconciler.py            # NEW：asyncio 背景 loop
```

### Libraries

| 用途 | Library | 理由 |
|------|---------|------|
| K8s API | `kubernetes` (官方 Python client) | 官方維護、async 支援 |
| Harbor API | `httpx` | Harbor REST 簡單，不引第三方 client |
| 加密 | `cryptography` 的 `Fernet` | Python 標配 |
| Git 操作 | `subprocess` + `git` CLI | 只需 ls-remote、讀 pyproject；不引 GitPython |
| GitHub API | `httpx` | 不引 PyGithub（大而 sync-only） |
| AST 檢查 | stdlib `ast` | 量小、無依賴 |

### Build Helper Image

`charts/lolday/helpers/build-helper/` — 自製小 image（約 150 行 Python），推到 `harbor.harbor.svc:80/lolday/build-helper:v1`。內容：python:3.12-slim + git + uv + maldet_validator.py。每次 Phase 3 升級手動 bump version tag。

### Backend RBAC（namespace-scoped Role）

```yaml
# charts/lolday/templates/backend-rbac.yaml
Role:
  - apiGroups: [""]
    resources: [pods, pods/log]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, delete]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
```

RoleBinding 綁 `lolday` namespace。backend SA 權限**僅在 lolday 內**。動不了 `kube-system`、host、其他 namespace → SSH 不受威脅。

---

## Security Summary

| 威脅 | 控制點 |
|------|--------|
| 惡意 detector → build 逃逸 | non-root、non-privileged、drop ALL caps、readOnlyRootFilesystem、seccomp RuntimeDefault、SA token 不掛、activeDeadlineSeconds 20 分鐘 |
| 惡意 detector → 帶 CVE 的 image 被用到 | Harbor Trivy on-push 掃；Critical/High → 自動刪 artifact + DB 標 `cve_blocked` |
| 惡意 detector → 攻擊 cluster 內服務 | NetworkPolicy：build pod egress 限 Harbor、backend:8000、DNS、外網；擋其他 cluster-internal |
| PAT 洩漏 | Fernet 加密存 DB；build Secret 一次性、Job 結束即刪；log redact ghp_* pattern |
| backend 被攻陷橫向擴散 | namespace-scoped Role；動不了 kube-system/host/其他 ns |
| Phase 2 的 privilege escalation 回歸 | UserUpdate 不含 role 欄位；Admin 改 role 走獨立 `AdminUserUpdate`（Phase 2 既有修補） |
| Build 洪水攻擊 | per-user 2 個 in-flight 上限；slowapi 5 req/min |
| 過期 Secret 堆積 | Reconciler finalize 時同步刪；每日 CronJob 清孤兒 Secret |
| SSH 中斷 | Phase 3 不動 CNI；Harbor clusterIP only；registries.yaml patch 由使用者手動跑 + 備份 + rollback |

### Audit Log

stdout logger 輸出（Phase 6 Loki 會撈），不含敏感內容：

```
AUDIT detector.register      user=<id> git_url=<url> result=<ok|rejected>
AUDIT detector.build.start   user=<id> detector=<id> tag=<tag> build=<id>
AUDIT detector.build.finish  build=<id> status=<succeeded|failed|timeout|cve_blocked>
AUDIT detector.delete        user=<id> detector=<id>
AUDIT credential.update      user=<id> provider=github action=<set|delete>
```

### Fernet Key Management

- 一次性 `Fernet.generate_key()`；存 K8s Secret `lolday-fernet-key`
- v1 不做 key rotation（單一 key）；未來以 `MultiFernet` 漸進旋轉
- 備份：Phase 6 跟 PostgreSQL 一起走 R2
- 遺失 = 所有 PAT 需重新輸入（acceptable）

---

## Testing Strategy

### 單元測試（pytest，無 K8s）

| 模組 | 重點 |
|------|------|
| `services/git.py` | URL 正規化（所有輸入形式同一 HTTPS）、非法 URL 拒絕 |
| `services/validator.py` | AST 檢查：missing pyproject / BaseDetector / method；upxelfdet fixture 通過 |
| `services/crypto.py` | encrypt/decrypt round-trip；wrong key → InvalidToken |
| `services/build.py` | Job spec 正確性：Secret 名、env、resource limit、SecurityContext |
| `services/harbor.py` | 以 `respx` mock Harbor API，驗 request payload |
| `reconciler.py` | State transitions；timeout、CVE blocked、cancelled 分支 |

### 整合測試

- FastAPI TestClient + aiosqlite + mocked K8s client（`unittest.mock` override `kubernetes.client.BatchV1Api`）
- 覆蓋：註冊 → build → 查狀態 → 取消 → 刪除
- 失敗路徑：PAT 缺失、tag 不存在、concurrent 同 tag 409

### E2E 測試（手動）

- Fixture：`upxelfdet` repo（owner bolin8017）
- 流程：註冊 → build v0.1.0 → 驗 Harbor 有 image → schema 存 DB → `GET /versions` 拿到正確資料
- 每次 PR merge 前手動跑一遍，記錄於 `docs/phase3-e2e-checklist.md`

### Kaniko / Harbor Smoke Test

- Harbor 起來後跑最小 Dockerfile 的 Kaniko Job，確認 push + Trivy scan 觸發
- 不列 CI，列部署驗收步驟

---

## Helm / Deployment

### values.yaml 新增

```yaml
harbor:
  enabled: true
  # ... 見 Harbor Deployment 段

backend:
  env:
    HARBOR_URL: http://harbor.harbor.svc:80
    HARBOR_ADMIN_USERNAME: admin
    GITHUB_API_URL: https://api.github.com
    BUILD_NAMESPACE: lolday
    BUILD_IMAGE_HELPER: harbor.harbor.svc:80/lolday/build-helper:v1
    BUILD_IMAGE_KANIKO: gcr.io/kaniko-project/executor:latest
    BUILD_TIMEOUT_SECONDS: "1200"
    BUILD_CONCURRENCY_PER_USER: "2"
  secrets:
    fernetKey: ""             # --set，NEVER commit
    harborAdminPassword: ""   # --set

registry:
  enabled: false              # Harbor 接手後關閉 registry:2
```

### 新增 template

```
charts/lolday/templates/
├── backend-rbac.yaml            # Role + RoleBinding + ServiceAccount
├── backend-fernet-secret.yaml   # from --set
├── harbor-admin-secret.yaml     # from --set
├── build-networkpolicy.yaml     # 給 build pod
├── harbor-init-job.yaml         # post-install hook（初始化 projects / robot / retention）
└── (registry.yaml 保留但 flag 關閉)
```

### 部署腳本更新

`scripts/deploy.sh`：
1. `helm repo add harbor https://helm.goharbor.io && helm repo update`
2. `helm dependency build charts/lolday`（拉 Harbor sub-chart 到 charts/）
3. `helm upgrade --install lolday ...`（帶 `--set harbor.harborAdminPassword=...`、`--set backend.secrets.fernetKey=...`）
4. 部署完提示：**「請手動執行 `sudo bash scripts/patch-k3s-registries.sh`」**

`scripts/patch-k3s-registries.sh`（新）：
- Idempotent 加 Harbor 條目
- patch 前備份 `/etc/rancher/k3s/registries.yaml` → `.bak.{timestamp}`
- Dry-run diff 給使用者確認
- 失敗時自動從 `.bak` 還原
- 符合 Cilium incident memory 的 SSH safety safeguards

### Rollback

- Harbor 部署失敗 → `helm rollback lolday` 回 Phase 2；registries.yaml 從 .bak 還原
- Fernet key 遺失 → `user_git_credential` 全部 reset，使用者重輸 PAT
- Harbor storage 爆 → 手動 clean retention + 加大 PVC

---

## Decisions & Amendments

以下為 Phase 3 設計階段作出的關鍵決策，與主 spec (`2026-03-30-lolday-platform-design.md`) 的 amendments。未來翻查請以本節為準。

### A1. 維持 Flannel + kube-router，廢除 Cilium 計畫

**原 spec §8.1** 指定 Cilium 作為 CNI，理由為支援 L7/FQDN NetworkPolicy。

**Amendment：** Phase 3 及後續維持 K3s 預設 Flannel + kube-router。

**理由：**
- 2026-03-31 Cilium eBPF 曾導致 SSH 中斷（見 memory `project_cilium_ssh_incident.md`）
- 實際需求分析：training pod 根本不需外網（讀 NFS、寫 PV、只跟 cluster-internal MLflow 通訊），blanket deny egress 即可阻擋 exfiltration + DNS tunneling，**不需 L7 FQDN policy**
- Cilium 重要 features（FQDN policy、Hubble）是錦上添花非必要
- kube-router L3/L4 policy 足以覆蓋 build pod 與 training pod 的安全需求
- 符合「don't break SSH」、「don't over-engineer」原則

**Trade-off：** 失去 FQDN-based egress filtering、Hubble 觀測性。可接受。

### A2. Cluster-internal 流量用 HTTP，HTTPS 在邊界終止

**原 spec §9.1** 暗示全鏈路 HTTPS。

**Amendment：** Harbor、backend 等 cluster-internal 服務用 HTTP；外部存取透過 Phase 6 Cloudflare Tunnel 自動加 HTTPS。

**理由：**
- 單節點下 cluster-internal 流量走 host veth + bridge，不離開實體機器
- HTTPS 對 cluster-internal 需自行管 cert / renewal / trust store，維運成本高、安全收益 0
- 主流做法（K3s、Argo、GitLab Runner）皆是 cluster-internal HTTP + edge TLS 終止
- 未來多節點時再評估 mTLS / service mesh

### A3. JSON Schema 轉換延到 Phase 5

**原 spec §4.4** 提到需 Draft 2020-12 → Draft 7 normalizer。

**Amendment：** Phase 3 不做 normalizer，直接存 Pydantic 原生輸出。轉換決策延至 Phase 5。

**理由：**
- Phase 3 是 backend，前端未啟動，不知實際會碰到什麼渲染問題
- 提前實作 normalizer 屬於 premature optimization
- Phase 5 時可能選 JSONForms 或 RJSF + Ajv2020 而完全不需 normalizer
- 原始 Pydantic schema 存 JSONB 欄位，隨時可再處理

### A4. registry:2 由 Harbor 取代

**原 spec §2 tech stack** 已指定 Harbor；Phase 2 用 registry:2 是臨時措施。

**Amendment：** Phase 3 部署 Harbor，Phase 2 的 `registry:2` deployment 在遷移後關閉。

**理由：** Harbor 內建 Trivy / retention / robot account / webhook，取代自寫 CronJob + Trivy CLI glue code；符合「open-source > custom code」原則。

### A5. 使用 K8s Job + 背景 reconciler，Phase 3 不引 Celery

**原 spec §2 tech stack** 有 Celery；Phase 3 時 Celery 尚未部署。

**Amendment：** Phase 3 build orchestration 用 K8s Job + asyncio reconciler，不引 Celery。Celery 留到 Phase 4 針對 train/eval/predict 需求（fair-share queue）再評估。

**理由：**
- Build pipeline 為單一線性流程，K8s Job 原生 retry / timeout / resource limit 足夠
- K8s 本身即 job queue，無需多裝 Celery
- YAGNI：需要時再加

### A6. 不使用 Cloudflare Tunnel / 不對外暴露，延至 Phase 6

**原 spec §8.4** 提 Cloudflared replicas=2。

**Amendment：** Phase 3 僅 cluster-internal 運作；外部存取延至 Phase 6 Operations 階段一併部署。Phase 3 的測試透過 `kubectl port-forward`。

**理由：** server30 port 6443/10250 已 expose 的風險已知（見 project overview memory）；Phase 3 不擴大 attack surface。

### A7. Runtime validation 用 build-scoped one-time token callback，不用 K8s SA

**新增決策：** validate container 不掛 K8s ServiceAccount token（automountServiceAccountToken: false）。改用 backend 生成 build-scoped one-time token，注入 Secret，container 結束即作廢。

**理由：** 更嚴格的最小權限；降低 token 洩漏爆炸半徑至單一 build。

---

## Phase Roadmap Touchpoints

| Phase | 名稱 | 狀態 | Phase 3 對其影響 |
|-------|------|------|------------------|
| 1 | Infrastructure Foundation | ✅ Complete | 不影響 |
| 2 | Backend Core | ✅ Complete | models.py / schemas.py 拆分 |
| 3 | Detector Lifecycle | **Current** | — |
| 4 | Dataset & Jobs | Pending | 延續 Phase 3 的 Harbor + detector_version；train/eval/predict Pod 用 detector image |
| 5 | Frontend | Pending | 承接 `config_schema` 選擇 form library（RJSF / JSONForms）；build log streaming UI |
| 6 | Operations | Pending | Cloudflare Tunnel / Resend email / Loki / 備份 R2；Harbor webhook 若要啟用也在此階段 |
