#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from polymarket_lower_win.profile_cache import cache_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache Polymarket profile activity and positions locally.")
    parser.add_argument("username", help="Polymarket username, with or without leading @.")
    parser.add_argument("--output-root", default="data/raw/polymarket_profiles")
    parser.add_argument("--activity-limit", type=int, default=100)
    parser.add_argument("--positions-limit", type=int, default=500)
    parser.add_argument("--max-activity-pages", type=int, default=31)
    parser.add_argument("--max-positions-pages", type=int, default=4)
    parser.add_argument("--import-pages-dir", default="", help="Import already-downloaded activity pages.")
    parser.add_argument("--no-network", action="store_true", help="Skip all network fetches.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = cache_profile(
        args.username,
        output_root=args.output_root,
        activity_limit=args.activity_limit,
        positions_limit=args.positions_limit,
        max_activity_pages=args.max_activity_pages,
        max_positions_pages=args.max_positions_pages,
        import_pages_dir=args.import_pages_dir or None,
        no_network=bool(args.no_network),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(
        f"Saved cache: {Path(args.output_root) / str(args.username).strip().lstrip('@') / 'cache_summary.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
