#!/usr/bin/env python3
"""Serve a read-only local dashboard over Phil's journals and score report."""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = pathlib.Path(__file__).resolve().parent
CACHE_SECONDS = 15
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_snapshot = None


def read_jsonl(path):
    rows = []
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return rows


def summarize(rows):
    counts = {}
    for row in rows:
        status = row.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    settled = [row for row in rows if row.get("status") in ("won", "lost")]
    opened = [row for row in rows if row.get("status") == "open"]
    return {
        "total": len(rows),
        "counts": counts,
        "open": len(opened),
        "settled": len(settled),
        "wins": sum(row.get("status") == "won" for row in settled),
        "pnl_usd": round(sum(row.get("pnl_usd", 0) or 0 for row in settled), 2),
        "open_stake_usd": round(sum(row.get("stake_usd", 0) or 0 for row in opened), 2),
    }


def public_row(row):
    fields = (
        "id", "ts", "question", "outcome", "status", "category", "edge_class",
        "entry_price", "market_prob_at_entry", "est_prob", "stake_usd", "pnl_usd",
        "settled_ts", "skip_reason", "strategy_rev", "superseded_by",
    )
    return {key: row[key] for key in fields if key in row}


def loop_status():
    lock = ROOT / ".loop.pid"
    try:
        pid = int(lock.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        if stat.split()[2] == "Z":
            raise ProcessLookupError
        command = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        ).strip()
        return {
            "active": True,
            "pid": pid,
            "mode": "real" if "--real" in command.split() else "paper",
            "command": command[:240],
        }
    except (OSError, ValueError):
        return {"active": False, "pid": None, "mode": None, "command": None}


def latest_cycle(lines):
    for line in reversed(lines):
        if " cycle done:" not in line:
            continue
        match = re.search(r"^(\S+) cycle done:.*?cash \$([\d,.]+)", line)
        return {
            "line": line[:1800],
            "timestamp": match.group(1) if match else None,
            "cash_usd": float(match.group(2).replace(",", "")) if match else None,
        }
    return None


def score_report():
    result = subprocess.run(
        [sys.executable, str(ROOT / "core" / "score.py"), "--json", "--skip-mtm"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "score.py failed")[-1200:])
    return json.loads(result.stdout)


def git_rows():
    result = subprocess.run(
        ["git", "log", "-12", "--format=%h%x1f%s%x1f%ct", "--", "strategy/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    rows = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) == 3:
                rows.append({"hash": parts[0], "subject": parts[1], "timestamp": parts[2]})
    return rows


def schedule_data():
    try:
        schedule = json.loads((ROOT / "strategy" / "schedule.json").read_text(encoding="utf-8"))
        return {
            "next_full_cycle_after": schedule.get("next_full_cycle_after"),
            "reason": schedule.get("reason", ""),
            "min_full_cycles_per_day": schedule.get("min_full_cycles_per_day"),
        }
    except (OSError, json.JSONDecodeError):
        return {}


def retrospectives():
    directory = ROOT / "journal" / "retros"
    files = sorted(directory.glob("*.md"), reverse=True)[:5] if directory.exists() else []
    result = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        result.append({"name": path.name, "excerpt": text[:900]})
    return result


def build_snapshot():
    ledger = read_jsonl(ROOT / "journal" / "ledger.jsonl")
    forecasts = read_jsonl(ROOT / "journal" / "forecasts.jsonl")
    real_ledger = read_jsonl(ROOT / "journal" / "real-ledger.jsonl")
    cycle_lines = (ROOT / "journal" / "cycles.log").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()[-30:]
    try:
        score = score_report()
        score_error = None
    except (OSError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        score = None
        score_error = str(exc)

    settled = [row for row in ledger if row.get("status") in ("won", "lost")]
    open_rows = [row for row in ledger if row.get("status") == "open"]
    settled.sort(key=lambda row: row.get("settled_ts", row.get("ts", "")), reverse=True)
    open_rows.sort(key=lambda row: row.get("ts", ""), reverse=True)
    forecasts.sort(key=lambda row: row.get("ts", ""), reverse=True)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "loop": loop_status(),
        "cycle": latest_cycle(cycle_lines),
        "cycles": [line[:1800] for line in cycle_lines if " cycle done:" in line][-8:][::-1],
        "ledger": summarize(ledger),
        "open_positions": [public_row(row) for row in open_rows[:12]],
        "recent_settled": [public_row(row) for row in settled[:12]],
        "real_ledger": summarize(real_ledger),
        "forecasts": {
            "summary": (score or {}).get("forecasts", {}),
            "rows": [public_row(row) for row in forecasts[:12]],
        },
        "score": score,
        "score_error": score_error,
        "schedule": schedule_data(),
        "strategy_commits": git_rows(),
        "retrospectives": retrospectives(),
    }


def snapshot():
    global _cached_at, _cached_snapshot
    with _cache_lock:
        now = time.monotonic()
        if _cached_snapshot is None or now - _cached_at >= CACHE_SECONDS:
            _cached_snapshot = build_snapshot()
            _cached_at = now
        return _cached_snapshot


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/snapshot":
            try:
                body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
            else:
                self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/", "/index.html"):
            body = (DASHBOARD / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        print(f"dashboard: {self.address_string()} {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.daemon_threads = True
    print(f"Phil dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()