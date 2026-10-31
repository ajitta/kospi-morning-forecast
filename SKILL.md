---
name: kospi-morning-forecast
description: Use when writing the daily pre-open KOSPI forecast.
version: 1.3.0
author: ajitta
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [KOSPI, KRX, Korea, Stock Market, Forecast, Pre-market, Briefing, Investor Flows, Finance, Cron]
    homepage: https://github.com/ajitta/kospi-morning-forecast
---

# KOSPI Morning Forecast

Daily 08:00 KST briefing (cron, weekdays, KRX holidays skipped by the script's wake gate):
previous-session KOSPI investor flows (spot / futures / options / program), overnight US market,
strong/weak themes, foreign & institution top-10 net buy/sell with reasons, and today's KOSPI
direction forecast that is recorded and scored the next day. KOSPI only — KOSDAQ excluded.

## Install / share / release

Bundled files: `scripts/kospi_morning.py` (collector, cron `script=`), `scripts/kospi_model.py`
(baseline), `scripts/kospi_rates.py` (US Treasury + credit block), `scripts/kospi_record.py` (forecast log + git sync), `scripts/kospi_snapshot.py`
(after-close snapshot, `--no-agent` cron job), `references/cron-prompt.md` (job prompt).
`bash scripts/install.sh [--cron [<deliver>]] [--snapshot-cron [<deliver>]] [--with-history]` copies the
scripts to `$HERMES_HOME/scripts/` (cron scripts must live there), smoke-tests the live data path,
optionally seeds the forecast log from the `data` branch, and optionally creates the weekday 08:00 KST
briefing job plus the 16:10/20:10 KST snapshot job (`--snapshot-cron` adds only the latter, for upgrades).

Canonical source is the public repo `ajitta/kospi-morning-forecast` (working copy
`~/work/kospi-morning-forecast`). Every change ships as:
1. `git fetch && git status` in the working copy, rebase onto `origin/main` if behind — main gets
   commits from elsewhere (GitHub web edits, other sessions); tagging before the rebase leaves the tag
   on an orphaned commit.
2. `diff -r --exclude=__pycache__ . <this skill dir>` (SKILL.md *and* references/) — this installed copy
   gets patched directly (skill reviews), and install.sh overwrites it from the working copy, silently
   discarding those edits. Copy installed-only changes into the working copy first.
3. Edit the working copy; test with `KOSPI_FORECAST_DIR=<scratch>` (never the real data dir). Anything
   that depends on time of day (pre-open resets, today's row, snapshot gating) must be tested with a
   faked clock, not just a run "now" — recipe and run-audit procedure in `references/maintenance-audit.md`.
4. `bash scripts/install.sh`, then also `cp scripts/kospi_*.py ~/.hermes/scripts/` (see profile pitfall).
5. Bump `version:` → commit → push main → tag `vX.Y.Z` on the pushed HEAD and push the tag →
   `gh release create` with a one-line Korean changelog. Push main and tag as separate steps and
   stop on the first failure; a chained `&&` that pushes the tag before a rejected main push
   publishes a tag for an unpublished commit.
If the cron prompt changes, update `references/cron-prompt.md` and the live job together. General
sharing procedure: `hermes-agent-skill-authoring` → `references/sharing-user-skills.md`.

Persisted state is deliberately minimal: the model refits from live Naver history (since 2016) on
every run, so a fresh install reproduces model + backtest exactly; only the forecast log and the
realised outcomes (not re-fetchable as a long series from the windowed price endpoint) are stored.
When adding a feature, persist only what can't be re-fetched.

## Always-on rules

- **Every number comes from this run's data block or a cited web source.** Never invent,
  interpolate or "round from memory". Missing item → write `데이터 수집 실패` and continue.
- **Options flows have no public API.** Fill them only from same-day market-close articles
  (search e.g. `{M}월 {D}일 옵션 외국인 콜 풋 순매수`, `{날짜} 선물옵션 동향 외국인`). Label the
  block `[기사 기반]` and cite outlet + URL. Nothing found → `옵션: 확인된 기사 없음`.
- **Top-10 lists are individual stocks only.** ETFs/ETNs are excluded by the script and summarized
  in one line per list. Entries marked `≈` are estimates (net qty × close) — keep the `≈`.
- **Reasons for a stock's flow need evidence.** Tag each reason `근거:` (news, cite outlet) or
  `추정:` (inferred from theme/sector move or US peer). Search news for the 3–5 largest names on each
  list; infer the rest from theme/sector data in the block. Never present an inference as fact.
- **Theme labels are relative.** On a broad sell-off the "strong" themes can all be negative —
  say "상대 강세" rather than calling a falling theme bullish.
- **The probability and range come from the statistical baseline (data block section `## 0`),
  never from judgment alone.** See "Forecast rules". If section 0 failed, give direction only,
  labelled `주관적 판단`, with no % and no range.
- Direction is binary: 상승 if final P(상승) ≥ 50%, else 하락. No 보합.
- Telegram output: Korean, headers + bullets, no tables. End with `투자 판단 참고용이며 투자 권유가 아닙니다.`

## Procedure

1. Read the injected data block (`~/.hermes/scripts/kospi_morning.py` output). Note `!!` lines and
   the `수집 오류` section — those items are 데이터 수집 실패. `[전일 스냅샷 HH:MM]` tags mark data
   saved after yesterday's close — it is the previous session's data, use it as such. Top-10 lists
   whose header says `전부 ≈` are estimates from per-stock investor days — keep the `≈`.
2. Web-search (Korean sources first): overnight US close wrap (`뉴욕증시 마감 {날짜}`), key US
   macro/earnings events, options flows (rule above), and news for the largest top-10 names.
   Also check today's scheduled events (Korean/US data releases, 옵션 만기일, FOMC, big earnings).
3. Write the report in this order:
   1. 한 줄 결론: 오늘 KOSPI 방향 · 최종 P(상승) (기준 X% → 최종 Y%) · 68% 범위
   2. 전일 예측 채점 (scorecard section; one line on why it hit/missed)
   3. 미국 시장 (indices per session, semis, FX/oil, futures now, top headlines with sources)
      - 금리·신용 (section `8-1`): 국채 3M/2Y/10Y/30Y 수준·변화(bp)와 크기 판정, 커브 형태(불/베어
        스티프닝·플래트닝, 역전 여부), 신용 스프레드 수준(1년 백분위)·변화, 회사채 ETF의 국채 대비
        초과수익, 조합 판정 → **평가·해석 2–3줄**: 무엇이 움직였나(단기물 vs 장기물, 금리 vs 신용) →
        KOSPI 경로(미 금리·달러 → 원/달러·외국인 수급; 장기금리 → 반도체·성장주 밸류에이션; 신용
        확대 → 글로벌 위험회피) → 확률 영향은 `## 0`의 '금리·신용 반영 여부' 줄 그대로(HYG는 기준치에
        이미 반영, 금리·스프레드 변화 자체로는 조정 없음). 'z별 KOSPI' 구간 통계는 "표본 내 집계"로만 인용.
   4. 전일 KOSPI 수급: 현물(외/기/개, 기관 세부, 5·20일 누적, 연속일), 선물(계약, 미결제 변화,
      누적 = 포지션 방향), 옵션 [기사 기반], 프로그램(차익/비차익)
   5. 강세/약세 테마 + 업종 수급, and what they imply for today
   6. 외국인 순매수/순매도 TOP10, 기관 순매수/순매도 TOP10 — `종목 금액 등락률 — 이유(근거/추정)`
   7. 오늘 전망: 통계 기준치(입력값·P(상승)·68% 범위 + 백테스트 한 줄) → 조정(폭과 사유, 없으면
      "조정 없음") → 최종. 그다음 정성 근거 3–5, 반대 시나리오 + trigger, 장중 체크포인트
4. Record the forecast (required, before the final answer):
   `python3 ~/.hermes/scripts/kospi_record.py --p-model 22 --p-final 28 --low -3.59 --high 0.23 --adj-reason "사유" --thesis "핵심 근거 한 줄"`
   Copy `--p-model --low --high` exactly from the `기록용 값` line. If the command fails, say so at the end.

## Forecast rules

Baseline (`~/.hermes/scripts/kospi_model.py`): logistic regression of "KOSPI closes up" on the
overnight SOX, Nasdaq-100 and HYG (US high-yield corporate bond ETF) returns (each ÷ its 20-day vol),
US sessions since the last KRX close compounded; 68% range = OLS on vol-scaled KOSPI return +
empirical residual 16/84 percentiles × current KOSPI 20-day vol. Walk-forward 2019–2026
out-of-sample: direction 65.3% vs 55.5% always-up, Brier 0.2184 vs 0.247 base rate (SOX/NDX only:
64%, 0.2223), well calibrated, 68% range covered 66.6%. HYG passed the feature rule (paired t −3.8,
both 2019–22 and 2023–26 halves). Tested and rejected: foreign spot flow, previous day's KOSPI move,
Treasury moves alone (IEF t −1.0; US10Y change 2023–26 t −0.8), HY-minus-Treasury credit-only
return (t +0.2) — don't re-add any of them as adjustment reasons.

Changing the baseline: add a feature only if it improves walk-forward (refit every 40 days, test from
2019) Brier over the current model, and update the numbers quoted above and in the README. History
for features comes from Naver `api.stock.naver.com/chart/{domestic|foreign}/index/{code}/day`
(daily since 2016) and Daum investor days (`perPage=100`, ~26 pages back to 2016). Night futures have
no history source, so they stay an adjustment input, not a feature.

Adjustment: final P(상승) = baseline ± at most 10pp (the record script rejects more). Adjust only for
information the model can't see, and name it with a number + source:
- KOSPI200 night-futures move (cite level/%; it overlaps with the US move, so adjust only for the gap
  between what night futures imply and what the baseline implies)
- material news/earnings released after the US close or Korea pre-open events (data releases, policy)
- one-off regime facts (circuit-breaker day, holiday gap of several sessions, option expiry)
Never adjust for the US index moves themselves, Treasury yield / curve / credit-spread moves (HYG is
in the model, the rest tested null), foreign flows, "oversold" or general mood. No clear
reason → adjust 0. The range is the model's — don't widen or narrow it. Say plainly that the 68%
range misses about one day in three and tail days (e.g. 2026-10-15 −7.57% vs range −3.6~+0.2) will fall
outside.

## Scoring (done by the script next morning)

Actual close-to-close > 0 = 상승, else 하락. Scored separately for baseline and final: direction hit,
Brier, and 68%-range coverage (target ~68%). After ≥20 records the scorecard says whether the
adjustments helped (Brier final vs baseline). If adjustments keep hurting, adjust less.

Files in `~/.hermes/data/kospi_forecast/` (override with `KOSPI_FORECAST_DIR`), both append-only:
`predictions.jsonl` (last record per date wins; records without `p_model` are ignored) and
`actuals.jsonl` (realised close + `chg` from the Naver daily chart, appended by the collector for every
finished forecast date — the 08:00 run scores yesterday's forecast). The scorecard reads the stored
actuals, so it stays cumulative. `snapshots/<session>.json` (written by `kospi_snapshot.py`, override
with `KOSPI_SNAPSHOT_DIR`) holds program/breadth/sector/theme data for one session; it is git-ignored
and not part of the track record.

Track record: on the publisher's machine the data dir is a clone of the repo's `data` branch with
`git config kospi.push true`; `kospi_record.py` commits both files and pushes after each record (git
errors only warn). `install.sh --with-history` seeds an empty data dir from that branch (no push).

## Data sources (verified 2026-10)

- Naver mobile JSON (`m.stock.naver.com/api/...`, `api.stock.naver.com/...`): KOSPI/KPI200 daily chart
  (`chart/domestic/index/{code}/day`, session source of truth), integration (program, breadth), FUT
  integration, themes, market-cap list, US indices/stocks/futures, market indices (bonds, DXY, FX, oil, gold).
- US rates/credit: Naver `api.stock.naver.com/marketindex/bond/{US3MT|US2YT|US10YT|US30YT}=RR/prices`
  (`pageSize` ≤ 60, ~13 pages ≈ 3 years, rows settle 17:05 ET); ETF daily since 2016 from
  `chart/foreign/item/{HYG|LQD|IEF.O}/day` + `stock/{code}/price`; CNN fear & greed
  `junk_bond_demand` raw = HY − IG yield spread (%p, ~1 year).
- Daum (`finance.daum.net/api/...`, needs `Referer: https://finance.daum.net/`): investor days with
  detail breakdown, futures days (investor contracts + open interest), investor top lists (with
  `fromDate`/`toDate`), per-stock investor days (`/api/investor/days?symbolCode=A{code}`), sectors.

## Pitfalls

- **Before the open, session-day endpoints are already reset — the 08:00 run can't re-fetch yesterday's
  program, breadth, sector or theme data** (integration `programTrendInfo.bizdate` = None, sectors empty,
  theme rates = pre-market). That's why `kospi_snapshot.py` saves them after the close. If the morning
  block says `스냅샷 없음`, check the `KOSPI 장마감 스냅샷` job (`hermes cron list`).
- Take the last-session date from the daily chart (`chart/domestic/index/KOSPI/day`), never from
  `m.stock.naver.com/api/index/KOSPI/price` — before the open that endpoint has dropped the previous
  session, and a stale session date cascades: scoring a day late, US sessions double-counted, top-10
  KRX-close lookups missing.
- Daum's investor top list ignores date params and its window moves (before the open it sums the last
  two sessions; after the close it still shows the previous one). Use it only when
  `fromDate == toDate == last session`; otherwise the script ranks ~370 market-cap stocks from per-stock
  investor days × session VWAP (agrees with the list within ~1%).
- Before the open Daum futures days already list today's row as a copy of the last session — drop rows
  dated after the last session or 5/20-day sums double-count a day.
- Legacy `finance.naver.com/sise/*.naver` pages are discontinued ("더 이상 제공되지 않습니다").
- KRX data portal JSON now returns `LOGOUT` without login; Yahoo chart API rate-limits cloud IPs (429);
  stooq CSV endpoint is gone. Don't fall back to them.
- Daum investor top list is hard-capped at 30 rows including ETFs; the script fills gaps from the
  market-cap universe (estimates, `≈`). Per-stock investor data for a session appears overnight, so an
  evening test run shows the top-10 lists as 수집 실패 — that's expected.
- Naver/Daum per-stock prices and theme rates after 15:30 are KRX+NXT integrated last prices (NXT
  after-market runs to 20:00), not the KRX close (e.g. 207,500 vs KRX 210,000). KRX close comes from
  Daum `/api/quote/A{code}/days` — pick the row whose date equals the last session (before the open the
  newest row is already today's), as the script does for top-10 rows (`KRX종가`).
- Manual test runs of the cron job write to the real prediction log even when told otherwise — and
  the record step pushes it to the public `data` branch. Test the scripts directly with
  `KOSPI_FORECAST_DIR=<scratch> python3 ~/.hermes/scripts/kospi_morning.py --force` (`--force` ignores
  the holiday gate); if a test record lands in the real log anyway, `git revert` it in the data dir and push.
- `hermes cron run <id>` executes in the calling shell's environment: an exported `KOSPI_FORECAST_DIR`
  / `KOSPI_SNAPSHOT_DIR` from testing redirects the real job's writes to scratch while it reports
  "succeeded". `unset` them before triggering a job, then check the file landed in the real data dir.
- Never score from a windowed endpoint: `m.stock.naver.com/api/index/KOSPI/price` caps at ~60 rows
  (`pageSize>60` returns non-JSON), so anything older silently drops out of a "cumulative" stat.
  Store the outcome once, or use the `chart/.../day` series for long ranges.
- Under a Hermes profile (`HERMES_HOME=~/.hermes/profiles/<p>`), install.sh copies scripts to the
  profile's `scripts/`, but the cron prompt calls `~/.hermes/scripts/kospi_record.py` — also copy
  the scripts there after every install.
- Naver `.RUT` is unsupported (409). Treasury ETF code is `IEF.O` (`IEF` → 409/empty); `HYG`, `LQD` work bare.
- FRED (`fred.stlouisfed.org`, ICE BofA OAS, DGS10) silently drops connections from this cloud IP;
  `api.stlouisfed.org` works only with an API key. Hence Naver yields + CNN spread + ETF proxies.
- HYG/LQD chart closes are not dividend-adjusted: each monthly ex-dividend day shows ≈ −0.3~0.5%
  that isn't a credit move. Check the date before calling a small HYG drop a credit signal.
- US market-index quotes (bonds, DXY, futures) are live at fetch time, not the US close — say "현재".
- Holiday table `KRX_HOLIDAYS` in the script runs through 2027; the script warns when it expires.
  KRX can add ad-hoc closures (elections, temporary holidays) — add them when announced.
