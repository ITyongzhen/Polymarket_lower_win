from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List


def load_env_file(path: str | Path) -> Dict[str, str]:
    """读取 .env 文件。

    这里只做最基础的 KEY=VALUE 解析，够当前项目使用。
    """
    env: Dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env
    for raw in file_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_value = value.strip().strip('"').strip("'")
        if clean_key:
            env[clean_key] = clean_value
    return env


def get_str(env: Dict[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return default if value is None or value == "" else str(value)


def get_int(env: Dict[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or value == "":
        return int(default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def get_float(env: Dict[str, str], key: str, default: float) -> float:
    value = env.get(key)
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def get_bool(env: Dict[str, str], key: str, default: bool) -> bool:
    value = env.get(key)
    if value is None or value == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_list(env: Dict[str, str], key: str, default: Iterable[str]) -> List[str]:
    value = env.get(key)
    if value is None or value.strip() == "":
        return [str(item) for item in default]
    parts = [item.strip() for item in value.split(",")]
    return [item for item in parts if item]
