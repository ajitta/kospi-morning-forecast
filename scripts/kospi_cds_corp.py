#!/usr/bin/env python3
"""Single-name (company) and index CDS block for the KOSPI morning briefing (stdlib only).

Prints section "8-3":
- Levels: ICE Clear Credit end-of-day settlement prices for 5-year CDS (the clearing house's official
  mark, free on ice.com) for Korean names (Rep Korea, KDB, KEXIM, Woori, KT, LG Chem, POSCO, SK hynix)
  and the AI/semiconductor chain that drives KOSPI's chip stocks (NVIDIA, TSMC, Broadcom, AMD, Intel,
  Amkor, Oracle, CoreWeave, hyperscalers, ...), plus CDX NA IG/HY and iTraxx Asia ex-Japan/Europe.
  Prices are points upfront (100 - upfront%); converted to a par spread (bp) with a flat-hazard model
  (recovery 40%, flat 4% rate, quarterly premium). Validated 2026-11-09 against press levels: NVIDIA
  138.7 vs ~139 (CNBC), Oracle 304 vs ~298-305, Rep Korea 22.4 vs KCIF 23.2, CDX IG 58.9 vs ~60.
  The endpoint serves only the latest day, so each run appends to a local history file
  (`<snapshot dir>/icc_cds.json`, git-ignored) — 1/5/20-day changes appear as history builds up.
- Trading: DTCC public SEC swap-data-repository dissemination (single-name CDS are security-based
  swaps). New trades (Action NEWT, Event TRAD) per reference entity on the latest US session vs the
  20-session average, plus the market-wide most-traded names. Notionals above $5M are capped in the
  public tape ("5,000,000+"), so notional is a lower bound; counts are exact for disseminated trades.
  Past sessions: daily cumulative files `api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_YYYY_MM_DD.zip`
  (published after the UTC day ends, kept >1 year); the latest session: intraday slices listed by
  `api/slice/SEC/CR` (rolling 24h, S3 zip per slice).

Descriptive only: no history long enough to test as a model input -> never a probability adjustment.
"""
import bisect
import csv
import io
import json
import math
import os
import re
import statistics as st
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
KST = timezone(timedelta(hours=9))
ICE = "https://www.ice.com/api/cds-settlement-prices/"
DTCC = "https://pddata.dtcc.com/ppd/api/"
DATA_DIR = os.environ.get("KOSPI_FORECAST_DIR", os.path.expanduser("~/.hermes/data/kospi_forecast"))
SNAP_DIR = os.environ.get("KOSPI_SNAPSHOT_DIR", os.path.join(DATA_DIR, "snapshots"))
HIST_FILE = os.path.join(SNAP_DIR, "icc_cds.json")

# (label, ICE name regex, DTCC reference-entity name regex, group)
NAMES = [
    ("대한민국", r"^Rep Korea$", r"^republic of korea$", "한국"),
    ("산업은행", r"^Korea Dev Bk", r"korea development bank", "한국"),
    ("수출입은행", r"^Expt Import Bk Korea", r"export-import bank of korea", "한국"),
    ("우리은행", r"^Woori Bk", r"^woori bank", "한국"),
    ("KT", r"^KT Corp", r"^kt corp", "한국"),
    ("LG화학", r"^LG Chem", r"^lg chem", "한국"),
    ("POSCO홀딩스", r"^POSCO", r"^posco", "한국"),
    ("SK하이닉스", r"^SK hynix", r"^sk hynix", "한국"),
    ("엔비디아", r"^NVIDIA", r"^nvidia", "AI·반도체"),
    ("TSMC", r"^Taiwan Semicon", r"^taiwan semiconductor", "AI·반도체"),
    ("브로드컴", r"^BROADCOM", r"^broadcom", "AI·반도체"),
    ("AMD", r"^Advanced Micro", r"^advanced micro", "AI·반도체"),
    ("인텔", r"^Intel Corp", r"^intel corp", "AI·반도체"),
    ("앰코", r"^Amkor", r"^amkor", "AI·반도체"),
    ("오라클", r"^Oracle", r"^oracle", "AI 인프라·빅테크"),
    ("코어위브", r"^COREWEAVE", r"^coreweave", "AI 인프라·빅테크"),
    ("메타", r"^META PLATFORMS", r"^meta platforms", "AI 인프라·빅테크"),
    ("마이크로소프트", r"^Microsoft", r"^microsoft", "AI 인프라·빅테크"),
    ("알파벳", r"^Alphabet", r"^alphabet", "AI 인프라·빅테크"),
    ("아마존", r"^Amazon", r"^amazon", "AI 인프라·빅테크"),
    ("델", r"^Dell Inc", r"^dell inc", "AI 인프라·빅테크"),
    ("애플", r"^Apple", r"^apple inc", "AI 인프라·빅테크"),
]
# (label, ICE index family prefix -> on-the-run = highest series, coupon)
INDEXES = [
    ("CDX 북미 IG", "CDX-NAIGS", 0.01), ("CDX 북미 HY", "CDX-NAHYS", 0.05),
    ("iTraxx 아시아(일본 제외) IG", "ITRAXX-ASIAXJIGS", 0.01),
    ("iTraxx 유럽", "ITRAXX-EUROPES", 0.01), ("iTraxx 크로스오버", "ITRAXX-XOVERS", 0.05),
]


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ price <-> spread
def _rpv01(s, t, r=0.04, rec=0.4):
    lam = s / (1 - rec)
    n = max(1, math.ceil(t / 0.25 - 1e-9))
    first = t - (n - 1) * 0.25
    out, prev = 0.0, 0.0
    for i in range(n):
        ti = first + 0.25 * i
        out += (ti - prev) * math.exp(-(r + lam) * ti)
        prev = ti
    return out


def spread_bp(price, coupon, years):
    """ICE points-upfront price -> par spread (bp). price < 100: protection buyer pays upfront (S > C)."""
    u = (100 - price) / 100
    lo, hi = (coupon, 0.6) if u >= 0 else (0.0, coupon)
    f = lambda s: (s - coupon) * _rpv01(s, years) - u  # noqa: E731
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(70):
        m = (lo + hi) / 2
        if f(lo) * f(m) <= 0:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2 * 1e4


def _parse_sn(instr):
    """'NVIDIA.SNRFOR.USD.XR14.100.2031-12-20' -> (ticker, tier, ccy, doc, coupon, maturity) or None."""
    p = instr.split(".")
    if len(p) < 6:
        return None
    try:
        return p[0], p[1], p[2], p[3], int(p[4]) / 1e4, date.fromisoformat(p[5])
    except ValueError:
        return None


# ------------------------------------------------------------------ ICE history
def fetch_icc():
    sn = json.loads(_get(ICE + "icc-single-names"))
    ix = json.loads(_get(ICE + "icc-indexes"))
    return sn, ix


def update_history(sn, ix):
    """Merge today's ICE prices into HIST_FILE: {instrument: {"n": name, "p": {date: price}}}."""
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = {}
    for r in sn + ix:
        k = r.get("instrumentName")
        if not k or not r.get("eodPrice") or not r.get("clearingDate"):
            continue
        e = hist.setdefault(k, {"n": r.get("name", ""), "p": {}})
        e["p"][r["clearingDate"]] = float(r["eodPrice"])
    os.makedirs(SNAP_DIR, exist_ok=True)
    tmp = HIST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, HIST_FILE)
    return hist


def save_only():
    """For the evening snapshot job: capture today's ICE prices so no US session is skipped."""
    sn, ix = fetch_icc()
    update_history(sn, ix)


def _pick_single(hist, ice_re):
    """On-the-run 5Y USD/senior instrument for a name; among coupons, the one priced closest to par."""
    rx = re.compile(ice_re, re.I)
    best = None
    for k, e in hist.items():
        if not rx.search(e.get("n", "")):
            continue
        q = _parse_sn(k)
        if not q or q[1] != "SNRFOR" or q[2] != "USD":
            continue
        last_d = max(e["p"])
        key = (q[5], last_d, -abs(e["p"][last_d] - 100), q[3].endswith("14"))
        if best is None or key > best[0]:
            best = (key, k)
    return best[1] if best else None


def _pick_index(hist, prefix):
    best = None
    for k in hist:
        m = re.match(re.escape(prefix) + r"(\d+)V(\d+)-5Y$", k)
        if m:
            key = (int(m.group(1)), int(m.group(2)), max(hist[k]["p"]))
            if best is None or key > best[0]:
                best = (key, k)
    return best[1] if best else None


def _series(hist, instr, coupon, maturity):
    """{date: spread bp} for one instrument (each price converted at its own date)."""
    out = {}
    for d, p in hist[instr]["p"].items():
        mat = maturity or _index_maturity(date.fromisoformat(d))
        s = spread_bp(p, coupon, (mat - date.fromisoformat(d)).days / 365.25)
        if s is not None:
            out[d] = s
    return out


def _index_maturity(d):
    """On-the-run 5Y index maturity: series roll 20 Mar/20 Sep, maturing 5 years after the next 20 Jun/20 Dec."""
    y = d.year
    if (d.month, d.day) < (3, 20):
        return date(y + 4, 12, 20)
    if (d.month, d.day) < (9, 20):
        return date(y + 5, 6, 20)
    return date(y + 5, 12, 20)


def _chg_txt(ser, before):
    ds = [d for d in sorted(ser) if d < before]
    if not ds:
        return None, ""
    last = ds[-1]
    parts = []
    for k, lab in ((1, "전일"), (5, "5일"), (20, "20일")):
        if len(ds) > k:
            parts.append(f"{lab} {ser[last] - ser[ds[-1 - k]]:+.1f}")
    return last, ("; " + ", ".join(parts) + "bp") if parts else ""


# ------------------------------------------------------------------ DTCC trades
def _zip_rows(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode("utf-8", "replace"))))


def _cumulative(d):
    try:
        return _zip_rows(_get(f"{DTCC}report/cumulative/sec/SEC_CUMULATIVE_CREDITS_{d:%Y_%m_%d}.zip", 60))
    except Exception:  # noqa: BLE001 — not published yet / holiday
        return None


def _retry(fn, n=3):
    for i in range(n):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == n - 1:
                raise
    return None


def _slices(day_iso):
    """All intraday slices whose window starts on `day_iso` (UTC). -> (rows, n_ok, n_failed)."""
    lst = json.loads(_retry(lambda: _get(DTCC + "slice/SEC/CR")))
    todo = [x["fullFilePath"] for x in lst if str(x.get("startTs", ""))[:10] == day_iso]

    def one(u):
        try:
            return _retry(lambda: _zip_rows(_get(u, 30)))
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(8) as ex:
        parts = list(ex.map(one, todo))
    ok = [p for p in parts if p is not None]
    return [r for p in ok for r in p], len(ok), len(parts) - len(ok)


def _red(r):
    m = dict(zip(r.get("Underlier ID source-Leg 1", "").split(";"), r.get("Underlier ID-Leg 1", "").split(";")))
    return m.get("REDID", "")[:6]


def _is_sn_cds(r):
    f = r.get("UPI FISN", "")
    return f.startswith("NA/CDS") and "Idx" not in f and "Basket" not in f


def trade_stats(us_days, latest):
    """us_days: past US session dates (ascending, excluding `latest`). Returns dict or raises."""
    need = us_days + [latest]
    files = {}
    with ThreadPoolExecutor(8) as ex:
        for d, rows in zip(need, ex.map(_cumulative, need)):
            files[d] = rows
    src = "누적 파일"
    if files.get(latest) is None:
        rows, n, bad = _slices(latest.isoformat())
        files[latest] = rows
        src = f"실시간 슬라이스 {n}개" + (f"(실패 {bad}개 — 건수 과소)" if bad else "") + ", 당일 지연보고분 미포함 가능"
    seen = set()
    per_day = defaultdict(Counter)      # day -> entity key -> trades
    notional = defaultdict(lambda: defaultdict(float))  # day -> entity key -> USD lower bound
    capped = defaultdict(Counter)
    names = defaultdict(Counter)        # entity key -> names seen
    total = Counter()
    for rows in files.values():
        for r in rows or []:
            if r.get("Action type") != "NEWT" or r.get("Event type") != "TRAD" or not _is_sn_cds(r):
                continue
            i = r.get("Dissemination Identifier")
            if i in seen:
                continue
            seen.add(i)
            d = r.get("Execution Timestamp", "")[:10]
            key = _red(r) or r.get("Underlying Asset Name", "")
            if not key:
                continue
            nm = r.get("Underlying Asset Name", "")
            if nm and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", nm):
                names[key][nm] += 1
            per_day[d][key] += 1
            total[d] += 1
            n = r.get("Notional amount-Leg 1", "").replace(",", "")
            if n.endswith("+"):
                capped[d][key] += 1
                n = n[:-1]
            try:
                notional[d][key] += float(n)
            except ValueError:
                pass
    return {"per_day": per_day, "notional": notional, "capped": capped, "names": names, "total": total,
            "src": src, "past": [d.isoformat() for d in us_days if files.get(d) is not None]}


def _us_sessions(before, n):
    """Last n US weekdays strictly before `before` (US holidays show up as zero-trade days and are dropped)."""
    out, d = [], before - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


# ------------------------------------------------------------------ section
def section(last_session, today=None):
    today = today or datetime.now(KST).date()
    errors = []
    L = ["\n## 8-3. 개별 기업·지수 CDS (5년물; 수준 = ICE Clear Credit 정산가 환산, 거래 = DTCC 미국 보고 체결)"]
    hist = None
    try:
        sn, ix = fetch_icc()
        hist = update_history(sn, ix)
    except Exception as e:  # noqa: BLE001
        errors.append(f"ICE 정산가: {type(e).__name__}: {e}")
        try:
            with open(HIST_FILE, encoding="utf-8") as f:
                hist = json.load(f)
            errors.append("→ 저장된 마지막 정산가로 대체")
        except (OSError, ValueError):
            hist = None
    before = today.isoformat()
    if hist:
        dates = sorted({d for e in hist.values() for d in e["p"]})
        L.append(f"- 환산: 정산가(100−업프런트%)를 회수율 40%·금리 4% 단일 위험률 모형으로 bp 환산(≈, 업계 표준 모형과 "
                 f"수 bp 차이 가능). 변화는 같은 종목 정산가끼리 비교. 저장 이력 {len(dates)}일({dates[0]}~)"
                 + (" — 5·20일 변화는 이력이 쌓이면 표시" if len(dates) < 21 else ""))
        lines = defaultdict(list)
        movers = []
        for lab, prefix, cpn in INDEXES:
            k = _pick_index(hist, prefix)
            if not k:
                lines["지수"].append(f"{lab} 없음")
                continue
            ser = _series(hist, k, cpn, None)
            d, chg = _chg_txt(ser, before)
            if d:
                ser_no = re.search(r"S(\d+)V", k)
                lines["지수"].append(f"{lab} {ser[d]:.0f}bp (S{ser_no.group(1) if ser_no else '?'}, 기준 {d}{chg})")
        for lab, ice_re, _, grp in NAMES:
            k = _pick_single(hist, ice_re)
            if not k:
                lines[grp].append(f"{lab} 청산 종목 없음")
                continue
            q = _parse_sn(k)
            ser = _series(hist, k, q[4], q[5])
            d, chg = _chg_txt(ser, before)
            if not d:
                continue
            lines[grp].append(f"{lab} {ser[d]:.0f}bp (정산가 {hist[k]['p'][d]:.2f}, 쿠폰 {q[4]*1e4:.0f}, 기준 {d}{chg})")
            prev = [x for x in sorted(ser) if x < d]
            if prev:
                movers.append((ser[d] - ser[prev[-1]], ser[d] / ser[prev[-1]] - 1, lab))
        for grp in ("지수", "한국", "AI·반도체", "AI 인프라·빅테크"):
            if lines[grp]:
                L.append(f"- {grp}: " + "; ".join(lines[grp]))
        if movers:
            up = sorted(movers, key=lambda x: -x[1])[:3]
            dn = sorted(movers, key=lambda x: x[1])[:3]
            L.append("  - 전일 대비 확대 상위: " + ", ".join(f"{n} {a:+.1f}bp({b*100:+.1f}%)" for a, b, n in up if a > 0)
                     + " | 축소 상위: " + ", ".join(f"{n} {a:+.1f}bp({b*100:+.1f}%)" for a, b, n in dn if a < 0))
    else:
        L.append("- 정산가: 데이터 수집 실패")

    # trading activity on the latest finished US session
    latest = _us_sessions(today, 1)[0]
    try:
        ts = trade_stats(_us_sessions(latest, 20), latest)
        lat = latest.isoformat()
        past = [d for d in ts["past"] if ts["total"].get(d)]
        avg_tot = st.mean(ts["total"][d] for d in past) if past else None
        tot = ts["total"].get(lat, 0)
        if tot == 0:
            raise RuntimeError(f"{lat} 체결 0건 — 미국 휴장이거나 DTCC 데이터 미게시")
        L.append(f"- 거래(DTCC SEC 보고 단일종목 CDS 신규 체결, {lat} 미국 세션, {ts['src']}): 전체 {tot}건"
                 + (f" (직전 {len(past)}세션 평균 {avg_tot:.0f}건, ×{tot / avg_tot:.1f})" if avg_tot else ""))
        # entity keys per watched name: any key whose seen names match the DTCC regex
        wl = []
        for lab, _, dre, _ in NAMES:
            rx = re.compile(dre, re.I)
            keys = {k for k, c in ts["names"].items() if any(rx.search(n) for n in c)}
            if not keys:
                continue
            n_now = sum(ts["per_day"][lat][k] for k in keys)
            n_avg = st.mean(sum(ts["per_day"][d][k] for k in keys) for d in past) if past else 0
            if n_now == 0 and n_avg < 1:
                continue
            ntl = sum(ts["notional"][lat][k] for k in keys) / 1e6
            cap = sum(ts["capped"][lat][k] for k in keys)
            ratio = f", ×{n_now / n_avg:.1f}" if n_avg >= 1 else ""
            amt = (f"; 최소 ${ntl:.0f}M" + (f", $5M+ {cap}건" if cap else "")) if n_now else ""
            wl.append((n_now, f"{lab} {n_now}건(평균 {n_avg:.1f}{ratio}{amt})"))
        if wl:
            L.append("  - 관심 종목: " + "; ".join(t for _, t in sorted(wl, key=lambda x: -x[0])))
        def nm(k):
            c = ts["names"].get(k)
            return c.most_common(1)[0][0] if c else None
        by_name = Counter()
        for k, n in ts["per_day"][lat].items():
            label = nm(k)
            if label:  # unnamed entities (ISIN/RED only) are left out of the ranking
                by_name[re.sub(r"[^A-Z0-9 ]", " ", label.upper()).split("  ")[0].strip()] += n
        top = by_name.most_common(10)
        if top:
            L.append("  - 체결 상위 10(이름 공개분): " + ", ".join(f"{k.title()} {n}" for k, n in top))
        L.append("  - 주의: 미국 보고 대상(미국인 관여) 체결만 포함 — 한국 기업 CDS는 대부분 역외 거래라 거의 안 잡힘. "
                 "$5M 초과 명목금액은 '5,000,000+'로 가려져 금액은 하한")
    except Exception as e:  # noqa: BLE001
        errors.append(f"DTCC 체결: {type(e).__name__}: {e}")
    L.append("- 확률 반영: 개별·지수 CDS는 검증 이력이 없어 조정 사유 아님. 반도체·AI 신용 위험 인식(엔비디아·TSMC·하이닉스) "
             "→ 외국인 반도체 수급 해석용")
    for e in errors:
        L.append(f"- !! {e}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--save":
        save_only()
    else:
        print(section(sys.argv[1] if len(sys.argv) > 1 else (datetime.now(KST).date() - timedelta(days=1)).isoformat()))
