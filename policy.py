"""Gate + sizing. Every threshold lives here, in code."""
from __future__ import annotations
import math
from dataclasses import dataclass
from .jev_schema import JevDecision


@dataclass(frozen=True)
class PolicyParams:
    min_quality: int = 2
    min_conf: float = 0.80            # strictly greater than, per spec
    escalate_conf: float = 0.60       # spec text read "8.60": I assume 0.60
    kelly_cap: float = 0.25           # quarter Kelly
    risk_cap: float = 0.02            # max equity risked per trade. ASSUMPTION
    p_haircut: float = 0.05           # shrink Jev's p until calibration is proven. ESTIMATE
    reward_risk: float = 2.0          # ASSUMPTION: backtest it
    stop_vol_mult: float = 3.0        # stop = mult * mid * rv * sqrt(horizon). ASSUMPTION
    horizon_ticks: int = 60
    min_stop_usd: float = 2.0
    contract_oz: float = 100.0        # oz per 1.0 lot. VERIFY WITH YOUR BROKER
    min_lot: float = 0.01
    lot_step: float = 0.01


def needs_escalation(d: JevDecision, p: PolicyParams) -> bool:
    return d.direction_conf < p.escalate_conf or d.regime == "crisis"


def gate(d: JevDecision, p: PolicyParams) -> tuple[bool, str]:
    if d.direction == "neutral":
        return False, "neutral"
    if d.regime == "crisis":
        return False, "crisis_regime"
    if d.toxic_flow:
        return False, "toxic_flow"
    if d.setup_quality < p.min_quality:
        return False, "low_quality"
    if not d.direction_conf > p.min_conf:
        return False, "low_confidence"
    if d.risk_state != "safe":
        return False, f"risk_state_{d.risk_state}"
    return True, "pass"


def kelly_fraction(prob: float, reward_risk: float) -> float:
    """f* = (p*b - (1-p)) / b, floored at 0."""
    if reward_risk <= 0:
        return 0.0
    return max(0.0, (prob * reward_risk - (1 - prob)) / reward_risk)


def stop_distance_usd(mid: float, rv_bps: float, p: PolicyParams) -> float:
    est = p.stop_vol_mult * mid * (rv_bps / 1e4) * math.sqrt(p.horizon_ticks)
    return max(p.min_stop_usd, est)


def size_lots(equity: float, prob: float, stop_usd: float, p: PolicyParams) -> float:
    """Quarter-Kelly risk fraction, hard-capped; floors to lot step. Returns 0.0 when below
    the broker minimum lot (never rounds UP into more risk than the model allows)."""
    f = min(p.kelly_cap * kelly_fraction(prob - p.p_haircut, p.reward_risk), p.risk_cap)
    if f <= 0 or stop_usd <= 0 or equity <= 0:
        return 0.0
    lots = (equity * f) / (stop_usd * p.contract_oz)
    lots = math.floor(lots / p.lot_step + 1e-9) * p.lot_step
    return round(lots, 2) if lots >= p.min_lot else 0.0
