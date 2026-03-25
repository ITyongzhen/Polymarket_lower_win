#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATA_API_BASE = "https://data-api.polymarket.com"
PROFILE_BASE = "https://polymarket.com"
BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
UPDOWN_RE = re.compile(r"^(?P<symbol>[a-z0-9]+)-updown-(?P<mins>\d+)m-(?P<start>\d{10})$")
UTC = timezone.utc
BINANCE_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "bnb": "BNBUSDT",
}
MAX_ACTIVITY_OFFSET = 3000


def iso_to_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def ts_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def day_floor(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=UTC)
    floored = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(floored.timestamp())


def pct(part: float, whole: float) -> float:
    if whole == 0:
        return 0.0
    return 100.0 * part / whole


def fmt_num(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}"


def fmt_pct(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}%"


def http_get(url: str, params: Dict[str, Any] | None = None, expect_json: bool = True) -> Any:
    query = urlencode(params or {}, doseq=True)
    full_url = f"{url}?{query}" if query else url
    req = Request(
        full_url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        },
    )
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            try:
                curl_result = subprocess.run(
                    ["curl", "-sL", "--http1.1", "--retry", "2", "--retry-delay", "1", full_url],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception:
                curl_result = None
            if curl_result is not None and curl_result.returncode == 0 and curl_result.stdout.strip():
                try:
                    return json.loads(curl_result.stdout) if expect_json else curl_result.stdout
                except json.JSONDecodeError as json_exc:
                    last_exc = json_exc
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if expect_json else body
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            last_exc = exc
            if attempt == 4:
                break
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"request failed: {full_url}\n{last_exc}")


def find_next_data(html: str) -> Dict[str, Any]:
    match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html,
        flags=re.S,
    )
    if not match:
        raise RuntimeError("could not locate __NEXT_DATA__ on profile page")
    return json.loads(match.group(1))


def extract_profile_snapshot(username: str) -> Dict[str, Any]:
    html = http_get(f"{PROFILE_BASE}/@{username}?tab=positions", expect_json=False)
    next_data = find_next_data(html)
    page_props = next_data.get("props", {}).get("pageProps", {})
    queries = page_props.get("dehydratedState", {}).get("queries", [])

    out: Dict[str, Any] = {
        "username": page_props.get("username") or username,
        "base_address": page_props.get("baseAddress"),
        "proxy_address": page_props.get("proxyAddress"),
        "primary_address": page_props.get("primaryAddress"),
        "has_traded_from_base_address": page_props.get("hasTradedFromBaseAddress"),
        "profile_slug": page_props.get("profileSlug"),
    }
    for query in queries:
        key = query.get("queryKey", [])
        state = query.get("state", {})
        data = state.get("data")
        if key[:1] == ["/api/profile/volume"] and isinstance(data, dict):
            out["volume_snapshot"] = data
        elif key[:1] == ["/api/profile/userData"] and isinstance(data, dict):
            out["user_data"] = data
        elif key[:1] == ["positions", "value"] and isinstance(data, (int, float)):
            out["positions_value"] = float(data)
        elif key[:1] == ["user-stats"] and isinstance(data, dict):
            out["user_stats"] = data
    return out


def fetch_paged(
    url: str,
    base_params: Dict[str, Any],
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 500,
    split_depth: int = 0,
) -> List[Dict[str, Any]]:
    if split_depth > 10:
        raise RuntimeError(f"pagination split too deep for {url}")

    params = dict(base_params)
    params["limit"] = limit
    if start_ts is not None:
        params["start"] = start_ts
    if end_ts is not None:
        params["end"] = end_ts

    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params["offset"] = offset
        page = http_get(url, params=params, expect_json=True)
        if isinstance(page, dict):
            error_text = str(page.get("error") or "")
            if (
                "max historical activity offset" in error_text.lower()
                and start_ts is not None
                and end_ts is not None
                and end_ts > start_ts
            ):
                mid = start_ts + (end_ts - start_ts) // 2
                left = fetch_paged(
                    url,
                    base_params=base_params,
                    start_ts=start_ts,
                    end_ts=mid,
                    limit=limit,
                    split_depth=split_depth + 1,
                )
                right = fetch_paged(
                    url,
                    base_params=base_params,
                    start_ts=mid + 1,
                    end_ts=end_ts,
                    limit=limit,
                    split_depth=split_depth + 1,
                )
                return left + right
        if not isinstance(page, list):
            raise RuntimeError(f"unexpected response from {url}: {page!r}")
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        # Historical activity pagination currently refuses offsets above 3000.
        if offset + limit > MAX_ACTIVITY_OFFSET and start_ts is not None and end_ts is not None and end_ts > start_ts:
            mid = start_ts + (end_ts - start_ts) // 2
            left = fetch_paged(
                url,
                base_params=base_params,
                start_ts=start_ts,
                end_ts=mid,
                limit=limit,
                split_depth=split_depth + 1,
            )
            right = fetch_paged(
                url,
                base_params=base_params,
                start_ts=mid + 1,
                end_ts=end_ts,
                limit=limit,
                split_depth=split_depth + 1,
            )
            return left + right
    return rows


def fetch_activity_by_day(address: str, start_ts: int, end_ts: int, side: str | None = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    current = day_floor(start_ts)
    while current <= end_ts:
        window_start = max(current, start_ts)
        window_end = min(current + 86400 - 1, end_ts)
        params: Dict[str, Any] = {
            "user": address,
            "type": "TRADE",
            "sortBy": "TIMESTAMP",
            "sortDirection": "ASC",
        }
        if side:
            params["side"] = side
        rows.extend(
            fetch_paged(
                f"{DATA_API_BASE}/activity",
                base_params=params,
                start_ts=window_start,
                end_ts=window_end,
            )
        )
        current += 86400
    rows.sort(key=lambda item: (int(item.get("timestamp", 0)), str(item.get("transactionHash", ""))))
    return rows


def fetch_activity_latest(address: str, side: str | None = None, limit: int = 100) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while offset <= MAX_ACTIVITY_OFFSET:
        params: Dict[str, Any] = {
            "user": address,
            "type": "TRADE",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "limit": limit,
            "offset": offset,
        }
        if side:
            params["side"] = side
        page = http_get(f"{DATA_API_BASE}/activity", params=params, expect_json=True)
        if isinstance(page, dict):
            break
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    rows.sort(key=lambda item: (int(item.get("timestamp", 0)), str(item.get("transactionHash", ""))))
    return rows


def fetch_positions(address: str, closed: bool) -> List[Dict[str, Any]]:
    endpoint = "closed-positions" if closed else "positions"
    params = {
        "user": address,
        "limit": 500,
        "offset": 0,
        "sortBy": "TOKENS",
        "sortDirection": "DESC",
        "sizeThreshold": 0,
    }
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params["offset"] = offset
        page = http_get(f"{DATA_API_BASE}/{endpoint}", params=params, expect_json=True)
        if not isinstance(page, list):
            raise RuntimeError(f"unexpected {endpoint} response")
        if not page:
            break
        rows.extend(page)
        if len(page) < 500:
            break
        offset += 500
        if offset > 10000:
            break
    return rows


def parse_updown_slug(event_slug: str) -> Dict[str, Any] | None:
    match = UPDOWN_RE.match(event_slug or "")
    if not match:
        return None
    symbol = match.group("symbol").lower()
    mins = int(match.group("mins"))
    start_ts = int(match.group("start"))
    end_ts = start_ts + mins * 60
    return {
        "symbol": symbol,
        "duration_min": mins,
        "start_ts": start_ts,
        "nominal_end_ts": end_ts,
    }


def price_bucket(price: float) -> str:
    if price < 0.001:
        return "<0.001"
    if price < 0.01:
        return "0.001-0.01"
    if price <= 0.03:
        return "0.01-0.03"
    if price <= 0.05:
        return "0.03-0.05"
    if price <= 0.10:
        return "0.05-0.10"
    return ">0.10"


def move_bucket(abs_bps: float) -> str:
    if abs_bps <= 5:
        return "<=5bps"
    if abs_bps <= 10:
        return "5-10bps"
    if abs_bps <= 20:
        return "10-20bps"
    if abs_bps <= 50:
        return "20-50bps"
    return ">50bps"


def elapsed_bucket(elapsed_pct: float) -> str:
    if elapsed_pct <= 0.25:
        return "<=25%"
    if elapsed_pct <= 0.50:
        return "25-50%"
    if elapsed_pct <= 0.75:
        return "50-75%"
    if elapsed_pct <= 1.00:
        return "75-100%"
    return ">100%"


def timing_bucket(seconds_to_end: float) -> str:
    if seconds_to_end > 300:
        return ">300s"
    if seconds_to_end > 60:
        return "60-300s"
    if seconds_to_end > 30:
        return "30-60s"
    if seconds_to_end > 10:
        return "10-30s"
    if seconds_to_end >= 0:
        return "0-10s"
    if seconds_to_end >= -10:
        return "-10-0s"
    return "<-10s"


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class EnrichedTrade:
    timestamp: int
    side: str
    symbol: str
    duration_min: int
    event_slug: str
    condition_id: str
    outcome: str
    price: float
    size: float
    usdc_size: float
    seconds_to_end: float
    timing_bucket: str
    price_bucket: str
    transaction_hash: str
    title: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": ts_to_iso(self.timestamp),
            "side": self.side,
            "symbol": self.symbol,
            "duration_min": self.duration_min,
            "event_slug": self.event_slug,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "price": self.price,
            "size": self.size,
            "usdc_size": self.usdc_size,
            "seconds_to_end": self.seconds_to_end,
            "timing_bucket": self.timing_bucket,
            "price_bucket": self.price_bucket,
            "transaction_hash": self.transaction_hash,
            "title": self.title,
        }


def enrich_activity(rows: Sequence[Dict[str, Any]], symbols: set[str]) -> List[EnrichedTrade]:
    out: List[EnrichedTrade] = []
    for row in rows:
        parsed = parse_updown_slug(str(row.get("eventSlug", "")))
        if not parsed:
            continue
        symbol = parsed["symbol"]
        if symbols and symbol not in symbols:
            continue
        timestamp = int(row.get("timestamp", 0) or 0)
        seconds_to_end = float(parsed["nominal_end_ts"] - timestamp)
        out.append(
            EnrichedTrade(
                timestamp=timestamp,
                side=str(row.get("side", "")),
                symbol=symbol,
                duration_min=int(parsed["duration_min"]),
                event_slug=str(row.get("eventSlug", "")),
                condition_id=str(row.get("conditionId", "")),
                outcome=str(row.get("outcome", "")),
                price=safe_float(row.get("price")),
                size=safe_float(row.get("size")),
                usdc_size=safe_float(row.get("usdcSize")),
                seconds_to_end=seconds_to_end,
                timing_bucket=timing_bucket(seconds_to_end),
                price_bucket=price_bucket(safe_float(row.get("price"))),
                transaction_hash=str(row.get("transactionHash", "")),
                title=str(row.get("title", "")),
            )
        )
    return out


def cluster_trades(trades: Sequence[EnrichedTrade]) -> List[Dict[str, Any]]:
    buckets: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for trade in trades:
        key = (
            trade.event_slug,
            trade.outcome,
            trade.side,
            trade.timestamp,
        )
        entry = buckets.setdefault(
            key,
            {
                "timestamp": trade.timestamp,
                "event_slug": trade.event_slug,
                "outcome": trade.outcome,
                "side": trade.side,
                "symbol": trade.symbol,
                "duration_min": trade.duration_min,
                "title": trade.title,
                "fills": 0,
                "size": 0.0,
                "usdc_size": 0.0,
                "seconds_to_end": trade.seconds_to_end,
                "timing_bucket": trade.timing_bucket,
            },
        )
        entry["fills"] += 1
        entry["size"] += trade.size
        entry["usdc_size"] += trade.usdc_size
    clustered: List[Dict[str, Any]] = []
    for entry in buckets.values():
        size = float(entry["size"])
        usdc = float(entry["usdc_size"])
        entry["avg_price"] = usdc / size if size > 0 else 0.0
        entry["price_bucket"] = price_bucket(entry["avg_price"])
        entry["timestamp_iso"] = ts_to_iso(int(entry["timestamp"]))
        clustered.append(entry)
    clustered.sort(key=lambda item: (int(item["timestamp"]), item["event_slug"], item["outcome"]))
    return clustered


def summarize_counts(rows: Sequence[EnrichedTrade], weight_key: str = "usdc_size") -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    total_items = float(len(rows))
    total_usdc = sum(getattr(row, weight_key) for row in rows)
    for bucket_name in ["price_bucket", "timing_bucket"]:
        counter = Counter(getattr(row, bucket_name) for row in rows)
        weight_totals: Dict[str, float] = defaultdict(float)
        for row in rows:
            weight_totals[getattr(row, bucket_name)] += getattr(row, weight_key)
        bucket_summary: Dict[str, float] = {}
        for key, count in counter.items():
            bucket_summary[f"{key}__count"] = float(count)
            bucket_summary[f"{key}__count_pct"] = pct(float(count), total_items)
            bucket_summary[f"{key}__usdc"] = weight_totals[key]
            bucket_summary[f"{key}__usdc_pct"] = pct(weight_totals[key], total_usdc)
        out[bucket_name] = bucket_summary
    return out


def summarize_symbol_timing(rows: Sequence[EnrichedTrade]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, int], List[EnrichedTrade]] = defaultdict(list)
    for row in rows:
        groups[(row.symbol, row.duration_min)].append(row)
    summary: List[Dict[str, Any]] = []
    for (symbol, duration), items in sorted(groups.items()):
        seconds = [row.seconds_to_end for row in items]
        usdc = sum(row.usdc_size for row in items)
        after_end = sum(1 for row in items if row.seconds_to_end < 0)
        summary.append(
            {
                "symbol": symbol,
                "duration_min": duration,
                "rows": len(items),
                "usdc_size": usdc,
                "median_seconds_to_end": median(seconds),
                "mean_seconds_to_end": sum(seconds) / len(seconds),
                "share_after_nominal_end_pct": pct(after_end, len(items)),
            }
        )
    return summary


def fetch_binance_window(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    market_symbol = BINANCE_SYMBOLS.get(symbol.lower())
    if not market_symbol:
        return []
    payload = http_get(
        BINANCE_SPOT_KLINES,
        params={
            "symbol": market_symbol,
            "interval": "1s",
            "startTime": start_ts * 1000,
            "endTime": end_ts * 1000,
            "limit": 1000,
        },
        expect_json=True,
    )
    if not isinstance(payload, list):
        return []
    rows: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 5:
            continue
        rows.append(
            {
                "open_time_s": int(int(item[0]) / 1000),
                "open": safe_float(item[1]),
                "high": safe_float(item[2]),
                "low": safe_float(item[3]),
                "close": safe_float(item[4]),
            }
        )
    return rows


def pick_binance_price(klines: Sequence[Dict[str, Any]], ts: int) -> float | None:
    if not klines:
        return None
    prior: Dict[str, Any] | None = None
    for row in klines:
        open_time = int(row.get("open_time_s", 0))
        if open_time == ts:
            price = safe_float(row.get("open")) or safe_float(row.get("close"))
            return price if price > 0 else None
        if open_time > ts:
            break
        prior = row
    if prior is None:
        return None
    price = safe_float(prior.get("close"))
    return price if price > 0 else None


def classify_entry_style(delta_bps: float, outcome: str) -> str:
    clean_outcome = str(outcome or "").strip().lower()
    if abs(delta_bps) <= 5.0:
        return "flat"
    if delta_bps > 0:
        return "continuation" if clean_outcome == "up" else "reversal"
    return "continuation" if clean_outcome == "down" else "reversal"


def enrich_clusters_with_binance(clusters: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    event_cache: Dict[str, List[Dict[str, Any]]] = {}
    enriched: List[Dict[str, Any]] = []
    for cluster in clusters:
        parsed = parse_updown_slug(str(cluster.get("event_slug", "")))
        if not parsed:
            enriched.append(dict(cluster))
            continue
        event_slug = str(cluster["event_slug"])
        if event_slug not in event_cache:
            # Add a 1-second tail so we can price the exact end second when it exists.
            event_cache[event_slug] = fetch_binance_window(
                parsed["symbol"],
                parsed["start_ts"],
                parsed["nominal_end_ts"] + 1,
            )
        klines = event_cache[event_slug]
        start_price = pick_binance_price(klines, parsed["start_ts"])
        buy_price = pick_binance_price(klines, int(cluster["timestamp"]))
        end_price = pick_binance_price(klines, parsed["nominal_end_ts"])
        row = dict(cluster)
        row["window_start_ts"] = parsed["start_ts"]
        row["window_end_ts"] = parsed["nominal_end_ts"]
        row["window_start_iso"] = ts_to_iso(parsed["start_ts"])
        row["window_end_iso"] = ts_to_iso(parsed["nominal_end_ts"])
        row["elapsed_s"] = int(cluster["timestamp"]) - parsed["start_ts"]
        duration_s = parsed["duration_min"] * 60
        row["elapsed_pct"] = (float(row["elapsed_s"]) / float(duration_s)) if duration_s > 0 else 0.0
        row["elapsed_bucket"] = elapsed_bucket(float(row["elapsed_pct"]))
        row["binance_start_price"] = start_price
        row["binance_buy_price"] = buy_price
        row["binance_end_price"] = end_price
        if start_price and buy_price and start_price > 0:
            delta_bps = ((buy_price - start_price) / start_price) * 10000.0
            row["binance_start_to_buy_bps"] = delta_bps
            row["binance_abs_start_to_buy_bps"] = abs(delta_bps)
            row["binance_move_bucket"] = move_bucket(abs(delta_bps))
            row["entry_style"] = classify_entry_style(delta_bps, str(cluster.get("outcome", "")))
        else:
            row["binance_start_to_buy_bps"] = None
            row["binance_abs_start_to_buy_bps"] = None
            row["binance_move_bucket"] = "missing"
            row["entry_style"] = "missing"
        if buy_price and end_price and buy_price > 0:
            row["binance_buy_to_end_bps"] = ((end_price - buy_price) / buy_price) * 10000.0
        else:
            row["binance_buy_to_end_bps"] = None
        enriched.append(row)
    return enriched


def summarize_binance_context(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("symbol", "")), int(row.get("duration_min", 0)))].append(row)
    summary: List[Dict[str, Any]] = []
    for (symbol, duration), items in sorted(groups.items()):
        abs_moves = [
            float(row["binance_abs_start_to_buy_bps"])
            for row in items
            if row.get("binance_abs_start_to_buy_bps") is not None
        ]
        elapsed = [float(row.get("elapsed_pct") or 0.0) for row in items]
        style_counts = Counter(str(row.get("entry_style") or "missing") for row in items)
        summary.append(
            {
                "symbol": symbol,
                "duration_min": duration,
                "clusters": len(items),
                "usdc_size": sum(safe_float(row.get("usdc_size")) for row in items),
                "median_abs_start_to_buy_bps": median(abs_moves) if abs_moves else 0.0,
                "mean_abs_start_to_buy_bps": (sum(abs_moves) / len(abs_moves)) if abs_moves else 0.0,
                "share_abs_move_le_10bps_pct": pct(sum(1 for value in abs_moves if value <= 10.0), len(abs_moves)),
                "share_abs_move_le_20bps_pct": pct(sum(1 for value in abs_moves if value <= 20.0), len(abs_moves)),
                "median_elapsed_pct": median(elapsed) if elapsed else 0.0,
                "share_elapsed_le_75pct_pct": pct(sum(1 for value in elapsed if value <= 0.75), len(elapsed)),
                "share_elapsed_gt_100pct_pct": pct(sum(1 for value in elapsed if value > 1.0), len(elapsed)),
                "flat_count": int(style_counts.get("flat", 0)),
                "reversal_count": int(style_counts.get("reversal", 0)),
                "continuation_count": int(style_counts.get("continuation", 0)),
            }
        )
    return summary


def summarize_participation(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    event_starts: Dict[tuple[str, int], set[int]] = defaultdict(set)
    for row in rows:
        parsed = parse_updown_slug(str(row.get("event_slug", "")))
        if not parsed:
            continue
        event_starts[(parsed["symbol"], parsed["duration_min"])].add(parsed["start_ts"])
    summary: List[Dict[str, Any]] = []
    for (symbol, duration), starts in sorted(event_starts.items()):
        ordered = sorted(starts)
        if not ordered:
            continue
        duration_s = duration * 60
        possible_cycles = 1 + int((ordered[-1] - ordered[0]) / duration_s) if duration_s > 0 else len(ordered)
        summary.append(
            {
                "symbol": symbol,
                "duration_min": duration,
                "traded_event_count": len(ordered),
                "first_cycle_start_iso": ts_to_iso(ordered[0]),
                "last_cycle_start_iso": ts_to_iso(ordered[-1]),
                "possible_cycles_assuming_continuous_series": possible_cycles,
                "participation_rate_pct": pct(len(ordered), possible_cycles),
            }
        )
    return summary


def summarize_dual_side_overlap(trades: Sequence[EnrichedTrade]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for trade in trades:
        if trade.side.upper() != "BUY":
            continue
        event_entry = grouped.setdefault(
            trade.event_slug,
            {
                "event_slug": trade.event_slug,
                "symbol": trade.symbol,
                "duration_min": trade.duration_min,
                "title": trade.title,
                "window_start_ts": None,
                "window_end_ts": None,
                "sides": defaultdict(
                    lambda: {
                        "size": 0.0,
                        "usdc": 0.0,
                        "fills": 0,
                        "first_ts": None,
                        "last_ts": None,
                    }
                ),
            },
        )
        parsed = parse_updown_slug(trade.event_slug)
        if parsed:
            event_entry["window_start_ts"] = parsed["start_ts"]
            event_entry["window_end_ts"] = parsed["nominal_end_ts"]
        side_entry = event_entry["sides"][trade.outcome]
        side_entry["size"] += trade.size
        side_entry["usdc"] += trade.usdc_size
        side_entry["fills"] += 1
        side_entry["first_ts"] = trade.timestamp if side_entry["first_ts"] is None else min(side_entry["first_ts"], trade.timestamp)
        side_entry["last_ts"] = trade.timestamp if side_entry["last_ts"] is None else max(side_entry["last_ts"], trade.timestamp)

    summary: List[Dict[str, Any]] = []
    for event in grouped.values():
        sides = event["sides"]
        if "Up" not in sides or "Down" not in sides:
            continue
        up = sides["Up"]
        down = sides["Down"]
        up_size = safe_float(up["size"])
        down_size = safe_float(down["size"])
        if up_size <= 0 or down_size <= 0:
            continue
        matched_qty = min(up_size, down_size)
        up_avg_price = safe_float(up["usdc"]) / up_size if up_size > 0 else 0.0
        down_avg_price = safe_float(down["usdc"]) / down_size if down_size > 0 else 0.0
        pair_cost_per_share = up_avg_price + down_avg_price
        matched_cost = matched_qty * pair_cost_per_share
        locked_gross_profit = matched_qty - matched_cost
        first_trade_ts = min(int(up["first_ts"]), int(down["first_ts"]))
        last_trade_ts = max(int(up["last_ts"]), int(down["last_ts"]))
        window_end_ts = event["window_end_ts"]
        first_seconds_to_end = float(window_end_ts - first_trade_ts) if window_end_ts is not None else None
        last_seconds_to_end = float(window_end_ts - last_trade_ts) if window_end_ts is not None else None
        summary.append(
            {
                "event_slug": event["event_slug"],
                "symbol": event["symbol"],
                "duration_min": int(event["duration_min"]),
                "title": event["title"],
                "window_start_ts": event["window_start_ts"],
                "window_end_ts": window_end_ts,
                "window_start_iso": ts_to_iso(int(event["window_start_ts"])) if event["window_start_ts"] is not None else "",
                "window_end_iso": ts_to_iso(int(window_end_ts)) if window_end_ts is not None else "",
                "up_size": up_size,
                "down_size": down_size,
                "up_avg_price": up_avg_price,
                "down_avg_price": down_avg_price,
                "up_usdc": safe_float(up["usdc"]),
                "down_usdc": safe_float(down["usdc"]),
                "matched_qty": matched_qty,
                "pair_cost_per_share": pair_cost_per_share,
                "matched_cost": matched_cost,
                "locked_gross_profit": locked_gross_profit,
                "unhedged_up_qty": max(0.0, up_size - matched_qty),
                "unhedged_down_qty": max(0.0, down_size - matched_qty),
                "first_trade_ts": first_trade_ts,
                "last_trade_ts": last_trade_ts,
                "first_trade_iso": ts_to_iso(first_trade_ts),
                "last_trade_iso": ts_to_iso(last_trade_ts),
                "entry_span_s": last_trade_ts - first_trade_ts,
                "first_seconds_to_end": first_seconds_to_end,
                "last_seconds_to_end": last_seconds_to_end,
            }
        )
    summary.sort(
        key=lambda item: (
            safe_float(item.get("locked_gross_profit")),
            safe_float(item.get("matched_qty")),
            str(item.get("event_slug")),
        ),
        reverse=True,
    )
    return summary


def summarize_dual_side_by_symbol(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("symbol", "")), int(row.get("duration_min", 0)))].append(row)
    summary: List[Dict[str, Any]] = []
    for (symbol, duration), items in sorted(groups.items()):
        pair_costs = [safe_float(item.get("pair_cost_per_share")) for item in items]
        last_seconds = [
            safe_float(item.get("last_seconds_to_end"))
            for item in items
            if item.get("last_seconds_to_end") is not None
        ]
        summary.append(
            {
                "symbol": symbol,
                "duration_min": duration,
                "events": len(items),
                "matched_qty": sum(safe_float(item.get("matched_qty")) for item in items),
                "locked_gross_profit": sum(safe_float(item.get("locked_gross_profit")) for item in items),
                "median_pair_cost_per_share": median(pair_costs) if pair_costs else 0.0,
                "median_last_seconds_to_end": median(last_seconds) if last_seconds else 0.0,
                "share_last_trade_after_end_pct": pct(sum(1 for value in last_seconds if value < 0), len(last_seconds)),
                "share_pair_cost_le_0_05_pct": pct(sum(1 for value in pair_costs if value <= 0.05), len(pair_costs)),
            }
        )
    return summary


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_markdown_report(
    username: str,
    profile: Dict[str, Any],
    all_activity: Sequence[Dict[str, Any]],
    filtered_trades: Sequence[EnrichedTrade],
    low_price_trades: Sequence[EnrichedTrade],
    low_price_clusters: Sequence[Dict[str, Any]],
    low_price_clusters_binance: Sequence[Dict[str, Any]],
    participation_summary: Sequence[Dict[str, Any]],
    binance_summary: Sequence[Dict[str, Any]],
    dual_side_overlap: Sequence[Dict[str, Any]],
    dual_side_symbol_summary: Sequence[Dict[str, Any]],
    current_positions: Sequence[Dict[str, Any]],
    closed_positions: Sequence[Dict[str, Any]],
) -> str:
    volume_snapshot = profile.get("volume_snapshot", {})
    user_data = profile.get("user_data", {})
    created_at = user_data.get("createdAt") or ""
    current_positions_crypto = [
        row for row in current_positions if parse_updown_slug(str(row.get("eventSlug", "")))
    ]
    closed_positions_crypto = [
        row for row in closed_positions if parse_updown_slug(str(row.get("eventSlug", "")))
    ]

    total_usdc = sum(trade.usdc_size for trade in filtered_trades if trade.side == "BUY")
    low_price_usdc = sum(trade.usdc_size for trade in low_price_trades)
    after_end_rows = [trade for trade in low_price_trades if trade.seconds_to_end < 0]
    within_10s_rows = [trade for trade in low_price_trades if 0 <= trade.seconds_to_end <= 10]
    before_30s_rows = [trade for trade in low_price_trades if trade.seconds_to_end > 30]
    cluster_after_end = [
        row for row in low_price_clusters_binance if safe_float(row.get("elapsed_pct")) > 1.0
    ]
    flat_rows = [row for row in low_price_clusters_binance if row.get("entry_style") == "flat"]
    small_move_rows = [
        row for row in low_price_clusters_binance
        if row.get("binance_abs_start_to_buy_bps") is not None and float(row["binance_abs_start_to_buy_bps"]) <= 10.0
    ]
    abs_moves = [
        float(row["binance_abs_start_to_buy_bps"])
        for row in low_price_clusters_binance
        if row.get("binance_abs_start_to_buy_bps") is not None
    ]
    median_abs_move = median(abs_moves) if abs_moves else 0.0
    paired_locked_profit = sum(safe_float(row.get("locked_gross_profit")) for row in dual_side_overlap)
    paired_matched_qty = sum(safe_float(row.get("matched_qty")) for row in dual_side_overlap)

    symbol_stats = summarize_symbol_timing(low_price_trades)

    lines: List[str] = []
    lines.append(f"# {username} 低价 Crypto Up/Down 研究报告")
    lines.append("")
    lines.append("## 账户快照")
    lines.append("")
    lines.append(f"- 用户名: `{username}`")
    lines.append(f"- Base address: `{profile.get('base_address', '')}`")
    lines.append(f"- Proxy address: `{profile.get('proxy_address', '')}`")
    lines.append(f"- 建号时间: `{created_at}`")
    lines.append(f"- 页面披露累计成交额: `{fmt_num(safe_float(volume_snapshot.get('amount', 0)), 2)} USDC`")
    lines.append(f"- 页面披露累计 PnL: `{fmt_num(safe_float(volume_snapshot.get('pnl', 0)), 2)} USDC`")
    lines.append(f"- 当前持仓总价值: `{fmt_num(safe_float(profile.get('positions_value', 0)), 4)} USDC`")
    lines.append("")
    lines.append("## 本次抓取范围")
    lines.append("")
    lines.append(f"- 抓到的全部 `TRADE` 活动数: `{len(all_activity)}`")
    lines.append(f"- 过滤后 Crypto Up/Down 交易数: `{len(filtered_trades)}`")
    lines.append(f"- 其中 BUY 数: `{sum(1 for trade in filtered_trades if trade.side == 'BUY')}`")
    lines.append(
        f"- 低价 BUY (`0.001 <= price <= 0.03`) 数: `{len(low_price_trades)}`，"
        f"投入 `{fmt_num(low_price_usdc, 2)} USDC`，占 Crypto Up/Down BUY 的 `{fmt_pct(pct(low_price_usdc, total_usdc))}`"
    )
    lines.append(
        f"- 当前可见 Crypto Up/Down 持仓条数: `{len(current_positions_crypto)}`，"
        f"已关闭 Crypto Up/Down 持仓条数: `{len(closed_positions_crypto)}`"
    )
    lines.append("")
    lines.append("## 关键观察")
    lines.append("")
    lines.append(
        f"- 低价 BUY 中，名义结算时间之后才成交的有 `{len(after_end_rows)}` 笔，"
        f"占 `{fmt_pct(pct(len(after_end_rows), len(low_price_trades)))}`。"
    )
    lines.append(
        f"- 低价 BUY 中，落在 `0-10s` 窗口的有 `{len(within_10s_rows)}` 笔，"
        f"占 `{fmt_pct(pct(len(within_10s_rows), len(low_price_trades)))}`。"
    )
    lines.append(
        f"- 真正还留有 `>30s` 缓冲的低价 BUY 只有 `{len(before_30s_rows)}` 笔，"
        f"占 `{fmt_pct(pct(len(before_30s_rows), len(low_price_trades)))}`。"
    )
    lines.append(
        f"- 低价买点 cluster 里，Binance 从该周期起点到买点的绝对变动中位数是 `{fmt_num(median_abs_move, 2)} bps`；"
        f"`<=10bps` 的平稳窗口共有 `{len(small_move_rows)}` 个，占 `{fmt_pct(pct(len(small_move_rows), len(low_price_clusters_binance)))}`。"
    )
    lines.append(
        f"- cluster 口径下，`{len(flat_rows)}` 个买点属于“外部价格基本走平再出手”，"
        f"`{len(cluster_after_end)}` 个已经超过名义结算时刻。"
    )
    lines.append(
        f"- 低价 BUY 事件里，`{len(dual_side_overlap)}` 个事件同时买了 `Up` 和 `Down`；"
        f"按可对冲重叠数量估算，最近样本可锁定的毛收益约 `{fmt_num(paired_locked_profit, 2)} USDC`，"
        f"对应可配对份额 `{fmt_num(paired_matched_qty, 2)}`。"
    )
    lines.append("")
    lines.append("## 各品种低价买点")
    lines.append("")
    lines.append("| 品种 | 窗口 | 笔数 | USDC | 中位剩余秒数 | 平均剩余秒数 | 名义结算后成交占比 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for row in symbol_stats:
        lines.append(
            f"| `{row['symbol']}` | `{row['duration_min']}m` | `{row['rows']}` | "
            f"`{fmt_num(row['usdc_size'], 2)}` | `{fmt_num(row['median_seconds_to_end'], 2)}` | "
            f"`{fmt_num(row['mean_seconds_to_end'], 2)}` | `{fmt_pct(row['share_after_nominal_end_pct'])}` |"
        )
    lines.append("")
    if binance_summary:
        lines.append("## 外部价格对照")
        lines.append("")
        lines.append("| 品种 | 窗口 | cluster数 | USDC | 起点到买点绝对变动中位数(bps) | `<=10bps` 占比 | `<=20bps` 占比 | `<=75%` 周期内出手占比 | `>100%` 占比 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in binance_summary:
            lines.append(
                f"| `{row['symbol']}` | `{row['duration_min']}m` | `{row['clusters']}` | `{fmt_num(row['usdc_size'], 2)}` | "
                f"`{fmt_num(row['median_abs_start_to_buy_bps'], 2)}` | `{fmt_pct(row['share_abs_move_le_10bps_pct'])}` | "
                f"`{fmt_pct(row['share_abs_move_le_20bps_pct'])}` | `{fmt_pct(row['share_elapsed_le_75pct_pct'])}` | "
                f"`{fmt_pct(row['share_elapsed_gt_100pct_pct'])}` |"
            )
        lines.append("")
    else:
        lines.append("## 外部价格对照")
        lines.append("")
        lines.append("- 本次运行跳过了 Binance 补价，所以这里先不下外部价格结论。")
        lines.append("")

    lines.append("## 双边低价重叠")
    lines.append("")
    lines.append("| 品种 | 窗口 | 事件数 | 可配对份额 | 估算锁定毛收益 | 单份额配对成本中位数 | 最后一次下单距结算中位秒数 | 最后一次超时占比 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in dual_side_symbol_summary:
        lines.append(
            f"| `{row['symbol']}` | `{row['duration_min']}m` | `{row['events']}` | `{fmt_num(row['matched_qty'], 2)}` | "
            f"`{fmt_num(row['locked_gross_profit'], 2)}` | `{fmt_num(row['median_pair_cost_per_share'], 4)}` | "
            f"`{fmt_num(row['median_last_seconds_to_end'], 2)}` | `{fmt_pct(row['share_last_trade_after_end_pct'])}` |"
        )
    lines.append("")
    lines.append("| 事件 | 品种 | 窗口 | Up均价 | Down均价 | 配对总成本/份 | 可配对份额 | 估算锁定毛收益 | 最后一次下单距结算秒数 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in dual_side_overlap[:10]:
        lines.append(
            f"| {row['title']} | `{row['symbol']}` | `{row['duration_min']}m` | "
            f"`{fmt_num(row['up_avg_price'], 4)}` | `{fmt_num(row['down_avg_price'], 4)}` | "
            f"`{fmt_num(row['pair_cost_per_share'], 4)}` | `{fmt_num(row['matched_qty'], 2)}` | "
            f"`{fmt_num(row['locked_gross_profit'], 2)}` | `{fmt_num(safe_float(row.get('last_seconds_to_end')), 2)}` |"
        )
    lines.append("")
    lines.append("## 参与率")
    lines.append("")
    lines.append("| 品种 | 窗口 | 参与事件数 | 连续序列估算总周期 | 参与率 | 首次周期 | 最后周期 |")
    lines.append("| --- | --- | ---: | ---: | ---: | --- | --- |")
    for row in participation_summary:
        lines.append(
            f"| `{row['symbol']}` | `{row['duration_min']}m` | `{row['traded_event_count']}` | "
            f"`{row['possible_cycles_assuming_continuous_series']}` | `{fmt_pct(row['participation_rate_pct'])}` | "
            f"`{row['first_cycle_start_iso']}` | `{row['last_cycle_start_iso']}` |"
        )
    lines.append("")
    lines.append("## 策略判断")
    lines.append("")
    lines.append(
        "1. 只看账户总 PnL，这个账号目前是盈利的；但盈利更像建立在“超晚进场捕捉错误定价”上，"
        "不是单纯因为买了 0.01-0.03 这种低概率票。"
    )
    lines.append(
        "2. 从参与率看，他明显不是每期都买，而是在大量周期里完全不出手，所以这更像“挑错价周期”，"
        "不是“每期固定买彩票”。"
    )
    lines.append(
        "3. 从 Binance 对照看，他不少低价买点发生在“起点到现在价格还没走太远”的周期；"
        "这支持你的假设：如果一个周期已经走得太远，再去抄极低概率单，通常更难赚到。"
    )
    lines.append(
        "4. 你截图里的关键点是对的: 真正异常赚钱的，可能不是“抄单边低概率反转”，而是“同周期双边都买得极低”。"
        "这种更接近错误定价套利，而不是方向判断。"
    )
    lines.append(
        "5. 你的过滤条件必须同时加上“时间窗 + 外部价格偏离度 + 是否存在双边低价重叠”，"
        "否则把 `<10s` 甚至 `<0s` 的单子也学进去，回测会很好看，实盘却大概率吃不到。"
    )
    lines.append("")
    lines.append("## 建议的实盘前门槛")
    lines.append("")
    lines.append("- 只做 `BTC/ETH`，先别扩到全币种。")
    lines.append("- 只看 `0.005-0.03`，跳过极端的 `0.001` 零散噪音。")
    lines.append("- 强制排除 `<=10s` 的买点，最好先排除所有 `<30s` 的买点单独看。")
    lines.append("- 加一层外部价格过滤，例如 `|Binance start->buy move| <= 10~20bps` 再考虑入场。")
    lines.append("- 先做 maker / 挂单优先的模拟，不要默认自己能 taker 吃到他这种价格。")
    lines.append("- 如果 `>30s` 且 `外部偏离不大` 的窗口回测不成立，就说明这不是你当前条件下可复制的策略。")
    lines.append("")
    lines.append("## 低价买点样例")
    lines.append("")
    lines.append("| 时间 | 品种 | 窗口 | 方向 | 价格 | USDC | 距名义结算秒数 | Binance起点到买点(bps) | 标题 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for row in low_price_clusters_binance[-10:]:
        lines.append(
            f"| `{row['timestamp_iso']}` | `{row['symbol']}` | `{row['duration_min']}m` | "
            f"`{row['outcome']}` | `{fmt_num(row['avg_price'], 4)}` | `{fmt_num(row['usdc_size'], 4)}` | "
            f"`{fmt_num(row['seconds_to_end'], 2)}` | "
            f"`{fmt_num(safe_float(row.get('binance_start_to_buy_bps')), 2) if row.get('binance_start_to_buy_bps') is not None else 'NA'}` | "
            f"{row['title']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a Polymarket profile's low-price crypto up/down buys.")
    parser.add_argument("--username", default="little-dead")
    parser.add_argument("--symbols", nargs="*", default=["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"])
    parser.add_argument("--start", help="UTC ISO start time override, e.g. 2026-01-01T00:00:00Z")
    parser.add_argument("--end", help="UTC ISO end time override")
    parser.add_argument("--latest-only", action="store_true", help="Only analyze the latest public activity window accessible via offset pagination.")
    parser.add_argument("--skip-binance", action="store_true", help="Skip Binance enrichment for a faster structural pass.")
    parser.add_argument("--out-dir", default="artifacts/little_dead")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    reports_dir = out_dir / "reports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    profile = extract_profile_snapshot(args.username)
    base_address = str(profile.get("base_address") or "")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", base_address):
        raise SystemExit("failed to resolve base address from profile page")

    user_data = profile.get("user_data", {})
    if args.start:
        start_ts = iso_to_ts(args.start)
    else:
        join_date = str(user_data.get("createdAt") or user_data.get("termsAcceptedAt") or "")
        if not join_date:
            raise SystemExit("could not find a profile createdAt timestamp")
        start_ts = iso_to_ts(join_date)
    end_ts = iso_to_ts(args.end) if args.end else int(time.time())

    if args.latest_only:
        all_activity = fetch_activity_latest(base_address, side=None, limit=100)
        if all_activity:
            start_ts = int(all_activity[0].get("timestamp") or start_ts)
            end_ts = int(all_activity[-1].get("timestamp") or end_ts)
    else:
        all_activity = fetch_activity_by_day(base_address, start_ts, end_ts, side=None)
    current_positions = fetch_positions(base_address, closed=False)
    closed_positions = fetch_positions(base_address, closed=True)

    symbols = {symbol.lower() for symbol in args.symbols}
    filtered_trades = enrich_activity(all_activity, symbols=symbols)
    buy_trades = [trade for trade in filtered_trades if trade.side.upper() == "BUY"]
    low_price_trades = [trade for trade in buy_trades if 0.001 <= trade.price <= 0.03]
    low_price_clusters = cluster_trades(low_price_trades)
    low_price_clusters_binance = enrich_clusters_with_binance(low_price_clusters) if not args.skip_binance else []
    participation_summary = summarize_participation(low_price_clusters)
    binance_summary = summarize_binance_context(low_price_clusters_binance) if low_price_clusters_binance else []
    dual_side_overlap = summarize_dual_side_overlap(low_price_trades)
    dual_side_symbol_summary = summarize_dual_side_by_symbol(dual_side_overlap)
    valid_abs_moves = [
        float(cluster["binance_abs_start_to_buy_bps"])
        for cluster in low_price_clusters_binance
        if cluster.get("binance_abs_start_to_buy_bps") is not None
    ]

    write_json(raw_dir / "profile_snapshot.json", profile)
    write_json(raw_dir / "activity.json", all_activity)
    write_json(raw_dir / "positions_open.json", current_positions)
    write_json(raw_dir / "positions_closed.json", closed_positions)

    write_csv(reports_dir / "filtered_trades.csv", [trade.to_dict() for trade in filtered_trades])
    write_csv(reports_dir / "buy_low_price_trades.csv", [trade.to_dict() for trade in low_price_trades])
    write_csv(reports_dir / "buy_low_price_clusters.csv", low_price_clusters)
    write_csv(reports_dir / "buy_low_price_clusters_binance.csv", low_price_clusters_binance)
    write_csv(reports_dir / "symbol_timing_summary.csv", summarize_symbol_timing(low_price_trades))
    write_csv(reports_dir / "participation_summary.csv", participation_summary)
    write_csv(reports_dir / "binance_context_summary.csv", binance_summary)
    write_csv(reports_dir / "dual_side_overlap.csv", dual_side_overlap)
    write_csv(reports_dir / "dual_side_overlap_summary.csv", dual_side_symbol_summary)

    summary_payload = {
        "username": args.username,
        "base_address": base_address,
        "proxy_address": profile.get("proxy_address"),
        "latest_only": bool(args.latest_only),
        "skip_binance": bool(args.skip_binance),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_iso": ts_to_iso(start_ts),
        "end_iso": ts_to_iso(end_ts),
        "all_activity_rows": len(all_activity),
        "filtered_trades": len(filtered_trades),
        "buy_trades": len(buy_trades),
        "low_price_buy_trades": len(low_price_trades),
        "low_price_buy_usdc": sum(trade.usdc_size for trade in low_price_trades),
        "profile_volume_snapshot": profile.get("volume_snapshot", {}),
        "profile_user_stats": profile.get("user_stats", {}),
        "symbol_timing_summary": summarize_symbol_timing(low_price_trades),
        "participation_summary": participation_summary,
        "binance_context_summary": binance_summary,
        "dual_side_overlap_summary": dual_side_symbol_summary,
        "dual_side_overlap": {
            "events": len(dual_side_overlap),
            "matched_qty": sum(safe_float(row.get("matched_qty")) for row in dual_side_overlap),
            "locked_gross_profit": sum(safe_float(row.get("locked_gross_profit")) for row in dual_side_overlap),
            "median_pair_cost_per_share": median(
                [safe_float(row.get("pair_cost_per_share")) for row in dual_side_overlap]
            ) if dual_side_overlap else 0.0,
        },
        "low_price_clusters": {
            "count": len(low_price_clusters),
            "median_avg_price": median([cluster["avg_price"] for cluster in low_price_clusters]) if low_price_clusters else 0.0,
            "median_seconds_to_end": median([cluster["seconds_to_end"] for cluster in low_price_clusters]) if low_price_clusters else 0.0,
            "share_after_nominal_end_pct": pct(
                sum(1 for cluster in low_price_clusters if cluster["seconds_to_end"] < 0),
                len(low_price_clusters),
            ),
        },
        "low_price_clusters_binance": {
            "count": len(low_price_clusters_binance),
            "median_abs_start_to_buy_bps": median(valid_abs_moves) if valid_abs_moves else 0.0,
            "share_abs_move_le_10bps_pct": pct(
                sum(
                    1
                    for cluster in low_price_clusters_binance
                    if cluster.get("binance_abs_start_to_buy_bps") is not None
                    and float(cluster["binance_abs_start_to_buy_bps"]) <= 10.0
                ),
                len(valid_abs_moves),
            ),
        },
    }
    write_json(reports_dir / "summary.json", summary_payload)

    markdown_report = make_markdown_report(
        username=args.username,
        profile=profile,
        all_activity=all_activity,
        filtered_trades=filtered_trades,
        low_price_trades=low_price_trades,
        low_price_clusters=low_price_clusters,
        low_price_clusters_binance=low_price_clusters_binance,
        participation_summary=participation_summary,
        binance_summary=binance_summary,
        dual_side_overlap=dual_side_overlap,
        dual_side_symbol_summary=dual_side_symbol_summary,
        current_positions=current_positions,
        closed_positions=closed_positions,
    )
    (reports_dir / "report.md").write_text(markdown_report, encoding="utf-8")

    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    print(f"Saved report: {reports_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
