#!/usr/bin/env python3
"""Re-trim whitespace on MC question PNGs (processed/MC and synced notes copies)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from split_mc import trim_question_image  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed",
        type=Path,
        default=ROOT / "processed" / "MC",
        help="Root of processed MC PNGs (default: processed/MC)",
    )
    parser.add_argument(
        "--notes",
        type=Path,
        default=ROOT.parent / "notes",
        help="Also trim synced notes/**/questions/*.png",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def trim_file(path: Path, *, dry_run: bool) -> tuple[int, int] | None:
    image = Image.open(path)
    image.load()
    before = image.size
    trimmed = trim_question_image(image)
    after = trimmed.size
    if after == before:
        return None
    if not dry_run:
        trimmed.save(path, format="PNG")
    return before, after


def main() -> None:
    args = parse_args()
    paths: list[Path] = []
    if args.processed.is_dir():
        paths.extend(sorted(args.processed.glob("**/q*.png")))
    if args.notes.is_dir():
        paths.extend(sorted(args.notes.glob("**/questions/*.png")))
    paths = sorted(set(paths))

    changed = 0
    for path in paths:
        if not path.is_file():
            continue
        result = trim_file(path, dry_run=args.dry_run)
        if result is None:
            continue
        before, after = result
        changed += 1
        print(f"{path.relative_to(ROOT.parent)}: {before[0]}x{before[1]} -> {after[0]}x{after[1]}")

    action = "Would trim" if args.dry_run else "Trimmed"
    print(f"\n{action} {changed} / {len(paths)} PNGs.")


if __name__ == "__main__":
    main()
