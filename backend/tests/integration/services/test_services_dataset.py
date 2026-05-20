import hashlib
from pathlib import Path

import pytest
from app.services.dataset import (
    DatasetIntegrityError,
    DatasetValidationError,
    compute_checksum,
    parse_csv,
    spot_check_samples,
)

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "sample_dataset.csv"


def test_parse_valid_csv_returns_stats():
    content = FIXTURE.read_text()
    result = parse_csv(content)

    assert result.sample_count == 7
    assert result.label_distribution == {"Malware": 5, "Benign": 2}
    assert result.family_distribution == {"xorddos": 3, "gafgyt": 1, "ngioweb": 1}
    assert result.size_bytes == len(content.encode("utf-8"))


def test_parse_csv_missing_file_name_column_raises():
    bad = "label,family\nMalware,xorddos\n"
    with pytest.raises(DatasetValidationError, match="file_name"):
        parse_csv(bad)


def test_parse_csv_missing_label_column_raises():
    bad = (
        "file_name\n0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11\n"
    )
    with pytest.raises(DatasetValidationError, match="label"):
        parse_csv(bad)


def test_parse_csv_rejects_non_sha256_filename():
    bad = "file_name,label\nnot-a-sha256,Malware\n"
    with pytest.raises(DatasetValidationError, match="file_name"):
        parse_csv(bad)


def test_parse_csv_rejects_uppercase_hex():
    bad = (
        "file_name,label\n"
        "0000002158D35C2BB5E7D96A39FF464EA4C83DE8C5FD72094736F79125AACA11,Malware\n"
    )
    with pytest.raises(DatasetValidationError, match="lowercase"):
        parse_csv(bad)


def test_parse_empty_csv_rejected():
    with pytest.raises(DatasetValidationError, match="empty"):
        parse_csv("file_name,label\n")


def test_parse_blank_input_rejected():
    """Whitespace-only or empty input hits the early `not content.strip()` guard.

    Distinct from `test_parse_empty_csv_rejected` (which carries a header
    but no data rows and hits the late `no data rows` guard).
    """
    with pytest.raises(DatasetValidationError, match="empty"):
        parse_csv("")
    with pytest.raises(DatasetValidationError, match="empty"):
        parse_csv("   \n\t  ")


def test_parse_csv_rejects_empty_file_name_value():
    """An explicitly-empty file_name in a data row must be rejected.

    Distinct from the "missing file_name column" case (header-level) —
    here the column exists but the row's value is blank.
    """
    bad = (
        "file_name,label\n"
        ",Malware\n"  # empty file_name, valid label
    )
    with pytest.raises(DatasetValidationError, match="empty file_name"):
        parse_csv(bad)


def test_parse_csv_rejects_invalid_label_value():
    """A row whose label is not in VALID_LABELS must be rejected.

    Distinct from "missing label column" (header-level) — column exists,
    value is just out-of-vocabulary.
    """
    bad = (
        "file_name,label\n"
        "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11,NotARealLabel\n"
    )
    with pytest.raises(DatasetValidationError, match="label must be one of"):
        parse_csv(bad)


def test_parse_csv_skips_malware_row_with_empty_family():
    """`family` column present + label=Malware + family empty → row counted,
    no family entry added.

    Covers the branch `101->77` in `parse_csv` (the `if family:` inner check
    that skips the family_counts increment when the field is whitespace-only).
    """
    content = (
        "file_name,label,family\n"
        "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11,Malware,\n"
        "00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35,Malware,xorddos\n"
    )
    result = parse_csv(content)
    assert result.sample_count == 2
    # Only the second row contributes a family
    assert result.family_distribution == {"xorddos": 1}


def test_parse_malformed_csv_rejected():
    bad = "file_name,label\nabc\n"  # too few columns
    with pytest.raises(DatasetValidationError):
        parse_csv(bad)


def test_compute_checksum_is_sha256_of_bytes():
    content = "hello"
    expected = hashlib.sha256(b"hello").hexdigest()
    assert compute_checksum(content) == expected


def test_spot_check_all_present(tmp_path):
    samples_root = tmp_path / "samples"
    (samples_root / "00").mkdir(parents=True)
    name = "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11"
    (samples_root / "00" / name).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[name],
        labels=["Malware"],
        samples_root=samples_root,
        sample_count=1,
        missing_threshold=1,
    )
    assert result.checked == 1
    assert result.missing == 0


def test_spot_check_all_missing(tmp_path):
    samples_root = tmp_path / "samples"
    samples_root.mkdir(parents=True)

    names = [
        "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11",
        "00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35",
    ]
    labels = ["Malware", "Malware"]
    with pytest.raises(DatasetIntegrityError, match="2 missing"):
        spot_check_samples(
            file_names=names,
            labels=labels,
            samples_root=samples_root,
            sample_count=2,
            missing_threshold=1,
        )


def test_spot_check_benign_path_is_flat(tmp_path):
    """Benign samples share the same flat layout as malware (no label subdir)."""
    samples_root = tmp_path / "samples"
    (samples_root / "de").mkdir(parents=True)
    benign = "deadbeef0000000000000000000000000000000000000000000000000000beef"
    (samples_root / "de" / benign).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[benign],
        labels=["Benign"],
        samples_root=samples_root,
        sample_count=1,
        missing_threshold=1,
    )
    assert result.missing == 0


def test_spot_check_sample_count_exceeds_dataset(tmp_path):
    """When sample_count > len(file_names), check all of them."""
    samples_root = tmp_path / "samples"
    (samples_root / "aa").mkdir(parents=True)
    name = "aa" + "0" * 62
    (samples_root / "aa" / name).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[name],
        labels=["Malware"],
        samples_root=samples_root,
        sample_count=9999,
        missing_threshold=1,
    )
    assert result.checked == 1
    assert result.missing == 0


def test_spot_check_rejects_unknown_label(tmp_path):
    samples_root = tmp_path / "samples"
    names = ["0" * 64]
    labels = ["Weird"]
    with pytest.raises(DatasetValidationError, match="label"):
        spot_check_samples(
            file_names=names,
            labels=labels,
            samples_root=samples_root,
            sample_count=1,
            missing_threshold=1,
        )


def test_spot_check_rejects_length_mismatch(tmp_path):
    """`file_names` and `labels` arrive parallel from parse_csv; any mismatch
    here means a caller-side bug, surfaced as DatasetValidationError before
    the random spot-check loop runs.
    """
    samples_root = tmp_path / "samples"
    with pytest.raises(DatasetValidationError, match="length mismatch"):
        spot_check_samples(
            file_names=["0" * 64, "1" * 64],
            labels=["Malware"],  # 1 != 2
            samples_root=samples_root,
            sample_count=1,
            missing_threshold=1,
        )
