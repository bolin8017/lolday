"""Phase 3 — GPU1 resource profile."""

from app.models.job import RESOURCE_PROFILE_GPU_COUNT, ResourceProfile


def test_gpu1_enum_value_present() -> None:
    assert ResourceProfile.GPU1.value == "gpu1"


def test_gpu1_gpu_count_is_one() -> None:
    assert ResourceProfile.GPU1.gpu_count == 1
    assert RESOURCE_PROFILE_GPU_COUNT[ResourceProfile.GPU1] == 1


def test_resource_profile_map_total_over_enum() -> None:
    """Module-level assert in models/job.py already enforces this — but a
    runtime test guards against future enum additions that forget the map."""
    assert set(RESOURCE_PROFILE_GPU_COUNT.keys()) == set(ResourceProfile)
