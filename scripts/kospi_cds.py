#!/usr/bin/env python3
"""Sovereign CDS block for the KOSPI morning briefing (stdlib only).

Prints section "8-2": South Korea 5-year sovereign CDS premium (bp) — level, change since the last
KRX close, size label, 1-year/3-year percentile — plus peer sovereigns (China, Japan, US) so a
Korea-specific move can be told apart from a regional/global one. Descriptive only: Korea CDS was
tested as a baseline feature and rejected (see kospi_model docstring), so it is never a probability
adjustment reason.

Sources (verified 2026-11):
- Korea: 국제금융센터(KCIF) `POST https://www.kcif.or.kr/comm/AuthCheck` form
  `code=11&period=D&sector=INTR3&chart_type=0&type=CHART[&startdate=YYYY.MM.DD&enddate=...]` →
  `history` [{date 'YYYY.MM.DD', value}] newest first, daily since 2008 (works without login even
  though `auth_yn` says N1). Same series 한국은행/언론 quote (외평채 5년물 CDS).
- Peers: worldgovernmentbonds.com `POST /wp-json/common/v1/historical` with the page's own
  `jsGlobalVars` as `{"GLOBALVAR": ...}`; needs Origin + Referer (403 without). Different data vendor
  from KCIF (levels differ by ~1bp for Korea; WGB's Korea row is also a business day late and has
  gaps) — use it only for the peers' direction/size. Weekend rows (copies of Friday) are dropped.
"""
import bisect
import json
import statistics as st
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
ENDPOINT = "https://www.worldgovernmentbonds.com/wp-json/common/v1/historical"
# (site symbol, url slug, Korean label)
COUNTRIES = [("29", "south-korea", "한국"), ("9", "china", "중국"), ("11", "japan", "일본"), ("6", "united-states", "미국")]
KST = timezone(timedelta(hours=9))


def _cds_hist(symbol, slug, name="", tenor_months=60):
    """-> {YYYY-MM-DD: bp} weekday rows only, plus {date: stamp} for the newest row."""
    page = f"https://www.worldgovernmentbonds.com/cds-historical-data/{slug}/5-years/"
    gv = {"JS_VARIABLE": "jsGlobalVars", "FUNCTION": "CDS", "DOMESTIC": True, "ENDPOINT": ENDPOINT,
          "DATE_RIF": "2099-12-31", "DEBUG": True,
          "OBJ": {"UNIT": "", "DECIMAL": 2, "UNIT_DELTA": "%", "DECIMAL_DELTA": 2},
          "COUNTRY1": {"SYMBOL": symbol, "PAESE": name, "PAESE_UPPERCASE": name.upper(), "BANDIERA": "", "URL_PAGE": slug},
          "COUNTRY2": None, "OBJ1": {"DURATA_STRING": "5 Years", "DURATA": tenor_months}, "OBJ2": None}
    req = urllib.request.Request(ENDPOINT, data=json.dumps({"GLOBALVAR": gv}).encode(), headers={
        "User-Agent": UA, "Content-type": "application/json; charset=UTF-8", "Accept": "*/*",
        "Origin": "https://www.worldgovernmentbonds.com", "Referer": page})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    if not d.get("success"):
        raise RuntimeError(f"success=false ({str(d)[:120]})")
    out, stamp = {}, {}
    for q in d["result"]["quote"].values():
        ds = q["DATA_VAL"]
        if date.fromisoformat(ds).weekday() >= 5 or q.get("CLOSE_VAL") in (None, ""):
            continue
        out[ds] = float(q["CLOSE_VAL"])
        stamp[ds] = q.get("TIME_VAL", "")
    return out, stamp


def _kcif_hist(years=3):
    end = datetime.now(KST).date()
    start = end - timedelta(days=366 * years + 7)
    body = (f"code=11&period=D&sector=INTR3&chart_type=0&type=CHART&startdate={start:%Y.%m.%d}"
            f"&enddate={end:%Y.%m.%d}").encode()
    req = urllib.request.Request("https://www.kcif.or.kr/comm/AuthCheck", data=body, headers={
        "User-Agent": UA, "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.kcif.or.kr/chart/intrList",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    out = {x["date"].replace(".", "-"): float(x["value"]) for x in d.get("history") or [] if x.get("value")}
    if not out:
        raise RuntimeError(f"history 비어 있음 (auth_yn={d.get('auth_yn')})")
    return out


def _move(m, since, before):
    """Change from the newest row dated < since (state at the last KRX close) to the newest row
    dated < before (what the 08:00 run can see). Same window as the tested feature."""
    ds = [d for d in sorted(m) if d < before]
    i = bisect.bisect_left(ds, since)
    if i == 0 or not ds:  # no row before the last KRX close
        return None
    base, last = m[ds[i - 1]], m[ds[-1]]
    diffs = [m[ds[k]] - m[ds[k - 1]] for k in range(max(1, i - 60), i)]
    sd = st.pstdev(diffs[-20:]) if len(diffs) >= 10 else None
    n = len(ds) - i
    out = {"last": last, "last_date": ds[-1], "base_date": ds[i - 1], "n": n, "chg": last - base,
           "z": (last - base) / (sd * max(n, 1) ** 0.5) if sd else None}
    for k in (5, 20):
        if len(ds) > k:
            out[f"d{k}"] = last - m[ds[-1 - k]]
    for yrs in (1, 3):
        lo = (date.fromisoformat(ds[-1]) - timedelta(days=365 * yrs)).isoformat()
        win = [m[d] for d in ds if d >= lo]
        out[f"pct{yrs}"] = bisect.bisect_right(sorted(win), last) / len(win) * 100
        if yrs == 1:
            out["min1"], out["max1"] = min(win), max(win)
    return out


def _size(z):
    if z is None:
        return "크기 판정 불가"
    a = abs(z)
    return "통상 범위" if a < 1 else "다소 큰 변동(대략 상위 30%)" if a < 2 else "큰 변동(상위 5% 안팎)"


def section(last_session, today=None):
    """last_session: 'YYYY-MM-DD' of the last KRX close; today: KST date (default now)."""
    today = today or datetime.now(KST).date()
    errors = []

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {type(e).__name__}: {e}")
            return None

    with ThreadPoolExecutor(4) as ex:
        kf = ex.submit(safe, "CDS 한국(국제금융센터)", _kcif_hist)
        fut = {s: ex.submit(safe, f"CDS {n}(worldgovernmentbonds)", lambda s=s, g=g, n=n: _cds_hist(s, g, n))
               for s, g, n in COUNTRIES[1:]}
    H = {s: f.result() for s, f in fut.items()}
    K = kf.result()

    L = [f"\n## 8-2. 국가 CDS 프리미엄 (5년물, USD, bp; {last_session} KRX 마감 시점 대비)"]
    mv = {}
    for sym, _, name in COUNTRIES[1:]:
        h = H.get(sym)
        mv[sym] = _move(h[0], last_session, today.isoformat()) if h else None
    kr = _move(K, last_session, today.isoformat()) if K else None
    if not kr:
        L.append("- 한국 CDS: 데이터 수집 실패")
    else:
        # the 08:00 run should see the previous weekday's row
        expect = today - timedelta(days=1)
        while expect.weekday() >= 5:
            expect -= timedelta(days=1)
        stale = kr["last_date"] < expect.isoformat()
        if kr["n"] == 0:
            chg = f"{last_session} 마감 이후 새 값 없음 → 변화 판정 불가"
        else:
            z = f", z {kr['z']:+.1f} {_size(kr['z'])}" if kr["z"] is not None else ""
            chg = f"{kr['base_date']} 대비 {kr['chg']:+.2f}bp{z}"
        L.append(f"- 한국(외평채 5년물, 국제금융센터): {kr['last']:.2f}bp (기준 {kr['last_date']}; {chg}; "
                 f"5일 {kr.get('d5', 0):+.2f}bp, 20일 {kr.get('d20', 0):+.2f}bp)")
        regime = ("1년 중 높은 편(위험 인식↑)" if kr["pct1"] >= 80 else "1년 중 낮은 편(위험 인식↓)" if kr["pct1"] <= 20 else "1년 범위 중간")
        L.append(f"  - 수준: 1년 백분위 {kr['pct1']:.0f}% (1년 범위 {kr['min1']:.1f}~{kr['max1']:.1f}bp), "
                 f"3년 백분위 {kr['pct3']:.0f}% → {regime}")
        if stale:
            L.append(f"  - !! 미갱신: 최신 행 {kr['last_date']} (기대 {expect.isoformat()}) — 변화량은 그 날짜까지 기준")
    peers = []
    for sym, _, name in COUNTRIES[1:]:
        r = mv.get(sym)
        if not r:
            peers.append(f"{name} 수집 실패")
            continue
        chg = f"{r['chg']:+.2f}bp" if r["n"] else "새 값 없음"
        peers.append(f"{name} {r['last']:.2f}bp ({chg}, 20일 {r.get('d20', 0):+.2f}bp, 1년 백분위 {r['pct1']:.0f}%; 기준 {r['last_date']})")
    L.append("- 비교(worldgovernmentbonds, 같은 구간·다른 데이터 공급원이라 수준 비교는 대략적): " + ", ".join(peers))
    cn = mv.get("9")
    if kr and cn:
        both = ("판정 불가" if kr["n"] == 0 or cn["n"] == 0 else "동반" if kr["chg"] * cn["chg"] > 0 else
                "엇갈림" if kr["chg"] * cn["chg"] < 0 else "한쪽 변화 없음")
        L.append(f"  - 한국−중국 격차 {kr['last'] - cn['last']:+.2f}bp; 이번 변화 방향 {both} "
                 f"(한국만 크게 움직이면 국내 요인, 같이 움직이면 지역·글로벌 요인 쪽)")
    L.append("- 확률 반영: 한국 CDS 변화·수준은 기준 모델 검증에서 개선 없음(2019–26 워크포워드, 국제금융센터 시계열: 변화 z t +0.75, 1년 수준 z t +0.87) "
             "→ 조정 사유 아님. 외국인 수급·원화 위험 인식을 해석하는 배경 지표로만 사용")
    for e in errors:
        L.append(f"- !! {e}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(section(sys.argv[1] if len(sys.argv) > 1 else (datetime.now(KST).date() - timedelta(days=1)).isoformat()))
