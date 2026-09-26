"""Compact view of a core/scan.py pool, for unscreened fallback cycles.

When the screener quota is exhausted, screen.py prepare writes no pool
file, so the fallback selection needs its own readable view of scan's
JSONL (added 2026-09-26 20:2xZ, first quota-exhausted FULL on the operator
machine).

    python3 core/scan.py --hours 336 --limit 800 2>/dev/null \
        | python3 strategy/tools/pool.py [--max-hours 72] [--min-liq 5000] [--grep REGEX]

One line per market, sorted by end date: hours to end, market id, first
outcome price, liquidity, 24h volume, question. Line-constructed sports
markets (O/U, spreads) are dropped unless --lines is given, matching the
screener's filter.
"""
import argparse
import datetime as dt
import json
import re
import sys

LINE_RE = re.compile(r"O/U \d|Spread|\([+-]\d+(\.\d+)?\)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=72.0)
    ap.add_argument("--min-liq", type=float, default=5000.0)
    ap.add_argument("--grep", default=None)
    ap.add_argument("--lines", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    pat = re.compile(a.grep, re.I) if a.grep else None
    rows = []
    for line in sys.stdin:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        q = r.get("question", "")
        if not a.lines and LINE_RE.search(q):
            continue
        if pat and not pat.search(q + " " + r.get("slug", "")):
            continue
        try:
            end = dt.datetime.fromisoformat(r["end_date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        h = (end - now).total_seconds() / 3600
        if h > a.max_hours or (r.get("liquidity") or 0) < a.min_liq:
            continue
        p = (r.get("outcome_prices") or [None])[0]
        rows.append((h, r["market_id"], p, r.get("liquidity") or 0,
                     r.get("volume_24h") or 0, q))
    for h, mid, p, liq, vol, q in sorted(rows):
        print(f"{h:6.1f}h {mid:>8} p={p} liq={liq:9.0f} v24={vol:9.0f} {q[:110]}")
    print(f"pool.py: {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
