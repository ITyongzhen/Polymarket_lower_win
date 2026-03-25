from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Iterator, Optional
from urllib.parse import urlencode

from websocket import WebSocketTimeoutException, create_connection

from polymarket_lower_win.polymarket import iso_utc


# 这些 feedId 直接来自 Chainlink 官方公开页面 data.chain.link 的对应 stream 页面。
DEFAULT_CHAINLINK_FEED_IDS: Dict[str, str] = {
    "btc": "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8",
    "eth": "0x000362205e10b3a147d02792eccee483dca6c7b44ecce7012cb8c6e0b68b3ae9",
    "sol": "0x0003b778d3f6b2ac4991302b89cb313f99a42467d6c9c5f96f57c29c0d2bc24f",
    "xrp": "0x0003c16c6aed42294f5cb4741f6e59ba2d728f0eae2eb9e6d3f555808c59fc45",
    "doge": "0x000356ca64d3b32135e17dc0dc721a645bf50d0303be8ceb2cdca0a50bab8fdc",
    "bnb": "0x000335fd3f3ffa06cfd9297b97367f77145d7a5f132e84c736cc471dd98621fe",
    "hype": "0x0003d34539af562867c3cb309b59efccf40e74b404fb415eeb7699d61322aed9",
}


class ChainlinkStreamsError(RuntimeError):
    """Chainlink Data Streams 采集相关错误。"""


@dataclass(frozen=True)
class ChainlinkStreamsConfig:
    """Chainlink Data Streams WebSocket 采集配置。"""

    api_key: str
    api_secret: str
    ws_endpoint: str = "wss://ws.dataengine.chain.link"
    symbols: tuple[str, ...] = ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")
    recv_timeout_seconds: float = 10.0
    ping_interval_seconds: float = 5.0
    reconnect_seconds: float = 2.0
    max_messages: int = 0


def parse_feed_id_overrides(raw: str) -> Dict[str, str]:
    """解析 env 里的 `btc=0x...,eth=0x...` 覆盖配置。"""

    mapping: Dict[str, str] = {}
    clean = str(raw or "").strip()
    if not clean:
        return mapping
    for item in clean.split(","):
        piece = item.strip()
        if not piece or "=" not in piece:
            continue
        symbol, feed_id = piece.split("=", 1)
        clean_symbol = symbol.strip().lower()
        clean_feed_id = feed_id.strip()
        if clean_symbol and clean_feed_id:
            mapping[clean_symbol] = clean_feed_id
    return mapping


def resolve_feed_ids(symbols: Iterable[str], overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """把币种列表映射到 feedId。

    如果 env 里提供了覆盖值，优先使用覆盖值；否则回退到默认公开 feedId。
    """

    mapping: Dict[str, str] = {}
    custom = {str(k).lower(): str(v) for k, v in (overrides or {}).items()}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).lower().strip()
        if not symbol:
            continue
        feed_id = custom.get(symbol) or DEFAULT_CHAINLINK_FEED_IDS.get(symbol)
        if not feed_id:
            raise ChainlinkStreamsError(f"缺少币种 {symbol} 的 Chainlink feedId")
        mapping[symbol] = feed_id
    if not mapping:
        raise ChainlinkStreamsError("没有可订阅的 Chainlink feedId")
    return mapping


def build_ws_path(feed_ids: Iterable[str]) -> str:
    """构造官方文档要求的 WebSocket path。"""

    query = urlencode({"feedIDs": ",".join(feed_ids)})
    return f"/api/v1/ws?{query}"


def build_ws_auth_headers(
    api_key: str,
    api_secret: str,
    path_with_query: str,
    *,
    timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    """按 Chainlink 官方文档生成 WebSocket 鉴权头。

    文档要求签名字符串格式：
    `GET <path> <empty_body_sha256> <api_key> <timestamp_ms>`
    """

    if not api_key or not api_secret:
        raise ChainlinkStreamsError("Chainlink API key / secret 不能为空")

    ts_ms = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    empty_body_hash = hashlib.sha256(b"").hexdigest()
    string_to_sign = f"GET {path_with_query} {empty_body_hash} {api_key} {ts_ms}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Authorization": api_key,
        "X-Authorization-Timestamp": str(ts_ms),
        "X-Authorization-Signature-SHA256": signature,
    }


def build_ws_url(ws_endpoint: str, path_with_query: str) -> str:
    base = str(ws_endpoint).rstrip("/")
    if base.endswith("/api/v1/ws"):
        return f"{base}?{path_with_query.split('?', 1)[1]}"
    return f"{base}{path_with_query}"


def iter_chainlink_reports(
    cfg: ChainlinkStreamsConfig,
    *,
    feed_map: Dict[str, str],
    on_status: Optional[Callable[[str], None]] = None,
) -> Iterator[Dict[str, object]]:
    """持续监听 Chainlink WebSocket，并逐条产出报告。

    这里先只做“原始报告落盘”层，不在这一层尝试解码 fullReport。
    原因是 fullReport 本质是给链上校验用的完整二进制 blob，
    先把原始数据稳定留存下来，再做更稳妥的二次解析。
    """

    if not cfg.api_key or not cfg.api_secret:
        raise ChainlinkStreamsError(
            "缺少 Chainlink API 凭证。请在 .env 里填写 PM_CHAINLINK_API_KEY / PM_CHAINLINK_API_SECRET"
        )

    feed_ids = list(feed_map.values())
    feed_to_symbol = {feed_id: symbol for symbol, feed_id in feed_map.items()}
    path_with_query = build_ws_path(feed_ids)
    ws_url = build_ws_url(cfg.ws_endpoint, path_with_query)
    message_count = 0

    while True:
        headers = build_ws_auth_headers(cfg.api_key, cfg.api_secret, path_with_query)
        header_lines = [f"{key}: {value}" for key, value in headers.items()]
        if on_status:
            on_status(f"connecting {ws_url}")
        ws = create_connection(ws_url, header=header_lines, timeout=float(cfg.recv_timeout_seconds))
        last_ping_at = time.monotonic()

        try:
            while True:
                try:
                    raw_message = ws.recv()
                except WebSocketTimeoutException:
                    now = time.monotonic()
                    if now - last_ping_at >= float(cfg.ping_interval_seconds):
                        ws.ping()
                        last_ping_at = now
                    continue

                if raw_message is None:
                    raise ChainlinkStreamsError("WebSocket 收到空消息")

                received_ts_ms = int(time.time() * 1000)
                payload = json.loads(raw_message)
                report = payload.get("report") or {}
                feed_id = str(report.get("feedID") or report.get("feedId") or "")
                symbol = feed_to_symbol.get(feed_id, "unknown")
                message_count += 1

                yield {
                    "received_ts_ms": received_ts_ms,
                    "received_at_utc": iso_utc(int(received_ts_ms / 1000)),
                    "symbol": symbol,
                    "feed_id": feed_id,
                    "report": report,
                    "message_index": message_count,
                }

                if cfg.max_messages > 0 and message_count >= int(cfg.max_messages):
                    return
        finally:
            try:
                ws.close()
            except Exception:
                pass

        if cfg.reconnect_seconds <= 0:
            return
        if on_status:
            on_status(f"reconnecting in {cfg.reconnect_seconds:.1f}s")
        time.sleep(float(cfg.reconnect_seconds))
