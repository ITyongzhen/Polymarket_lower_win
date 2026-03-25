#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from polymarket_lower_win.env_config import get_bool, get_float, get_int, get_list, get_str, load_env_file
from polymarket_lower_win.paper import PaperConfig, PaperSimulator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 .env 读取参数，运行低概率单边模拟盘。")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def load_paper_config_from_env(env_path: Path, *, override_run_id: str = "") -> PaperConfig:
    """把 .env 转成 PaperConfig。

    用户要求“所有参数都写在 env 配置里”，所以这里不再依赖 json 配置文件。
    """
    env = load_env_file(env_path)
    run_id = override_run_id.strip() or get_str(
        env,
        "PM_RUN_ID",
        f"paper-low-win-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
    )
    payload = {
        "run_id": run_id,
        "poll_seconds": get_float(env, "PM_POLL_SECONDS", 5.0),
        "run_minutes": get_float(env, "PM_RUN_MINUTES", 60.0),
        "bankroll_usd": get_float(env, "PM_BANKROLL_USD", 200.0),
        "symbols": get_list(env, "PM_SYMBOLS", ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]),
        "timeframes": get_list(env, "PM_TIMEFRAMES", ["5m", "15m"]),
        "logs_root": get_str(env, "PM_LOGS_ROOT", "Logs/paper_low_win"),
        "shares_per_signal": get_float(env, "PM_SHARES_PER_SIGNAL", 10.0),
        "child_shares": get_float(env, "PM_CHILD_SHARES", 2.0),
        "max_shares_per_market": get_float(env, "PM_MAX_SHARES_PER_MARKET", 10.0),
        "max_open_positions": get_int(env, "PM_MAX_OPEN_POSITIONS", 16),
        "min_low_price": get_float(env, "PM_MIN_LOW_PRICE", 0.001),
        "max_low_price": get_float(env, "PM_MAX_LOW_PRICE", 0.03),
        "skip_dual_side_pair_markets": get_bool(env, "PM_SKIP_DUAL_SIDE_PAIR_MARKETS", True),
        "dual_side_pair_price_cap": get_float(env, "PM_DUAL_SIDE_PAIR_PRICE_CAP", 0.05),
        "pre_min_seconds_remaining": get_int(env, "PM_PRE_MIN_SECONDS_REMAINING", 30),
        "pre_max_seconds_remaining": get_int(env, "PM_PRE_MAX_SECONDS_REMAINING", 240),
        "allow_post_close": get_bool(env, "PM_ALLOW_POST_CLOSE", True),
        "post_max_seconds_after_end": get_int(env, "PM_POST_MAX_SECONDS_AFTER_END", 5),
        "settlement_grace_seconds": get_int(env, "PM_SETTLEMENT_GRACE_SECONDS", 8),
        "block_post_close_for_source_mismatch": get_bool(
            env,
            "PM_BLOCK_POST_CLOSE_FOR_SOURCE_MISMATCH",
            True,
        ),
        "source_mismatch_guard_seconds": get_int(
            env,
            "PM_SOURCE_MISMATCH_GUARD_SECONDS",
            90,
        ),
        "source_mismatch_min_edge_points_near_close": get_float(
            env,
            "PM_SOURCE_MISMATCH_MIN_EDGE_POINTS_NEAR_CLOSE",
            0.01,
        ),
        "flat_move_bps": get_float(env, "PM_FLAT_MOVE_BPS", 5.0),
        "flat_range_bps": get_float(env, "PM_FLAT_RANGE_BPS", 15.0),
        "mild_move_bps": get_float(env, "PM_MILD_MOVE_BPS", 12.0),
        "mild_range_bps": get_float(env, "PM_MILD_RANGE_BPS", 25.0),
        "stress_move_bps": get_float(env, "PM_STRESS_MOVE_BPS", 20.0),
        "stress_range_bps": get_float(env, "PM_STRESS_RANGE_BPS", 40.0),
        "pre_flat_price_cap": get_float(env, "PM_PRE_FLAT_PRICE_CAP", 0.03),
        "pre_mild_price_cap": get_float(env, "PM_PRE_MILD_PRICE_CAP", 0.02),
        "pre_stress_price_cap": get_float(env, "PM_PRE_STRESS_PRICE_CAP", 0.01),
        "post_flat_price_cap": get_float(env, "PM_POST_FLAT_PRICE_CAP", 0.01),
        "post_mild_price_cap": get_float(env, "PM_POST_MILD_PRICE_CAP", 0.005),
        "fair_base_prob": get_float(env, "PM_FAIR_BASE_PROB", 0.003),
        "fair_time_weight": get_float(env, "PM_FAIR_TIME_WEIGHT", 0.015),
        "fair_flat_bonus": get_float(env, "PM_FAIR_FLAT_BONUS", 0.012),
        "fair_calm_bonus": get_float(env, "PM_FAIR_CALM_BONUS", 0.010),
        "fair_disagreement_bonus": get_float(env, "PM_FAIR_DISAGREEMENT_BONUS", 0.015),
        "fair_reversal_bonus": get_float(env, "PM_FAIR_REVERSAL_BONUS", 0.006),
        "fair_post_close_penalty": get_float(env, "PM_FAIR_POST_CLOSE_PENALTY", 0.015),
        "min_edge_points": get_float(env, "PM_MIN_EDGE_POINTS", 0.003),
        "hold_to_settlement": get_bool(env, "PM_HOLD_TO_SETTLEMENT", True),
    }
    return PaperConfig.from_dict(payload)


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file)
    cfg = load_paper_config_from_env(env_path, override_run_id=args.run_id)
    runner = PaperSimulator(cfg)
    payload = runner.run(once=bool(args.once))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Logs: {runner.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
