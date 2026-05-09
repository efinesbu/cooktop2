"""CLI: argparse entry (`python -m cooktop_monitor`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cooktop_monitor",
        description="Detect cooktop control state from a photo.",
    )
    p.add_argument(
        "image",
        nargs="?",
        help="Path to a single image (JPG, PNG, HEIC, …).",
    )
    p.add_argument(
        "--batch",
        type=Path,
        metavar="DIR",
        help="Process every image file in DIR.",
    )
    p.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Batch mode: write combined JSON to FILE.",
    )
    p.add_argument(
        "--no-debug",
        action="store_true",
        help="Do not write <input>_debug.jpg.",
    )
    p.add_argument(
        "--layout",
        type=Path,
        metavar="YAML",
        help="Override panel_layout.yaml path.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.batch:
        sys.stderr.write("Batch mode not implemented yet.\n")
        return 2
    if not args.image:
        build_parser().print_help()
        return 1

    from cooktop_monitor.pipeline import run_single

    run_single(
        Path(args.image),
        layout_path=args.layout,
        write_debug=not args.no_debug,
    )
    return 0
