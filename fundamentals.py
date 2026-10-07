#!/usr/bin/env python3
"""
fundamentals.py — HNImanshu Fundamentals page
==============================================
Two ways to run it:

  1. From build.py (website):
         import fundamentals
         fundamentals.build_site([csv1, csv2, csv3], "public/fundamentals.html", minify=minify_html_css)
     Only the CSV paths passed in are read (so test_*.csv files in the repo are ignored),
     the page gets a "Screener" back button and shares the screener's day / night theme.

  2. Locally (Sublime Ctrl+B / python fundamentals.py):
     Scans this folder for valuation CSVs, writes dashboard.html + a dated copy in
     saved_dashboards/, and opens it in the browser (same as the old offline script).

1-YEAR-BACK VIEW
    Rebuilds every company's FUNDAMENTALS as they stood one year before the download date
    (only results / shareholding already published by then) and shows how the business did since.
    Price-based numbers (PE, PB, yields) are not shown there - the CSVs have no old prices.

Needs only pandas.
"""

import glob
import json
import math
import os
import pathlib
import re
import sys
import webbrowser

import warnings

import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

# ----------------------------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(HERE, "dashboard.html")
OUT_SORTED_CSV = os.path.join(HERE, "stocks_sorted_by_pe.csv")
OUT_INDUSTRY_CSV = os.path.join(HERE, "industry_pe_summary.csv")
ARCHIVE_DIR = os.path.join(HERE, "saved_dashboards")      # dated copy of every local build
OPEN_BROWSER = True

# file name (without leading digits and "_valuation.csv")  ->  label shown on the dashboard
UNIVERSES = {"nifty500": "NIFTY 500", "niftysmallcap500": "Smallcap 500", "niftymicrocap250": "Microcap 250"}

GROWTH_YEARS = 5          # EPS / PAT / reserves CAGR window (uses fewer years if that is all there is)
CASH_YEARS = 5            # cash conversion and FCF are averaged over this many financial years
MAX_GAP_DAYS = 490        # a gap bigger than this between two annual points breaks the history
MAX_STALE_DAYS = 400      # latest annual figure must be newer than this vs the reference date
JUMP_PP = 10              # a single-quarter holding move bigger than this = likely merger / OFS / listing
BACK_DAYS = 365           # the "1Y back" snapshot date = download date minus this
# Trend score (improving fundamentals) - separate from Value score and Magic Formula
TREND_QTRS = 6            # quarterly checks look at the last 6 quarter-ends
TREND_SHARE = 2 / 3       # "consistently green" = TTM up year-on-year in >= 2/3 of those quarters (4 of 6)
TREND_MIN_CHECKS = 4      # a stock needs at least this many checks with data to get a Trend score
TREND_MIN_CHECKS_FIN = 3  # financials only have 4 applicable checks (no OPM / ROCE / cash conversion)
# what was already published on a snapshot date
ANNUAL_LAG_DAYS = 60      # annual results out within ~60 days of year end
QUARTER_LAG_DAYS = 45     # quarterly results within ~45 days
HOLDING_LAG_DAYS = 21     # shareholding pattern within ~21 days

# Government-owned companies (edit freely - symbols as in the CSV)
# PSU      = Central / State government holds a majority, directly or through another PSU
# SEMI_PSU = Government or PSUs hold a large but non-controlling stake, or control jointly
PSU = {
    "BANKBARODA", "BANKINDIA", "MAHABANK", "CANBK", "CENTRALBK", "INDIANB", "IOB", "PNB", "SBIN", "UCOBANK",
    "UNIONBANK", "IDBI", "J&KBANK",
    "LICI", "GICRE", "NIACL", "SBILIFE", "SBICARD", "CANHLIFE",
    "PFC", "RECLTD", "IRFC", "IREDA", "HUDCO", "IFCI",
    "ONGC", "OIL", "IOC", "BPCL", "HINDPETRO", "GAIL", "MRPL", "CHENNPETRO",
    "COALINDIA", "BHARATCOAL", "CMPDI", "NMDC", "NSLNISP", "SAIL", "NATIONALUM", "HINDCOPPER", "GMDCLTD", "MMTC",
    "NTPC", "NTPCGREEN", "NHPC", "SJVN", "NLCINDIA", "POWERGRID",
    "HAL", "BEL", "BDL", "BEML", "BHEL", "MAZDOCK", "COCHINSHIP", "GRSE",
    "IRCTC", "RVNL", "IRCON", "RITES", "RAILTEL", "CONCOR",
    "NBCC", "ENGINERSIN", "SCI", "FACT", "ITI",
}
SEMI_PSU = {
    "HINDZINC",    # Govt ~28%, Vedanta controls
    "IDEA",        # Govt ~49% after dues-to-equity conversion
    "TATACOMM",    # Govt ~26%
    "PETRONET",    # GAIL, IOC, BPCL, ONGC 12.5% each
    "IGL",         # GAIL + BPCL 22.5% each, Delhi govt 5%
    "MGL",         # GAIL 32.5%, Maharashtra govt 10%
    "LICHSGFIN",   # LIC ~45%
    "PNBHOUSING",  # PNB ~28%
    "UTIAMC",      # SBI, LIC, BoB, PNB ~10% each
}

FIN_RE = re.compile(r"financ|bank|nbfc|insur|lending|capital market|asset manag|broking", re.I)
MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
ANNUAL_FIELDS = ["A_PL_EPS_BASIC", "A_PL_PAT_CR", "A_PL_PBT_CR", "A_PL_INTEREST_CR", "A_PL_OTHER_INCOME_CR",
                 "A_PL_DIVIDEND_CR", "A_PL_OPM_PCT", "C_CF_OPERATING_CR", "C_CF_INVESTING_CR",
                 "B_BS_RESERVES_CR", "B_BS_TOTAL_ASSETS_CR"]
ANNUAL_RE = re.compile(r"^(%s)_(%s)(\d{4})$" % ("|".join(ANNUAL_FIELDS), MONTHS))
QTR_RE = re.compile(r"^(Q_EPS_GENERIC|Q_PL_PAT_CR|Q_PL_REVENUE_CR|Q_PL_EBITDA_CR)_Q(%s)(\d{4})$" % MONTHS)   # EBITDA -> quarterly OPM
SH_RE = re.compile(r"^SH_(PROMOTER|FII|DII|PUBLIC)_PCT_Q(%s)(\d{4})$" % MONTHS)
SH_KEY = {"PROMOTER": "pro", "PUBLIC": "pub", "FII": "fii", "DII": "dii"}

SCALARS = ["MARKET_CAP_CR", "CMP", "HIGH_52W", "PE", "BOOK_VALUE", "ROE_PCT", "ROCE_PCT", "DIV_YIELD_PCT",
           "TTM_PAT_CR", "SHARES_CR", "BS_RESERVES_CR", "OTHER_INCOME_TO_PBT_PCT", "INTEREST_COVERAGE",
           "OPM_CALC_PCT", "PL_OPM_PCT", "OPM_AVG_5YR_PCT", "DIVIDEND_CONSECUTIVE_YRS", "F_SCORE"]

# ----------------------------------------------------------------------------
# LOAD ALL VALUATION CSVs, REMOVE OVERLAPS
# ----------------------------------------------------------------------------
def universe_key(path):
    """Which list a file is, from its name: works for 'niftysmallcap500_valuation (1).csv', 'Smallcap 500.xlsx' ..."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    flat = re.sub(r"[^a-z0-9]", "", stem)
    for key in UNIVERSES:                              # known lists, matched anywhere in the name
        if key in flat:
            return key
    if "smallcap" in flat:
        return "niftysmallcap500"
    if "microcap" in flat:
        return "niftymicrocap250"
    stem = re.sub(r"\s*(\(\d+\)|- copy.*|copy)$", "", stem).strip()
    stem = re.sub(r"^\d+_", "", stem)
    return re.sub(r"[_ -]*valuation.*$", "", stem) or stem


def read_any(path):
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)                        # needs:  pip install openpyxl
    return pd.read_csv(path, low_memory=False, encoding_errors="replace")


def load_stocks(paths=None):
    """
    paths=None  -> scan this folder (local use), every csv / Excel with SYMBOL + CMP/PE columns.
    paths=[...] -> read ONLY these files (website build). Missing files are skipped.
    """
    own = ("stocks_sorted", "industry_pe", "dashboard")
    if paths is None:
        print("Looking for valuation files in", HERE)
        candidates = sorted(glob.glob(os.path.join(HERE, "*")))
    else:
        candidates = [str(p) for p in paths]
    groups = {}
    for f in candidates:
        name = os.path.basename(f)
        if not os.path.isfile(f):
            if paths is not None:
                print("  skip %-50s (file not found)" % name)
            continue
        if not name.lower().endswith((".csv", ".xlsx", ".xls")) or name.lower().startswith(own):
            continue
        try:
            head = read_any(f).head(0) if name.lower().endswith((".xlsx", ".xls")) else pd.read_csv(f, nrows=0, encoding_errors="replace")
        except Exception as e:
            print("  skip %-50s (cannot read: %s)" % (name, e))
            continue
        cols = {str(c).strip().upper() for c in head.columns}
        if "SYMBOL" not in cols or not ({"CMP", "PE"} & cols):
            print("  skip %-50s (no SYMBOL / CMP columns, not a valuation file)" % name)
            continue
        groups.setdefault(universe_key(f), []).append(f)
    if not groups:
        raise RuntimeError("No valuation files (csv with SYMBOL and CMP columns) found")

    order = list(UNIVERSES) + sorted(k for k in groups if k not in UNIVERSES)
    frames = []
    for rank, key in enumerate(k for k in order if k in groups):
        best = None
        for p in groups[key]:                           # several downloads of the same list -> newest wins
            d = read_any(p)
            d.columns = [str(c).strip() for c in d.columns]
            dt = pd.to_datetime(d["DATE_DOWNLOADED"], errors="coerce").max() if "DATE_DOWNLOADED" in d else pd.NaT
            score = (dt if pd.notna(dt) else pd.Timestamp.min, os.path.getmtime(p))
            if best is None or score > best[0]:
                best = (score, d, p)
        _, d, p = best
        label = UNIVERSES.get(key, key)
        d = d.assign(_UNI=label, _RANK=rank)
        frames.append(d)
        extra = "" if len(groups[key]) == 1 else "  (newest of %d copies)" % len(groups[key])
        print("Reading %-55s %4d rows  -> %s%s" % (os.path.basename(p), len(d), label, extra))

    missing = [lab for key, lab in UNIVERSES.items() if key not in groups]
    if missing:
        print("*** WARNING: no file found for %s" % ", ".join(missing))
        if paths is None:                               # local use only: hint where the file might be
            hints = []
            for folder in [os.path.expanduser("~/Downloads"), os.path.expanduser("~/Desktop"), os.path.expanduser("~/Documents"), os.path.dirname(HERE)]:
                hints += [f for f in glob.glob(os.path.join(folder, "*")) if re.search(r"smallcap|microcap", os.path.basename(f), re.I)]
            if hints:
                print("    Found these elsewhere - move them into %s:" % HERE)
                for h in hints[:10]:
                    print("      ", h)

    df = pd.concat(frames, ignore_index=True, sort=False)
    df = df[df["SYMBOL"].notna()].copy()
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    membership = df.groupby("SYMBOL", sort=False)["_UNI"].agg(lambda x: list(dict.fromkeys(x)))
    df["_NN"] = df.notna().sum(axis=1)
    before = len(df)
    df = df.sort_values(["_NN", "_RANK"], ascending=[False, True]).drop_duplicates("SYMBOL")
    df["_UNIS"] = df["SYMBOL"].map(membership)
    print("Combined %d rows -> %d unique stocks (%d overlaps removed)" % (before, len(df), before - len(df)))

    for c in ["SCREENER_URL", "COMPANY_NAME", "INDUSTRY", "DATE_DOWNLOADED"] + SCALARS:
        if c not in df.columns:
            df[c] = float("nan")
    df = df[df["CMP"].notna() | df["MARKET_CAP_CR"].notna() | df["COMPANY_NAME"].notna()].copy()   # drop empty placeholders
    miss = int(df["CMP"].isna().sum())
    if miss:
        print("Note: %d stocks have no price in the CSVs - shown with N/A" % miss)
    df["INDUSTRY"] = df["INDUSTRY"].fillna("Unclassified")
    for c in SCALARS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in df.columns:
        if ANNUAL_RE.match(c) or QTR_RE.match(c) or SH_RE.match(c):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[~(df["PE"] > 0), "PE"] = float("nan")       # loss-making / missing PE -> N/A
    asof = pd.to_datetime(df["DATE_DOWNLOADED"], errors="coerce").max()
    if pd.isna(asof):
        asof = pd.Timestamp.today().normalize()
    return df.reset_index(drop=True), asof


# ----------------------------------------------------------------------------
# TIME-SERIES HELPERS
# ----------------------------------------------------------------------------
def period_end(mon, yr):
    return pd.Timestamp(year=yr, month=MONTH_NUM[mon], day=1) + pd.offsets.MonthEnd(0)


def column_maps(df):
    """F[field] -> [(date, 'Mar 2024', column), ...] oldest -> newest, for annual, quarterly and holding columns."""
    F = {}
    for c in df.columns:
        for rx, keyf in ((ANNUAL_RE, lambda m: m.group(1)), (QTR_RE, lambda m: m.group(1)), (SH_RE, lambda m: SH_KEY[m.group(1)])):
            m = rx.match(c)
            if m:
                F.setdefault(keyf(m), []).append((period_end(m.group(2), int(m.group(3))), "%s %s" % (m.group(2).title(), m.group(3)), c))
    for k in F:
        F[k].sort(key=lambda x: x[0])
    return F


def series(row, cols, until=None):
    return [(dt, lab, float(row[c])) for dt, lab, c in cols if pd.notna(row[c]) and (until is None or dt <= until)]


def latest_run(pts, ref):
    """Latest unbroken run of annual points, empty if the newest one is stale vs ref."""
    if not pts or (ref - pts[-1][0]).days > MAX_STALE_DAYS:
        return []
    run = [pts[-1]]
    for p in reversed(pts[:-1]):
        if (run[0][0] - p[0]).days > MAX_GAP_DAYS:
            break
        run.insert(0, p)
    return run


def cagr(run, years=GROWTH_YEARS):
    """CAGR % over up to `years` (needs >= 2.5 years span and positive values at both ends)."""
    if len(run) < 3:
        return None, None, None, None
    start = run[-(years + 1)] if len(run) > years else run[0]
    end = run[-1]
    yrs = (end[0] - start[0]).days / 365.25
    if yrs < 2.5 or start[2] <= 0 or end[2] <= 0:
        return None, round(yrs, 1), start, end
    return round(((end[2] / start[2]) ** (1 / yrs) - 1) * 100, 2), round(yrs, 1), start, end


def holding(pts):
    """Holding-% change in percentage points over every quarter available."""
    if len(pts) < 2:
        return None
    first, last = pts[0], pts[-1]
    yrs = (last[0] - first[0]).days / 365.25
    if yrs < 0.4:
        return None
    target = last[0] - pd.DateOffset(years=1)
    near = [p for p in pts if abs((p[0] - target).days) <= 46]
    up = dn = 0
    jump = 0.0
    for a, b in zip(pts, pts[1:]):
        d = b[2] - a[2]
        jump = max(jump, abs(d))                          # any step, incl. across data gaps
        if (b[0] - a[0]).days > 100:
            continue
        up += d > 0.05
        dn += d < -0.05
    return {"now": round(last[2], 2), "chg": round(last[2] - first[2], 2), "yrs": round(yrs, 2),
            "since": first[1], "till": last[1],
            "chg1": round(last[2] - near[0][2], 2) if near else None,
            "up": int(up), "dn": int(dn), "jump": round(jump, 2),
            "s": [[p[1], round(p[2], 2)] for p in pts[-14:]]}


def num(v, nd=2):
    if v is None:
        return None
    try:
        if pd.isna(v) or math.isinf(float(v)):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(v), nd)


def ok(v):
    return v is not None and not pd.isna(v)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


# ----------------------------------------------------------------------------
# INPUTS: TODAY (straight from the CSV) and 1 YEAR BACK (rebuilt from history)
# ----------------------------------------------------------------------------
def inputs_now(r, F, asof, fin):
    cmp_, pe, roe = r["CMP"], r["PE"], r["ROE_PCT"]
    ttm = None
    if ok(cmp_) and ok(pe):
        ttm = cmp_ / pe                                   # exactly the EPS behind the CSV's PE
    elif ok(r["TTM_PAT_CR"]) and ok(r["SHARES_CR"]) and r["SHARES_CR"] > 0:
        ttm = r["TTM_PAT_CR"] / r["SHARES_CR"]
    pb, pb_src = None, None
    if ok(r["BOOK_VALUE"]) and r["BOOK_VALUE"] > 0 and ok(cmp_):
        pb, pb_src = cmp_ / r["BOOK_VALUE"], "csv"
    elif ok(pe) and ok(roe) and roe > 0:
        pb, pb_src = pe * roe / 100, "derived"
    opm_now = r["OPM_CALC_PCT"] if ok(r["OPM_CALC_PCT"]) else r["PL_OPM_PCT"]
    opm_t = (opm_now - r["OPM_AVG_5YR_PCT"]) if (not fin and ok(opm_now) and ok(r["OPM_AVG_5YR_PCT"])) else None
    return {"trend": trend_checks(r, F, asof, fin, lag=False),        # today: everything in the CSV counts
            "ref": asof, "acut": None, "scut": None, "price": cmp_, "pe": pe, "ttm": ttm, "mcap": r["MARKET_CAP_CR"],
            "pb": pb, "pbSrc": pb_src, "roe": roe, "roce": r["ROCE_PCT"], "dy": r["DIV_YIELD_PCT"],
            "oi": r["OTHER_INCOME_TO_PBT_PCT"], "icov": r["INTEREST_COVERAGE"], "opmT": opm_t, "hi52": r["HIGH_52W"]}


def inputs_fund(r, F, cut, fin, with_trend=True):
    """Fundamentals as they stood on `cut`, using only what was published by then. No prices."""
    acut = cut - pd.Timedelta(days=ANNUAL_LAG_DAYS)
    qcut = cut - pd.Timedelta(days=QUARTER_LAG_DAYS)
    scut = cut - pd.Timedelta(days=HOLDING_LAG_DAYS)
    ann = lambda k: series(r, F.get(k, []), acut)

    def fy(k, i=-1):
        a = ann(k)
        return a[i] if len(a) >= abs(i) else None

    def ttm4(k):                                          # last 4 published quarters
        q = series(r, F.get(k, []), qcut)[-4:]
        if len(q) == 4 and (q[-1][0] - q[0][0]).days < 300 and (cut - q[-1][0]).days < 200:
            return sum(p[2] for p in q), q[-1]
        return None, None
    ttm_eps, _ = ttm4("Q_EPS_GENERIC")
    ttm_pat, qlast = ttm4("Q_PL_PAT_CR")
    ttm_rev, _ = ttm4("Q_PL_REVENUE_CR")

    pat, res, res0 = fy("A_PL_PAT_CR"), fy("B_BS_RESERVES_CR"), fy("B_BS_RESERVES_CR", -2)
    shares_now = r["SHARES_CR"] if ok(r["SHARES_CR"]) and r["SHARES_CR"] > 0 else None
    cap = share_cap(r)                                    # share capital ~ book equity - reserves (today)
    eq = (res[2] + cap) if res else None
    eq0 = (res0[2] + cap) if res0 else None
    roe = None
    if pat and eq and pat[0] == res[0]:
        base = (eq + eq0) / 2 if (eq0 and eq0 > 0) else eq
        roe = pat[2] / base * 100 if base > 0 else None

    # ROCE ~ today's ROCE scaled by EBIT and capital (total assets) change
    roce = None
    ebit = lambda p, i: (p[2] + i[2]) if (p and i and p[0] == i[0]) else None
    pbt_all, int_all = series(r, F.get("A_PL_PBT_CR", [])), series(r, F.get("A_PL_INTEREST_CR", []))
    ta_all = series(r, F.get("B_BS_TOTAL_ASSETS_CR", []))
    e_then = ebit(fy("A_PL_PBT_CR"), fy("A_PL_INTEREST_CR"))
    e_now = ebit(pbt_all[-1] if pbt_all else None, int_all[-1] if int_all else None)
    ta_then, ta_now = fy("B_BS_TOTAL_ASSETS_CR"), (ta_all[-1] if ta_all else None)
    if ok(r["ROCE_PCT"]) and e_then and e_now and e_then > 0 and e_now > 0 and ta_then and ta_now and ta_then[2] > 0:
        roce = r["ROCE_PCT"] * (e_then / e_now) * (ta_now[2] / ta_then[2])

    pbt, intr, oth = fy("A_PL_PBT_CR"), fy("A_PL_INTEREST_CR"), fy("A_PL_OTHER_INCOME_CR")
    oi = (oth[2] / pbt[2] * 100) if (oth and pbt and pbt[2] > 0) else None
    icov = ((pbt[2] + intr[2]) / intr[2]) if (pbt and intr and intr[2] > 0) else None
    opm = ann("A_PL_OPM_PCT")[-5:]
    opm_t = (opm[-1][2] - sum(p[2] for p in opm) / len(opm)) if (not fin and len(opm) >= 3) else None
    pro = series(r, F.get("pro", []), scut)
    ttm = ttm_eps if ttm_eps is not None else ((ttm_pat / shares_now) if (ttm_pat is not None and shares_now) else None)
    return {"trend": trend_checks(r, F, cut, fin, lag=True) if with_trend else None,
            "ref": cut, "acut": acut, "scut": scut, "price": None, "pe": None, "ttm": ttm, "mcap": None,
            "pb": None, "pbSrc": None, "roe": roe, "roce": roce, "dy": None, "oi": oi, "icov": icov,
            "opmT": opm_t, "hi52": None,
            "fund": {"pat": ttm_pat, "rev": ttm_rev, "q": qlast[1] if qlast else None, "roe": roe, "roce": roce,
                     "opm": opm[-1][2] if opm else None, "fy": opm[-1][1] if opm else None,
                     "pro": pro[-1][2] if pro else None}}


def share_cap(r):
    """Share capital ~ today's book equity - reserves (0 if it doesn't look sane)."""
    shares = r["SHARES_CR"] if ok(r["SHARES_CR"]) and r["SHARES_CR"] > 0 else None
    if ok(r["BOOK_VALUE"]) and shares and ok(r["BS_RESERVES_CR"]):
        eq_now = r["BOOK_VALUE"] * shares
        if eq_now > 0 and 0 <= (eq_now - r["BS_RESERVES_CR"]) / eq_now <= 0.6:
            return eq_now - r["BS_RESERVES_CR"]
    return 0.0


# ----------------------------------------------------------------------------
# TREND SCORE: is the business getting better? (7 pass / fail checks)
# ----------------------------------------------------------------------------
def q_run(pts, ref):
    """Latest unbroken run of quarterly points (gap <= ~1 quarter), empty if the newest is stale."""
    if not pts or (ref - pts[-1][0]).days > 200:
        return []
    run = [pts[-1]]
    for p in reversed(pts[:-1]):
        if (run[0][0] - p[0]).days > 100:
            break
        run.insert(0, p)
    return run


def yoy_consistency(ttm):
    """ttm = [(date, value)] consecutive quarter-ends. Compares each of the last TREND_QTRS points with
    4 quarters earlier. Returns (passed?, ups, checked) or (None, 0, n) when fewer than 3 comparisons."""
    diffs = [ttm[i][1] - ttm[i - 4][1] for i in range(4, len(ttm))][-TREND_QTRS:]
    if len(diffs) < 3:
        return None, 0, len(diffs)
    ups = sum(1 for d in diffs if d >= 0)
    return ups >= math.ceil(len(diffs) * TREND_SHARE - 1e-9), ups, len(diffs)


def trend_checks(r, F, cut, fin, lag=True):
    """Fundamentals trend as of `cut`. lag=True -> only results published by then (1Y back view)."""
    acut = cut - pd.Timedelta(days=ANNUAL_LAG_DAYS if lag else 0)
    qcut = cut - pd.Timedelta(days=QUARTER_LAG_DAYS if lag else 0)
    scut = cut - pd.Timedelta(days=HOLDING_LAG_DAYS if lag else 0)
    chk, det = {}, {}

    def ttm_of(key):
        run = q_run(series(r, F.get(key, []), qcut), cut)
        return [(run[i][0], sum(p[2] for p in run[i - 3:i + 1])) for i in range(3, len(run))]

    # 1-2. profit and revenue: TTM not lower than a year earlier, in most of the last 6 quarters
    for k, key in (("pat", "Q_PL_PAT_CR"), ("rev", "Q_PL_REVENUE_CR")):
        t = ttm_of(key)
        res, ups, n = yoy_consistency(t)
        chk[k] = res
        if n >= 3:
            det[k] = "%d/%d qtrs" % (ups, n)
            a, b = t[-5][1], t[-1][1]
            det[k + "G"] = round((b / a - 1) * 100, 1) if a > 0 else None   # latest TTM vs a year ago, %

    # 3. operating margin (TTM EBITDA / TTM revenue), not for financials
    chk["opm"] = None
    if not fin:
        e = {d: v for d, v in ttm_of("Q_PL_EBITDA_CR")}
        rv = {d: v for d, v in ttm_of("Q_PL_REVENUE_CR")}
        t = [(d, e[d] / rv[d] * 100) for d in sorted(set(e) & set(rv)) if rv[d] > 0]
        t = q_run([(d, "", v) for d, v in t], cut)                       # keep consecutive quarters only
        t = [(p[0], p[2]) for p in t]
        res, ups, n = yoy_consistency(t)
        chk["opm"] = res
        if n >= 3:
            det["opm"] = "%d/%d qtrs" % (ups, n)                         # same evidence format as profit / revenue
        if len(t) >= 5:
            det["opmD"] = round(t[-1][1] - t[-5][1], 2)

    # 4. promoter holding: not lower than 6 quarters ago
    pro = q_run(series(r, F.get("pro", []), scut), cut)
    chk["pro"] = None
    if len(pro) >= 4:
        base = pro[-(TREND_QTRS + 1)] if len(pro) > TREND_QTRS else pro[0]
        d = pro[-1][2] - base[2]
        chk["pro"] = d >= -0.01
        det["proD"] = round(d, 2)

    # 5-6. ROE and ROCE proxy: latest FY vs 2 FY earlier (annual data only)
    cap = share_cap(r)
    pat = {p[0]: p[2] for p in series(r, F.get("A_PL_PAT_CR", []), acut)}
    res_ = {p[0]: p[2] for p in series(r, F.get("B_BS_RESERVES_CR", []), acut)}
    pbt = {p[0]: p[2] for p in series(r, F.get("A_PL_PBT_CR", []), acut)}
    intr = {p[0]: p[2] for p in series(r, F.get("A_PL_INTEREST_CR", []), acut)}
    ta = {p[0]: p[2] for p in series(r, F.get("B_BS_TOTAL_ASSETS_CR", []), acut)}
    roe = sorted((d, "", pat[d] / (res_[d] + cap) * 100) for d in pat if d in res_ and res_[d] + cap > 0)
    roce = sorted((d, "", (pbt[d] + intr[d]) / ta[d] * 100) for d in pbt if d in intr and d in ta and ta[d] > 0)
    for k, pts, skip in (("roe", roe, False), ("roce", roce, fin)):
        run = latest_run(pts, cut)
        chk[k] = None
        if not skip and len(run) >= 3:
            d = run[-1][2] - run[-3][2]
            chk[k] = d >= 0
            det[k + "D"] = round(d, 2)

    # 7. cash conversion: last 3 FY (CFO / PAT) vs the 3 FY before, not for financials
    chk["cc"] = None
    if not fin:
        cfo = {p[0]: p[2] for p in series(r, F.get("C_CF_OPERATING_CR", []), acut)}
        yrs = [p for p in latest_run(sorted((d, "", v) for d, v in pat.items()), cut) if p[0] in cfo]
        w = min(3, len(yrs) // 2)
        if w >= 2:
            new, old = yrs[-w:], yrs[-2 * w:-w]
            sn, so = sum(p[2] for p in new), sum(p[2] for p in old)
            if sn > 0 and so > 0:
                ccn, cco = sum(cfo[p[0]] for p in new) / sn, sum(cfo[p[0]] for p in old) / so
                chk["cc"] = ccn >= cco
                det["ccNow"], det["ccOld"] = round(ccn, 2), round(cco, 2)

    done = [v for v in chk.values() if v is not None]
    if len(done) < (TREND_MIN_CHECKS_FIN if fin else TREND_MIN_CHECKS):
        return None
    passed = sum(1 for v in done if v)
    return {"pct": round(passed / len(done) * 100), "pass": passed, "of": len(done),
            "c": {k: (None if v is None else int(v)) for k, v in chk.items()}, "d": det}


def growth_pct(then, now):
    """% growth, or a label when the base is a loss."""
    if then is None or now is None:
        return None, None
    if then > 0:
        return round((now / then - 1) * 100, 2), None
    if now > 0:
        return None, "turned profitable"
    return None, "still loss" if now <= 0 else None


# ----------------------------------------------------------------------------
# METRICS (same maths for both dates)
# ----------------------------------------------------------------------------
def build_record(r, F, inp, fin):
    acut, ref = inp["acut"], inp["ref"]
    price, pe, ttm, mcap = inp["price"], inp["pe"], inp["ttm"], inp["mcap"]
    eps_run = latest_run(series(r, F.get("A_PL_EPS_BASIC", []), acut), ref)
    pat_run = latest_run(series(r, F.get("A_PL_PAT_CR", []), acut), ref)
    cfo = {p[0]: p[2] for p in series(r, F.get("C_CF_OPERATING_CR", []), acut)}
    cfi = {p[0]: p[2] for p in series(r, F.get("C_CF_INVESTING_CR", []), acut)}
    if ttm is None and eps_run:
        ttm = eps_run[-1][2]

    pb = inp["pb"]
    bv = (price / pb) if (pb and ok(price)) else None
    graham = math.sqrt(22.5 * ttm * bv) if (ttm and ttm > 0 and bv and bv > 0) else None
    oi = inp["oi"]
    core_pe = pe / (1 - oi / 100) if (not fin and ok(pe) and ok(oi) and -50 <= oi < 95) else None

    eps_cagr, eps_y, e0, e1 = cagr(eps_run)
    pat_cagr, pat_y, _, _ = cagr(pat_run)
    pat_at = {p[0]: p[2] for p in pat_run}
    g, g_y, g_src, sh_cagr = eps_cagr, eps_y, "EPS", None
    if e0 and e1 and e0[2] > 0 and e1[2] > 0 and pat_at.get(e0[0], 0) > 0 and pat_at.get(e1[0], 0) > 0:
        ratio = (pat_at[e1[0]] / e1[2]) / (pat_at[e0[0]] / e0[2])      # implied share count change
        yrs_sh = (e1[0] - e0[0]).days / 365.25
        if ratio > 2.5 or ratio < 0.4:
            g, g_y, g_src = pat_cagr, pat_y, "PAT"                       # pre-IPO / unadjusted share base
        elif yrs_sh > 0:
            sh_cagr = (ratio ** (1 / yrs_sh) - 1) * 100
    elif eps_cagr is None and pat_cagr is not None and not eps_run:
        g, g_y, g_src = pat_cagr, pat_y, "PAT"

    last5 = eps_run[-5:]
    loss5 = sum(1 for p in last5 if p[2] <= 0) if len(last5) >= 3 else None
    lo5pct = None
    if len(last5) >= 3 and ttm is not None:
        lo5 = min(min(p[2] for p in last5), ttm)
        lo5pct = ((ttm - lo5) / abs(lo5) * 100) if lo5 != 0 else (0 if ttm <= 0 else None)

    cc = fcf = fcfy = None
    if not fin:
        yrs = [p for p in pat_run[-CASH_YEARS:] if p[0] in cfo]
        if len(yrs) >= 3:
            sp = sum(p[2] for p in yrs)
            if sp > 0:
                cc = sum(cfo[p[0]] for p in yrs) / sp
        yrs = [p for p in yrs if p[0] in cfi]
        if len(yrs) >= 3:
            fcf = sum(cfo[p[0]] + cfi[p[0]] for p in yrs) / len(yrs)
            if ok(mcap) and mcap > 0:
                fcfy = fcf / mcap * 100

    h = {}
    for key in ("pro", "pub", "fii", "dii"):
        v = holding(series(r, F.get(key, []), inp["scut"]))
        if v:
            h[key] = v

    return {
        "sym": r["SYMBOL"],
        "name": r["COMPANY_NAME"] if isinstance(r["COMPANY_NAME"], str) else r["SYMBOL"],
        "ind": r["INDUSTRY"], "fin": fin, "uni": r["_UNIS"],
        "psu": "psu" if r["SYMBOL"] in PSU else ("semi" if r["SYMBOL"] in SEMI_PSU else None),
        "url": r["SCREENER_URL"] if isinstance(r["SCREENER_URL"], str) else None,
        "mcap": num(mcap, 0), "cmp": num(price), "hi52": num(inp["hi52"]),
        "pe": num(pe), "cpe": num(core_pe), "roe": num(inp["roe"]), "roce": num(inp["roce"]),
        "dy": num(inp["dy"]), "ttm": num(ttm),
        "pb": num(pb), "pbSrc": inp["pbSrc"], "graham": num(graham),
        "cagr": g, "cagrY": g_y, "cagrSrc": g_src, "shg": num(sh_cagr),
        "loss5": loss5, "lo5pct": num(lo5pct),
        "cc": num(cc), "fcf": num(fcf, 0), "fcfy": num(fcfy),
        "oi": num(oi), "icov": num(inp["icov"]), "opmT": num(inp["opmT"]),
        "divYrs": num(r["DIVIDEND_CONSECUTIVE_YRS"], 0), "fs": num(r["F_SCORE"], 0),
        "eps": [[p[1], round(p[2], 2)] for p in eps_run[-15:]] if len(eps_run) >= 2 else [],
        "h": h,
        "tr": inp.get("trend"),
    }


def build_all(df, asof):
    F = column_maps(df)
    back = asof - pd.Timedelta(days=BACK_DAYS)
    now, then = [], []
    for _, r in df.iterrows():
        fin = bool(FIN_RE.search(str(r["INDUSTRY"])))
        now.append(build_record(r, F, inputs_now(r, F, asof, fin), fin))
        inp = inputs_fund(r, F, back, fin)
        rec = build_record(r, F, inp, fin)
        rec["mcap"] = num(r["MARKET_CAP_CR"], 0)          # today's size, used only for the Mcap filters
        a, b = inp["fund"], inputs_fund(r, F, asof, fin, with_trend=False)["fund"]   # trend not needed here
        patG, patL = growth_pct(a["pat"], b["pat"])
        revG, _ = growth_pct(a["rev"], b["rev"])
        d = lambda k: round(b[k] - a[k], 2) if (a[k] is not None and b[k] is not None) else None
        rec["since"] = {"patG": patG, "patL": patL, "revG": revG, "roeD": d("roe"), "roceD": d("roce"),
                        "opmD": d("opm"), "proD": d("pro"), "qThen": a["q"], "qNow": b["q"],
                        "fyThen": a["fy"], "fyNow": b["fy"]}
        then.append(rec)
    return now, then, back


def write_csvs(df):
    """Local use only: sorted-by-PE and industry summary CSVs."""
    d = df.copy()
    rows = []
    for ind, g in d.groupby("INDUSTRY"):
        pe = g.dropna(subset=["PE"])
        w = pe.dropna(subset=["MARKET_CAP_CR"])
        weighted = (w["MARKET_CAP_CR"].sum() / (w["MARKET_CAP_CR"] / w["PE"]).sum()) if len(w) else float("nan")
        rows.append({"INDUSTRY": ind, "INDUSTRY_PE_MEDIAN": pe["PE"].median(),
                     "INDUSTRY_PE_MEAN": pe["PE"].mean(), "INDUSTRY_PE_WEIGHTED": weighted,
                     "STOCKS": len(g), "STOCKS_WITH_PE": len(pe)})
    summ = pd.DataFrame(rows).sort_values("INDUSTRY_PE_MEDIAN")
    summ.round(2).to_csv(OUT_INDUSTRY_CSV, index=False)
    d = d.merge(summ[["INDUSTRY", "INDUSTRY_PE_MEDIAN"]], on="INDUSTRY", how="left")
    d["PREMIUM_DISCOUNT_VS_INDUSTRY_PCT"] = (d["PE"] / d["INDUSTRY_PE_MEDIAN"] - 1) * 100
    d["PB"] = d["CMP"] / d["BOOK_VALUE"].where(d["BOOK_VALUE"] > 0)
    d["LISTS"] = d["_UNIS"].map(lambda x: " + ".join(x))
    d = d.sort_values(["INDUSTRY", "PE"], na_position="last")
    cols = ["INDUSTRY", "SYMBOL", "COMPANY_NAME", "LISTS", "CMP", "PE", "INDUSTRY_PE_MEDIAN",
            "PREMIUM_DISCOUNT_VS_INDUSTRY_PCT", "PB", "ROE_PCT", "ROCE_PCT", "DIV_YIELD_PCT", "MARKET_CAP_CR"]
    d[cols].round(2).to_csv(OUT_SORTED_CSV, index=False)


# ----------------------------------------------------------------------------
# HTML TEMPLATE  (HNImanshu palette, shares the screener's day / night theme)
# ----------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HNImanshu — Fundamentals</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=DM+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23141414'/%3E%3Ctext x='16' y='23' font-family='Georgia,serif' font-size='18' font-weight='bold' fill='%23a89060' text-anchor='middle'%3EH%3C/text%3E%3C/svg%3E">
<style>
:root{--mono:'IBM Plex Mono',ui-monospace,monospace;--serif:var(--mono);--sans:'DM Sans',system-ui,-apple-system,"Segoe UI",sans-serif}
body.night{
  --bg:#0e0e0f;--panel:#141416;--panel2:#1a1a1d;--line:#323238;--ink:#f0ece4;--mute:#c0b8ae;--faint:#807870;
  --brass:#d4b47a;--brassbg:rgba(212,180,122,.12);--good:#78cc90;--goodbg:rgba(120,204,144,.13);
  --bad:#e09878;--badbg:rgba(224,152,120,.13);--warn:#e0b840;--warnbg:rgba(224,184,64,.13);--hover:#1f1f23;--accent:#d4b47a}
body.day{
  --bg:#c8c1b5;--panel:#bdb6a8;--panel2:#cdc6b8;--line:#7a7468;--ink:#060402;--mute:#281e12;--faint:#4a4238;
  --brass:#4c2e0a;--brassbg:rgba(76,46,10,.12);--good:#075e1a;--goodbg:rgba(7,94,26,.12);
  --bad:#781c00;--badbg:rgba(120,28,0,.12);--warn:#482c00;--warnbg:rgba(72,44,0,.13);--hover:#b5ae9f;--accent:#4c2e0a}
*{box-sizing:border-box}
/* theme on <html> too: the page scrollbar belongs to <html>, so it can't see body.day / body.night vars */
:root{color-scheme:dark;--sdot:#4a4640;--sdot-hi:#d4b47a;--drop-shadow:0 10px 28px rgba(0,0,0,.55),0 2px 6px rgba(0,0,0,.35)}
:root[data-theme=day]{color-scheme:light;--sdot:#7a7062;--sdot-hi:#4c2e0a;--drop-shadow:0 10px 28px rgba(40,30,18,.20),0 2px 6px rgba(40,30,18,.12)}
/* scrollbar = a single dot riding on an invisible thumb, track merged with the background */
::-webkit-scrollbar{width:10px;height:10px;background:transparent}
::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:transparent}
::-webkit-scrollbar-button{display:none;width:0;height:0}
::-webkit-scrollbar-thumb{background:radial-gradient(circle at center,var(--sdot) 0 2.5px,transparent 3px);border:0}
::-webkit-scrollbar-thumb:hover,::-webkit-scrollbar-thumb:active{background:radial-gradient(circle at center,var(--sdot-hi) 0 3px,transparent 3.5px)}
@supports (-moz-appearance:none){*{scrollbar-width:thin;scrollbar-color:var(--sdot) transparent}}   /* Firefox has no dot option: thinnest bar instead */

/* smart filter box */
.fbox{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:12px}
.fbox .t{font:600 10.5px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:1px;margin-right:2px}
.presets{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid var(--line)}
.preset{font:600 11.5px var(--mono);padding:5px 11px;border:1px solid var(--line);border-radius:999px;background:transparent;color:var(--mute);cursor:pointer;letter-spacing:.2px}
.preset:hover{border-color:var(--brass);color:var(--ink)}
.preset.on{background:var(--brass);border-color:var(--brass);color:var(--bg)}
.preset-custom{font:600 11px var(--mono);color:var(--faint);padding:0 6px;font-style:italic}
.frow{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}
.frow label,.fpanel label{color:var(--mute);font-size:12.5px;display:inline-flex;align-items:center;gap:6px}
.fbtns{display:inline-flex;gap:6px;margin-left:auto}
.btn.ghost{background:transparent;height:30px;font-size:11.5px}
.btn.ghost.open{border-color:var(--brass);color:var(--brass)}
.btn .car{display:inline-block;transition:transform .15s}
.btn.open .car{transform:rotate(180deg)}
.btn .cnt{background:var(--brass);color:var(--bg);border-radius:999px;padding:1px 6px;font-size:10px}
.fpanel{display:none;flex-wrap:wrap;align-items:center;gap:8px 16px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)}
.fpanel.open{display:flex}
.fpanel .bar{margin:0}
.fpanel .frow{width:100%}

/* table card: 15-row window + footer with count and help text */
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card .wrap{border:0;border-radius:0;max-height:none}
.tfoot{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;padding:8px 14px;border-top:1px solid var(--line);background:var(--panel2);font-size:11.5px;color:var(--mute);line-height:1.55}
.tcount{font:600 11px var(--mono);color:var(--brass);white-space:nowrap}
.thint{flex:1;min-width:260px}
.thint a{color:var(--brass)}

/* 1Y back note: small (i) popover instead of a banner */
.backnote{position:relative}
.info{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;border:1px solid var(--brass);background:transparent;color:var(--brass);font:italic 700 10px Georgia,serif;cursor:pointer;vertical-align:1px;margin-left:2px;padding:0}
.pop{display:none;position:absolute;top:calc(100% + 8px);left:0;z-index:70;width:min(420px,86vw);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;box-shadow:var(--drop-shadow);color:var(--mute);font-size:12px;line-height:1.55;white-space:normal}
.pop b{display:block;color:var(--ink);font:600 12px var(--mono);margin-bottom:4px}
.pop.open{display:block}

/* desktop: theme lives on the screener (shared setting), so one button less here */
@media (min-width:701px){#themeBtn{display:none}}
@media (max-width:700px){.lg{display:none} .fbtns{margin-left:0} .thint{min-width:0} .pop{left:auto;right:-40px}}

/* custom dropdown: replaces the native <select> popup (which ignores the theme) */
.fsel{position:relative;display:inline-flex;align-items:center}
.fsel > select{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.fsel-btn{display:inline-flex;align-items:center;gap:8px;background:transparent;border:0;padding:0 2px;color:var(--ink);font:inherit;font-size:12.5px;cursor:pointer;line-height:1}
.fsel-btn.boxed{border:1px solid var(--line);border-radius:6px;padding:6px 10px;background:var(--bg)}
.fsel-btn.boxed:hover,.fsel.open .fsel-btn.boxed{border-color:var(--brass)}
.fsel-btn svg{flex-shrink:0;opacity:.7;transition:transform .15s}
.fsel.open .fsel-btn svg{transform:rotate(180deg)}
.fsel-menu{display:none;position:absolute;top:calc(100% + 8px);left:-8px;min-width:calc(100% + 16px);z-index:60;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px;box-shadow:var(--drop-shadow)}
.fsel.open .fsel-menu{display:block}
.fsel-opt{display:block;width:100%;text-align:left;white-space:nowrap;padding:8px 12px;border:0;border-radius:5px;background:transparent;color:var(--mute);font:inherit;font-size:12.5px;cursor:pointer}
.fsel-opt:hover,.fsel-opt:focus-visible{background:var(--hover);color:var(--ink);outline:none}
.fsel-opt.on{color:var(--brass);background:var(--brassbg);font-weight:600}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans);font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px}
:focus-visible{outline:2px solid var(--brass);outline-offset:2px;border-radius:4px}

header.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:12px 24px;background:var(--panel);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:18px;flex-wrap:wrap;min-width:0}
.logo{display:flex;align-items:center;gap:9px}
.logo:hover{text-decoration:none}
.logo svg{width:32px;height:32px;flex-shrink:0}
.wm{font:700 13px var(--mono);display:flex;align-items:baseline;gap:1px}
.wm .hni{color:var(--brass);letter-spacing:2px}
.wm .manshu{color:var(--faint);font-weight:400;font-size:11px;letter-spacing:.5px}
.tagline{font:7.5px var(--mono);color:var(--faint);letter-spacing:1.5px;text-transform:uppercase;margin-top:2px}
.brand .sub{color:var(--mute);font-size:12.5px}
.brand .sub b{color:var(--ink);font-weight:600}
.global{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:7px;height:32px;line-height:1;padding:0 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--mute);font-size:12.5px;cursor:pointer;user-select:none}
.chip:has(input:checked){border-color:var(--brass);color:var(--ink);background:var(--brassbg)}
.chip input{margin:0;accent-color:var(--brass);vertical-align:middle}
.chip.ytoggle{position:relative}
.chip.ytoggle input{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.chip.ytoggle::before{content:"";width:9px;height:9px;border-radius:50%;border:1.5px solid currentColor}
.chip.ytoggle:has(input:checked){background:var(--brass);border-color:var(--brass);color:var(--bg);font-weight:600}
.chip.ytoggle:has(input:checked)::before{background:currentColor}
.chip:has(input:focus-visible){outline:2px solid var(--brass);outline-offset:2px}
.chip select{border:0;background:transparent;padding:0 2px;color:var(--ink);font-size:12.5px}
.banner{display:flex;gap:10px 18px;flex-wrap:wrap;align-items:baseline;background:var(--brassbg);border:1px solid var(--brass);border-radius:10px;padding:12px 16px;margin-bottom:16px;color:var(--ink)}
.banner b{font:600 14px var(--mono)}
.banner span{color:var(--mute);font-size:13px}
.btn{font:600 12px var(--mono);display:inline-flex;align-items:center;gap:6px;height:32px;line-height:1;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink);cursor:pointer;letter-spacing:.3px}
.btn:hover{border-color:var(--brass);text-decoration:none}
.btn.primary{background:var(--brass);border-color:var(--brass);color:var(--bg)}
.btn.back{border-color:var(--brass);color:var(--brass);background:transparent}
.btn.back:hover{background:var(--brassbg)}
.btn.theme{width:32px;justify-content:center;padding:0;font-size:14px}

nav{display:flex;gap:2px;padding:0 16px;border-bottom:1px solid var(--line);overflow-x:auto;position:sticky;top:0;z-index:5;background:var(--panel)}
nav a{padding:11px 14px 9px;color:var(--mute);font:500 12px var(--mono);letter-spacing:.3px;border-bottom:2px solid transparent;white-space:nowrap}
nav a:hover{color:var(--ink);text-decoration:none}
nav a.on{color:var(--brass);border-color:var(--brass)}

main{padding:20px 24px 56px;max-width:1680px;margin:0 auto}
h2.page{font:700 18px/1.2 var(--mono);margin:2px 0 14px;letter-spacing:.2px}
.bar{display:flex;gap:10px 18px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.bar label{color:var(--mute);font-size:12.5px;display:inline-flex;align-items:center;gap:6px}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:12px}
.box .t{font:600 11px var(--mono);color:var(--brass);margin-right:4px;text-transform:uppercase;letter-spacing:1px}
input,select{font:inherit;font-size:12.5px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 9px}
input[type=number]{width:74px;font-family:var(--mono)}
input[type=search]{width:300px;max-width:100%;padding:7px 12px}
input[type=search]::placeholder{color:var(--faint)}
input[type=checkbox]{accent-color:var(--brass);width:15px;height:15px}
input:focus,select:focus{border-color:var(--brass);outline:none}

.stats{display:flex;flex-wrap:wrap;margin:6px 0 16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.stat{padding:10px 24px 10px 0;margin-right:24px;border-right:1px solid var(--line)}
.stat:last-child{border-right:0}
.stat b{display:block;font:700 20px/1.2 var(--mono);color:var(--brass)}
.stat > span{color:var(--faint);font-size:11.5px}

.wrap{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:auto;max-height:78vh}
table{border-collapse:separate;border-spacing:0;width:100%}
th,td{padding:8px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
td{font:12.5px var(--mono)}
td.l{font-family:var(--sans)}
th{position:sticky;top:0;z-index:2;background:var(--panel2);color:var(--faint);font:600 10px var(--mono);text-transform:uppercase;letter-spacing:.7px;cursor:pointer;user-select:none;border-bottom:2px solid var(--line)}
th:hover{color:var(--ink)}
th.sorted{color:var(--brass)}
th.l,td.l{text-align:left}
td.idx{color:var(--faint);font:11px var(--mono);width:1%}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--hover)}
tbody tr:hover td:first-child{box-shadow:inset 3px 0 0 var(--brass)}
tbody tr.click{cursor:pointer}
td a.sym{font:700 12.5px var(--mono);color:var(--ink);letter-spacing:.01em}
td a.ind{color:var(--mute)}
td .co{color:var(--mute);display:inline-block;max-width:260px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom}

.pill{display:inline-block;padding:2px 8px;border-radius:4px;font:600 11px var(--mono)}
.disc{background:var(--goodbg);color:var(--good)}
.prem{background:var(--badbg);color:var(--bad)}
.warn{background:var(--warnbg);color:var(--warn)}
.na{color:var(--faint)}
.up{color:var(--good)}
.dn{color:var(--bad)}
.tag{display:inline-block;margin-left:6px;padding:0 5px;border-radius:3px;font:700 9.5px var(--mono);vertical-align:1px;border:1px solid var(--brass);color:var(--brass)}
.tag.semi{border-style:dashed}

.meter{display:inline-flex;align-items:center;gap:8px}
.meter i{display:block;width:64px;height:6px;border-radius:3px;background:var(--line);overflow:hidden}
.meter i b{display:block;height:100%;background:var(--brass);border-radius:3px}
.meter span{min-width:22px;font-weight:700}
.rank{display:inline-block;min-width:26px;font-weight:700}
.rank.top{color:var(--brass)}

.hint{color:var(--mute);font-size:12.5px;margin:12px 2px;line-height:1.65;max-width:110ch}
.hint a{color:var(--brass)}
.back-link{display:inline-block;margin-bottom:8px;color:var(--brass);font:12px var(--mono)}
.doc{max-width:900px;line-height:1.7}
.doc h2{font:700 16px var(--mono);margin:30px 0 8px;padding-top:14px;border-top:1px solid var(--line);color:var(--brass)}
.doc h2:first-child{border-top:0;margin-top:0;padding-top:0}
.doc p{margin:6px 0;max-width:80ch}
.doc code{background:var(--panel2);border:1px solid var(--line);padding:1px 6px;border-radius:5px;font:12px var(--mono)}
.doc .wrap{max-height:none;margin:8px 0 12px}
.doc td,.doc th{text-align:left;white-space:normal;cursor:default;position:static;vertical-align:top}
.doc td{font-family:var(--sans)}
@media (max-width:700px){
  header.top{padding:10px 12px;gap:10px}
  .brand{gap:8px}
  .brand .sub{font-size:11.5px;width:100%}
  .global{gap:6px;width:100%}
  .chip,.btn{height:30px;font-size:11.5px;padding:0 9px}
  .btn.theme{width:30px;padding:0}
  nav{padding:0 6px}
  nav a{padding:10px 10px 8px;font-size:11px}
  main{padding:14px 10px 40px}
  h2.page{font-size:16px}
  .bar{gap:8px 12px}
  .box{padding:10px}
  input[type=search]{width:100%!important}
  th,td{padding:7px 9px}
  .stat{padding:8px 14px 8px 0;margin-right:14px}
  .stat b{font-size:16px}
  .wrap{max-height:72vh}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body class="night">
<script>
(function(){ var t = 'night'; try { if (localStorage.getItem('hnimanshu_theme') === 'day') t = 'day'; } catch(e){}
  document.body.classList.remove('night', 'day'); document.body.classList.add(t);
  document.documentElement.dataset.theme = t;   /* scrollbar + dropdown shadow read this */ })();
</script>
<header class="top">
  <div class="brand">
    <a class="logo" href="index.html" id="homeLink" aria-label="HNImanshu screener">
      <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg"><rect width="32" height="32" rx="7" fill="var(--panel2)"/><text x="16" y="23" font-family="Georgia,serif" font-size="18" font-weight="bold" fill="var(--brass)" text-anchor="middle">H</text></svg>
      <div><div class="wm"><span class="hni">HNI</span><span class="manshu">manshu</span></div><div class="tagline">Fundamentals</div></div>
    </a>
    <div class="sub" id="meta"></div>
  </div>
  <div class="global">
    <a class="btn back" id="backBtn" href="index.html"><span class="lg">Back to </span>Screener</a>
    <label class="chip">List <select id="uniSel"></select></label>
    <label class="chip"><input type="checkbox" id="hidePSU"> Hide PSU</label>
    <label class="chip"><input type="checkbox" id="hideSemi"> Hide semi-PSU</label>
    <label class="chip ytoggle" id="backChip"><input type="checkbox" id="back1y"> 1Y back</label>
    <button class="btn primary" id="saveHtml">Save as HTML</button>
    <button class="btn theme" id="themeBtn" aria-label="Toggle day / night theme" title="Day / night">&#9680;</button>
  </div>
</header>
<nav id="nav">
  <a href="#/" data-t="home">Industries</a>
  <a href="#/value" data-t="value">Value screen</a>
  <a href="#/magic" data-t="magic">Magic Formula</a>
  <a href="#/promoter" data-t="promoter">Promoter holding</a>
  <a href="#/public" data-t="public">Public holding</a>
  <a href="#/improving" data-t="improving">Improving</a>
  <a href="#/method" data-t="method">Methodology</a>
</nav>
<main id="view"></main>

<script>
const WEB = __WEB__;
const D = __DATA__;
let S = D.stocks, BACK = false;
const $ = (s, el=document) => el.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = (v, d=1) => v == null || !isFinite(v) ? '–' : v.toLocaleString('en-IN', {minimumFractionDigits:d, maximumFractionDigits:d});
const NA = '<span class="na">–</span>';
/* storage keys are prefixed so they never clash with the screener on the same domain */
const LSP = 'hnif_', mem = {};
const lsGet = k => { if (k in mem) return mem[k]; try { return localStorage.getItem(LSP + k); } catch (e) { return null; } };
const lsSet = (k, v) => { mem[k] = String(v); try { localStorage.setItem(LSP + k, v); } catch (e) {} };
const lsJson = (k, def) => { try { return Object.assign({}, def, JSON.parse(lsGet(k) || '{}')); } catch (e) { return Object.assign({}, def); } };
let method = lsGet('pemethod') || 'median';
/* global PSU filter: applies to every tab (industry benchmarks still use all stocks) */
const GF = lsJson('gf', {psu:false, semi:false, back:false, uni:'all'});
const QP = new URLSearchParams(location.search);
if (QP.get('uni')) GF.uni = QP.get('uni');               // list passed from the screener's active tab
const vis = s => !(GF.psu && s.psu === 'psu') && !(GF.semi && s.psu === 'semi')
  && (!GF.uni || GF.uni === 'all' || (s.uni || []).includes(GF.uni));
const V = () => S.filter(vis);
const sortState = {}, searchState = {};

/* ---------- industry maths ---------- */
const median = a => { const s=[...a].filter(x=>x!=null&&isFinite(x)).sort((x,y)=>x-y), m=s.length>>1; return s.length? (s.length%2? s[m] : (s[m-1]+s[m])/2) : null; };
const mean = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : null;
let industries = {};
/* switch between today's data and the 1-year-back snapshot */
function setMode() {
  BACK = !!(GF.back && D.then);
  S = BACK ? D.then.stocks : D.stocks;
  industries = {};
  S.forEach(s => (industries[s.ind] = industries[s.ind] || {name:s.ind, stocks:[]}).stocks.push(s));
  Object.values(industries).forEach(buildIndustry);
}
function buildIndustry(g) {
  const pes = g.stocks.filter(s=>s.pe!=null);
  const w = pes.filter(s=>s.mcap);
  g.n = g.stocks.length; g.nPE = pes.length;
  g.mcap = g.stocks.reduce((t,s)=>t+(s.mcap||0),0);
  g.pe = {median: median(pes.map(s=>s.pe)), mean: mean(pes.map(s=>s.pe)),
          weighted: w.length ? w.reduce((t,s)=>t+s.mcap,0) / w.reduce((t,s)=>t+s.mcap/s.pe,0) : null};
  g.medPB = median(g.stocks.map(s=>s.pb));
  g.medROE = median(g.stocks.map(s=>s.roe));
  g.medDY = median(g.stocks.map(s=>s.dy));
  g.cheapest = pes.length ? pes.reduce((a,b)=>a.pe<=b.pe?a:b) : null;
}
setMode();
const indPE = name => industries[name].pe[method];
const prem = s => { const ip = indPE(s.ind); return (s.pe!=null && ip) ? (s.pe/ip-1)*100 : null; };
const pctFrom = (cmp, ref) => (cmp!=null && ref) ? (cmp/ref-1)*100 : null;

/* ---------- value maths ---------- */
const qual = s => s.fin ? s.roe : s.roce;
const peg = s => (s.pe!=null && s.cagr!=null && s.cagr>0) ? s.pe/s.cagr : null;
const grahamUp = s => (s.graham && s.cmp) ? (s.graham/s.cmp-1)*100 : null;
const grahamPass = s => s.pe!=null && s.pb!=null && s.pe*s.pb <= 22.5;
const ey = s => s.pe ? 100/s.pe : null;
const hv = (s, k, f) => s.h && s.h[k] ? s.h[k][f] : null;
const smart = s => { const a = hv(s,'fii','chg'), b = hv(s,'dii','chg'); return a==null||b==null ? null : a+b; };

const W_DEF = {pe:25, pb:15, q:15, g:15, fcf:15, cc:10, dy:5};
const BACK_KEYS = ['q', 'g', 'cc'];
const W_LABEL = {pe:'PE vs industry', pb:'PB', q:'ROCE (ROE fin.)', g:'EPS growth', fcf:'FCF yield', cc:'Cash conversion', dy:'Div yield'};
const METRICS = {
  pe:  {v:s=>prem(s), hi:false},
  pb:  {v:s=>s.pb,    hi:false},
  q:   {v:qual,       hi:true},
  g:   {v:s=>s.cagr,  hi:true},
  fcf: {v:s=>s.fcfy,  hi:true},
  cc:  {v:s=>s.cc,    hi:true},
  dy:  {v:s=>s.dy,    hi:true},
};
const ok = v => v!=null && isFinite(v);
function pctl(sorted, v, hi) {
  if (sorted.length < 2) return 0.5;
  let lo = 0, eq = 0;
  for (const x of sorted) { if (x < v) lo++; else if (x === v) eq++; }
  const p = (lo + Math.max(eq-1,0)/2) / (sorted.length-1);
  return hi ? p : 1-p;
}
function computeScores() {
  const W = lsJson('vw', W_DEF);
  const uni = {}, byInd = {};
  for (const k in METRICS) uni[k] = S.map(METRICS[k].v).filter(ok).sort((a,b)=>a-b);
  Object.values(industries).forEach(g => {
    byInd[g.name] = {};
    for (const k in METRICS) byInd[g.name][k] = g.stocks.map(METRICS[k].v).filter(ok).sort((a,b)=>a-b);
  });
  S.forEach(s => {
    s.score = null; s.parts = {};
    if (!BACK && s.pe == null) return;
    let tot = 0, wt = 0, avail = 0;
    for (const k in METRICS) {
      const w = +W[k] || 0; if (!w) continue;
      if (BACK && !BACK_KEYS.includes(k)) continue;        // no prices a year back: quality metrics only
      if (s.fin && (k==='fcf' || k==='cc')) continue;      // not applicable to financials
      avail += w;
      const v = METRICS[k].v(s);
      if (!ok(v)) continue;
      const peers = byInd[s.ind][k].length >= 4 ? byInd[s.ind][k] : uni[k];
      const p = pctl(peers, v, METRICS[k].hi);
      s.parts[k] = p; tot += p*w; wt += w;
    }
    if (avail && wt >= 0.5*avail) s.score = tot/wt*100;
  });
}
function trapReasons(s) {
  const r = [];
  if (s.cagr == null) r.push(s.ttm!=null && s.ttm<=0 ? 'loss now' : 'no growth data');
  else if (s.cagr <= 0) r.push('EPS shrinking');
  if (s.loss5) r.push(s.loss5 + ' loss yr');
  if (s.lo5pct != null && s.lo5pct < 10) r.push('EPS near 5Y low');
  if (!s.fin) {
    if (s.cc != null && s.cc < 0.5) r.push('weak cash conversion');
    if (s.oi != null && s.oi > 40) r.push('other-income heavy');
    if (s.icov != null && s.icov < 2) r.push('low interest cover');
  }
  if (s.shg != null && s.shg > 6) r.push('equity dilution');
  return r;
}

/* ---------- generic sortable table ---------- */
const sv = (s, k) => s.since ? s.since[k] : null;
const medSince = (rows, k) => median(rows.map(s => sv(s, k)));
const signPct = v => v==null ? '–' : `<span class="${v>=0?'up':'dn'}">${v>0?'+':''}${fmt(v)}%</span>`;
const signPP = v => v==null ? '–' : `<span class="${v>=0?'up':'dn'}">${v>0?'+':''}${fmt(v)} pp</span>`;
function perfStats(rows) {
  if (!BACK || !rows) return [];
  const r = rows.filter(s => sv(s,'patG') != null || sv(s,'patL'));
  const up = r.filter(s => sv(s,'patG') > 0 || sv(s,'patL') === 'turned profitable').length;
  return [['Median profit growth since, this list', signPct(medSince(rows,'patG'))],
          ['Median profit growth, all stocks', signPct(medSince(V(),'patG'))],
          ['Profit up since then', r.length ? `${up} of ${r.length}` : '–'],
          ['Median ROE change', signPP(medSince(rows,'roeD'))]];
}
/* in 1Y-back mode, price-based columns are dropped and the "since then" columns take the price column's place */
const PRICE_KEYS = new Set(['cmp','pe','cpe','prem','indpe','pb','peg','gup','dy','ey','fcfy','from52','cheap','nPE','rEY']);
const TABLE_ROWS = 15;                                    // visible rows per table, the rest scrolls
function makeTable(id, cols, rows, opt={}) {
  if (BACK) {
    const add = opt.sinceCols || [C.patG, C.revG, C.roeD, C.opmD];
    cols = cols.flatMap(c => c.k === 'cmp' ? add : (PRICE_KEYS.has(c.k) ? [] : [c]));
    id += '_back';
    if (!cols.find(c => c.k === opt.sortKey)) {
      const pg = cols.find(c => c.k === 'patG');
      opt = Object.assign({}, opt, {sortKey: pg ? 'patG' : cols[0].k, sortDir: pg ? -1 : 1});
    }
  }
  const box = document.createElement('div');
  if (opt.search) {
    const bar = document.createElement('div'); bar.className='bar';
    bar.innerHTML = '<input type="search" placeholder="Filter by symbol / name / industry…" style="width:280px">';
    const inp = $('input', bar); inp.value = searchState[id] || '';
    inp.addEventListener('input', () => { searchState[id] = inp.value; draw(); });
    box.appendChild(bar);
  }
  const card = document.createElement('div'); card.className = 'card';      // table + footer (count, help text)
  const wrap = document.createElement('div'); wrap.className='wrap';
  const tbl = document.createElement('table'); wrap.appendChild(tbl); card.appendChild(wrap);
  const foot = document.createElement('div'); foot.className = 'tfoot';
  foot.innerHTML = '<span class="tcount"></span>'; card.appendChild(foot); box.appendChild(card);
  const st = sortState[id] = sortState[id] || {k: opt.sortKey, dir: opt.sortDir || 1};
  function draw() {
    const q = (searchState[id]||'').toLowerCase();
    let data = rows.filter(r => !q || (r.sym+' '+r.name+' '+(r.ind||'')).toLowerCase().includes(q));
    const col = cols.find(c=>c.k===st.k);
    if (col) data = data.slice().sort((a,b) => {
      const x = col.v(a), y = col.v(b);
      if (x==null && y==null) return 0; if (x==null) return 1; if (y==null) return -1;
      return (typeof x==='string' ? x.localeCompare(y) : x-y) * st.dir;
    });
    tbl.innerHTML = '<thead><tr>' + (opt.noIndex ? '' : '<th class="l">#</th>') + cols.map(c =>
      `<th class="${c.l?'l':''} ${c.k===st.k?'sorted':''}" data-k="${c.k}" title="${esc(c.tip||'')}">${c.label}${c.k===st.k?(st.dir>0?' ▲':' ▼'):''}</th>`).join('') + '</tr></thead>';
    const tb = document.createElement('tbody');
    data.forEach((r,i) => {
      const tr = document.createElement('tr');
      if (opt.onRow) { tr.className='click'; tr.addEventListener('click', () => opt.onRow(r)); }
      tr.innerHTML = (opt.noIndex ? '' : `<td class="l idx">${i+1}</td>`) + cols.map(c => `<td class="${c.l?'l':''}">${c.f(r)}</td>`).join('');
      tb.appendChild(tr);
    });
    if (!data.length) tb.innerHTML = `<tr><td class="l na" colspan="${cols.length+1}">No stocks match these filters. Loosen a filter or press Reset.</td></tr>`;
    tbl.appendChild(tb);
    $('.tcount', foot).textContent = data.length + (data.length === 1 ? ' stock' : ' stocks') + (data.length > TABLE_ROWS ? ' · scroll for more' : '');
    requestAnimationFrame(() => {                        // window = header + 15 rows, rest scrolls inside the card
      const r = tb.rows[0];
      if (r && r.offsetHeight) wrap.style.maxHeight = (tbl.tHead.offsetHeight + r.offsetHeight * TABLE_ROWS + 1) + 'px';
    });
    tbl.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
      const k = th.dataset.k; st.dir = (st.k===k) ? -st.dir : 1; st.k = k; draw();
    }));
  }
  draw();
  return box;
}

/* ---------- cells ---------- */
function sparkLine(pts, color='var(--accent)') {
  if (!pts || pts.length < 2) return NA;
  const v = pts.map(p => p[1]);
  const mn = Math.min(...v), mx = Math.max(...v), rg = (mx - mn) || 1, W = 90, H = 24;
  const xy = v.map((y, i) => [2 + i * (W - 4) / (v.length - 1), H - 3 - (y - mn) / rg * (H - 6)]);
  const last = xy[xy.length - 1];
  return `<svg width="${W}" height="${H}" style="vertical-align:middle"><title>${esc(pts.map(p => p[0] + ': ' + p[1]).join('\n'))}</title>
    <polyline fill="none" stroke="${color}" stroke-width="1.5" points="${xy.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ')}"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.6" fill="var(--bad)"/></svg>`;
}
const epsSpark = s => s.ttm == null ? NA : sparkLine(s.eps.concat([['Now (TTM)', s.ttm]]));
const pctPill = (v, good, d=1, suf='%') => v==null ? NA : `<span class="pill ${good(v)?'disc':'prem'}">${v>0?'+':''}${fmt(v,d)}${suf}</span>`;
const ppPill = (v, upGood=true) => v==null ? NA : `<span class="pill ${Math.abs(v)<0.05?'':((v>0)===upGood?'disc':'prem')}">${v>0?'+':''}${fmt(v,2)} pp</span>`;
const psuTag = s => s.psu === 'psu' ? '<span class="tag" title="Government-owned">PSU</span>' : s.psu === 'semi' ? '<span class="tag semi" title="Large government / PSU stake, not controlling">semi</span>' : '';
const symCell = s => (s.url ? `<a class="sym" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.sym)}</a>` : `<b>${esc(s.sym)}</b>`) + psuTag(s);
const indLink = s => `<a class="ind" href="#/industry/${encodeURIComponent(s.ind)}">${esc(s.ind)}</a>`;
const premCell = s => { const p = prem(s); if (p==null) return '<span class="na">N/A</span>';
  return `<span class="pill ${p<=0?'disc':'prem'}">${p<=0?'':'+'}${fmt(p)}% ${p<=0?'disc.':'prem.'}</span>`; };
const scoreCell = s => {
  if (s.score == null) return NA;
  const tip = Object.entries(s.parts).map(([k,p]) => W_LABEL[k] + ': ' + Math.round(p*100)).join('\n');
  return `<span class="meter" title="${esc(tip)}"><span>${fmt(s.score,0)}</span><i><b style="width:${Math.max(3, s.score).toFixed(0)}%"></b></i></span>`;
};
/* ---------- trend score (improving fundamentals) ---------- */
const TR_KEYS = ['pat','rev','opm','pro','roe','roce','cc'];
const TR_LABEL = {pat:'Profit (TTM YoY)', rev:'Revenue (TTM YoY)', opm:'Operating margin', pro:'Promoter holding', roe:'ROE', roce:'ROCE (proxy)', cc:'Cash conversion'};
const trD = (s, k) => s.tr && s.tr.d ? s.tr.d[k] : null;
const sgn = (v, d=1, suf='') => v==null ? '' : (v>0?'+':'') + fmt(v, d) + suf;
const TR_DETAIL = {                       // short evidence shown next to each ✓ / ✗
  pat: s => trD(s,'pat') ? trD(s,'pat') + (trD(s,'patG')!=null ? ', ' + sgn(trD(s,'patG'),1,'%') : '') : '',
  rev: s => trD(s,'rev') ? trD(s,'rev') + (trD(s,'revG')!=null ? ', ' + sgn(trD(s,'revG'),1,'%') : '') : '',
  opm: s => [trD(s,'opm'), sgn(trD(s,'opmD'), 1, ' pp')].filter(Boolean).join(', '),
  pro: s => sgn(trD(s,'proD'), 2, ' pp'),
  roe: s => sgn(trD(s,'roeD'), 1, ' pp'),
  roce: s => sgn(trD(s,'roceD'), 1, ' pp'),
  cc: s => trD(s,'ccNow')!=null ? fmt(trD(s,'ccOld'),2) + 'x → ' + fmt(trD(s,'ccNow'),2) + 'x' : '',
};
const trendTip = s => TR_KEYS.map(k => {
  const v = s.tr.c[k]; return (v==null ? '–' : v ? '✓' : '✗') + ' ' + TR_LABEL[k] + (v==null ? ': n/a' : ' ' + TR_DETAIL[k](s));
}).join('\n');
const trendCell = s => s.tr == null ? NA :
  `<span class="meter" title="${esc(trendTip(s))}"><span>${s.tr.pass}/${s.tr.of}</span><i><b style="width:${Math.max(3, s.tr.pct)}%"></b></i></span>`;
const trCheck = k => ({k:'tc_'+k, label:TR_LABEL[k], v:s=>s.tr && s.tr.c[k]!=null ? s.tr.c[k] : null,
  f:s => { const v = s.tr ? s.tr.c[k] : null; if (v==null) return NA;
           return `<span class="pill ${v?'disc':'prem'}">${v?'✓':'✗'} ${esc(TR_DETAIL[k](s))}</span>`; }});
const trendOk = (s, min) => min === '' || min == null || (s.tr != null && s.tr.pct >= +min);

const C = {
  sym: {k:'sym', label:'Symbol', l:1, v:s=>s.sym, f:symCell},
  name: {k:'name', label:'Company', l:1, v:s=>s.name, f:s=>`<span class="co" title="${esc(s.name)}">${esc(s.name)}</span>`},
  ind: {k:'ind', label:'Industry', l:1, v:s=>s.ind, f:indLink},
  cmp: {k:'cmp', label:'CMP ₹', v:s=>s.cmp, f:s=>fmt(s.cmp,2)},
  patG: {k:'patG', label:'Profit growth since', tip:'TTM profit now vs TTM profit a year earlier',
         v:s=>sv(s,'patG') ?? (sv(s,'patL')==='turned profitable' ? 1e9 : null),
         f:s=>{const l=sv(s,'patL'); return l ? `<span class="pill ${l==='turned profitable'?'disc':'prem'}">${l}</span>` : pctPill(sv(s,'patG'), v=>v>=0);}},
  revG: {k:'revG', label:'Revenue growth since', tip:'TTM revenue now vs a year earlier', v:s=>sv(s,'revG'), f:s=>pctPill(sv(s,'revG'), v=>v>=0)},
  roeD: {k:'roeD', label:'ROE Δ', tip:'ROE of the latest financial year minus ROE of the year known then', v:s=>sv(s,'roeD'), f:s=>ppPill(sv(s,'roeD'), true)},
  roceD: {k:'roceD', label:'ROCE Δ', tip:'approximate', v:s=>sv(s,'roceD'), f:s=>ppPill(sv(s,'roceD'), true)},
  opmD: {k:'opmD', label:'OPM Δ', tip:'operating margin, latest FY minus FY known then', v:s=>sv(s,'opmD'), f:s=>ppPill(sv(s,'opmD'), true)},
  proD: {k:'proD', label:'Promoter Δ since', tip:'promoter holding now minus then', v:s=>sv(s,'proD'), f:s=>ppPill(sv(s,'proD'), true)},
  pe: {k:'pe', label:'PE', v:s=>s.pe, f:s=>s.pe==null?'<span class="na">N/A</span>':fmt(s.pe)},
  cpe: {k:'cpe', label:'Core PE', tip:'PE after removing other income from profit', v:s=>s.cpe, f:s=>s.cpe==null?NA:(s.pe!=null && s.cpe>s.pe*1.25?`<span class="pill warn">${fmt(s.cpe)}</span>`:fmt(s.cpe))},
  indpe: {k:'indpe', label:'Industry PE', v:s=>indPE(s.ind), f:s=>fmt(indPE(s.ind))},
  prem: {k:'prem', label:'vs Industry PE', v:prem, f:premCell},
  mcap: {k:'mcap', label:'Mcap ₹ Cr', v:s=>s.mcap, f:s=>fmt(s.mcap,0)},
  roe: {k:'roe', label:'ROE %', v:s=>s.roe, f:s=>fmt(s.roe)},
  roce: {k:'roce', label:'ROCE %', v:s=>s.roce, f:s=>fmt(s.roce)},
  q: {k:'q', label:'ROCE %', tip:'ROE used for financials', v:qual, f:s=>fmt(qual(s))+(s.fin?' <span class="na">ROE</span>':'')},
  from52: {k:'from52', label:'vs 52W high', v:s=>pctFrom(s.cmp,s.hi52), f:s=>{const p=pctFrom(s.cmp,s.hi52); return p==null?NA:fmt(p)+'%';}},
  score: {k:'score', label:'Value score', v:s=>s.score, f:scoreCell},
  pb: {k:'pb', label:'PB', v:s=>s.pb, f:s=>s.pb==null?'<span class="na">N/A</span>':fmt(s.pb,2)+(s.pbSrc==='derived'?'<span class="na">*</span>':'')},
  cagr: {k:'cagr', label:'EPS CAGR', v:s=>s.cagr, f:s=>{
    if (s.cagr==null) return NA;
    const y = (s.cagrSrc==='PAT' ? ' <span class="na" title="share base changed (IPO / restructure), profit growth used">(PAT)</span>' : '')
      + (s.cagrY && s.cagrY < 4.5 ? ` <span class="na">(${fmt(s.cagrY,0)}y)</span>` : '');
    return `<span class="${s.cagr>0?'':'na'}">${s.cagr>0?'+':''}${fmt(s.cagr)}%</span>${y}`; }},
  peg: {k:'peg', label:'PEG', v:peg, f:s=>{const p=peg(s); return p==null?NA:`<span class="pill ${p<=1?'disc':p<=2?'':'prem'}">${fmt(p,2)}</span>`;}},
  fcfy: {k:'fcfy', label:'FCF yield', tip:'5Y avg (CFO + CFI) / Mcap', v:s=>s.fcfy, f:s=>pctPill(s.fcfy, v=>v>0)},
  cc: {k:'cc', label:'Cash conv.', tip:'5Y operating cash flow / 5Y profit', v:s=>s.cc, f:s=>s.cc==null?NA:`<span class="pill ${s.cc>=0.8?'disc':s.cc>=0.5?'':'prem'}">${fmt(s.cc,2)}x</span>`},
  opmT: {k:'opmT', label:'OPM vs 5Y', tip:'current operating margin minus 5Y average, pp', v:s=>s.opmT, f:s=>s.opmT==null?NA:`<span class="${s.opmT>=0?'':'na'}">${s.opmT>0?'+':''}${fmt(s.opmT)} pp</span>`},
  gup: {k:'gup', label:'Graham upside', v:grahamUp, f:s=>pctPill(grahamUp(s), v=>v>0, 0)},
  dy: {k:'dy', label:'Div yield %', v:s=>s.dy, f:s=>s.dy==null?NA:(s.dy>=3?`<b class="up">${fmt(s.dy,2)}</b>`:fmt(s.dy,2))},
  ey: {k:'ey', label:'Earnings yld %', v:ey, f:s=>fmt(ey(s),2)},
  proChg: {k:'proChg', label:'Promoter Δ', tip:'promoter holding change over all quarters available', v:s=>hv(s,'pro','chg'), f:s=>ppPill(hv(s,'pro','chg'), true)},
  flags: {k:'flags', label:'Value-trap flags', l:1, v:s=>trapReasons(s).length, f:s=>{
    const r = trapReasons(s); return r.length ? `<span class="pill warn">${esc(r.join(' · '))}</span>` : '<span class="pill disc">clean</span>'; }},
  spark: {k:'spark', label:'EPS trend', v:s=>null, f:epsSpark},
  trend: {k:'trend', label:'Trend', tip:'improving-fundamentals checks passed (see Improving tab)', v:s=>s.tr?s.tr.pct+s.tr.of/100:null, f:trendCell},
};

/* ---------- views ---------- */
const view = $('#view');
function setNav(t){ document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.t===t)); }
function pePicker() {
  const d = document.createElement('div'); d.className='bar';
  if (BACK) return d;
  d.innerHTML = `<label>Industry PE =</label><select>
    <option value="median">Median PE (robust to outliers)</option>
    <option value="mean">Simple average PE</option>
    <option value="weighted">Market-cap weighted PE</option></select>`;
  const sel = $('select', d); sel.value = method;
  sel.addEventListener('change', () => { method = sel.value; lsSet('pemethod', method); route(); });
  return d;
}
const pageTitle = t => view.insertAdjacentHTML('beforeend', `<h2 class="page">${t}</h2>`);
const stats = (arr, rows) => view.insertAdjacentHTML('beforeend', '<div class="stats">' + arr.concat(perfStats(rows)).map(([l,v]) => `<div class="stat"><span>${l}</span><b>${v}</b></div>`).join('') + '</div>');
const hint = html => {                                   // help text lives in the table footer, not under the page
  const feet = view.querySelectorAll('.tfoot'), ft = feet[feet.length - 1];
  const h = `<span class="thint">${html} <a href="#/method">Methodology</a></span>`;
  if (ft) ft.insertAdjacentHTML('beforeend', h); else view.insertAdjacentHTML('beforeend', `<div class="hint">${h}</div>`);
};

const cheapest = g => g.stocks.filter(s=>s.pe!=null && vis(s)).reduce((a,b)=>!a||b.pe<a.pe?b:a, null);
function homeView() {
  setNav('home'); view.innerHTML = '';
  pageTitle('Industries');
  view.appendChild(pePicker());
  const cols = [
    {k:'name', label:'Industry', l:1, v:g=>g.name, f:g=>`<b>${esc(g.name)}</b>`},
    {k:'pe', label:'Industry PE', v:g=>g.pe[method], f:g=>`<b>${fmt(g.pe[method])}</b>`},
    {k:'pb', label:'Median PB', v:g=>g.medPB, f:g=>fmt(g.medPB,2)},
    {k:'roe', label:'Median ROE %', v:g=>g.medROE, f:g=>fmt(g.medROE)},
    {k:'dy', label:'Median div yield %', v:g=>g.medDY, f:g=>fmt(g.medDY,2)},
    ...(BACK ? [{k:'patG', label:'Median profit growth since', v:g=>medSince(g.stocks.filter(vis),'patG'), f:g=>pctPill(medSince(g.stocks.filter(vis),'patG'), v=>v>=0)},
                {k:'roeD', label:'Median ROE Δ', v:g=>medSince(g.stocks.filter(vis),'roeD'), f:g=>ppPill(medSince(g.stocks.filter(vis),'roeD'), true)}] : []),
    {k:'n', label:'Stocks', v:g=>g.n, f:g=>g.n},
    {k:'nPE', label:'With valid PE', v:g=>g.nPE, f:g=>g.nPE},
    {k:'mcap', label:'Total Mcap ₹ Cr', v:g=>g.mcap, f:g=>fmt(g.mcap,0)},
    {k:'cheap', label:'Lowest-PE stock', l:1, v:g=>{const c=cheapest(g); return c?c.pe:null;}, f:g=>{const c=cheapest(g); return c?`${esc(c.sym)}${psuTag(c)} <span class="na">PE ${fmt(c.pe)}</span>`:NA;}},
  ];
  const rows = Object.values(industries).map(g => Object.assign(g, {sym:g.name, ind:g.name}));
  view.appendChild(makeTable('home', cols, rows, {sortKey:'pe', sortDir:1, search:true,
    onRow: g => location.hash = '#/industry/' + encodeURIComponent(g.name), noIndex:true}));
  hint('Click an industry to see its stocks. Stocks with negative / missing PE are excluded from industry PE. The CSV has only broad sector buckets, so industry PE is a rough benchmark.');
}

function industryView(name) {
  const g = industries[name]; setNav('home'); view.innerHTML = '';
  if (!g) { view.innerHTML = '<p>Industry not found.</p>'; return; }
  view.insertAdjacentHTML('beforeend', `<a class="back-link" href="#/">&#8592; All industries</a><h2 class="page">${esc(g.name)}</h2>`);
  if (BACK) stats([['Stocks', g.n], ['Median ROE then', fmt(g.medROE) + '%']], g.stocks.filter(vis));
  else stats([['Industry PE (selected)', fmt(g.pe[method])], ['Median PE', fmt(g.pe.median)], ['Average PE', fmt(g.pe.mean)],
         ['Cap-weighted PE', fmt(g.pe.weighted)], ['Median PB', fmt(g.medPB,2)], ['Stocks', g.n]], g.stocks.filter(vis));
  view.appendChild(pePicker());
  view.appendChild(makeTable('ind', [C.sym, C.name, C.cmp, C.pe, C.cpe, C.prem, C.pb, C.score, C.q, C.cagr, C.fcfy, C.dy, C.proChg, C.mcap, C.from52],
    g.stocks.filter(vis), {sortKey:'pe', sortDir:1, search:true}));
  hint('Sorted by PE, lowest first. Green = PE below industry PE (discount), red = above (premium).');
}

/* ---------- smart filter box: screen presets + main fields + collapsible "More filters" / extra panel ---------- */
const PANEL_OPEN = {};                                    // which panels are expanded, remembered while the page is open
const fNum = (st, k, label, o={}) => `<label>${label} <input type="number" data-f="${k}"${o.min!=null?` min="${o.min}"`:''}${o.max!=null?` max="${o.max}"`:''} step="${o.step||1}" value="${st[k]}"${o.w?` style="width:${o.w}px"`:''}></label>`;
const fChk = (st, k, label) => `<label><input type="checkbox" data-f="${k}" ${st[k]?'checked':''}> ${label}</label>`;
const fSel = (st, k, label, opts) => `<label>${label} <select data-f="${k}">${opts.map(([v,t]) => `<option value="${v}" ${st[k]===v?'selected':''}>${t}</option>`).join('')}</select></label>`;
function filterBox(o, rerender) {
  const {key, def} = o;
  const st = lsJson(key, def);
  const presets = (o.presets || []).filter(p => !BACK || !p.price);     // price-based screens make no sense 1Y back
  const isOn = p => Object.entries(Object.assign({}, def, p.st)).every(([k, v]) => String(st[k]) === String(v));
  const active = presets.find(isOn);
  const moreHtml = o.more ? o.more(st) : '';
  const nMore = (o.moreKeys || []).filter(k => String(st[k]) !== String(def[k])).length;
  const f = document.createElement('div'); f.className = 'fbox';
  f.innerHTML =
      (presets.length ? `<div class="presets"><span class="t">Screens</span>` + presets.map((p, i) =>
        `<button type="button" class="preset${p === active ? ' on' : ''}" data-p="${i}" title="${esc(p.tip || '')}">${esc(p.name)}</button>`).join('')
        + (active ? '' : '<span class="preset-custom">Custom</span>') + '</div>' : '')
    + `<div class="frow"><span class="t">Filters</span>${o.main(st)}<span class="fbtns">`
    + (moreHtml ? `<button type="button" class="btn ghost" data-tog="more">More filters${nMore ? ` <b class="cnt">${nMore}</b>` : ''} <span class="car">▾</span></button>` : '')
    + (o.extra ? `<button type="button" class="btn ghost" data-tog="extra">${o.extra.label} <span class="car">▾</span></button>` : '')
    + `<button type="button" class="btn ghost" data-reset title="Back to defaults">Reset</button></span></div>`
    + (moreHtml ? `<div class="fpanel" data-panel="more">${moreHtml}</div>` : '')
    + (o.extra ? `<div class="fpanel" data-panel="extra"></div>` : '');
  if (o.extra) o.extra.build($('[data-panel="extra"]', f));
  f.querySelectorAll('[data-panel]').forEach(p => p.classList.toggle('open', !!PANEL_OPEN[key + p.dataset.panel]));
  f.querySelectorAll('[data-tog]').forEach(b => {
    const name = b.dataset.tog; b.classList.toggle('open', !!PANEL_OPEN[key + name]);
    b.addEventListener('click', () => {
      PANEL_OPEN[key + name] = !PANEL_OPEN[key + name];
      $(`[data-panel="${name}"]`, f).classList.toggle('open', PANEL_OPEN[key + name]); b.classList.toggle('open', PANEL_OPEN[key + name]);
    });
  });
  f.querySelectorAll('[data-f]').forEach(el => el.addEventListener('change', () => {
    st[el.dataset.f] = el.type === 'checkbox' ? el.checked : (el.tagName === 'SELECT' ? el.value : (el.value === '' ? '' : +el.value));
    lsSet(key, JSON.stringify(st)); rerender();
  }));
  f.querySelectorAll('[data-p]').forEach(b => b.addEventListener('click', () => {
    lsSet(key, JSON.stringify(Object.assign({}, def, presets[+b.dataset.p].st))); rerender();
  }));
  $('[data-reset]', f).addEventListener('click', () => { lsSet(key, '{}'); rerender(); });
  view.appendChild(f);
  return st;
}

/* ---------- VALUE SCREEN ---------- */
const VF_DEF = {maxPE:'', maxPB:'', minQ:12, minScore:0, minFcf:'', minMcap:0, trap:true, graham:false, minTrend:'',
                maxPrem:'', maxPeg:'', minDy:'', minCc:''};
const VF_PRESETS = [
  {name:'Quality, fair price', tip:'ROCE (ROE for financials) ≥ 15%, Value score ≥ 60, no value-trap flags', st:{minQ:15, minScore:60}},
  {name:'Deep value', price:1, tip:'PE ≤ 15, PB ≤ 2, ROCE ≥ 10%, no value traps', st:{maxPE:15, maxPB:2, minQ:10}},
  {name:'Cheap vs peers', price:1, tip:'PE at least 30% below its industry PE, ROCE ≥ 12%, no value traps', st:{maxPrem:-30}},
  {name:'GARP', price:1, tip:'Growth at a reasonable price: PEG ≤ 1, ROCE ≥ 15%, no value traps', st:{maxPeg:1, minQ:15}},
  {name:'Cash machines', price:1, tip:'FCF yield ≥ 4%, cash conversion ≥ 0.9x, ROCE ≥ 15%, no value traps', st:{minFcf:4, minCc:0.9, minQ:15}},
  {name:'Dividend', price:1, tip:'Dividend yield ≥ 3%, cash conversion ≥ 0.8x, no value traps', st:{minDy:3, minCc:0.8, minQ:''}},
  {name:'Graham defensive', price:1, tip:'PE × PB ≤ 22.5, pays a dividend, no value traps', st:{graham:true, minDy:0.1, minQ:''}},
  {name:'Cheap & improving', price:1, tip:'PE below industry PE and Trend ≥ 70%, no value traps', st:{maxPrem:0, minTrend:70, minQ:''}},
  {name:'Improving quality', tip:'ROCE ≥ 15% and Trend ≥ 70%, no value traps', st:{minQ:15, minTrend:70}},
];
function valueView() {
  setNav('value'); view.innerHTML = '';
  pageTitle(BACK ? 'Quality screen, 1 year back' : 'Value screen');
  const vf = filterBox({
    key:'vf', def:VF_DEF, presets:VF_PRESETS,
    main: st => (BACK ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fNum(st,'minQ','Min ROCE / ROE %')
      + fNum(st,'minScore','Min score',{min:0,max:100,step:5}) + fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10})
      + fChk(st,'trap','Hide value traps'),
    more: st => (BACK ? '' : fNum(st,'maxPB','Max PB',{min:0,step:0.5}) + fNum(st,'maxPrem','Max vs industry PE %',{step:5})
      + fNum(st,'maxPeg','Max PEG',{min:0,step:0.25}) + fNum(st,'minFcf','Min FCF yield %',{step:0.5})
      + fNum(st,'minDy','Min div yield %',{min:0,step:0.5}))
      + fNum(st,'minCc','Min cash conv. x',{step:0.1}) + fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90})
      + (BACK ? '' : fChk(st,'graham','Graham pass (PE × PB ≤ 22.5)')),
    moreKeys: BACK ? ['minCc','minMcap'] : ['maxPB','maxPrem','maxPeg','minFcf','minDy','minCc','minMcap','graham'],
    extra: {label:'Scoring', build: el => {                 // industry PE method + score weights, out of the way
      const W = lsJson('vw', W_DEF);
      el.appendChild(pePicker());
      el.insertAdjacentHTML('beforeend', '<div class="frow"><span class="t">Weights</span>' + Object.keys(W_DEF).filter(k => !BACK || BACK_KEYS.includes(k)).map(k =>
        `<label>${W_LABEL[k]} <input type="number" data-w="${k}" min="0" max="100" step="5" value="${W[k]}" style="width:60px"></label>`).join('')
        + '<button type="button" class="btn ghost" data-wr>Reset weights</button></div>');
      el.querySelectorAll('[data-w]').forEach(i => i.addEventListener('change', () => {
        W[i.dataset.w] = Math.max(0, +i.value || 0); lsSet('vw', JSON.stringify(W)); route();
      }));
      $('[data-wr]', el).addEventListener('click', () => { lsSet('vw', '{}'); route(); });
    }},
  }, valueView);

  const rows = V().filter(s => s.score != null
    && (BACK || vf.maxPE === '' || s.pe <= vf.maxPE)
    && (BACK || vf.maxPB === '' || (s.pb != null && s.pb <= vf.maxPB))
    && (BACK || vf.maxPrem === '' || (prem(s) != null && prem(s) <= vf.maxPrem))
    && (BACK || vf.maxPeg === '' || (peg(s) != null && peg(s) <= vf.maxPeg))
    && (BACK || vf.minDy === '' || (s.dy != null && s.dy >= vf.minDy))
    && (vf.minQ === '' || (qual(s) != null && qual(s) >= vf.minQ))
    && (BACK || vf.minFcf === '' || s.fin || (s.fcfy != null && s.fcfy >= vf.minFcf))
    && (vf.minCc === '' || s.fin || (s.cc != null && s.cc >= vf.minCc))
    && s.score >= (+vf.minScore || 0)
    && (s.mcap || 0) >= (+vf.minMcap || 0)
    && (!vf.trap || !trapReasons(s).length)
    && (BACK || !vf.graham || grahamPass(s))
    && trendOk(s, vf.minTrend));                                       // Trend is a filter here, never part of the score
  if (BACK) stats([['Stocks passing filters', rows.length], ['Stocks with a quality score', V().filter(s=>s.score!=null).length]], rows);
  else stats([['Stocks passing filters', rows.length], ['Stocks with a value score', V().filter(s=>s.score!=null).length],
         ['Median PE of list', fmt(median(rows.map(s=>s.pe)))], ['Median PB of list', fmt(median(rows.map(s=>s.pb)),2)],
         ['Graham pass in list', rows.filter(grahamPass).length]], rows);
  view.appendChild(makeTable('value',
    [C.score, C.trend, C.sym, C.name, C.ind, C.cmp, C.pe, C.cpe, C.prem, C.pb, C.q, C.cagr, C.peg, C.fcfy, C.cc, C.opmT, C.gup, C.dy, C.proChg, C.mcap, C.flags, C.spark],
    rows, {sortKey:'score', sortDir:-1, search:true}));
  if (BACK) hint('1 year back the score uses only the quality metrics as they stood then (ROCE / ROE, EPS growth, cash conversion), ranked within each industry. Green / red "since" columns show what the business did after that. Mcap filter uses today\'s market cap.');
  else hint('Value score = weighted percentile rank within the stock\'s own industry (hover a score for each part). Screens are one-click presets; any change turns them into Custom. FCF yield, cash conversion and core PE are not used for financials. Orange core PE = over 20% of profit is other income.');
}

/* ---------- MAGIC FORMULA ---------- */
const MF_DEF = {exFin:true, minMcap:1000, top:50, trap:false, minTrend:''};
const MF_PRESETS = [
  {name:'Classic', tip:'Greenblatt as published: ex-financials, Mcap ≥ ₹1,000 Cr, top 30, nothing else', st:{top:30}},
  {name:'Magic + safety', tip:'Classic, then drop stocks with value-trap flags', st:{top:30, trap:true}},
  {name:'Magic + improving', tip:'Classic + no value traps + Trend ≥ 70%', st:{top:30, trap:true, minTrend:70}},
];
function magicView() {
  setNav('magic'); view.innerHTML = '';
  pageTitle('Magic Formula');
  const mf = filterBox({
    key:'mf', def:MF_DEF, presets:MF_PRESETS,
    main: st => fChk(st,'exFin','Exclude financials') + fNum(st,'top','Show top',{min:5,max:500,step:5}) + fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90}),
    more: st => fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10}) + fChk(st,'trap','Hide value traps'),
    moreKeys: ['minTrend','trap'],
  }, magicView);
  const u = V().filter(s => (BACK || s.pe != null) && s.roce != null && s.roce > 0 && (s.mcap || 0) >= (+mf.minMcap || 0) && !(mf.exFin && s.fin));
  [...u].sort((a,b) => b.roce - a.roce).forEach((s,i) => s.rQ = i+1);
  if (BACK) {                                            // no PE a year back: rank on the ROCE half only
    u.forEach(s => { s.rEY = null; s.mf = s.rQ; });
    u.sort((a,b) => a.mf - b.mf).forEach((s,i) => s.mfRank = i+1);
  } else {
    [...u].sort((a,b) => a.pe - b.pe).forEach((s,i) => s.rEY = i+1);
    u.forEach(s => s.mf = s.rEY + s.rQ);
    u.sort((a,b) => a.mf - b.mf || a.pe - b.pe).forEach((s,i) => s.mfRank = i+1);
  }
  // ranks above stay pure Greenblatt; Trend / trap only filter the ranked list afterwards
  const rows = u.filter(s => (!mf.trap || !trapReasons(s).length) && trendOk(s, mf.minTrend)).slice(0, +mf.top || 50);
  stats([['Stocks ranked', u.length], ...(BACK ? [] : [[`Median PE of top ${rows.length}`, fmt(median(rows.map(s=>s.pe)))]]),
         [`Median ROCE of top ${rows.length}`, fmt(median(rows.map(s=>s.roce))) + '%']], rows);
  view.appendChild(makeTable('magic', [
    {k:'mfRank', label:'Rank', l:1, v:s=>s.mfRank, f:s=>`<span class="rank ${s.mfRank<=10?'top':''}">${s.mfRank}</span>`},
    C.sym, C.name, C.ind, C.cmp, C.pe, C.ey, {k:'rEY', label:'EY rank', v:s=>s.rEY, f:s=>`<span class="na">${s.rEY}</span>`},
    C.roce, {k:'rQ', label:'ROCE rank', v:s=>s.rQ, f:s=>`<span class="na">${s.rQ}</span>`},
    C.pb, C.dy, C.cagr, C.fcfy, C.score, C.trend, C.mcap, C.flags, C.spark], rows, {sortKey:'mfRank', sortDir:1, search:true, noIndex:true}));
  if (BACK) hint('1 year back the Magic Formula is ranked on its quality half only (ROCE as it stood then, approximate), since PE needs a price. "Since" columns show how those businesses did afterwards.');
  else hint('Greenblatt: rank by earnings yield (100 ÷ PE) + rank by ROCE, lowest total wins. Uses PE instead of EBIT / EV because the CSV has no debt data. Trend and value-trap filters only remove stocks from the ranked list, they never change the ranks.');
}

/* ---------- SHAREHOLDING TABS ---------- */
const sinceCell = (s, k) => { const h = s.h && s.h[k]; return h ? `<span class="na">${esc(h.since)} → ${esc(h.till)} (${fmt(h.yrs,2)}y)</span>` : NA; };
function holdCols(k, upGood) {
  return {
    now:  {k:k+'now', label:'Now %', v:s=>hv(s,k,'now'), f:s=>fmt(hv(s,k,'now'),2)},
    chg:  {k:k+'chg', label:'Δ full period', v:s=>hv(s,k,'chg'), f:s=>ppPill(hv(s,k,'chg'), upGood)},
    win:  {k:k+'win', label:'Period', l:1, v:s=>hv(s,k,'yrs'), f:s=>sinceCell(s,k)},
    chg1: {k:k+'chg1', label:'Δ last 1Y', v:s=>hv(s,k,'chg1'), f:s=>ppPill(hv(s,k,'chg1'), upGood)},
    ud:   {k:k+'ud', label:'Qtrs ↑ / ↓', v:s=>s.h&&s.h[k]?s.h[k].up-s.h[k].dn:null, f:s=>s.h&&s.h[k]?`<span class="${upGood?'up':'dn'}">${s.h[k].up}↑</span> <span class="${upGood?'dn':'up'}">${s.h[k].dn}↓</span>`:NA},
    sp:   {k:k+'sp', label:'Trend', v:s=>null, f:s=>s.h&&s.h[k]?sparkLine(s.h[k].s, upGood?'var(--good)':'var(--accent)'):NA},
  };
}
const jumpOf = s => Math.max(hv(s,'pro','jump')||0, hv(s,'pub','jump')||0);
const jumpCell = {k:'jump', label:'Check', l:1, tip:'largest single-quarter move', v:jumpOf,
  f:s=>{const j=jumpOf(s); return j>D.jumpPP?`<span class="pill warn">${fmt(j,1)} pp in 1 qtr: merger / OFS?</span>`:''; }};
const hasJump = s => jumpOf(s) > D.jumpPP;
const dirOk = (c, dir, m) => dir==='up' ? c >= Math.max(m, 0.01) : dir==='down' ? c <= -Math.max(m, 0.01) : Math.abs(c) >= m;

const PR_DEF = {dir:'up', minChg:1, hideJump:true, maxPE:'', minMcap:0, minNow:''};
const PR_PRESETS = [
  {name:'Insiders buying', tip:'Promoter holding up ≥ 1 pp, merger / OFS jumps hidden', st:{dir:'up', minChg:1}},
  {name:'Buying + cheap', price:1, tip:'Promoter up ≥ 0.5 pp and PE ≤ 20', st:{dir:'up', minChg:0.5, maxPE:20}},
  {name:'Insiders selling', tip:'Promoter holding down ≥ 2 pp', st:{dir:'down', minChg:2}},
];
function promoterView() {
  setNav('promoter'); view.innerHTML = '';
  pageTitle('Promoter holding');
  const f = filterBox({
    key:'pr', def:PR_DEF, presets:PR_PRESETS,
    main: st => fSel(st,'dir','Direction',[['up','Increasing'],['down','Decreasing'],['all','All']]) + fNum(st,'minChg','Min change pp',{min:0,step:0.5})
      + (BACK ? '' : fNum(st,'maxPE','Max PE',{min:0})),
    more: st => fNum(st,'minNow','Min promoter % now',{min:0,step:5}) + fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90})
      + fChk(st,'hideJump',`Hide jumps > ${D.jumpPP} pp in one quarter`),
    moreKeys: ['minNow','minMcap','hideJump'],
  }, promoterView);
  const P = holdCols('pro', true), F = holdCols('fii', true), Dd = holdCols('dii', true);
  const all = V().filter(s => s.h && s.h.pro);
  const rows = all.filter(s => dirOk(s.h.pro.chg, f.dir, +f.minChg || 0)
      && (f.minNow === '' || s.h.pro.now >= f.minNow)
      && (BACK || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && (s.mcap || 0) >= (+f.minMcap || 0)
      && (!f.hideJump || !hasJump(s)));
  stats([['Stocks shown', rows.length], ['Promoter ↑ ≥ 1 pp', all.filter(s=>s.h.pro.chg>=1).length],
         ['Promoter ↓ ≥ 1 pp', all.filter(s=>s.h.pro.chg<=-1).length], ['With holding history', all.length],
         ['Typical period', fmt(median(all.map(s=>s.h.pro.yrs)),2) + ' yrs']], rows);
  const st = sortState['promoter'];
  if (st && st.k === 'prochg') st.dir = f.dir==='down' ? 1 : -1;      // keep the biggest movers on top
  view.appendChild(makeTable('promoter', [C.sym, C.name, C.ind, C.cmp, P.now, P.chg, P.chg1, P.ud, P.win, P.sp,
      Object.assign({}, F.chg, {label:'FII Δ'}), Object.assign({}, Dd.chg, {label:'DII Δ'}), C.pe, C.pb, C.dy, C.score, C.trend, C.mcap, jumpCell],
    rows, {sortKey:'prochg', sortDir: f.dir==='down' ? 1 : -1, search:true, sinceCols:[C.proD, C.patG, C.roeD]}));
  hint(`Δ = change in percentage points between the first and last quarter available (most stocks ~${fmt(median(all.map(s=>s.h.pro.yrs)),2)} years, see Period). Qtrs ↑ / ↓ counts quarter-on-quarter moves above 0.05 pp; steady creeping buying matters more than one jump.`);
}

const PU_DEF = {dir:'down', minChg:1, smart:false, hideJump:true, maxPE:'', minMcap:0};
const PU_PRESETS = [
  {name:'Smart money absorbing', tip:'Public % falling ≥ 1 pp while FII + DII rise', st:{dir:'down', minChg:1, smart:true}},
  {name:'Retail piling in', tip:'Public % rising ≥ 2 pp (often promoters or funds selling)', st:{dir:'up', minChg:2}},
];
function publicView() {
  setNav('public'); view.innerHTML = '';
  pageTitle('Public holding');
  const f = filterBox({
    key:'pu', def:PU_DEF, presets:PU_PRESETS,
    main: st => fSel(st,'dir','Direction',[['down','Falling'],['up','Rising'],['all','All']]) + fNum(st,'minChg','Min change pp',{min:0,step:0.5})
      + fChk(st,'smart','FII + DII rising'),
    more: st => (BACK ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90})
      + fChk(st,'hideJump',`Hide jumps > ${D.jumpPP} pp in one quarter`),
    moreKeys: ['maxPE','minMcap','hideJump'],
  }, publicView);
  const U = holdCols('pub', false), P = holdCols('pro', true);
  const all = V().filter(s => s.h && s.h.pub);
  const rows = all.filter(s => dirOk(s.h.pub.chg, f.dir, +f.minChg || 0)
      && (!f.smart || (smart(s) != null && smart(s) > 0))
      && (BACK || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && (s.mcap || 0) >= (+f.minMcap || 0)
      && (!f.hideJump || !hasJump(s)));
  stats([['Stocks shown', rows.length], ['Public ↓ ≥ 1 pp', all.filter(s=>s.h.pub.chg<=-1).length],
         ['Public ↑ ≥ 1 pp', all.filter(s=>s.h.pub.chg>=1).length],
         ['Public ↓ and FII+DII ↑', all.filter(s=>s.h.pub.chg<=-1 && smart(s)>0).length]], rows);
  const st = sortState['public'];
  if (st && st.k === 'pubchg') st.dir = f.dir==='up' ? -1 : 1;
  view.appendChild(makeTable('public', [C.sym, C.name, C.ind, C.cmp, U.now, U.chg, U.chg1, U.ud, U.win, U.sp,
      {k:'smart', label:'FII + DII Δ', tip:'smart money', v:smart, f:s=>ppPill(smart(s), true)},
      Object.assign({}, P.chg, {label:'Promoter Δ'}), C.pe, C.pb, C.dy, C.score, C.trend, C.mcap, jumpCell],
    rows, {sortKey:'pubchg', sortDir: f.dir==='up' ? -1 : 1, search:true, sinceCols:[C.patG, C.revG, C.roeD]}));
  hint('Public = retail and other non-institutional holders. Falling public % (green) = promoters or institutions absorbing shares; rising (red) = shares moving to retail. Strongest signal: public falling while FII + DII rise.');
}

/* ---------- IMPROVING (trend score) ---------- */
const IM_DEF = {minTrend:70, minMcap:0, maxPE:'', exFin:false, trap:false};
const IM_PRESETS = [
  {name:'All green', tip:'Every check with data passed', st:{minTrend:100}},
  {name:'Mostly green', tip:'Trend ≥ 70%', st:{minTrend:70}},
  {name:'Improving & cheap', price:1, tip:'Trend ≥ 70%, PE ≤ 20, no value traps', st:{minTrend:70, maxPE:20, trap:true}},
  {name:'Improving, ex-fin', tip:'Trend ≥ 70%, all 7 checks apply (no financials)', st:{minTrend:70, exFin:true}},
];
function improvingView() {
  setNav('improving'); view.innerHTML = '';
  pageTitle(BACK ? 'Improving, 1 year back' : 'Improving fundamentals');
  const f = filterBox({
    key:'im', def:IM_DEF, presets:IM_PRESETS,
    main: st => fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10}) + (BACK ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fChk(st,'exFin','Exclude financials'),
    more: st => fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90}) + fChk(st,'trap','Hide value traps'),
    moreKeys: ['minMcap','trap'],
  }, improvingView);
  const all = V().filter(s => s.tr);
  const rows = all.filter(s => trendOk(s, f.minTrend)
      && (BACK || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && (s.mcap || 0) >= (+f.minMcap || 0)
      && !(f.exFin && s.fin)
      && (!f.trap || !trapReasons(s).length));
  stats([['Stocks shown', rows.length], ['With a Trend score', all.length],
         ['All checks passed', all.filter(s => s.tr.pass === s.tr.of).length],
         ...(BACK ? [] : [['Median PE of list', fmt(median(rows.map(s=>s.pe)))]]),
         ['Median Value score of list', fmt(median(rows.map(s=>s.score)),0)]], rows);
  view.appendChild(makeTable('improving', [C.trend, C.sym, C.name, C.ind, C.cmp, ...TR_KEYS.map(trCheck), C.pe, C.score, C.mcap, C.flags],
    rows, {sortKey:'trend', sortDir:-1, search:true, sinceCols:[C.patG, C.revG, C.roeD]}));
  hint(BACK ? 'Trend checks as they stood 1 year back, using only results published by then. "Since" columns show whether the improving businesses kept delivering.'
            : 'Trend = share of 7 pass / fail checks that are green (financials: 4). Each ✓ / ✗ shows its evidence; hover the Trend bar for all of them. Improving is not cheap: pair it with Value screen or Magic Formula. Cyclicals look best here right at a peak.');
}

/* ---------- METHODOLOGY ---------- */
function methodView() {
  setNav('method'); view.innerHTML = '';
  pageTitle('Methodology');
  const W = lsJson('vw', W_DEF);
  view.insertAdjacentHTML('beforeend', `<div class="doc">
  <h2>Data</h2>
  <p>Everything comes from the same valuation CSVs that power the screener (downloaded ${esc(D.asOf)}), ${S.length} stocks, consolidated figures.
  Annual history (EPS, profit, cash flow, reserves) runs roughly 2015 → 2026. Shareholding history mostly covers ${esc(D.shSpan)}.
  Stocks whose latest annual figure is older than ${D.staleDays} days are treated as having no history. Industry = the CSV's broad sector bucket (${Object.keys(industries).length} buckets).
  "Financials" = industries matching bank / finance / NBFC / insurance; they get special handling below.</p>

  <h2>Basic ratios</h2>
  <div class="wrap"><table>
  <tr><th>Metric</th><th>Formula</th><th>Notes</th></tr>
  <tr><td>PE</td><td><code>PE</code> column from the CSV (TTM)</td><td>Zero / negative PE shown as N/A</td></tr>
  <tr><td>TTM EPS</td><td><code>CMP ÷ PE</code></td><td>Loss-makers: TTM profit ÷ shares</td></tr>
  <tr><td>Core PE</td><td><code>PE ÷ (1 − other income ÷ PBT)</code></td><td>PE if other income were stripped out of profit. Orange when 25%+ above PE (i.e. more than 20% of profit is other income). Not for financials</td></tr>
  <tr><td>PB</td><td><code>CMP ÷ BOOK_VALUE</code></td><td>If book value is missing: <code>PE × ROE ÷ 100</code>, marked *</td></tr>
  <tr><td>Earnings yield</td><td><code>100 ÷ PE</code></td><td></td></tr>
  <tr><td>EPS CAGR</td><td><code>(EPS latest FY ÷ EPS 5 FY earlier)^(1/years) − 1</code></td><td>Uses fewer years (min 2.5) if that is all the unbroken history there is; needs positive EPS at both ends.
  If the implied share count (PAT ÷ EPS) changed more than 2.5× over the window, old EPS is on a pre-IPO / unadjusted share base, so profit (PAT) CAGR is used instead, marked (PAT)</td></tr>
  <tr><td>PEG</td><td><code>PE ÷ EPS CAGR</code></td><td>Below 1 = cheap for its growth</td></tr>
  <tr><td>Graham number</td><td><code>√(22.5 × TTM EPS × book value per share)</code></td><td>Upside = Graham number ÷ CMP − 1. Graham pass = PE × PB ≤ 22.5</td></tr>
  <tr><td>Cash conversion</td><td><code>Σ operating cash flow ÷ Σ net profit</code>, last ${D.cashYears} FY</td><td>Below 0.5 = profits not turning into cash. Not for financials</td></tr>
  <tr><td>FCF yield</td><td><code>avg(CFO + CFI) over last ${D.cashYears} FY ÷ Mcap</code></td><td>CFI includes capex and treasury investments, so this is a conservative FCF proxy. Not for financials</td></tr>
  <tr><td>OPM vs 5Y</td><td><code>current OPM − 5Y average OPM</code> (pp)</td><td>Positive = margins expanding</td></tr>
  <tr><td>Dilution</td><td>share count CAGR, share count = <code>PAT ÷ EPS</code> at both ends of the EPS window</td><td>Shares growing = QIP, ESOPs, rights, merger issuance. Not computed when the share base changed more than 2.5× (see EPS CAGR)</td></tr>
  <tr><td>Industry PE</td><td>median / simple average / market-cap weighted (<code>Σ Mcap ÷ Σ (Mcap ÷ PE)</code>)</td><td>Only stocks with positive PE. Picker at the top of each page</td></tr>
  <tr><td>vs Industry PE</td><td><code>PE ÷ industry PE − 1</code></td><td>Negative = discount</td></tr>
  <tr><td>Div yield</td><td><code>DIV_YIELD_PCT</code> column from the CSV</td><td>Shown bold green at 3% or more</td></tr>
  </table></div>

  <h2>Value score (0–100)</h2>
  <p>Each stock is ranked against the other stocks <b>in its own industry</b> on each metric below (if fewer than 4 peers have that metric, it is ranked against all stocks).
  A rank becomes a percentile from 0 (worst) to 1 (best), then the weighted average × 100 is the score. Current weights (editable on the Value screen):</p>
  <div class="wrap"><table><tr><th>Metric</th><th>Better when</th><th>Weight</th></tr>
  ${Object.keys(W_DEF).map(k => `<tr><td>${W_LABEL[k]}</td><td>${METRICS[k].hi?'higher':'lower'}</td><td>${W[k]}</td></tr>`).join('')}</table></div>
  <p>Missing metrics are skipped and the remaining weights re-scaled; a stock needs at least half of the applicable weight to get a score, and must have a positive PE.
  For financials ROE replaces ROCE, and FCF yield / cash conversion are not applicable (their cash flows are their business), so they are scored on the rest.</p>

  <h2>Value-trap flags</h2>
  <p>Cheap stocks are often cheap for a reason. A stock is flagged (and hidden when "Hide value traps" is on) if any of these hold:</p>
  <div class="wrap"><table><tr><th>Flag</th><th>Rule</th></tr>
  <tr><td>EPS shrinking</td><td>EPS CAGR ≤ 0</td></tr>
  <tr><td>no growth data / loss now</td><td>EPS CAGR cannot be measured</td></tr>
  <tr><td>loss yr</td><td>any negative EPS year in the last 5</td></tr>
  <tr><td>EPS near 5Y low</td><td>TTM EPS within 10% of its lowest in the last 5 years</td></tr>
  <tr><td>weak cash conversion</td><td>cash conversion &lt; 0.5 (non-financials)</td></tr>
  <tr><td>other-income heavy</td><td>other income &gt; 40% of PBT (non-financials)</td></tr>
  <tr><td>low interest cover</td><td>interest coverage &lt; 2 (non-financials)</td></tr>
  <tr><td>equity dilution</td><td>share count growing faster than 6% a year (consolidated PAT includes minority interest, so small values are noise)</td></tr></table></div>

  <h2>Magic Formula</h2>
  <p>Joel Greenblatt's method: rank all eligible stocks by earnings yield (highest = rank 1) and separately by ROCE (highest = rank 1), add the two ranks, lowest total is best.
  The original uses EBIT ÷ enterprise value; the CSV has no debt data so 100 ÷ PE is used. Financials are excluded by default because ROCE does not apply to them. Stocks need positive PE and ROCE.</p>

  <h2>Promoter and public holding</h2>
  <p>Holding is already a percentage, so change is measured in <b>percentage points (pp)</b>, not CAGR: 52.1% → 55.3% = +3.2 pp. (A CAGR on a percentage blows up for small holdings, e.g. 2% → 4% would read as +100%.)</p>
  <div class="wrap"><table>
  <tr><th>Column</th><th>Meaning</th></tr>
  <tr><td>Δ full period</td><td>last available quarter − first available quarter, per stock. Most stocks: ${esc(D.shSpan)}; stocks with older quarters in the CSV use the longer period (see Period column)</td></tr>
  <tr><td>Δ last 1Y</td><td>last quarter − the quarter one year earlier</td></tr>
  <tr><td>Qtrs ↑ / ↓</td><td>number of consecutive-quarter moves up / down by more than 0.05 pp</td></tr>
  <tr><td>FII + DII Δ</td><td>FII change + DII change over the same period ("smart money")</td></tr>
  <tr><td>Check</td><td>any single-quarter move larger than ${D.jumpPP} pp, usually a merger, OFS, QIP or fresh listing rather than buying / selling. Hidden by default</td></tr></table></div>
  <p><b>Reading it:</b> promoter rising = insiders buying (creeping acquisition), usually positive. Public falling while FII + DII rise = institutions absorbing retail supply, usually positive. Public rising = shares moving to retail, often promoters or funds selling.</p>

  <h2>Lists and overlaps</h2>
  <p>Lists combined: ${D.lists.map(esc).join(', ')}. A stock that appears in more than one list (for example ranks 251–500 are in both NIFTY 500 and Smallcap 500) is kept once, using the row with the most data, and remembers every list it belongs to.
  The <b>List</b> picker at the top shows one list at a time. Industry benchmarks and Value score peers always use every stock across all lists.</p>

  <h2>1Y back</h2>
  <p>The CSVs contain no old prices, so this view is <b>fundamentals only</b>. It rebuilds each company as it stood on ${esc(D.then.date)} (one year before the download) using only what had been published by then, and compares it with the latest numbers now.</p>
  <div class="wrap"><table><tr><th>Item</th><th>As of the 1Y-back date</th></tr>
  <tr><td>Results known then</td><td>quarters that ended ≥ 45 days before, financial years that ended ≥ 60 days before, shareholding ≥ 21 days before</td></tr>
  <tr><td>ROE</td><td>that year's PAT ÷ average equity (reserves + today's share capital)</td></tr>
  <tr><td>ROCE</td><td>approximate: today's ROCE × (EBIT then ÷ EBIT now) × (total assets now ÷ then)</td></tr>
  <tr><td>EPS CAGR, cash conversion, other income, interest cover, OPM trend, value-trap flags</td><td>same formulas as today, on the history known then</td></tr>
  <tr><td>Quality score (then)</td><td>Value score with only ROCE / ROE, EPS growth and cash conversion (the price-based parts cannot be rebuilt)</td></tr>
  <tr><td>Magic Formula</td><td>ROCE rank only</td></tr>
  <tr><td>Hidden</td><td>price, PE, core PE, PB, PEG, Graham, dividend / earnings / FCF yield, 52-week high. Market cap shown is today's (for the filters only)</td></tr></table></div>
  <p>"Since" columns (then → latest now):</p>
  <div class="wrap"><table><tr><th>Column</th><th>Formula</th></tr>
  <tr><td>Profit growth since</td><td><code>TTM PAT now ÷ TTM PAT then − 1</code>, each the sum of the last 4 quarters. Loss bases are labelled "turned profitable" / "still loss"</td></tr>
  <tr><td>Revenue growth since</td><td><code>TTM revenue now ÷ TTM revenue then − 1</code></td></tr>
  <tr><td>ROE Δ / OPM Δ</td><td>latest financial year minus the year known then, in percentage points</td></tr>
  <tr><td>Promoter Δ since</td><td>promoter % now minus promoter % then</td></tr></table></div>
  <p>Each page shows the median profit growth of the list it produced against all stocks. This answers "did the businesses these screens liked a year ago keep delivering?", not "did the share price go up?". Only stocks in today's lists are included.</p>

  <h2>Trend score (Improving tab)</h2>
  <p>Answers "is the business getting better?", separately from "is it cheap?". It is <b>not</b> part of the Value score or the Magic Formula ranks; on those tabs it is only a filter (Min Trend %).
  Each check is pass / fail. Ratios (margins, ROE, holding %) are compared in percentage points, never as a CAGR, because a CAGR on a percentage explodes on small bases.</p>
  <div class="wrap"><table><tr><th>Check</th><th>Green when</th><th>Data</th></tr>
  <tr><td>Profit</td><td>TTM PAT ≥ TTM PAT four quarters earlier, in at least 4 of the last 6 quarter-ends</td><td>quarterly</td></tr>
  <tr><td>Revenue</td><td>same test on TTM revenue</td><td>quarterly</td></tr>
  <tr><td>Operating margin</td><td>TTM OPM (TTM EBITDA ÷ TTM revenue) ≥ a year earlier, in at least 4 of the last 6 quarter-ends. Not for financials</td><td>quarterly</td></tr>
  <tr><td>Promoter holding</td><td>promoter % now ≥ 6 quarters ago (stocks with no promoter: n/a)</td><td>quarterly</td></tr>
  <tr><td>ROE</td><td>latest FY ROE ≥ ROE two FY earlier (PAT ÷ (reserves + share capital))</td><td>annual</td></tr>
  <tr><td>ROCE (proxy)</td><td>latest FY EBIT ÷ total assets ≥ two FY earlier. Proxy because the CSV has no debt data. Not for financials</td><td>annual</td></tr>
  <tr><td>Cash conversion</td><td>Σ CFO ÷ Σ PAT over the last 3 FY ≥ the 3 FY before (2 + 2 if history is short). Not for financials</td><td>annual</td></tr></table></div>
  <p>Profit uses PAT rather than quarterly EPS because quarterly EPS in the CSV is not adjusted for splits and bonuses. Comparing each quarter's TTM with the TTM a year earlier removes seasonality.
  <b>Trend % = checks passed ÷ checks with data</b>, shown as e.g. 5/7. A stock needs data for at least 4 checks (3 for financials). Missing checks are left out, not counted as fails.
  In 1Y back mode the checks use only results published by then, so fewer stocks qualify (shorter quarterly history).</p>
  <p><b>Caveats:</b> improving margins and ROE peak with the business cycle, so cyclicals score best just before they turn. Use it alongside valuation, not alone.</p>

  <h2>PSU and semi-PSU filter</h2>
  <p>The CSV has no ownership-type field, so government-owned companies are tagged from a fixed list.
  <b>PSU</b> (${S.filter(s=>s.psu==='psu').length} stocks): central or state government holds a majority, directly or through another PSU (e.g. SBI Life, Chennai Petroleum).
  <b>semi</b> (${S.filter(s=>s.psu==='semi').length} stocks): government or PSUs hold a large non-controlling or jointly controlling stake (e.g. Hindustan Zinc, Vodafone Idea, Petronet LNG, IGL).
  The two switches at the top hide them from every tab. Industry PE / PB and the Value score peer ranking still use all stocks, so a stock's score does not change when you toggle them; Magic Formula ranks are recomputed on the visible stocks.</p>

  <h2>How this relates to the screener</h2>
  <p>The screener's Grade, Fair Value and Rank Score are a separate model (fair-value blend + Piotroski + quality). This page does not use them; its Value score is an independent, industry-relative ranking, so the two can disagree on the same stock.</p>

  <h2>What this page cannot do</h2>
  <p>No enterprise value data, so no EV/EBITDA. No historical prices, so no historical PE band. Shareholding history is short (~${esc(D.shSpan)}).</p>
  </div>`);
}

function backInfo() {                                    // was a big banner; now a small popover behind the (i)
  const mode = k => { const c = {}; S.forEach(s => { const v = sv(s,k); if (v) c[v] = (c[v]||0) + 1; });
                      return Object.keys(c).sort((a,b) => c[b]-c[a])[0] || '–'; };
  return `<b>Fundamentals as they stood on ${esc(D.then.date)}</b>Latest results then: quarter ${esc(mode('qThen'))}, FY ${esc(mode('fyThen'))}.
    "Since" columns compare with the latest now (quarter ${esc(mode('qNow'))}, FY ${esc(mode('fyNow'))}). No old prices in the CSVs, so PE, PB, yields and price columns are hidden.`;
}
function route() {
  setMode();
  C.score.label = BACK ? 'Quality score (then)' : 'Value score';
  computeScores();
  const h = location.hash || '#/';
  if (h.startsWith('#/industry/')) industryView(decodeURIComponent(h.slice(11)));
  else if (h === '#/value') valueView();
  else if (h === '#/magic') magicView();
  else if (h === '#/promoter') promoterView();
  else if (h === '#/public') publicView();
  else if (h === '#/improving') improvingView();
  else if (h === '#/method') methodView();
  else homeView();
  meta();
  window.scrollTo(0,0);
}
function meta() {
  $('#meta').innerHTML = `<b>${V().length}</b> stocks in <b>${Object.keys(industries).length}</b> sectors, data downloaded <b>${esc(D.asOf)}</b>`
    + (BACK ? `<span class="backnote">, showing <b>${esc(D.then.date)}</b> <button type="button" class="info" id="backInfo" aria-label="About the 1Y back view">i</button>`
            + `<span class="pop" id="backPop">${backInfo()}</span></span>` : '');
  const b = $('#backInfo');
  if (b) b.addEventListener('click', e => { e.stopPropagation(); $('#backPop').classList.toggle('open'); });
}
document.addEventListener('click', e => { const p = $('#backPop'); if (p && !e.target.closest('#backPop')) p.classList.remove('open'); });
const saveGF = () => lsSet('gf', JSON.stringify(GF));
[['psu','#hidePSU'], ['semi','#hideSemi'], ['back','#back1y']].forEach(([k, id]) => {
  const el = $(id); el.checked = !!GF[k];
  el.addEventListener('change', () => { GF[k] = el.checked; saveGF(); route(); });
});
$('#backChip').title = 'Show fundamentals as of ' + D.then.date + ' and how each business did since';
const us = $('#uniSel');
us.innerHTML = '<option value="all">All lists</option>' + D.lists.map(l => `<option value="${esc(l)}">${esc(l)}</option>`).join('');
us.value = D.lists.includes(GF.uni) ? GF.uni : 'all';
GF.uni = us.value;                                          // drop an unknown list name passed in the URL
us.addEventListener('change', () => { GF.uni = us.value; saveGF(); route(); });

/* day / night: same key as the screener, so both pages always match */
/* ---------- themed dropdown: hides the native <select>, keeps it as the source of truth ---------- */
const CHEV = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="M2 3.5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const closeFsel = except => document.querySelectorAll('.fsel.open').forEach(w => { if (w !== except) w.classList.remove('open'); });
function fancySelect(sel) {
  if (sel.dataset.fancy) return;
  sel.dataset.fancy = '1';
  const wrap = document.createElement('span'); wrap.className = 'fsel';
  sel.parentNode.insertBefore(wrap, sel); wrap.appendChild(sel);
  const btn = document.createElement('button'); btn.type = 'button';
  btn.className = 'fsel-btn' + (sel.closest('.chip') ? '' : ' boxed');   // header chip already draws the box
  btn.setAttribute('aria-haspopup', 'listbox');
  const menu = document.createElement('div'); menu.className = 'fsel-menu'; menu.setAttribute('role', 'listbox');
  wrap.append(btn, menu);
  const paint = () => {
    const cur = sel.options[sel.selectedIndex];
    btn.innerHTML = `<span>${esc(cur ? cur.text : '')}</span>${CHEV}`;
    menu.innerHTML = [...sel.options].map((o, i) =>
      `<button type="button" role="option" class="fsel-opt${i === sel.selectedIndex ? ' on' : ''}" data-i="${i}">${esc(o.text)}</button>`).join('');
  };
  paint();
  btn.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();
    closeFsel(wrap);
    const open = wrap.classList.toggle('open');
    btn.setAttribute('aria-expanded', open);
    if (open) { const on = $('.fsel-opt.on', menu); if (on) on.focus(); }
  });
  menu.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();
    const o = e.target.closest('.fsel-opt'); if (!o) return;
    wrap.classList.remove('open'); btn.focus();
    if (+o.dataset.i !== sel.selectedIndex) {
      sel.selectedIndex = +o.dataset.i;
      paint();
      sel.dispatchEvent(new Event('change', {bubbles:true}));   // existing listeners run unchanged
    }
  });
  menu.addEventListener('keydown', e => {
    const opts = [...menu.querySelectorAll('.fsel-opt')], i = opts.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (opts[i + 1] || opts[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (opts[i - 1] || opts[opts.length - 1]).focus(); }
    else if (e.key === 'Escape') { wrap.classList.remove('open'); btn.focus(); }
  });
}
document.addEventListener('click', () => closeFsel());
document.querySelectorAll('select').forEach(fancySelect);   // header List picker
new MutationObserver(() => view.querySelectorAll('select:not([data-fancy])').forEach(fancySelect))
  .observe(view, {childList:true, subtree:true});            // filter boxes re-render on every change, not only on route
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFsel(); });

$('#themeBtn').addEventListener('click', () => {
  const day = !document.body.classList.contains('day');
  document.body.classList.toggle('day', day); document.body.classList.toggle('night', !day);
  document.documentElement.dataset.theme = day ? 'day' : 'night';   // scrollbar dot + dropdown shadow follow the theme
  try { localStorage.setItem('hnimanshu_theme', day ? 'day' : 'night'); } catch (e) {}
});

if (WEB) {                                                  // website: back button, no offline save
  $('#saveHtml').remove();
} else {                                                    // local file: no screener to go back to
  $('#backBtn').remove();
  $('#homeLink').removeAttribute('href');
  const PAGE_SOURCE = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;   // pristine copy before any rendering
  $('#saveHtml').addEventListener('click', () => {
    const blob = new Blob([PAGE_SOURCE], {type:'text/html'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'valuation_dashboard_' + D.asOfIso + '.html';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
}
window.addEventListener('hashchange', route);
route();
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# RENDER / BUILD
# ----------------------------------------------------------------------------
def render_html(now, then, back, asof, web=False):
    """Fill the HTML template with the computed records."""
    pro = [r["h"]["pro"] for r in now if "pro" in r["h"]]
    span = pd.Series([p["since"] + " → " + p["till"] for p in pro]).mode()[0] if pro else "n/a"
    lists = [u for u in UNIVERSES.values() if any(u in r["uni"] for r in now)]
    lists += sorted({u for r in now for u in r["uni"]} - set(lists))
    payload = {
        "stocks": now,
        "lists": lists,
        "asOf": asof.strftime("%d %b %Y"),
        "asOfIso": asof.strftime("%Y-%m-%d"),
        "shSpan": span,
        "jumpPP": JUMP_PP,
        "cashYears": CASH_YEARS,
        "staleDays": MAX_STALE_DAYS,
        "then": {"stocks": then, "date": back.strftime("%d %b %Y")},
    }
    data = json.dumps(clean(payload), allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    html = HTML.replace("__WEB__", "true" if web else "false")     # flag first, data can't contain it then
    return html.replace("__DATA__", data), span


def build_site(paths, out_path, minify=None):
    """Called from build.py: read only `paths`, write the web version to `out_path`."""
    df, asof = load_stocks(paths)
    df = df.sort_values("PE", na_position="last")
    now, then, back = build_all(df, asof)
    html, span = render_html(now, then, back, asof, web=True)
    if minify:
        html = minify(html)
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print("    Fundamentals: %d stocks, data as of %s, 1Y back = %s, %.1f MB -> %s" % (
        len(now), asof.strftime("%d %b %Y"), back.strftime("%d %b %Y"), len(html.encode("utf-8")) / 1e6, out_path))
    return len(now)


def main():
    """Local use: scan this folder, write dashboard.html + dated copy, open browser."""
    try:
        df, asof = load_stocks()
    except RuntimeError as e:
        sys.exit("%s in %s" % (e, HERE))
    df = df.sort_values("PE", na_position="last")
    write_csvs(df)
    now, then, back = build_all(df, asof)
    html, span = render_html(now, then, back, asof, web=False)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    archive = os.path.join(ARCHIVE_DIR, "dashboard_%s.html" % asof.strftime("%Y-%m-%d"))
    with open(archive, "w", encoding="utf-8") as f:
        f.write(html)

    cnt = lambda k: sum(1 for r in now if r[k] is not None)
    pro = sum(1 for r in now if "pro" in r["h"])
    print("Stocks: %d | PB: %d | EPS CAGR: %d | FCF yield: %d | holding history: %d (mostly %s)" % (
        len(now), cnt("pb"), cnt("cagr"), cnt("fcfy"), pro, span))
    print("1Y back : fundamentals as of %s, profit growth since then for %d stocks" % (
        back.strftime("%d %b %Y"), sum(1 for t in then if t["since"]["patG"] is not None)))
    print("Dashboard :", OUT_HTML)
    print("Saved copy:", archive)
    if OPEN_BROWSER:
        webbrowser.open(pathlib.Path(OUT_HTML).as_uri())


if __name__ == "__main__":
    main()
