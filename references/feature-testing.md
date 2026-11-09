# Testing a candidate feature for the baseline model

Use when the user asks to add a new input (rates, credit, flows, FX, …) to the forecast, or when you
are tempted to justify a probability adjustment with a market variable. Shipping it as *display +
interpretation* is always allowed; putting it in `## 0` or using it as an adjustment reason needs this test.

## 1. Find a long history first

- The baseline trains from 2016 and tests walk-forward from 2019. A series with only 1–3 years of
  history (Naver bond yields ≈ 3y, CNN junk-bond spread ≈ 1y) can only be tested on a short window
  against a model refit on that same window — weak evidence.
- Look for a long-history tradable proxy on the same Naver chart endpoint
  (`api.stock.naver.com/chart/foreign/item/{code}/day?startDateTime=201601010000…`, US ETFs since
  2016: HYG, LQD, `IEF.O`, …). A proxy that exists since 2016 gets the full 1,800-day test.
- Probe codes with `stock/{code}/basic` first — some need the exchange suffix (`IEF.O`); a wrong
  code returns 409 or an empty list, not an error message.

## 2. Build the feature exactly like the existing ones

Overnight move over the US sessions in `[last KRX session, T)` (the same session list SOX uses),
divided by the series' own 20-day stdev before `P`. Drop a training row if the candidate is missing
a session SOX has (data lag) instead of filling 0. Keep the feature-construction code in
`kospi_model.build_rows` so the backtest and the live row use one path.

## 3. The test (scratch kernel, `import kospi_model as km`)

- Same rows, two models: current features vs current + candidate. Walk-forward, refit every 40,
  OOS from 2019 (`km.fit_logit`, `km._sig`).
- Per OOS day compute `d = (p_new − y)² − (p_cur − y)²`; report mean(d) and `t = mean/(sd/√n)`.
  A Brier gain without a paired t is noise — a −0.001 gain had t −0.8.
- Split the OOS period in halves (2019–22, 2023–26); require the gain in both.
- Count the specifications you tried. With ~10 variants, ask for |t| ≳ 3 overall, not 2.
- Also check the 68% range coverage of the new model (the backtest prints it) — it must stay ≈ 65–68%.

## 4. Ship

- Accepted: add to `FEATURES`, keep a fallback to the previous feature set when the series fails to
  load (print a `!!` line), update the backtest numbers in SKILL.md "Forecast rules" and the README,
  and list it in the model docstring with its t.
- Rejected: list it under "tested and rejected" in SKILL.md with its t, add it to the "never adjust
  for" sentence, and keep it only as a displayed/interpreted block.
- Descriptive bucket stats (KOSPI up-rate by the candidate's z bucket) may be printed for
  interpretation, labelled in-sample and "not a probability basis" — they mix in the SOX/NDX effect.
