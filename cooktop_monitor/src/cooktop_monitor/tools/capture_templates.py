"""Extract digit crops from reference photos into reference_templates/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build digit_* templates from known-state photo filenames.")
    p.add_argument("photos_dir", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    del args
    sys.stderr.write("capture_templates not implemented yet.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
