"""Pod-log capture helpers for failed/successful build and job pods.

Phase 13a A2 introduced :func:`_capture_pod_logs` as a generic helper that
tries containers in priority order (failure-reason hint → main container →
init containers) and returns whatever logs were retrievable, prefixed per
container with a ``[<container>]`` header. The build-pod wrapper
:func:`_capture_log_tail` and the job-pod wrapper :func:`_capture_job_log_tail`
are thin adapters that supply the right label selector and container names.
"""

from kubernetes.client import ApiException

from app.config import settings
from app.services.k8s import core_v1


def _container_from_failure_reason(failure_reason: str | None) -> str | None:
    """Extract container name from a failure_reason string like 'clone_failed: exit=1'."""
    if not failure_reason:
        return None
    head = failure_reason.split(":", 1)[0].strip()
    if head.endswith("_failed"):
        return head.removesuffix("_failed")
    return None


async def _capture_pod_logs(
    *,
    namespace: str,
    label_selector: str,
    main_container: str,
    init_containers: tuple[str, ...],
    failure_reason: str | None,
    tail_bytes: int,
    tail_lines: int = 200,
) -> str:
    """Capture log tail from the failing or main container of a labeled pod.

    Phase 13a A2: previous implementations hard-coded the container name
    (kaniko vs buildkit; detector only) and could not surface init-container
    output when the build/job failed before main started. This generic
    helper:
      1. Tries the container hinted by failure_reason first (e.g.
         'validate_failed' → 'validate').
      2. Falls back to main_container.
      3. Falls back to each init_container in order.
      4. Concatenates whatever logs were retrievable, prefixed with a
         '[<container>]' header line so the reader can tell what's what.
      5. Returns "" if no logs are retrievable from any container.

    The result is truncated to `tail_bytes` from the end so the persisted
    log_tail column doesn't blow up.
    """
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
    except ApiException:
        return ""
    if not pods.items:
        return ""
    pod = pods.items[0]

    # Build the container query order
    hinted = _container_from_failure_reason(failure_reason)
    order: list[str] = []
    if hinted and (hinted == main_container or hinted in init_containers):
        order.append(hinted)
    if main_container not in order:
        order.append(main_container)
    for ic in init_containers:
        if ic not in order:
            order.append(ic)

    # Try each container in order; collect what we can.
    chunks: list[str] = []
    for container in order:
        try:
            log = core_v1().read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
        except ApiException:
            continue
        if log:
            chunks.append(f"[{container}]\n{log}")

    if not chunks:
        return ""
    combined = "\n\n".join(chunks)
    return combined[-tail_bytes:]


async def _capture_log_tail(b) -> str:
    """Capture build pod's log tail.

    Phase 13a A2: was hard-coded to container='kaniko' (wrong — actual
    name is 'buildkit'). Now uses the generic helper with init-container
    fallback for when builds fail in clone/validate.
    """
    return await _capture_pod_logs(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
        main_container="buildkit",
        init_containers=("clone", "validate"),
        failure_reason=b.failure_reason,
        tail_bytes=settings.BUILD_LOG_TAIL_BYTES,
    )


async def _capture_job_log_tail(j) -> str:
    """Capture job pod's log tail.

    Phase 13a A2: previously read main 'detector' container only. Now
    also captures init-container logs (config-writer, model-fetcher) when
    the job fails before main starts.
    """
    return await _capture_pod_logs(
        namespace=settings.JOB_NAMESPACE,
        label_selector=f"lolday.job-id={j.id}",
        main_container="detector",
        init_containers=("config-writer", "model-fetcher"),
        failure_reason=j.failure_reason,
        tail_bytes=8192,
    )
