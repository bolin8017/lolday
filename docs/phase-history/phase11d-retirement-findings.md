# Phase 11d — E2E and v0 retirement post-mortem

**Date:** 2026-04-27
**Outcome:** ✅ Train → evaluate → predict E2E proven for both detectors against the real Phase-8.2 800-sample corpus. v0 artifacts retired from DB, Harbor, and GitHub. PyPI yank deferred to user.

## Summary

Phase 11c shipped a build pipeline that produced detector images, registered DetectorVersions, and routed events. Train jobs failed at submission though — diagnosed at the time as a single upstream `maldet._materialize` ValueError. Phase 11d started with that single fix in mind, but each layer of fix exposed a new one underneath. The actual E2E required **8 maldet patch releases**, **1 lolday backend patch**, and a sklearn-API shim in the cnn detector. Two of the eight maldet bugs (numbers 3 and 9 below) were genuine architectural mismatches between maldet 1.0.0's "framework only" design and the realities of running it under lolday's hardened pod security context.

## The bug chain (in encounter order)

| #   | Maldet ver | Issue                                                                                                                            | Fix                                                                                                 |
| --- | ---------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1   | 1.0.1      | sklearn/Lightning trainers' `_materialize` propagated `extractor.extract` ValueError                                             | Wrap in try/except, skip + warn, abort only if >50% skip                                            |
| 2   | 1.0.1      | scaffold templates' `Dockerfile.j2` did not `COPY README.md`                                                                     | Add to both rf and cnn templates                                                                    |
| 3   | 1.0.2      | CI `setup-uv@v3 version: 0.4.*` rejected newer lockfile (no `version` for editable dynamic packages)                             | Drop the version pin                                                                                |
| 4   | 1.0.3      | runner used `hydra_instantiate(cfg.model)`, requiring `_target_` — lolday's params guard rejects `_target_` (RCE prevention)     | Load model symbol from manifest (`stages.train.model`), treat `cfg.model` as kwargs                 |
| 5   | 1.0.4      | torch 2.7+ wheels (CUDA 12.8) crashed at `_cuda_init` under driver 560.35.03 (CUDA 12.6)                                         | Cap `torch>=2.2,<2.7` in lightning extra                                                            |
| 6   | 1.0.5      | `LightningTrainer.fit` defaulted `default_root_dir` to cwd `/app`, RO under `readOnlyRootFilesystem`                             | Fall back to `tempfile.gettempdir()` when not set                                                   |
| 7   | 1.0.6      | runner wrote `output_dir/{model,metrics.json,predictions.csv}` locally but never invoked `logger.log_artifact` to push to MLflow | Each stage uploads its primary artifact via the composite logger                                    |
| 8   | 1.0.7      | `BinaryClassification.evaluate` and `BatchPredictor.predict` had the same skip-on-`ValueError` gap as 1.0.1                      | Mirror the trainer fix into evaluator + predictor                                                   |
| 9   | 1.0.8      | `LightningTrainer.load` requires `model_factory`; runner called `trainer.load(source_model)` with no kwargs                      | Runner threads `model_factory` from manifest into `trainer.load(...)` when the signature accepts it |

Two more issues lived outside maldet:

| Layer     | Issue                                                                                                         | Fix                                                                           |
| --------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| lolday    | torch>=2.x calls `getpass.getuser()` at import; UID 1000 has no `/etc/passwd` entry under `runAsUser:1000`    | Backend `job_spec.py` adds `USER=maldet` env to the detector container        |
| elfcnndet | `BinaryClassification` calls `model.predict`/`predict_proba`; LightningModule doesn't expose those by default | `ByteCNN.predict` / `predict_proba` adapt forward + softmax (elfcnndet 2.1.0) |

Detector / image / dep things that bit:

- **`maldet[mlflow]` extra is not optional in production.** Without it, `MlflowEventLogger._available()` is False and every `log_metric`/`log_artifact` silently no-ops — metrics and model both fall on the floor with no error. Detector pyprojects must declare `maldet[mlflow]` (or `[lightning,mlflow]`).
- **PyPI/CDN propagation race.** A v2.0.2 build resolved maldet 1.0.2 instead of 1.0.3 because the build pod's pip hit PyPI before the new index was visible. Pinning `maldet>=1.0.<latest>` in the detector pyproject failed-fast on the next iteration.

## Why the original handoff diagnosis was wrong

The Phase 11c session diagnosis was "blocked by upstream maldet bug at `_materialize`". That bug **does** exist (item 1) and would have bitten the train run **after** the train pod successfully started. But the real Phase 11c train pod never got that far — it died early at `cfg.model` missing `_target_` (item 4). Phase 11c's events table only has `stage_begin` for the train job; that's the runner-emit before `hydra_instantiate(cfg.model)` blew up.

If the previous session had pulled the pod's full traceback rather than relying on `failure_reason: detector_exit_nonzero`, the misdirection would have been caught. Lesson: when a job exits non-zero, always pull the detector container's stdout, not just the platform's failure_reason classifier.

## What got retired

- **DB:** 2 v0 detector_version rows + 1 v0 model_version + 3 v0 jobs + 9 v0 detector_build rows. Snapshot at `phase11d-v0-snapshot.json` (in this same `docs/phase-history/` directory; 40 KB JSON).
- **Harbor:** 1 v0 artifact (`detectors/elfrfdet:v0.1.1`).
- **GitHub:** `bolin8017/islab-malware-detector` archived.
- **Volcano namespace:** ~170 Aborted/Failed historical jobs deleted; 21 Completed kept as audit trail.

## Deferred to user / next session

- **PyPI yank or deprecation** of `islab-malware-detector` 0.x. There is no clean CLI flow for this; do it from https://pypi.org/manage/project/islab-malware-detector/ when convenient.
- **Frontend openapi schema regeneration.** The frontend image is still on `phase10`; the schema bundled into the SPA references some types that no longer exist. This is cosmetic until a new frontend release picks up the regen.
- **Frontend live-metric verification.** `useJobEvents` WS hook + `JobMetricChart` rendering on `/jobs/:id` was not exercised in this session. Manual browser check recommended.
- **Maldet template improvements.** Bake `model.predict/predict_proba` into the cnn `models.py.j2` so future Lightning-based detectors don't trip the same wire as elfcnndet 2.0.x. Also reconsider whether `MlflowEventLogger` should hard-error (rather than no-op) when `MLFLOW_RUN_ID` env is set, so detectors missing the `[mlflow]` extra fail loudly at start instead of silently leaking metrics.
- **Driver upgrade unlocks loosening `torch<2.7`** (item 5).
- **Service-token cleanup.** Phase 11c E2E service token + CF Access policy + DB user + `~/.lolday-cf-svctoken.env` are still in place. Keep for future automation OR rotate when next migration cycle starts. (2026-04-29: the env vars were merged into `.lolday-secrets.env` — see CF_ACCESS_CLIENT_ID/SECRET there.)

## Final state

- Backend: `lolday-backend:phase11d` (helm release rev 69)
- Detectors live: elfrfdet v2.0.6, elfcnndet v2.1.0
- maldet on PyPI: 1.0.8
- Frontend: phase10 (unchanged)
