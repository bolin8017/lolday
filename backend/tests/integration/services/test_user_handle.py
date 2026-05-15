"""Unit tests for User.handle slug derivation."""

import pytest
from app.services.user_handle import (
    HANDLE_MAX_LEN,
    derive_handle_from_email,
    is_valid_handle,
    next_unique_handle,
)


class TestIsValidHandle:
    @pytest.mark.parametrize("h", ["bolin8017", "alice", "elf-rf", "user_42", "u-1"])
    def test_valid(self, h):
        assert is_valid_handle(h) is True

    @pytest.mark.parametrize(
        "h",
        [
            "",  # empty
            "1abc",  # starts with digit
            "-abc",  # starts with hyphen
            "abc-",  # ends with hyphen
            "ab--cd",  # consecutive hyphens
            "ABC",  # uppercase
            "user@x",  # invalid char
            "a" * 61,  # too long
        ],
    )
    def test_invalid(self, h):
        assert is_valid_handle(h) is False


class TestDeriveHandleFromEmail:
    def test_simple_prefix(self):
        assert derive_handle_from_email("alice@example.com") == "alice"

    def test_dot_in_prefix_replaced_with_hyphen(self):
        assert derive_handle_from_email("first.last@x.com") == "first-last"

    def test_underscore_preserved(self):
        assert derive_handle_from_email("first_last@x.com") == "first_last"

    def test_uppercase_lowered(self):
        assert derive_handle_from_email("Alice@X.com") == "alice"

    def test_starts_with_digit_prepends_u(self):
        assert derive_handle_from_email("123abc@x.com") == "u-123abc"

    def test_invalid_chars_replaced_with_hyphen(self):
        assert derive_handle_from_email("a+b!c@x.com") == "a-b-c"

    def test_collapses_double_hyphens(self):
        assert derive_handle_from_email("a..b@x.com") == "a-b"

    def test_strips_leading_trailing_hyphens(self):
        assert derive_handle_from_email("-foo-@x.com") == "foo"

    def test_truncated_to_max_len(self):
        long_email = "a" * 80 + "@x.com"
        result = derive_handle_from_email(long_email)
        assert len(result) <= HANDLE_MAX_LEN
        assert is_valid_handle(result)

    def test_empty_local_part_falls_back(self):
        # Synthetic CF-Access service-token edge case
        result = derive_handle_from_email("@cf-access.local")
        assert is_valid_handle(result)
        assert result.startswith("u-")


class TestNextUniqueHandle:
    def test_returns_base_when_unused(self):
        assert next_unique_handle("alice", existing=set()) == "alice"

    def test_appends_suffix_2_on_collision(self):
        assert next_unique_handle("alice", existing={"alice"}) == "alice-2"

    def test_increments_until_unique(self):
        existing = {"alice", "alice-2", "alice-3"}
        assert next_unique_handle("alice", existing=existing) == "alice-4"

    def test_truncates_base_to_make_room_for_suffix(self):
        long_base = "a" * HANDLE_MAX_LEN
        existing = {long_base}
        result = next_unique_handle(long_base, existing=existing)
        assert len(result) <= HANDLE_MAX_LEN
        assert result.endswith("-2")
        assert is_valid_handle(result)
