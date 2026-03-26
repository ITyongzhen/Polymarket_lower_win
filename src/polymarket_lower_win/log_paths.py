from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _local_dt(now_ts: int | float | None = None) -> datetime:
    if now_ts is None:
        return datetime.now().astimezone()
    return datetime.fromtimestamp(float(now_ts)).astimezone()


def local_day_key(now_ts: int | float | None = None) -> str:
    return _local_dt(now_ts).date().isoformat()


def local_run_stamp(now_ts: int | float | None = None) -> str:
    return _local_dt(now_ts).strftime("%Y%m%d%H%M%S")


def midnight_run_stamp(now_ts: int | float | None = None) -> str:
    return _local_dt(now_ts).strftime("%Y%m%d000000")


def resolve_run_id(raw: str | None, *, now_ts: int | float | None = None) -> str:
    clean = str(raw or "").strip()
    if clean and clean.lower() != "auto":
        return clean
    return local_run_stamp(now_ts)


def normalize_logs_root(raw: str | Path | None, *, default_subdir: str) -> Path:
    clean = str(raw or "").strip()
    path = Path(clean) if clean else Path("logs") / default_subdir

    normalized_parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        fixed = "logs" if part == "Logs" else part
        if fixed == "logs" and normalized_parts and normalized_parts[-1] == "logs":
            continue
        normalized_parts.append(fixed)

    normalized = Path(*normalized_parts) if normalized_parts else Path("logs") / default_subdir
    if not normalized.is_absolute():
        parts = list(normalized.parts)
        if not parts or parts[0] != "logs":
            normalized = Path("logs") / normalized
        if normalized == Path("logs"):
            normalized = normalized / default_subdir
    return normalized
