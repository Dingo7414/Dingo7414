"""Typed decision schema. Jev API details are NOT verified here: JevClient is an interface
you bind to the real console.typesafe.al client once you have its docs."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

REGIMES = ("trending", "mean_reverting", "high_vol", "crisis")
DIRECTIONS = ("long", "short", "neutral")
RISK_STATES = ("safe", "near_limit", "reduce")

SCHEMA = {
    "regime": {"type": "choice", "options": list(REGIMES)},
    "direction": {"type": "choice", "options": list(DIRECTIONS)},
    "direction_conf": {"type": "float", "min": 0.0, "max": 1.0},
    "toxic_flow": {"type": "bool"},                      # my reading of the garbled spec field
    "setup_quality": {"type": "int", "min": 0, "max": 3},
    "risk_state": {"type": "choice", "options": list(RISK_STATES)},
}


@dataclass(frozen=True)
class JevDecision:
    regime: str
    direction: str
    direction_conf: float
    toxic_flow: bool
    setup_quality: int
    risk_state: str


class SchemaError(ValueError):
    pass


def parse_decision(raw: dict) -> JevDecision:
    try:
        d = JevDecision(str(raw["regime"]), str(raw["direction"]), float(raw["direction_conf"]),
                        bool(raw["toxic_flow"]), int(raw["setup_quality"]), str(raw["risk_state"]))
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError(f"malformed decision: {e}") from e
    if (d.regime not in REGIMES or d.direction not in DIRECTIONS or d.risk_state not in RISK_STATES
            or not 0.0 <= d.direction_conf <= 1.0 or not 0 <= d.setup_quality <= 3):
        raise SchemaError("decision out of range")
    return d


class JevClient(Protocol):
    def decide(self, snapshot_text: str, schema: dict) -> dict: ...


class Brain(Protocol):
    """Opus deep re-read. Returns a raw decision dict, or None to stand down."""
    def reread(self, snapshot_text: str, jev_decision: JevDecision) -> dict | None: ...
