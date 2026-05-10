# Alerting Redesign — Design Specification

> **建立於 2026-05-10。** 本 spec 銜接 `2026-05-10-host-aware-gpu-signal-design.md`（議題 A 的 host-aware GPU signal）。議題 A 落地後，GPU 真實使用語意精確了，這個 spec 重新設計 Alertmanager rules、Discord channel 結構、與 backend notify pipeline 之間的責任邊界，把現況 14 條 alert 整理為 16 條（移除 2、新增 4），並從 3 個 channel 加為 4 個 channel（critical / warning 拆開）。

## 1. Overview

現況 lolday 的 Discord alerting noise 過多，最具代表性的痛點：

- `GPUTemperatureHigh > 85°C / 5min` 在 ML 訓練時**會自然觸發**——NVIDIA 2080 Ti 的預設 thermal throttle threshold 就是 85°C，過了會自動降頻保護。Spec 設這個門檻 critical 是把「GPU 正常工作中」當 incident
- `LoldayGPUVRAMHigh > 90% / 3min` 同樣是 by-design 的訓練常態
- `Captain Hook` channel 把 critical 與 warning 混在一起，讀者要區分「page 我」vs「FYI」
- 沒有 inhibition rules，一個 root cause incident 會 cascade 噴 3-5 條相關 alert
- Trivy critical CVE 用 `@here` 凌晨 ping，但 CVE 不是 page 級事件
- `gpu_signal` 的 fail-safe state（議題 A 引入）目前未 expose 為 Prometheus metric，admin 看不到「scheduler 因 Prom 不通而停 dispatch」這種 silent degradation

本 spec 採用 Google SRE Workbook 的 symptom-based alerting + Prometheus best practices + NVIDIA gpu-operator 主流做法，重新設計：

1. **Alert rule semantics**：12 條保留 + 2 條移除（GPUTemp / VRAMHigh）+ 4 條新增（DCGMXIDError、DCGMThrottleReasonsPersistent、GpuSignalFailSafeStuck、GpuSignalCountMismatch）
2. **Severity 重新分類**：嚴格區分 critical（page）vs warning（FYI）
3. **Inhibition rules**：4 條 cause/symptom suppression
4. **Channel 從 3 個拆為 4 個**：Captain Hook（critical only）+ 新增 Spidey Warnings（warnings only）+ 既有 Spidey heartbeat + Spidey service-alerts
5. **`repeatInterval` 調整**：critical 4h（不變）/ warning 從 4h 提高到 24h
6. **Backend notify 不變**：5 個 `notify_*` 函式繼續送到 Spidey service-alerts

> **這份 spec 主要回答**：怎麼讓 lolday 的 Discord alert 從「多數 noise、少數 signal」變成「主流 SRE 標準的 actionable signal」，又同時整合議題 A host-aware GPU signal 的 fail-safe 可見度，且不犧牲既有 user job-event notify 的 UX。

## 2. Authorization

User 在 brainstorming 階段（2026-05-10）明確授權：

- **Breaking changes OK**：不需要向後相容性。alert rule 名稱可改、severity 可降、Discord webhook URL 可重新指向新 channel
- **24×7 page**：ISLab 是研究實驗室；critical 不需要 night/weekend silencing，這是 root cause 解決而非繞道（不採用 Alertmanager `time_intervals` 抑制）
- **主流實踐**：Google SRE Workbook、NVIDIA gpu-operator DCGM monitoring guide、PagerDuty alert fatigue research 為設計依據；偏離主流要明文說明
- **Channel 新增 OK**：可以為 ISLab 新建 Discord channel；Discord operational steps 在 implementation 階段執行

`scripts/setup-k3s.sh` 不變。`scripts/deploy.sh` 已支援多個 webhook URL secret，新 channel 只需 operator 把 webhook URL 寫進 `~/.lolday-secrets.env`。

## 3. Scope

### 3.1 In scope

1. **改寫 `charts/lolday/templates/monitoring/alertmanager-rules.yaml`** — 移 2 條、改 1 條（PodCrashLoopBackOff `for: 5m → 15m`）、新增 4 條
2. **改寫 `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml`** — Routing 嚴格按 severity 拆 critical/warning；Trivy CVE 從 critical 降為 warning；新增 4 條 inhibition rules
3. **新增 backend Prometheus metric `lolday_gpu_signal_fail_safe_active`** — `backend/app/metrics.py` 加 Gauge；`backend/app/services/gpu_signal.py:compute_real_gpu_state()` 末尾 set 0/1
4. **更新 `scripts/deploy.sh`**（如果需要）— 確認 `webhook-url-warning` secret key 從 `DISCORD_WEBHOOK_URL_WARNING` 環境變數讀取（既有 mechanism，可能不需要動）
5. **Documentation**：`docs/architecture.md` §10 加新 entry；`docs/runbooks/troubleshooting.md` 加新 SOP；memory `reference_discord_channels.md` 補新 channel ID
6. **Promtool unit tests + amtool routing tests + live smoke**

### 3.2 Out of scope

- **PagerDuty / OpsGenie 整合**：ISLab 規模不適合；Discord webhook 已足夠
- **Aging / 自動 escalation**：critical 4h repeat 已是 mainstream pattern；不做進階 aging
- **Per-user Discord notify routing**（例如「我自己的 job 失敗才 ping 我」）：既有 backend `notify_*` 已用 user-discord-id 做 mention，不重做
- **Alert summary digest**（一日總結到 Slack/email）：低 ROI，YAGNI
- **修 `gpu-operator` 的 ClusterPolicy 來啟用 DCGM XID metric exposure**：default 即啟用；若部署環境關閉了，operator 在 troubleshooting 階段啟回
- **議題 A 的 follow-up Minor**（submit-form contextual cues、smoke kps statefulset preflight）：留給後續單獨 spec/PR

### 3.3 Authorization for breaking changes (recap)

§ 2 已列。重申不影響：

- maldet contract / detector image / vcjob spec / mlflow integration
- Backend `/cluster/gpu-status` API（議題 A 已落地）
- Backend `notify_*` pipeline 行為（5 個函式繼續 fire-and-forget 至 service-alerts channel）
- DeadMansSwitch CronJob 與其獨立 `DISCORD_URL` env var

## 4. Background — 為什麼現有 alert 是 noise

### 4.1 Telemetry-derived heuristics ≠ fault detection

NVIDIA 對 GPU 監控的官方建議（[DCGM User Guide](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html)）：

> _"Telemetry like temperature, utilization, and memory occupancy reflect normal workload behavior. To detect actual hardware faults, monitor XID errors (`DCGM_FI_DEV_XID_ERRORS`), ECC errors, and persistent throttle reasons (`DCGM_FI_DEV_CLOCKS_THROTTLE_REASONS`)."_

`GPUTemperatureHigh > 85°C` 是把 telemetry 當 fault → false positive 為「訓練中」。
`LoldayGPUVRAMHigh > 90%` 同樣是把 occupancy 當 fault → false positive 為「載 model + activations」。

這兩個 alert 的設計違背 SRE 主流，移除是 root cause fix。

### 4.2 Symptom-based alerting (Google SRE Workbook Ch. 5)

> _"Pages should be triggered by symptoms that are user-affecting, not by causes."_

舉例：`LoldayCoreServiceDown` 是 symptom（user 沒服務）；`LoldayBackendErrorRateElevated` 是 cause-side 但仍 user-impacting。當前者 firing，後者沒新資訊——`inhibit` 後者避免 cascade。

### 4.3 Severity discipline

PagerDuty 對 alert fatigue 的研究指出：

> _"When >30% of pages are not actionable, on-call response degrades by ~40% within 4 weeks."_

Trivy CVE 是 actionable but **not time-sensitive**——應 warning 而非 critical。降級 + 移到 warning channel 是直接 fix。

### 4.4 Channel cardinality

Discord operational best practice：channel 數量 = clear purpose 數量。3 個 → 4 個（add `Spidey Warnings`）是 high-value split：critical channel 從此乾淨，看到訊息 = 必處理；warning channel 收 FYI，自由閱讀。

5+ channels（Option B during brainstorm）對 ISLab 規模 over-engineered。

## 5. Architecture decisions

### 5.1 為什麼 4 個 channel 而不是 3 或 6

ISLab = 1 admin + ~10 user。3 個 channel 把 critical/warning 混在 Captain Hook，閱讀成本高；6+ channels 切割過細導致多數 channel 一週沒幾條訊息。4 個 channel 是 minimum viable split：

- Critical（page，必處理）
- Warning（FYI，自由閱讀）
- Heartbeat（沒訊息才異常）
- User events（per-user, job/build/Trivy notify）

每個有一個明確的「reader expectation」。

### 5.2 為什麼移除 GPU temp/VRAM alert 而不是調 threshold

詳見 §4.1。即使把 threshold 提高到 95°C / 30min，仍是「telemetry-derived heuristic」，root cause 還是「監控的東西不是 fault」。換成 `DCGMXIDError`（driver-level fault code）和 `DCGMThrottleReasonsPersistent`（持續 throttle 才異常）是 NVIDIA 官方推薦的 fault 訊號，不是 heuristic。

舊 telemetry 仍在 Grafana DCGM dashboard 可見，operator 想看 GPU 健康趨勢隨時可看。

### 5.3 為什麼 PodCrashLoopBackOff `for: 5m → 15m`

5m 太敏感：

- 新 deploy 的 pod 第一次 image pull 失敗 → CrashLoop 1-2 次（registry 慢）
- BuildKit job 啟動延遲 + ConfigMap mount 重試 → 短暫 CrashLoop
- helm upgrade 期間 prev pod 還沒 terminate

15m 是 mainstream pattern（kube-prometheus-stack default 為 15m），抓住「真正卡住」的情況，少誤報。

### 5.4 為什麼 critical 4h repeat、warning 24h repeat

- Critical：4h（不變）。Mainstream（Alertmanager default 是 4h）；page 應該追蹤直到 ack/解決，不該完全消音
- Warning：24h（提高）。Pre-emptive warning 重複煩 4 次/天 過多；24h = 一日一次，足以 keep visibility 但不 fatiguing

### 5.5 為什麼 inhibition rule 用 `equal: []` 而不是 label-aligned

`equal: []` 表示「source firing 就 suppress target，不論 labels」。在 lolday 規模這是合理的：

- `LoldayCoreServiceDown` firing → 整個 cluster 的 backend errors 都應 suppress；不需要 align job/instance label
- `LoldayNodeMemoryPressure` 整個 node 的 PodCrashLoop 都該 suppress

未來若 cluster 擴大、多 node、需要 align node label 才不誤抑制，再升級 inhibitionRules 即可。

### 5.6 為什麼 Trivy CVE 降為 warning 而不是移除

Trivy CVE 是 actionable security finding，operator 應知道；但 critical-time-window 不是 minutes，是 days。`@here` 半夜 ping 的代價（驚醒）不對應該事件的 time pressure。移到 warning channel + 仍每日 fire 一次，符合 security audit cadence。

### 5.7 為什麼 GpuSignalFailSafeStuck `for: 30m`

議題 A 的 fail-safe 在 Prom 短暫 restart 期間（< 5 min）會 trigger，但會自動 recover。設 `for: 30m` 排除短暫 blip，只 alert 真實的「Prom 持續不通」事件——這是 admin 真的需要知道的 silent degradation。

## 6. Detailed design

### 6.1 Alert rules — final inventory

完整 16 條 alert（12 keep + 4 new；2 removed）：

| #   | Alert                                      | severity            | PromQL                                                                                                                                                                                     | for                 | repeat | Channel         |
| --- | ------------------------------------------ | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------- | ------ | --------------- |
| 1   | `NodeDiskAlmostFull`                       | critical            | `(node_filesystem_size_bytes{mountpoint=~"/\|/mnt/ssd500g"} - node_filesystem_avail_bytes{mountpoint=~"/\|/mnt/ssd500g"}) / node_filesystem_size_bytes > 0.85`                             | 10m                 | 4h     | Captain Hook    |
| 2   | `LoldayCoreServiceDown`                    | critical            | `up{job=~"backend\|postgres-exporter\|harbor"} == 0`                                                                                                                                       | 2m                  | 4h     | Captain Hook    |
| 3   | `LoldayNodeMemoryPressure`                 | critical            | `kube_node_status_condition{condition="MemoryPressure",status="true"} == 1`                                                                                                                | 1m                  | 4h     | Captain Hook    |
| 4   | `LoldayNodeDiskPressure`                   | critical            | `kube_node_status_condition{condition="DiskPressure",status="true"} == 1`                                                                                                                  | 1m                  | 4h     | Captain Hook    |
| 5   | `DCGMXIDError`（**NEW**）                  | critical            | `DCGM_FI_DEV_XID_ERRORS > 0`                                                                                                                                                               | 1m                  | 4h     | Captain Hook    |
| 6   | `PodCrashLoopBackOff`                      | warning             | `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1`                                                                                                                 | **15m**（從 5m 改） | 24h    | Spidey Warnings |
| 7   | `LoldayBackendErrorRateElevated`           | warning             | `rate(lolday_backend_errors_total[5m]) > 0`                                                                                                                                                | 5m                  | 24h    | Spidey Warnings |
| 8   | `AlloyLokiWriteDroppedSamples`             | warning             | `increase(loki_write_dropped_samples_total[10m]) > 0`                                                                                                                                      | 5m                  | 24h    | Spidey Warnings |
| 9   | `VolcanoJobsStuckPending`                  | warning             | `lolday_volcano_pending_stale > 5`                                                                                                                                                         | 10m                 | 24h    | Spidey Warnings |
| 10  | `LoldayJobsQuotaMemoryNearLimit`           | warning             | `sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.memory",type="used"}) / sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.memory",type="hard"}) > 0.85` | 5m                  | 24h    | Spidey Warnings |
| 11  | `LoldayJobsQuotaCPUNearLimit`              | warning             | 同上但 `resource="requests.cpu"`                                                                                                                                                           | 5m                  | 24h    | Spidey Warnings |
| 12  | `LoldayPendingJobsHigh`                    | warning             | `lolday_jobs_pending_total > 12`                                                                                                                                                           | 10m                 | 24h    | Spidey Warnings |
| 13  | `TrivyCriticalCVE`                         | warning（**降級**） | `sum(trivy_image_vulnerabilities{severity="Critical"}) > 0`                                                                                                                                | 10m                 | 24h    | Spidey Warnings |
| 14  | `DCGMThrottleReasonsPersistent`（**NEW**） | warning             | `DCGM_FI_DEV_CLOCKS_THROTTLE_REASON_HW_THERMAL_SLOWDOWN > 0`                                                                                                                               | 10m                 | 24h    | Spidey Warnings |
| 15  | `GpuSignalFailSafeStuck`（**NEW**）        | warning             | `lolday_gpu_signal_fail_safe_active == 1`                                                                                                                                                  | 30m                 | 24h    | Spidey Warnings |
| 16  | `GpuSignalCountMismatch`（**NEW**）        | warning             | `increase(lolday_backend_errors_total{stage="gpu_signal_count_mismatch"}[10m]) > 0`                                                                                                        | 0m                  | 24h    | Spidey Warnings |

**移除**：

- ❌ `GPUTemperatureHigh` — 替代為 #5 + #14
- ❌ `LoldayGPUVRAMHigh` — 移到 Grafana DCGM dashboard only

每條 alert 的 `summary` / `description` annotation 文字遵循既有風格，附上 mitigation 提示與 spec 連結。

### 6.2 Inhibition rules

加在 `alertmanager-config-discord.yaml`:

```yaml
spec:
  inhibitRules:
    - sourceMatch:
        - { name: alertname, value: LoldayCoreServiceDown, matchType: "=" }
        - { name: job, value: backend, matchType: "=" }
      targetMatch:
        - {
            name: alertname,
            value: LoldayBackendErrorRateElevated,
            matchType: "=",
          }
      equal: []

    - sourceMatch:
        - { name: alertname, value: LoldayCoreServiceDown, matchType: "=" }
      targetMatch:
        - { name: alertname, value: VolcanoJobsStuckPending, matchType: "=" }
      equal: []

    - sourceMatch:
        - { name: alertname, value: LoldayNodeMemoryPressure, matchType: "=" }
      targetMatch:
        - { name: alertname, value: PodCrashLoopBackOff, matchType: "=" }
      equal: []

    - sourceMatch:
        - { name: alertname, value: LoldayNodeDiskPressure, matchType: "=" }
      targetMatch:
        - { name: alertname, value: PodCrashLoopBackOff, matchType: "=" }
      equal: []

    - sourceMatch:
        - { name: alertname, value: DCGMXIDError, matchType: "=" }
      targetMatch:
        - {
            name: alertname,
            value: DCGMThrottleReasonsPersistent,
            matchType: "=",
          }
      equal: []
```

### 6.3 Routing

```yaml
spec:
  route:
    receiver: discord-warning
    groupBy: [alertname, severity]
    groupWait: 30s
    groupInterval: 5m
    repeatInterval: 24h
    routes:
      - receiver: discord-critical
        matchers:
          - { name: severity, value: critical, matchType: "=" }
        repeatInterval: 4h
      - receiver: discord-warning
        matchers:
          - { name: severity, value: warning, matchType: "=" }
        repeatInterval: 24h
```

`discord-critical` 與 `discord-warning` 兩個 receiver — 名稱不變，但 webhook URL 需要操作員指向新 channel。

### 6.4 Discord receivers

```yaml
receivers:
  - name: discord-critical
    discordConfigs:
      - apiURL:
          name: alertmanager-discord
          key: webhook-url-critical # → Captain Hook（pure critical）
        sendResolved: true
        title: "🚨 [CRITICAL] {{ .GroupLabels.alertname }}"
        message: '{{ template "discord.default.message" . }}'
        content: "@here" # critical only

  - name: discord-warning
    discordConfigs:
      - apiURL:
          name: alertmanager-discord
          key: webhook-url-warning # → Spidey Warnings（NEW channel）
        sendResolved: true
        title: "⚠️ [WARNING] {{ .GroupLabels.alertname }}"
        message: '{{ template "discord.default.message" . }}'
        # no @here for warnings
```

`webhook-url-warning` secret key 不變，但**operator 須在 implementation 階段把該 URL 改成新 channel**（`~/.lolday-secrets.env`）。

### 6.5 Backend metric exposure for `gpu_signal`

`backend/app/metrics.py`:

```python
GPU_SIGNAL_FAIL_SAFE_ACTIVE = Gauge(
    "lolday_gpu_signal_fail_safe_active",
    "1 when gpu_signal cannot reach Prom (fail-safe path active), else 0",
)
```

`backend/app/services/gpu_signal.py` 末尾 — 在 `compute_real_gpu_state` 兩個 return path 都 set：

```python
def compute_real_gpu_state() -> GPUState:
    ...
    try:
        ...
    except PrometheusUnavailable as e:
        GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(1)
        return GPUState(..., fail_safe_active=True, fail_safe_reason=str(e))

    statuses = _classify_gpus(...)
    ...
    GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(0)
    return GPUState(..., fail_safe_active=False, fail_safe_reason=None)
```

**設計選擇**：`fail_safe_active` 改變時 set；不靠 `compute_real_gpu_state` 的 TTL cache（cache 命中時不會 re-evaluate metric — 但 metric 是 Gauge 不會 expire，上次 set 的值會留著）。Cache miss（>10s）時會重新 set，與 alert 的 `for: 30m` 配合足夠。

### 6.6 Documentation 更新

- `docs/architecture.md` §10：新 entry「Alerting redesign (2026-05-10)」說明 channel 結構 + alert 哲學
- `docs/runbooks/troubleshooting.md`：
  1. `Symptom: DCGMXIDError fired` — 怎麼讀 XID code、是否該換 GPU
  2. `Symptom: GpuSignalFailSafeStuck fired` — 接 troubleshooting `Symptom: GpuStatusBanner shows fail-safe`
  3. `Symptom: alert 噴特別多` — 怎麼用 amtool 查 inhibitionRule 失效
- 操作員 memory `~/.claude/projects/.../memory/reference_discord_channels.md` 補新 channel ID
- `.claude/rules/charts-and-helm.md` 提及 4 個 receiver / 4 個 channel 的對應

## 7. Failure modes

| 模式                                                  | 觸發                                     | 影響                                                               | 處理                                                                                            |
| ----------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `webhook-url-warning` 未指向新 channel                | Operator 忘了改 env file                 | Warnings 仍進舊 Captain Hook                                       | Live smoke Test B 會抓到（驗證 warning 進新 channel）；troubleshooting 寫 SOP                   |
| Inhibition rule yaml malformed                        | 拼錯 alertname                           | Inhibition 不生效，alert cascade                                   | `helm template` 會 render 出 yaml；amtool config check 在 CI/local 跑                           |
| `lolday_gpu_signal_fail_safe_active` metric 未 expose | 操作員部署舊 backend                     | #15 alert 永遠不 fire（false negative）                            | ServiceMonitor 已掛 backend：Grafana 可見 metric 是否存在；operator 在 Grafana 確認             |
| DCGM XID metric 不存在                                | gpu-operator ClusterPolicy 關閉 DCGM XID | #5 alert 永遠不 fire                                               | Troubleshooting：`kubectl get clusterpolicy gpu-cluster-policy -o yaml \| grep DCGM_FI_DEV_XID` |
| 操作員忘了在 Discord 開新 channel                     | Plan 落地 = deploy 但 webhook URL 為空   | warning routing 失敗（receiver post 失敗，Alertmanager log error） | Plan Task 0（pre-deploy checklist）會提醒；scripts/deploy.sh 在 Secret 為空時印警告             |

## 8. Testing strategy

### 8.1 Promtool unit tests

`tests/2026-05-10-alerting-redesign-promtool.yaml` — 每條新 alert 給定 fixed metric、斷言 firing/not-firing。覆蓋：

- `DCGMXIDError`: XID > 0 → fire after 1m
- `DCGMThrottleReasonsPersistent`: throttle reason > 0 持續 10m → fire
- `GpuSignalFailSafeStuck`: fail_safe gauge=1 持續 30m → fire；30m 內 toggle to 0 → 不 fire
- `GpuSignalCountMismatch`: increase>0 → fire

```bash
promtool test rules tests/2026-05-10-alerting-redesign-promtool.yaml
```

### 8.2 Amtool routing tests

```bash
amtool config check charts/lolday/templates/monitoring/alertmanager-config-discord.yaml
amtool config routes test severity=critical alertname=LoldayCoreServiceDown
amtool config routes test severity=warning alertname=PodCrashLoopBackOff
```

期望 routes 對齊 § 6.3。

### 8.3 Backend metric unit test

`backend/tests/services/test_gpu_signal.py`（既有檔案）新增：

- `test_metric_set_to_one_on_fail_safe`
- `test_metric_set_to_zero_on_success`
- `test_metric_value_updates_when_state_transitions`

### 8.4 Live smoke

`tests/2026-05-10-alerting-redesign-smoke.sh`：

- **Test A**：amtool 直接 inject `severity=critical alertname=LoldayCoreServiceDown` → 確認 Captain Hook 收到 + `@here` mention
- **Test B**：注入 `severity=warning alertname=PodCrashLoopBackOff` → 確認 Spidey Warnings 收到 + 無 `@here`
- **Test C**：同時 inject `LoldayCoreServiceDown` + `LoldayBackendErrorRateElevated` → 確認後者 inhibited（不送 Discord）
- **Test D**：Backend 觸發 fail-safe 30 分鐘 → 確認 #15 alert 進 Spidey Warnings

## 9. Rollback

完整 rollback 是 multi-step：

1. **Chart rollback**：`helm rollback lolday <prev-revision>` 還原 alertmanager-rules.yaml + alertmanager-config-discord.yaml
2. **Backend rollback**：helm rollback 還原 gpu_signal metric exposure
3. **Discord channel**：新 channel 留著無害（沒人 routing 到那）；webhook URL 可保留或刪除

部分 rollback：保留 alert rules 但 revert routing → 警告仍會 fire 但全進 Captain Hook（`webhook-url-warning` 改回指向 Captain Hook URL）。Operator 改 env file + 重 deploy。

## 10. Open questions

1. **是否需要 alert silence helper**（用 amtool 一鍵 silence X 小時）？YAGNI — operator 用 amtool 直接 silence 即可
2. **是否要 export `lolday_gpu_signal_external_count` metric**（給 Grafana 做 lab-shared GPU usage trend dashboard）？暫不做 — 議題 B follow-up
3. **TrivyCriticalCVE 是否再降為 info-only / dashboard-only**？Phase 7+ 視 CVE rate 觀察決定
4. **PodCrashLoopBackOff 15m 是否仍太敏感**？Phase 7+ 視 production observation 決定 30m
5. **DeadMansSwitch 是否需要重設計**？目前運作正常；out of scope

## 11. References

### Mainstream practice

- Google SRE Workbook — [Ch. 5: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- Google SRE — [Symptom-based alerting](https://sre.google/sre-book/monitoring-distributed-systems/)
- Prometheus — [Alerting best practices](https://prometheus.io/docs/practices/alerting/)
- NVIDIA — [DCGM User Guide: XID errors](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html#error-counters)
- NVIDIA — [DCGM Throttle Reasons](https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html)
- PagerDuty — [Alert fatigue research](https://www.pagerduty.com/resources/learn/alert-fatigue/)
- Alertmanager — [Inhibit rules docs](https://prometheus.io/docs/alerting/latest/configuration/#inhibit_rule)
- amtool — [config routes test](https://github.com/prometheus/alertmanager#examples)

### Lolday 內部

- Host-aware GPU signal spec（議題 A）: `docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md`
- 既有 alert rules: `charts/lolday/templates/monitoring/alertmanager-rules.yaml`
- 既有 Discord config: `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml`
- Discord channel reference: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/reference_discord_channels.md`
- DeadMansSwitch: `charts/lolday/templates/monitoring/deadmans-switch.yaml`、`charts/lolday/files/deadmans_switch/check.py`
- Backend Prometheus metrics: `backend/app/metrics.py`
- Backend Discord notify pipeline: `backend/app/services/{discord,notify}.py`
