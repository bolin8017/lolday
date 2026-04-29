#!/usr/bin/env python3
"""Sample 500 malware + 500 benign ELF SHAs and build train/test CSVs.

Intersects three sources:
  1. /data/samples/ — SHAs actually present on disk (what the K8s pods see)
  2. ~/Documents/Malware202403_info.csv — canonical malware label manifest
  3. ~/Documents/benignware_info.csv — canonical benign label manifest

Emits two CSVs in lolday's dataset_config format (file_name,label,family):
  - elf-text256-train.csv (800 samples, 80% stratified)
  - elf-text256-test.csv  (200 samples, 20% stratified)

Usage:
  python3 scripts/sample_elf_dataset.py \
      --malware-manifest ~/Documents/Malware202403_info.csv \
      --benign-manifest  ~/Documents/benignware_info.csv \
      --samples-root     /data/samples \
      --output-dir       /tmp/elf-text256-datasets \
      --per-class 500 --seed 42

The script does NOT upload to lolday — see the companion curl commands
in the Phase 8 E2E checklist, which depend on the operator having an
auth token.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def _collect_on_disk_shas(samples_root: Path) -> set[str]:
    """Walk /data/samples/<prefix>/<sha> — return set of sha filenames."""
    if not samples_root.is_dir():
        raise SystemExit(f"samples root does not exist: {samples_root}")
    shas: set[str] = set()
    for prefix_dir in samples_root.iterdir():
        if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
            continue
        for child in prefix_dir.iterdir():
            if child.is_file():
                shas.add(child.name)
    return shas


def _load_manifest(path: Path, expected_label: str) -> dict[str, str]:
    """Read manifest CSV → {sha: family_or_empty}.

    Row is kept iff its `label` column literally equals expected_label
    ('Malware' or 'Benignware' in our manifests). The platform's allowed
    labels are 'Malware' | 'Benign' — we map Benignware → Benign downstream.
    """
    entries: dict[str, str] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sha = row.get("file_name", "").strip()
            if len(sha) != 64:
                continue
            if row.get("label", "").strip() != expected_label:
                continue
            entries[sha] = row.get("family", "").strip()
    return entries


def _stratified_split(
    samples: list[tuple[str, str, str]], train_frac: float, rng: random.Random
) -> tuple[list, list]:
    """Split samples into (train, test) preserving class balance.

    `samples` rows are (sha, label, family). Shuffle within each label,
    then take train_frac from each class.
    """
    by_label: dict[str, list[tuple[str, str, str]]] = {}
    for s in samples:
        by_label.setdefault(s[1], []).append(s)
    train: list = []
    test: list = []
    for rows in by_label.values():
        rng.shuffle(rows)
        cut = int(len(rows) * train_frac)
        train.extend(rows[:cut])
        test.extend(rows[cut:])
    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


def _write_csv(rows: list[tuple[str, str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file_name", "label", "family"])
        for sha, label, family in rows:
            w.writerow([sha, label, family])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--malware-manifest", type=Path, required=True)
    p.add_argument("--benign-manifest", type=Path, required=True)
    p.add_argument("--samples-root", type=Path, default=Path("/data/samples"))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--per-class", type=int, default=500)
    p.add_argument("--train-frac", type=float, default=0.8)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    rng = random.Random(args.seed)

    print(f"[1/4] Scanning {args.samples_root} …")
    on_disk = _collect_on_disk_shas(args.samples_root)
    print(f"      {len(on_disk):,} sample files on disk")

    print(f"[2/4] Reading {args.malware_manifest} …")
    malware_all = _load_manifest(args.malware_manifest, "Malware")
    malware_candidates = [sha for sha in malware_all if sha in on_disk]
    print(
        f"      {len(malware_all):,} Malware rows → {len(malware_candidates):,} present on disk"
    )

    print(f"[3/4] Reading {args.benign_manifest} …")
    benign_all = _load_manifest(args.benign_manifest, "Benignware")
    benign_candidates = [sha for sha in benign_all if sha in on_disk]
    print(
        f"      {len(benign_all):,} Benignware rows → {len(benign_candidates):,} present on disk"
    )

    if len(malware_candidates) < args.per_class:
        raise SystemExit(
            f"only {len(malware_candidates)} malware SHAs available on disk "
            f"(need {args.per_class})"
        )
    if len(benign_candidates) < args.per_class:
        raise SystemExit(
            f"only {len(benign_candidates)} benign SHAs available on disk "
            f"(need {args.per_class})"
        )

    print(f"[4/4] Sampling {args.per_class} of each class + splitting …")
    mal_picked = rng.sample(malware_candidates, args.per_class)
    ben_picked = rng.sample(benign_candidates, args.per_class)

    samples: list[tuple[str, str, str]] = []
    for sha in mal_picked:
        samples.append((sha, "Malware", malware_all[sha]))
    for sha in ben_picked:
        # Manifest label is "Benignware"; platform schema expects "Benign".
        samples.append((sha, "Benign", ""))

    train_rows, test_rows = _stratified_split(samples, args.train_frac, rng)

    train_csv = args.output_dir / "elf-text256-train.csv"
    test_csv = args.output_dir / "elf-text256-test.csv"
    _write_csv(train_rows, train_csv)
    _write_csv(test_rows, test_csv)

    print()
    print(f"Wrote {train_csv}  ({len(train_rows)} rows)")
    print(f"Wrote {test_csv}   ({len(test_rows)} rows)")
    print()
    print("Next step — upload to lolday (requires $TOKEN):")
    print("  curl -X POST http://localhost:8000/api/v1/datasets \\")
    print('    -H "Authorization: Bearer $TOKEN" \\')
    print('    -H "Content-Type: application/json" \\')
    print(
        '    -d "{\\"name\\":\\"elf-text256-train\\",\\"csv_content\\":\\"$(python3 -c \'import sys,json;print(json.dumps(open(sys.argv[1]).read())[1:-1])\' %s)\\"}"'
        % train_csv
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
