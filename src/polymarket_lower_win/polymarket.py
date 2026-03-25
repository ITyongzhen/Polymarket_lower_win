from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from polymarket_lower_win.http import http_get_json, http_get_text

DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
PROFILE_BASE = "https://polymarket.com"

TIMEFRAME_SECONDS = {"5m": 5 * 60, "15m": 15 * 60}
UPDOWN_RE = re.compile(r"^(?P<symbol>[a-z0-9]+)-updown-(?P<mins>\d+)m-(?P<start>\d{10})$")


def iso_utc(ts: int | float | None = None) -> str:
    value = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def parse_json_array(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_optional_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def parse_timestamp(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        return int(raw)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def floor_timeframe_start(now_ts: int, timeframe: str) -> int:
    step = TIMEFRAME_SECONDS[timeframe]
    return now_ts - (now_ts % step)


def build_updown_slug(symbol: str, timeframe: str, start_ts: int) -> str:
    minutes = int(TIMEFRAME_SECONDS[timeframe] / 60)
    return f"{symbol.lower()}-updown-{minutes}m-{int(start_ts)}"


def parse_start_end_timestamps(market: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    slug = str(market.get("slug", "")).strip().lower()
    match = UPDOWN_RE.match(slug)
    if match:
        mins = int(match.group("mins"))
        start_ts = int(match.group("start"))
        return start_ts, start_ts + mins * 60
    start_ts = parse_timestamp(market.get("startDate")) or parse_timestamp(market.get("eventStartTime"))
    end_ts = (
        parse_timestamp(market.get("endDate"))
        or parse_timestamp(market.get("closeTime"))
        or parse_timestamp(market.get("umaEndDate"))
    )
    return start_ts, end_ts


def parse_binary_prices(market: Dict[str, Any]) -> Optional[Dict[str, float]]:
    outcomes = parse_json_array(market.get("outcomes"))
    prices = parse_json_array(market.get("outcomePrices"))
    if outcomes and prices and len(outcomes) == len(prices):
        out: Dict[str, float] = {}
        for outcome, price in zip(outcomes, prices):
            parsed = parse_optional_float(price)
            if parsed is None:
                return None
            out[str(outcome)] = parsed
        return out
    yes = parse_optional_float(market.get("yesPrice"))
    no = parse_optional_float(market.get("noPrice"))
    if yes is None or no is None:
        return None
    return {"Yes": yes, "No": no}


def infer_symbol(value: str | Dict[str, Any]) -> Optional[str]:
    if isinstance(value, dict):
        haystack = " ".join(
            str(value.get(key, "")) for key in ("slug", "title", "question", "eventSlug", "seriesSlug")
        ).lower()
    else:
        haystack = str(value or "").lower()
    if "bitcoin" in haystack or "btc" in haystack:
        return "btc"
    if "ethereum" in haystack or re.search(r"\beth\b", haystack):
        return "eth"
    if "solana" in haystack or re.search(r"\bsol\b", haystack):
        return "sol"
    if "xrp" in haystack or "ripple" in haystack:
        return "xrp"
    if "dogecoin" in haystack or re.search(r"\bdoge\b", haystack):
        return "doge"
    if "binance coin" in haystack or "binancecoin" in haystack or re.search(r"\bbnb\b", haystack):
        return "bnb"
    if "hyperliquid" in haystack or re.search(r"\bhype\b", haystack):
        return "hype"
    return None


def choose_latest_market(markets: Iterable[Dict[str, Any]], now_ts: int) -> Optional[Dict[str, Any]]:
    future: List[tuple[int, int, Dict[str, Any]]] = []
    all_rows: List[tuple[int, int, Dict[str, Any]]] = []
    for market in markets:
        start_ts, end_ts = parse_start_end_timestamps(market)
        if end_ts is None:
            continue
        row = (end_ts, start_ts or 0, market)
        all_rows.append(row)
        if end_ts >= now_ts - 15:
            future.append(row)
    if future:
        future.sort(key=lambda item: (item[0], -item[1]))
        return future[0][2]
    if not all_rows:
        return None
    all_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return all_rows[0][2]


@dataclass(frozen=True)
class BinaryMarket:
    symbol: str
    timeframe: str
    slug: str
    title: str
    start_ts: int
    end_ts: int
    up_price: float
    down_price: float
    min_order_size: float
    tick_size: float
    active: bool
    closed: bool
    source: str

    @property
    def pair_price(self) -> float:
        return float(self.up_price) + float(self.down_price)

    @property
    def low_outcome(self) -> str:
        return "Up" if self.up_price <= self.down_price else "Down"

    @property
    def low_price(self) -> float:
        return min(float(self.up_price), float(self.down_price))

    @property
    def high_outcome(self) -> str:
        return "Down" if self.low_outcome == "Up" else "Up"

    def price_for_outcome(self, outcome: str) -> float:
        return float(self.up_price) if str(outcome).lower() == "up" else float(self.down_price)


def market_from_gamma(market: Dict[str, Any], *, source: str) -> Optional[BinaryMarket]:
    prices = parse_binary_prices(market)
    if not prices:
        return None
    symbol = infer_symbol(market)
    start_ts, end_ts = parse_start_end_timestamps(market)
    slug = str(market.get("slug") or "").strip()
    if not symbol or start_ts is None or end_ts is None or not slug:
        return None
    timeframe = next(
        (label for label, seconds in TIMEFRAME_SECONDS.items() if int(seconds) == int(end_ts - start_ts)),
        None,
    )
    if timeframe is None:
        return None
    up_price = None
    down_price = None
    for outcome, price in prices.items():
        low = outcome.strip().lower()
        if low == "yes" or "up" in low:
            up_price = float(price)
        if low == "no" or "down" in low:
            down_price = float(price)
    if up_price is None or down_price is None:
        return None
    return BinaryMarket(
        symbol=symbol,
        timeframe=timeframe,
        slug=slug,
        title=str(market.get("question") or market.get("title") or slug),
        start_ts=int(start_ts),
        end_ts=int(end_ts),
        up_price=float(up_price),
        down_price=float(down_price),
        min_order_size=float(parse_optional_float(market.get("orderMinSize")) or 0.0),
        tick_size=float(parse_optional_float(market.get("orderPriceMinTickSize")) or 0.01),
        active=bool(market.get("active", True)),
        closed=bool(market.get("closed", False)),
        source=source,
    )


def fetch_markets_by_slug(slug: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    payload = http_get_json(f"{GAMMA_API_BASE}/markets", params={"slug": slug, "limit": limit})
    return payload if isinstance(payload, list) else []


def fetch_current_market(symbol: str, timeframe: str, *, now_ts: int | None = None) -> Optional[BinaryMarket]:
    current_ts = int(now_ts or time.time())
    start_ts = floor_timeframe_start(current_ts, timeframe)
    starts = [start_ts, start_ts - TIMEFRAME_SECONDS[timeframe]]
    raw_rows: List[Dict[str, Any]] = []
    for item in starts:
        raw_rows.extend(fetch_markets_by_slug(build_updown_slug(symbol, timeframe, item), limit=5))
    chosen = choose_latest_market(raw_rows, current_ts)
    if chosen is None:
        return None
    market = market_from_gamma(chosen, source="gamma_slug")
    if market is None:
        return None
    if market.end_ts < current_ts - 15:
        return None
    return market


def fetch_current_markets(
    symbols: Iterable[str],
    timeframes: Iterable[str],
    *,
    now_ts: int | None = None,
) -> List[BinaryMarket]:
    current_ts = int(now_ts or time.time())
    rows: List[BinaryMarket] = []
    for symbol in symbols:
        for timeframe in timeframes:
            if timeframe not in TIMEFRAME_SECONDS:
                continue
            market = fetch_current_market(symbol, timeframe, now_ts=current_ts)
            if market is not None:
                rows.append(market)
    rows.sort(key=lambda item: (item.symbol, item.timeframe, item.end_ts))
    return rows


def find_next_data(html: str) -> Dict[str, Any]:
    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, flags=re.S)
    if match is None:
        raise RuntimeError("next_data_missing")
    return json.loads(match.group(1))


def resolve_profile(username: str, *, tab: str = "positions") -> Dict[str, Any]:
    clean = str(username or "").strip().lstrip("@")
    if not clean:
        raise ValueError("username required")
    html = http_get_text(f"{PROFILE_BASE}/@{clean}", params={"tab": tab})
    next_data = find_next_data(html)
    props = next_data.get("props", {}).get("pageProps", {})
    return {
        "username": props.get("username") or clean,
        "base_address": props.get("baseAddress"),
        "proxy_address": props.get("proxyAddress"),
        "primary_address": props.get("primaryAddress"),
        "has_traded_from_base_address": props.get("hasTradedFromBaseAddress"),
        "profile_slug": props.get("profileSlug"),
    }


def fetch_profile_activity_page(user: str, *, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    payload = http_get_json(
        f"{DATA_API_BASE}/activity",
        params={
            "user": str(user or "").strip(),
            "limit": int(limit),
            "offset": int(offset),
            "type": "TRADE",
        },
    )
    return payload if isinstance(payload, list) else []


def fetch_profile_positions_page(
    user: str,
    *,
    limit: int = 500,
    offset: int = 0,
    closed: bool = False,
) -> List[Dict[str, Any]]:
    endpoint = "closed-positions" if closed else "positions"
    payload = http_get_json(
        f"{DATA_API_BASE}/{endpoint}",
        params={
            "user": str(user or "").strip(),
            "limit": int(limit),
            "offset": int(offset),
            "sizeThreshold": 0,
        },
    )
    return payload if isinstance(payload, list) else []
