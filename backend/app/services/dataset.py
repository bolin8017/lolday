"""Dataset CSV parsing + integrity validation.

- parse_csv: validates format, computes label/family/sample distributions + checksum
- compute_checksum: SHA256 of raw CSV bytes (UTF-8)
- spot_check_samples: randomly picks N file_names and verifies they exist on disk

Design notes:
- File name convention: 64-char lowercase hex (SHA256)
- Samples live under <samples_root>/<first_2_chars>/<file_name> (flat, per upxelfdet convention)
- `label` column values: "Malware" (case-sensitive, matches CSV fixture) or "Benign"
- Full existence scan is O(N); spot-check is cheap and catches catastrophic mount failures
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import re
from dataclasses import dataclass
from pathlib import Path


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VALID_LABELS = {"Malware", "Benign"}


class DatasetValidationError(ValueError):
    """Raised on malformed CSV / bad values."""


class DatasetIntegrityError(RuntimeError):
    """Raised when spot-check finds >= threshold missing samples."""


@dataclass(frozen=True)
class ParsedCsv:
    sample_count: int
    label_distribution: dict[str, int]
    family_distribution: dict[str, int] | None
    size_bytes: int
    checksum: str
    file_names: list[str]
    labels: list[str]


@dataclass(frozen=True)
class SpotCheckResult:
    checked: int
    missing: int


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_csv(content: str) -> ParsedCsv:
    if not content or not content.strip():
        raise DatasetValidationError("CSV is empty")

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise DatasetValidationError("CSV has no header")

    required = {"file_name", "label"}
    missing_cols = required - set(reader.fieldnames)
    if missing_cols:
        raise DatasetValidationError(f"CSV missing columns: {sorted(missing_cols)}")

    has_family_col = "family" in reader.fieldnames

    file_names: list[str] = []
    labels: list[str] = []
    label_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    for row_num, row in enumerate(reader, start=2):
        name = (row.get("file_name") or "").strip()
        label = (row.get("label") or "").strip()
        if not name:
            raise DatasetValidationError(f"row {row_num}: empty file_name")
        if not SHA256_PATTERN.match(name):
            if name.lower() == name:
                raise DatasetValidationError(
                    f"row {row_num}: file_name must be 64-char lowercase hex (SHA256), got: {name!r}"
                )
            raise DatasetValidationError(
                f"row {row_num}: file_name must be lowercase hex: {name!r}"
            )
        if label not in VALID_LABELS:
            raise DatasetValidationError(
                f"row {row_num}: label must be one of {sorted(VALID_LABELS)}, got: {label!r}"
            )

        file_names.append(name)
        labels.append(label)
        label_counts[label] = label_counts.get(label, 0) + 1

        if has_family_col and label == "Malware":
            family = (row.get("family") or "").strip()
            if family:
                family_counts[family] = family_counts.get(family, 0) + 1

    if not file_names:
        raise DatasetValidationError("CSV is empty (no data rows)")

    return ParsedCsv(
        sample_count=len(file_names),
        label_distribution=label_counts,
        family_distribution=family_counts if family_counts else None,
        size_bytes=len(content.encode("utf-8")),
        checksum=compute_checksum(content),
        file_names=file_names,
        labels=labels,
    )


def _sample_path(samples_root: Path, file_name: str) -> Path:
    prefix = file_name[:2]
    return samples_root / prefix / file_name


def spot_check_samples(
    *,
    file_names: list[str],
    labels: list[str],
    samples_root: Path,
    sample_count: int,
    missing_threshold: int,
    rng: random.Random | None = None,
) -> SpotCheckResult:
    """Verify random subset of samples exists on disk.

    Args:
      file_names: parallel list of SHA256 file names
      labels: parallel list of labels (values: Malware | Benign); validated but
        not used for path construction (samples live flat under samples_root)
      samples_root: mount root containing <prefix>/<file_name> samples
      sample_count: how many samples to check; clamped to len(file_names)
      missing_threshold: raise DatasetIntegrityError if missing >= threshold

    Returns:
      SpotCheckResult on success (missing < threshold)

    Raises:
      DatasetValidationError: label not recognised
      DatasetIntegrityError: too many samples missing
    """
    if len(file_names) != len(labels):
        raise DatasetValidationError("file_names and labels length mismatch")
    for lbl in labels:
        if lbl not in VALID_LABELS:
            raise DatasetValidationError(f"unexpected label: {lbl!r}")

    n = min(sample_count, len(file_names))
    indices = list(range(len(file_names)))
    rng = rng or random.Random()
    rng.shuffle(indices)
    indices = indices[:n]

    missing = 0
    for i in indices:
        p = _sample_path(samples_root, file_names[i])
        if not p.exists():
            missing += 1

    if missing >= missing_threshold:
        raise DatasetIntegrityError(
            f"spot-check: {missing} missing out of {n} (threshold {missing_threshold})"
        )

    return SpotCheckResult(checked=n, missing=missing)
