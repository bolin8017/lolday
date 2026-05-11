# Storage Architecture Redesign — Design Specification

> **建立於 2026-05-11**。觸發點:在完成 MLflow 資料模型重設計 (#122) 後,operator 詢問「現在整個系統的資料儲存怎麼處理、有沒有舊資料殘留可清、未來空間不夠如何擴充」。經 audit 發現:
>
> - 全部 11 個 PV 都跑在 K3s local-path-provisioner 上(hostPath,單機,無 abstraction 層)
> - 加新 SSD 必須手動 stop pods + bind mount 遷移,沒 application-transparent 的擴充路徑
> - MLflow artifact / Harbor blob / Loki chunk 三個 object-style 大資料元件全用 block storage 跑,違反 ML 平台 storage layer 主流分層
> - 沒有任何 lifecycle / retention policy,容量管理全靠 operator 手動清

> **本份 spec 主要回答**:在 server30 單機(將來可能加 SSD,可能加 node)的條件下,如何把 object-style 資料層改成 mainstream MLOps 平台的標準分層,讓「加 SSD = 一行 config」、「升 multi-node = 一個 deployment mode 切換」、「retention policy = 平台 native primitive」三件事**結構上自然發生**,而不是每次擴充都靠 operator 動手腳。

## 1. Overview

當前 storage 架構不是「不能用」,而是**長尾的「每件事都靠 operator 親手操作」**:加 SSD 要停機搬 PV、清舊 image 要手動跑 crictl、設 retention 要每個元件各寫一遍 ConfigMap、未來如果要加第二個 server 整個架構打掉重來。

這份 spec 的解法:**引入 MinIO 作為單一 S3-compatible object storage layer**,把 MLflow / Harbor / Loki 三個 object-style 元件全部改寫到 S3 backend。其餘 block-style 元件(PostgreSQL、Prometheus TSDB、Grafana / Redis / Alertmanager 等小型 PV)維持 local-path。

| 層              | 改動                                                                     | 對應的痛點                      |
| --------------- | ------------------------------------------------------------------------ | ------------------------------- |
| **MinIO**       | 新增 Helm sub-chart,單機單磁碟模式起                                     | 提供 storage abstraction layer  |
| **MLflow**      | `--default-artifact-root` 改 `s3://mlflow-artifacts/`,加 S3 endpoint env | 5 MB artifact 改走 object store |
| **Harbor**      | registry storage driver 從 `filesystem` 改 `s3`                          | 25 GB blob 改走 object store    |
| **Loki**        | `storage_config` 從 `filesystem` 改 `s3`                                 | logs 改走 object store          |
| **Helm values** | 新增 storage class、retention policy 配置                                | lifecycle 統一管理              |
| **Runbook**     | 新增 `docs/runbooks/add-ssd.md`                                          | SSD 擴充流程明文化              |

> **Breaking change 是被授權的** (見 §2)。不留 dual-backend、不留 migration toggle、不分階段保留舊路徑。一次到位。

## 2. Authorization

使用者於 2026-05-11 brainstorming 階段明示授權:

- **「從根本解決問題」為最高原則** — 不接受「先 patch 一下」、「等下版再做」、「等空間真的不夠再說」
- **不需考慮向後相容性** — 既有 5 MB MLflow artifact 與 25 GB Harbor blob 一次性遷移,不留舊路徑
- **必須基於主流且被驗證的實踐** — MinIO、S3 backend、CNCF storage pattern 都是 MLOps 平台 mainstream
- **單機 server30 為主**,未來主要透過**掛接新 SSD** 擴容
- **若主流做法是先把 multi-node 考慮進去,允許這麼做**

## 3. Scope

### 3.1 In scope

**新元件部署**

1. MinIO sub-chart 進 `charts/lolday/charts/`,版本 RELEASE.2025-09-01T00-00-00Z 或更新(latest stable)
2. MinIO StatefulSet 配置:single-server-single-drive (SNSD) 起步,host bind 至既有 nvme PV 路徑
3. MinIO Helm values 與 lolday umbrella chart 整合(`charts/lolday/values.yaml` 加 `minio:` section)
4. MinIO root credentials 由 K8s secret 提供(`minio-root-cred`)
5. Application-specific MinIO service-account credentials(read/write 限定 bucket):`mlflow-s3-cred`、`harbor-s3-cred`、`loki-s3-cred`
6. Buckets pre-create via Job hook:`mlflow-artifacts`、`harbor-blobs`、`loki-chunks`、`loki-ruler`(Loki 規則用)
7. Bucket lifecycle policies(object expiration / version retention)

**Application backend 切換**

8. `charts/lolday/templates/mlflow.yaml` 改 `--default-artifact-root=s3://mlflow-artifacts/`、`--artifacts-destination=s3://mlflow-artifacts/`,加 `MLFLOW_S3_ENDPOINT_URL` + AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY env
9. Harbor sub-chart values:`registry.config.storage.s3.*` 改寫,disable `filesystem` driver
10. Loki sub-chart values:`storageConfig.aws.*`(loki 7.0+ 是 `commonConfig.storage.object_store: s3`),disable filesystem chunk storage

**Migration / 資料搬移**

11. One-shot Job manifests:`migrate-mlflow-to-s3.yaml`、`migrate-harbor-to-s3.yaml`、`migrate-loki-to-s3.yaml`
12. 既有 100 Gi `mlflow-artifacts` / 100 Gi `lolday-harbor-registry` / 30 Gi `storage-loki-0` PVC 在 migration 完成 + smoke 通過後刪除(空間還給 host disk)

**Runbook / Docs**

13. `docs/runbooks/add-ssd.md` — 加新 SSD 的端到端流程(主要 deliverable)
14. `docs/runbooks/storage-migration.md` — 一次性遷移流程
15. `docs/architecture.md` §6 改寫 storage 層描述
16. `docs/runbooks/troubleshooting.md` 加 MinIO 常見問題排查

**Cleanup tooling**

17. `scripts/storage-audit.sh` — 列出每個 backend 的實際使用量、object count、最舊 object 時間(支援週期性檢查)
18. PV-side 既有 retention policy 整合到 MinIO bucket lifecycle(自動 object expiration)

### 3.2 Out of scope

- **PostgreSQL data migration**:PostgreSQL 是 block storage 用戶,維持 local-path PVC,**主流 OLTP DB 不跑 object store**(Databricks metastore、MLflow tracking store 都是 block)。若 server30 disk 壓力大,先做 retention 不換 backend
- **Prometheus TSDB migration**:Prometheus 自己是 block(mmap 為主),改 object store 需要 Thanos sidecar,**這是 future scope**(列在 §5.10)
- **Grafana / Redis / Trivy DB / Alertmanager state**:每個 < 5 GB,單機 OLTP/cache,不值得搬
- **Samples PV**:已是 hostPath 至另一顆 SSD (`/mnt/ssd500g/data/samples`),read-only big static,不在 redesign 範圍
- **既有 MLflow / Harbor / Loki 資料的 backfill 比對 / re-hash**:`aws s3 sync` 後信任 ETag,不額外做 content verification
- **MinIO multi-node distributed mode**:**架構上預留**(§5.6),但 Phase 1 不部署 — single-server-single-drive 起,等未來真要加 node 再升
- **vcjob TTL bug**(audit 階段發現的 follow-up):lolday-controllers 沒有 vcjob GC,vcjobs `ttlSecondsAfterFinished: 604800` 被忽略。**另開 spec 處理**(列在 §9 follow-up)
- **既有 K3s local-path-provisioner 的留存 PV(其他小型元件)**:不動,維持現狀

### 3.3 已被排除的替代解法(留紀錄)

- **方案 B:保留 local-path + 手動 bind mount 新 SSD** — 否決原因:每次擴 SSD 都要停機 + 手動遷移 + 修 PV hostPath,**不主流**;沒 abstraction 等於把實體拓樸暴露給 application
- **方案 C:Longhorn block storage** — 否決原因:單機跑 replication 無收益;比 MinIO 重很多(數十個 controller pod);block vs object 阻抗(MLflow / Harbor / Loki 內部都已抽象到「object/file」概念,套 block 等於繞遠路);**ML 平台 storage layer 主流不選 block-distributed**
- **方案 D:NFS / Ceph / Rook** — 否決原因:NFS 單點故障 + 性能差 + K8s 不主流;Ceph/Rook 需要 3+ node + 學習曲線陡 + 對單機過 overkill
- **方案 E:lazy SSD addition with bind mount only when needed** — 否決原因:這是「先不解,等真的滿了再說」,違反 root-cause 原則
- **直接接外部 S3(AWS / Cloudflare R2 / Backblaze B2)** — 否決原因:ISLab 樣本為 sensitive malware,**不能流出私有環境**;雖然加 region+egress 流量分區可緩解,但複雜度高、與 server30-only 架構不符

## 4. Background — 為什麼會出現這些痛點

### 4.1 K3s 預設 local-path-provisioner 的設計取捨

K3s 出廠帶 [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner) 作為 default StorageClass。它的設計目標明文寫:

> Local Path Provisioner provides a way for the Kubernetes users to utilize the local storage in each node. **Based on the user configuration, the Local Path Provisioner will create either hostPath or local based persistent volume on the node automatically.**

注意關鍵字:**"based on the user configuration"** 與 **"on the node"**。它的本意是「給 dev / 小型 single-node 部署使用」,**不是 production multi-node ML 平台 storage layer**。Rancher 自己的 docs 也直接寫 [Limitations](https://github.com/rancher/local-path-provisioner#limitation):

- 只能 `ReadWriteOnce` (RWO),不能跨 node
- 沒 dynamic resize
- 沒 snapshot
- 沒 replication
- bind to specific node

這些限制每一條都對應到 lolday 痛點。我們不是「不知道用了 local-path 會痛」,而是 K3s 出廠預設就是這條路、沒有人在 Phase 1 重新評估過。**那個評估現在做**。

### 4.2 為什麼三個 object-style 元件用 block storage 跑

當前 MLflow / Harbor / Loki 都用 PVC + local-path 跑,理由是「開箱即用」:

| 元件   | 預設 storage                                                         | Helm chart 預設值   |
| ------ | -------------------------------------------------------------------- | ------------------- |
| MLflow | `--artifacts-destination=/mlflow-artifacts`(filesystem,本地路徑)     | self-hosted 預設    |
| Harbor | `registry.config.storage.filesystem.rootdirectory: /storage`         | bitnami/harbor 預設 |
| Loki   | `commonConfig.storage.filesystem.chunks_directory: /var/loki/chunks` | grafana/loki 預設   |

三個都**原生支援 S3**(Harbor 內建 storage driver、MLflow 透過 boto3、Loki 透過 thanos-io/objstore),但要切過去需要 (1) 部署 S3-compatible storage,(2) 改 Helm values,(3) 一次性 migration。沒人做過所以沒做。

**主流參考**:

- MLflow 官方 [tracking server § Artifact Store](https://mlflow.org/docs/2.20/tracking/artifact-stores) 列出建議 backend: S3, Azure Blob, GCS, **filesystem 標註 "only for local development or single-node deployments"**
- Harbor 官方 [Configuring the Storage Backend](https://goharbor.io/docs/2.10.0/install-config/configure-storage-backend/) 多數 production 範例用 S3 (含 MinIO)
- Loki 官方 [Storage](https://grafana.com/docs/loki/latest/configure/storage/) Single Store TSDB 預設**就是 S3-compatible**,filesystem 標註 "Single binary, single tenant only"

我們是 single-binary 沒錯,但 multi-component + 25 GB+ Harbor 已經出 "single-tenant only" 的最佳適用範圍。

### 4.3 為什麼加 SSD 沒有 abstract 流程

K3s local-path-provisioner 的 PV 配置是 `hostPath: /var/lib/rancher/k3s/storage/<pv-id>`。當 disk 滿時,operator 必須:

1. mount 新 SSD 到 `/mnt/ssd1/`
2. 把選定 PV 內容 `cp -a` 搬到新位置
3. 改 PV 的 `hostPath` 指向新位置(`kubectl edit pv ...` — 但 hostPath 通常 immutable)
4. 或者:刪 PVC + 重建在新 storage class + 從備份還原

每一步都會中斷服務、容易出錯、需要 sudo。**這不是現代 K8s 平台應該有的擴充體驗**。CNCF reference architecture 對 storage 擴充的期待是:application 端對 storage layer 完全 transparent,新增容量只是 storage layer 內部事件。

## 5. Architecture decisions

### 5.1 MinIO 作為單一 object storage layer

**選定**:在 `charts/lolday/charts/` 加 MinIO sub-chart,作為平台**唯一**的 S3-compatible object storage endpoint。

**部署型態**:Single-Node Single-Drive (SNSD) mode 起步。

```
MinIO StatefulSet (replicas: 1)
├── volumeMounts:
│   └── /data1 ← PVC `minio-data1` (100 Gi, local-path on /var/lib/rancher/k3s/storage)
├── args:
│   └── server /data1 --console-address ":9001"
└── env:
    └── MINIO_ROOT_USER / MINIO_ROOT_PASSWORD (from secret)

Service: minio.lolday.svc:9000 (S3 API)
Service: minio-console.lolday.svc:9001 (Web UI for debug, 不暴露外部)
```

**為什麼 SNSD (而不是直接上 SNMD / Distributed)**:

- 當前 NVMe 是單一磁碟,沒有第二顆同尺寸 drive 可組 EC
- SNSD 的 redundancy posture **完全等同**現在 local-path-provisioner 的 posture(都是「單一 host 單一 disk,壞了什麼都沒了」),**沒倒退**
- 一旦加第二顆 SSD,**升級路徑明確**(§5.5)

**為什麼選 MinIO 而不是 SeaweedFS / Garage / Ceph object storage**:

| 比較項                    | MinIO          | SeaweedFS | Garage | Ceph RGW      |
| ------------------------- | -------------- | --------- | ------ | ------------- |
| S3 API compat 完整度      | ★★★★★          | ★★★★☆     | ★★★★☆  | ★★★★★         |
| ML 平台主流選擇           | **★★★★★**      | ★★        | ★      | ★★★           |
| CNCF graduated / sandbox  | sandbox        | —         | —      | (separate)    |
| 單機到分散式升級路徑      | ★★★★★          | ★★★       | ★★★    | ★★★           |
| 單一 binary               | ✓              | ✓         | ✓      | ✗ (多 daemon) |
| Kubeflow / Vertex AI 文件 | 官方 reference | 第三方    | —      | 第三方        |
| 運維文件完整度            | ★★★★★          | ★★★       | ★★     | ★★★★★         |

MinIO 是 ML 平台 storage layer 的**事實標準**:Kubeflow、SeldonCore、KServe、ZenML、Argo Workflows 的官方範例都用它。

### 5.2 三個 application backend 切換目標

| Application     | 來源(filesystem)          | 目標(S3)                                 | 預估資料量         | 預估遷移時間 |
| --------------- | ------------------------- | ---------------------------------------- | ------------------ | ------------ |
| MLflow          | `/mlflow-artifacts` (PVC) | `s3://mlflow-artifacts/`                 | 5 MB               | < 10 秒      |
| Harbor registry | `/storage` (PVC)          | `s3://harbor-blobs/`                     | 25 GB              | 5–10 分鐘    |
| Loki            | `/var/loki` (PVC)         | `s3://loki-chunks/` + `s3://loki-ruler/` | 全量 logs(< 30 GB) | 5–10 分鐘    |

切換過程中三個 application 都會短暫不可用(rollout 期間)。MLflow / Harbor / Loki **不在 detector job 的關鍵路徑**(detector pod 啟動會打 MLflow tracking server 寫 metric,Harbor pull image 在 pod 啟動瞬間),所以遷移時段要選 detector job 空閒視窗。

### 5.3 不搬的元件 — 為什麼留在 local-path

| 元件                                                           | 為什麼留 local-path                                                                                     | 主流參考                                                                                                                            |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL(lolday + mlflow + harbor 三 DB 共用 instance,74 MB) | OLTP DB **需要 block storage**(隨機 I/O、page cache、WAL fsync);object store 對 PostgreSQL 是反 pattern | Databricks Unity Catalog metastore、AWS RDS 都跑 block;PostgreSQL 官方明文 "Do not run on NFS or network FS without careful tuning" |
| Prometheus TSDB(6.3 GB on 20 Gi)                               | mmap-heavy timeseries,本地 SSD 性能不可替代;**short-term retention** (15 天) 不需 object 後援           | Prometheus 自己的 long-term storage 解法是 **Thanos sidecar + object store**,本 spec §5.10 列為 future scope                        |
| Grafana(5 Gi)                                                  | SQLite + dashboards,單機 OLTP 風格                                                                      | —                                                                                                                                   |
| Redis(2 Gi, Harbor 自帶)                                       | in-memory cache,PV 只存 AOF/RDB                                                                         | —                                                                                                                                   |
| Trivy DB(10 Gi)                                                | scanner 本地 cache                                                                                      | —                                                                                                                                   |
| Alertmanager(2 Gi)                                             | local state for silencing                                                                               | —                                                                                                                                   |
| Samples(600 Gi external SSD)                                   | read-only big static,**已經是另一顆 SSD hostPath**,結構合理                                             | —                                                                                                                                   |

**統一原則**:object store 給 object-style 資料(write-once-read-many、大檔案、append-mostly);block store 給 transactional OLTP / cache / TSDB。**這是 CNCF cloud-native data layer 的標準分類**(see [CNCF Cloud Native Storage Whitepaper](https://www.cncf.io/wp-content/uploads/2020/12/Cloud-Native-Storage-Whitepaper-Nov2020.pdf))。

### 5.4 初始 topology — SNSD on NVMe

```
server30
├── /dev/nvme0n1p1 (458 GB)
│   └── /var/lib/rancher/k3s/storage/
│       ├── pvc-<minio-data1>  100 Gi ← MinIO 寫這
│       ├── pvc-<postgresql>     10 Gi
│       ├── pvc-<prometheus>     20 Gi
│       ├── ... (小 PV)
│       └── (既有 mlflow-artifacts / harbor / loki PVC 刪除後消失)
└── /mnt/ssd500g (另一顆 SSD)
    └── /data/samples           600 GB (read-only hostPath)
```

容量規劃 — **MinIO bucket-level soft quota**(`mc admin bucket quota`),作為 application-level 容量上限提示:

| Bucket             | 初始 soft quota | 一年預估流量                                                                                | 觸發擴充 SSD |
| ------------------ | --------------- | ------------------------------------------------------------------------------------------- | ------------ |
| `mlflow-artifacts` | 50 Gi           | 10 GB (50 train run/月 × 500 KB model + system metrics × 1 KB × 60 sample/run × 12 月)      | < 5 GB free  |
| `harbor-blobs`     | 80 Gi           | 50–80 GB(每月 2 個 detector × 3.4 GB + 4 個 backend image × 200 MB,retention 後 < 10 GB/月) | < 10 GB free |
| `loki-chunks`      | 30 Gi           | < 30 GB(7-day retention 已設)                                                               | < 5 GB free  |
| `loki-ruler`       | 1 Gi            | < 100 MB                                                                                    | —            |

> **Bucket quota vs PV size 區分**:bucket soft quota 是 MinIO 自己對該 bucket 的 logical 用量提示(超過會 alert,不會拒寫);**MinIO 自己跑在 100 Gi local-path PVC 上**(§6.1)。Phase 1 全部 buckets 共用同一個 PVC 的容量。加 SSD 後新 pool 是另一個 PVC,**MinIO 跨 pool 自動 routing 寫入**,bucket quota 仍然是 logical 上限。

監控:**bucket 用量由 MinIO Prometheus metrics 抓**(`minio_bucket_usage_total_bytes`),搭 `minio_cluster_capacity_usable_free_bytes` 看 storage layer 剩餘空間。任一條低於 threshold 觸發 Discord 警報(走既有 alerting redesign 的 #lolday-service-alerts channel)。

### 5.5 SSD 擴充流程 — 走 MinIO server pool

**選定的 mainstream pattern**:每加一顆新 SSD,在 MinIO StatefulSet 加一個 **新 server pool**(各 pool 獨立)。MinIO 把每個獨立的 `/path` 參數視為一個 pool(see [MinIO server pool semantics](https://min.io/docs/minio/linux/operations/install-deploy-manage/deploy-minio-multi-node-multi-drive.html#minio-mnmd-server-pools)),所以 multi-pool 配置就是用**分開的 path 參數**而非 expansion-set 語法:

```
Phase 1 (initial):
  minio server /data1                         # 1 pool × 1 drive

Phase 2 (掛新 SSD):
  minio server /data1 /data2                  # 2 pools × 1 drive each (separate args = separate pools)
  └── /data2 是新 SSD 對應的 PVC

Phase 3 (再加):
  minio server /data1 /data2 /data3           # 3 pools × 1 drive each
```

對照:**`minio server /data{1...4}`(用 expansion-set 語法)= 1 pool × 4 drives**,會觸發 cross-drive EC。本 spec 不走這條,因為它跟「每加一顆 SSD 即時可用」需求衝突。

MinIO 官方稱本 spec 採用的模式為「Server Pool Expansion」,文件 [Expand a MinIO Server Deployment](https://min.io/docs/minio/linux/operations/install-deploy-manage/expand-minio-deployment.html)。

**為什麼選 pool-based(每 pool 1 drive)而不是 add-drive-to-existing-pool**:

- Pool-based:加 pool 不觸發 rebalance,**零 downtime / 零等待**;MinIO 把後續寫入往新 pool 導(舊 pool 達一定使用率後)
- Add-drive 到既有 pool 必須 rebalance 整個 pool,IO heavy + 等待時間長;且 1 → 2 drive 會自動 enable EC:1(mirror),**有效容量沒增加只是多了 redundancy** — 不符合「加 SSD = 加容量」直覺
- Pool-based 對 SSD 大小不一致 friendly(不要求 same size)
- Pool-based 對應到 user 的「每次加一顆 SSD」期待

**Trade-off**:每 pool 1 drive 沒有 intra-pool erasure coding。Phase 1–3 都沒 redundancy(等同 local-path 現狀,沒倒退)。**真要 EC redundancy 時**,可以做 Phase 4:把 4+ drive 合成 1 個 pool 重 deploy,或上 Phase 5 multi-node。

### 5.6 多 node 升級路徑(future, 預留架構)

**現在不做,但架構不擋路**。當未來真要加 server31:

```
Phase 5 (multi-node distributed):
  minio server http://server{30,31}.lolday.svc/data{1...N}
```

MinIO 進入 distributed 模式後:

- Application S3 endpoint 不變(`minio.lolday.svc:9000`,K8s Service routing 自動 fanout)
- Erasure coding 自動 enable(預設 EC:N/2)
- 容錯:單 node 掛仍能讀寫

**Migration from Phase 3 → Phase 5**:**non-trivial**,需要 stop writes + 重新 deploy + 從備份 import。但**這是大版本變化,合理代價**。

> 重要:**這份 spec 不在 Phase 1 就上 distributed mode**。理由:
>
> - 當前只有 server30,distributed mode 退化為 single-node,完全沒收益
> - distributed mode 對 drive 數量有最低要求(2-server-2-drive 起,但官方建議 4+ drive),拉抬複雜度
> - 等真有 server31 時再升,**這正是 MinIO 主流升級路徑**

### 5.7 Object lifecycle / retention policies

每個 bucket 走 MinIO **server-side lifecycle rules**(等同 AWS S3 lifecycle),由 MinIO Operator 或 `mc ilm` 配置。Helm chart 用一個 Job hook 在部署完跑 `mc admin policy attach` + `mc ilm rule add`。

| Bucket             | 規則                                                                                                                | 主流參考                                                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `mlflow-artifacts` | 保留所有 object,**無自動刪除**(model 是 first-class asset)                                                          | Vertex AI Models / Databricks MLflow 都是「明確刪 run 才刪 artifact」                                                       |
| `harbor-blobs`     | **由 Harbor 自己的 GC 管**,MinIO 端不設 lifecycle(避免雙重刪除衝突);Harbor retention policy 已在 cleanup phase 設好 | Harbor 官方文件警告:「不要在 backend 端設 lifecycle,讓 Harbor GC 統一管」                                                   |
| `loki-chunks`      | object expiration = 7 days(比照當前 Loki retention)                                                                 | Grafana Loki 官方 [Lifecycle management](https://grafana.com/docs/loki/latest/configure/storage/#lifecycle-management) 推薦 |
| `loki-ruler`       | 不設 expiration                                                                                                     | —                                                                                                                           |

`mlflow-artifacts` bucket 啟用 **versioning**(MinIO 內建),這樣即使 operator 誤刪 run,artifact 還在 version history 內。對 Harbor / Loki bucket 不啟用(會 double-store)。

### 5.8 Credentials / IAM design

走 MinIO **service accounts**(per-application credential),不要全用 root credential:

| Service Account | Bucket Access                      | 用途                                 | 取得方式                                         |
| --------------- | ---------------------------------- | ------------------------------------ | ------------------------------------------------ |
| `mlflow-app`    | RW on `mlflow-artifacts`           | MLflow server pod                    | K8s secret `mlflow-s3-cred`                      |
| `harbor-app`    | RW on `harbor-blobs`               | Harbor registry pod                  | K8s secret `harbor-s3-cred`                      |
| `loki-app`      | RW on `loki-chunks` + `loki-ruler` | Loki pod                             | K8s secret `loki-s3-cred`                        |
| Root            | full                               | 只有 init-bucket Job + operator 救援 | K8s secret `minio-root-cred`(僅 admin RBAC 可讀) |

MinIO service account 用 `mc admin user svcacct add` 建立,access key / secret 寫到 K8s secret。**符合 least-privilege**,跟 AWS IAM role-per-service 同型。

### 5.9 vcjob TTL bug(audit 發現,follow-up 另開 spec)

審計過程發現 `JOB_TTL_SECONDS_AFTER_FINISHED = 604800 (7d)` 已設,但 vcjobs 5 天沒被清理。Root cause:

- lolday 用 Volcano CRD `batch.volcano.sh/v1alpha1:Job`(vcjob)而**不是 batch/v1:Job**
- K8s 內建 TTL controller 只管 `batch/v1:Job`,**不認 vcjob**
- 應該要有 Volcano 自己的 TTL controller(`volcano-controller-manager`),但 `kubectl get pods -A | grep volcano` **為空**
- 結果:`ttlSecondsAfterFinished` 設了但沒人讀,vcjobs 永久累積

**修法選項**(留給下一個 spec):

- (a) 部署 volcano-controller-manager(Volcano 官方主流,但會引入完整 Volcano controller stack)
- (b) 在 `app/reconciler/` 加 vcjob GC reconciler(輕量,但 reinventing wheel)
- (c) 用 K8s CronJob 跑 `kubectl delete vcjob` 過濾終態 + 超齡(quick fix,但繞)

**現在的 mitigation**:本 spec 的 cleanup phase 已手動清掉 12 個累積的 vcjob,接下來透過監控 vcjob 數量觸發 ad-hoc cleanup。**這是 tech debt**,標記在 `docs/architecture.md` §9,等本 spec landed 後另開。

### 5.10 Prometheus → Thanos sidecar(future scope)

Prometheus 本地 TSDB retention 設 15 天,對「過去 15 天的告警 / debug」夠用。若未來需要**長期 capacity planning 趨勢圖**(例如「過去 6 個月 GPU 利用率變化」),CNCF 主流是加 **Thanos sidecar**:

```
Prometheus pod (server30)
├── prometheus container
├── thanos-sidecar container ← 把 TSDB block 上傳到 s3://prometheus-tsdb/
└── (kept) /prometheus PVC ← short-term TSDB

Thanos store-gateway (cluster query)
└── reads s3://prometheus-tsdb/ for long-term queries
```

這是 [Thanos getting started](https://thanos.io/tip/thanos/getting-started.md/) 的標準 pattern,**Cilium、Linkerd、Argo 等大型 CNCF 專案都這麼用**。

**本 spec 不做**,但 MinIO 部署完後上 Thanos 只需 (1) 加 sidecar (2) 建 `prometheus-tsdb` bucket (3) 部署 Thanos store-gateway。**架構預留位置**,明文寫在 `docs/architecture.md`。

## 6. Implementation details

### 6.1 MinIO Helm sub-chart 整合

新增 `charts/lolday/charts/minio-<version>.tgz`(從 [bitnami/minio](https://github.com/bitnami/charts/tree/main/bitnami/minio) 或 [minio/minio](https://github.com/minio/minio/tree/master/helm/minio) pull,**選 minio/minio 官方 chart**,理由:bitnami 改名 Broadcom 後不穩定,官方 chart 跟 binary release 同步)。

`charts/lolday/values.yaml` 新增:

```yaml
minio:
  enabled: true
  mode: standalone # single-node-single-drive
  replicas: 1
  drivesPerNode: 1
  persistence:
    enabled: true
    size: 100Gi # 初始 quota,擴 SSD 時加新 pool
    storageClass: local-path
  rootUser: minio-admin
  # rootPassword from existingSecret
  existingSecret: minio-root-cred
  resources:
    requests: { cpu: 250m, memory: 512Mi }
    limits: { cpu: 2, memory: 4Gi }
  service:
    type: ClusterIP
    ports:
      api: 9000
      console: 9001
  metrics:
    serviceMonitor:
      enabled: true # Prometheus scrape
  buckets:
    - name: mlflow-artifacts
      versioning: true
    - name: harbor-blobs
      versioning: false
    - name: loki-chunks
      versioning: false
    - name: loki-ruler
      versioning: false
  policies:
    # per-bucket lifecycle (參考 §5.7)
    - name: loki-chunks-expire-7d
      bucket: loki-chunks
      rules:
        - id: expire-old
          status: Enabled
          expiration:
            days: 7
```

### 6.2 MLflow backend swap

`charts/lolday/templates/mlflow.yaml` 改:

```yaml
spec:
  template:
    spec:
      containers:
        - name: mlflow
          args:
            - mlflow
            - server
            - --backend-store-uri=postgresql+psycopg://... # 不變
            - --default-artifact-root=s3://mlflow-artifacts/ # ← 改
            - --artifacts-destination=s3://mlflow-artifacts/ # ← 改
            - --serve-artifacts
            - --static-prefix=/mlflow
          env:
            # existing PG_* env vars unchanged
            - name: MLFLOW_S3_ENDPOINT_URL
              value: http://minio.lolday.svc:9000
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef: { name: mlflow-s3-cred, key: access-key }
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef: { name: mlflow-s3-cred, key: secret-key }
            - name: AWS_DEFAULT_REGION
              value: us-east-1 # MinIO 預設,可任意
          # remove volumeMounts: /mlflow-artifacts (no longer needed)
      # remove volumes: artifacts (no longer needed)
```

`charts/lolday/templates/mlflow-secret.yaml`(新增 `mlflow-s3-cred`):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mlflow-s3-cred
  namespace: lolday
type: Opaque
data:
  access-key: <generated by minio-init-job>
  secret-key: <generated by minio-init-job>
```

**Detector pod 端**:MLflow client 走 `--serve-artifacts` 模式,所有 artifact upload 經 mlflow-server proxy,**detector pod 不需要直接打 MinIO**,**MinIO credential 不外洩到 job-jobs namespace**。

### 6.3 Harbor registry storage driver swap

Harbor sub-chart `values.yaml`:

```yaml
harbor:
  persistence:
    persistentVolumeClaim:
      registry:
        existingClaim: "" # disable filesystem PVC
        size: 100Gi # ignored
        storageClass: "local-path"
        accessMode: ReadWriteOnce
    imageChartStorage:
      type: s3 # ← was filesystem
      disableredirect: true
      s3:
        region: us-east-1
        bucket: harbor-blobs
        regionendpoint: http://minio.lolday.svc:9000
        accesskey: <from-secret>
        secretkey: <from-secret>
        secure: false
        skipverify: true
        v4auth: true
```

Credentials via `harbor-s3-cred` secret(`mc admin user svcacct add` 在 init job 跑時建立)。

### 6.4 Loki S3 backend

Loki 7.0+ chart `values.yaml`:

```yaml
loki:
  loki:
    commonConfig:
      replication_factor: 1
    storage:
      type: s3
      bucketNames:
        chunks: loki-chunks
        ruler: loki-ruler
      s3:
        endpoint: http://minio.lolday.svc:9000
        region: us-east-1
        accessKeyId: <from-secret>
        secretAccessKey: <from-secret>
        s3ForcePathStyle: true
        insecure: true # internal cluster, http only
    storageConfig:
      tsdb_shipper:
        shared_store: s3 # ← was filesystem
  # remove persistence config for chunks
  singleBinary:
    persistence:
      enabled: false # state in S3, not local
```

Credentials via `loki-s3-cred` secret.

### 6.5 Migration scripts

**`scripts/migrate-storage-to-minio.sh`**:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Wait for MinIO to be ready
kubectl rollout status -n lolday statefulset/minio --timeout=5m

# 2. MLflow migration (5 MB, trivial)
kubectl exec -n lolday deploy/mlflow -- mc alias set local http://minio.lolday.svc:9000 ...
kubectl exec -n lolday deploy/mlflow -- mc mirror /mlflow-artifacts/ local/mlflow-artifacts/

# 3. Harbor migration (25 GB, ~10 min)
# Stop Harbor briefly to avoid in-flight pushes
kubectl scale deploy -n lolday lolday-harbor-core --replicas=0
kubectl scale statefulset -n lolday lolday-harbor-registry --replicas=0
# rsync to S3
kubectl run harbor-migrator --rm -i --image=minio/mc --restart=Never -- \
  /bin/sh -c "mc alias set src ...; mc alias set dst http://minio...:9000 ...; \
              mc mirror src/storage/ dst/harbor-blobs/"
kubectl scale statefulset -n lolday lolday-harbor-registry --replicas=1
kubectl scale deploy -n lolday lolday-harbor-core --replicas=1

# 4. Loki migration
# Loki can dual-write or chunks expire — easiest is to flush chunks then start fresh on S3
kubectl scale statefulset -n lolday loki --replicas=0
# (delete old chunks PV or just leave stale — Loki will rotate within retention period)
kubectl scale statefulset -n lolday loki --replicas=1

# 5. Smoke test
./scripts/storage-smoke.sh
```

**`scripts/storage-smoke.sh`**:

```bash
# Submit a test train job, verify:
# - mlflow run artifacts/list shows files in s3://mlflow-artifacts/...
# - docker pull harbor.lolday.svc:80/detectors/elf-rf:v4.2.0 works (image from S3 blob store)
# - loki query loki: {namespace="lolday-jobs"} returns recent logs
```

## 7. Migration plan

### 7.1 Phase ordering(嚴格按序執行)

```
Step 1: Deploy MinIO + buckets + service accounts                 (15 min)
Step 2: Smoke MinIO standalone (mc ls / mb / cp / rm)             (5 min)
Step 3: Switch MLflow backend (rollout)                           (10 min, downtime ~3 min)
Step 4: Verify MLflow new runs land in s3://mlflow-artifacts/     (5 min)
Step 5: Switch Harbor backend (rollout, includes data copy)       (30 min, downtime ~10 min)
Step 6: Verify Harbor pull / push                                 (5 min)
Step 7: Switch Loki backend (rollout)                             (10 min, downtime ~3 min)
Step 8: Verify Loki queries return chunks                         (5 min)
Step 9: Delete legacy PVCs (mlflow-artifacts, lolday-harbor-registry, storage-loki-0)  (after 24h burn-in)
Step 10: Update runbooks, architecture docs, CLAUDE.md            (commit)
```

Total downtime: ~16 分鐘(分散在三個 rollout window)。可選平日凌晨低峰時段執行。

### 7.2 Rollback

每個 step 都單獨 revertable(Helm revision):

- Step 3 失敗:`helm rollback lolday <prev-revision>` 把 MLflow 拉回 filesystem backend
- Step 5 失敗:同理 Harbor
- Step 7 失敗:同理 Loki

**legacy PVC 在 Step 9 才刪**,前面任何 step 失敗都還能 fallback。

Step 1 失敗(MinIO 起不來)直接 `helm uninstall lolday-minio`,application 端完全沒動。

### 7.3 既有 user 影響

- MLflow 跑中 / 排隊中的 detector run:Step 3 rollout 期間會看到 `503` from mlflow-server,**maldet 端 `MlflowClient` 已經 implement retry**(由 mlflow-skinny 內建),恢復後繼續寫
- Harbor pull:Step 5 rollout 期間新 detector pod 無法 `kubectl run`,**需要在 detector job 靜默時段**做(以 reconciler queue 空 + 沒 ad-hoc submit 為訊號)
- Loki 查詢:Step 7 rollout 期間 Grafana log panel 沒資料,**Operator 可接受**(已知期程內)

## 8. SSD 擴充 runbook (`docs/runbooks/add-ssd.md`)

> **這是這份 spec 的 user-facing 主要 deliverable**。Operator(包括 future-Claude)拿到新 SSD 後,照這個 runbook 一條一條跑,**全程 SSH + kubectl,不需要 stop pod、不需要動 application config**。

````markdown
# Adding a new SSD to lolday storage

## Prerequisites

- New SSD installed and visible in `lsblk` (e.g., `/dev/nvme1n1`)
- Sudo on server30 (operator typically temporarily granted)
- MinIO running healthy: `kubectl get pod -n lolday -l app.kubernetes.io/name=minio`

## Step 1 — Format and mount (sudo)

```bash
# Identify new disk
sudo lsblk -d -o NAME,SIZE,MODEL | grep -v boot

# Format (XFS recommended by MinIO docs for ≥ 1 TB drives; ext4 fine otherwise)
sudo mkfs.xfs /dev/nvme1n1

# Mount at predictable path
sudo mkdir -p /mnt/ssd1
sudo mount /dev/nvme1n1 /mnt/ssd1
sudo chown 1001:1001 /mnt/ssd1   # MinIO container UID

# Persist across reboots
echo "/dev/nvme1n1 /mnt/ssd1 xfs defaults,nofail 0 0" | sudo tee -a /etc/fstab
```
````

## Step 2 — Create the PV + PVC pair pointing to the new mount

> 為什麼**手動**創 PV 而不是讓 local-path-provisioner 自動 dynamic provision:provisioner 動態 PV 會落在預設路徑 `/var/lib/rancher/k3s/storage/`,**不會用到新 SSD**。我們要 PV 明確 bind 到 `/mnt/ssd1`。

```bash
cat <<'EOF' | kubectl apply -f -
# Static PV pointing to new SSD mount
apiVersion: v1
kind: PersistentVolume
metadata:
  name: minio-data2
spec:
  capacity:
    storage: 1Ti                                    # adjust to disk size
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain             # keep data if PVC deleted
  storageClassName: minio-local                     # NOT 'local-path' (avoid auto-provisioner)
  hostPath:
    path: /mnt/ssd1/minio
    type: DirectoryOrCreate
  claimRef:
    name: minio-data2-pvc
    namespace: lolday
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - { key: kubernetes.io/hostname, operator: In, values: [server30] }
---
# Matching PVC that the MinIO StatefulSet will mount
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: minio-data2-pvc
  namespace: lolday
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: minio-local
  resources:
    requests:
      storage: 1Ti
  volumeName: minio-data2
EOF
```

確認 PVC 進 `Bound` 狀態:

```bash
kubectl get pvc -n lolday minio-data2-pvc
# NAME              STATUS   VOLUME        CAPACITY   ACCESS MODES   STORAGECLASS   AGE
# minio-data2-pvc   Bound    minio-data2   1Ti        RWO            minio-local    5s
```

## Step 3 — Extend MinIO StatefulSet with new pool

Edit `charts/lolday/values.yaml`:

```yaml
minio:
  drivesPerNode: 2 # was 1
  extraVolumes:
    - name: data2
      persistentVolumeClaim:
        claimName: minio-data2-pvc
  extraVolumeMounts:
    - name: data2
      mountPath: /data2
  args:
    - server
    - /data1
    - /data2 # ← new
    - --console-address
    - ":9001"
```

Apply:

```bash
bash scripts/deploy.sh
kubectl rollout status -n lolday statefulset/minio --timeout=5m
```

## Step 4 — Verify MinIO sees both pools

```bash
kubectl exec -n lolday minio-0 -- mc admin info local
# Expected: 2 drives, 0 healing, capacity = sum of both
```

## Step 5 — Confirm writes balance across drives

```bash
# Submit a test job that writes to MLflow artifacts
# Check disk usage on each drive over time
df -h /var/lib/rancher/k3s/storage/pvc-<minio-data1>
df -h /mnt/ssd1/minio
```

MinIO will direct new writes to the less-full pool. **No application restart, no data migration, no config in MLflow / Harbor / Loki touched.**

````

## 9. Test plan

### 9.1 MinIO standalone smoke

| Test | 驗證 |
| --- | --- |
| `mc alias set local http://minio.lolday.svc:9000` | API 可達 |
| `mc mb local/test-bucket` | bucket 建立 |
| `mc cp /etc/hostname local/test-bucket/` | 上傳 |
| `mc ls local/test-bucket/` | 列出 |
| `mc rb --force local/test-bucket` | 刪除 bucket |
| `curl http://minio.lolday.svc:9000/minio/v2/metrics/cluster` | Prometheus metrics 端點 |

### 9.2 MLflow end-to-end

| Test | 驗證 |
| --- | --- |
| 提交 train job,等完成 | run 進 PostgreSQL (`mlflow.tracking_uri`)、artifact 進 `s3://mlflow-artifacts/<exp>/<run>/artifacts/model/MLmodel` |
| `mc ls local/mlflow-artifacts/29/<run-id>/artifacts/` | 看到 MLmodel, model.pkl 等 |
| 從 MLflow UI 下載 model.pkl | proxy via mlflow-server, content 對 |
| Evaluate job 用 source-model-version 啟動 | model-fetcher init container 從 s3:// 拉成功 |

### 9.3 Harbor end-to-end

| Test | 驗證 |
| --- | --- |
| `docker pull harbor.lolday.svc:80/detectors/elf-rf:v4.2.0` | image 從 S3 blob 拉成功 |
| 推一個新 detector image | push 完成,Harbor UI 顯示 size |
| `mc ls local/harbor-blobs/docker/registry/v2/blobs/` | 看到 layer blob |
| Harbor GC job 跑 | 沒亂刪 |

### 9.4 Loki end-to-end

| Test | 驗證 |
| --- | --- |
| `logcli query '{namespace="lolday-jobs"}'` | 返回 recent log |
| `mc ls local/loki-chunks/` | chunks tree 存在 |
| 7 天前的 log 自動消失(lifecycle) | object expiration 生效 |

### 9.5 Lifecycle policy

| Test | 驗證 |
| --- | --- |
| `mc ilm rule ls local/loki-chunks` | 規則 `expire-old` 存在 |
| `mc admin trace local --call ilm` 偽造一個 7 天前 object 後等 lifecycle | 隔天被刪 |

### 9.6 SSD 擴充 runbook walkthrough(模擬)

由於 server30 還沒第二顆 SSD,用 **loop device** 模擬:

```bash
sudo dd if=/dev/zero of=/tmp/fake-ssd.img bs=1M count=10240   # 10 GB fake disk
sudo losetup /dev/loop10 /tmp/fake-ssd.img
sudo mkfs.xfs /dev/loop10
# Continue Step 1–5 with /dev/loop10
````

驗證:MinIO 加 pool 後 `mc admin info` 顯示 2 drives,新 object 寫進新 pool。

## 10. Open questions / future work

1. **MinIO operator vs raw Helm chart** — 本 spec 採 raw Helm。MinIO Operator (`minio/operator`) 提供 tenants 抽象 + GUI,但對 single-bucket-per-tenant 模型過重。等多 tenant 時再評估
2. **Thanos sidecar for Prometheus** — §5.10 預留,follow-up spec
3. **vcjob TTL bug** — §5.9 follow-up spec
4. **MinIO root credential rotation** — 目前用 K8s secret,沒有自動 rotation。可整合 Vault / Sealed Secrets,等有人寫
5. **跨 region geo-replication** — 等真有 backup site 才考慮(MinIO 內建 site-replication)
6. **Application-side S3 retry / circuit breaker tuning** — MLflow / Harbor / Loki 各自的 S3 client 都有 retry,但 timeout 預設可能對 internal MinIO 過長。production-tune 是後續觀測項

## 11. References

- MinIO 官方
  - [Single-Node Single-Drive deployment](https://min.io/docs/minio/linux/operations/install-deploy-manage/deploy-minio-single-node-single-drive.html)
  - [Expand a Single-Node deployment](https://min.io/docs/minio/linux/operations/install-deploy-manage/expand-minio-deployment.html)
  - [Bucket lifecycle management](https://min.io/docs/minio/linux/administration/object-management/object-lifecycle-management.html)
- MLflow [Tracking § Artifact Stores](https://mlflow.org/docs/2.20/tracking/artifact-stores)
- Harbor [Configuring the Storage Backend](https://goharbor.io/docs/2.10.0/install-config/configure-storage-backend/)
- Grafana Loki [Storage](https://grafana.com/docs/loki/latest/configure/storage/)
- Prometheus + Thanos [Getting Started](https://thanos.io/tip/thanos/getting-started.md/)
- CNCF [Cloud Native Storage Whitepaper](https://www.cncf.io/wp-content/uploads/2020/12/Cloud-Native-Storage-Whitepaper-Nov2020.pdf)
- 既有相關 spec:
  - `2026-05-11-mlflow-data-model-redesign-design.md`(MLflow data model 重設計,本 spec 是其延伸)
  - `2026-05-10-alerting-redesign-design.md`(alerting 4-channel,新 storage 警報走同管道)
  - `2026-05-10-host-aware-gpu-signal-design.md`(host-aware GPU,類似的「把現實狀態抓進控制平面」設計)

---

附錄 A:本 spec 觸發的 cleanup 已執行紀錄(2026-05-11)

審計過程中發現的舊資料殘留,**已在本 spec 撰寫前手動清掉**,留紀錄:

| 項目                                               | 動作                                                                            | 釋出/影響                                                  |
| -------------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 31 個 unused containerd image                      | `kubectl debug node/server30 + k3s crictl rmi --prune`                          | host disk 269 GB → 219 GB (62% → 51%),**~50 GB recovered** |
| 15 個 Completed pod                                | `kubectl delete pod -n lolday-jobs --field-selector=status.phase=Succeeded`     | namespace 乾淨                                             |
| 12 個終態 vcjob                                    | `kubectl delete vcjob` 過濾 (1 小時前完成且 phase ∈ {Completed, Aborted})       | namespace 乾淨,**揭露 vcjob TTL 沒生效這個 bug**(§5.9)     |
| 20 個 MLflow start_time=0 legacy run               | 直接 `UPDATE runs SET start_time = end_time - <heuristic>` on MLflow PostgreSQL | MLflow UI duration 顯示正確化(5 MB 容量無變化)             |
| Harbor retention policy(空)                        | `POST /api/v2.0/retentions` 新增 keep-semver + keep-latest-5,daily 03:00        | 防未來 backend / frontend image tag 無限累積               |
| `MLFLOW_SYSTEM_METRICS_SAMPLING_INTERVAL` 10s → 1s | `backend/app/services/job_spec.py:172`                                          | 短 run 也有 system metrics sample                          |

這些動作不在本 spec 的實作 plan 內(已完成),但作為 audit 紀錄保存。
