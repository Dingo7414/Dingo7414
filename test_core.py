import json, os, tempfile
import pytest
from xau_agent.state_engine import StateEngine, BookTick, CausalityError, est_tokens
from xau_agent.risk import RiskGuard, RiskLimits
from xau_agent.jev_schema import parse_decision, SchemaError, JevDecision
from xau_agent.policy import PolicyParams, gate, kelly_fraction, size_lots, needs_escalation
from xau_agent.review import brier, nightly_report
from xau_agent.loop import Agent

P = PolicyParams()
def D(**k):
    base = dict(regime="trending", direction="long", direction_conf=0.9, toxic_flow=False, setup_quality=3, risk_state="safe")
    base.update(k); return JevDecision(**base)

def test_gate_rules():
    assert gate(D(), P)[0]
    assert not gate(D(direction_conf=0.80), P)[0]      # strictly above 0.80
    assert not gate(D(setup_quality=1), P)[0]
    assert not gate(D(risk_state="near_limit"), P)[0]
    assert not gate(D(regime="crisis"), P)[0]
    assert not gate(D(toxic_flow=True), P)[0]
    assert not gate(D(direction="neutral"), P)[0]

def test_escalation():
    assert needs_escalation(D(direction_conf=0.55), P)
    assert needs_escalation(D(regime="crisis"), P)
    assert not needs_escalation(D(direction_conf=0.7), P)

def test_kelly_and_size():
    assert kelly_fraction(0.5, 1.0) == 0.0
    assert abs(kelly_fraction(0.6, 2.0) - 0.4) < 1e-9
    assert size_lots(150, 0.9, 5.0, P) == 0.0           # tiny account: below min lot, never rounded up
    lots = size_lots(10000, 0.9, 5.0, P)
    assert 0 < lots <= 0.4 and abs(lots * 5 * 100 / 10000 - 0.02) < 0.002   # capped at ~2% risk

def test_state_causality_and_budget():
    e = StateEngine()
    t = BookTick(100.0, 2000.0, 2000.3, ((2000.0, 5),), ((2000.3, 3),))
    s = e.update(t, 100.5, 0.0, 1000, 1000)
    assert s.imbalance == pytest.approx(0.25) and est_tokens(s.to_text()) < 400
    with pytest.raises(CausalityError): e.update(BookTick(99.0, 2000, 2000.3), 101, 0, 1000, 1000)
    with pytest.raises(CausalityError): e.update(BookTick(200.0, 2000, 2000.3), 101, 0, 1000, 1000)

def test_risk_latch_and_reduce_only():
    g = RiskGuard(RiskLimits(), 1000, "d1")
    g.mark(1000, "d1", 0.03)
    assert g.check_order("buy", 0.02, 0.3, 0.1).ok is False or g.limits.max_position_lots >= 0.05
    g.mark(840, "d1", 0.03)                              # 16% drawdown
    assert g.tripped
    assert not g.check_order("buy", 0.01, 0.3, 0.1).ok
    assert g.check_order("sell", 0.03, 0.3, 0.1).ok      # flatten allowed
    assert not g.check_order("sell", 0.08, 0.3, 0.1).ok  # flip through zero blocked
    with pytest.raises(PermissionError): g.reset("")

def test_schema_rejects_garbage():
    with pytest.raises(SchemaError): parse_decision({"regime": "x"})
    with pytest.raises(SchemaError): parse_decision(dict(regime="trending", direction="long", direction_conf=1.5, toxic_flow=False, setup_quality=3, risk_state="safe"))

def test_brier_and_report():
    assert brier([(1.0, 1), (0.0, 0)]) == 0.0
    assert brier([(0.9, 0)]) == pytest.approx(0.81)
    f = tempfile.mktemp()
    open(f, "w").write("\n".join(json.dumps({"direction": "long", "conf": 0.9, "fwd_ret_bps": 5, "gated": True}) for _ in range(3)))
    assert nightly_report(f)["n_scored"] == 3

class FakeBroker:
    def __init__(s, eq=100000.0): s.eq, s.pos, s.orders = eq, 0.0, []
    def equity(s): return s.eq
    def position(s): return s.pos
    def place(s, side, lots, stop, target): s.orders.append((side, lots)); s.pos += lots if side == "buy" else -lots; return {"ok": 1}
class FakeJev:
    def __init__(s, raw): s.raw = raw
    def decide(s, t, schema): return s.raw

def _agent(raw, brain=None, broker=None):
    f = tempfile.mktemp()
    b = broker or FakeBroker()
    a = Agent(StateEngine(), FakeJev(raw), brain, b, RiskGuard(RiskLimits(max_position_lots=1.0), b.eq, "d"), P, lambda m: None, f)
    return a, b

GOOD = dict(regime="trending", direction="long", direction_conf=0.9, toxic_flow=False, setup_quality=3, risk_state="safe")

def test_loop_orders_then_holds():
    a, b = _agent(GOOD)
    for i in range(3): a.on_tick(BookTick(100 + i, 2000 + i * .1, 2000.3 + i * .1), 100.5 + i, "d")
    assert len(b.orders) == 1                            # one position at a time

def test_loop_blocks_low_conf_and_bad_schema():
    a, b = _agent(dict(GOOD, direction_conf=0.7))
    assert a.on_tick(BookTick(100, 2000, 2000.3), 100.5, "d") == "hold"
    a2, b2 = _agent({"junk": 1})
    assert a2.on_tick(BookTick(100, 2000, 2000.3), 100.5, "d") == "hold" and not b2.orders

def test_loop_escalation_defers_to_brain_but_gate_still_applies():
    class Brain:
        def reread(s, t, d): return dict(GOOD, direction_conf=0.7)   # brain unconvinced -> gate blocks
    a, b = _agent(dict(GOOD, direction_conf=0.4), Brain())
    assert a.on_tick(BookTick(100, 2000, 2000.3), 100.5, "d") == "hold" and not b.orders

def test_loop_kill_switch_flattens():
    b = FakeBroker(eq=80000.0); b.pos = 0.5
    a, _ = _agent(GOOD, broker=b)
    a.guard.peak = 100000.0
    assert a.on_tick(BookTick(100, 2000, 2000.3), 100.5, "d") == "killed" and b.pos == 0
