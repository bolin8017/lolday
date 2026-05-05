# Phase 0: K3s Kubelet Args (Host Safety Net) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `kube-reserved` / `system-reserved` / memory `eviction-hard` / memory + disk `eviction-soft` to server30 K3s kubelet so the Linux global OOM Killer can never reach kubelet/sshd; closes the worst failure mode in `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §5.1.

**Architecture:** Two delivery paths. (a) Edit `scripts/setup-k3s.sh` for fresh installs — adds `INSTALL_K3S_EXEC` env. (b) Author new `scripts/patch-k3s-kubelet-args.sh` to upgrade the existing server30 cluster in place by editing `/etc/systemd/system/k3s.service` (drop-in style: `/etc/systemd/system/k3s.service.d/10-lolday-kubelet-args.conf`). Both routes converge to the same final kubeletconfig. SSH safety hard rule applies — `systemctl restart k3s` requires operator-confirmed dry-run.

**Tech Stack:** bash, systemd unit drop-ins, K3s installer, kubectl, jq.

**Spec:** `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` — read §4 (current state audit), §5.1 (root cause), §7 Phase 0 (numerical values + rollback), §11 (rollback strategy) before starting.

---

## Reference: chosen kubelet flags

These are the canonical strings used in every script + doc below. **Do not paraphrase or re-derive** — copy verbatim.

```
--kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi
--kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi
--kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10%
--kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15%
--kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m
--kubelet-arg=eviction-max-pod-grace-period=60
```

Rationale for each value: spec §7 Phase 0 table.

---

## File map

**New files:**

- `scripts/patch-k3s-kubelet-args.sh` — idempotent in-place patcher (operator runs)
- `tests/2026-05-05-kubelet-args-smoke.sh` — post-apply verification (shell smoke, run manually like other `tests/phase7/*.sh`)

**Modified files:**

- `scripts/setup-k3s.sh` — add `INSTALL_K3S_EXEC` env to fresh-install path
- `docs/runbooks/deploy.md` — new section "Upgrading kubelet args on existing K3s"
- `docs/architecture.md` §9 / §10 — add the new defense layer entry; mark "no host-level memory partition" as resolved
- `CLAUDE.md` — bump quickstart with the patch script (one line)

**Not touched in this PR:** anything under `charts/lolday/` or `backend/` — they belong to Phase 1+.

---

## Execution order

```
Wave 0 (sequential — author scripts/docs)
├── Task 1: branch ready (already on feat/gpu-scheduling-oom-defense)
├── Task 2: edit scripts/setup-k3s.sh
├── Task 3: create scripts/patch-k3s-kubelet-args.sh (with --dry-run)
├── Task 4: create tests/2026-05-05-kubelet-args-smoke.sh
└── Task 5: update runbook + architecture.md + CLAUDE.md

Wave 1 (sequential — operator-attended live patch)
├── Task 6: pre-flight snapshot
├── Task 7: dry-run + operator SSH-safety verification (HARD RULE)
├── Task 8: apply (systemctl restart k3s)
├── Task 9: post-apply verification
└── Task 10: smoke test (negative test)

Wave 2 (sequential — close out)
├── Task 11: pre-commit + commit
├── Task 12: push + open PR
└── Task 13: PR review + merge
```

Wave 0 = pure Claude. Wave 1 = operator-attended (Claude assists, operator runs the destructive lines). Wave 2 = pure Claude.

---

## Task 1: Branch confirmation

**Files:** none

- [ ] **Step 1: Verify the working branch**

```bash
git rev-parse --abbrev-ref HEAD
```

Expected: `feat/gpu-scheduling-oom-defense` (already created earlier in the brainstorming session).

If a different branch:

```bash
git checkout -b feat/gpu-scheduling-oom-defense
```

- [ ] **Step 2: Verify clean tree**

```bash
git status --porcelain
```

Expected: only the spec + plan markdown files staged or untracked. No code touched yet.

---

## Task 2: Edit `scripts/setup-k3s.sh`

**Files:**

- Modify: `scripts/setup-k3s.sh:42-52`

This task is for **fresh K3s installs** only. Existing server30 keeps running; Task 3+ handles in-place upgrade.

- [ ] **Step 1: Replace the install command**

Current (line 46):

```bash
curl -sfL https://get.k3s.io | sh -
```

Replace with:

```bash
INSTALL_K3S_EXEC="server \
  --kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi \
  --kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi \
  --kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10% \
  --kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15% \
  --kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m \
  --kubelet-arg=eviction-max-pod-grace-period=60" \
curl -sfL https://get.k3s.io | sh -
```

Note: the `INSTALL_K3S_EXEC=` line and the `curl` line stay separate — that's how the K3s installer reads it (env var carried into the sh subshell).

- [ ] **Step 2: Update the surrounding echo**

Adjust the banner at line 42 from `(default Flannel + network policy)` to `(default Flannel + network policy + host safety reservations)`.

- [ ] **Step 3: Lint check**

Run:

```bash
pre-commit run shellcheck --files scripts/setup-k3s.sh
```

If `shellcheck` is configured. Otherwise skip — pre-commit framework will catch it later.

---

## Task 3: Create `scripts/patch-k3s-kubelet-args.sh`

**Files:**

- Create: `scripts/patch-k3s-kubelet-args.sh`

Idempotent in-place patcher. Writes a systemd drop-in at `/etc/systemd/system/k3s.service.d/10-lolday-kubelet-args.conf`, then `daemon-reload` + `restart k3s`. Has `--dry-run` mode (default) and `--apply` mode.

- [ ] **Step 1: Author the script**

```bash
cat > scripts/patch-k3s-kubelet-args.sh <<'SCRIPT'
#!/usr/bin/env bash
# Patch K3s kubelet args on an existing server30 install.
#
# Adds kube-reserved + system-reserved + memory eviction so the Linux global
# OOM Killer can never reach kubelet. See:
#   docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §5.1
#
# Idempotent: re-runnable; always rewrites the drop-in to match the canonical
# values. SSH safety hard rule (CLAUDE.md): operator must verify SSH from a
# fresh session before --apply.
#
# Usage:
#   sudo bash scripts/patch-k3s-kubelet-args.sh             # dry-run (default)
#   sudo bash scripts/patch-k3s-kubelet-args.sh --apply     # actually patch + restart k3s
#   sudo bash scripts/patch-k3s-kubelet-args.sh --revert    # remove the drop-in
set -euo pipefail

DROPIN_DIR=/etc/systemd/system/k3s.service.d
DROPIN_FILE=${DROPIN_DIR}/10-lolday-kubelet-args.conf

# Canonical kubelet args — keep in sync with scripts/setup-k3s.sh
read -r -d '' EXEC_OVERRIDE <<'CONF' || true
[Service]
ExecStart=
ExecStart=/usr/local/bin/k3s server \
  --kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi \
  --kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi \
  --kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10% \
  --kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15% \
  --kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m \
  --kubelet-arg=eviction-max-pod-grace-period=60
CONF

mode=dry-run
[ "${1:-}" = "--apply" ] && mode=apply
[ "${1:-}" = "--revert" ] && mode=revert

if [ "$(id -u)" -ne 0 ]; then
  echo "[fatal] this script must be run with sudo" >&2
  exit 1
fi

if ! systemctl is-active --quiet ssh; then
  echo "[fatal] ssh service is not active — aborting to prevent lockout" >&2
  exit 1
fi

case "$mode" in
  dry-run)
    echo "[mode] dry-run (no changes)"
    echo "[plan] would write the following to ${DROPIN_FILE}:"
    echo "----"
    echo "${EXEC_OVERRIDE}"
    echo "----"
    echo "[plan] would then run: systemctl daemon-reload && systemctl restart k3s"
    echo "[plan] expected effect: ~30 seconds k3s server restart; pod runtime unaffected"
    echo ""
    echo "[next] re-run with --apply after verifying SSH from a fresh session"
    ;;
  apply)
    echo "[step 1/4] writing drop-in ${DROPIN_FILE}"
    mkdir -p "${DROPIN_DIR}"
    printf '%s\n' "${EXEC_OVERRIDE}" > "${DROPIN_FILE}"
    chmod 644 "${DROPIN_FILE}"

    echo "[step 2/4] daemon-reload"
    systemctl daemon-reload

    echo "[step 3/4] restart k3s"
    echo "[warn] expect ~30s control-plane downtime; SSH stays alive"
    systemctl restart k3s

    echo "[step 4/4] waiting for kubelet ready (60s timeout)..."
    for i in $(seq 1 30); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready after ${i}×2s"
        break
      fi
      sleep 2
    done

    if ! kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
      echo "[fatal] kubelet not ready after 60s" >&2
      echo "[hint] check 'journalctl -u k3s --since=2min' and 'systemctl cat k3s'" >&2
      exit 2
    fi

    echo "[done] applied. verify next:"
    echo "  kubectl get --raw /api/v1/nodes/server30/proxy/configz | jq .kubeletconfig"
    ;;
  revert)
    echo "[step 1/3] removing drop-in"
    rm -f "${DROPIN_FILE}"
    rmdir --ignore-fail-on-non-empty "${DROPIN_DIR}" || true

    echo "[step 2/3] daemon-reload"
    systemctl daemon-reload

    echo "[step 3/3] restart k3s"
    systemctl restart k3s

    for i in $(seq 1 30); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready"
        break
      fi
      sleep 2
    done
    echo "[done] reverted"
    ;;
esac
SCRIPT
chmod +x scripts/patch-k3s-kubelet-args.sh
```

- [ ] **Step 2: Lint**

```bash
bash -n scripts/patch-k3s-kubelet-args.sh
```

Expected: no output (syntax OK).

- [ ] **Step 3: Self-test dry-run mode (without sudo)**

Without sudo, the script should fail-fast with clear error message:

```bash
bash scripts/patch-k3s-kubelet-args.sh
```

Expected output:

```
[fatal] this script must be run with sudo
```

(Exit code 1.)

---

## Task 4: Create smoke test

**Files:**

- Create: `tests/2026-05-05-kubelet-args-smoke.sh`

Verifies the new kubeletconfig values are actually live.

- [ ] **Step 1: Author the smoke test**

```bash
cat > tests/2026-05-05-kubelet-args-smoke.sh <<'SCRIPT'
#!/usr/bin/env bash
# Smoke: verify Phase 0 kubelet args landed.
# Run manually post-apply (no automation).
set -euo pipefail

NODE=${NODE:-server30}
fail=0

echo "[step 1/4] kubeletconfig has kubeReserved/systemReserved set"
kc=$(kubectl get --raw "/api/v1/nodes/${NODE}/proxy/configz")
echo "$kc" | python3 -c "
import json, sys
d = json.load(sys.stdin)['kubeletconfig']
errs = []
if d.get('kubeReserved', {}).get('memory') != '2Gi':
    errs.append(f'kubeReserved.memory expected 2Gi, got {d.get(\"kubeReserved\")}')
if d.get('systemReserved', {}).get('memory') != '4Gi':
    errs.append(f'systemReserved.memory expected 4Gi, got {d.get(\"systemReserved\")}')
if d.get('evictionHard', {}).get('memory.available') != '1Gi':
    errs.append(f'evictionHard.memory.available expected 1Gi, got {d.get(\"evictionHard\")}')
if d.get('evictionSoft', {}).get('memory.available') != '2Gi':
    errs.append(f'evictionSoft.memory.available expected 2Gi, got {d.get(\"evictionSoft\")}')
if errs:
    print('FAIL:')
    for e in errs: print(' -', e)
    sys.exit(1)
print('OK')
"

echo ""
echo "[step 2/4] node Allocatable shrunk vs Capacity"
delta=$(kubectl get node "${NODE}" -o json | python3 -c "
import json, sys
n = json.load(sys.stdin)
def parse(s):
  if s.endswith('Ki'): return int(s[:-2])
  return int(s)
cap = parse(n['status']['capacity']['memory'])
alloc = parse(n['status']['allocatable']['memory'])
delta_gi = (cap - alloc) / 1024 / 1024
print(f'{delta_gi:.1f}')
")

# kube=2Gi + system=4Gi + eviction-hard=1Gi = ~7Gi
if (( $(echo "$delta < 6.0 || $delta > 8.0" | bc -l) )); then
  echo "FAIL: Capacity-Allocatable delta ${delta} GiB; expected ~7 GiB"
  fail=1
else
  echo "OK: delta = ${delta} GiB (close to expected 7)"
fi

echo ""
echo "[step 3/4] systemd drop-in present"
if [ -f /etc/systemd/system/k3s.service.d/10-lolday-kubelet-args.conf ]; then
  echo "OK"
else
  echo "FAIL: drop-in file missing"
  fail=1
fi

echo ""
echo "[step 4/4] no NodeMemoryPressure right now"
mp=$(kubectl get node "${NODE}" -o json | python3 -c "
import json, sys
for c in json.load(sys.stdin)['status']['conditions']:
  if c['type'] == 'MemoryPressure':
    print(c['status'])
")
if [ "$mp" = "False" ]; then
  echo "OK: MemoryPressure=False"
else
  echo "FAIL: MemoryPressure=${mp}"
  fail=1
fi

echo ""
if [ $fail -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
SCRIPT
chmod +x tests/2026-05-05-kubelet-args-smoke.sh
```

- [ ] **Step 2: Lint**

```bash
bash -n tests/2026-05-05-kubelet-args-smoke.sh
```

Expected: clean (no syntax error).

---

## Task 5: Update runbook + architecture.md + CLAUDE.md

**Files:**

- Modify: `docs/runbooks/deploy.md` (new section)
- Modify: `docs/architecture.md` §10 + §9
- Modify: `CLAUDE.md` (one line in quickstart)

- [ ] **Step 1: Add runbook section**

Append a new section to `docs/runbooks/deploy.md` before the final reference / appendix section. The section text:

````markdown
## Upgrading kubelet args on an existing K3s

When `scripts/setup-k3s.sh` evolves to add new `--kubelet-arg=...`, the
existing server30 install does **not** auto-pick them up. Use the patch
script to upgrade in place.

```bash
# 1. dry-run — prints the systemd drop-in that would be written
sudo bash scripts/patch-k3s-kubelet-args.sh

# 2. SSH safety verify (CLAUDE.md hard rule)
#    operator opens a SECOND ssh session to server30, leaves it idle as canary
#    that session must stay alive across the restart

# 3. apply — writes drop-in, daemon-reload, restart k3s, waits for Ready
sudo bash scripts/patch-k3s-kubelet-args.sh --apply

# 4. verify
bash tests/2026-05-05-kubelet-args-smoke.sh

# 5. (rollback path) — same script
sudo bash scripts/patch-k3s-kubelet-args.sh --revert
```
````

Expected effects of `--apply`:

- ~30 s K3s server restart. SSH unaffected (control plane only).
- Pod runtime (containerd) does **not** restart; in-flight detector pods keep running.
- New `Allocatable.memory` ≈ Capacity − 7 GiB (= kube 2 + system 4 + eviction-hard 1).

````

- [ ] **Step 2: Update architecture.md §10 (Common gotchas)**

Add a new gotcha entry numbered after the existing ones:

```markdown
13. **Host RAM partition** — kubelet runs with `kube-reserved=memory=2Gi`,
    `system-reserved=memory=4Gi`, `eviction-hard=memory.available<1Gi`,
    `eviction-soft=memory.available<2Gi grace 2m` since 2026-05-05
    (Phase 0 of the GPU scheduling & OOM defense design). Allocatable
    memory is therefore **62 GB − 7 GB = 55 GB**, not the raw Capacity.
    Bumping these requires editing both `scripts/setup-k3s.sh`
    (fresh installs) and re-running `scripts/patch-k3s-kubelet-args.sh
    --apply` (existing cluster). Don't forget the second one.
````

(Insert at the end of the §10 list, renumbering nothing else.)

- [ ] **Step 3: Update architecture.md §9 (tech debt)** — close the row

If §9 listed "no host-level memory partition" as tech debt, mark it resolved with the same strikethrough + date pattern used by other resolved items. (As of 2026-05-05 audit it's not listed yet — skip if absent.)

Manually scan §9 for any of: `kube-reserved`, `system-reserved`, `host RAM`, `OOM Killer`. None expected; skip if no hit.

- [ ] **Step 4: CLAUDE.md quickstart — add one line**

In the existing `## Quickstart commands` block, after the `bash scripts/install-tools.sh` line, append:

```bash
sudo bash scripts/patch-k3s-kubelet-args.sh         # safety patch on existing K3s
```

Place the comment so the column lines up with the others in the block.

- [ ] **Step 5: pre-commit run**

```bash
pre-commit run --files docs/runbooks/deploy.md docs/architecture.md CLAUDE.md scripts/setup-k3s.sh scripts/patch-k3s-kubelet-args.sh tests/2026-05-05-kubelet-args-smoke.sh
```

Fix any issues.

---

## Task 6: Pre-flight snapshot (operator-attended)

**Files:** none (just observation)

This is the "before" snapshot to compare against after Task 8.

- [ ] **Step 1: Capture current kubeletconfig**

```bash
kubectl get --raw /api/v1/nodes/server30/proxy/configz | python3 -c "
import json, sys
d = json.load(sys.stdin)['kubeletconfig']
print('kubeReserved:', d.get('kubeReserved'))
print('systemReserved:', d.get('systemReserved'))
print('evictionHard:', d.get('evictionHard'))
print('evictionSoft:', d.get('evictionSoft'))
" > /tmp/kubelet-before.txt
cat /tmp/kubelet-before.txt
```

Expected output (matches §4.2 of the spec):

```
kubeReserved: None
systemReserved: None
evictionHard: {'imagefs.available': '5%', 'nodefs.available': '5%'}
evictionSoft: None
```

- [ ] **Step 2: Capture current Allocatable**

```bash
kubectl describe node server30 | grep -A 8 -E "^(Capacity|Allocatable):" > /tmp/alloc-before.txt
cat /tmp/alloc-before.txt
```

- [ ] **Step 3: Capture currently running workload**

```bash
kubectl get vcjobs -A 2>/dev/null || kubectl get jobs.batch.volcano.sh -A
kubectl get pods -A --field-selector=status.phase=Running | wc -l
```

Note the workload state in case verification later fails and we need to attribute.

---

## Task 7: Dry-run + operator SSH-safety verification (HARD RULE)

**Files:** none (operator action)

> ⚠️ **CLAUDE.md hard rule applies:** SSH on server30 has no out-of-band fallback. The 2026-03-31 Cilium incident was the precedent. Do not skip this task.

- [ ] **Step 1: Dry-run the patch script**

Operator runs (Claude can read output):

```bash
sudo bash scripts/patch-k3s-kubelet-args.sh
```

Expected: prints planned drop-in content + planned `daemon-reload` + `restart k3s`. **No changes** made.

- [ ] **Step 2: Operator opens a SECOND SSH session to server30**

The second session is a canary. Operator confirms in chat:

> "Second SSH session is open and idle on server30 port 9453 from a different terminal."

Do not proceed without this confirmation.

- [ ] **Step 3: Operator confirms readiness for apply**

Operator types "ready" or "apply" in chat. Claude does not run the apply itself — operator-attended only.

---

## Task 8: Apply (operator runs the destructive line)

**Files:** none (operator-attended)

- [ ] **Step 1: Operator runs apply**

```bash
sudo bash scripts/patch-k3s-kubelet-args.sh --apply
```

Expected output sequence:

```
[step 1/4] writing drop-in /etc/systemd/system/k3s.service.d/10-lolday-kubelet-args.conf
[step 2/4] daemon-reload
[step 3/4] restart k3s
[warn] expect ~30s control-plane downtime; SSH stays alive
[step 4/4] waiting for kubelet ready (60s timeout)...
[ok] kubelet ready after 5×2s
[done] applied. verify next: ...
```

- [ ] **Step 2: Operator verifies SSH still works**

The canary SSH session from Task 7 step 2 should still respond. Operator confirms.

If SSH is broken: see `docs/postmortems/2026-03-31-cilium-ssh-incident.md` recovery procedure. **Do not panic, do not restart anything else.**

---

## Task 9: Post-apply verification

**Files:** none

Claude runs these and reports back.

- [ ] **Step 1: Verify kubeletconfig has new values**

```bash
kubectl get --raw /api/v1/nodes/server30/proxy/configz | python3 -c "
import json, sys
d = json.load(sys.stdin)['kubeletconfig']
print('kubeReserved:', d.get('kubeReserved'))
print('systemReserved:', d.get('systemReserved'))
print('evictionHard:', d.get('evictionHard'))
print('evictionSoft:', d.get('evictionSoft'))
"
```

Expected:

```
kubeReserved: {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '10Gi'}
systemReserved: {'cpu': '1', 'memory': '4Gi', 'ephemeral-storage': '10Gi'}
evictionHard: {'memory.available': '1Gi', 'nodefs.available': '10%', 'imagefs.available': '10%'}
evictionSoft: {'memory.available': '2Gi', 'nodefs.available': '15%'}
```

If any value is `None` or wrong: rollback (Task 9 step 4).

- [ ] **Step 2: Verify Allocatable shrunk by ~7 GiB**

```bash
kubectl describe node server30 | grep -A 8 -E "^(Capacity|Allocatable):"
```

Expected: `Capacity.memory = 65748876Ki` (unchanged); `Allocatable.memory ≈ 58407500Ki ± a few KB` (= 62 GiB − 7 GiB ≈ 55 GiB; in Ki scale roughly 58407500). The exact number may shift slightly because K3s rounds.

- [ ] **Step 3: Verify SSH still active (final)**

```bash
systemctl is-active ssh
```

Expected: `active`.

- [ ] **Step 4: ROLLBACK PATH (only if verification fails)**

```bash
sudo bash scripts/patch-k3s-kubelet-args.sh --revert
```

Then re-run Task 9 Step 1; expected to match Task 6 Step 1 (`None`/`None`/`{imagefs:5%, nodefs:5%}`/`None`).

---

## Task 10: Smoke test

**Files:** none

- [ ] **Step 1: Run smoke**

```bash
bash tests/2026-05-05-kubelet-args-smoke.sh
```

Expected: `=== SMOKE PASSED ===` + `OK` for all 4 steps.

If smoke fails: investigate the failing step first. Don't roll back unless Task 9 verification also fails — smoke is stricter than the kubelet API.

- [ ] **Step 2: (Optional) Negative test — trigger MemoryPressure**

Skip in production. In a controlled dev environment (if one existed): `stress-ng --vm 2 --vm-bytes 60G --timeout 60s` and observe `kube_node_status_condition{condition="MemoryPressure"}` flip to 1, then a Burstable pod evicted. **Do not run this in production server30.**

---

## Task 11: pre-commit + commit

**Files:**

- All Phase 0 changes (new scripts, modified docs, plan + spec from earlier)

- [ ] **Step 1: Stage**

```bash
git add scripts/setup-k3s.sh scripts/patch-k3s-kubelet-args.sh \
  tests/2026-05-05-kubelet-args-smoke.sh \
  docs/runbooks/deploy.md docs/architecture.md CLAUDE.md \
  docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md \
  docs/superpowers/plans/2026-05-05-gpu-scheduling-phase0-kubelet-args.md
```

- [ ] **Step 2: pre-commit (hard rule: never `--no-verify`)**

```bash
pre-commit run --all-files
```

If a hook fails: read its message, fix root cause, re-stage, re-run. Do **not** bypass with `--no-verify`.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'MSG'
feat(infra): phase 0 — host-level kubelet reservations + memory eviction

Adds kube-reserved, system-reserved, memory eviction-hard / eviction-soft
to server30 K3s so the Linux global OOM Killer can never reach kubelet
or sshd. Closes the worst failure mode in the GPU scheduling & OOM
defense design (spec §5.1).

Two delivery paths:
- scripts/setup-k3s.sh: INSTALL_K3S_EXEC env for fresh installs
- scripts/patch-k3s-kubelet-args.sh: idempotent in-place patcher
  (--dry-run / --apply / --revert) for the existing server30 cluster

Allocatable.memory after apply: 62 GiB − 7 GiB = 55 GiB (= kube 2 +
system 4 + eviction-hard 1). Smoke test under tests/.

Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md
Plan: docs/superpowers/plans/2026-05-05-gpu-scheduling-phase0-kubelet-args.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 12: Push + open PR

**Files:** none (gh CLI)

- [ ] **Step 1: Push**

```bash
git push -u origin feat/gpu-scheduling-oom-defense
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(infra): phase 0 — host-level kubelet reservations + memory eviction" --body "$(cat <<'EOF'
## Summary

- Adds K3s kubelet `kube-reserved=memory=2Gi`, `system-reserved=memory=4Gi`, `eviction-hard memory.available<1Gi`, `eviction-soft memory.available<2Gi grace 2m` so the Linux global OOM Killer can never reach kubelet/sshd.
- New idempotent in-place patcher `scripts/patch-k3s-kubelet-args.sh` (--dry-run / --apply / --revert) for upgrading the existing server30 cluster without reinstalling K3s.
- Updates `scripts/setup-k3s.sh` so fresh installs ship with the same reservations from day one.
- Smoke test at `tests/2026-05-05-kubelet-args-smoke.sh`.

This is **Phase 0 of 5** in the GPU scheduling & OOM defense design — the most urgent layer because it prevents OOM-Killer-induced cluster collapse. Subsequent phases (namespace separation, Volcano per-user queue, GPU1 profile, alerts, per-job deadlines) ship as independent PRs.

Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md`
Plan: `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase0-kubelet-args.md`

## Test plan

- [x] `bash -n` syntax check on both new scripts
- [x] `pre-commit run --all-files` clean
- [x] dry-run on server30 prints the planned drop-in without touching anything
- [ ] operator opens canary SSH session, runs `--apply`, k3s restarts in ~30s, kubelet returns Ready
- [ ] post-apply: `kubectl get --raw /api/v1/nodes/server30/proxy/configz | jq .kubeletconfig` matches the canonical values
- [ ] post-apply: `kubectl describe node server30` Allocatable.memory ≈ 55 GiB
- [ ] `bash tests/2026-05-05-kubelet-args-smoke.sh` returns `=== SMOKE PASSED ===`
- [ ] SSH session on server30 stays alive across the restart

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Capture PR URL**

`gh pr view --json url -q .url` — record for the operator.

---

## Task 13: PR review + merge (operator-driven)

**Files:** none

- [ ] **Step 1: Wait for CI to go green**

Six required workflows: lint, backend, frontend, helm, images, helpers. Path-filtered ones may skip — that's expected.

```bash
gh pr checks <pr-number> --watch
```

- [ ] **Step 2: Operator self-reviews + merges**

`gh pr merge <pr-number> --squash`

- [ ] **Step 3: Apply on server30**

After merge, operator pulls main and runs `--apply` (Tasks 6–10). Until applied on server30, the PR is "merged but not effective" — note in the PR comment.

---

## Out of scope for this plan

- Anything under `charts/lolday/` — Phase 1.
- Anything under `backend/` — Phase 2+.
- Per-user Volcano queue / GPU1 profile / alerts / per-job deadline — Phases 1–5.
- `node-problem-detector` / `prometheus-node-exporter` extra collectors — already deployed via kps; not relevant.

## Self-review checklist

After all Wave 0 tasks, before triggering Wave 1:

- [ ] Every kubelet flag value matches §7 Phase 0 of the spec exactly (cpu=1, memory=2Gi, etc.)
- [ ] `setup-k3s.sh` and `patch-k3s-kubelet-args.sh` produce identical kubeletconfig
- [ ] Smoke test asserts the exact expected values, not "non-empty"
- [ ] Runbook has a verbatim "operator opens second SSH session" step
- [ ] Rollback path is documented and tested in `--revert` mode
- [ ] No reference to "TBD" / "later" / "TODO" in the plan
- [ ] PR title follows Conventional Commits (`feat(infra): ...`)
- [ ] No bypass of pre-commit (`--no-verify`) anywhere
