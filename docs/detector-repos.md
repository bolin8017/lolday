# Detector repositories

The Lolday platform builds and runs detector images out of independent
GitHub repositories. Each detector repo is a Python package that depends
on the [`maldet`](https://github.com/bolin8017/maldet) framework and
declares its detector contract via a top-level `maldet.toml`.

This file lists the in-scope detector repos for cutover, dependency
bumps, and image rebuild loops (e.g. Plan §4.8 `for repo in …`). Update
it whenever a new detector repo is onboarded or retired.

## Active detectors

| Repo (local path)                    | GitHub                                 | Framework                  | Detector ver. | maldet pin   |
| ------------------------------------ | -------------------------------------- | -------------------------- | ------------- | ------------ |
| `~/Documents/repositories/elfrfdet`  | https://github.com/bolin8017/elfrfdet  | sklearn (Random Forest)    | `4.0.0`       | `>=2.0,<3.0` |
| `~/Documents/repositories/elfcnndet` | https://github.com/bolin8017/elfcnndet | PyTorch Lightning (1D-CNN) | `4.0.0`       | `>=2.0,<3.0` |

Both repos:

- Have a top-level `maldet.toml` declaring `[detector] name`, `[input]`, `[output]`, `[resources]`, `[lifecycle]`, `[artifacts]`, and `[compat]`.
- Build into a Harbor image at `harbor.lolday.svc:80/lolday/<detector-name>:<git-tag>` via `POST /api/v1/detectors/<id>/builds` against the lolday backend.
- Are tracked here as the canonical scope for "for each detector repo, …" loops.

## Not detectors (do not loop over)

| Repo                                              | What it actually is                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------------------ |
| `~/Documents/repositories/maldet`                 | The `maldet` library itself, published to PyPI as `maldet`. No `maldet.toml`.  |
| `~/Documents/repositories/islab-malware-detector` | Pre-`maldet` ISLab legacy. No `maldet.toml`, not integrated with the platform. |

If you see these in a loop labelled "for each detector", remove them.

## When updating this file

- New detector onboarded: add a row to the active table and link the GitHub repo.
- Detector retired: move the row to a "Retired" section (do not delete history).
- Version / pin bump: update the row in lock-step with the bump PR.
