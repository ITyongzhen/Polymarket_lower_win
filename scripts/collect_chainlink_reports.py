#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

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
from polymarket_lower_win.log_paths import local_day_key, midnight_run_stamp, normalize_logs_root, resolve_run_id
from polymarket_lower_win.polymarket import iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 Chainlink Data Streams 原始报告并落盘。")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-messages", type=int, default=-1)
    return parser.parse_args()


def build_config(env_path: Path, *, override_run_id: str = "", override_max_messages: int = -1) -> tuple[str, Path, ChainlinkStreamsConfig, dict[str, str]]:
    env = load_env_file(env_path)
    run_id = resolve_run_id(override_run_id.strip() or env.get("PM_CHAINLINK_RUN_ID", ""))
    logs_root = normalize_logs_root(
        get_str(env, "PM_CHAINLINK_LOGS_ROOT", "logs/chainlink_streams"),
        default_subdir="chainlink_streams",
    )
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


class ChainlinkRunLogger:
    """按天切分 Chainlink 原始报告目录。"""

    def __init__(self, logs_root: Path, initial_run_id: str, cfg: ChainlinkStreamsConfig, feed_map: dict[str, str]) -> None:
        self.logs_root = normalize_logs_root(logs_root, default_subdir="chainlink_streams")
        self.cfg = cfg
        self.feed_map = dict(feed_map)
        now_ts = int(time.time())
        self.active_log_local_day = local_day_key(now_ts)
        self.run_id = resolve_run_id(initial_run_id, now_ts=now_ts)
        self.run_dir = self.logs_root / self.run_id
        self.reports_path = self.run_dir / "reports.jsonl"
        self.summary_path = self.run_dir / "summary_latest.json"
        self.feed_map_path = self.run_dir / "feed_map.json"
        self.summary: dict[str, Any] = {}
        self._activate_run_dir(self.run_id, started_ts=now_ts)

    def _activate_run_dir(self, run_id: str, *, started_ts: int) -> None:
        self.run_id = run_id
        self.run_dir = self.logs_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.reports_path = self.run_dir / "reports.jsonl"
        self.summary_path = self.run_dir / "summary_latest.json"
        self.feed_map_path = self.run_dir / "feed_map.json"
        self.feed_map_path.write_text(json.dumps(self.feed_map, ensure_ascii=False, indent=2), encoding="utf-8")
        self.reports_path.touch(exist_ok=True)
        self.summary = {
            "run_id": run_id,
            "started_at_utc": iso_utc(started_ts),
            "updated_at_utc": "",
            "message_count": 0,
            "symbols": list(self.cfg.symbols),
            "feed_map": self.feed_map,
            "last_symbol": "",
            "last_feed_id": "",
            "last_error": "",
            "logs_root": str(self.logs_root),
        }
        self.write_summary()

    def _rotate_logs_if_needed(self, now_ts: int) -> None:
        current_local_day = local_day_key(now_ts)
        if current_local_day == self.active_log_local_day:
            return
        previous_run_id = self.run_id
        self.active_log_local_day = current_local_day
        self._activate_run_dir(midnight_run_stamp(now_ts), started_ts=now_ts)
        self.summary["rotation"] = {
            "previous_run_id": previous_run_id,
            "run_id": self.run_id,
            "local_day": current_local_day,
            "rotated_at_utc": iso_utc(now_ts),
        }
        self.write_summary()

    def write_summary(self) -> None:
        self.summary["updated_at_utc"] = iso_utc(int(time.time()))
        self.summary_path.write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def on_status(self, status: str) -> None:
        self._rotate_logs_if_needed(int(time.time()))
        self.summary["last_error"] = ""
        self.summary["status"] = status
        self.write_summary()

    def append_record(self, record: dict[str, Any]) -> None:
        received_ts_ms = int(record.get("received_ts_ms") or int(time.time() * 1000))
        self._rotate_logs_if_needed(int(received_ts_ms / 1000))
        with self.reports_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.summary["message_count"] = int(self.summary["message_count"]) + 1
        self.summary["last_symbol"] = record.get("symbol", "")
        self.summary["last_feed_id"] = record.get("feed_id", "")
        self.write_summary()

    def set_error(self, message: str) -> None:
        self.summary["last_error"] = str(message)
        self.write_summary()


def main() -> int:
    args = parse_args()
    run_id, run_dir, cfg, feed_map = build_config(
        Path(args.env_file),
        override_run_id=args.run_id,
        override_max_messages=args.max_messages,
    )
    logger = ChainlinkRunLogger(run_dir.parent, run_id, cfg, feed_map)

    try:
        for record in iter_chainlink_reports(cfg, feed_map=feed_map, on_status=logger.on_status):
            logger.append_record(record)
    except KeyboardInterrupt:
        logger.set_error("keyboard_interrupt")
        print(json.dumps(logger.summary, ensure_ascii=False, indent=2))
        print(f"Logs: {logger.run_dir}")
        return 0
    except ChainlinkStreamsError as exc:
        logger.set_error(str(exc))
        print(json.dumps(logger.summary, ensure_ascii=False, indent=2))
        print(f"Logs: {logger.run_dir}")
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.set_error(f"{type(exc).__name__}: {exc}")
        print(json.dumps(logger.summary, ensure_ascii=False, indent=2))
        print(f"Logs: {logger.run_dir}")
        return 1

    print(json.dumps(logger.summary, ensure_ascii=False, indent=2))
    print(f"Logs: {logger.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
