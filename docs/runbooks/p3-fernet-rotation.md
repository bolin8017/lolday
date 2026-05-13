# Fernet key rotation — operator runbook (P3)

**Scope:** rotating the backend's `FERNET_KEYS` symmetric key without losing
the encrypted-at-rest data in `user_git_credential.encrypted_token`.

**Cadence:** ad-hoc (after credential leak suspicion, key compromise, or
periodic hygiene — recommended annually). Each rotation is one maintenance
window of ≤30 min.

**Why a script, not a hot rotation:** every encrypted column has to be
re-encrypted under the new key. The `MultiFernet` primitive lets the running
backend decrypt under either key during a 24 h window, but the offline
re-encryption is one transaction per row and aborts cleanly on any
unrotatable row.

## Pre-flight (T-10 min)

- [ ] Confirm `helm upgrade --install lolday` is healthy: `kubectl -n lolday get pods`.
- [ ] Confirm a recent Postgres backup exists (`kubectl -n lolday exec deploy/postgresql -- pg_dump ... > backup.sql`).
- [ ] Confirm operator workstation has the current `FERNET_KEYS` value in `.lolday-secrets.env`.

## Procedure

```text
# T-0 = maintenance window start

# 1. Cordon backend submissions so no new tokens are written under the OLD
#    key during the rotation window.
helm upgrade lolday charts/lolday --reuse-values \
  --set backend.acceptingJobs=false
#    (acceptingJobs is the existing BACKEND_MAINTENANCE_MODE feature flag —
#     POST /api/v1/jobs and POST /api/v1/detectors/{id}/builds return 503.)

# 2. Wait for in-flight builds and jobs to drain.
kubectl -n lolday-jobs get vcjob,job
# Expected: all reach Completed / Failed within ~20 min. If not, escalate.

# 3. Export the OLD key.
OLD=$(kubectl -n lolday get secret backend-fernet-key -o jsonpath='{.data.key}' | base64 -d)
echo "OLD key length: ${#OLD}"  # sanity: 44 chars (single key)

# 4. Generate the NEW key.
NEW=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
echo "NEW key length: ${#NEW}"  # sanity: 44 chars

# 5. Deploy NEW alongside OLD (NEW first → active for encrypt).
helm upgrade lolday charts/lolday --reuse-values \
  --set backend.fernetKeys="$NEW $OLD"
kubectl -n lolday rollout status deploy/backend  # wait for rollout

# 6. Re-encrypt every UserGitCredential row under NEW.
kubectl -n lolday exec deploy/backend -- \
  uv run --project /app python -m app.scripts.rotate_fernet \
    --old "$OLD" --new "$NEW"
# Expected log: "done — rotated=<N> skipped=0"
# On abort: the message names the user_id; inspect the row, decide whether to
# delete or wait, then re-run. The script is idempotent.

# 7. Uncordon — backend resumes accepting new submissions.
helm upgrade lolday charts/lolday --reuse-values \
  --set backend.acceptingJobs=true

# 8. Update .lolday-secrets.env on the operator workstation:
#    FERNET_KEYS="$NEW $OLD"
#    (so the next regular deploy reflects the same state).

# ============ T+24h: retire the OLD key. ============

# 9. Re-check: does any row still decrypt only under OLD? (Defensive — the
#    script in step 6 should have caught these, but new writes between
#    step 5 and step 7 went through MultiFernet([NEW, OLD]) and were
#    encrypted under NEW. The 24 h cushion is for any out-of-band re-import
#    that may have used the OLD-key path.)
kubectl -n lolday exec deploy/backend -- \
  uv run --project /app python -m app.scripts.rotate_fernet --old "$OLD" --new "$NEW"
# Expected: "rotated=0 skipped=<N>". If rotated > 0, do NOT proceed to step 10.

# 10. Drop the OLD key.
helm upgrade lolday charts/lolday --reuse-values \
  --set backend.fernetKeys="$NEW"
kubectl -n lolday rollout status deploy/backend

# 11. Update .lolday-secrets.env:
#     FERNET_KEYS="$NEW"
```

## Rollback

If step 6 (re-encryption) hits an unrotatable row and the operator can't
immediately repair it:

- The backend continues to run under `MultiFernet([NEW, OLD])` — both keys
  decrypt, so all reads succeed.
- New writes go under NEW (first key).
- Committed rows are in NEW state; uncommitted rows are still in OLD state.
- Re-run step 6 after fixing the bad row (or deleting it). The script is
  idempotent.

If step 9 (T+24h verification) shows `rotated > 0` (an out-of-band write
re-encrypted under OLD between step 7 and step 9 — should not happen,
defensive):

- Re-run step 6 to roll those rows forward.
- Re-run step 9 — if still `rotated == 0`, proceed to step 10.
- If you cannot reach `rotated == 0`, do NOT drop OLD: investigate the
  code path that's still writing under the wrong cipher.
