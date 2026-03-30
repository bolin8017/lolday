# Lolday Platform — System Design Specification

## Overview

Lolday is an internal ML platform for ISLab (Information Security Lab) that manages, trains, and runs malware detectors built on the [maldet](https://github.com/bolin8017/islab-malware-detector) spec. Lab members register detectors via Git repos, and the platform provides a web UI for training, evaluating, and predicting with those detectors on shared malware datasets.

**Core goals:**
- Collect all lab members' detectors in one place
- Provide a web UI to train, evaluate, and predict without touching CLI
- GPU scheduling and fair resource sharing
- Reproducible experiments with full tracking
- Docker-based deployment, easy to migrate or scale

**Design principles:**
- Use open-source, actively maintained projects over custom code
- Only write custom code for the maldet-spec glue layer and platform-specific logic
- Every component should be independently replaceable

---

## 1. User Roles & Permissions (RBAC)

Three roles with increasing privilege:

| Operation | Admin | Developer | User |
|-----------|:-----:|:---------:|:----:|
| Manage user accounts | ✅ | ❌ | ❌ |
| Manage platform settings | ✅ | ❌ | ❌ |
| View system monitoring (Grafana) | ✅ | ❌ | ❌ |
| Register detector (Git URL) | ✅ | ✅ | ❌ |
| Update/delete own detector | ✅ | ✅ | ❌ |
| Trigger detector build | ✅ | ✅ | ❌ |
| Create dataset config | ✅ | ✅ | ✅ |
| Run train / evaluate / predict | ✅ | ✅ | ✅ |
| View public experiments/models | ✅ | ✅ | ✅ |
| Mark own models/experiments private | ✅ | ✅ | ✅ |
| Download prediction results | ✅ | ✅ | ✅ |
| Manage Model Registry state (Staging→Production) | ✅ | ✅ (own) | ❌ |

Authentication: FastAPI Users with JWT (bcrypt password hashing, token expiry + refresh rotation).

---

## 2. Tech Stack

| Category | Component | Tool |
|----------|-----------|------|
| Frontend | Web UI | React |
| | Dynamic config forms | react-jsonschema-form |
| Backend | API server | FastAPI |
| | Auth & user management | FastAPI Users (JWT + RBAC) |
| | Rate limiting | slowapi |
| | API versioning | `/api/v1/` prefix |
| Job Queue | Broker | Redis |
| | Workers | Celery (3 separate queues: train, eval, predict) |
| Database | Primary | PostgreSQL |
| ML Tracking | Experiment tracking | MLflow |
| | Model registry | MLflow Model Registry (Staging → Production → Archived) |
| Container Registry | Image storage | Harbor |
| GPU & Scheduling | GPU management | NVIDIA GPU Operator |
| | Batch scheduling | Volcano (fair-share, queue-based) |
| Container Orchestration | Cluster | K3s |
| | Package management | Helm |
| Network | CNI (NetworkPolicy support) | Cilium (replaces Flannel) |
| | Ingress | Traefik (K3s built-in) |
| | External access | Cloudflare Tunnel |
| | Access control | Cloudflare Access (Zero Trust) |
| | Domain & DNS | Cloudflare Registrar |
| | HTTPS | Cloudflare (automatic) |
| Monitoring | Metrics collection | Prometheus |
| | Alerting | Alertmanager |
| | Dashboards | Grafana |
| | GPU metrics | DCGM Exporter |
| | Log aggregation | Loki |
| Security | Image scanning | Trivy Operator |
| | Container hardening | K8s SecurityContext + Seccomp |
| | Network isolation | Cilium NetworkPolicy |
| | Secrets | K8s Secrets + etcd encryption at rest |
| Notifications | Email | Resend (free tier: 3,000/month) |
| | Future | Discord webhook |
| Backup | DB backup | pg_dump CronJob → Cloudflare R2 |
| | K8s state | k3s etcd-snapshot → Cloudflare R2 |
| | ML artifacts | rsync CronJob → Cloudflare R2 |

---

## 3. System Architecture

```
Users (Browser)
  │ HTTPS
  ▼
┌─────────────────────────────────────┐
│ Cloudflare                          │
│ DNS + Access (Zero Trust) + Tunnel  │
│ + DDoS Protection                   │
└─────────────┬───────────────────────┘
              │ Encrypted Tunnel
              ▼
┌─────────────────────────────────────────────────────────┐
│ K3s Cluster                                             │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Traefik Ingress → Route requests to services        │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌──────────────────┐  ┌──────────────────┐              │
│ │ React Frontend   │  │ FastAPI Backend   │              │
│ │ + RJSF forms     │  │ + FastAPI Users   │              │
│ └──────────────────┘  └──────────────────┘              │
│                                                         │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐            │
│ │ Celery     │ │ MLflow     │ │ PostgreSQL │            │
│ │ + Redis    │ │ Server     │ │            │            │
│ └────────────┘ └────────────┘ └────────────┘            │
│                                                         │
│ ┌────────────────────┐ ┌────────────────────┐           │
│ │ Volcano + NVIDIA   │ │ Trivy Operator     │           │
│ │ GPU Operator       │ │ + SecurityContext   │           │
│ └────────────────────┘ └────────────────────┘           │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Monitoring: Prometheus + Alertmanager + Grafana     │ │
│ │ + DCGM Exporter + Loki                              │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌──────────────────┐  ┌──────────────────┐              │
│ │ NFS Mount        │  │ Persistent       │              │
│ │ (read-only,      │  │ Volumes          │              │
│ │  noexec)         │  │ (models, DB,     │              │
│ │ 300GB+ datasets  │  │  artifacts)      │              │
│ └──────────────────┘  └──────────────────┘              │
└─────────────────────────────────────────────────────────┘
              │                        │
    ┌─────────┴────────┐     ┌────────┴────────┐
    │ Git Repos        │     │ Resend API      │
    │ (Detectors)      │     │ (Email)         │
    └──────────────────┘     └─────────────────┘
```

---

## 4. Detector Lifecycle

### 4.1 Registration

1. Developer enters Git repo URL on the platform
2. Platform clones the repo
3. Validates maldet spec compliance:
   - Inherits from `BaseDetector`
   - Implements `train()`, `evaluate()`, `predict()`
   - Has `pyproject.toml`
   - `config_class` inherits from `BaseDetectorConfig`
4. Validation passes → detector registered, Git tags listed as available versions

### 4.2 Build Pipeline (Sandboxed)

1. Developer selects a Git tag to build
2. Build runs in an isolated Pod (restricted network, resource limits)
3. Checkout the specified tag
4. `docker build` inside the sandbox
5. Trivy scans the resulting image
   - Critical/High CVE → build blocked, developer notified
   - Pass → image pushed to Harbor
6. Extract JSON Schema from the detector's `config_class` (via `model_json_schema()`)
7. Store schema in PostgreSQL for frontend form rendering
8. Record Git tag + commit SHA (SHA is immutable, tags can be force-pushed)

### 4.3 Version Management

- Each Git tag = one detector version
- Harbor retains the last 3 image versions per detector; older images auto-cleaned
- Users select detector + version when submitting jobs
- MLflow records the exact commit SHA with each experiment

### 4.4 JSON Schema Compatibility

Pydantic v2 generates JSON Schema Draft 2020-12; react-jsonschema-form supports Draft 7. A schema normalization layer in the backend converts Draft 2020-12 → Draft 7 during the build step before storing in the database.

---

## 5. Dataset Management

### 5.1 Storage Architecture

- Raw malware binaries stored on NFS server (external, not managed by lolday)
- K3s mounts NFS via NFS CSI Driver as read-only with noexec
- Platform only manages Dataset Configs — small CSV manifests that reference files on NFS

### 5.2 Dataset Config Data Model (PostgreSQL)

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | string | User-assigned name, e.g., "UPX-ELF-v3-balanced" |
| description | text | Description |
| owner_id | FK → User | Creator |
| visibility | enum | public (default) / private |
| csv_content | text | Raw CSV content (file_name, label, family) |
| file_count | int | Auto-computed sample count |
| label_distribution | JSON | Auto-computed label distribution |
| checksum | string | SHA256 of the CSV content |
| created_at | timestamp | Creation time |
| updated_at | timestamp | Last update time |

### 5.3 User Operations

- **Create**: Upload CSV → platform validates all files exist on NFS, computes statistics
- **Browse**: View name, description, sample count, label distribution chart
- **Clone**: Copy someone's public config, modify, save as own
- **Use**: Select a config as input for train/evaluate/predict jobs

### 5.4 Integrity Verification

- On creation: validate CSV format, check all referenced files exist on NFS, compute SHA256 of CSV content
- On each use: re-verify CSV checksum; warn if manifest has changed since creation
- Note: checksum covers the CSV manifest, not the underlying NFS binaries (checksumming 300GB+ on every use is impractical). NFS files are assumed stable since the mount is read-only.

---

## 6. Training / Evaluate / Predict Workflow

### 6.1 User Submission

1. Select detector + version
2. Select dataset config
3. Fill hyperparameters via dynamic form (rendered from JSON Schema)
4. For predict: additionally select a trained model
5. Submit

### 6.2 Backend Processing

1. Validate dataset config integrity (checksum + file existence)
2. Idempotency check (same detector + dataset + params within 5 minutes → reject duplicate; window is configurable)
3. Display current GPU status ("GPU: 2/4 in use, 1 job queued")
4. Route to the appropriate Celery queue:
   - `train_queue` for training jobs
   - `eval_queue` for evaluation jobs
   - `predict_queue` for prediction jobs (shorter jobs not blocked by long training)

### 6.3 Volcano Scheduling

1. Fair-share scheduling across users
2. When GPU available → launch Pod with:
   - Detector image from Harbor
   - NFS dataset mount (read-only, noexec)
   - PV for output artifacts
   - Resource limits (CPU/RAM/GPU/disk)
   - SecurityContext: non-root, read-only fs, drop ALL capabilities, no privilege escalation
   - Seccomp profile
   - Cilium NetworkPolicy: no external network, no K8s API access, no DNS tunneling
   - Service account token automount disabled

### 6.4 Execution

1. Platform wrapper invokes detector's `train()` / `evaluate()` / `predict()`
2. Automatic MLflow logging:
   - **Parameters**: hyperparams, detector version, commit SHA, dataset config ID
   - **Metrics**: accuracy, precision, recall, F1, confusion matrix
   - **Artifacts**: model files, prediction CSV, feature vectors
   - **Tags**: executor, job type, duration, GPU model
3. Logs streamed to Loki; users can view real-time log output in UI

### 6.5 Completion

1. Job status updated → completed / failed
2. Email notification via Resend
3. Results page: metrics + download links
4. Trained models auto-registered in MLflow Model Registry with Staging status
   - Developer/Admin manually promotes to Production after review
   - Production models are the default selection for predict jobs

---

## 7. Results & Model Management

### 7.1 MLflow Tracking

Every job automatically records:

| Category | Content |
|----------|---------|
| Parameters | Hyperparams, detector version, commit SHA, dataset config ID |
| Metrics | Accuracy, Precision, Recall, F1, Confusion Matrix |
| Artifacts | Model files, prediction CSV, feature vectors |
| Tags | Executor, job type, duration, GPU model |

### 7.2 Model Registry Lifecycle

```
Train completes → auto-register as Staging
  → Developer/Admin reviews metrics
  → Promote to Production (default for predict)
  → Eventually replaced → Archived (still viewable, not in selection menus)
```

### 7.3 Visibility

- Default: public to all lab members
- Can be marked private (only owner + Admin can see)
- Production models are always visible (cannot be set to private)

### 7.4 User Features

- Single experiment view: metrics, confusion matrix chart, hyperparams table, download model/CSV
- Cross-experiment comparison: select multiple experiments, side-by-side metric comparison (MLflow built-in)
- Model list: categorized by detector, showing Production / Staging / Archived status

---

## 8. Infrastructure & Deployment

### 8.1 Deployment Flow

```bash
# 1. Install K3s (no Flannel — Cilium will handle networking)
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--flannel-backend=none --disable-network-policy" sh -

# 2. Install Cilium (CNI with NetworkPolicy support)
cilium install

# 3. Deploy entire platform
helm install lolday ./charts/lolday
```

### 8.2 Helm Chart Contents

| Category | Component | K8s Resource Type |
|----------|-----------|-------------------|
| Application | FastAPI Backend | Deployment + Service |
| | React Frontend | Deployment + Service |
| | Celery Workers (×3 queues) | Deployment |
| Data | PostgreSQL | StatefulSet + PV |
| | Redis | Deployment |
| | MLflow Server | Deployment + PV |
| | Harbor Registry | Helm sub-chart |
| GPU & Scheduling | NVIDIA GPU Operator | Helm sub-chart |
| | Volcano Scheduler | Helm sub-chart |
| Network | Cloudflared Tunnel | Deployment (replicas: 2) |
| Monitoring | kube-prometheus-stack | Helm sub-chart (Prometheus + Alertmanager + Grafana) |
| | DCGM Exporter | DaemonSet |
| | Loki | Helm sub-chart |
| Security | Trivy Operator | Helm sub-chart |
| Storage | NFS CSI Driver | Helm sub-chart |

### 8.3 Scaling to Multiple Nodes

```bash
# On a new machine:
curl -sfL https://get.k3s.io | K3S_URL=https://<master-ip>:6443 \
  K3S_TOKEN=<token> INSTALL_K3S_EXEC="--flannel-backend=none" sh -
# Cilium auto-extends to new node
# Volcano auto-schedules GPU jobs to new node
```

### 8.4 Key Configuration

- Cloudflared runs as Deployment with 2 replicas (HA, no single point of failure)
- PostgreSQL uses PV for persistence
- Harbor and MLflow artifacts each use independent PVs
- All StatefulSets use nodeSelector (pinned for future multi-node stability)

---

## 9. Security

### 9.1 Security Layers

| Layer | Measure | Implementation |
|-------|---------|----------------|
| Network entry | Cloudflare Access Zero Trust | IP range / email domain restriction + DDoS |
| Transport | HTTPS | Cloudflare automatic |
| Authentication | JWT + bcrypt | FastAPI Users, token expiry + refresh rotation |
| Authorization | RBAC (3 roles) | Custom middleware on FastAPI |
| API protection | Rate limiting | slowapi middleware |
| Build isolation | Sandbox Pod | Isolated Pod with restricted network + resources |
| Image scanning | Trivy Operator | Post-build scan; Critical/High CVE blocks Harbor push |
| Container runtime | SecurityContext | non-root, read-only fs, drop ALL capabilities, no privilege escalation |
| Syscall restriction | Seccomp profile | Restrict available system calls |
| Network isolation | Cilium NetworkPolicy | Training Pods: no external access, no K8s API, no DNS tunneling |
| NFS protection | Mount options | read-only + noexec (prevent malware execution) |
| Secrets | K8s Secrets + etcd encryption | Encryption at rest enabled |
| Service accounts | Disable automount | Training Pods do not receive SA tokens |
| Audit | Operation logs | Login, role changes, job submit/complete, model state changes → Loki |

### 9.2 Malware-Specific Considerations

- Training Pods can only read malware samples on NFS, never execute them
- Training Pods have no external network access (including DNS tunneling blocked by Cilium)
- Build pipeline runs in sandbox with equal isolation to runtime

---

## 10. Monitoring & Observability

### 10.1 Tool Stack

| Tool | Purpose | Audience |
|------|---------|----------|
| Prometheus | Metrics collection | System |
| Alertmanager | Alert notifications | Admin (via email) |
| Grafana | Dashboards | Admin |
| DCGM Exporter | GPU metrics | Admin |
| Loki | Log aggregation | Admin + Users (own job logs) |

### 10.2 Alert Rules

| Alert | Condition | Severity |
|-------|-----------|----------|
| Node disk almost full | > 85% | Critical |
| GPU temperature too high | > 85°C | Critical |
| PostgreSQL down | health check fail | Critical |
| NFS mount failure | mount point unreadable | Critical |
| Cloudflare Tunnel disconnected | both replicas down | Critical |
| Backup job failed | CronJob exit ≠ 0 | High |
| PV usage high | > 80% | High |
| Job queued too long | > 2 hours | Warning |
| Harbor storage low | > 80% | Warning |

### 10.3 User-Facing UI

- Job list: Queued → Running → Completed/Failed
- Real-time log output (streamed from Loki)
- GPU status: "GPU 0: In use (user_a training) / GPU 1: Idle"
- Queue position: "Your job is #2 in queue"

### 10.4 Admin Grafana Dashboards

- GPU utilization / memory / temperature (DCGM dashboard template)
- CPU / RAM / Disk trends
- Job completion rate, average duration
- Per-user GPU usage time statistics

---

## 11. Notifications

| Event | Recipient | Channel |
|-------|-----------|---------|
| Job completed | Submitter | Email (Resend) |
| Job failed | Submitter | Email (Resend) |
| Detector build completed/failed | Developer | Email (Resend) |
| Trivy scan blocked | Developer | Email (Resend) |
| System alert | Admin | Email (Alertmanager) |
| Future expansion | All | Discord webhook |

---

## 12. Backup & Disaster Recovery

### 12.1 Backup Strategy

| Data | Method | Frequency | Retention | Destination |
|------|--------|-----------|-----------|-------------|
| PostgreSQL | pg_dump CronJob | Every 6 hours | 7 days | Cloudflare R2 |
| etcd / K3s state | k3s etcd-snapshot CronJob | Daily | 7 days | Cloudflare R2 |
| MLflow artifacts | rsync CronJob | Daily | 14 days | Cloudflare R2 |
| Harbor images | Not backed up (rebuild from Git) | — | — | — |
| Helm chart | Git repo (natural backup) | — | — | GitHub |
| NFS dataset | Not managed by lolday | — | — | — |

### 12.2 Disaster Recovery (Full Server Loss)

1. Install K3s + Cilium on new machine
2. Restore etcd snapshot from R2
3. `helm install lolday`
4. Restore PostgreSQL dump from R2
5. Restore MLflow artifacts from R2
6. Remount NFS
7. Rebuild Harbor images from Git repos
8. Platform fully recovered

### 12.3 Backup Validation

Monthly manual restore test to a temporary environment to confirm backups are usable.

---

## 13. Prerequisites & CLI Tools

### Already Available

| Tool | Version |
|------|---------|
| Docker | 29.3.1 |
| Python | 3.12.7 |
| uv | 0.11.2 |
| Node.js | 24.14.1 |
| npm | 11.11.0 |
| gh (GitHub CLI) | 2.89.0 |

### Installed for This Project

| Tool | Version | Purpose |
|------|---------|---------|
| kubectl | 1.35.3 | K8s CLI |
| Helm | 3.17.3 | K8s package management |
| K3s | 1.34.5 | Kubernetes cluster |
| Cilium CLI | 0.19.2 | CNI with NetworkPolicy |
| k9s | 0.50.18 | K8s terminal UI |
| Trivy | 0.69.3 | Image vulnerability scanning |
| Cloudflared | 2026.3.0 | Cloudflare Tunnel |
| pnpm | 10.33.0 | Node.js package management |

---

## 14. Future Enhancements (v2+)

- gVisor / Kata Containers for stronger container isolation
- Hyperparameter search (grid / random / Bayesian)
- Early stopping + real-time training metric streaming
- GPU time-slicing / MPS for sharing a single GPU
- Model drift detection
- Per-user GPU usage tracking and reporting
- Detector CI/CD (auto-test spec compliance on push)
- In-app notification feed
- Discord webhook integration
