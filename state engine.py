"""Deterministic state engine: broker quotes/book -> one compact causal snapshot.
All numbers are computed here, never by the model. Snapshot is the ONLY thing Jev sees."""
from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass


class CausalityError(ValueError):
    """Raised on future-dated or out-of-order data so nothing leaks from the future."""


@dataclass(frozen=True)
class BookTick:
    ts: float            # broker timestamp, epoch seconds
    bid: float
    ask: float
    bids: tuple = ()     # optional depth: ((px, size), ...) best first. Spot gold often has none.
    asks: tuple = ()


@dataclass(frozen=True)
class Snapshot:
    ts: float
    mid: float
    spread: float
    imbalance: float     # -1..+1, 0 when no depth available
    rv_bps: float        # per-tick realized vol of log mid returns, in bps
    inventory: float     # signed lots
    drawdown: float      # fraction from peak equity
    age_s: float
    stale: bool

    def to_text(self) -> str:
        return (f"ts={self.ts:.3f}|mid={self.mid:.2f}|spr={self.spread:.2f}|imb={self.imbalance:+.3f}"
                f"|rv_bps={self.rv_bps:.2f}|inv={self.inventory:+.2f}|dd={self.drawdown:.4f}"
                f"|age={self.age_s:.2f}|stale={int(self.stale)}")


def est_tokens(text: str) -> int:
    """Conservative estimate (~3 chars/token). Label: estimate, not a tokenizer count."""
    return math.ceil(len(text) / 3)


class StateEngine:
    def __init__(self, vol_window: int = 60, depth: int = 5, max_tokens: int = 400,
                 max_stale_s: float = 5.0, clock_tol_s: float = 0.0):
        self.depth, self.max_tokens = depth, max_tokens
        self.max_stale_s, self.clock_tol_s = max_stale_s, clock_tol_s
        self._mids: deque[float] = deque(maxlen=vol_window + 1)
        self._last_ts: float | None = None

    def update(self, tick: BookTick, now: float, inventory: float,
               equity: float, peak_equity: float) -> Snapshot:
        if not (tick.bid > 0 and tick.ask >= tick.bid):
            raise ValueError("bad quote")
        if tick.ts > now + self.clock_tol_s:
            raise CausalityError("tick timestamp is in the future")
        if self._last_ts is not None and tick.ts <= self._last_ts:
            raise CausalityError("out-of-order or duplicate tick")
        self._last_ts = tick.ts

        mid = (tick.bid + tick.ask) / 2
        self._mids.append(mid)

        b = sum(s for _, s in tick.bids[: self.depth])
        a = sum(s for _, s in tick.asks[: self.depth])
        imbalance = (b - a) / (b + a) if (b + a) > 0 else 0.0

        m = list(self._mids)
        rets = [math.log(m[i] / m[i - 1]) for i in range(1, len(m))]
        if len(rets) >= 2:
            mu = sum(rets) / len(rets)
            rv = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * 1e4
        else:
            rv = 0.0

        dd = max(0.0, (peak_equity - equity) / peak_equity) if peak_equity > 0 else 0.0
        age = max(0.0, now - tick.ts)
        snap = Snapshot(tick.ts, mid, tick.ask - tick.bid, imbalance, rv, inventory, dd,
                        age, age > self.max_stale_s)
        if est_tokens(snap.to_text()) > self.max_tokens:
            raise ValueError("snapshot exceeds token budget")
        return snap
