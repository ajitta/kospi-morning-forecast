#!/usr/bin/env python3
"""Statistical baseline for the KOSPI morning forecast (stdlib only).

Target  : KOSPI close-to-close % change on the forecast day T.
Features: overnight US moves that closed between the last KRX close and T's open
          - SOX return  / SOX 20-day daily-return stdev
          - NDX return  / NDX 20-day daily-return stdev
          - HYG return  / HYG 20-day daily-return stdev  (US high-yield corporate bond ETF = rates + credit)
          (US sessions dated in [last KRX session, T) are compounded, so KRX holiday gaps are summed.)
Models  : logistic regression -> P(up);  OLS on (KOSPI return / KOSPI 20d stdev) + empirical
          residual 16/84 percentiles -> 68% range, rescaled by current KOSPI 20d stdev.
Validation: walk-forward (refit every 40 days) from 2019-01-01, out-of-sample only.

Foreign spot flow and previous-day KOSPI return were tested (2019-2026 walk-forward) and added
no out-of-sample skill, so they are not in the model.
Rates/credit (tested 2026-10, same walk-forward, paired t on squared-error difference):
  + HYG z            Brier 0.2223 -> 0.2184 (t -3.8; 2019-22 t -3.1, 2023-26 t -2.5)  -> in the model
  + IEF z (rates)    0.2223 -> 0.2218 (t -1.0)   + HYG-IEF z (credit only) 0.2224 (t +0.2)
  + US10Y change z (Naver yields, 2023-26 only)  0.2145 -> 0.2135 (t -0.8)       -> not in the model
"""
import bisect
import json
import math
import statistics as st
import urllib.request
from datetime import date

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
FEATURES = ["sox", "ndx", "hyg"]
FEATURE_LABEL = {"sox": "SOX", "ndx": "NDX", "hyg": "HYG"}
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
    for code, key in (("HYG", "HYG"), ("IEF.O", "IEF")):  # US HY corporate bond ETF (feature), 7-10y Treasury ETF (display)
        try:
            h = _get(f"https://api.stock.naver.com/chart/foreign/item/{code}/day?startDateTime=201601010000&endDateTime={end}")
            m = {r["localDate"]: r["closePrice"] for r in h}
            try:
                for r in _get(f"https://api.stock.naver.com/stock/{code}/price?page=1&pageSize=10"):
                    ts = r["localTradedAt"]
                    if ts[11:16] >= "16:00":
                        m[ts[:10].replace("-", "")] = _num(r["closePrice"])
            except Exception:  # noqa: BLE001
                pass
            if m:
                out[key] = m
        except Exception:  # noqa: BLE001 — model falls back to SOX/NDX only
            pass
    # a KOSPI row dated today before the close is not a finished session
    if drop_today:
        out["KOSPI"].pop(today.strftime("%Y%m%d"), None)
    return out


def _rets(m):
    ds = sorted(m)
    return ds, {ds[i]: (m[ds[i]] / m[ds[i - 1]] - 1) * 100 for i in range(1, len(ds))}


def build_rows(series, target_date: str, use_hyg: bool = True):
    kd, kr = _rets(series["KOSPI"])
    sd, sr = _rets(series["SOX"])
    nd, nr = _rets(series["NDX"])
    sd = [d for d in sd if d in sr]
    nd = [d for d in nd if d in nr]
    use_hyg = use_hyg and "HYG" in series
    hd, hr = _rets(series["HYG"]) if use_hyg else ([], {})
    hd = [d for d in hd if d in hr]
    idd, ir = _rets(series["IEF"]) if "IEF" in series else ([], {})
    idd = [d for d in idd if d in ir]

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
        r = {"T": T, "P": P, "y": y, "sox": sox, "ndx": ndx, "n_us": ns, "us_sessions": sess,
             "kvol": st.pstdev(kpast), "x": [1.0, sox / svol, ndx / nvol]}
        if use_hyg:
            hyg, nh, _ = overnight(hr, hd, P, T)
            hvol = vol_before(hr, hd, P)
            if nh != ns or not hvol:  # HYG session missing (data lag) -> unusable row
                return None
            r["hyg"] = hyg
            r["x"].append(hyg / hvol)
        if idd:
            ief, ni, _ = overnight(ir, idd, P, T)
            ivol = vol_before(ir, idd, P)
            if ni == ns and ivol:
                r["ief"], r["ief_z"] = ief, ief / ivol
        return r

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


def _bucket_line(hist, key, label, cur_z):
    """In-sample descriptive stat: KOSPI up-rate / mean by the overnight z bucket of `key`."""
    rows = [r for r in hist if r.get(key) is not None]
    if len(rows) < 200:
        return None
    def stat(sel):
        n = len(sel)
        return f"상승 {sum(r['y'] > 0 for r in sel) / n * 100:.0f}% 평균 {st.mean(r['y'] for r in sel):+.2f}% (n={n})" if n else "n=0"
    lo = [r for r in rows if r[key] <= -1]
    mid = [r for r in rows if -1 < r[key] < 1]
    hi = [r for r in rows if r[key] >= 1]
    where = "z≤-1" if cur_z is not None and cur_z <= -1 else "z≥+1" if cur_z is not None and cur_z >= 1 else "-1<z<+1"
    return (f"  - {label}: z≤-1 {stat(lo)} | -1<z<+1 {stat(mid)} | z≥+1 {stat(hi)} → 오늘 구간 {where}")


def run(today: date, target: str = None, drop_today: bool = True):
    target = target or today.strftime("%Y%m%d")
    series = load_series(today, drop_today)
    hist, cur = build_rows(series, target)
    note = None
    if "HYG" not in series:
        note = "  !! HYG 데이터 수집 실패 → SOX·NDX 2변수 모델로 계산(백테스트 수치는 아래 값 기준)"
    elif cur is None or "hyg" not in cur:
        note = "  !! 오늘 행의 HYG 세션이 SOX와 맞지 않음(데이터 지연) → SOX·NDX 2변수 모델로 계산"
        hist, cur = build_rows(series, target, use_hyg=False)
    if cur is None:
        return "- 통계 기준치: 계산 실패(입력 데이터 부족)"
    model = fit(hist)
    p, lo, hi, mid = predict(model, cur)
    bt = backtest(hist)
    w = model[0]
    feats = ["sox", "ndx"] + (["hyg"] if "hyg" in cur else [])
    L = []
    L.append(f"- 대상일 {target} | 직전 KRX 세션 {cur['P']} | 반영 미국 세션 {cur['n_us']}개 ({', '.join(cur['us_sessions']) or '없음'})")
    if cur["n_us"] == 0:
        L.append("  !! 반영할 미국 세션 없음(미국 휴장/데이터 지연) → 기준치는 사실상 과거 평균 상승확률. 신뢰도 낮음")
    if note:
        L.append(note)
    inp = f"- 입력: SOX {cur['sox']:+.2f}% (z {cur['x'][1]:+.2f}), 나스닥100 {cur['ndx']:+.2f}% (z {cur['x'][2]:+.2f})"
    if "hyg" in cur:
        inp += f", HYG(미 하이일드 회사채 ETF) {cur['hyg']:+.2f}% (z {cur['x'][3]:+.2f})"
    L.append(inp + f", KOSPI 20일 일간변동성 {cur['kvol']:.2f}%")
    L.append(f"- **기준 상승확률 P(up) = {p*100:.0f}%** → 기준 방향 {'상승' if p >= 0.5 else '하락'}")
    L.append(f"- **기준 68% 범위 = {lo:+.2f}% ~ {hi:+.2f}%** (중앙 기대치 {mid:+.2f}%)")
    L.append(f"- 모델: 로지스틱 계수 절편 {w[0]:+.3f}, " + ", ".join(f"{FEATURE_LABEL[f]} {c:+.3f}" for f, c in zip(feats, w[1:]))
             + f" | 학습 {hist[0]['T']}~{hist[-1]['T']} {len(hist)}일")
    if bt:
        L.append(f"- 워크포워드 검증({bt['from']}~{bt['to']}, 표본외 {bt['n']}일): 방향 적중 {bt['acc']*100:.1f}% "
                 f"(항상 '상승' 찍기 {bt['always_up_acc']*100:.1f}%), Brier {bt['brier']:.4f} (기저율 {bt['brier_base']:.4f}), "
                 f"68% 범위 실제 포함률 {bt['coverage68']*100:.1f}%")
        L.append("- 보정(예측확률 구간: 평균예측→실제상승비율): " + "; ".join(
            f"{a:.2f}-{min(b,1):.2f} n={n} {mp:.2f}→{my:.2f}" for a, b, n, mp, my in bt["calib"]))
    # rates / credit: what is already in the probability, and what the history says (descriptive, in-sample)
    L.append("- 금리·신용 반영 여부: HYG(하이일드 회사채 = 금리+신용)는 모델 입력 — 워크포워드 Brier 0.2223→0.2184 개선(t -3.8)으로 채택. "
             "국채 금리만(IEF·미10년 변화)은 추가 개선이 유의하지 않아(t -1.0/-0.8) 미반영 → 금리·스프레드 변화를 이유로 확률을 조정하지 말 것")
    for key, label, cz in (("hyg_z", "HYG 하룻밤 z별 KOSPI 당일", cur["x"][3] if "hyg" in cur else None),
                           ("ief_z", "IEF(미 7-10년 국채, +는 금리 하락) z별 KOSPI 당일", cur.get("ief_z"))):
        if key == "hyg_z":
            for r in hist:
                if len(r["x"]) > 3:
                    r["hyg_z"] = r["x"][3]
        line = _bucket_line(hist, key, label, cz)
        if line:
            if key == "ief_z" and cz is not None:
                line += f" (오늘 IEF {cur['ief']:+.2f}%, z {cz:+.2f})"
            L.append(line)
    L.append("  (위 구간 통계는 2016년 이후 표본 내 단순 집계 — SOX·NDX 동반 효과가 섞여 있으니 해석 참고용, 확률 근거 아님)")
    L.append(f"- 기록용 값: --p-model {p*100:.0f} --low {lo:.2f} --high {hi:.2f}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    print(run(now.date(), sys.argv[1] if len(sys.argv) > 1 else None, drop_today=now.hour < 16))
