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

from polymarket_lower_win.chainlink_streams import (
    ChainlinkStreamsConfig,
    ChainlinkStreamsError,
    iter_chainlink_reports,
    parse_feed_id_overrides,
    resolve_feed_ids,
)
from polymarket_lower_win.env_config import get_float, get_int, get_list, get_str, load_env_file
from polymarket_lower_win.polymarket import iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 Chainlink Data Streams 原始报告并落盘。")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-messages", type=int, default=-1)
    return parser.parse_args()


def build_config(env_path: Path, *, override_run_id: str = "", override_max_messages: int = -1) -> tuple[str, Path, ChainlinkStreamsConfig, dict[str, str]]:
    env = load_env_file(env_path)
    run_id = override_run_id.strip() or get_str(
        env,
        "PM_CHAINLINK_RUN_ID",
        f"chainlink-streams-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
    )
    logs_root = Path(get_str(env, "PM_CHAINLINK_LOGS_ROOT", "Logs/chainlink_streams"))
    overrides = parse_feed_id_overrides(get_str(env, "PM_CHAINLINK_FEED_IDS", ""))
    symbols = tuple(get_list(env, "PM_CHAINLINK_SYMBOLS", ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]))
    feed_map = resolve_feed_ids(symbols, overrides=overrides)
    max_messages = override_max_messages if override_max_messages >= 0 else get_int(env, "PM_CHAINLINK_MAX_MESSAGES", 0)
    cfg = ChainlinkStreamsConfig(
        api_key=get_str(env, "PM_CHAINLINK_API_KEY", ""),
        api_secret=get_str(env, "PM_CHAINLINK_API_SECRET", ""),
        ws_endpoint=get_str(env, "PM_CHAINLINK_WS_ENDPOINT", "wss://ws.dataengine.chain.link"),
        symbols=tuple(str(item).lower() for item in symbols),
        recv_timeout_seconds=get_float(env, "PM_CHAINLINK_RECV_TIMEOUT_SECONDS", 10.0),
        ping_interval_seconds=get_float(env, "PM_CHAINLINK_PING_INTERVAL_SECONDS", 5.0),
        reconnect_seconds=get_float(env, "PM_CHAINLINK_RECONNECT_SECONDS", 2.0),
        max_messages=max_messages,
    )
    return run_id, logs_root / run_id, cfg, feed_map


def main() -> int:
    args = parse_args()
    run_id, run_dir, cfg, feed_map = build_config(
        Path(args.env_file),
        override_run_id=args.run_id,
        override_max_messages=args.max_messages,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    reports_path = run_dir / "reports.jsonl"
    summary_path = run_dir / "summary_latest.json"
    feed_map_path = run_dir / "feed_map.json"
    feed_map_path.write_text(json.dumps(feed_map, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "started_at_utc": iso_utc(int(time.time())),
        "updated_at_utc": "",
        "message_count": 0,
        "symbols": list(cfg.symbols),
        "feed_map": feed_map,
        "last_symbol": "",
        "last_feed_id": "",
        "last_error": "",
    }

    def write_summary() -> None:
        summary["updated_at_utc"] = iso_utc(int(time.time()))
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def on_status(status: str) -> None:
        summary["last_error"] = ""
        summary["status"] = status
        write_summary()

    try:
        with reports_path.open("a", encoding="utf-8") as handle:
            for record in iter_chainlink_reports(cfg, feed_map=feed_map, on_status=on_status):
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                summary["message_count"] = int(summary["message_count"]) + 1
                summary["last_symbol"] = record.get("symbol", "")
                summary["last_feed_id"] = record.get("feed_id", "")
                write_summary()
    except KeyboardInterrupt:
        summary["last_error"] = "keyboard_interrupt"
        write_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Logs: {run_dir}")
        return 0
    except ChainlinkStreamsError as exc:
        summary["last_error"] = str(exc)
        write_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Logs: {run_dir}")
        return 1
    except Exception as exc:  # noqa: BLE001
        summary["last_error"] = f"{type(exc).__name__}: {exc}"
        write_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Logs: {run_dir}")
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Logs: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
