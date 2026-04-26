"""Guard for user-supplied Hydra overrides on job submission.

Phase 11b/11c removed the v0 per-detector pydantic JSON schema validation. To
keep the platform from accepting Hydra overrides that would (a) execute
arbitrary code via ``_target_`` instantiation or (b) clobber platform-controlled
sections of the rendered Hydra YAML (paths/data/mlflow are platform-injected),
this module rejects two classes of keys:

1. **Hydra meta-fields** anywhere in the params tree: ``_target_``,
   ``_partial_``, ``_args_``, ``_recursive_``. These are how Hydra knows to
   ``importlib.import_module`` a target — letting users override one means
   arbitrary remote code execution inside the detector container.
2. **Platform-controlled top-level keys**: ``paths``, ``data``, ``mlflow``.
   These are written by ``services/job_config.JobConfigRenderer`` and must
   not be overridable.

Allowlist rather than blocklist would be safer, but maldet's per-detector
config trees are open-ended (every detector author can name new sections
freely), and a strict allowlist would force every detector to declare its
configurable surface area. Phase 11c trades that for a tight blocklist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HYDRA_META_KEYS = frozenset({"_target_", "_partial_", "_args_", "_recursive_"})
PLATFORM_RESERVED_PREFIXES = frozenset({"paths", "data", "mlflow"})


class UserParamsRejected(ValueError):
    """Raised on a forbidden key in user-submitted Hydra params."""


def validate_user_params(params: Any) -> None:
    """Recursively validate ``params``; raise on any forbidden key.

    Accepts both dotted-flat (``"model.n_estimators": 1``) and nested
    (``"model": {"n_estimators": 1}``) shapes — both are how lolday users
    pass overrides today.
    """
    if not isinstance(params, Mapping):
        raise UserParamsRejected(
            f"user params must be a dict, got {type(params).__name__}"
        )
    for key, val in params.items():
        if not isinstance(key, str):
            raise UserParamsRejected(
                f"user param keys must be strings, got {type(key).__name__}: {key!r}"
            )
        _check_key_is_safe(key)
        if isinstance(val, Mapping):
            _walk(val, parents=(key,))


def _check_key_is_safe(key: str) -> None:
    parts = key.split(".")
    if parts[0] in PLATFORM_RESERVED_PREFIXES:
        raise UserParamsRejected(
            f"key {key!r} starts with platform-reserved prefix {parts[0]!r}; "
            f"reserved={sorted(PLATFORM_RESERVED_PREFIXES)}"
        )
    for part in parts:
        if part in HYDRA_META_KEYS:
            raise UserParamsRejected(
                f"key {key!r} contains forbidden Hydra meta-field {part!r}; "
                f"forbidden={sorted(HYDRA_META_KEYS)}"
            )


def _walk(node: Mapping[str, Any], *, parents: tuple[str, ...]) -> None:
    for key, val in node.items():
        if not isinstance(key, str):
            raise UserParamsRejected(
                f"nested key under {'.'.join(parents)!r} must be a string"
            )
        if key in HYDRA_META_KEYS:
            raise UserParamsRejected(
                f"key {'.'.join((*parents, key))!r} is forbidden Hydra meta-field {key!r}"
            )
        if isinstance(val, Mapping):
            _walk(val, parents=(*parents, key))
