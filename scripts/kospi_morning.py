#!/usr/bin/env python3
"""KOSPI morning briefing data collector (cron pre-run script).

Prints a compact, labeled data dump for the agent: last KRX session flows
(spot / futures / program), foreign & institution top-10 net buy/sell
(individual stocks only, ETFs aggregated separately), theme & sector strength,
overnight US market since the last KRX session, and the forecast scorecard.

On KRX holidays/weekends prints {"wakeAgent": false} as the last line so the
cron agent is skipped entirely. Stdlib only.
"""
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
DATA_DIR = os.environ.get("KOSPI_FORECAST_DIR", os.path.expanduser("~/.hermes/data/kospi_forecast"))
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")

# KRX weekday closures. 2027 list = official public holidays (월력요항) + 12/31;
# KRX may add ad-hoc closures (elections, temporary holidays) — update when announced.
KRX_HOLIDAYS = {
    # 2026 (remaining)
    "2026-12-25", "2026-12-31",
    # 2027
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-03",
    "2027-05-05", "2027-05-13", "2027-07-19", "2027-08-16", "2027-09-14",
    "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27",
    "2027-12-31",
}
HOLIDAY_TABLE_LAST_YEAR = 2027

US_INDICES = [(".DJI", "다우"), (".INX", "S&P500"), (".IXIC", "나스닥종합"),
              (".NDX", "나스닥100"), (".SOX", "필라델피아반도체"), (".VIX", "VIX")]
US_STOCKS = [("NVDA.O", "엔비디아"), ("MU.O", "마이크론"), ("AVGO.O", "브로드컴"),
             ("TSM", "TSMC ADR"), ("AMD.O", "AMD"), ("AAPL.O", "애플"),
             ("MSFT.O", "MS"), ("GOOGL.O", "알파벳"), ("AMZN.O", "아마존"),
             ("META.O", "메타"), ("TSLA.O", "테슬라")]
MARKET_INDEX = [("bond/US10YT=RR", "미국채10년(%)"), ("bond/US2YT=RR", "미국채2년(%)"),
                ("exchange/.DXY", "달러인덱스"), ("exchange/FX_USDKRW", "원/달러(하나은행)"),
                ("energy/CLcv1", "WTI"), ("metals/GCcv1", "금")]

errors = []


def get(url, referer=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def safe(label, fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        errors.append(f"{label}: {type(e).__name__}: {e}")
        return default


def daum(path):
    return get("https://finance.daum.net" + path, referer="https://finance.daum.net/")


def num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def eok(won):
    """won -> 억원 string with sign."""
    if won is None:
        return "n/a"
    return f"{won / 1e8:+,.0f}억"


def pct(x, nd=2):
    return "n/a" if x is None else f"{x:+.{nd}f}%"


# ---------------------------------------------------------------- calendar
def is_krx_open(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in KRX_HOLIDAYS


def main():
    now = datetime.now(KST)
    today = now.date()
    force = "--force" in sys.argv
    if not is_krx_open(today) and not force:
        print(f"오늘({today}) KRX 휴장일 — 브리핑 생략")
        print(json.dumps({"wakeAgent": False}))
        return

    def P(line=""):
        print(line, flush=True)
    P(f"# KOSPI 모닝 데이터 (수집시각 {now:%Y-%m-%d %H:%M} KST, 오늘 {today} {'월화수목금토일'[today.weekday()]})")
    if today.year > HOLIDAY_TABLE_LAST_YEAR:
        P(f"!! 휴장일 테이블이 {HOLIDAY_TABLE_LAST_YEAR}년까지만 있음 — 스크립트 KRX_HOLIDAYS 갱신 필요(사용자에게 알릴 것)")

    # ---------------------------------------------------- KOSPI price history
    kospi_hist = safe("KOSPI 일별", lambda: get("https://m.stock.naver.com/api/index/KOSPI/price?pageSize=25&page=1"), [])
    # before the 09:00 open, a row dated today (if any) is not a finished session
    if kospi_hist and kospi_hist[0]["localTradedAt"][:10] == today.isoformat() and now.hour < 16:
        kospi_hist = kospi_hist[1:]
    k200_hist = safe("KOSPI200 일별", lambda: get("https://m.stock.naver.com/api/index/KPI200/price?pageSize=5&page=1"), [])
    if not kospi_hist:
        P("!! KOSPI 시세 수집 실패 — 아래 데이터 신뢰도 낮음")
        last_session = None
    else:
        last_session = kospi_hist[0]["localTradedAt"][:10]
    P(f"\n## 기준 세션: 직전 KRX 거래일 = {last_session}")

    integ = safe("KOSPI integration", lambda: get("https://m.stock.naver.com/api/index/KOSPI/integration"), {}) or {}
    P("\n## 1. KOSPI 지수")
    for row in (kospi_hist or [])[:6]:
        P(f"- {row['localTradedAt'][:10]} 종가 {row['closePrice']} ({row['fluctuationsRatio']}%) "
          f"시 {row['openPrice']} 고 {row['highPrice']} 저 {row['lowPrice']}")
    if kospi_hist and len(kospi_hist) >= 20:
        c = [num(r["closePrice"]) for r in kospi_hist]
        ma5, ma20 = sum(c[:5]) / 5, sum(c[:20]) / 20
        P(f"- 5일선 {ma5:,.2f} / 20일선 {ma20:,.2f} (종가 대비 5일선 {pct((c[0]/ma5-1)*100)}, 20일선 {pct((c[0]/ma20-1)*100)})")
    for info in integ.get("totalInfos", []):
        if info.get("code") in ("accumulatedTradingVolume", "accumulatedTradingValue", "highPrice52Week", "lowPrice52Week"):
            P(f"- {info['key']}: {info['value']}")
    ud = integ.get("upDownStockInfo") or {}
    if ud:
        P(f"- 등락종목: 상승 {ud.get('riseCount')}(상한 {ud.get('upperCount')}) / 보합 {ud.get('steadyCount')} / 하락 {ud.get('fallCount')}(하한 {ud.get('lowerCount')})")
    if k200_hist:
        P(f"- KOSPI200 {k200_hist[0]['localTradedAt'][:10]} 종가 {k200_hist[0]['closePrice']} ({k200_hist[0]['fluctuationsRatio']}%)")

    # ---------------------------------------------------- spot investor flows
    P("\n## 2. 현물 투자자별 순매수 (KOSPI, 억원)")
    inv = safe("현물 투자자 일별", lambda: daum("/api/investor/KOSPI/days?market=KOSPI&page=1&perPage=20&details=true&pagination=true")["data"], [])
    if inv:
        d0 = inv[0]
        if last_session and d0["date"][:10] != last_session:
            P(f"!! 현물 수급 최신일 {d0['date'][:10]} ≠ 기준 세션 {last_session}")
        det = d0.get("details", {})
        P(f"- {d0['date'][:10]}: 외국인 {eok(d0['foreignStraightPurchasePrice'])} / 기관 {eok(d0['institutionStraightPurchasePrice'])} / 개인 {eok(d0['individualStraightPurchasePrice'])} / 기타법인 {eok(det.get('ETC_CORPORATION'))}")
        P(f"  기관 세부: 금융투자 {eok(det.get('FINANCIAL_INVESTOR'))}, 투신 {eok(det.get('MUTUAL_FUND'))}, 사모 {eok(det.get('PRIVATE_EQUITY_FUND'))}, "
          f"연기금 {eok(det.get('PENSION_FUND'))}, 보험 {eok(det.get('INSURANCE_COMPANIES'))}, 은행 {eok(det.get('BANK'))}, 기타금융 {eok(det.get('ETC_FINANCIAL_INSTITUTION'))}")
        for n in (5, 20):
            seg = inv[:n]
            P(f"- 최근 {len(seg)}일 누적: 외국인 {eok(sum(x['foreignStraightPurchasePrice'] for x in seg))} / "
              f"기관 {eok(sum(x['institutionStraightPurchasePrice'] for x in seg))} / 개인 {eok(sum(x['individualStraightPurchasePrice'] for x in seg))}")
        sign = 1 if d0["foreignStraightPurchasePrice"] >= 0 else -1
        streak = 0
        for x in inv:
            if (x["foreignStraightPurchasePrice"] >= 0) == (sign > 0):
                streak += 1
            else:
                break
        P(f"- 외국인 연속 {'순매수' if sign > 0 else '순매도'} {streak}일째")
        P("- 일별(외/기/개): " + " | ".join(
            f"{x['date'][5:10]} {x['foreignStraightPurchasePrice']/1e8:+,.0f}/{x['institutionStraightPurchasePrice']/1e8:+,.0f}/{x['individualStraightPurchasePrice']/1e8:+,.0f}"
            for x in inv[:7]))

    # ---------------------------------------------------- program trading
    P("\n## 3. 프로그램 매매 (KOSPI, 억원)")
    prog = integ.get("programTrendInfo")
    if prog:
        flag = "" if not last_session or prog.get("bizdate") == last_session.replace("-", "") else f" !! 기준일 불일치({prog.get('bizdate')})"
        P(f"- {prog.get('bizdate')}: 차익 {prog.get('indexDifferenceReal')} / 비차익 {prog.get('indexBiDifferenceReal')} / 전체 {prog.get('indexTotalReal')}{flag}")
    else:
        P("- 수집 실패")

    # ---------------------------------------------------- futures
    P("\n## 4. KOSPI200 선물 (최근월물, 투자자별 순매수 = 계약)")
    masters = safe("선물 마스터", lambda: daum("/api/futures/masters")["data"], [])
    if masters:
        fcode, fname = masters[0]["symbolCode"], masters[0]["name"]
        fut = []
        for pg in (1, 2):
            fut += safe(f"선물 일별 p{pg}", lambda pg=pg: daum(f"/api/future/{fcode}/days?pagination=true&page={pg}")["data"], []) or []
        if fut:
            f0 = fut[0]
            P(f"- 종목 {fname} ({fcode})")
            k200c = num(k200_hist[0]["closePrice"]) if k200_hist else None
            basis = f"{f0['tradePrice'] - k200c:+.2f}pt" if k200c and k200_hist[0]['localTradedAt'][:10] == f0['date'][:10] else "n/a"
            P(f"- {f0['date'][:10]}: 종가 {f0['tradePrice']} ({f0['changeRate']*100:+.2f}%), 시장베이시스(선물-현물) {basis}")
            P(f"  외국인 {f0['foreignSettlement']:+,} / 기관 {f0['institutionSettlement']:+,} / 개인 {f0['privateSettlement']:+,} 계약")
            if len(fut) > 1:
                P(f"  미결제약정 {f0['unsettledVolume']:,} (전일 대비 {f0['unsettledVolume']-fut[1]['unsettledVolume']:+,})")
            for n in (5, 20):
                seg = fut[:n]
                P(f"- 최근 {len(seg)}일 누적 외국인 {sum(x['foreignSettlement'] for x in seg):+,} / 기관 {sum(x['institutionSettlement'] for x in seg):+,} 계약")
            P("- 일별 외국인 계약(미결제): " + " | ".join(f"{x['date'][5:10]} {x['foreignSettlement']:+,}({x['unsettledVolume']:,})" for x in fut[:7]))
            P("  (참고: 이 시리즈는 해당 월물 기준. 만기/롤오버 주간에는 누적치 해석 주의)")
    naver_fut = safe("네이버 선물", lambda: get("https://m.stock.naver.com/api/index/FUT/integration"), {}) or {}
    dti = naver_fut.get("dealTrendInfo")
    if dti:
        P(f"- (네이버 선물 투자자별, 억원 추정) 외국인 {dti.get('foreignValue')} / 기관 {dti.get('institutionalValue')} / 개인 {dti.get('personalValue')} ({dti.get('bizdate')})")
    P("- 옵션 투자자별 데이터: 공개 API 없음 → 마감 시황 기사 검색으로 보충할 것")

    # ---------------------------------------------------- top 10 by investor
    P("\n## 5. 외국인·기관 순매수/순매도 상위 (개별 종목만, ETF/ETN 제외, 억원; 금액은 포털 집계라 언론 보도치와 다소 다를 수 있음)")
    type_cache = {}

    def stock_type(sym):
        code = sym.lstrip("A")
        if code not in type_cache:
            d = safe(f"종목유형 {code}", lambda: get(f"https://m.stock.naver.com/api/stock/{code}/basic"), {}) or {}
            type_cache[code] = d.get("stockEndType", "unknown")
        return type_cache[code]

    # fallback only (primary check = Naver stockEndType)
    ETF_NAME = re.compile(r"ETN|^(KODEX|TIGER|RISE|KBSTAR|ACE|SOL|HANARO|KOSEF|ARIRANG|PLUS|TIMEFOLIO|"
                          r"1Q|UNICORN|TRUSTON|FOCUS|ITF|VITA|마이티|WON|KIWOOM|BNK|에셋플러스|마이다스|히어로즈|파워) ")

    # Daum caps the ranking at 30 rows incl. ETFs. When ETFs crowd out stocks, fill from a
    # top-market-cap universe using Naver per-stock trend (net qty x close = estimated amount).
    universe_cache = {}

    def universe_flows():
        if "rows" in universe_cache:
            return universe_cache["rows"]
        stocks = []
        for pg in (1, 2):
            d = safe(f"시총 상위 p{pg}", lambda pg=pg: get(f"https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page={pg}&pageSize=100"), {}) or {}
            stocks += [s for s in d.get("stocks", []) if s.get("stockEndType") == "stock"]

        def trend(s):
            try:
                t = get(f"https://m.stock.naver.com/api/stock/{s['itemCode']}/trend?pageSize=1")[0]
            except Exception:  # noqa: BLE001
                return None
            close = num(t["closePrice"])
            return {"code": s["itemCode"], "name": s["stockName"], "bizdate": t["bizdate"],
                    "FOREIGN": num(t["foreignerPureBuyQuant"]) * close, "INSTITUTION": num(t["organPureBuyQuant"]) * close,
                    "close": close, "chg": num(s.get("fluctuationsRatio"))}
        with ThreadPoolExecutor(10) as ex:
            rows = [r for r in ex.map(trend, stocks) if r]
        universe_cache["rows"] = rows
        return rows

    for inv_type, label in (("FOREIGN", "외국인"), ("INSTITUTION", "기관")):
        res = safe(f"{label} 매매상위", lambda t=inv_type: daum(
            "/api/trend/investor_purchase/?buyFieldName=straightPurchasePrice&buyOrder=desc"
            f"&sellFieldName=straightPurchasePrice&sellOrder=asc&limit=30&market=KOSPI&investorType={t}")["data"], {}) or {}
        for side, side_lbl in (("BUY", "순매수"), ("SELL", "순매도")):
            rows = res.get(side, [])
            with ThreadPoolExecutor(8) as ex:
                types = list(ex.map(lambda r: stock_type(r["symbolCode"]), rows))
            stocks, etfs = [], []
            for r, t in zip(rows, types):
                is_etf = t in ("etf", "etn") or (t == "unknown" and ETF_NAME.search(r["name"]))
                (etfs if is_etf else stocks).append(r)
            items = [{"name": r["name"], "code": r["symbolCode"].lstrip("A"), "amt": r["straightPurchasePrice"],
                      "close": r["tradePrice"], "chg": r["changeRate"] * 100, "est": False} for r in stocks]
            if len(items) < 10:
                have = {x["code"] for x in items}
                cand = [u for u in universe_flows() if u["code"] not in have and u[inv_type]
                        and (u[inv_type] > 0 if side == "BUY" else u[inv_type] < 0)
                        and (not last_session or u["bizdate"] == last_session.replace("-", ""))]
                cand.sort(key=lambda u: u[inv_type], reverse=(side == "BUY"))
                for u in cand[:10 - len(items)]:
                    items.append({"name": u["name"], "code": u["code"], "amt": u[inv_type], "close": u["close"],
                                  "chg": u["chg"], "est": True})
            P(f"\n### {label} {side_lbl} TOP10")
            top = items[:10]

            def krx_close(it):
                try:
                    d = daum(f"/api/quote/A{it['code']}/days?symbolCode=A{it['code']}&page=1&perPage=1&pagination=true")["data"][0]
                    if not last_session or d["date"][:10] == last_session:
                        it["close"], it["chg"], it["krx"] = d["tradePrice"], d["changeRate"] * 100, True
                except Exception:  # noqa: BLE001
                    pass
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(krx_close, top))
            for i, r in enumerate(top, 1):
                P(f"{i}. {r['name']}({r['code']}) {'≈' if r['est'] else ''}{eok(r['amt'])} | "
                  f"{'KRX종가' if r.get('krx') else '통합최종가(NXT포함)'} {r['close']:,.0f} ({r['chg']:+.2f}%)"
                  + (" [보충: 시총상위200 내 순매수수량×종가 추정]" if r["est"] else ""))
            if etfs:
                tot = sum(r["straightPurchasePrice"] for r in etfs)
                P(f"- [ETF/ETN 별도] 상위권 내 {len(etfs)}개 합계 {eok(tot)}: " +
                  ", ".join(f"{r['name']} {eok(r['straightPurchasePrice'])}" for r in etfs[:4]))

    # ---------------------------------------------------- sectors & themes
    P("\n## 6. 업종별 수급 (KOSPI 업종, 억원)")
    sectors = safe("업종 수급", lambda: daum("/api/investor/sectors?fieldName=&order=desc&perPage=100&market=KOSPI")["data"], []) or []
    sectors = [s for s in sectors if not re.search(r"대형주|중형주|소형주|코스피$|종합|^제조업$", s["sectorName"])]
    if sectors:
        fs = sorted(sectors, key=lambda s: s["foreignStraightPurchasePrice"])
        ins = sorted(sectors, key=lambda s: s["institutionStraightPurchasePrice"])
        P("- 외국인 순매수 업종: " + ", ".join(f"{s['sectorName']} {eok(s['foreignStraightPurchasePrice'])}" for s in fs[::-1][:5]))
        P("- 외국인 순매도 업종: " + ", ".join(f"{s['sectorName']} {eok(s['foreignStraightPurchasePrice'])}" for s in fs[:5]))
        P("- 기관 순매수 업종: " + ", ".join(f"{s['sectorName']} {eok(s['institutionStraightPurchasePrice'])}" for s in ins[::-1][:5]))
        P("- 기관 순매도 업종: " + ", ".join(f"{s['sectorName']} {eok(s['institutionStraightPurchasePrice'])}" for s in ins[:5]))

    P("\n## 7. 테마 등락 (네이버 테마, 코스피+코스닥 혼합 구성, 직전 거래일 기준. 등락률은 NXT 애프터마켓 포함 통합가 기준일 수 있음)")
    themes = []
    for pg in (1, 2, 3, 4):
        g = safe(f"테마 p{pg}", lambda pg=pg: get(f"https://m.stock.naver.com/api/stocks/theme?page={pg}&pageSize=100").get("groups", []), []) or []
        themes += g
        if len(g) < 100:
            break
    themes = [t for t in themes if (t.get("totalCount") or 0) >= 4 and "SPAC" not in t["name"]]
    themes.sort(key=lambda t: num(t["changeRate"]) or 0, reverse=True)

    def theme_leaders(no):
        d = safe(f"테마 {no}", lambda: get(f"https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=40"), {}) or {}
        st = [s for s in d.get("stocks", []) if s.get("stockEndType") == "stock"]
        kospi = [s for s in st if str(s.get("sosok")) == "0"]
        kospi.sort(key=lambda s: num(s.get("marketValue")) or 0, reverse=True)
        return ", ".join(f"{s['stockName']} {num(s['fluctuationsRatio']):+.1f}%" for s in kospi[:4]) or "(코스피 구성종목 없음)"

    if themes:
        top, bot = themes[:10], themes[-10:][::-1]
        with ThreadPoolExecutor(8) as ex:
            lead_top = list(ex.map(lambda t: theme_leaders(t["no"]), top[:6]))
            lead_bot = list(ex.map(lambda t: theme_leaders(t["no"]), bot[:6]))
        P(f"- 집계 테마 {len(themes)}개 (구성 4종목 이상)")
        P("### 강세 테마 TOP10")
        for i, t in enumerate(top):
            extra = f" | 코스피 대표: {lead_top[i]}" if i < len(lead_top) else ""
            P(f"{i+1}. {t['name']} {num(t['changeRate']):+.2f}% (상승 {t['riseCount']}/하락 {t['fallCount']}){extra}")
        P("### 약세 테마 TOP10")
        for i, t in enumerate(bot):
            extra = f" | 코스피 대표: {lead_bot[i]}" if i < len(lead_bot) else ""
            P(f"{i+1}. {t['name']} {num(t['changeRate']):+.2f}% (상승 {t['riseCount']}/하락 {t['fallCount']}){extra}")

    # ---------------------------------------------------- US overnight
    P("\n## 8. 미국 시장 (KRX 직전 거래일 이후 마감된 세션 전부)")
    since = last_session or (today - timedelta(days=1)).isoformat()

    def us_sessions(code):
        rows = get(f"https://api.stock.naver.com/index/{code}/price?page=1&pageSize=10")
        new = [r for r in rows if r["localTradedAt"][:10] >= since]
        return new, rows[:1]

    with ThreadPoolExecutor(8) as ex:
        sess = dict(zip([c for c, _ in US_INDICES],
                        ex.map(lambda c: safe(f"US {c}", lambda: us_sessions(c), ([], [])), [c for c, _ in US_INDICES])))
    n_sess = max((len(v[0]) for v in sess.values()), default=0)
    if n_sess == 0:
        P(f"- !! {since} 이후 마감된 미국 세션 없음(미국 휴장이었거나 데이터 지연). 아래는 가장 최근 세션 — 이미 KOSPI에 반영됐을 수 있음")
    else:
        P(f"- 반영 세션 수: {n_sess} ({'KRX 휴장 기간 포함 합산' if n_sess > 1 else '전일 1세션'})")
    for code, name in US_INDICES:
        rows, latest = sess.get(code) or ([], [])
        rows = rows or latest
        if not rows:
            P(f"- {name}: 데이터 수집 실패")
            continue
        cum = 1.0
        for r in rows:
            cum *= 1 + (num(r["fluctuationsRatio"]) or 0) / 100
        daily = ", ".join(f"{r['localTradedAt'][5:10]} {r['fluctuationsRatio']}%" for r in reversed(rows))
        P(f"- {name}: {rows[0]['closePrice']} | 세션별 {daily}" + (f" | 누적 {pct((cum-1)*100)}" if len(rows) > 1 else ""))

    def us_stock(code):
        d = get(f"https://api.stock.naver.com/stock/{code}/basic")
        return d
    with ThreadPoolExecutor(8) as ex:
        stk = list(ex.map(lambda c: safe(f"US {c[0]}", lambda: us_stock(c[0]), {}), US_STOCKS))
    P("- 주요 종목(최근 정규장 종가): " + ", ".join(
        f"{n} {d.get('closePrice')} ({d.get('fluctuationsRatio')}%)" for (c, n), d in zip(US_STOCKS, stk) if d))
    oms = [d for d in stk if d and isinstance(d.get("overMarketPriceInfo"), dict)]
    if oms:
        P("- 시간외(프리/애프터, 수집 시점): " + ", ".join(
            f"{d['stockName']} {d['overMarketPriceInfo'].get('fluctuationsRatio')}% ({d['overMarketPriceInfo'].get('tradingSessionType')})"
            for d in oms))

    futs = safe("US 선물", lambda: get("https://api.stock.naver.com/futures/nation/USA"), []) or []
    fmap = {"EScv1": "S&P500선물", "NQcv1": "나스닥100선물", "YMcv1": "다우선물", "RTYcv1": "러셀선물"}
    P("- 지수선물(현재): " + ", ".join(f"{fmap.get(f['reutersCode'], f['reutersCode'])} {f.get('closePrice')} ({f.get('fluctuationsRatio')}%)" for f in futs if f.get("reutersCode") in fmap))

    def mi(path):
        d = get(f"https://api.stock.naver.com/marketindex/{path}")
        d = d.get("exchangeInfo", d)
        return d
    with ThreadPoolExecutor(6) as ex:
        mis = list(ex.map(lambda p: safe(f"지표 {p[0]}", lambda: mi(p[0]), {}), MARKET_INDEX))
    P("- 금리/환율/원자재(현재): " + ", ".join(
        f"{n} {d.get('closePrice')} ({d.get('fluctuations')}, {d.get('fluctuationsRatio')}%)" for (p, n), d in zip(MARKET_INDEX, mis) if d))

    # ---------------------------------------------------- statistical baseline
    P("\n## 0. 통계 기준 예측 (예측 확률·범위의 출발점 — 이 값에서 ±10%p 이내로만 조정)")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import kospi_model
        P(kospi_model.run(today, drop_today=now.hour < 16))
    except Exception as e:  # noqa: BLE001
        P(f"- !! 통계 기준치 계산 실패: {type(e).__name__}: {e} → 이번 리포트는 확률·범위를 내지 말고 방향 판단만 '주관적'으로 표기")

    # ---------------------------------------------------- scorecard
    P("\n## 9. 예측 성적표")
    P(scorecard(kospi_hist or []))

    if errors:
        P("\n## 수집 오류 (해당 항목은 '데이터 수집 실패'로 표기)")
        for e in errors:
            P(f"- {e}")


def scorecard(kospi_hist):
    if not os.path.exists(PRED_FILE):
        return "- 기록된 예측 없음 (첫 실행)"
    actual = {r["localTradedAt"][:10]: r for r in kospi_hist}
    by_date = {}
    with open(PRED_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "p_model" in p:  # current schema only
                by_date[p["date"]] = p  # last record per date wins
    rows = []
    for d in sorted(by_date):
        a = actual.get(d)
        if not a:
            continue
        p = by_date[d]
        chg = num(a["fluctuationsRatio"])
        up = 1 if chg > 0 else 0
        pm, pf = p["p_model"] / 100, p["p_final"] / 100
        rows.append({"d": d, "p": p, "chg": chg, "up": up,
                     "hit_m": (pm >= 0.5) == bool(up), "hit_f": (pf >= 0.5) == bool(up),
                     "bri_m": (pm - up) ** 2, "bri_f": (pf - up) ** 2,
                     "in": p["low"] <= chg <= p["high"]})
    if not rows:
        return "- 채점 가능한 과거 예측 없음"
    r = rows[-1]
    p = r["p"]
    out = [f"- 직전 예측({r['d']}): 기준 P(상승) {p['p_model']:.0f}% → 최종 {p['p_final']:.0f}% ({p['direction']}), "
           f"68% 범위 {p['low']:+.2f}~{p['high']:+.2f}% → 실제 {r['chg']:+.2f}%",
           f"  기준 방향 {'적중' if r['hit_m'] else '빗나감'} / 최종 방향 {'적중' if r['hit_f'] else '빗나감'} / 범위 {'안' if r['in'] else '밖'}"]
    if p.get("adj_reason"):
        out.append(f"  당시 조정 사유: {p['adj_reason']}")

    def summ(rs, label):
        n = len(rs)
        return (f"- {label} {n}회: 방향 적중 기준 {sum(x['hit_m'] for x in rs)/n*100:.0f}% / 최종 {sum(x['hit_f'] for x in rs)/n*100:.0f}%, "
                f"Brier 기준 {sum(x['bri_m'] for x in rs)/n:.3f} / 최종 {sum(x['bri_f'] for x in rs)/n:.3f} (낮을수록 좋음, 0.25=동전던지기), "
                f"68% 범위 포함률 {sum(x['in'] for x in rs)/n*100:.0f}% (목표 ~68%)")
    out.append(summ(rows, "누적"))
    if len(rows) > 20:
        out.append(summ(rows[-20:], "최근"))
    if len(rows) >= 20:
        diff = sum(x["bri_f"] for x in rows) - sum(x["bri_m"] for x in rows)
        out.append(f"- 조정 효과: 최종이 기준보다 {'나음' if diff < 0 else '나쁨'} (Brier 합 차이 {diff:+.3f}). 20회 이상 '나쁨'이 이어지면 조정을 줄일 것")
    else:
        out.append(f"- 표본 {len(rows)}회 — 20회 전까지 적중률은 우연의 영향이 큼")
    out.append("- 채점 규칙: 실제 종가 등락 > 0 이면 '상승', 아니면 '하락'. 방향은 P(상승) ≥ 50%면 상승 예측.")
    return "\n".join(out)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — never let a parse bug kill the briefing silently
        import traceback
        print(f"!! 수집 스크립트 예외: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stdout)
        print("!! 위 이전까지 출력된 데이터만 사용하고, 나머지는 웹 검색으로 보충하되 출처를 표기할 것")
