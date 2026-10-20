#!/usr/bin/env python3
"""Record today's KOSPI forecast for next-day scoring.

usage: kospi_record.py --p-model 22 --p-final 30 --low -3.57 --high 0.24 \
         --adj-reason "야간선물 +1.2%로 미국장 하락분 일부 선반영" --thesis "한 줄 근거" [--date YYYY-MM-DD]

p-model / p-final are P(KOSPI closes up) in %. low/high are the model's 68% range (copy as printed).
The adjustment |p-final - p-model| is capped at 10 percentage points and needs a reason when non-zero.

If the data dir is a git checkout (install.sh --with-history, or the publisher's track-record clone),
the new record and any actuals the collector added are committed; they are pushed only when the
checkout has `git config kospi.push true`. Git problems print a warning and never fail the record.
"""
import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

PRED_FILE = os.path.join(os.environ.get("KOSPI_FORECAST_DIR", os.path.expanduser("~/.hermes/data/kospi_forecast")), "predictions.jsonl")
MAX_ADJ = 10

ap = argparse.ArgumentParser()
ap.add_argument("--p-model", type=float, required=True, help="통계 기준 P(상승) %%")
ap.add_argument("--p-final", type=float, required=True, help="조정 후 최종 P(상승) %%")
ap.add_argument("--low", type=float, required=True, help="모델 68%% 범위 하단 (전일 종가 대비 %%)")
ap.add_argument("--high", type=float, required=True, help="모델 68%% 범위 상단")
ap.add_argument("--adj-reason", default="")
ap.add_argument("--thesis", default="")
ap.add_argument("--date", default=datetime.now(timezone(timedelta(hours=9))).date().isoformat())
a = ap.parse_args()
for v in (a.p_model, a.p_final):
    if not 0 <= v <= 100:
        raise SystemExit("probabilities must be 0-100 (P(up) in %)")
if abs(a.p_final - a.p_model) > MAX_ADJ + 1e-9:
    raise SystemExit(f"adjustment {a.p_final - a.p_model:+.0f}pp exceeds ±{MAX_ADJ}pp cap")
if a.p_final != a.p_model and not a.adj_reason.strip():
    raise SystemExit("--adj-reason is required when p-final differs from p-model")
if a.low > a.high:
    a.low, a.high = a.high, a.low
os.makedirs(os.path.dirname(PRED_FILE), exist_ok=True)
rec = {"date": a.date, "p_model": a.p_model, "p_final": a.p_final,
       "direction": "상승" if a.p_final >= 50 else "하락", "low": a.low, "high": a.high,
       "adj_reason": a.adj_reason[:200], "thesis": a.thesis[:200],
       "recorded_at": datetime.now(timezone.utc).isoformat()}
with open(PRED_FILE, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("recorded", json.dumps(rec, ensure_ascii=False))


def git_sync(data_dir, msg):
    if not os.path.isdir(os.path.join(data_dir, ".git")):
        return
    def g(*args):
        return subprocess.run(["git", "-C", data_dir, *args], capture_output=True, text=True, timeout=60)
    try:
        g("add", "--", "*.jsonl")
        if g("diff", "--cached", "--quiet").returncode == 0:
            return
        r = g("commit", "-q", "-m", msg)
        if r.returncode:
            print("warning: git commit failed:", (r.stderr or r.stdout).strip()[:300])
            return
        if g("config", "--bool", "kospi.push").stdout.strip() != "true":
            print("committed locally (push disabled)")
            return
        r = g("push", "-q", "origin", "HEAD")
        print("pushed track record" if r.returncode == 0 else "warning: git push failed: " + r.stderr.strip()[:300])
    except Exception as e:  # noqa: BLE001
        print(f"warning: git sync skipped: {type(e).__name__}: {e}")


git_sync(os.path.dirname(PRED_FILE), f"forecast {a.date}: P(up) {a.p_model:g}% -> {a.p_final:g}%")
