#!/usr/bin/env python3
"""US Treasury yields + corporate credit block for the KOSPI morning briefing (stdlib only).

Prints section "8-1" of the data block: Treasury levels and changes, curve shape, a credit spread
and credit-only ETF proxies, each with a size label (z vs recent daily moves) and a rule-based
regime label. Labels are descriptive; whether any of this moves the KOSPI probability is settled
by kospi_model (HYG is a model input; Treasury moves alone tested no out-of-sample gain).

Sources (verified 2026-10):
- Naver `api.stock.naver.com/marketindex/bond/{code}/prices?page=N&pageSize=60` — daily yields,
  ~3 years (13 pages). Rows are dated in US local time and settle at 17:05 ET.
- CNN fear & greed `junk_bond_demand` component — HY minus IG yield spread (%p), ~1 year daily.
- Naver `api.stock.naver.com/stock/{HYG|LQD|IEF.O}/price` — ETF closes (60 sessions).
FRED (ICE BofA OAS, DGS10) is unreachable from cloud IPs without an API key (the site drops the
connection), so it isn't used.
"""
import bisect
import json
import statistics as st
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
YIELDS = [("US3MT=RR", "3개월"), ("US2YT=RR", "2년"), ("US10YT=RR", "10년"), ("US30YT=RR", "30년")]
ETFS = [("HYG", "HYG(하이일드)"), ("LQD", "LQD(투자등급)"), ("IEF.O", "IEF(7-10년 국채)")]


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _yield_hist(code, pages=13):
    out = {}
    for pg in range(1, pages + 1):
        rows = _get(f"https://api.stock.naver.com/marketindex/bond/{code}/prices?page={pg}&pageSize=60")
        if not rows:
            break
        for r in rows:
            out[r["localTradedAt"][:10]] = float(str(r["closePrice"]).replace(",", ""))
    return out


def _etf_hist(code):
    rows = _get(f"https://api.stock.naver.com/stock/{code}/price?page=1&pageSize=60")
    return {r["localTradedAt"][:10]: float(str(r["closePrice"]).replace(",", "")) for r in rows
            if r["localTradedAt"][11:16] >= "16:00"}


def _cnn_junk():
    d = _get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
             {"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com", "Accept": "application/json"})
    out = {}
    for p in sorted(d["junk_bond_demand"]["data"], key=lambda p: p["x"]):  # last point per day wins
        out[datetime.fromtimestamp(p["x"] / 1000, timezone.utc).date().isoformat()] = p["y"]
    return out


def _move(m, since, diff=True, n_vol=60):
    """Change over the sessions dated >= since (US sessions after the last KRX close).
    diff=True: level change (yields, spreads); False: % return (ETFs). Returns dict or None."""
    ds = sorted(m)
    i = bisect.bisect_left(ds, since)
    if i == 0 or i == len(ds):
        return None
    base, last = m[ds[i - 1]], m[ds[-1]]
    steps = [(m[ds[k]] - m[ds[k - 1]]) if diff else (m[ds[k]] / m[ds[k - 1]] - 1) * 100 for k in range(1, i)]
    sd = st.pstdev(steps[-n_vol:]) if len(steps) >= 20 else None
    chg = (last - base) if diff else (last / base - 1) * 100
    n = len(ds) - i
    # several sessions (KRX holiday gap) -> compare with the n-day move scale, not the 1-day one
    out = {"last": last, "last_date": ds[-1], "n": n, "chg": chg, "z": chg / (sd * n ** 0.5) if sd else None}
    for k in (5, 20):
        if len(ds) > k:
            out[f"d{k}"] = (last - m[ds[-1 - k]]) if diff else (last / m[ds[-1 - k]] - 1) * 100
    lv = sorted(m.values())
    out["pct"] = bisect.bisect_right(lv, last) / len(lv) * 100
    out["span"] = (ds[0], ds[-1])
    return out


def _size(z):
    if z is None:
        return "크기 판정 불가"
    a = abs(z)
    return "통상 범위" if a < 1 else "다소 큰 변동(대략 상위 30%)" if a < 2 else "큰 변동(상위 5% 안팎)"


def _curve_shape(d2, d10):
    """Classic bull/bear steepening/flattening from the 2y and 10y changes (bp)."""
    dc = d10 - d2
    if abs(dc) < 1.0:
        return "평행 이동" + (" (금리 하락)" if d10 < 0 else " (금리 상승)" if d10 > 0 else "")
    level = (d2 + d10) / 2
    kind = "스티프닝" if dc > 0 else "플래트닝"
    return ("불 " if level < 0 else "베어 ") + kind + (" — 단기물 주도 하락" if level < 0 and dc > 0 else
                                                         " — 장기물 주도 상승" if level > 0 and dc > 0 else
                                                         " — 장기물 주도 하락" if level < 0 else " — 단기물 주도 상승")


def section(last_session):
    """last_session: 'YYYY-MM-DD' of the last KRX close. Returns the printed block (str)."""
    errors = []

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {type(e).__name__}: {e}")
            return None

    with ThreadPoolExecutor(8) as ex:
        yf = {c: ex.submit(safe, f"국채 {c}", lambda c=c: _yield_hist(c)) for c, _ in YIELDS}
        ef = {c: ex.submit(safe, f"ETF {c}", lambda c=c: _etf_hist(c)) for c, _ in ETFS}
        jf = ex.submit(safe, "CNN 정크본드 스프레드", _cnn_junk)
    Y = {c: f.result() for c, f in yf.items()}
    E = {c: f.result() for c, f in ef.items()}
    J = jf.result()

    L = [f"\n## 8-1. 미국 국채 금리·회사채 신용 ({last_session} KRX 마감 이후 마감된 미국 세션 기준 변화)"]
    mv = {}
    parts = []
    for code, name in YIELDS:
        m = Y.get(code)
        r = _move(m, last_session) if m else None
        mv[code] = r
        if not r:
            parts.append(f"{name} 데이터 수집 실패")
            continue
        z = f", z {r['z']:+.1f}" if r["z"] is not None else ""
        parts.append(f"{name} {r['last']:.3f}% ({r['chg']*100:+.1f}bp{z}; 5일 {r.get('d5', 0)*100:+.0f}bp, 20일 {r.get('d20', 0)*100:+.0f}bp; "
                     f"3년 백분위 {r['pct']:.0f}%)")
    span = next((r["span"] for r in mv.values() if r), None)
    L.append(f"- 국채 금리 (기준 {next((r['last_date'] for r in mv.values() if r), '?')}, 반영 세션 "
             f"{next((r['n'] for r in mv.values() if r), 0)}개; z = 이번 변화 ÷ (최근 60일 일간 변화 표준편차 × √세션수); 백분위 기간 "
             f"{span[0] if span else '?'}~): ")
    for ptxt in parts:
        L.append(f"  - {ptxt}")
    r10, r2, r3m = mv.get("US10YT=RR"), mv.get("US2YT=RR"), mv.get("US3MT=RR")
    if r10:
        L.append(f"  - 판정: 10년물 변화 {_size(r10['z'])}")
    if r10 and r2:
        c_now = (r10["last"] - r2["last"]) * 100
        d2, d10 = r2["chg"] * 100, r10["chg"] * 100
        L.append(f"- 커브: 10년-2년 {c_now:+.0f}bp (이번 {d10 - d2:+.1f}bp) → {_curve_shape(d2, d10)}")
    if r10 and r3m:
        c3 = (r10["last"] - r3m["last"]) * 100
        L.append(f"  10년-3개월 {c3:+.0f}bp ({'역전' if c3 < 0 else '정상'}; 이번 {(r10['chg'] - r3m['chg'])*100:+.1f}bp)")

    jr = _move(J, last_session) if J else None
    if jr:
        lvl = jr["pct"]
        regime = ("1년 중 넓은 편(위험회피 쪽)" if lvl >= 80 else "1년 중 좁은 편(위험선호 쪽)" if lvl <= 20 else "1년 범위 중간")
        z = f", z {jr['z']:+.1f} {_size(jr['z'])}" if jr["z"] is not None else ""
        L.append(f"- 신용 스프레드(CNN 정크본드 수요 원자료 = 하이일드−투자등급 수익률차, 기준 {jr['last_date']}): {jr['last']:.3f}%p "
                 f"({jr['chg']*100:+.1f}bp{z}; 5일 {jr.get('d5', 0)*100:+.0f}bp, 20일 {jr.get('d20', 0)*100:+.0f}bp) — "
                 f"1년 백분위 {lvl:.0f}% → {regime}")
    else:
        L.append("- 신용 스프레드(CNN): 데이터 수집 실패")

    em = {c: (_move(E[c], last_session, diff=False, n_vol=20) if E.get(c) else None) for c, _ in ETFS}
    if all(em.values()):  # noqa — every value checked non-None
        txt = ", ".join(f"{n} {em[c]['chg']:+.2f}%" + (f" (z {em[c]['z']:+.1f})" if em[c]["z"] is not None else "") for c, n in ETFS)
        L.append(f"- 회사채·국채 ETF 하룻밤: {txt}")
        ief = em["IEF.O"]["chg"]
        L.append(f"  신용만 떼어 본 초과수익(국채 대비): 하이일드 HYG−IEF {em['HYG']['chg'] - ief:+.2f}%p, "
                 f"투자등급 LQD−IEF {em['LQD']['chg'] - ief:+.2f}%p (+ = 스프레드 축소 쪽, − = 확대 쪽)")
    else:
        L.append("- 회사채·국채 ETF: 데이터 수집 실패")

    # rule-based regime (a common reading frame, not a tested signal)
    if r10 and jr:
        up = r10["chg"] * 100 >= 2
        down = r10["chg"] * 100 <= -2
        wide = jr["chg"] * 100 >= 2
        tight = jr["chg"] * 100 <= -2
        regime = ("긴축·위험회피형(금리↑·스프레드↑) — 주식에 가장 불리한 조합" if up and wide else
                  "성장·위험선호형 금리 상승(금리↑·스프레드↓)" if up and tight else
                  "안전자산 선호형(금리↓·스프레드↑) — 경기 우려" if down and wide else
                  "완화 기대형(금리↓·스프레드↓) — 위험선호" if down and tight else
                  "뚜렷한 조합 없음(금리 또는 스프레드 변화 2bp 미만)")
        L.append(f"- 조합 판정(통상적 해석 틀, 통계 검증 아님): {regime}")
    L.append("- 확률 반영: HYG는 '## 0' 모델 입력, 국채 금리·스프레드 변화 자체는 조정 사유 아님('## 0' 금리·신용 줄 참고)")
    for e in errors:
        L.append(f"- !! {e}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(section(sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()))
