"""Phase 13a A2: log capture from build / job pods with init-container fallback."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from app.reconciler import _capture_pod_logs
from kubernetes.client import ApiException


@pytest.fixture
def mock_k8s_pod():
    """Returns a mock pod whose .metadata.name is fixed."""
    pod = MagicMock()
    pod.metadata.name = "test-pod-abc"
    return pod


def _make_v1(pod, log_responses):
    """Build a mocked core_v1() that returns `pod` for list and dispatches
    log reads from `log_responses` (dict container_name -> str | ApiException)."""
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[pod])

    def read_log(name, namespace, container, tail_lines):
        result = log_responses.get(container)
        if isinstance(result, ApiException):
            raise result
        return result or ""

    v1.read_namespaced_pod_log.side_effect = read_log
    return v1


@pytest.mark.asyncio
async def test_capture_pod_logs_main_container_success(mock_k8s_pod):
    """Happy path: main container has logs, returned as-is."""
    v1 = _make_v1(mock_k8s_pod, {"buildkit": "BUILD OUTPUT\nfinal layer pushed"})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert "BUILD OUTPUT" in result
    assert "final layer pushed" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_falls_back_to_init_when_main_missing(mock_k8s_pod):
    """Build failed in init container; main never started → 404. Should walk
    back through init containers and return the first one with logs."""
    v1 = _make_v1(
        mock_k8s_pod,
        {
            "buildkit": ApiException(
                status=400, reason="container 'buildkit' not found"
            ),
            "validate": ApiException(status=400, reason="not found"),
            "clone": "fatal: could not read from remote repository",
        },
    )
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert "[clone]" in result  # header marks which container the log came from
    assert "could not read from remote" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_uses_failure_reason_hint(mock_k8s_pod):
    """When failure_reason names a container ('validate_failed: ...'),
    that container is queried first."""
    call_order = []
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[mock_k8s_pod])

    def read_log(name, namespace, container, tail_lines):
        call_order.append(container)
        if container == "validate":
            return "ValidationError: maldet.toml missing [project] section"
        raise ApiException(status=400, reason="not found")

    v1.read_namespaced_pod_log.side_effect = read_log

    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason="validate_failed: exit=2",
            tail_bytes=1024,
        )
    assert call_order[0] == "validate"
    assert "[validate]" in result
    assert "missing [project]" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_returns_empty_when_all_fail(mock_k8s_pod):
    """All container reads 404 → return empty string."""
    v1 = _make_v1(
        mock_k8s_pod,
        {
            c: ApiException(status=400, reason="not found")
            for c in ("buildkit", "clone", "validate")
        },
    )
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert result == ""


@pytest.mark.asyncio
async def test_capture_pod_logs_no_pod_returns_empty(mock_k8s_pod):
    """list_namespaced_pod returns no items → empty."""
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[])
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert result == ""


@pytest.mark.asyncio
async def test_capture_pod_logs_truncates_to_tail_bytes(mock_k8s_pod):
    """tail_bytes truncates the result so we don't blow log_tail column."""
    v1 = _make_v1(mock_k8s_pod, {"buildkit": "X" * 10_000})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=8192,
        )
    assert len(result) <= 8192


@pytest.mark.asyncio
async def test_capture_log_tail_uses_buildkit_container(mock_k8s_pod):
    """Regression: previous code looked for 'kaniko' which didn't exist,
    so log_tail was always empty for real builds."""
    from app.models.detector import DetectorBuild
    from app.reconciler import _capture_log_tail

    build = MagicMock(spec=DetectorBuild)
    build.id = uuid4()
    build.failure_reason = None

    v1 = _make_v1(mock_k8s_pod, {"buildkit": "buildctl-daemonless: pushed sha256:abc"})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_log_tail(build)
    assert "pushed sha256:abc" in result
    assert "[buildkit]" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_list_api_error_returns_empty():
    """list_namespaced_pod raising ApiException → return empty string."""
    v1 = MagicMock()
    v1.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert result == ""


def test_both_build_handlers_capture_log_tail():
    """Phase 13a A2 follow-up: regression guard. Earlier _handle_succeeded
    was missing a `_capture_log_tail(b)` call (only the failure path had
    it), so green builds shipped with log_tail=NULL and the UI showed
    '(no output)'. Both handlers must capture; this test makes sure
    nobody removes the call again.
    """
    import inspect

    from app.reconciler import _handle_failed, _handle_succeeded

    succ_src = inspect.getsource(_handle_succeeded)
    fail_src = inspect.getsource(_handle_failed)
    assert "_capture_log_tail(b)" in succ_src, (
        "_handle_succeeded must capture build log_tail (phase 13a A2 fix)"
    )
    assert "_capture_log_tail(b)" in fail_src, (
        "_handle_failed must capture build log_tail (pre-existing)"
    )
