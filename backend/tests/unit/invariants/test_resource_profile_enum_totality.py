"""Property-based invariant test: every ResourceProfile enum value must
have a corresponding entry in _RESOURCE_PROFILE_GPU_COUNT.

The existing import-time assert in app/models/job.py catches this
violation, but only when the module is first imported. This test catches
it at pytest collection time, giving fast CI feedback if someone adds a
new enum value without updating the mapping.
"""

from __future__ import annotations

from app.models.job import _RESOURCE_PROFILE_GPU_COUNT, ResourceProfile
from hypothesis import given
from hypothesis import strategies as st

all_profiles = st.sampled_from(list(ResourceProfile))


@given(profile=all_profiles)
def test_every_profile_has_gpu_count(profile: ResourceProfile):
    """Every ResourceProfile must map to a GPU count."""
    assert profile in _RESOURCE_PROFILE_GPU_COUNT, (
        f"{profile} missing from _RESOURCE_PROFILE_GPU_COUNT — update the mapping"
    )


def test_mapping_keys_equal_enum_set():
    """The mapping's key set equals the full ResourceProfile enum (no orphans
    on either side)."""
    enum_set = set(ResourceProfile)
    mapping_set = set(_RESOURCE_PROFILE_GPU_COUNT.keys())

    missing = enum_set - mapping_set
    extra = mapping_set - enum_set

    assert not missing, f"enum values missing from mapping: {missing}"
    assert not extra, f"mapping has stale keys (not in enum): {extra}"


def test_mapping_values_are_non_negative_ints():
    """GPU counts must be sensible non-negative integers (a 'no GPU' profile
    is 0; we don't allow negative counts)."""
    for profile, count in _RESOURCE_PROFILE_GPU_COUNT.items():
        assert isinstance(count, int), f"{profile}'s GPU count is not int: {count!r}"
        assert count >= 0, f"{profile}'s GPU count is negative: {count}"
