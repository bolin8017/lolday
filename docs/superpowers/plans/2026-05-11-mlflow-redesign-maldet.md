# MLflow Redesign — maldet Framework Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade maldet to 2.2.0 with kind-aware MLflow event routing, MLflow Models flavor save/load in trainers, dataset lineage via `mlflow.log_input`, and an EventLogger `close()` lifecycle method for buffered events. Publish to PyPI.

**Architecture:** `MlflowEventLogger.log_event` becomes a dispatch table keyed by `EventKind`; structured payloads (confusion_matrix, per_class) flow into `log_dict` artifacts; warnings/errors are buffered in-memory and flushed as `*.jsonl` artifacts on `close()`. Trainers' `save()` and `load()` route through `mlflow.sklearn.save_model/load_model` and `mlflow.pytorch.save_model/load_model` respectively, generating proper MLmodel YAML + signature + dependencies. `StageRunner._pinned_mlflow_run` invokes `logger.close()` before `mlflow.end_run()` to flush buffers, and emits `mlflow.log_input()` per stage for dataset lineage. The `EventLogger` Protocol gains `log_model(model, flavor, ...)` and `close()` methods so jsonl/stdout sinks can coexist with mlflow.

**Tech Stack:** Python 3.12 + mlflow≥2.20 + sklearn + pytorch-lightning (optional extras). uv for build/publish. pytest for tests. Repo: `/home/bolin8017/Documents/repositories/maldet`.

**Reference:** Spec — `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`.

---

## File Structure

### To modify

| Path                                       | Change                                                                                                                                                             |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/maldet/protocols.py`                  | `EventLogger` protocol gains `log_model` + `close`                                                                                                                 |
| `src/maldet/events/logger.py`              | `CompositeEventLogger` fans out `log_model` + `close`                                                                                                              |
| `src/maldet/events/jsonl.py`               | `JsonlEventLogger.log_model` writes 1 line; `close()` no-op                                                                                                        |
| `src/maldet/events/stdout.py`              | `StdoutEventLogger.log_model` prints 1 line; `close()` no-op                                                                                                       |
| `src/maldet/events/mlflow_logger.py`       | **Rewrite** — kind-aware routing, warning/error buffers, log_model dispatch, close() flush                                                                         |
| `src/maldet/trainers/sklearn_trainer.py`   | `save()` adds `*, logger, signature_input_sample=None`; uses `mlflow.sklearn.save_model`. `load()` uses `mlflow.sklearn.load_model`.                               |
| `src/maldet/trainers/lightning_trainer.py` | Same pattern with `mlflow.pytorch`                                                                                                                                 |
| `src/maldet/runner.py`                     | Train branch threads `signature_input_sample` into `trainer.save`; emits `mlflow.log_input()` per stage; `_pinned_mlflow_run` calls `logger.close()` in `finally`. |
| `src/maldet/_version.py`                   | `2.1.0` → `2.2.0`                                                                                                                                                  |
| `CHANGELOG.md`                             | Add `[2.2.0] — 2026-05-11` section                                                                                                                                 |

### To create

| Path                                                  | Purpose                                                                                            |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `tests/events/test_mlflow_logger_routing.py`          | Tests for kind-aware routing (confusion_matrix → log_dict, per_class, warning buffer, close flush) |
| `tests/events/test_mlflow_logger_log_model.py`        | Tests for `log_model` flavor dispatch                                                              |
| `tests/events/test_logger_close.py`                   | Tests that Composite / Jsonl / Stdout `close()` are safe no-ops                                    |
| `tests/trainers/test_sklearn_save_mlflow_flavor.py`   | Tests Sklearn save creates MLmodel YAML + roundtrips via mlflow.sklearn.load_model                 |
| `tests/trainers/test_lightning_save_mlflow_flavor.py` | Same for Lightning                                                                                 |
| `tests/integration/test_runner_logs_input.py`         | Tests runner emits log_input per stage with correct context                                        |

### Tooling

```bash
cd /home/bolin8017/Documents/repositories/maldet
uv sync --extra all --extra dev
uv run pytest                      # full unit tests
uv build                           # produces dist/maldet-2.2.0-{wheel,sdist}
UV_PUBLISH_TOKEN=$UV_PUBLISH_TOKEN uv publish   # to PyPI
```

---

## Task 1: Extend `EventLogger` protocol with `log_model` and `close`

**Files:**

- Modify: `src/maldet/protocols.py:34-39`

- [ ] **Step 1: Read the current protocol**

Run: `sed -n '33,45p' src/maldet/protocols.py`

Expected output: the `EventLogger` Protocol block.

- [ ] **Step 2: Replace the EventLogger Protocol with the extended version**

Edit `src/maldet/protocols.py` — replace the `EventLogger` block (currently lines 33-39) with:

```python
@runtime_checkable
class EventLogger(Protocol):
    def log_metric(self, name: str, value: float, step: int | None = None) -> None: ...
    def log_params(self, params: dict[str, Any]) -> None: ...
    def log_artifact(self, path: Path, artifact_path: str | None = None) -> None: ...
    def log_event(self, kind: str, **payload: Any) -> None: ...
    def set_tags(self, tags: dict[str, str]) -> None: ...
    def log_model(
        self,
        model: Any,
        flavor: str,
        artifact_path: str = "model",
        signature: Any = None,
        input_example: Any = None,
        pip_requirements: list[str] | None = None,
    ) -> None: ...
    def close(self) -> None: ...
```

- [ ] **Step 3: Run protocol tests**

Run: `cd /home/bolin8017/Documents/repositories/maldet && uv run pytest tests/ -k "not mlflow_logger and not integration" -x`

Expected: type-check passes; existing tests still pass because no impl claims to be a full EventLogger yet. May fail on `isinstance(obj, EventLogger)` checks if any.

If failures appear about missing `log_model`/`close` on Composite/Jsonl/Stdout, that's expected — fix in Tasks 2-4.

---

## Task 2: Implement `close` + `log_model` on `JsonlEventLogger`

**Files:**

- Modify: `src/maldet/events/jsonl.py`
- Test: `tests/events/test_logger_close.py` (new)

- [ ] **Step 1: Write failing test for `close()` no-op**

Create `tests/events/test_logger_close.py`:

```python
"""close() is a graceful no-op on jsonl / stdout sinks; only mlflow uses it."""

from __future__ import annotations

from pathlib import Path

from maldet.events.jsonl import JsonlEventLogger
from maldet.events.stdout import StdoutEventLogger


def test_jsonl_close_is_noop(tmp_path: Path) -> None:
    logger = JsonlEventLogger(tmp_path / "events.jsonl")
    logger.close()  # must not raise
    logger.close()  # idempotent


def test_stdout_close_is_noop(capsys: object) -> None:
    logger = StdoutEventLogger()
    logger.close()
    logger.close()


def test_jsonl_log_model_writes_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    logger = JsonlEventLogger(path)
    logger.log_model(model=object(), flavor="sklearn", artifact_path="model")
    text = path.read_text()
    assert '"kind": "model_logged"' in text
    assert '"flavor": "sklearn"' in text
    assert '"artifact_path": "model"' in text


def test_stdout_log_model_prints_line(capsys: object) -> None:
    logger = StdoutEventLogger()
    logger.log_model(model=object(), flavor="pytorch", artifact_path="model")
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "model_logged" in out
    assert "pytorch" in out
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/events/test_logger_close.py -x`

Expected: `AttributeError: 'JsonlEventLogger' has no attribute 'close'` or `log_model`.

- [ ] **Step 3: Implement `JsonlEventLogger.log_model` and `close`**

Edit `src/maldet/events/jsonl.py` — append after the `set_tags` method:

```python
    def log_model(
        self,
        model: object,
        flavor: str,
        artifact_path: str = "model",
        signature: object = None,
        input_example: object = None,
        pip_requirements: list[str] | None = None,
    ) -> None:
        self._write(
            {
                "kind": "model_logged",
                "flavor": flavor,
                "artifact_path": artifact_path,
                "model_class": type(model).__name__,
            }
        )

    def close(self) -> None:
        # Jsonl is append-only with per-write fsync; no buffer to flush.
        return None
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/events/test_logger_close.py -x`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/maldet
git add src/maldet/events/jsonl.py tests/events/test_logger_close.py
git commit -m "feat(events): add log_model + close to JsonlEventLogger"
```

---

## Task 3: Implement `close` + `log_model` on `StdoutEventLogger`

**Files:**

- Read: `src/maldet/events/stdout.py` first

- [ ] **Step 1: Read existing stdout logger**

Run: `cat src/maldet/events/stdout.py`

- [ ] **Step 2: Implement `log_model` and `close`**

Edit `src/maldet/events/stdout.py` — append after the `set_tags` method:

```python
    def log_model(
        self,
        model: object,
        flavor: str,
        artifact_path: str = "model",
        signature: object = None,
        input_example: object = None,
        pip_requirements: list[str] | None = None,
    ) -> None:
        print(
            f"[maldet] model_logged flavor={flavor} "
            f"artifact_path={artifact_path} class={type(model).__name__}"
        )

    def close(self) -> None:
        return None
```

- [ ] **Step 3: Run stdout slice of the close test**

Run: `uv run pytest tests/events/test_logger_close.py::test_stdout_close_is_noop tests/events/test_logger_close.py::test_stdout_log_model_prints_line -v`

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/events/stdout.py
git commit -m "feat(events): add log_model + close to StdoutEventLogger"
```

---

## Task 4: Extend `CompositeEventLogger` to fan out `log_model` and `close`

**Files:**

- Modify: `src/maldet/events/logger.py`

- [ ] **Step 1: Write failing test**

Append to `tests/events/test_logger_composite.py`:

```python
def test_composite_log_model_fans_out() -> None:
    from unittest.mock import MagicMock
    from maldet.events.logger import CompositeEventLogger

    a, b = MagicMock(), MagicMock()
    composite = CompositeEventLogger([a, b])
    composite.log_model(model=object(), flavor="sklearn", artifact_path="model")
    a.log_model.assert_called_once()
    b.log_model.assert_called_once()


def test_composite_close_fans_out_and_isolates_failure() -> None:
    from unittest.mock import MagicMock
    from maldet.events.logger import CompositeEventLogger

    a = MagicMock()
    a.close.side_effect = RuntimeError("boom")
    b = MagicMock()
    composite = CompositeEventLogger([a, b])
    composite.close()  # must not raise
    a.close.assert_called_once()
    b.close.assert_called_once()
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/events/test_logger_composite.py -k "fans_out or fans_out_and_isolates" -x`

Expected: `AttributeError: 'CompositeEventLogger' has no attribute 'log_model'`.

- [ ] **Step 3: Implement fanout for `log_model` and `close`**

Edit `src/maldet/events/logger.py` — append two methods after `set_tags`:

```python
    def log_model(
        self,
        model: object,
        flavor: str,
        artifact_path: str = "model",
        signature: object = None,
        input_example: object = None,
        pip_requirements: list[str] | None = None,
    ) -> None:
        self._fanout(
            "log_model", model, flavor, artifact_path,
            signature, input_example, pip_requirements,
        )

    def close(self) -> None:
        self._fanout("close")
```

Note: `_fanout` already swallows exceptions and logs them, so error isolation comes for free.

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/events/test_logger_composite.py -x`

Expected: all green (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/maldet/events/logger.py tests/events/test_logger_composite.py
git commit -m "feat(events): fanout log_model + close in CompositeEventLogger"
```

---

## Task 5: Write failing tests for kind-aware routing in `MlflowEventLogger`

**Files:**

- Create: `tests/events/test_mlflow_logger_routing.py`

- [ ] **Step 1: Write the new test file**

Create `tests/events/test_mlflow_logger_routing.py`:

```python
"""MlflowEventLogger routes EventKind payloads to the right MLflow API.

Spec §5.2 — confusion_matrix / per_class are structured artifacts not tags;
warnings/errors are buffered + flushed on close().
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from maldet.events.mlflow_logger import MlflowEventLogger


def test_confusion_matrix_writes_dict_artifact_not_stringified_tag() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event(
        "confusion_matrix",
        labels=["Benign", "Malware"],
        matrix=[[90, 0], [1, 77]],
    )
    mlflow.log_dict.assert_called_once_with(
        {"labels": ["Benign", "Malware"], "matrix": [[90, 0], [1, 77]]},
        "confusion_matrix.json",
    )
    # critically — the old stringified tag pattern must NOT happen
    mlflow.set_tag.assert_not_called()


def test_per_class_writes_dict_and_per_class_metrics() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    per_class = {
        "Benign": {"precision": 0.989, "recall": 1.0, "f1": 0.994, "support": 90},
        "Malware": {"precision": 1.0, "recall": 0.987, "f1": 0.994, "support": 78},
    }
    logger.log_event("per_class", per_class=per_class)
    mlflow.log_dict.assert_called_once_with(per_class, "per_class_metrics.json")
    calls = [c.args for c in mlflow.log_metric.call_args_list]
    assert ("per_class/Benign/precision", 0.989) in calls
    assert ("per_class/Malware/f1", 0.994) in calls
    assert ("per_class/Benign/support", 90.0) in calls


def test_data_loaded_emits_metric_not_tag() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("data_loaded", n_train=645)
    mlflow.log_metric.assert_called_with("maldet/n_train", 645.0)


def test_warning_is_buffered_not_set_as_tag() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("warning", message="bad sample 1", sample_sha256="aaa")
    logger.log_event("warning", message="bad sample 2", sample_sha256="bbb")
    # No tag overwrites
    mlflow.set_tag.assert_not_called()
    # Both stored in buffer
    assert len(logger._warning_buf) == 2
    assert logger._warning_buf[0]["sample_sha256"] == "aaa"
    assert logger._warning_buf[1]["sample_sha256"] == "bbb"


def test_close_flushes_warnings_to_log_text() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("warning", message="m1", sample_sha256="a")
    logger.log_event("warning", message="m2", sample_sha256="b")
    logger.close()
    # warnings.jsonl uploaded as JSONL string
    args = mlflow.log_text.call_args
    text, name = args.args
    assert name == "warnings.jsonl"
    lines = [json.loads(line) for line in text.splitlines() if line]
    assert len(lines) == 2
    # also a count metric
    mlflow.log_metric.assert_any_call("maldet/warnings_total", 2.0)


def test_close_with_no_warnings_does_not_call_log_text() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.close()
    mlflow.log_text.assert_not_called()


def test_stage_begin_writes_stage_tag_and_timestamp() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("stage_begin", stage="train")
    calls = [c.args for c in mlflow.set_tag.call_args_list]
    assert ("maldet.stage", "train") in calls
    # stage_begin_ts also recorded (value is a float string)
    keys_set = {c.args[0] for c in mlflow.set_tag.call_args_list}
    assert "maldet.stage_begin_ts" in keys_set


def test_stage_end_writes_status_tag() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("stage_end", stage="train", status="success")
    calls = [c.args for c in mlflow.set_tag.call_args_list]
    assert ("maldet.status", "success") in calls
    assert ("maldet.stage_end", "train") in calls


def test_artifact_written_logs_metric_for_size_and_tag_for_path() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("artifact_written", path="/mnt/output/predictions.csv", size_bytes=14541)
    # tag carries the path under the basename key
    mlflow.set_tag.assert_any_call("maldet.artifact.predictions.csv", "/mnt/output/predictions.csv")
    # size goes to a metric
    mlflow.log_metric.assert_any_call("maldet/artifact_bytes/predictions.csv", 14541.0)


def test_unknown_kind_falls_back_to_scoped_tags() -> None:
    """Forward compat: unknown event kind shouldn't crash; payload still gets recorded as tags."""
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("my_future_event", foo="bar", n=42)
    mlflow.set_tag.assert_any_call("maldet.my_future_event.foo", "bar")
    mlflow.set_tag.assert_any_call("maldet.my_future_event.n", "42")
```

- [ ] **Step 2: Run the test — expect failures**

Run: `uv run pytest tests/events/test_mlflow_logger_routing.py -x`

Expected: many failures — current logger stringifies everything as `maldet.{kind}.{k}` tags.

---

## Task 6: Rewrite `MlflowEventLogger` with kind-aware routing

**Files:**

- Modify: `src/maldet/events/mlflow_logger.py` (full rewrite — file < 100 lines)

- [ ] **Step 1: Replace `src/maldet/events/mlflow_logger.py` entirely**

```python
"""MLflow-backed event logger with kind-aware routing.

Spec § 5.2 — structured payloads (confusion_matrix, per_class) become
``log_dict`` artifacts; line-stream events (warning, error) are buffered
in-memory and flushed as ``*.jsonl`` artifacts on ``close()``; scalar fields
become metrics or tags depending on shape. MLflow is a soft dependency —
install ``maldet[mlflow]`` to enable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _try_import_mlflow() -> Any:
    try:
        import mlflow

        return mlflow
    except ImportError:
        return None


class MlflowEventLogger:
    def __init__(self, mlflow: Any = None) -> None:
        self._mlflow = mlflow if mlflow is not None else _try_import_mlflow()
        self._warning_buf: list[dict[str, Any]] = []
        self._error_buf: list[dict[str, Any]] = []

    def _available(self) -> bool:
        return self._mlflow is not None

    # ---------- scalar / param / artifact passthrough ----------

    def log_metric(self, name: str, value: float, step: int | None = None) -> None:
        if not self._available():
            return
        self._mlflow.log_metric(name, value, step=step)

    def log_params(self, params: dict[str, Any]) -> None:
        if not self._available():
            return
        self._mlflow.log_params(dict(params))

    def log_artifact(self, path: Path, artifact_path: str | None = None) -> None:
        if not self._available():
            return
        if path.is_dir():
            self._mlflow.log_artifacts(str(path), artifact_path=artifact_path)
        else:
            self._mlflow.log_artifact(str(path), artifact_path=artifact_path)

    def set_tags(self, tags: dict[str, str]) -> None:
        if not self._available():
            return
        self._mlflow.set_tags(dict(tags))

    # ---------- kind-aware event routing ----------

    def log_event(self, kind: str, **payload: Any) -> None:
        if not self._available() or kind == "metric":
            return
        handler = _EVENT_HANDLERS.get(kind, _handle_generic_tag)
        handler(self._mlflow, kind, payload, self)

    # ---------- model logging ----------

    def log_model(
        self,
        model: Any,
        flavor: str,
        artifact_path: str = "model",
        signature: Any = None,
        input_example: Any = None,
        pip_requirements: list[str] | None = None,
    ) -> None:
        if not self._available():
            return
        if flavor == "sklearn":
            self._mlflow.sklearn.log_model(
                model,
                artifact_path=artifact_path,
                signature=signature,
                input_example=input_example,
                pip_requirements=pip_requirements,
            )
        elif flavor == "pytorch":
            self._mlflow.pytorch.log_model(
                model,
                artifact_path=artifact_path,
                signature=signature,
                input_example=input_example,
                pip_requirements=pip_requirements,
            )
        elif flavor == "pyfunc":
            self._mlflow.pyfunc.log_model(
                python_model=model,
                artifact_path=artifact_path,
                signature=signature,
                input_example=input_example,
                pip_requirements=pip_requirements,
            )
        else:
            raise ValueError(f"unknown mlflow flavor: {flavor!r}")

    # ---------- lifecycle ----------

    def close(self) -> None:
        """Flush buffered line-stream events to MLflow as JSONL artifacts."""
        if not self._available():
            return
        if self._warning_buf:
            self._mlflow.log_text(
                "\n".join(json.dumps(w, default=str) for w in self._warning_buf),
                "warnings.jsonl",
            )
            self._mlflow.log_metric("maldet/warnings_total", float(len(self._warning_buf)))
        if self._error_buf:
            self._mlflow.log_text(
                "\n".join(json.dumps(e, default=str) for e in self._error_buf),
                "errors.jsonl",
            )
            self._mlflow.log_metric("maldet/errors_total", float(len(self._error_buf)))


# ---------- event handlers (module-level for testability) ----------


def _handle_stage_begin(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    if "stage" in payload:
        mlflow.set_tag("maldet.stage", str(payload["stage"]))
    mlflow.set_tag("maldet.stage_begin_ts", str(time.time()))


def _handle_stage_end(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    if "stage" in payload:
        mlflow.set_tag("maldet.stage_end", str(payload["stage"]))
    if "status" in payload:
        mlflow.set_tag("maldet.status", str(payload["status"]))


def _handle_data_loaded(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    for k, v in payload.items():
        try:
            mlflow.log_metric(f"maldet/{k}", float(v))
        except (TypeError, ValueError):
            mlflow.set_tag(f"maldet.data.{k}", str(v))


def _handle_warning(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    logger._warning_buf.append({"ts": time.time(), **payload})


def _handle_error(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    logger._error_buf.append({"ts": time.time(), **payload})


def _handle_confusion_matrix(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    mlflow.log_dict(
        {"labels": payload["labels"], "matrix": payload["matrix"]},
        "confusion_matrix.json",
    )


def _handle_per_class(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    per_class = payload["per_class"]
    mlflow.log_dict(per_class, "per_class_metrics.json")
    for cls, metrics in per_class.items():
        if not isinstance(metrics, dict):
            continue
        for name, v in metrics.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(f"per_class/{cls}/{name}", float(v))


def _handle_artifact_written(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    path = payload.get("path", "")
    name = Path(path).name if path else "unknown"
    if path:
        mlflow.set_tag(f"maldet.artifact.{name}", str(path))
    if "size_bytes" in payload:
        try:
            mlflow.log_metric(f"maldet/artifact_bytes/{name}", float(payload["size_bytes"]))
        except (TypeError, ValueError):
            pass


def _handle_checkpoint_saved(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    _handle_artifact_written(mlflow, kind, payload, logger)


def _handle_generic_tag(mlflow: Any, kind: str, payload: dict[str, Any], logger: MlflowEventLogger) -> None:
    """Fallback for forward compat — scalar fields become scoped tags."""
    for k, v in payload.items():
        if isinstance(v, (str, int, float, bool)):
            mlflow.set_tag(f"maldet.{kind}.{k}", str(v))


_EVENT_HANDLERS: dict[str, Callable[[Any, str, dict[str, Any], MlflowEventLogger], None]] = {
    "stage_begin": _handle_stage_begin,
    "stage_end": _handle_stage_end,
    "data_loaded": _handle_data_loaded,
    "warning": _handle_warning,
    "error": _handle_error,
    "confusion_matrix": _handle_confusion_matrix,
    "per_class": _handle_per_class,
    "artifact_written": _handle_artifact_written,
    "checkpoint_saved": _handle_checkpoint_saved,
    "epoch_begin": _handle_generic_tag,
    "epoch_end": _handle_generic_tag,
}
```

- [ ] **Step 2: Run the routing tests**

Run: `uv run pytest tests/events/test_mlflow_logger_routing.py -v`

Expected: all 10 tests pass.

- [ ] **Step 3: Run the legacy mlflow_logger tests to confirm we didn't break the basic API**

Run: `uv run pytest tests/events/test_mlflow_logger.py -v`

Expected: most pass; **`test_log_event_is_a_mlflow_tag` will fail** because it expected old behavior (stage_begin → `maldet.stage_begin.stage` tag) which we changed to `maldet.stage`. Update the assertion in that test:

Edit `tests/events/test_mlflow_logger.py` — replace the body of `test_log_event_is_a_mlflow_tag` with:

```python
def test_log_event_is_a_mlflow_tag() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    logger.log_event("stage_begin", stage="train", config_hash="abc")
    # After 2.2.0 kind-aware routing: stage_begin sets maldet.stage and maldet.stage_begin_ts
    mlflow.set_tag.assert_any_call("maldet.stage", "train")
```

Run again: `uv run pytest tests/events/test_mlflow_logger.py -v`. Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/events/mlflow_logger.py tests/events/test_mlflow_logger.py tests/events/test_mlflow_logger_routing.py
git commit -m "feat(events)!: kind-aware MLflow routing + warning buffer + log_model dispatch

BREAKING: log_event no longer stringifies all payloads as tags.
Structured payloads (confusion_matrix, per_class) become log_dict artifacts.
Warnings buffer + flush on close() — no more tag-overwrite data loss.

Refs: docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md §5.2"
```

---

## Task 7: Write failing tests for `log_model` flavor dispatch

**Files:**

- Create: `tests/events/test_mlflow_logger_log_model.py`

- [ ] **Step 1: Write the test**

Create `tests/events/test_mlflow_logger_log_model.py`:

```python
"""log_model dispatches to mlflow.<flavor>.log_model."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maldet.events.mlflow_logger import MlflowEventLogger


def test_log_model_sklearn_dispatches_to_mlflow_sklearn() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    model = object()
    logger.log_model(model=model, flavor="sklearn", artifact_path="model")
    mlflow.sklearn.log_model.assert_called_once_with(
        model,
        artifact_path="model",
        signature=None,
        input_example=None,
        pip_requirements=None,
    )


def test_log_model_pytorch_dispatches_to_mlflow_pytorch() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    model = object()
    logger.log_model(
        model=model,
        flavor="pytorch",
        artifact_path="model",
        signature="sig",
        input_example="ex",
        pip_requirements=["torch==2.5"],
    )
    mlflow.pytorch.log_model.assert_called_once_with(
        model,
        artifact_path="model",
        signature="sig",
        input_example="ex",
        pip_requirements=["torch==2.5"],
    )


def test_log_model_pyfunc_dispatches_with_python_model_kw() -> None:
    mlflow = MagicMock()
    logger = MlflowEventLogger(mlflow=mlflow)
    model = object()
    logger.log_model(model=model, flavor="pyfunc", artifact_path="model")
    mlflow.pyfunc.log_model.assert_called_once_with(
        python_model=model,
        artifact_path="model",
        signature=None,
        input_example=None,
        pip_requirements=None,
    )


def test_log_model_unknown_flavor_raises() -> None:
    logger = MlflowEventLogger(mlflow=MagicMock())
    with pytest.raises(ValueError, match="unknown mlflow flavor"):
        logger.log_model(model=object(), flavor="tensorflow", artifact_path="model")


def test_log_model_noops_when_mlflow_unavailable() -> None:
    logger = MlflowEventLogger(mlflow=None)
    logger._mlflow = None
    logger.log_model(model=object(), flavor="sklearn")  # must not raise
```

- [ ] **Step 2: Run — expect pass (already implemented in Task 6)**

Run: `uv run pytest tests/events/test_mlflow_logger_log_model.py -v`

Expected: 5 passed (the `log_model` method was already implemented in Task 6's rewrite).

- [ ] **Step 3: Commit**

```bash
git add tests/events/test_mlflow_logger_log_model.py
git commit -m "test(events): cover log_model flavor dispatch"
```

---

## Task 8: SklearnTrainer save/load uses MLflow Models flavor — failing test

**Files:**

- Create: `tests/trainers/test_sklearn_save_mlflow_flavor.py`

- [ ] **Step 1: Write test**

Create the file:

```python
"""SklearnTrainer.save writes MLflow Models layout; load roundtrips via mlflow.sklearn."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier

from maldet.trainers.sklearn_trainer import SklearnTrainer
from maldet.types import Sample


class _DummyReader:
    def __init__(self, n: int) -> None:
        self._n = n

    def __iter__(self) -> Iterator[Sample]:
        for i in range(self._n):
            yield Sample(
                sha256=f"{i:064x}",
                path=Path("/tmp") / f"{i}",
                label="Malware" if i % 2 else "Benign",
            )

    def __len__(self) -> int:
        return self._n


class _DummyExtractor:
    output_shape = (4,)
    dtype = "uint8"

    def extract(self, sample: Sample) -> np.ndarray:
        return np.array(
            [1, 1, 1, 1] if sample.label == "Malware" else [0, 0, 0, 0], dtype=np.uint8
        )


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_metric(self, name, value, step=None): self.events.append(("metric", {"name": name, "value": value}))
    def log_params(self, params): self.events.append(("params", dict(params)))
    def log_artifact(self, path, artifact_path=None): self.events.append(("artifact", {"path": str(path), "artifact_path": artifact_path}))
    def log_event(self, kind, **payload): self.events.append((kind, dict(payload)))
    def set_tags(self, tags): self.events.append(("tags", dict(tags)))
    def log_model(self, **kwargs): self.events.append(("model", kwargs))
    def close(self): pass


def _trained_trainer(tmp_path: Path) -> tuple[SklearnTrainer, Any, np.ndarray]:
    logger = _RecordingLogger()
    model = RandomForestClassifier(n_estimators=5, random_state=0)
    trainer = SklearnTrainer()
    result = trainer.fit(
        model,
        _DummyReader(20),
        _DummyExtractor(),
        classes=["Benign", "Malware"],
        logger=logger,
    )
    return trainer, result, np.stack([
        np.array([1, 1, 1, 1], dtype=np.uint8),
        np.array([0, 0, 0, 0], dtype=np.uint8),
    ])


def test_save_writes_mlflow_models_layout(tmp_path: Path) -> None:
    trainer, result, sample_X = _trained_trainer(tmp_path)
    logger = _RecordingLogger()
    out = tmp_path / "model"
    trainer.save(result, out, logger=logger, signature_input_sample=sample_X)
    # MLflow Models layout
    assert (out / "MLmodel").exists()
    assert (out / "python_env.yaml").exists() or (out / "conda.yaml").exists()
    # the actual model file (mlflow.sklearn picks .pkl by default)
    assert any(out.glob("model.pkl")) or any(out.glob("*.pkl"))


def test_save_logs_model_artifact_to_logger(tmp_path: Path) -> None:
    trainer, result, sample_X = _trained_trainer(tmp_path)
    logger = _RecordingLogger()
    out = tmp_path / "model"
    trainer.save(result, out, logger=logger, signature_input_sample=sample_X)
    artifact_events = [e for e in logger.events if e[0] == "artifact"]
    assert any(e[1]["artifact_path"] == "model" for e in artifact_events)


def test_load_via_mlflow_sklearn_roundtrips(tmp_path: Path) -> None:
    trainer, result, sample_X = _trained_trainer(tmp_path)
    logger = _RecordingLogger()
    out = tmp_path / "model"
    trainer.save(result, out, logger=logger, signature_input_sample=sample_X)

    loaded = trainer.load(out)
    pred_loaded = loaded.predict(sample_X)
    pred_original = result.model.predict(sample_X)
    np.testing.assert_array_equal(pred_loaded, pred_original)


def test_save_includes_signature_when_sample_provided(tmp_path: Path) -> None:
    trainer, result, sample_X = _trained_trainer(tmp_path)
    logger = _RecordingLogger()
    out = tmp_path / "model"
    trainer.save(result, out, logger=logger, signature_input_sample=sample_X)
    # MLmodel YAML should contain a signature block when input was provided
    mlmodel_text = (out / "MLmodel").read_text()
    assert "signature:" in mlmodel_text
```

- [ ] **Step 2: Run — expect failure on `save` signature mismatch**

Run: `uv run pytest tests/trainers/test_sklearn_save_mlflow_flavor.py -x`

Expected: `TypeError: SklearnTrainer.save() got an unexpected keyword argument 'logger'` or similar.

---

## Task 9: Rewrite `SklearnTrainer.save` / `load` using mlflow.sklearn

**Files:**

- Modify: `src/maldet/trainers/sklearn_trainer.py`

- [ ] **Step 1: Replace the `save` and `load` methods on `SklearnTrainer`**

In `src/maldet/trainers/sklearn_trainer.py`:

- Remove the `_MODEL_FILENAME = "model.joblib"` constant (no longer referenced)
- Remove `import joblib` (mlflow.sklearn handles serialization)
- Replace the existing `save` and `load` methods with:

```python
    def save(
        self,
        result: TrainResult,
        out_dir: Path,
        *,
        logger: EventLogger,
        signature_input_sample: np.ndarray | None = None,
    ) -> None:
        """Write MLflow Models layout to ``out_dir`` and log it to the active MLflow run.

        The MLflow Models layout (MLmodel YAML + python_env.yaml + signature)
        lets evaluate/predict containers load via ``mlflow.sklearn.load_model``
        and lets the MLflow Model Registry pick up the dependencies + schema
        without manual MLmodel authoring.
        """
        import mlflow.sklearn
        from mlflow.models import infer_signature

        # mlflow.sklearn.save_model creates out_dir; remove any pre-existing
        # contents so save_model's mkdir doesn't raise on partial state.
        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir)

        signature = None
        input_example = None
        if signature_input_sample is not None and len(signature_input_sample) > 0:
            sample_X = signature_input_sample[:5]  # noqa: N806
            sample_y = result.model.predict(sample_X)
            signature = infer_signature(sample_X, sample_y)
            input_example = sample_X

        mlflow.sklearn.save_model(
            sk_model=result.model,
            path=str(out_dir),
            signature=signature,
            input_example=input_example,
        )
        logger.log_artifact(out_dir, artifact_path="model")

    def load(self, model_dir: Path) -> Any:
        import mlflow.sklearn

        return mlflow.sklearn.load_model(str(model_dir))
```

- [ ] **Step 2: Update legacy `test_sklearn.py` to thread the new kwargs**

The existing `tests/trainers/test_sklearn.py` calls `trainer.save(result, out)` without `logger=`. Update each call site:

Run: `grep -n "trainer.save(result" tests/trainers/test_sklearn.py`

For each match, change the call to include `logger=logger, signature_input_sample=np.array([[1,1,1,1]], dtype=np.uint8)`. Use the existing `RecordingLogger`-style fixtures in the file.

Also: the `test_save_writes_joblib` test asserts `(out / "model.joblib").exists()` — this is no longer true. Rewrite that test:

```python
def test_save_writes_mlmodel(tmp_path: Path) -> None:
    logger = RecordingLogger()
    model = RandomForestClassifier(n_estimators=5, random_state=0)
    trainer = SklearnTrainer()
    result = trainer.fit(
        model,
        DummyReader(_train_items()),
        DummyExtractor(),
        classes=list(_DEFAULT_CLASSES),
        logger=logger,
    )
    out = tmp_path / "model"
    trainer.save(result, out, logger=logger)
    assert (out / "MLmodel").exists()
```

And `test_load_roundtrips` similarly — replace the `(out / "model.joblib").exists()` check (if any) and verify roundtrip works.

- [ ] **Step 3: Run sklearn trainer tests**

Run: `uv run pytest tests/trainers/test_sklearn.py tests/trainers/test_sklearn_save_mlflow_flavor.py -v`

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/trainers/sklearn_trainer.py tests/trainers/test_sklearn.py tests/trainers/test_sklearn_save_mlflow_flavor.py
git commit -m "feat(trainers)!: SklearnTrainer.save/load use MLflow Models flavor

BREAKING: SklearnTrainer.save now requires logger= kwarg; out_dir layout
changes from raw model.joblib to MLflow Models format (MLmodel + .pkl +
python_env.yaml). Load now uses mlflow.sklearn.load_model.

Refs: docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md §5.3 §6.3"
```

---

## Task 10: LightningTrainer save/load uses MLflow Models flavor — failing test

**Files:**

- Create: `tests/trainers/test_lightning_save_mlflow_flavor.py`

- [ ] **Step 1: Write test**

Create the file (skip if no Lightning GPU runtime; mark `@pytest.mark.gpu` for now):

```python
"""LightningTrainer.save writes MLflow Models layout; load roundtrips via mlflow.pytorch."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("lightning", reason="LightningTrainer requires the [lightning] extra")
pytest.importorskip("torch", reason="LightningTrainer requires the [lightning] extra")

import lightning.pytorch as pl
import torch
from torch import nn

from maldet.trainers.lightning_trainer import LightningTrainer
from maldet.types import Sample


class _MinimalCNN(pl.LightningModule):
    def __init__(self) -> None:
        super().__init__()
        self.embed = nn.Embedding(num_embeddings=256, embedding_dim=4)
        self.fc = nn.Linear(4, 2)
        self.loss = nn.CrossEntropyLoss()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(self.embed(x).mean(dim=1))

    def training_step(self, batch, batch_idx):  # type: ignore[no-untyped-def]
        x, y = batch
        l = self.loss(self.forward(x), y)
        self.log("train_loss", l)
        return l

    def configure_optimizers(self):  # type: ignore[no-untyped-def]
        return torch.optim.Adam(self.parameters(), lr=1e-3)


class _DummyReader:
    def __init__(self, n: int) -> None:
        self._n = n

    def __iter__(self) -> Iterator[Sample]:
        for i in range(self._n):
            yield Sample(
                sha256=f"{i:064x}",
                path=Path("/tmp") / f"{i}",
                label="Malware" if i % 2 else "Benign",
            )

    def __len__(self) -> int:
        return self._n


class _DummyExtractor:
    output_shape = (4,)
    dtype = "uint8"

    def extract(self, sample: Sample) -> np.ndarray:
        return np.array(
            [1, 1, 1, 1] if sample.label == "Malware" else [0, 0, 0, 0], dtype=np.uint8
        )


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_metric(self, name, value, step=None): pass
    def log_params(self, params): pass
    def log_artifact(self, path, artifact_path=None): self.events.append(("artifact", {"path": str(path), "artifact_path": artifact_path}))
    def log_event(self, kind, **payload): pass
    def set_tags(self, tags): pass
    def log_model(self, **kwargs): pass
    def close(self): pass


def test_lightning_save_writes_mlflow_models_layout(tmp_path: Path) -> None:
    logger = _RecordingLogger()
    model = _MinimalCNN()
    trainer = LightningTrainer(max_epochs=1, batch_size=4, default_root_dir=str(tmp_path / "lt"))
    result = trainer.fit(
        model,
        _DummyReader(8),
        _DummyExtractor(),
        classes=["Benign", "Malware"],
        logger=logger,
    )
    out = tmp_path / "model"
    sample_in = torch.zeros(2, 4, dtype=torch.long)
    trainer.save(result, out, logger=logger, signature_input_sample=sample_in)
    assert (out / "MLmodel").exists()


def test_lightning_load_via_mlflow_pytorch(tmp_path: Path) -> None:
    logger = _RecordingLogger()
    model = _MinimalCNN()
    trainer = LightningTrainer(max_epochs=1, batch_size=4, default_root_dir=str(tmp_path / "lt"))
    result = trainer.fit(
        model,
        _DummyReader(8),
        _DummyExtractor(),
        classes=["Benign", "Malware"],
        logger=logger,
    )
    out = tmp_path / "model"
    sample_in = torch.zeros(2, 4, dtype=torch.long)
    trainer.save(result, out, logger=logger, signature_input_sample=sample_in)

    loaded = trainer.load(out)
    pred = loaded(sample_in)
    assert pred.shape == (2, 2)
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/trainers/test_lightning_save_mlflow_flavor.py -x`

Expected: TypeError on save() signature mismatch.

---

## Task 11: Rewrite `LightningTrainer.save` / `load` using mlflow.pytorch

**Files:**

- Modify: `src/maldet/trainers/lightning_trainer.py`

- [ ] **Step 1: Replace `save` and `load` methods**

```python
    def save(
        self,
        result: TrainResult,
        out_dir: Path,
        *,
        logger: EventLogger,
        signature_input_sample: torch.Tensor | None = None,
    ) -> None:
        """Write MLflow Models layout via ``mlflow.pytorch.save_model``.

        If a best-checkpoint exists, load its state_dict back into the
        in-memory module so the saved model reflects the best epoch.
        """
        import mlflow.pytorch
        from mlflow.models import infer_signature

        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir)

        if result.best_checkpoint is not None and result.best_checkpoint.exists():
            state = torch.load(result.best_checkpoint, map_location="cpu")
            sd = state.get("state_dict", state) if isinstance(state, dict) else state
            result.model.load_state_dict(sd)

        signature = None
        input_example = None
        if signature_input_sample is not None and len(signature_input_sample) > 0:
            with torch.no_grad():
                sample_in = signature_input_sample[:5]
                result.model.eval()
                sample_out = result.model(sample_in)
            signature = infer_signature(sample_in.cpu().numpy(), sample_out.cpu().numpy())
            input_example = sample_in.cpu().numpy()

        mlflow.pytorch.save_model(
            pytorch_model=result.model,
            path=str(out_dir),
            signature=signature,
            input_example=input_example,
        )
        logger.log_artifact(out_dir, artifact_path="model")

    def load(
        self,
        model_dir: Path,
        *,
        model_factory: Callable[[], pl.LightningModule] | None = None,
    ) -> pl.LightningModule:
        """Load via ``mlflow.pytorch.load_model`` — factory no longer needed."""
        import mlflow.pytorch

        # model_factory kept for backward-compatibility with custom trainers
        # that subclass LightningTrainer and override load(); ignored here.
        del model_factory
        return mlflow.pytorch.load_model(str(model_dir))
```

- [ ] **Step 2: Update `runner.py` to not require factory at evaluate/predict load**

Edit `src/maldet/runner.py` — the function `_load_with_optional_factory` is now mostly redundant. Keep it (forward compat for custom trainers), but in the evaluate/predict branches the result of `trainer.load(...)` no longer needs `model_factory` for the built-in LightningTrainer.

No code change required if the function still inspects signature and gracefully degrades when `model_factory` isn't accepted — verify by reading `runner.py:58-75`.

- [ ] **Step 3: Update existing `tests/trainers/test_lightning.py` save calls**

Run: `grep -n "trainer.save" tests/trainers/test_lightning.py`

For each match, thread the new kwargs (similar to Task 9 step 2). If the existing test asserts `(out / "model.ckpt").exists()`, rewrite to assert `(out / "MLmodel").exists()` instead.

- [ ] **Step 4: Run all lightning trainer tests**

Run: `uv run pytest tests/trainers/test_lightning.py tests/trainers/test_lightning_save_mlflow_flavor.py -v`

Expected: green (Lightning installed via `[lightning]` extra; skip if not available).

- [ ] **Step 5: Commit**

```bash
git add src/maldet/trainers/lightning_trainer.py tests/trainers/test_lightning.py tests/trainers/test_lightning_save_mlflow_flavor.py
git commit -m "feat(trainers)!: LightningTrainer.save/load use MLflow Models flavor

BREAKING: LightningTrainer.save now requires logger= kwarg; out_dir layout
changes from model.ckpt to MLflow Models format. Load uses
mlflow.pytorch.load_model; model_factory kwarg no longer required.

Refs: docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md §5.3 §6.4"
```

---

## Task 12: Runner emits `mlflow.log_input` per stage — failing test

**Files:**

- Create: `tests/integration/test_runner_logs_input.py`

- [ ] **Step 1: Write the test**

Create the file:

```python
"""StageRunner emits mlflow.log_input() for dataset lineage in train/evaluate/predict."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# This is an integration test that depends on the train CSV / sample setup
# already used by the e2e tests; skip if the e2e fixtures aren't present.
pytest.importorskip("mlflow")


def test_train_branch_calls_log_input_with_training_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """We mock mlflow.log_input + mlflow.data.from_pandas to assert call site."""
    from maldet.runner import _log_dataset_input
    import pandas as pd

    train_csv = tmp_path / "train.csv"
    pd.DataFrame({"file_name": ["a", "b"], "label": ["Benign", "Malware"]}).to_csv(train_csv, index=False)

    cfg = type("Cfg", (), {})()  # type: ignore[no-untyped-def]
    cfg.get = lambda k: {"lolday": {"train_dataset_id": "abc-123"}}.get(k)  # type: ignore[assignment]

    with patch("mlflow.log_input") as mock_log_input, \
         patch("mlflow.data.from_pandas") as mock_from_pandas, \
         patch("mlflow.active_run", return_value=object()):
        _log_dataset_input(cfg, "train", train_csv)
        mock_from_pandas.assert_called_once()
        mock_log_input.assert_called_once()
        kwargs = mock_log_input.call_args.kwargs
        assert kwargs.get("context") == "training"


def test_log_input_noop_when_mlflow_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If mlflow is not importable, _log_dataset_input must not raise."""
    from maldet.runner import _log_dataset_input
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name.startswith("mlflow"):
            raise ImportError("mlflow not installed in this test env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # must not raise
    _log_dataset_input(None, "train", tmp_path / "nonexistent.csv")
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/integration/test_runner_logs_input.py -x`

Expected: `ImportError: cannot import name '_log_dataset_input' from 'maldet.runner'`.

---

## Task 13: Implement `_log_dataset_input` + thread into StageRunner branches

**Files:**

- Modify: `src/maldet/runner.py`

- [ ] **Step 1: Add `_log_dataset_input` helper**

Append to `src/maldet/runner.py` (after the `_load_with_optional_factory` function, before `StageRunner`):

```python
import hashlib


def _log_dataset_input(cfg: DictConfig | None, stage: str, csv_path: Path) -> None:
    """Emit ``mlflow.log_input`` for the dataset consumed by ``stage``.

    No-ops when mlflow isn't importable, there is no active run, or the CSV
    can't be loaded. ``cfg.lolday.{train,test,predict}_dataset_id`` may
    provide a stable platform-side ID; falls back to ``"unknown"`` otherwise.
    """
    try:
        import mlflow
        import mlflow.data
        import pandas as pd
    except ImportError:
        return
    if mlflow.active_run() is None:
        return
    if not csv_path.exists():
        return
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return

    lolday_meta = {}
    if cfg is not None and hasattr(cfg, "get"):
        raw = cfg.get("lolday")
        if raw is not None:
            try:
                lolday_meta = dict(raw) if not isinstance(raw, dict) else raw
            except Exception:
                lolday_meta = {}

    key_map = {
        "train": "train_dataset_id",
        "evaluate": "test_dataset_id",
        "predict": "predict_dataset_id",
    }
    ds_id = lolday_meta.get(key_map.get(stage, ""), "unknown")
    digest = hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]
    try:
        ds = mlflow.data.from_pandas(
            df=df,
            source=str(csv_path),
            name=f"{stage}_{ds_id}",
            digest=digest,
        )
        context_map = {"train": "training", "evaluate": "evaluation", "predict": "prediction"}
        mlflow.log_input(ds, context=context_map.get(stage, stage))
    except Exception:
        # log_input is best-effort lineage; don't bring down the stage if it fails
        return
```

- [ ] **Step 2: Call `_log_dataset_input` from each stage branch**

In `_run_stage`, find each `Path(str(cfg.data.{train,test,predict}_csv))` declaration:

- `train` branch — after `train_csv = Path(str(cfg.data.train_csv))`, add:

```python
            _log_dataset_input(cfg, "train", train_csv)
```

- `evaluate` branch — after `test_csv = Path(str(cfg.data.test_csv))`, add:

```python
            _log_dataset_input(cfg, "evaluate", test_csv)
```

- `predict` branch — after `predict_csv = Path(str(cfg.data.predict_csv))`, add:

```python
            _log_dataset_input(cfg, "predict", predict_csv)
```

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_runner_logs_input.py -v`

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/runner.py tests/integration/test_runner_logs_input.py
git commit -m "feat(runner): emit mlflow.log_input per stage for dataset lineage"
```

---

## Task 14: Runner thread `signature_input_sample` into `trainer.save` + call `logger.close()`

**Files:**

- Modify: `src/maldet/runner.py`

- [ ] **Step 1: Thread sample into trainer.save in the train branch**

In `_run_stage`, the train branch currently calls:

```python
trainer.save(result, output_dir / "model")
logger.log_artifact(output_dir / "model", artifact_path="model")
```

The trainer now does the upload itself via `logger.log_artifact` inside save(); the redundant `logger.log_artifact(...)` call after save was already there but is now harmless (idempotent re-upload of same dir).

To produce a signature, we need a sample of the post-extraction feature matrix. Refactor the train branch to materialize once explicitly and pass the sample:

Replace the existing train branch body with:

```python
        if stage == "train":
            train_csv = Path(str(cfg.data.train_csv))
            _log_dataset_input(cfg, "train", train_csv)
            samples_root = Path(str(cfg.paths.samples_root))
            reader = reader_cls(csv=train_csv, samples_root=samples_root)
            extractor = extractor_cls()

            model_factory = _load_symbol(_require(stage_spec.model, "model"))
            model = model_factory(**_model_kwargs(cfg))
            trainer_cls = _load_symbol(_require(stage_spec.trainer, "trainer"))
            trainer = trainer_cls()
            result = trainer.fit(
                model,
                reader,
                extractor,
                classes=self._manifest.output.classes,
                logger=logger,
            )
            # Materialize a tiny sample again for signature inference. Re-reading
            # the CSV head is acceptable — extraction is cheap on a handful of
            # samples, and the alternative (returning sample data from fit) leaks
            # implementation detail across the Trainer protocol.
            sig_sample = _first_sample_features(reader, extractor, n=5)
            trainer.save(
                result, output_dir / "model", logger=logger,
                signature_input_sample=sig_sample,
            )
            # save() already uploaded via logger.log_artifact; no redundant call needed.
            return
```

Add a helper near `_log_dataset_input`:

```python
def _first_sample_features(reader: Any, extractor: Any, *, n: int = 5) -> np.ndarray | None:
    """Pull the first ``n`` successfully-extracted feature vectors as a 2-D array."""
    import numpy as np

    out: list[np.ndarray] = []
    for sample in reader:
        try:
            out.append(extractor.extract(sample))
        except Exception:
            continue
        if len(out) >= n:
            break
    if not out:
        return None
    return np.stack(out)
```

Remove the trailing `logger.log_artifact(output_dir / "model", artifact_path="model")` from the train branch (it's now done inside `trainer.save`).

- [ ] **Step 2: Call `logger.close()` in `_pinned_mlflow_run` finally**

In `_pinned_mlflow_run`, replace the existing `finally:` block with:

```python
        finally:
            # Flush composite-logger buffers (warnings.jsonl, errors.jsonl).
            # The composite swallows individual delegate failures internally.
            # logger.close() is reached via the outer scope in _run_stage; here we
            # cannot reach it directly because _pinned_mlflow_run is a static
            # context manager. Move logger.close() out of here — see Task 14b.
            if mlflow.active_run() is not None:
                mlflow.end_run()
```

The above comment is correct: `_pinned_mlflow_run` is a static context manager that runs before the per-stage logger is built. The logger close call must happen in `_run_stage` instead.

In `_run_stage`, wrap the existing stage-dispatch in a try/finally:

```python
    def _run_stage(self, stage: str, stage_spec: Any, cfg: DictConfig) -> None:
        output_dir = Path(str(cfg.paths.output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "manifest.json").write_text(
            json.dumps(self._manifest.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )

        logger = CompositeEventLogger(
            [
                JsonlEventLogger(output_dir / "events.jsonl"),
                StdoutEventLogger(),
                MlflowEventLogger(),
            ]
        )
        try:
            self._dispatch_stage(stage, stage_spec, cfg, logger, output_dir)
        finally:
            try:
                logger.close()
            except Exception:
                pass
```

Then move the existing `if stage == "train": ...` / evaluate / predict branches into a new helper `_dispatch_stage(self, stage, stage_spec, cfg, logger, output_dir)`. Mechanical refactor.

- [ ] **Step 3: Run runner integration tests**

Run: `uv run pytest tests/integration/ -v`

Expected: green. Some sklearn / lightning e2e tests may need their `RecordingLogger` to grow `close()` and `log_model()` — add the methods (no-op) if assertion errors mention missing attrs.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/runner.py tests/integration/
git commit -m "feat(runner): thread signature sample + close logger to flush buffers"
```

---

## Task 15: Update existing `RecordingLogger`-style test doubles

**Files:**

- Modify: `tests/trainers/test_sklearn.py`, `tests/trainers/test_lightning.py`, `tests/evaluators/test_binary.py`, `tests/integration/test_e2e_sklearn.py`, `tests/integration/test_e2e_lightning.py`

- [ ] **Step 1: Find all places using a RecordingLogger pattern**

Run: `grep -rn "class RecordingLogger\|class.*Logger:" tests/ | grep -v __pycache__`

- [ ] **Step 2: Add `close()` and `log_model()` no-ops to each test logger class**

For each match, ensure the class has:

```python
    def log_model(self, **kwargs):
        self.events.append(("model", dict(kwargs)))

    def close(self):
        pass
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -x`

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: extend RecordingLogger stubs with log_model + close no-ops"
```

---

## Task 16: Update CHANGELOG and bump version to 2.2.0

**Files:**

- Modify: `src/maldet/_version.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version**

Edit `src/maldet/_version.py`:

```python
__version__ = "2.2.0"
```

- [ ] **Step 2: Prepend new CHANGELOG entry**

Edit `CHANGELOG.md` — insert below `## [Unreleased]` and above `## [2.1.0]`:

````markdown
## [2.2.0] — 2026-05-11

### BREAKING

- `EventLogger` protocol gains `log_model(model, flavor, ...)` and `close()` methods. Custom logger implementations must add both (no-ops are acceptable for non-MLflow sinks).
- `Trainer.save()` signature gains required `logger: EventLogger` kwarg and optional `signature_input_sample`. Custom Trainer subclasses must update.
- `SklearnTrainer.save()` no longer writes `model.joblib` — output is MLflow Models layout (`MLmodel` YAML + `python_env.yaml` + `model.pkl` etc). Consumers calling `joblib.load(out_dir / "model.joblib")` must switch to `mlflow.sklearn.load_model(out_dir)` or use `SklearnTrainer.load`.
- `LightningTrainer.save()` no longer writes `model.ckpt` — output is MLflow Models layout. `LightningTrainer.load()` no longer requires `model_factory`; the kwarg is accepted for compatibility but ignored.
- `MlflowEventLogger.log_event()` no longer stringifies all event payloads as `maldet.{kind}.{k}` tags. Structured payloads (`confusion_matrix`, `per_class`) are written via `mlflow.log_dict()` as JSON artifacts. `warning`/`error` payloads are buffered and flushed as `warnings.jsonl`/`errors.jsonl` artifacts on `close()` (no more tag-overwrite data loss). `stage_begin`/`stage_end` tag namespace flattens: `maldet.stage`, `maldet.stage_end`, `maldet.status`. Downstream consumers parsing the old stringified tags must switch to reading the artifacts.

### Added

- MLflow Models flavor integration in trainers — generated `MLmodel` + `python_env.yaml` + optional `signature` + `input_example` enables Model Registry signature surfacing, `mlflow models serve`, and `mlflow.evaluate()`.
- `mlflow.log_input(mlflow.data.from_pandas(...))` per stage in `StageRunner` for dataset lineage. Backend platforms can inject `cfg.lolday.{train,test,predict}_dataset_id` to thread a stable platform-side ID.
- `MlflowEventLogger.close()` lifecycle hook — runner calls in `finally` to flush buffered events.

### Migration

```python
# Trainer.save callers:
- trainer.save(result, out_dir)
+ trainer.save(result, out_dir, logger=logger)

# Sklearn detector evaluate/predict load:
- model = joblib.load(out_dir / "model.joblib")
+ model = trainer.load(out_dir)  # uses mlflow.sklearn.load_model

# Lightning detector evaluate/predict load:
- model = trainer.load(out_dir, model_factory=ByteCNN)  # required
+ model = trainer.load(out_dir)  # factory no longer needed
```
````

Lolday platform: when creating an MLflow run, inject `cfg.lolday.train_dataset_id` / `test_dataset_id` / `predict_dataset_id` via the rendered Hydra config so `StageRunner._log_dataset_input` can name datasets stably.

Detector authors: bump `compat.min_maldet` to `"2.2"` in `maldet.toml`. No detector-side code changes are required for the new MLflow rendering — it happens entirely inside maldet.

````

- [ ] **Step 3: Verify version & changelog**

Run: `cd /home/bolin8017/Documents/repositories/maldet && grep -n "2.2.0" src/maldet/_version.py CHANGELOG.md | head -5`

Expected output: at least one line each.

- [ ] **Step 4: Commit**

```bash
git add src/maldet/_version.py CHANGELOG.md
git commit -m "chore: bump maldet to 2.2.0 with migration notes"
````

---

## Task 17: Full test suite + lint + type check

- [ ] **Step 1: Run full pytest**

Run: `cd /home/bolin8017/Documents/repositories/maldet && uv run pytest -x`

Expected: all green. Fix any failure surfaced before proceeding.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

Expected: clean. Run `uv run ruff format src tests` if format needed.

- [ ] **Step 3: Run mypy**

Run: `uv run mypy`

Expected: clean. Fix any type errors introduced.

- [ ] **Step 4: Commit any fixups**

```bash
git status
# If changes:
git add -A && git commit -m "chore: ruff/mypy fixes for 2.2.0"
```

---

## Task 18: Build artifacts + publish to PyPI

- [ ] **Step 1: Clean previous build**

Run: `cd /home/bolin8017/Documents/repositories/maldet && rm -rf dist/ build/ *.egg-info`

- [ ] **Step 2: Build**

Run: `uv build`

Expected output: `dist/maldet-2.2.0-py3-none-any.whl` + `dist/maldet-2.2.0.tar.gz`.

- [ ] **Step 3: Sanity-check the wheel**

Run: `uv run pip install --target /tmp/maldet-smoke dist/maldet-2.2.0-py3-none-any.whl && /tmp/maldet-smoke/bin/python -c "import maldet; print(maldet.__version__)"`

Wait — the install target won't bring `bin/python`. Simpler smoke:

```bash
uv run --with dist/maldet-2.2.0-py3-none-any.whl python -c "import maldet; print(maldet.__version__)"
```

Expected: `2.2.0`.

- [ ] **Step 4: Publish to PyPI**

Run:

```bash
source ~/.zshrc 2>/dev/null  # ensure UV_PUBLISH_TOKEN is in env
UV_PUBLISH_TOKEN="${UV_PUBLISH_TOKEN}" uv publish
```

Expected: `Uploading maldet-2.2.0-py3-none-any.whl` followed by `Uploading maldet-2.2.0.tar.gz`. No HTTP 4xx error.

- [ ] **Step 5: Verify on PyPI**

Run:

```bash
curl -s https://pypi.org/pypi/maldet/json | python -c "import sys,json; d=json.load(sys.stdin); print(d['info']['version'])"
```

Expected: `2.2.0`.

> NOTE: PyPI uploads are **irreversible**. If post-upload bugs surface, fix them by publishing 2.2.1, not by trying to delete 2.2.0.

- [ ] **Step 6: Tag the release**

```bash
cd /home/bolin8017/Documents/repositories/maldet
git tag -a v2.2.0 -m "maldet 2.2.0 — MLflow data-model redesign"
git push origin main --tags
```

---

## Self-review

- **Spec coverage**: Tasks 1-6 cover §5.2 (kind-aware routing); Tasks 7 + log_model handler in Task 6 cover §5.3; Tasks 8-11 cover §5.3 (Models flavor); Tasks 12-13 cover §5.4 (dataset lineage); Task 14 covers `logger.close()` lifecycle. CHANGELOG (Task 16) lists every BREAKING change.
- **No placeholders**: every step has concrete code blocks and exact commands.
- **Type consistency**: `EventLogger.log_model` signature matches across Protocol (Task 1), Composite (Task 4), Jsonl (Task 2), Stdout (Task 3), Mlflow (Task 6); `Trainer.save` signature matches sklearn (Task 9) and lightning (Task 11).
- **Test-first**: every implementation task is preceded by a failing-test task (Tasks 2 → 3, 5 → 6, 7 → already-impl, 8 → 9, 10 → 11, 12 → 13).
