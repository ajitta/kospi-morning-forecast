#!/usr/bin/env python3
"""Statistical baseline for the KOSPI morning forecast (stdlib only).

Target  : KOSPI close-to-close % change on the forecast day T.
Features: overnight US moves that closed between the last KRX close and T's open
          - SOX return  / SOX 20-day daily-return stdev
          - NDX return  / NDX 20-day daily-return stdev
          (US sessions dated in [last KRX session, T) are compounded, so KRX holiday gaps are summed.)
Models  : logistic regression -> P(up);  OLS on (KOSPI return / KOSPI 20d stdev) + empirical
          residual 16/84 percentiles -> 68% range, rescaled by current KOSPI 20d stdev.
Validation: walk-forward (refit every 40 days) from 2019-01-01, out-of-sample only.

Foreign spot flow and previous-day KOSPI return were tested (2019-2026 walk-forward) and added
no out-of-sample skill, so they are not in the model.
"""
import bisect
import json
import math
import statistics as st
import urllib.request
from datetime import date

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
FEATURES = ["sox", "ndx"]
REFIT = 40
OOS_START = "20190101"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _num(s):
    return float(str(s).replace(",", ""))


def load_series(today: date, drop_today: bool = True):
    end = today.strftime("%Y%m%d") + "2359"
    k = _get(f"https://api.stock.naver.com/chart/domestic/index/KOSPI/day?startDateTime=201601010000&endDateTime={end}")
    out = {"KOSPI": {r["localDate"]: r["closePrice"] for r in k}}
    for code in (".SOX", ".NDX"):
        h = _get(f"https://api.stock.naver.com/chart/foreign/index/{code}/day?startDateTime=201601010000&endDateTime={end}")
        m = {r["localDate"]: r["closePrice"] for r in h}
        # chart can lag a session; merge the most recent rows from the price endpoint
        try:
            for r in _get(f"https://api.stock.naver.com/index/{code}/price?page=1&pageSize=10"):
                ts = r["localTradedAt"]  # e.g. 2026-10-14T16:36:08-04:00; skip in-progress sessions
                if ts[11:16] >= "16:00":
                    m[ts[:10].replace("-", "")] = _num(r["closePrice"])
        except Exception:  # noqa: BLE001
            pass
        out[code.strip(".")] = m
    # a KOSPI row dated today before the close is not a finished session
    if drop_today:
        out["KOSPI"].pop(today.strftime("%Y%m%d"), None)
    return out


def _rets(m):
    ds = sorted(m)
    return ds, {ds[i]: (m[ds[i]] / m[ds[i - 1]] - 1) * 100 for i in range(1, len(ds))}


def build_rows(series, target_date: str):
    kd, kr = _rets(series["KOSPI"])
    sd, sr = _rets(series["SOX"])
    nd, nr = _rets(series["NDX"])
    sd = [d for d in sd if d in sr]
    nd = [d for d in nd if d in nr]

    def overnight(rmap, dates, lo, hi):
        i, j = bisect.bisect_left(dates, lo), bisect.bisect_left(dates, hi)
        c = 1.0
        for d in dates[i:j]:
            c *= 1 + rmap[d] / 100
        return (c - 1) * 100, j - i, dates[i:j]

    def vol_before(rmap, dates, d, n=20):
        j = bisect.bisect_left(dates, d)
        past = [rmap[x] for x in dates[max(0, j - n):j]]
        return st.pstdev(past) if len(past) >= 10 else None

    def row(T, P, y):
        sox, ns, sess = overnight(sr, sd, P, T)
        ndx, _, _ = overnight(nr, nd, P, T)
        t = bisect.bisect_left(kd, T)
        kpast = [kr[d] for d in kd[max(1, t - 20):t] if d in kr]
        svol, nvol = vol_before(sr, sd, P), vol_before(nr, nd, P)
        if len(kpast) < 15 or not svol or not nvol:
            return None
        return {"T": T, "P": P, "y": y, "sox": sox, "ndx": ndx, "n_us": ns, "us_sessions": sess,
                "kvol": st.pstdev(kpast), "x": [1.0, sox / svol, ndx / nvol]}

    hist = []
    for i in range(22, len(kd)):
        T, P = kd[i], kd[i - 1]
        if T not in kr:
            continue
        r = row(T, P, kr[T])
        if r and r["n_us"] > 0:
            hist.append(r)
    # forecast row: previous session = last finished KRX day before target
    prevs = [d for d in kd if d < target_date]
    cur = row(target_date, prevs[-1], None) if prevs else None
    if cur is not None:
        # kvol for today's row must include the last finished session
        t = len(prevs)
        cur["kvol"] = st.pstdev([kr[d] for d in kd[max(1, t - 20):t] if d in kr])
    return hist, cur


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda i: abs(M[i][c]))
        M[c], M[p] = M[p], M[c]
        for i in range(n):
            if i != c and M[c][c] != 0:
                f = M[i][c] / M[c][c]
                for j in range(c, n + 1):
                    M[i][j] -= f * M[c][j]
    return [M[i][n] / M[i][i] for i in range(n)]


def _sig(z):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def fit_logit(X, Y, l2=1.0, iters=10):
    k = len(X[0])
    w = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for x, y in zip(X, Y):
            p = _sig(sum(a * b for a, b in zip(w, x)))
            for i in range(k):
                g[i] += (p - y) * x[i]
                for j in range(k):
                    H[i][j] += p * (1 - p) * x[i] * x[j]
        for i in range(1, k):
            g[i] += l2 * w[i]
            H[i][i] += l2
        d = _solve(H, g)
        w = [a - b for a, b in zip(w, d)]
    return w


def fit_range(X, Z):
    k = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(k)] for i in range(k)]
    b = [sum(x[i] * z for x, z in zip(X, Z)) for i in range(k)]
    beta = _solve(A, b)
    res = sorted(z - sum(a * c for a, c in zip(beta, x)) for x, z in zip(X, Z))

    def q(p):
        i = p * (len(res) - 1)
        lo = int(i)
        hi = min(lo + 1, len(res) - 1)
        return res[lo] * (1 - (i - lo)) + res[hi] * (i - lo)
    return beta, q(0.16), q(0.84)


def fit(hist):
    X = [r["x"] for r in hist]
    w = fit_logit(X, [1 if r["y"] > 0 else 0 for r in hist])
    beta, qlo, qhi = fit_range(X, [r["y"] / r["kvol"] for r in hist])
    return w, beta, qlo, qhi


def predict(model, r):
    w, beta, qlo, qhi = model
    p = _sig(sum(a * b for a, b in zip(w, r["x"])))
    m = sum(a * b for a, b in zip(beta, r["x"]))
    return p, (m + qlo) * r["kvol"], (m + qhi) * r["kvol"], m * r["kvol"]


def backtest(hist):
    start = next((i for i, r in enumerate(hist) if r["T"] >= OOS_START), None)
    if start is None:
        return None
    P, Y, cov = [], [], []
    model = None
    for i in range(start, len(hist)):
        if model is None or (i - start) % REFIT == 0:
            model = fit(hist[:i])
        p, lo, hi, _ = predict(model, hist[i])
        y = hist[i]["y"]
        P.append(p)
        Y.append(1 if y > 0 else 0)
        cov.append(lo <= y <= hi)
    n = len(P)
    base = sum(Y) / n
    return {
        "n": n, "from": hist[start]["T"], "to": hist[-1]["T"],
        "acc": sum((p > 0.5) == (y == 1) for p, y in zip(P, Y)) / n,
        "always_up_acc": base,
        "brier": sum((p - y) ** 2 for p, y in zip(P, Y)) / n,
        "brier_base": sum((base - y) ** 2 for y in Y) / n,
        "coverage68": sum(cov) / n,
        "calib": _calib(P, Y),
    }


def _calib(P, Y):
    out = []
    for lo, hi in ((0, .35), (.35, .45), (.45, .55), (.55, .65), (.65, .75), (.75, 1.01)):
        idx = [i for i, p in enumerate(P) if lo <= p < hi]
        if idx:
            out.append((lo, hi, len(idx), st.mean(P[i] for i in idx), st.mean(Y[i] for i in idx)))
    return out


def run(today: date, target: str = None, drop_today: bool = True):
    target = target or today.strftime("%Y%m%d")
    series = load_series(today, drop_today)
    hist, cur = build_rows(series, target)
    if cur is None:
        return "- 통계 기준치: 계산 실패(입력 데이터 부족)"
    model = fit(hist)
    p, lo, hi, mid = predict(model, cur)
    bt = backtest(hist)
    w = model[0]
    L = []
    L.append(f"- 대상일 {target} | 직전 KRX 세션 {cur['P']} | 반영 미국 세션 {cur['n_us']}개 ({', '.join(cur['us_sessions']) or '없음'})")
    if cur["n_us"] == 0:
        L.append("  !! 반영할 미국 세션 없음(미국 휴장/데이터 지연) → 기준치는 사실상 과거 평균 상승확률. 신뢰도 낮음")
    L.append(f"- 입력: SOX {cur['sox']:+.2f}% (z {cur['x'][1]:+.2f}), 나스닥100 {cur['ndx']:+.2f}% (z {cur['x'][2]:+.2f}), KOSPI 20일 일간변동성 {cur['kvol']:.2f}%")
    L.append(f"- **기준 상승확률 P(up) = {p*100:.0f}%** → 기준 방향 {'상승' if p >= 0.5 else '하락'}")
    L.append(f"- **기준 68% 범위 = {lo:+.2f}% ~ {hi:+.2f}%** (중앙 기대치 {mid:+.2f}%)")
    L.append(f"- 모델: 로지스틱 계수 절편 {w[0]:+.3f}, SOX {w[1]:+.3f}, NDX {w[2]:+.3f} | 학습 {hist[0]['T']}~{hist[-1]['T']} {len(hist)}일")
    if bt:
        L.append(f"- 워크포워드 검증({bt['from']}~{bt['to']}, 표본외 {bt['n']}일): 방향 적중 {bt['acc']*100:.1f}% "
                 f"(항상 '상승' 찍기 {bt['always_up_acc']*100:.1f}%), Brier {bt['brier']:.4f} (기저율 {bt['brier_base']:.4f}), "
                 f"68% 범위 실제 포함률 {bt['coverage68']*100:.1f}%")
        L.append("- 보정(예측확률 구간: 평균예측→실제상승비율): " + "; ".join(
            f"{a:.2f}-{min(b,1):.2f} n={n} {mp:.2f}→{my:.2f}" for a, b, n, mp, my in bt["calib"]))
    L.append(f"- 기록용 값: --p-model {p*100:.0f} --low {lo:.2f} --high {hi:.2f}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    print(run(now.date(), sys.argv[1] if len(sys.argv) > 1 else None, drop_today=now.hour < 16))
