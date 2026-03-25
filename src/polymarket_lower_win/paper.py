from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from polymarket_lower_win.binance import BinancePeriodSnapshot, determine_winning_outcome, fetch_period_snapshot
from polymarket_lower_win.polymarket import BinaryMarket, fetch_current_markets, iso_utc


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return max(float(low), min(float(high), float(value)))


@dataclass(frozen=True)
class PaperConfig:
    """模拟盘配置。

    这里刻意把参数拆得比较细，因为这套策略本质上不是单一阈值，
    而是“时间窗 + 外部价格 + 错价幅度 + 拆单”的组合。
    """

    run_id: str
    poll_seconds: float = 5.0
    run_minutes: float = 60.0
    bankroll_usd: float = 200.0
    symbols: tuple[str, ...] = ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")
    timeframes: tuple[str, ...] = ("5m", "15m")
    logs_root: str = "Logs/paper_low_win"

    # 单笔目标与拆单参数。
    shares_per_signal: float = 10.0
    child_shares: float = 2.0
    max_shares_per_market: float = 10.0
    max_open_positions: int = 16

    # 总体价格带。是否允许进入，先看这里。
    min_low_price: float = 0.001
    max_low_price: float = 0.03
    skip_dual_side_pair_markets: bool = True
    dual_side_pair_price_cap: float = 0.05

    # 盘前时间窗。
    pre_min_seconds_remaining: int = 30
    pre_max_seconds_remaining: int = 240

    # 盘后时间窗。默认允许少量研究型模拟，但价格要求更严。
    allow_post_close: bool = True
    post_max_seconds_after_end: int = 5
    settlement_grace_seconds: int = 8
    block_post_close_for_source_mismatch: bool = True
    source_mismatch_guard_seconds: int = 90
    source_mismatch_min_edge_points_near_close: float = 0.01

    # 外部价格分层：平、轻度、剧烈。
    flat_move_bps: float = 5.0
    flat_range_bps: float = 15.0
    mild_move_bps: float = 12.0
    mild_range_bps: float = 25.0
    stress_move_bps: float = 20.0
    stress_range_bps: float = 40.0

    # 不同阶段允许的最高买价。
    pre_flat_price_cap: float = 0.03
    pre_mild_price_cap: float = 0.02
    pre_stress_price_cap: float = 0.01
    post_flat_price_cap: float = 0.01
    post_mild_price_cap: float = 0.005

    # 错价评分模型参数。
    fair_base_prob: float = 0.003
    fair_time_weight: float = 0.015
    fair_flat_bonus: float = 0.012
    fair_calm_bonus: float = 0.010
    fair_disagreement_bonus: float = 0.015
    fair_reversal_bonus: float = 0.006
    fair_post_close_penalty: float = 0.015
    min_edge_points: float = 0.003

    # 结算阶段是否继续持有到结算。
    hold_to_settlement: bool = True

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PaperConfig":
        clean = dict(payload)
        symbols = tuple(str(item).lower() for item in clean.get("symbols", cls.symbols))
        timeframes = tuple(str(item) for item in clean.get("timeframes", cls.timeframes))
        run_id = str(clean.get("run_id") or f"paper-low-win-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        return cls(
            run_id=run_id,
            poll_seconds=_safe_float(clean.get("poll_seconds"), cls.poll_seconds),
            run_minutes=_safe_float(clean.get("run_minutes"), cls.run_minutes),
            bankroll_usd=_safe_float(clean.get("bankroll_usd"), cls.bankroll_usd),
            symbols=symbols or cls.symbols,
            timeframes=timeframes or cls.timeframes,
            logs_root=str(clean.get("logs_root") or cls.logs_root),
            shares_per_signal=_safe_float(clean.get("shares_per_signal"), cls.shares_per_signal),
            child_shares=_safe_float(clean.get("child_shares"), cls.child_shares),
            max_shares_per_market=_safe_float(clean.get("max_shares_per_market"), cls.max_shares_per_market),
            max_open_positions=int(clean.get("max_open_positions", cls.max_open_positions)),
            min_low_price=_safe_float(clean.get("min_low_price"), cls.min_low_price),
            max_low_price=_safe_float(clean.get("max_low_price"), cls.max_low_price),
            skip_dual_side_pair_markets=bool(
                clean.get("skip_dual_side_pair_markets", cls.skip_dual_side_pair_markets)
            ),
            dual_side_pair_price_cap=_safe_float(
                clean.get("dual_side_pair_price_cap"),
                cls.dual_side_pair_price_cap,
            ),
            pre_min_seconds_remaining=int(
                clean.get("pre_min_seconds_remaining", cls.pre_min_seconds_remaining)
            ),
            pre_max_seconds_remaining=int(
                clean.get("pre_max_seconds_remaining", cls.pre_max_seconds_remaining)
            ),
            allow_post_close=bool(clean.get("allow_post_close", cls.allow_post_close)),
            post_max_seconds_after_end=int(
                clean.get("post_max_seconds_after_end", cls.post_max_seconds_after_end)
            ),
            settlement_grace_seconds=int(
                clean.get("settlement_grace_seconds", cls.settlement_grace_seconds)
            ),
            block_post_close_for_source_mismatch=bool(
                clean.get(
                    "block_post_close_for_source_mismatch",
                    cls.block_post_close_for_source_mismatch,
                )
            ),
            source_mismatch_guard_seconds=int(
                clean.get("source_mismatch_guard_seconds", cls.source_mismatch_guard_seconds)
            ),
            source_mismatch_min_edge_points_near_close=_safe_float(
                clean.get(
                    "source_mismatch_min_edge_points_near_close",
                    cls.source_mismatch_min_edge_points_near_close,
                ),
                cls.source_mismatch_min_edge_points_near_close,
            ),
            flat_move_bps=_safe_float(clean.get("flat_move_bps"), cls.flat_move_bps),
            flat_range_bps=_safe_float(clean.get("flat_range_bps"), cls.flat_range_bps),
            mild_move_bps=_safe_float(clean.get("mild_move_bps"), cls.mild_move_bps),
            mild_range_bps=_safe_float(clean.get("mild_range_bps"), cls.mild_range_bps),
            stress_move_bps=_safe_float(clean.get("stress_move_bps"), cls.stress_move_bps),
            stress_range_bps=_safe_float(clean.get("stress_range_bps"), cls.stress_range_bps),
            pre_flat_price_cap=_safe_float(clean.get("pre_flat_price_cap"), cls.pre_flat_price_cap),
            pre_mild_price_cap=_safe_float(clean.get("pre_mild_price_cap"), cls.pre_mild_price_cap),
            pre_stress_price_cap=_safe_float(clean.get("pre_stress_price_cap"), cls.pre_stress_price_cap),
            post_flat_price_cap=_safe_float(clean.get("post_flat_price_cap"), cls.post_flat_price_cap),
            post_mild_price_cap=_safe_float(clean.get("post_mild_price_cap"), cls.post_mild_price_cap),
            fair_base_prob=_safe_float(clean.get("fair_base_prob"), cls.fair_base_prob),
            fair_time_weight=_safe_float(clean.get("fair_time_weight"), cls.fair_time_weight),
            fair_flat_bonus=_safe_float(clean.get("fair_flat_bonus"), cls.fair_flat_bonus),
            fair_calm_bonus=_safe_float(clean.get("fair_calm_bonus"), cls.fair_calm_bonus),
            fair_disagreement_bonus=_safe_float(
                clean.get("fair_disagreement_bonus"),
                cls.fair_disagreement_bonus,
            ),
            fair_reversal_bonus=_safe_float(clean.get("fair_reversal_bonus"), cls.fair_reversal_bonus),
            fair_post_close_penalty=_safe_float(
                clean.get("fair_post_close_penalty"),
                cls.fair_post_close_penalty,
            ),
            min_edge_points=_safe_float(clean.get("min_edge_points"), cls.min_edge_points),
            hold_to_settlement=bool(clean.get("hold_to_settlement", cls.hold_to_settlement)),
        )


@dataclass(frozen=True)
class StrategyDecision:
    """一条市场快照对应的一次决策结果。"""

    should_buy: bool
    reason: str
    reason_zh: str
    phase: str
    phase_zh: str
    context_label: str
    pattern: str
    outcome: str
    shares: float
    child_shares: float
    low_price: float
    pair_price: float
    seconds_remaining: int
    external_delta_bps: float | None
    external_range_bps: float | None
    fair_low_prob: float | None
    target_limit_price: float | None
    mispricing_points: float | None


@dataclass
class PaperPosition:
    """模拟盘持仓。

    当前先做最小闭环：建仓后持有到结算，由 Binance 同周期代理价格判断输赢。
    """

    position_id: str
    symbol: str
    timeframe: str
    slug: str
    title: str
    outcome: str
    shares: float
    entry_price: float
    entry_cost_usd: float
    opened_ts: int
    opened_at_utc: str
    start_ts: int
    end_ts: int
    phase: str
    pattern: str
    signal_low_price: float
    signal_pair_price: float
    external_delta_bps: float | None
    external_range_bps: float | None
    fair_low_prob: float | None
    target_limit_price: float | None
    mispricing_points: float | None
    child_index: int
    child_count_hint: int
    status: str = "open"
    closed_ts: int = 0
    closed_at_utc: str = ""
    realized_pnl_usd: float = 0.0
    settlement_outcome: str = ""
    settlement_source: str = ""


def _phase_labels(seconds_remaining: int, cfg: PaperConfig) -> tuple[str, str]:
    """把当前时间映射成交易阶段。"""
    if seconds_remaining > int(cfg.pre_max_seconds_remaining):
        return "too_early", "离结算太远"
    if seconds_remaining >= int(cfg.pre_min_seconds_remaining):
        return "pre_close", "盘前可交易区间"
    if seconds_remaining >= 0:
        return "tail", "尾盘过晚"
    if not bool(cfg.allow_post_close):
        return "post_close_disabled", "盘后买入已禁用"
    if abs(seconds_remaining) <= int(cfg.post_max_seconds_after_end):
        return "post_close", "盘后容忍窗口"
    return "expired", "盘后过久"


def _external_context_label(external: BinancePeriodSnapshot, cfg: PaperConfig) -> str:
    abs_move = abs(float(external.delta_bps))
    range_bps = abs(float(external.range_bps))
    if abs_move <= float(cfg.flat_move_bps) and range_bps <= float(cfg.flat_range_bps):
        return "flat"
    if abs_move <= float(cfg.mild_move_bps) and range_bps <= float(cfg.mild_range_bps):
        return "mild"
    if abs_move <= float(cfg.stress_move_bps) and range_bps <= float(cfg.stress_range_bps):
        return "stress"
    return "wild"


def _trend_side(external: BinancePeriodSnapshot, cfg: PaperConfig) -> str:
    delta = float(external.delta_bps)
    if abs(delta) <= float(cfg.flat_move_bps):
        return "Flat"
    return "Up" if delta > 0 else "Down"


def _pattern_label(market: BinaryMarket, external: BinancePeriodSnapshot, cfg: PaperConfig) -> str:
    trend_side = _trend_side(external, cfg)
    if trend_side == "Flat":
        return "flat_uncertain"
    # 如果外部方向和当前低价方向一致，说明市场可能在错误低估“本该更高”的那一边。
    if market.low_outcome == trend_side:
        return "external_disagreement"
    return "reversal"


def _context_price_cap(phase: str, context_label: str, cfg: PaperConfig) -> float:
    """决定 0.01 还是 0.02 还是 0.03 能买。"""
    if phase == "pre_close":
        if context_label == "flat":
            return float(cfg.pre_flat_price_cap)
        if context_label == "mild":
            return float(cfg.pre_mild_price_cap)
        if context_label == "stress":
            return float(cfg.pre_stress_price_cap)
        return 0.0
    if phase == "post_close":
        if context_label == "flat":
            return float(cfg.post_flat_price_cap)
        if context_label == "mild":
            return float(cfg.post_mild_price_cap)
        return 0.0
    return 0.0


def _estimate_fair_low_prob(
    market: BinaryMarket,
    external: BinancePeriodSnapshot,
    cfg: PaperConfig,
    *,
    phase: str,
    seconds_remaining: int,
) -> float:
    """估算“低价腿”的粗略合理概率。

    这不是严格定价模型，而是研究型启发式：
    1. 时间越早，低价腿的剩余不确定性越高。
    2. 外部价格越平，低价腿越可能被错杀。
    3. 如果外部方向与市场低价方向一致，说明有“市场没跟上外部价格”的嫌疑。
    """
    timeframe_seconds = max(1, int(market.end_ts - market.start_ts))
    remaining_ratio = _clamp(max(0.0, float(seconds_remaining)) / float(timeframe_seconds), 0.0, 1.0)
    flatness = _clamp(1.0 - abs(float(external.delta_bps)) / max(1.0, float(cfg.mild_move_bps)), 0.0, 1.0)
    calmness = _clamp(1.0 - float(external.range_bps) / max(1.0, float(cfg.mild_range_bps)), 0.0, 1.0)
    pattern = _pattern_label(market, external, cfg)
    pattern_bonus = 0.0
    if pattern == "external_disagreement":
        pattern_bonus = float(cfg.fair_disagreement_bonus)
    elif pattern == "reversal":
        pattern_bonus = float(cfg.fair_reversal_bonus)
    else:
        pattern_bonus = float(cfg.fair_flat_bonus) * 0.5
    fair_prob = (
        float(cfg.fair_base_prob)
        + float(cfg.fair_time_weight) * remaining_ratio
        + float(cfg.fair_flat_bonus) * flatness
        + float(cfg.fair_calm_bonus) * calmness
        + pattern_bonus
    )
    if phase == "post_close":
        fair_prob -= float(cfg.fair_post_close_penalty)
    return _clamp(fair_prob, 0.0, 0.50)


def _child_shares(cfg: PaperConfig, current_shares: float) -> tuple[float, int]:
    """拆单规则。

    例如总仓位上限 10 份、单次子单 2 份，那么信号持续就会逐步补到 10 份。
    """
    remaining_capacity = max(0.0, float(cfg.max_shares_per_market) - float(current_shares))
    shares = min(float(cfg.child_shares), float(cfg.shares_per_signal), remaining_capacity)
    total_hint = max(1, int(round(float(cfg.shares_per_signal) / max(1e-9, float(cfg.child_shares)))))
    return shares, total_hint


def evaluate_market(
    market: BinaryMarket,
    external: Optional[BinancePeriodSnapshot],
    cfg: PaperConfig,
    *,
    current_shares: float,
    now_ts: int,
    open_position_count: int,
) -> StrategyDecision:
    """决定当前这一拍是否应该买。"""
    seconds_remaining = int(market.end_ts - now_ts)
    phase, phase_zh = _phase_labels(seconds_remaining, cfg)
    if open_position_count >= int(cfg.max_open_positions):
        return StrategyDecision(False, "max_open_positions", "持仓数超上限", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if current_shares >= float(cfg.max_shares_per_market):
        return StrategyDecision(False, "max_shares_reached", "该市场仓位已满", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if market.low_price < float(cfg.min_low_price) or market.low_price > float(cfg.max_low_price):
        return StrategyDecision(False, "price_out_of_band", "低价不在研究区间", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if bool(cfg.skip_dual_side_pair_markets) and market.pair_price <= float(cfg.dual_side_pair_price_cap):
        return StrategyDecision(False, "dual_side_pair_market", "疑似双边低价错价盘", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if phase in {"too_early", "tail", "post_close_disabled", "expired"}:
        zh = {
            "too_early": "离结算太远，先不进",
            "tail": "离结算太近，单边反转太难",
            "post_close_disabled": "盘后策略被禁用",
            "expired": "已经超过盘后容忍窗口",
        }[phase]
        return StrategyDecision(False, phase, zh, phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if phase == "post_close" and bool(cfg.block_post_close_for_source_mismatch):
        return StrategyDecision(False, "post_close_blocked_for_source_mismatch", "盘后几秒容易被 Chainlink 与 Binance 差异误导，默认禁做", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)
    if external is None:
        return StrategyDecision(False, "missing_external_price", "缺少 Binance 外部价格", phase, phase_zh, "none", "none", market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, None, None, None, None, None)

    context_label = _external_context_label(external, cfg)
    if context_label == "wild":
        return StrategyDecision(False, "external_too_wild", "外部价格波动太大，低价单大概率是假便宜", phase, phase_zh, context_label, _pattern_label(market, external, cfg), market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), None, None, None)

    fair_low_prob = _estimate_fair_low_prob(
        market,
        external,
        cfg,
        phase=phase,
        seconds_remaining=seconds_remaining,
    )
    target_limit_price = min(
        _context_price_cap(phase, context_label, cfg),
        max(0.0, fair_low_prob - float(cfg.min_edge_points)),
    )
    mispricing_points = float(fair_low_prob) - float(market.low_price)
    child_shares, _ = _child_shares(cfg, current_shares)
    pattern = _pattern_label(market, external, cfg)

    if target_limit_price <= 0:
        return StrategyDecision(False, "no_price_cap", "当前阶段和波动条件下不给买价", phase, phase_zh, context_label, pattern, market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), fair_low_prob, target_limit_price, mispricing_points)
    if mispricing_points < float(cfg.min_edge_points):
        return StrategyDecision(False, "insufficient_edge", "错价幅度不够", phase, phase_zh, context_label, pattern, market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), fair_low_prob, target_limit_price, mispricing_points)
    if (
        seconds_remaining <= int(cfg.source_mismatch_guard_seconds)
        and mispricing_points < float(cfg.source_mismatch_min_edge_points_near_close)
    ):
        return StrategyDecision(False, "edge_too_small_for_source_mismatch", "离结算太近，当前错价不足以覆盖 Chainlink 与 Binance 源差异", phase, phase_zh, context_label, pattern, market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), fair_low_prob, target_limit_price, mispricing_points)
    if market.low_price > target_limit_price:
        return StrategyDecision(False, "market_price_above_limit", "市场低价高于目标买价", phase, phase_zh, context_label, pattern, market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), fair_low_prob, target_limit_price, mispricing_points)
    if child_shares <= 0:
        return StrategyDecision(False, "zero_child_shares", "拆单后没有可买份额", phase, phase_zh, context_label, pattern, market.low_outcome, 0.0, 0.0, market.low_price, market.pair_price, seconds_remaining, float(external.delta_bps), float(external.range_bps), fair_low_prob, target_limit_price, mispricing_points)

    return StrategyDecision(
        True,
        "buy_low_prob_single_side",
        "满足低价单边条件，允许买入一笔子单",
        phase,
        phase_zh,
        context_label,
        pattern,
        market.low_outcome,
        float(child_shares),
        float(child_shares),
        float(market.low_price),
        float(market.pair_price),
        int(seconds_remaining),
        float(external.delta_bps),
        float(external.range_bps),
        float(fair_low_prob),
        float(target_limit_price),
        float(mispricing_points),
    )


class PaperSimulator:
    """低概率单边模拟盘。

    这里先不追求“像交易所撮合一样细”，重点是：
    1. 决策透明
    2. 日志完整
    3. 后续你可以直接拿 Logs 里的文件继续做研究
    """

    def __init__(self, cfg: PaperConfig, *, base_dir: str | Path | None = None) -> None:
        self.cfg = cfg
        root = Path(base_dir or cfg.logs_root)
        self.run_dir = root / cfg.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_path = self.run_dir / "snapshots.jsonl"
        self.signals_path = self.run_dir / "signals.jsonl"
        self.trades_path = self.run_dir / "trades.jsonl"
        self.state_path = self.run_dir / "state.json"
        self.summary_path = self.run_dir / "summary_latest.json"
        self.cash_usd = float(cfg.bankroll_usd)
        self.realized_pnl_usd = 0.0
        self.positions: List[PaperPosition] = []
        self.cycle_count = 0
        self.last_error: str = ""
        self.snapshots_path.touch(exist_ok=True)
        self.signals_path.touch(exist_ok=True)
        self.trades_path.touch(exist_ok=True)

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_state(self) -> None:
        state = {
            "updated_at_utc": iso_utc(),
            "cash_usd": round(self.cash_usd, 6),
            "realized_pnl_usd": round(self.realized_pnl_usd, 6),
            "cycle_count": self.cycle_count,
            "open_positions": [asdict(item) for item in self.positions if item.status == "open"],
            "closed_positions": [asdict(item) for item in self.positions if item.status != "open"],
            "last_error": self.last_error,
        }
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_summary(self, snapshot_rows: List[Dict[str, Any]]) -> None:
        open_positions = [item for item in self.positions if item.status == "open"]
        closed_positions = [item for item in self.positions if item.status != "open"]
        market_price_map = {row["slug"]: row for row in snapshot_rows}
        unrealized_value = 0.0
        for position in open_positions:
            row = market_price_map.get(position.slug)
            if row is None:
                current_price = position.entry_price
            else:
                current_price = float(row["up_price"]) if position.outcome == "Up" else float(row["down_price"])
            unrealized_value += current_price * position.shares
        payload = {
            "updated_at_utc": iso_utc(),
            "run_id": self.cfg.run_id,
            "cash_usd": round(self.cash_usd, 6),
            "realized_pnl_usd": round(self.realized_pnl_usd, 6),
            "equity_usd_est": round(self.cash_usd + unrealized_value, 6),
            "open_position_count": len(open_positions),
            "closed_position_count": len(closed_positions),
            "positions": {
                "open": [asdict(item) for item in open_positions],
                "closed_tail": [asdict(item) for item in closed_positions[-10:]],
            },
            "latest_markets": snapshot_rows,
            "last_error": self.last_error,
        }
        self.summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _current_shares_by_slug(self, slug: str) -> float:
        return sum(item.shares for item in self.positions if item.status == "open" and item.slug == slug)

    def _open_position_count(self) -> int:
        return sum(1 for item in self.positions if item.status == "open")

    def _signal_child_index(self, slug: str) -> int:
        return 1 + sum(1 for item in self.positions if item.slug == slug)

    def _execute_buy(self, market: BinaryMarket, decision: StrategyDecision, now_ts: int) -> Optional[PaperPosition]:
        cost = float(decision.shares) * float(decision.low_price)
        if cost > self.cash_usd:
            return None
        child_count_hint = max(
            1,
            int(round(float(self.cfg.shares_per_signal) / max(1e-9, float(self.cfg.child_shares)))),
        )
        position = PaperPosition(
            position_id=str(uuid.uuid4()),
            symbol=market.symbol,
            timeframe=market.timeframe,
            slug=market.slug,
            title=market.title,
            outcome=decision.outcome,
            shares=float(decision.shares),
            entry_price=float(decision.low_price),
            entry_cost_usd=round(cost, 6),
            opened_ts=now_ts,
            opened_at_utc=iso_utc(now_ts),
            start_ts=market.start_ts,
            end_ts=market.end_ts,
            phase=decision.phase,
            pattern=decision.pattern,
            signal_low_price=float(decision.low_price),
            signal_pair_price=float(decision.pair_price),
            external_delta_bps=decision.external_delta_bps,
            external_range_bps=decision.external_range_bps,
            fair_low_prob=decision.fair_low_prob,
            target_limit_price=decision.target_limit_price,
            mispricing_points=decision.mispricing_points,
            child_index=self._signal_child_index(market.slug),
            child_count_hint=child_count_hint,
        )
        self.cash_usd -= cost
        self.positions.append(position)
        self._append_jsonl(
            self.trades_path,
            {
                "ts_utc": iso_utc(now_ts),
                "event": "BUY",
                "position": asdict(position),
            },
        )
        return position

    def _settle_positions(self, now_ts: int) -> None:
        if not bool(self.cfg.hold_to_settlement):
            return
        for position in self.positions:
            if position.status != "open":
                continue
            if now_ts < int(position.end_ts) + int(self.cfg.settlement_grace_seconds):
                continue
            winner = determine_winning_outcome(position.symbol, position.timeframe, position.start_ts)
            if winner is None:
                continue
            payout = position.shares if winner == position.outcome else 0.0
            pnl = payout - position.entry_cost_usd
            position.status = "settled"
            position.closed_ts = now_ts
            position.closed_at_utc = iso_utc(now_ts)
            position.realized_pnl_usd = round(pnl, 6)
            position.settlement_outcome = winner
            position.settlement_source = "binance_proxy"
            self.cash_usd += payout
            self.realized_pnl_usd += pnl
            self._append_jsonl(
                self.trades_path,
                {
                    "ts_utc": iso_utc(now_ts),
                    "event": "SETTLE",
                    "position_id": position.position_id,
                    "slug": position.slug,
                    "symbol": position.symbol,
                    "timeframe": position.timeframe,
                    "winner": winner,
                    "held_outcome": position.outcome,
                    "shares": position.shares,
                    "entry_cost_usd": position.entry_cost_usd,
                    "payout_usd": round(payout, 6),
                    "realized_pnl_usd": round(pnl, 6),
                    "settlement_source": "binance_proxy",
                },
            )

    def run_cycle(self, *, now_ts: int | None = None) -> Dict[str, Any]:
        cycle_ts = int(now_ts or time.time())
        self.last_error = ""
        markets = fetch_current_markets(self.cfg.symbols, self.cfg.timeframes, now_ts=cycle_ts)
        snapshot_rows: List[Dict[str, Any]] = []

        for market in markets:
            external = fetch_period_snapshot(market.symbol, market.timeframe, market.start_ts)
            decision = evaluate_market(
                market,
                external,
                self.cfg,
                current_shares=self._current_shares_by_slug(market.slug),
                now_ts=cycle_ts,
                open_position_count=self._open_position_count(),
            )
            current_outcome_price = market.price_for_outcome(decision.outcome)
            row = {
                "ts_utc": iso_utc(cycle_ts),
                "symbol": market.symbol,
                "timeframe": market.timeframe,
                "slug": market.slug,
                "title": market.title,
                "start_ts": market.start_ts,
                "end_ts": market.end_ts,
                "seconds_remaining": int(market.end_ts - cycle_ts),
                "up_price": round(market.up_price, 6),
                "down_price": round(market.down_price, 6),
                "pair_price": round(market.pair_price, 6),
                "low_outcome": market.low_outcome,
                "low_price": round(market.low_price, 6),
                "current_shares": round(self._current_shares_by_slug(market.slug), 6),
                "external_delta_bps": None if external is None else round(external.delta_bps, 6),
                "external_range_bps": None if external is None else round(external.range_bps, 6),
                "current_outcome_price": round(float(current_outcome_price), 6),
                "decision": asdict(decision),
            }
            snapshot_rows.append(row)

            if decision.should_buy:
                self._append_jsonl(
                    self.signals_path,
                    {
                        "ts_utc": iso_utc(cycle_ts),
                        "symbol": market.symbol,
                        "timeframe": market.timeframe,
                        "slug": market.slug,
                        "title": market.title,
                        "decision": asdict(decision),
                    },
                )
                position = self._execute_buy(market, decision, cycle_ts)
                if position is None:
                    self._append_jsonl(
                        self.signals_path,
                        {
                            "ts_utc": iso_utc(cycle_ts),
                            "symbol": market.symbol,
                            "timeframe": market.timeframe,
                            "slug": market.slug,
                            "title": market.title,
                            "decision": asdict(decision),
                            "event": "REJECT_NO_CASH",
                        },
                    )

        self._settle_positions(cycle_ts)
        self._append_jsonl(
            self.snapshots_path,
            {
                "ts_utc": iso_utc(cycle_ts),
                "cash_usd": round(self.cash_usd, 6),
                "realized_pnl_usd": round(self.realized_pnl_usd, 6),
                "markets": snapshot_rows,
            },
        )
        self.cycle_count += 1
        self._write_state()
        self._write_summary(snapshot_rows)
        return {
            "ts_utc": iso_utc(cycle_ts),
            "cycle_count": self.cycle_count,
            "cash_usd": round(self.cash_usd, 6),
            "realized_pnl_usd": round(self.realized_pnl_usd, 6),
            "market_count": len(snapshot_rows),
            "open_position_count": self._open_position_count(),
            "last_error": self.last_error,
        }

    def run(self, *, once: bool = False) -> Dict[str, Any]:
        started = time.time()
        last_payload: Dict[str, Any] = {}
        while True:
            try:
                last_payload = self.run_cycle()
            except Exception as exc:  # pragma: no cover - 依赖外部网络环境
                self.last_error = str(exc)
                last_payload = {
                    "ts_utc": iso_utc(),
                    "cycle_count": self.cycle_count,
                    "cash_usd": round(self.cash_usd, 6),
                    "realized_pnl_usd": round(self.realized_pnl_usd, 6),
                    "market_count": 0,
                    "open_position_count": self._open_position_count(),
                    "error": self.last_error,
                }
                self._append_jsonl(
                    self.snapshots_path,
                    {
                        "ts_utc": iso_utc(),
                        "event": "ERROR",
                        "error": self.last_error,
                    },
                )
                self._write_state()
                self._write_summary([])
            if once:
                break
            if self.cfg.run_minutes > 0 and (time.time() - started) >= (self.cfg.run_minutes * 60.0):
                break
            time.sleep(max(0.2, float(self.cfg.poll_seconds)))
        return last_payload
