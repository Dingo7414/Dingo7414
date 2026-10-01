"""Hard deterministic risk layer. The model can never override it.
Kill switch is a latch: once tripped only a human can reset; while tripped, reduce-only."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_drawdown: float = 0.15        # from spec
    max_daily_loss: float = 0.05      # fraction of day-start equity. ASSUMPTION: tune it
    max_position_lots: float = 0.05   # ASSUMPTION: set for your account
    max_spread: float = 1.00          # USD; blocks entries in wide-spread conditions
    max_data_age_s: float = 5.0


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = "ok"


class RiskGuard:
    def __init__(self, limits: RiskLimits, equity: float, day: str):
        self.limits = limits
        self.equity = self.peak = self.day_start = equity
        self.day, self.position = day, 0.0
        self.tripped, self.trip_reason = False, ""

    def mark(self, equity: float, day: str, position: float) -> None:
        if day != self.day:
            self.day, self.day_start = day, equity
        self.equity, self.position = equity, position
        self.peak = max(self.peak, equity)
        if self.drawdown() >= self.limits.max_drawdown:
            self.trip("max_drawdown")
        if self.day_start > 0 and (self.day_start - equity) / self.day_start >= self.limits.max_daily_loss:
            self.trip("max_daily_loss")

    def drawdown(self) -> float:
        return max(0.0, (self.peak - self.equity) / self.peak) if self.peak > 0 else 0.0

    def trip(self, reason: str) -> None:
        if not self.tripped:
            self.tripped, self.trip_reason = True, reason

    def reset(self, operator_id: str) -> None:
        """Human-only. The agent loop never calls this."""
        if not operator_id:
            raise PermissionError("operator id required")
        self.tripped, self.trip_reason = False, ""

    def check_order(self, side: str, lots: float, spread: float, data_age_s: float) -> Verdict:
        if side not in ("buy", "sell") or lots <= 0:
            return Verdict(False, "invalid_order")
        new = self.position + (lots if side == "buy" else -lots)
        reduces = abs(new) < abs(self.position) - 1e-12
        if self.tripped and not reduces:
            return Verdict(False, f"kill_switch:{self.trip_reason}")
        if abs(new) > self.limits.max_position_lots + 1e-9 and not reduces:
            return Verdict(False, "max_position")
        if not reduces:
            if spread > self.limits.max_spread:
                return Verdict(False, "spread_too_wide")
            if data_age_s > self.limits.max_data_age_s:
                return Verdict(False, "stale_data")
        return Verdict(True)
