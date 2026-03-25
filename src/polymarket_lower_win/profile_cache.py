from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from polymarket_lower_win.polymarket import (
    UPDOWN_RE,
    fetch_profile_activity_page,
    fetch_profile_positions_page,
    infer_symbol,
    iso_utc,
    resolve_profile,
)


def _clean_username(username: str) -> str:
    return str(username or "").strip().lstrip("@")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _load_paged_rows(page_dir: Path, prefix: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(page_dir.glob(f"{prefix}_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            rows.extend(row for row in payload if isinstance(row, dict))
    return rows


def _copy_imported_pages(source_dir: Path, dest_dir: Path, prefix: str) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    source_files = sorted(
        [path for path in source_dir.glob("*.json") if path.is_file()],
        key=lambda item: int(item.stem) if item.stem.isdigit() else item.stem,
    )
    for index, source in enumerate(source_files):
        target = dest_dir / f"{prefix}_{index:05d}.json"
        shutil.copy2(source, target)
        copied += 1
    return copied


def _summarize_activity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    symbol_counts: Counter[str] = Counter()
    symbol_timeframe_counts: Counter[str] = Counter()
    timestamps: List[int] = []
    buy_count = 0
    total_usdc = 0.0
    for row in rows:
        slug = str(row.get("eventSlug") or row.get("slug") or "")
        match = UPDOWN_RE.match(slug)
        if match:
            symbol = match.group("symbol")
            timeframe = f"{match.group('mins')}m"
        else:
            symbol = infer_symbol(row) or "unknown"
            timeframe = "unknown"
        symbol_counts[symbol] += 1
        symbol_timeframe_counts[f"{symbol}:{timeframe}"] += 1
        timestamp = int(float(row.get("timestamp") or 0))
        if timestamp > 0:
            timestamps.append(timestamp)
        if str(row.get("side") or "").upper() == "BUY":
            buy_count += 1
        total_usdc += float(row.get("usdcSize") or 0.0)
    return {
        "trade_count": len(rows),
        "buy_count": buy_count,
        "gross_usdc_flow": round(total_usdc, 6),
        "symbol_counts": dict(symbol_counts.most_common()),
        "symbol_timeframe_counts": dict(symbol_timeframe_counts.most_common()),
        "time_range": {
            "start_utc": iso_utc(min(timestamps)) if timestamps else "",
            "end_utc": iso_utc(max(timestamps)) if timestamps else "",
        },
    }


def _summarize_low_price(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    low_rows: List[Dict[str, Any]] = []
    for row in rows:
        slug = str(row.get("eventSlug") or "")
        match = UPDOWN_RE.match(slug)
        if match is None:
            continue
        if str(row.get("side") or "").upper() != "BUY":
            continue
        price = float(row.get("price") or 0.0)
        if not (0.001 <= price <= 0.03):
            continue
        mins = int(match.group("mins"))
        start_ts = int(match.group("start"))
        low_rows.append(
            {
                "symbol": match.group("symbol"),
                "timeframe": f"{mins}m",
                "event_slug": slug,
                "outcome": str(row.get("outcome") or ""),
                "price": price,
                "size": float(row.get("size") or 0.0),
                "usdc": float(row.get("usdcSize") or 0.0),
                "timestamp": int(float(row.get("timestamp") or 0)),
                "end_ts": start_ts + mins * 60,
                "title": str(row.get("title") or slug),
            }
        )

    clusters: Dict[tuple[str, str, int], Dict[str, Any]] = {}
    for row in low_rows:
        key = (row["event_slug"], row["outcome"], row["timestamp"])
        entry = clusters.setdefault(
            key,
            {
                "event_slug": row["event_slug"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "timestamp": row["timestamp"],
                "end_ts": row["end_ts"],
                "size": 0.0,
                "usdc": 0.0,
            },
        )
        entry["size"] += row["size"]
        entry["usdc"] += row["usdc"]

    seconds_to_end = [int(entry["end_ts"]) - int(entry["timestamp"]) for entry in clusters.values()]
    by_event: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"size": 0.0, "usdc": 0.0, "first_ts": None, "last_ts": None})
    )
    for row in low_rows:
        side = by_event[row["event_slug"]][row["outcome"]]
        side["size"] += row["size"]
        side["usdc"] += row["usdc"]
        side["first_ts"] = row["timestamp"] if side["first_ts"] is None else min(side["first_ts"], row["timestamp"])
        side["last_ts"] = row["timestamp"] if side["last_ts"] is None else max(side["last_ts"], row["timestamp"])

    paired_examples: List[Dict[str, Any]] = []
    for event_slug, outcomes in by_event.items():
        if "Up" not in outcomes or "Down" not in outcomes:
            continue
        up = outcomes["Up"]
        down = outcomes["Down"]
        if float(up["size"]) <= 0 or float(down["size"]) <= 0:
            continue
        sample = next(row for row in low_rows if row["event_slug"] == event_slug)
        matched_qty = min(float(up["size"]), float(down["size"]))
        up_avg = float(up["usdc"]) / float(up["size"])
        down_avg = float(down["usdc"]) / float(down["size"])
        paired_examples.append(
            {
                "event_slug": event_slug,
                "symbol": sample["symbol"],
                "timeframe": sample["timeframe"],
                "title": sample["title"],
                "matched_qty": round(matched_qty, 6),
                "pair_cost_per_share": round(up_avg + down_avg, 6),
                "locked_gross_profit": round(matched_qty * (1.0 - up_avg - down_avg), 6),
                "first_seconds_to_end": int(sample["end_ts"] - min(int(up["first_ts"]), int(down["first_ts"]))),
                "last_seconds_to_end": int(sample["end_ts"] - max(int(up["last_ts"]), int(down["last_ts"]))),
            }
        )
    paired_examples.sort(key=lambda item: (item["locked_gross_profit"], item["matched_qty"]), reverse=True)

    return {
        "row_count": len(low_rows),
        "cluster_count": len(clusters),
        "median_seconds_to_end": median(seconds_to_end) if seconds_to_end else None,
        "share_after_end_pct": round(
            (100.0 * sum(1 for value in seconds_to_end if value < 0) / len(seconds_to_end)), 4
        )
        if seconds_to_end
        else 0.0,
        "share_0_10s_pct": round(
            (100.0 * sum(1 for value in seconds_to_end if 0 <= value <= 10) / len(seconds_to_end)), 4
        )
        if seconds_to_end
        else 0.0,
        "share_gt_30s_pct": round(
            (100.0 * sum(1 for value in seconds_to_end if value > 30) / len(seconds_to_end)), 4
        )
        if seconds_to_end
        else 0.0,
        "dual_side_event_count": len(paired_examples),
        "dual_side_total_locked_gross_profit": round(
            sum(float(item["locked_gross_profit"]) for item in paired_examples), 6
        ),
        "dual_side_examples": paired_examples[:12],
    }


def _summarize_positions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    symbol_counts: Counter[str] = Counter()
    total_initial = 0.0
    total_current = 0.0
    for row in rows:
        symbol_counts[infer_symbol(row) or "unknown"] += 1
        total_initial += float(row.get("initialValue") or 0.0)
        total_current += float(row.get("currentValue") or 0.0)
    return {
        "position_count": len(rows),
        "symbol_counts": dict(symbol_counts.most_common()),
        "total_initial_value": round(total_initial, 6),
        "total_current_value": round(total_current, 6),
    }


def cache_profile(
    username: str,
    *,
    output_root: str = "data/raw/polymarket_profiles",
    activity_limit: int = 100,
    positions_limit: int = 500,
    max_activity_pages: int = 31,
    max_positions_pages: int = 4,
    import_pages_dir: str | None = None,
    no_network: bool = False,
) -> Dict[str, Any]:
    clean = _clean_username(username)
    cache_dir = Path(output_root) / clean
    pages_dir = cache_dir / "pages"
    positions_dir = cache_dir / "position_pages"
    cache_dir.mkdir(parents=True, exist_ok=True)

    profile_meta: Dict[str, Any] = {"username": clean, "cached_at_utc": iso_utc()}
    if not no_network:
        try:
            profile_meta.update(resolve_profile(clean))
        except Exception as exc:
            profile_meta["profile_fetch_error"] = str(exc)
    _write_json(cache_dir / "profile_meta.json", profile_meta)

    if import_pages_dir:
        imported = _copy_imported_pages(Path(import_pages_dir), pages_dir, "activity")
        profile_meta["imported_activity_pages"] = imported
        _write_json(cache_dir / "profile_meta.json", profile_meta)

    fetch_user = str(profile_meta.get("base_address") or profile_meta.get("proxy_address") or clean)
    if not no_network and not import_pages_dir:
        for page_index in range(max(1, int(max_activity_pages))):
            offset = page_index * int(activity_limit)
            rows = fetch_profile_activity_page(fetch_user, limit=activity_limit, offset=offset)
            target = pages_dir / f"activity_{page_index:05d}.json"
            _write_json(target, rows)
            if len(rows) < int(activity_limit):
                break

    if not no_network:
        for page_index in range(max(1, int(max_positions_pages))):
            offset = page_index * int(positions_limit)
            rows = fetch_profile_positions_page(fetch_user, limit=positions_limit, offset=offset, closed=False)
            target = positions_dir / f"positions_{page_index:05d}.json"
            _write_json(target, rows)
            if len(rows) < int(positions_limit):
                break

    activity_rows = _load_paged_rows(pages_dir, "activity")
    position_rows = _load_paged_rows(positions_dir, "positions")
    _write_jsonl(cache_dir / "activity_trades.jsonl", activity_rows)
    _write_jsonl(cache_dir / "positions.jsonl", position_rows)

    activity_summary = _summarize_activity(activity_rows)
    low_price_summary = _summarize_low_price(activity_rows)
    positions_summary = _summarize_positions(position_rows)
    combined = {
        "username": clean,
        "cached_at_utc": iso_utc(),
        "profile_meta": profile_meta,
        "activity_summary": activity_summary,
        "low_price_summary": low_price_summary,
        "positions_summary": positions_summary,
    }
    _write_json(cache_dir / "activity_summary.json", activity_summary)
    _write_json(cache_dir / "low_price_summary.json", low_price_summary)
    _write_json(cache_dir / "positions_summary.json", positions_summary)
    _write_json(cache_dir / "cache_summary.json", combined)
    return combined
