"""Guard for user-supplied Hydra overrides on job submission.

Phase 11b/11c removed the v0 per-detector pydantic JSON schema validation. To
keep the platform from accepting Hydra overrides that would (a) execute
arbitrary code via ``_target_`` instantiation or (b) clobber platform-controlled
sections of the rendered Hydra YAML (paths/data/mlflow are platform-injected),
this module rejects two classes of keys:

1. **Hydra meta-fields** anywhere in the params tree: ``_target_``,
   ``_partial_``, ``_args_``, ``_recursive_``, ``_convert_``. These are how
   Hydra knows to ``importlib.import_module`` a target — letting users override
   one means arbitrary remote code execution inside the detector container.
2. **Platform-controlled top-level keys**: ``paths``, ``data``, ``mlflow``.
   These are written by ``services/job_config.JobConfigRenderer`` and must
   not be overridable.

Allowlist rather than blocklist would be safer, but maldet's per-detector
config trees are open-ended (every detector author can name new sections
freely), and a strict allowlist would force every detector to declare its
configurable surface area. Phase 11c trades that for a tight blocklist.

Lists are walked element-wise: ``{"callbacks": [{"_target_": ...}]}`` is rejected because
Hydra's ``instantiate()`` resolves ``_target_`` inside list elements as well.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

HYDRA_META_KEYS = frozenset(
    {"_target_", "_partial_", "_args_", "_recursive_", "_convert_"}
)
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
        _check_key_is_safe(key, is_top_level=True)
        _walk_value(val, parents=(key,))


def _check_key_is_safe(key: str, *, is_top_level: bool) -> None:
    """Validate one key. Splits on ``.`` and checks every segment.

    Hydra resolves dotted keys like ``model._target_`` as a full override path,
    so a Hydra meta-field in ANY segment of ANY key (top-level or nested) is
    a bypass and must be rejected.

    The platform-prefix check (``paths``/``data``/``mlflow``) is only meaningful
    at the top level: ``foo.paths`` is just a user-defined leaf, but
    ``paths.output_dir`` would clobber a platform-injected section.
    """
    parts = key.split(".")
    if is_top_level and parts[0] in PLATFORM_RESERVED_PREFIXES:
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
        _check_key_is_safe(key, is_top_level=False)
        _walk_value(val, parents=(*parents, key))


def _walk_value(val: Any, *, parents: tuple[str, ...]) -> None:
    """Recurse into Mapping (check keys) or non-string Sequence (check elements).

    Hydra's instantiate() resolves ``_target_`` in any dict node, including dicts
    nested inside lists (e.g. a callback list). Failing to walk lists would let
    ``{"callbacks": [{"_target_": "evil"}]}`` bypass the guard.
    """
    if isinstance(val, Mapping):
        _walk(val, parents=parents)
    elif isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        for i, item in enumerate(val):
            _walk_value(item, parents=(*parents, f"[{i}]"))
