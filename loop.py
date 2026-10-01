"""Live loop. Order: state -> Jev -> (escalate) -> gate -> size -> risk guard -> broker -> alert."""
from __future__ import annotations
import json
import time
from typing import Callable, Protocol
from .jev_schema import SCHEMA, JevClient, Brain, SchemaError, parse_decision
from .policy import PolicyParams, gate, needs_escalation, size_lots, stop_distance_usd
from .risk import RiskGuard
from .state_engine import BookTick, StateEngine


class Broker(Protocol):
    def equity(self) -> float: ...
    def position(self) -> float: ...                       # signed lots
    def place(self, side: str, lots: float, stop: float, target: float) -> dict: ...


class Agent:
    def __init__(self, engine: StateEngine, jev: JevClient, brain: Brain | None, broker: Broker,
                 guard: RiskGuard, params: PolicyParams, alert: Callable[[dict], None],
                 journal_path: str):
        self.engine, self.jev, self.brain, self.broker = engine, jev, brain, broker
        self.guard, self.p, self.alert, self.journal_path = guard, params, alert, journal_path

    def _log(self, rec: dict) -> None:
        with open(self.journal_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def on_tick(self, tick: BookTick, now: float, day: str) -> str:
        pos, eq = self.broker.position(), self.broker.equity()
        self.guard.mark(eq, day, pos)
        snap = self.engine.update(tick, now, pos, eq, self.guard.peak)
        text = snap.to_text()

        if self.guard.tripped:                              # reduce-only flatten
            if abs(pos) > 1e-9:
                side = "sell" if pos > 0 else "buy"
                if self.guard.check_order(side, abs(pos), snap.spread, snap.age_s).ok:
                    self.broker.place(side, abs(pos), 0.0, 0.0)
            self._log({"ts": snap.ts, "act": "killed", "why": self.guard.trip_reason})
            self.alert({"event": "kill_switch", "reason": self.guard.trip_reason})
            return "killed"
        if snap.stale:
            self._log({"ts": snap.ts, "act": "hold", "why": "stale"})
            return "hold"

        try:
            dec = parse_decision(self.jev.decide(text, SCHEMA))
            if needs_escalation(dec, self.p):
                raw = self.brain.reread(text, dec) if self.brain else None
                dec = parse_decision(raw) if raw else None
        except SchemaError as e:
            self._log({"ts": snap.ts, "act": "hold", "why": f"schema:{e}"})
            return "hold"
        if dec is None:
            self._log({"ts": snap.ts, "act": "hold", "why": "escalated_no_view"})
            return "hold"

        ok, why = gate(dec, self.p)
        rec = {"ts": snap.ts, "mid": snap.mid, "direction": dec.direction, "conf": dec.direction_conf,
               "quality": dec.setup_quality, "regime": dec.regime, "gated": ok, "why": why}
        if not ok or abs(pos) > 1e-9:                       # one position at a time
            rec["act"] = "hold"
            self._log(rec)
            return "hold"

        stop = stop_distance_usd(snap.mid, snap.rv_bps, self.p)
        lots = size_lots(eq, dec.direction_conf, stop, self.p)
        lots = min(lots, self.guard.limits.max_position_lots)   # clamp; guard still has final say
        side = "buy" if dec.direction == "long" else "sell"
        verdict = self.guard.check_order(side, lots, snap.spread, snap.age_s)
        if lots <= 0 or not verdict.ok:
            rec.update(act="hold", why=f"size/risk:{verdict.reason if lots > 0 else 'below_min_lot'}")
            self._log(rec)
            return "hold"

        sgn = 1 if side == "buy" else -1
        stop_px, tgt_px = snap.mid - sgn * stop, snap.mid + sgn * stop * self.p.reward_risk
        fill = self.broker.place(side, lots, stop_px, tgt_px)
        rec.update(act="order", side=side, lots=lots, stop=stop_px, target=tgt_px, fill=fill)
        self._log(rec)
        self.alert({"event": "order", "side": side, "lots": lots, "mid": snap.mid,
                    "stop": stop_px, "target": tgt_px, "conf": dec.direction_conf})
        return "order"
