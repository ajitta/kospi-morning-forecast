# Auditing past runs and testing time-dependent fixes

Use when asked to review how the briefing has been doing ("지금까지 문제점 찾아서 개선") or before
shipping any change whose behaviour depends on the time of day.

## 1. Collect the evidence (one pass, batch the reads)

- Per-run full transcript (prompt + injected data block + response):
  `$HERMES_HOME/cron/output/<job_id>/<YYYY-MM-DD_HH-MM-SS>.md` — the `## Script Output` block is the
  exact data the agent saw at 08:00. The flat `<job_id>_<ts>.txt` files are the delivered reply only.
- Job state: `hermes cron list` (with `HERMES_HOME` = the profile) or `cron/jobs.json`.
- Track record: `predictions.jsonl` / `actuals.jsonl` + `git log` in the data dir. Compare
  `recorded_at` of each actual with its date: an actual recorded two mornings later = the scorecard
  lagged a session.
- Grep every delivered reply for what the agent itself flagged:
  `grep -nE '!!|수집 실패|수집 오류|미채점|잘린|중복|다릅니다|dev 프로필' <job_id>_*.txt` — the agent
  reports recurring defects at the end of its reply; a failure that appears every day is a systematic
  time-of-day bug, not a flaky source.
- Then re-run the collector now (`KOSPI_FORECAST_DIR=<scratch> ... --force`). If a section that failed
  at 08:00 works in the afternoon, the endpoint is session-scoped and resets before the open.

## 2. Diagnose a portal number that disagrees with KRX/press

Fetch a dated per-stock series (Daum `/api/investor/days?symbolCode=A{code}&perPage=4`) for 4–6 names
from the suspicious list and test whether the listed amount equals one day, the previous day, or the
sum of several days (qty × price). An undated aggregate that matches `D-1 + D` is a moving window —
check the response's own `fromDate`/`toDate` and gate on them. Also check whether the price shown is
the KRX close or the NXT-integrated last price.

## 3. Test 08:00 behaviour in the afternoon (fake clock)

The data is live, but the date logic can be exercised by patching `datetime.now` in the module:

```python
# fakenow.py <iso-datetime-with-tz> x [--force|--snapshot]   (run from a scratch dir)
import sys, datetime as _dt
target = _dt.datetime.fromisoformat(sys.argv[1])
class FakeDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return target.astimezone(tz) if tz else target.replace(tzinfo=None)
sys.argv = [sys.argv[2]] + sys.argv[3:]
sys.path.insert(0, "/home/ubuntu/work/kospi-morning-forecast/scripts")
import kospi_morning as km
km.datetime = FakeDT
km.snapshot() if "--snapshot" in sys.argv else km.main()
```

Run with `KOSPI_FORECAST_DIR=<scratch copy of the data dir>`. Useful cases:
- yesterday 08:00 (today's chart row must be dropped, yesterday scored the same morning);
- tomorrow 08:00 after a real `--snapshot` run today (sections 3/6/7 come from `[전일 스냅샷]`);
- before 16:00 and a holiday with `--snapshot` (must be silent);
- a fresh `KOSPI_SNAPSHOT_DIR` at 20:10 (warning only if incomplete).
To exercise a fallback path (e.g. ranking window mismatch), wrap `km.daum` to alter the response
and compare the fallback's output with the primary source on the same day — they should agree
within a few percent before the fallback ships.

State plainly in the report that the real 08:00 run has not been observed yet; the next morning's
briefing is the first live check.
