"""Direct-call tests for ``app.services.k8s`` accessors that the rest of the
test suite bypasses.

Every production call site of ``core_v1`` / ``batch_v1`` / ``volcano_v1alpha1``
is monkey-patched to a stub in ``_stubs.CALLER_MODULE_REBIND_TARGETS`` (via the
``_mock_k8s_load_config`` autouse fixture in ``integration/conftest.py``), so
the real function bodies — including the in-cluster -> kubeconfig fallback
inside ``load_config`` — never execute under normal test runs and were stuck
at zero coverage. These tests bypass the rebinding and exercise the
contracts directly:

- ``load_config`` prefers ``load_incluster_config`` (the Pod path).
- It falls back to ``load_kube_config`` on ``ConfigException`` (local dev).
- ``core_v1`` / ``batch_v1`` / ``volcano_v1alpha1`` each wire through
  ``load_config`` and return a fresh client of the matching kind.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kubernetes import config as k8s_config


def _clear_load_config_cache() -> None:
    """The module-level ``@lru_cache(maxsize=1)`` on ``load_config`` means a
    single test that runs it would otherwise mask the fallback path for
    every subsequent invocation in the same process. Clear before each
    branch we exercise so the fallback isn't shadowed by the in-cluster
    happy path."""
    from app.services.k8s import load_config

    load_config.cache_clear()


def test_load_config_prefers_incluster() -> None:
    _clear_load_config_cache()
    from app.services import k8s

    with (
        patch.object(k8s.config, "load_incluster_config") as in_cluster,
        patch.object(k8s.config, "load_kube_config") as kubeconfig,
    ):
        k8s.load_config()

    in_cluster.assert_called_once()
    kubeconfig.assert_not_called()


def test_load_config_falls_back_to_kubeconfig_on_incluster_exception() -> None:
    _clear_load_config_cache()
    from app.services import k8s

    def _raise(*_a, **_kw):
        raise k8s_config.config_exception.ConfigException("not in a Pod")

    with (
        patch.object(k8s.config, "load_incluster_config", side_effect=_raise),
        patch.object(k8s.config, "load_kube_config") as kubeconfig,
    ):
        k8s.load_config()

    kubeconfig.assert_called_once()


def test_core_v1_returns_a_fresh_core_api_client() -> None:
    _clear_load_config_cache()
    from app.services import k8s

    fake_client = MagicMock(name="CoreV1Api")
    with (
        patch.object(k8s.config, "load_incluster_config"),
        patch.object(k8s.client, "CoreV1Api", return_value=fake_client) as core_cls,
    ):
        # ``app.services.k8s.core_v1`` is rebound to the StubCore singleton at
        # the conftest level via ``CALLER_MODULE_REBIND_TARGETS``. To exercise
        # the real function body we must call it through the module attribute
        # *before* any rebinding can take effect — but the rebinding targets
        # are the CALLER modules (job_dispatch, jobs, ...), not ``k8s``
        # itself, so calling ``k8s.core_v1()`` runs the real body.
        result = k8s.core_v1()

    assert result is fake_client
    core_cls.assert_called_once()


def test_batch_v1_returns_a_fresh_batch_api_client() -> None:
    _clear_load_config_cache()
    from app.services import k8s

    fake_client = MagicMock(name="BatchV1Api")
    with (
        patch.object(k8s.config, "load_incluster_config"),
        patch.object(k8s.client, "BatchV1Api", return_value=fake_client) as batch_cls,
    ):
        result = k8s.batch_v1()

    assert result is fake_client
    batch_cls.assert_called_once()


def test_volcano_v1alpha1_returns_a_fresh_custom_objects_api_client() -> None:
    _clear_load_config_cache()
    from app.services import k8s

    fake_client = MagicMock(name="CustomObjectsApi")
    with (
        patch.object(k8s.config, "load_incluster_config"),
        patch.object(
            k8s.client, "CustomObjectsApi", return_value=fake_client
        ) as custom_cls,
    ):
        result = k8s.volcano_v1alpha1()

    assert result is fake_client
    custom_cls.assert_called_once()
