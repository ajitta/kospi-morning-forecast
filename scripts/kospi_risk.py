#!/usr/bin/env python3
"""Volatility / safe-haven / crypto block for the KOSPI morning briefing (stdlib only).

Prints section "8-4" of the data block: VIX (S&P 500 30-day implied vol), VXN (Nasdaq-100), the VIX
term structure (VIX / VIX3M), the VXN-VIX gap (tech-specific fear premium), COMEX gold and Bitcoin —
each with its overnight change (US sessions after the last KRX close; Bitcoin: since 15:30 KST of
that session, it trades 24/7), a size label (z vs recent daily moves), 5/20-day changes, 1-year
percentile, and a trend block (10-point path, streak, 20-day range position, 20/60-day moving
averages). Labels are descriptive. Whether any of it moves the KOSPI probability is settled by the
walk-forward test quoted in kospi_model's docstring (VIX/gold/BTC: tested 2026-11, not in the model).

Sources (verified 2026-11):
- VIX: Naver `api.stock.naver.com/chart/foreign/index/.VIX/day` (daily since 2016) + `index/.VIX/price`
  (newest sessions; the chart can lag a session).
- VXN, VIX3M: not on Naver (`.VXN` -> 409). CNBC `ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol=.VXN`
  (~2 years daily; lags one session) + `quote.cnbc.com/.../restQuote/...?symbols=.VXN|.VIX3M` (last close).
  cdn.cboe.com CSVs are Cloudflare-blocked from cloud hosts, Yahoo 429s, FRED CSV hangs.
- Gold: Naver `marketindex/metals/GCcv1/prices` (front-month COMEX, daily, ~3 years) + live quote.
- Bitcoin: Binance `api/v3/klines?symbol=BTCUSDT` (1d history, 1m candle at the KRX close) + ticker;
  Coinbase `api.exchange.coinbase.com/products/BTC-USD/...` as fallback.
"""
import bisect
import json
import statistics as st
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
TREND_N = 10
CNBC_Q = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols={s}"
          "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _num(s):
    return float(str(s).replace(",", "").replace("%", "").replace("+", ""))


# ---------------------------------------------------------------- loaders -> {YYYY-MM-DD: close}
def _vix():
    start = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d") + "0000"
    end = datetime.now().strftime("%Y%m%d") + "2359"
    m = {}
    for r in _get(f"https://api.stock.naver.com/chart/foreign/index/.VIX/day?startDateTime={start}&endDateTime={end}"):
        d = r["localDate"]
        m[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = float(r["closePrice"])
    try:
        for r in _get("https://api.stock.naver.com/index/.VIX/price?page=1&pageSize=10"):
            ts = r["localTradedAt"]
            if ts[11:16] >= "16:00":  # finished session only
                m[ts[:10]] = _num(r["closePrice"])
    except Exception:  # noqa: BLE001
        pass
    return m


def _cnbc(sym):
    """CNBC daily history (~2y, lags one session) + the latest finished close from the quote API."""
    bars = _get(f"https://ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol={sym}")["barData"]["priceBars"]
    m = {}
    for b in bars:
        t = b["tradeTime"]
        m[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = float(b["close"])
    q = _get(CNBC_Q.format(s=sym))["FormattedQuoteResult"]["FormattedQuote"][0]
    lt = q.get("last_time") or ""
    if lt[11:16] >= "16:00" and q.get("last"):
        m[lt[:10]] = _num(q["last"])
    return m


def _gold():
    m = {}
    for pg in range(1, 6):
        rows = _get(f"https://api.stock.naver.com/marketindex/metals/GCcv1/prices?page={pg}&pageSize=60")
        if not rows:
            break
        for r in rows:
            m[r["localTradedAt"][:10]] = _num(r["closePrice"])
    live = None
    try:
        q = _get("https://api.stock.naver.com/marketindex/metals/GCcv1")
        live = (_num(q["closePrice"]), q["localTradedAt"])
    except Exception:  # noqa: BLE001
        pass
    return m, live


def _btc(krx_close_utc):
    """-> (daily closes {UTC date: close}, now price, price at the KRX close, source)."""
    try:
        rows = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=400")
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        m = {datetime.fromtimestamp(r[0] / 1000, timezone.utc).strftime("%Y-%m-%d"): float(r[4])
             for r in rows if r[6] < now_ms}  # drop today's in-progress candle (current price is added separately)
        now = float(_get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")["price"])
        ms = int(krx_close_utc.timestamp() * 1000)
        k = _get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={ms}&limit=1")
        base = float(k[0][1]) if k else None
        return m, now, base, "Binance BTCUSDT"
    except Exception:  # noqa: BLE001 — Binance blocks some regions; Coinbase fallback
        m = {}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for r in _get("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"):
            if datetime.fromtimestamp(r[0], timezone.utc).strftime("%Y-%m-%d") < today:
                m[datetime.fromtimestamp(r[0], timezone.utc).strftime("%Y-%m-%d")] = float(r[4])
        now = float(_get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")["price"])
        s = krx_close_utc.isoformat()
        e = (krx_close_utc + timedelta(minutes=5)).isoformat()
        k = _get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start={s}&end={e}")
        base = float(k[-1][3]) if k else None  # [time, low, high, open, close, vol], newest first
        return m, now, base, "Coinbase BTC-USD"


# ---------------------------------------------------------------- stats
def _streak(v):
    n, sign = 0, 0
    for k in range(len(v) - 1, 0, -1):
        d = v[k] - v[k - 1]
        sg = (d > 0) - (d < 0)
        if sg == 0 or (sign and sg != sign):
            break
        sign, n = sg, n + 1
    return (n, "상승" if sign > 0 else "하락") if n else (0, "")


def _size(z):
    if z is None:
        return "크기 판정 불가"
    a = abs(z)
    return "통상 범위" if a < 1 else "다소 큰 변동(대략 상위 30%)" if a < 2 else "큰 변동(상위 5% 안팎)"


def _pct_1y(m, last):
    ds = sorted(m)
    cut = (datetime.strptime(ds[-1], "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    lv = sorted(m[d] for d in ds if d >= cut)
    return bisect.bisect_right(lv, last) / len(lv) * 100, len(lv)


def _move(m, since, pct):
    """Change over the sessions dated >= since. pct=True: % return, else level change.
    z = change / (stdev of the previous 60 daily changes × √n sessions)."""
    ds = sorted(m)
    i = bisect.bisect_left(ds, since)
    if i == 0 or i == len(ds):
        return None
    f = (lambda a, b: (b / a - 1) * 100) if pct else (lambda a, b: b - a)
    steps = [f(m[ds[k - 1]], m[ds[k]]) for k in range(1, i)][-60:]
    sd = st.pstdev(steps) if len(steps) >= 20 else None
    n = len(ds) - i
    chg = f(m[ds[i - 1]], m[ds[-1]])
    out = {"last": m[ds[-1]], "date": ds[-1], "base": m[ds[i - 1]], "n": n, "chg": chg,
           "z": chg / (sd * n ** 0.5) if sd else None, "pchg": (m[ds[-1]] / m[ds[i - 1]] - 1) * 100}
    for k in (5, 20):
        if len(ds) > k:
            out[f"d{k}"] = f(m[ds[-1 - k]], m[ds[-1]])
    return out


def _trend(m, fmt, last=None):
    """10-point path, streak, 20-day range position, price vs 20/60-day MA (+ MA20 slope over 5 days)."""
    ds = sorted(m)
    v = [m[d] for d in ds]
    if last is not None:
        v = v + [last]
    if len(v) < 21:
        return "추세 계산 불가(이력 부족)"
    path = ", ".join(fmt(x) for x in v[-TREND_N:])
    n, dirn = _streak(v)
    w = v[-20:]
    hi, lo = max(w), min(w)
    pos = (v[-1] - lo) / (hi - lo) * 100 if hi > lo else 50
    ma20 = st.mean(v[-20:])
    ma20_5 = st.mean(v[-25:-5]) if len(v) >= 25 else None
    ma60 = st.mean(v[-60:]) if len(v) >= 60 else None
    s = (f"{path} | " + (f"{n}일 연속 {dirn} | " if n >= 2 else "")
         + f"20일 범위 {fmt(lo)}~{fmt(hi)}, 현재 위치 {pos:.0f}%"
         + (" (20일 고점)" if v[-1] >= hi else " (20일 저점)" if v[-1] <= lo else "")
         + f" | 20일선 {fmt(ma20)} {'위' if v[-1] > ma20 else '아래'}")
    if ma20_5:
        s += f"(20일선 5일 기울기 {(ma20 / ma20_5 - 1) * 100:+.1f}%)"
    if ma60:
        s += f", 60일선 {fmt(ma60)} {'위' if v[-1] > ma60 else '아래'}"
        s += " → 정배열(단기>중기)" if ma20 > ma60 else " → 역배열(단기<중기)"
    return s


def _vix_zone(x):
    return ("낮음(15 미만, 안주)" if x < 15 else "보통(15~20)" if x < 20 else "높음(20~30, 경계)" if x < 30
            else "매우 높음(30 이상, 공포)")


def _f2(x):
    return f"{x:.2f}"


def _f0(x):
    return f"{x:,.0f}"


def _f1(x):
    return f"{x:,.1f}"


# ---------------------------------------------------------------- section
def section(last_session, now=None):
    """last_session: 'YYYY-MM-DD' of the last KRX close. Returns the printed block (str)."""
    now = now or datetime.now(timezone.utc)
    errors = []

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {type(e).__name__}: {e}")
            return None

    krx_close = datetime.fromisoformat(last_session + "T06:30:00+00:00")  # 15:30 KST
    with ThreadPoolExecutor(6) as ex:
        fv = ex.submit(safe, "VIX", _vix)
        fx = ex.submit(safe, "VXN", lambda: _cnbc(".VXN"))
        f3 = ex.submit(safe, "VIX3M", lambda: _cnbc(".VIX3M"))
        fg = ex.submit(safe, "금", _gold)
        fb = ex.submit(safe, "비트코인", lambda: _btc(krx_close))
    VIX, VXN, V3M, G, B = fv.result(), fx.result(), f3.result(), fg.result(), fb.result()

    L = [f"\n## 8-4. 변동성·안전자산·가상자산 (VIX·VXN·금·비트코인; {last_session} KRX 마감 이후 기준 변화)"]
    sig = {}

    # --- implied vol
    for name, m, desc in (("VIX", VIX, "S&P500 30일 내재변동성"), ("VXN", VXN, "나스닥100 30일 내재변동성")):
        r = _move(m, last_session, pct=False) if m else None
        if not r:
            if not m:
                L.append(f"- {name}: 데이터 수집 실패")
            else:
                d = sorted(m)[-1]
                L.append(f"- {name}({desc}): {m[d]:.2f} (기준 {d}) — {last_session} 이후 마감된 미국 세션 없음(변화 없음) | "
                         f"구간 {_vix_zone(m[d])}")
                L.append(f"  - 추세: {_trend(m, _f2)}")
            continue
        p1, nlv = _pct_1y(m, r["last"])
        sig[name] = r
        z = f", z {r['z']:+.1f} {_size(r['z'])}" if r["z"] is not None else ""
        L.append(f"- {name}({desc}): {r['last']:.2f} (기준 {r['date']}, 반영 {r['n']}세션) | 변화 {r['chg']:+.2f}p "
                 f"({r['pchg']:+.1f}%{z}) | 5일 {r.get('d5', 0):+.2f}p, 20일 {r.get('d20', 0):+.2f}p | "
                 f"1년 백분위 {p1:.0f}% | 구간 {_vix_zone(r['last'])}")
        L.append(f"  - 추세: {_trend(m, _f2)}")
    if VIX and VXN:
        common = sorted(set(VIX) & set(VXN))
        gap = {d: VXN[d] - VIX[d] for d in common}
        if len(gap) > 25:
            g = [gap[d] for d in common]
            p1, _ = _pct_1y(gap, g[-1])
            L.append(f"- VXN−VIX 격차(기술주 고유 불안 프리미엄): {g[-1]:+.2f}p | 최근 {TREND_N}일 "
                     + ", ".join(f"{x:+.1f}" for x in g[-TREND_N:])
                     + f" | 20일 평균 {st.mean(g[-20:]):+.2f}p | 1년 백분위 {p1:.0f}%"
                     + (" → 기술주 쪽 불안이 시장 전체보다 두드러짐" if p1 >= 80 else
                        " → 기술주 프리미엄 평소보다 작음" if p1 <= 20 else ""))
    if VIX and V3M:
        common = sorted(set(VIX) & set(V3M))
        ratio = {d: VIX[d] / V3M[d] for d in common if V3M[d]}
        if len(ratio) > 25:
            rv = [ratio[d] for d in common]
            x = rv[-1]
            p1, _ = _pct_1y(ratio, x)
            shape = ("백워데이션(단기 공포 > 3개월 — 급성 스트레스)" if x > 1.0 else
                     "평탄(0.9~1.0, 경계)" if x >= 0.9 else "콘탱고(정상 — 단기 불안 낮음)")
            L.append(f"- VIX 기간구조 VIX/VIX3M {x:.3f} ({common[-1]}, VIX3M {V3M[common[-1]]:.2f}) → {shape} | 최근 {TREND_N}일 "
                     + ", ".join(f"{v:.2f}" for v in rv[-TREND_N:])
                     + f" | 1년 백분위 {p1:.0f}%"
                     + (f" | {sum(1 for v in rv[-20:] if v > 1)}/20일 백워데이션" if any(v > 1 for v in rv[-20:]) else ""))

    # --- gold
    if G:
        gm, live = G
        r = _move(gm, last_session, pct=True)
        if r:
            sig["금"] = r
            p1, _ = _pct_1y(gm, r["last"])
            z = f", z {r['z']:+.1f} {_size(r['z'])}" if r["z"] is not None else ""
            rets = [(gm[b] / gm[a] - 1) for a, b in zip(sorted(gm)[-21:-1], sorted(gm)[-20:])]
            rv = st.pstdev(rets) * 252 ** 0.5 * 100 if len(rets) >= 15 else None
            ln = (f"- 금(COMEX 근월물, $/oz): {r['last']:,.1f} (정산 {r['date']}, 반영 {r['n']}세션) | 변화 {r['chg']:+.2f}%{z} | "
                  f"5일 {r.get('d5', 0):+.2f}%, 20일 {r.get('d20', 0):+.2f}% | 1년 백분위 {p1:.0f}%"
                  + (f" | 20일 실현변동성(연율) {rv:.0f}%" if rv else ""))
            if live and live[1][:10] >= r["date"]:
                ln += f" | 현재 {live[0]:,.1f} ({live[1][5:16]} CT, 정산 대비 {(live[0] / r['last'] - 1) * 100:+.2f}%)"
            L.append(ln)
            hi = max(gm.values())
            L.append(f"  - 추세: {_trend(gm, _f1)}"
                     + (f" | 이력 최고 {hi:,.1f} 대비 {(r['last'] / hi - 1) * 100:+.1f}%" if hi else ""))
        else:
            d = sorted(gm)[-1]
            L.append(f"- 금(COMEX 근월물, $/oz): {gm[d]:,.1f} (정산 {d}) — {last_session} 이후 새 정산 없음(변화 없음)")
            L.append(f"  - 추세: {_trend(gm, _f1)}")
    else:
        L.append("- 금: 데이터 수집 실패")

    # --- bitcoin (24/7: since the KRX close, not per US session)
    if B:
        bm, now_p, base, src = B
        hours = (now - krx_close).total_seconds() / 3600
        ds = sorted(bm)
        dr = [(bm[b] / bm[a] - 1) * 100 for a, b in zip(ds[-61:-1], ds[-60:])]
        sd = st.pstdev(dr) if len(dr) >= 20 else None
        if base:
            chg = (now_p / base - 1) * 100
            z = chg / (sd * max(hours / 24, 1 / 24) ** 0.5) if sd else None
            sig["BTC"] = {"chg": chg, "z": z}
            p1, _ = _pct_1y(bm, now_p)
            d = {k: (now_p / bm[ds[-1 - k]] - 1) * 100 for k in (1, 7, 30) if len(ds) > k}
            rv = st.pstdev(dr[-20:]) * 365 ** 0.5 if len(dr) >= 20 else None
            L.append(f"- 비트코인({src}, $): {now_p:,.0f} (수집 시점) | KRX 마감({last_session} 15:30 KST, {base:,.0f}) 이후 "
                     f"{hours:.0f}시간 {chg:+.2f}%" + (f", z {z:+.1f} {_size(z)}" if z is not None else "")
                     + f" | 7일 {d.get(7, 0):+.1f}%, 30일 {d.get(30, 0):+.1f}% | 1년 백분위 {p1:.0f}%"
                     + (f" | 20일 실현변동성(연율) {rv:.0f}%" if rv else ""))
            hi = max(bm[x] for x in ds[-365:])
            L.append(f"  - 추세(UTC 일봉 종가 + 현재가): {_trend(bm, _f0, last=now_p)} | 1년 고점 {hi:,.0f} 대비 {(now_p / hi - 1) * 100:+.1f}%")
        else:
            L.append(f"- 비트코인: KRX 마감 시점 가격 수집 실패 (현재 {now_p:,.0f})")
    else:
        L.append("- 비트코인: 데이터 수집 실패")

    # --- combined reading (rule-based, descriptive)
    def zof(k):
        r = sig.get(k)
        return r.get("z") if r else None
    zv, zg, zb = zof("VIX"), zof("금"), zof("BTC")
    if None not in (zv, zg, zb):
        up = lambda z: z >= 0.5  # noqa: E731
        dn = lambda z: z <= -0.5  # noqa: E731
        if up(zv) and dn(zb):
            lab = "위험회피 — 변동성↑·비트코인↓" + (" + 금↑(안전자산 선호)" if up(zg) else
                                            " + 금↓(현금·달러 확보형 매도 가능성)" if dn(zg) else "")
        elif dn(zv) and up(zb):
            lab = "위험선호 — 변동성↓·비트코인↑" + (" + 금↑(유동성·달러 약세형)" if up(zg) else "")
        elif up(zv) and up(zb):
            lab = "엇갈림 — 변동성↑인데 비트코인↑ (주식 고유 이슈 가능성)"
        elif dn(zv) and dn(zb):
            lab = "엇갈림 — 변동성↓인데 비트코인↓ (가상자산 고유 이슈 가능성)"
        else:
            lab = "뚜렷한 방향 없음(대부분 통상 범위)"
        L.append(f"- 조합 판정 (z ±0.5 기준): VIX z {zv:+.1f}, 금 z {zg:+.1f}, 비트코인 z {zb:+.1f} → {lab}")
    L.append("- 확률 영향: 없음 — walk-forward(2019~2026) 검증: VIX 변화 t −0.16, VIX 수준 t +2.04(악화), 금(GLD) t −1.42 → 기각; "
             "비트코인 야간 수익률 t −3.09(2019~22 −1.12, 2023~26 −2.89)는 전반기 비유의로 보류. 모두 기준치에 없고 "
             "조정 사유로 쓰지 않는다(설명·해석용).")
    for e in errors:
        L.append(f"- !! {e}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(section(sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")))
