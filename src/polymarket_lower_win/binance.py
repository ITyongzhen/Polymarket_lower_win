from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from polymarket_lower_win.http import http_get_json, http_post_json
from polymarket_lower_win.polymarket import TIMEFRAME_SECONDS

BINANCE_SPOT_BASE = "https://api.binance.com/api/v3"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
BINANCE_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "bnb": "BNBUSDT",
}


@dataclass(frozen=True)
class BinancePeriodSnapshot:
    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
    open_price: float
    high_price: float
    low_price: float
    last_price: float
    source: str = "binance_rest"

    @property
    def delta_bps(self) -> float:
        if self.open_price <= 0:
            return 0.0
        return ((self.last_price - self.open_price) / self.open_price) * 10000.0

    @property
    def range_bps(self) -> float:
        if self.open_price <= 0:
            return 0.0
        return ((self.high_price - self.low_price) / self.open_price) * 10000.0


def _parse_kline_row(row: List[Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, list) or len(row) < 7:
        return None
    try:
        return {
            "open_time_s": int(int(row[0]) / 1000),
            "open_price": float(row[1]),
            "high_price": float(row[2]),
            "low_price": float(row[3]),
            "close_price": float(row[4]),
            "close_time_s": int(int(row[6]) / 1000),
        }
    except (TypeError, ValueError):
        return None


def _fetch_hyperliquid_snapshot(symbol: str, timeframe: str, start_ts: int) -> Optional[BinancePeriodSnapshot]:
    if str(symbol).lower() != "hype" or timeframe not in TIMEFRAME_SECONDS:
        return None
    start_ms = int(start_ts) * 1000
    end_ms = int(start_ts + TIMEFRAME_SECONDS[timeframe]) * 1000
    payload = http_post_json(
        HYPERLIQUID_INFO_URL,
        payload={
            "type": "candleSnapshot",
            "req": {
                "coin": "HYPE",
                "interval": str(timeframe),
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
    )
    if not isinstance(payload, list) or not payload:
        return None
    row = next((item for item in payload if int(item.get("t", 0)) == start_ms), payload[0])
    try:
        open_time_s = int(int(row["t"]) / 1000)
        open_price = float(row["o"])
        high_price = float(row["h"])
        low_price = float(row["l"])
        close_price = float(row["c"])
    except (KeyError, TypeError, ValueError):
        return None
    return BinancePeriodSnapshot(
        symbol="hype",
        timeframe=timeframe,
        start_ts=open_time_s,
        end_ts=open_time_s + TIMEFRAME_SECONDS[timeframe],
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        last_price=close_price,
        source="hyperliquid_rest",
    )


def fetch_period_snapshot(symbol: str, timeframe: str, start_ts: int) -> Optional[BinancePeriodSnapshot]:
    if str(symbol).lower() == "hype":
        return _fetch_hyperliquid_snapshot(symbol, timeframe, start_ts)
    pair = BINANCE_SYMBOLS.get(str(symbol).lower())
    if pair is None or timeframe not in TIMEFRAME_SECONDS:
        return None
    payload = http_get_json(
        f"{BINANCE_SPOT_BASE}/klines",
        params={
            "symbol": pair,
            "interval": timeframe,
            "startTime": int(start_ts) * 1000,
            "limit": 2,
        },
    )
    if not isinstance(payload, list):
        return None
    rows = [_parse_kline_row(item) for item in payload]
    clean = [item for item in rows if item is not None]
    if not clean:
        return None
    selected = next((item for item in clean if int(item["open_time_s"]) == int(start_ts)), clean[0])
    return BinancePeriodSnapshot(
        symbol=str(symbol).lower(),
        timeframe=timeframe,
        start_ts=int(selected["open_time_s"]),
        end_ts=int(selected["open_time_s"]) + TIMEFRAME_SECONDS[timeframe],
        open_price=float(selected["open_price"]),
        high_price=float(selected["high_price"]),
        low_price=float(selected["low_price"]),
        last_price=float(selected["close_price"]),
    )


def determine_winning_outcome(symbol: str, timeframe: str, start_ts: int) -> Optional[str]:
    snapshot = fetch_period_snapshot(symbol, timeframe, start_ts)
    if snapshot is None:
        return None
    return "Up" if snapshot.last_price >= snapshot.open_price else "Down"
