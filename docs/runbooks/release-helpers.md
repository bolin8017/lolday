# Release runbook: helper images

> Live runbook. Sister doc to `docs/runbooks/deploy.md`.
>
> SSH safety hard rule applies — see root `CLAUDE.md`.

This runbook covers the release flow for the two platform-side helper images:

- `harbor.lolday.svc:80/lolday/build-helper:<sha>` — build-pipeline init container.
- `harbor.lolday.svc:80/lolday/job-helper:<sha>` — job-pod init container, model-fetcher, and event-tailer sidecar.

`mlflow-server` and `pytorch-cu12-base` are out of scope; their tags carry external semantic meaning and stay manually pinned.

Spec: `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`.

## Pre-requisites

- Host docker (the operator's machine, typically server30) with network reach to `harbor.lolday.svc.cluster.local:80`.
- `kubectl` context pointing at the lolday cluster.
- `harbor-push-cred` Secret already in the `lolday` namespace. Create it via `bash scripts/recover-harbor.sh` if missing.
- A clean working tree on the feature branch — the build script refuses dirty subtrees.

## Standard flow

1. Edit the helper source — anything under `charts/lolday/helpers/<name>/` (Dockerfile, `pyproject.toml`, `uv.lock`, source files, tests). The 12-char subtree SHA captures every file that git tracks.
2. Run any per-helper unit tests:
   - `cd backend && uv run pytest charts/lolday/helpers/build-helper/test_maldet_validator.py`
   - `cd charts/lolday/helpers/job-helper && uv run pytest`
3. Commit the source change. The build script reads `HEAD:<path>` so the change must be committed before the SHA reflects it.
4. Run the build script:

   ```bash
   bash scripts/build-helpers.sh
   ```

   Output:
   - `[skip] <name>:<sha> already in Harbor` — Harbor already serves this SHA, no rebuild.
   - `[build] <name> -> <ref>` followed by docker build + push output.
   - `[lock] charts/lolday/helpers.lock updated` at the end.

5. Inspect the lock diff and commit:

   ```bash
   git diff charts/lolday/helpers.lock
   git commit charts/lolday/helpers.lock -m "chore(helpers): rebuild <name> at <sha>"
   ```

6. Deploy:

   ```bash
   bash scripts/deploy.sh
   ```

   The deploy script reads the lock, drift-guards it against HEAD, and injects the two image refs via Helm `--set`. A drift exits 1 with a diff message — re-run `bash scripts/build-helpers.sh` and commit the lock to fix.

## Variants

| Flag                                                | When to use                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bash scripts/build-helpers.sh --dry-run`           | Preview SHAs and image refs without contacting docker / Harbor / kubectl. Useful for sanity-checking the next tag in a PR description.                                                                                                                                                                                                                                |
| `bash scripts/build-helpers.sh --only build-helper` | Iterate on one helper without rebuilding the other. Updates only that key in the lock.                                                                                                                                                                                                                                                                                |
| `bash scripts/build-helpers.sh --allow-dirty`       | Dev-loop iteration on uncommitted changes. Stamps the tag with `-dirty-<unix-ts>`, builds and pushes, but leaves the lock untouched. **Never** use for a production rollout: an unreproducible tag is not what `helpers.lock` should pin. To deploy a `-dirty` image manually, pass it through `--set backend.env.BUILD_IMAGE_HELPER=...` to `helm upgrade` directly. |

## Rollback

`git revert` the lock commit and redeploy:

```bash
git revert <commit-sha-of-lock-bump>
bash scripts/deploy.sh
```

Older SHA tags persist in Harbor (Harbor does not auto-prune), so rollback is a redeploy of the previous lock — no rebuild needed. To wipe a tag from Harbor manually, use the Harbor UI or `scripts/harbor-inventory.sh`.

## Bootstrap (first-time install)

The lock is committed to git, but its tagged images do not exist in a fresh Harbor. Bootstrap order on a clean cluster:

1. `bash scripts/install-tools.sh`
2. `sudo bash scripts/setup-k3s.sh`
3. `bash scripts/deploy.sh` — first round, brings up Harbor and the platform; the backend pod will not yet have helper images, expect CrashLoopBackOff.
4. `bash scripts/recover-harbor.sh` — creates the Harbor `lolday` project, the `robot$build-pusher` account, and the `harbor-push-cred` Secret.
5. `bash scripts/build-helpers.sh` — pushes the helper images for the SHAs already pinned in the committed lock.
6. `bash scripts/deploy.sh` — second round; the backend pod now boots clean.

## CI integration sketch

Not implemented in this phase. The build script is pure-functional:

- Input: working tree git state.
- Output: Harbor pushes (idempotent against existing SHAs) + `helpers.lock` rewrite.

A future GitHub Actions / CI workflow can wrap it as:

```yaml
- run: bash scripts/build-helpers.sh
- run: |
    if ! git diff --exit-code charts/lolday/helpers.lock; then
      gh pr edit "$PR_NUMBER" --body "$(cat <<EOF
    Helper images rebuilt; lock updated. Reviewer: confirm and merge.
    EOF
    )"
      git config user.email "ci@lolday"
      git config user.name "lolday-ci"
      git add charts/lolday/helpers.lock
      git commit -m "chore(helpers): auto-rebuild helper image refs"
      git push
    fi
```

## Failure modes

| Symptom                                           | Cause                                                     | Fix                                                                                                                                               |
| ------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `harbor-push-cred not found`                      | First-time install or after `helm uninstall`              | `bash scripts/recover-harbor.sh`                                                                                                                  |
| `shallow clone detected`                          | Cloned with `--depth=N`                                   | `git fetch --unshallow`                                                                                                                           |
| `<helper> subtree dirty`                          | Uncommitted edit or untracked file under the helper       | `git status charts/lolday/helpers/<helper>` and either commit, stash, or pass `--allow-dirty`                                                     |
| `helpers.lock drift detected` from `deploy.sh`    | Helper subtree changed but lock not regenerated           | `bash scripts/build-helpers.sh` and commit the new lock                                                                                           |
| Pod stuck in `ImagePullBackOff` after deploy      | Harbor lost the tag (unusual — tag was deleted manually?) | `bash scripts/build-helpers.sh` to re-push; the same SHA tag is regenerated                                                                       |
| Pre-commit hook trips with `helpers.lock missing` | Fresh clone before the bootstrap rehearsal                | Run `bash scripts/build-helpers.sh` then commit the lock; or set `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` for the single commit if the build cannot run |
