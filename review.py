"""Nightly review: Brier score + calibration from the decision journal.
fwd_ret_bps is filled by a separate price-history job (forward return after each decision).
It PROPOSES changes only; shipping a new schema goes through the Agenkit approval gate."""
from __future__ import annotations
import json


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def _pairs(recs: list[dict]) -> list[tuple[float, int]]:
    out = []
    for r in recs:
        d, fr = r.get("direction"), r.get("fwd_ret_bps")
        if d in ("long", "short") and fr is not None and "conf" in r:
            hit = int((fr > 0) if d == "long" else (fr < 0))
            out.append((float(r["conf"]), hit))
    return out


def brier(pairs: list[tuple[float, int]]) -> float | None:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def calibration(pairs, bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)):
    rows = []
    for lo, hi in zip(bins, bins[1:]):
        b = [(p, y) for p, y in pairs if lo <= p < hi]
        if b:
            rows.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b),
                         "mean_conf": round(sum(p for p, _ in b) / len(b), 3),
                         "hit_rate": round(sum(y for _, y in b) / len(b), 3)})
    return rows


def nightly_report(path: str) -> dict:
    recs = load(path)
    pairs = _pairs(recs)
    gated = _pairs([r for r in recs if r.get("gated")])
    missed = _pairs([r for r in recs if not r.get("gated")])
    b = brier(pairs)
    notes = []
    if b is None or len(pairs) < 30:
        notes.append("sample < 30: no schema change justified")
    else:
        if b >= 0.25:
            notes.append("Brier >= 0.25 (no better than coin flip): raise min_conf or disable Jev gating")
        for row in calibration(pairs):
            if row["n"] >= 10 and row["mean_conf"] - row["hit_rate"] > 0.10:
                notes.append(f"overconfident in {row['bin']}: raise p_haircut")
    return {"n_decisions": len(recs), "n_scored": len(pairs), "brier": b,
            "brier_gated": brier(gated), "brier_missed": brier(missed),
            "calibration": calibration(pairs), "proposals": notes,
            "ship_requires": "operator approval via Agenkit gate"}
