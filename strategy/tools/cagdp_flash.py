#!/usr/bin/env python3
"""Canada monthly GDP: empirical flash -> first-print ladder.

Usage:
  python3 strategy/tools/cagdp_flash.py --flash 0.1
  python3 strategy/tools/cagdp_flash.py --flash 0.0 --brackets "<0,0.0-0.1,0.2-0.3,>=0.4"

Why (RETRO-20260929-1734): StatCan publishes an advance ("flash") estimate
for month m alongside month m-1's GDP, and Polymarket brackets resolve on
the later first print. Bet 05333272be9d (July 2026, 0.0-0.1 Yes @0.57, own
0.63) won on this series, fetched from The Daily on 2026-09-18, which beat
the market ladder on all three rows (own summed Brier 0.212 vs market
0.301). The series is the benchmark, so it lives here, not in someone's
memory: the from-memory prior for that same bracket was 0.76, and the
fetched series said 0.64.

Append each new (flash, first print) pair to PAIRS when its print lands.
Output is the RAW empirical read. Per the playbook's mechanical-read rule
(DEEP-2026-09-26), that raw read is est_prob unless a tilt comes from a
measured input quoted in the note (for example, a wholesale or retail print
against its own flash).
"""
import argparse
import json
import sys
from collections import Counter

# (reference month, flash, first print), MoM percent. Source: StatCan The Daily.
PAIRS = [
    ("2025-05", -0.1, -0.1), ("2025-06", 0.1, -0.1), ("2025-07", 0.1, 0.2),
    ("2025-08", 0.0, -0.3), ("2025-09", 0.1, 0.2), ("2025-10", -0.3, -0.3),
    ("2025-11", 0.1, 0.0), ("2025-12", 0.1, 0.2), ("2026-01", 0.0, 0.1),
    ("2026-02", 0.2, 0.2), ("2026-03", 0.0, -0.1), ("2026-04", 0.4, 0.5),
    ("2026-05", 0.1, 0.3), ("2026-06", 0.2, 0.3),
    ("2026-07", 0.0, 0.0),  # graded RETRO-20260929-1734
]


def errors():
    return [round(p - f, 1) for _, f, p in PAIRS]


def ladder(flash):
    c = Counter(round(flash + e, 1) for e in errors())
    n = sum(c.values())
    return {v: k / n for v, k in sorted(c.items())}


def in_bracket(v, spec):
    spec = spec.strip()
    if spec.startswith(">="):
        return v >= float(spec[2:]) - 1e-9
    if spec.startswith("<"):
        return v < float(spec[1:]) - 1e-9
    lo, hi = (float(x) for x in spec.split("-", 1)) if not spec.startswith("-") else _neg(spec)
    return lo - 1e-9 <= v <= hi + 1e-9


def _neg(spec):
    # "-0.2--0.1" style ranges with a negative lower bound
    rest = spec[1:]
    lo, hi = rest.split("-", 1)
    return -float(lo), float(hi)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--flash", type=float, required=True,
                    help="StatCan advance estimate for the month, MoM percent")
    ap.add_argument("--brackets", default="",
                    help="comma-separated: '<0', '0.0-0.1', '>=0.4'")
    a = ap.parse_args(argv)
    e = errors()
    mean = sum(e) / len(e)
    sd = (sum((x - mean) ** 2 for x in e) / (len(e) - 1)) ** 0.5
    lad = ladder(a.flash)
    out = {"n_pairs": len(e), "err_mean": round(mean, 3), "err_sd": round(sd, 3),
           "exact_hits": sum(1 for x in e if x == 0),
           "first_print": {f"{v:+.1f}": round(p, 3) for v, p in lad.items()}}
    if a.brackets:
        out["brackets"] = {b: round(sum(p for v, p in lad.items() if in_bracket(v, b)), 3)
                           for b in a.brackets.split(",")}
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
