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
- Compare runs before and after a US DST switch (first Sunday of Nov, second Sunday of Mar): a section
  that fails only on winter weekdays (Mondays fine) depends on something published ~18:00 ET.
- Section 8's `금리/환율/원자재(현재)` and `지수선물` lines log the live quotes at 08:00 every day. Diff them
  against the rows published later to measure what a live-quote stand-in would get wrong.

## 1b. Judge the live track record

- **Reproduce every recorded baseline** before blaming the model: load the series once
  (`kospi_model.load_series(<today>)`), then per forecast date `build_rows(series, T, use_hyg=<as
  shipped then>)`, fit on `hist` rows with `T < date`, predict `cur`. Exact match of `p_model/low/high`
  = the 08:00 pipeline fed the model correctly; a mismatch = time-of-day data bug.
- Significance with small n: paired t of per-day Brier (model vs 0.555 base rate; final vs model),
  t of the live mean vs the backtest Brier, binomial P(coverage ≤ k | 0.68). Then slice the walk-forward
  backtest by year and by the live window (`backtest` loop, keep per-day p/y) — if the backtest's own
  recent windows are fine, the live gap is noise. Don't change the model below ~40 live records.

## 1c. Cross-check flow numbers independently

One full daily table covers spot (개인/외국인/기관), program (차익/비차익) and futures contracts:
`open.shinhansec.com/goodicyber/mk/1201.jsp` (매경, 억원/계약); program also at
`markets.hankyung.com/investment/program-trading` (백만원). Find them via web search. Personal blogs
(tistory etc.) disagree often — a blog mismatch flagged in a report is not evidence of a collector bug.

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
import kospi_morning as km, kospi_rates as kr
km.datetime = kr.datetime = FakeDT  # kospi_rates has its own clock (live-quote fallback for yields)
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

For the yield live-quote stand-in, wrap `kr._get`: hide `/prices` rows dated ≥ D and return a synthetic
quote `{"localTradedAt": "<D>T<HH:MM>:00-05:00", "closePrice": ...}`. Cases: post-close 17:59 (used),
11:30 or 04:10 (holiday-like, rejected), 13:21 (stale, rejected), 14:30 (early close, used), a
Monday-dated quote under a Sunday-evening clock (rejected). Compare against history cut at the same
date (hide rows ≥ D+1) — full history adds later sessions and changes n. Matching numbers only prove
the plumbing when the stand-in is the final value; measure the real gap separately (§1).

State plainly in the report that the real 08:00 run has not been observed yet; the next morning's
briefing is the first live check.

The scratch dir is pruned after a day of inactivity — recreate `fakenow.py` from the block above
rather than assuming it is still there.

## 4. Dry-run a prompt or report-format change

Before editing the live job's prompt, delegate one subagent with: the skill path, the new
`references/cron-prompt.md`, and a saved real data block (scratch file). Ask it to write the report
once, then audit (a) every number against the block, (b) character count vs any cap, (c) instructions
that conflict with each other or can't be met from the block. Fix the prompt/collector from (b)/(c)
before shipping — infeasible length caps and contradictory rules only surface when an agent actually
writes from real data, and otherwise show up as daily caveats in delivered reports.

## 5. Independent re-review of a release

Delegate one subagent (it knows nothing of the session) with, in `context`:
- reply language (Korean), output shape: numbered findings `[높음/중간/낮음] file:line — problem —
  evidence from a real run — fix`, then what was checked and found correct, then what is unverified;
- guardrails: no repo edits, no commit/push/tag, no `kospi_record.py`, no `hermes cron run`, never
  write the real data dir — scratch `KOSPI_FORECAST_DIR`, `KOSPI_SNAPSHOT_DIR` unset;
- the diff range (`git diff vPREV..HEAD`), the original problem with its evidence paths, the fix design,
  and the tests already run ("re-run or extend them, don't just trust them");
- a probe list: Monday 08:00 (= Sunday evening ET), US bond-market and NYSE holidays, KRX holiday gaps
  (several US sessions), DST switch dates, evening/afternoon runs, endpoint failure, installed copies
  == HEAD, other sections' output format unchanged.
While it runs, do your own targeted checks (e.g. holiday rows in the history, logged-quote gaps) and
merge both into one report. Treat its claims as reports: re-check anything you will act on.
