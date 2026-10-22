#!/usr/bin/env python3
"""
fundamentals.py — HNImanshu Fundamentals page
==============================================
Two ways to run it:

  1. From build.py (website):
         import fundamentals
         fundamentals.build_site([csv1, csv2, csv3], "public/factor.html", minify=minify_html_css)
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
OTH_TAG = "oth"                    # file tag of the Others files (fundamentals_oth.json …)
OTHERS_KEY, OTHERS_LABEL = "others", "Others"   # every other NSE stock (Nifty_Others_Fundamentals.csv, others.py):
                                                # its own list, NOT part of "All lists"

GROWTH_YEARS = 5          # EPS / PAT / reserves CAGR window (uses fewer years if that is all there is)
CASH_YEARS = 5            # cash conversion and FCF are averaged over this many financial years
MAX_GAP_DAYS = 490        # a gap bigger than this between two annual points breaks the history
MAX_STALE_DAYS = 400      # latest annual figure must be newer than this vs the reference date
JUMP_PP = 10              # a single-quarter holding move bigger than this = likely merger / OFS / listing
BACK_DAYS = 365           # the "1Y back" snapshot date = download date minus this
# every back period on the site's "Back" dropdown: key (prices.csv column suffix) -> (label, days)
PERIODS = {"3M": ("3M", 91), "6M": ("6M", 182), "9M": ("9M", 273), "1Y": ("1Y", 365),
           "1_25Y": ("1.25Y", 456), "1_5Y": ("1.5Y", 548)}
TODAY_SAME = True         # True: today is rebuilt with the SAME formulas as every Historical date (3M … 1.5Y back), priced
                          #   with Angel's close, so today vs back is like for like everywhere (tables, checklist, history line).
                          # False: today shows Screener's own ratios (PE, ROE, ROCE, yields), as on screener.in.
DEFAULT_BACK = "1Y"       # embedded in the page; the others load on demand (fundamentals_back_<key>.json)
# Trend score (improving fundamentals) - separate from Value score and Magic Formula
TREND_QTRS = 6            # quarterly checks look at the last 6 quarter-ends
TREND_SHARE = 2 / 3       # "consistently green" = TTM up year-on-year in >= 2/3 of those quarters (4 of 6)
TREND_MIN_CHECKS = 4      # a stock needs at least this many checks with data to get a Trend score
TREND_MIN_CHECKS_FIN = 3  # financials only have 4 applicable checks (no OPM / ROCE / cash conversion)
# SAME-METHOD engine (today and 1Y back computed by one function, on windows the 1Y-back date can support)
# 1Y back the CSV holds ~9 quarters of results and ~4 quarters of shareholding, so both dates use:
SM_TREND_QTRS = 2         # TTM year-on-year comparisons per quarterly Trend check (needs 9 quarters)
SM_TREND_MIN = 2          # ... all of them must be available
SM_SH_QTRS = 4            # shareholding window: last 4 quarter-ends = change over 3 quarters
PRICES_CSV = "prices.csv" # written weekly by fetch_prices.py (Angel One); optional
PRICE_TOL_DAYS = 7        # a price is used if its date is within ±7 days of the date it stands for (not exact)
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
ANNUAL_FIELDS = ["B_BS_TOTAL_BORROWINGS_CR", "B_BS_SHARE_CAPITAL_CR",            # present once the scraper fix lands
                 "A_PL_EPS_BASIC", "A_PL_PAT_CR", "A_PL_PBT_CR", "A_PL_INTEREST_CR", "A_PL_OTHER_INCOME_CR",
                 "A_PL_DIVIDEND_CR", "A_PL_OPM_PCT", "A_PL_EBITDA_CR", "C_CF_OPERATING_CR", "C_CF_INVESTING_CR",
                 "B_BS_RESERVES_CR", "B_BS_TOTAL_ASSETS_CR",
                 "B_BS_NET_BLOCK_CR", "B_BS_CWIP_CR", "B_BS_INVESTMENTS_CR",          # asset backing (fixed assets / investments once scraped)
                 "A_PL_REVENUE_CR"]                                                    # 5-year sales growth (checklist light)
ANNUAL_RE = re.compile(r"^(%s)_(%s)(\d{4})$" % ("|".join(ANNUAL_FIELDS), MONTHS))
QTR_RE = re.compile(r"^(Q_EPS_GENERIC|Q_PL_PAT_CR|Q_PL_REVENUE_CR|Q_PL_EBITDA_CR|Q_PL_GNPA_PCT|Q_PL_NNPA_PCT|Q_PL_PBT_CR|Q_PL_OTHER_INCOME_CR)_Q(%s)(\d{4})$" % MONTHS)   # NPA: banks, once scraped   # EBITDA -> quarterly OPM
SH_RE = re.compile(r"^SH_(PROMOTER|FII|DII|PUBLIC)_PCT_Q(%s)(\d{4})$" % MONTHS)
SH_KEY = {"PROMOTER": "pro", "PUBLIC": "pub", "FII": "fii", "DII": "dii"}

SCALARS = ["MARKET_CAP_CR", "CMP", "HIGH_52W", "PE", "BOOK_VALUE", "ROE_PCT", "ROCE_PCT", "DIV_YIELD_PCT",
           "TTM_PAT_CR", "SHARES_CR", "BS_RESERVES_CR", "OTHER_INCOME_TO_PBT_PCT", "INTEREST_COVERAGE",
           "OPM_CALC_PCT", "PL_OPM_PCT", "OPM_AVG_5YR_PCT", "DIVIDEND_CONSECUTIVE_YRS", "F_SCORE"]
# every CSV column read by name anywhere in this file (plus SCALARS and the dated series matched by the regexes above)
USED_COLS = set(SCALARS) | {"SYMBOL", "SCREENER_URL", "COMPANY_NAME", "INDUSTRY", "DATE_DOWNLOADED", "BOOK_VALUE",
             "BS_RESERVES_CR", "CMP", "DIVIDEND_CONSECUTIVE_YRS", "DIV_YIELD_PCT", "F_SCORE", "HIGH_52W", "INTEREST_COVERAGE",
             "MARKET_CAP_CR", "OPM_AVG_5YR_PCT", "OPM_CALC_PCT", "OTHER_INCOME_TO_PBT_PCT", "PE", "PL_OPM_PCT", "ROCE_PCT",
             "ROE_PCT", "SHARES_CR", "TTM_PAT_CR"}

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
    if "others" in flat:
        return OTHERS_KEY
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
        label = UNIVERSES.get(key, OTHERS_LABEL if key == OTHERS_KEY else key)
        if key == OTHERS_KEY:                           # small companies: keep only those screener had a price / mcap for
            has = pd.Series(False, index=d.index)
            for c in ("CMP", "MARKET_CAP_CR"):
                if c in d.columns:
                    has |= pd.to_numeric(d[c], errors="coerce").notna()
            d = d[has]
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
    # keep only the columns this file reads (named ones + the dated yearly / quarterly / holding series): the CSVs carry
    # ~3,000–4,000 columns, mostly unused, and every per-stock loop below copies whole rows, so this cuts build time a lot
    keep = [c for c in df.columns if c in USED_COLS or c.startswith("_")
            or ANNUAL_RE.match(c) or QTR_RE.match(c) or SH_RE.match(c)]
    df = df[keep].copy()
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
    main = df["_UNIS"].map(lambda u: any(x != OTHERS_LABEL for x in u))   # data date = the 3 lists' (Others is scraped on other days)
    asof = pd.to_datetime(df.loc[main if main.any() else df.index, "DATE_DOWNLOADED"], errors="coerce").max()
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


def assets_at(r, F, until=None):
    """Balance-sheet asset backing from the latest financial year known at `until` (all parts from that same year).
    ta = total assets, fa = fixed assets (net block incl. land at book cost) + CWIP, inv = investments."""
    ta = series(r, F.get("B_BS_TOTAL_ASSETS_CR", []), until)
    if not ta:
        return {}
    d = ta[-1][0]
    same = lambda k: next((p[2] for p in series(r, F.get(k, []), until) if p[0] == d), None)
    nb, cw = same("B_BS_NET_BLOCK_CR"), same("B_BS_CWIP_CR")
    return {"ta": ta[-1][2], "fa": (nb + (cw or 0)) if nb is not None else None,
            "inv": same("B_BS_INVESTMENTS_CR"), "fy": ta[-1][1]}


def safety(r, F, acut, ref, fin):
    """Graham-style safety numbers, from the yearly / quarterly rows known at the date (same function today and 1Y back).
    de   = borrowings ÷ (reserves + share capital), latest FY (non-financials; needs the scraper's Borrowings rows)
    pup  = (years profit rose, years compared) over the last 10 financial years
    roa  = PAT ÷ total assets, latest FY (used for financials)
    gnpa = latest quarterly Gross NPA % (banks; needs the scraper's NPA rows)"""
    yr = lambda k: {p[0]: p[2] for p in series(r, F.get(k, []), acut)}
    pat, ta, debt, res, cap = (yr(k) for k in ("A_PL_PAT_CR", "B_BS_TOTAL_ASSETS_CR", "B_BS_TOTAL_BORROWINGS_CR",
                                                 "B_BS_RESERVES_CR", "B_BS_SHARE_CAPITAL_CR"))
    out = {"cfop": None, "de": None, "pup": None, "roa": None, "gnpa": None, "nnpa": None, "pcr": None, "q5": None, "q5n": 0, "debitda": None}
    ys = sorted(pat)
    if ys and (ref - ys[-1]).days <= MAX_STALE_DAYS:
        run = [ys[-1]]                                              # unbroken yearly run, newest first
        for d in reversed(ys[:-1]):
            if 300 <= (run[-1] - d).days <= 430 and len(run) < 11:
                run.append(d)
            else:
                break
        run.reverse()
        if len(run) >= 6:                                           # at least 5 year-on-year comparisons
            out["pup"] = (sum(pat[b] > pat[a] for a, b in zip(run, run[1:])), len(run) - 1)
        t = ys[-1]
        if t in ta and ta[t] > 0:
            out["roa"] = pat[t] / ta[t] * 100
        eqy = lambda d: (res[d] + (cap.get(d) or share_cap(r) or 0)) if d in res else None
        if not fin and t in debt and t in res:
            eq = eqy(t)
            if eq and eq > 0:
                out["de"] = debt[t] / eq
            ebitda = yr("A_PL_EBITDA_CR").get(t)
            if ebitda is not None:
                out["debitda"] = debt[t] / ebitda if ebitda > 0 else (0.0 if debt[t] <= 0 else 99.0)   # loss-making + debt = fail
        # cash-flow consistency: years with positive operating cash flow, last 10 FY (non-financials:
        # a lender's CFO swings with deposits and loans given, so it says nothing about quality)
        cfo = yr("C_CF_OPERATING_CR")
        cy = sorted(cfo)
        if not fin and cy and (ref - cy[-1]).days <= MAX_STALE_DAYS:
            run = [cy[-1]]
            for d in reversed(cy[:-1]):
                if 300 <= (run[-1] - d).days <= 430 and len(run) < 10:
                    run.append(d)
                else:
                    break
            if len(run) >= 5:
                out["cfop"] = (sum(cfo[d] > 0 for d in run), len(run))
        # 5-year median ROCE (ROE for financials): one good year at the top of a cycle can't carry it
        pbt, intr = yr("A_PL_PBT_CR"), yr("A_PL_INTEREST_CR")
        vals = []
        for d in [y for y in ys if y >= ys[-1] - pd.Timedelta(days=5 * 366)][-5:]:
            if fin:
                prev = [y for y in ys if 300 <= (d - y).days <= 430]
                e1, e0 = eqy(d), (eqy(prev[-1]) if prev else None)
                base = (e1 + e0) / 2 if (e1 and e0 and e0 > 0) else e1
                if base and base > 0:
                    vals.append(pat[d] / base * 100)
            elif d in pbt and d in debt and d in res:                # true capital employed only, never the proxy
                ce = (eqy(d) or 0) + debt[d]
                if ce > 0:
                    vals.append((pbt[d] + intr.get(d, 0)) / ce * 100)
        if len(vals) >= 3:
            out["q5"], out["q5n"] = float(pd.Series(vals).median()), len(vals)
    if fin:
        q = series(r, F.get("Q_PL_GNPA_PCT", []), (acut + pd.Timedelta(days=ANNUAL_LAG_DAYS - QUARTER_LAG_DAYS)) if acut is not None else None)
        if q and (ref - q[-1][0]).days <= 200:
            out["gnpa"] = q[-1][2]
            n = {p[0]: p[2] for p in series(r, F.get("Q_PL_NNPA_PCT", []))}.get(q[-1][0])   # same quarter
            if n is not None:
                out["nnpa"] = n
                if q[-1][2] > 0:                                    # provision coverage ≈ 1 − net NPA ÷ gross NPA
                    out["pcr"] = max(0.0, (1 - n / q[-1][2]) * 100)
    return out


def series(row, cols, until=None):
    """[(date, label, value)] oldest → newest, values known by `until`. Called ~1,000 times per stock: kept to plain
    Python (row is a dict, NaN test v != v), which is ~20× faster than pandas' per-cell lookups."""
    out = []
    for dt, lab, c in cols:
        if until is not None and dt > until:
            break                                           # cols are oldest → newest: the rest are later still
        v = row[c]
        if v is None or v != v:
            continue
        out.append((dt, lab, float(v)))
    return out


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


def inputs_sm(r, F, cut, fin, price, hi52):
    """SAME-METHOD inputs: every metric from the raw dated rows known at `cut` + one price.
    Called with (today, LTP) and with (1 year back, price then), so both dates share every formula.
    `price` must be on today's share basis (fetch_prices.py divides out later splits / bonuses)."""
    acut = cut - pd.Timedelta(days=ANNUAL_LAG_DAYS)
    qcut = cut - pd.Timedelta(days=QUARTER_LAG_DAYS)
    scut = cut - pd.Timedelta(days=HOLDING_LAG_DAYS)

    def fy(k, i=-1):
        a = series(r, F.get(k, []), acut)
        return a[i] if len(a) >= abs(i) else None

    def ttm4(k):
        q = series(r, F.get(k, []), qcut)[-4:]
        if len(q) == 4 and (q[-1][0] - q[0][0]).days < 300 and (cut - q[-1][0]).days < 200:
            return sum(p[2] for p in q)
        return None

    shares = r["SHARES_CR"] if ok(r["SHARES_CR"]) and r["SHARES_CR"] > 0 else (
        r["MARKET_CAP_CR"] / r["CMP"] if ok(r["MARKET_CAP_CR"]) and ok(r["CMP"]) and r["CMP"] > 0 else None)
    pat, res, res0 = fy("A_PL_PAT_CR"), fy("B_BS_RESERVES_CR"), fy("B_BS_RESERVES_CR", -2)
    cap_fy, cap_fy0 = fy("B_BS_SHARE_CAPITAL_CR"), fy("B_BS_SHARE_CAPITAL_CR", -2)
    cap_now = share_cap(r)
    cap = lambda c, rs: (c[2] if c and rs and c[0] == rs[0] else cap_now)   # that year's capital once scraped
    eq = (res[2] + cap(cap_fy, res)) if res else None
    eq0 = (res0[2] + cap(cap_fy0, res0)) if res0 else None

    ttm_pat = ttm4("Q_PL_PAT_CR")
    if ttm_pat is None and pat:
        ttm_pat = pat[2]                                   # no quarterly history: latest financial year
    eps = (ttm_pat / shares) if (ttm_pat is not None and shares) else None   # EPS on today's share basis
    mcap = (price * shares) if (ok(price) and shares) else None
    pe = (price / eps) if (ok(price) and eps and eps > 0) else None
    bvps = (eq / shares) if (eq and shares and eq > 0) else None
    pb = (price / bvps) if (ok(price) and bvps) else None

    roe = None
    if pat and eq and pat[0] == res[0]:
        base = (eq + eq0) / 2 if (eq0 and eq0 > 0) else eq
        roe = pat[2] / base * 100 if base > 0 else None

    pbt, intr, oth = fy("A_PL_PBT_CR"), fy("A_PL_INTEREST_CR"), fy("A_PL_OTHER_INCOME_CR")
    ta, debt = fy("B_BS_TOTAL_ASSETS_CR"), fy("B_BS_TOTAL_BORROWINGS_CR")
    ebit = (pbt[2] + intr[2]) if (pbt and intr and pbt[0] == intr[0]) else (pbt[2] if pbt else None)
    roce, roce_src = None, None
    if ebit is not None and eq and debt and debt[0] == res[0] and (eq + debt[2]) > 0:
        roce, roce_src = ebit / (eq + debt[2]) * 100, "ce"      # true capital employed (needs Borrowings rows)
    elif ebit is not None and ta and ta[2] > 0:
        roce, roce_src = ebit / ta[2] * 100, "ta"               # proxy until Borrowings are scraped
    oi = (oth[2] / pbt[2] * 100) if (oth and pbt and pbt[2] > 0) else None
    icov = ((pbt[2] + intr[2]) / intr[2]) if (pbt and intr and intr[2] > 0) else None
    opm = series(r, F.get("A_PL_OPM_PCT", []), acut)[-5:]
    opm_t = (opm[-1][2] - sum(p[2] for p in opm) / len(opm)) if (not fin and len(opm) >= 3) else None
    payout = fy("A_PL_DIVIDEND_CR")                            # Screener's "Dividend payout %" row
    dy = (payout[2] * pat[2] / 100 / mcap * 100) if (payout and pat and payout[0] == pat[0] and mcap and pat[2] > 0) else None
    return {"trend": trend_checks(r, F, cut, fin, lag=True, qtrs=SM_TREND_QTRS, min_cmp=SM_TREND_MIN, pro_q=SM_SH_QTRS - 1),
            "ref": cut, "acut": acut, "scut": scut, "shq": SM_SH_QTRS, "price": price, "pe": pe, "ttm": eps,
            "mcap": mcap, "pb": pb, "pbSrc": "sm", "roe": roe, "roce": roce, "roceSrc": roce_src, "dy": dy,
            "oi": oi, "icov": icov, "opmT": opm_t, "hi52": hi52}


def load_prices(path):
    """prices.csv from fetch_prices.py → ({SYMBOL: row}, benchmark row or None). Missing file → ({}, None)."""
    p = pathlib.Path(path)
    if not p.exists():
        return {}, None
    d = pd.read_csv(p)
    d["SYMBOL"] = d["SYMBOL"].astype(str).str.strip().str.upper()
    rows = {r["SYMBOL"]: r for r in d.to_dict("records")}
    print("Prices: %s, %d symbols, %d with a 1Y-back price" % (p.name, len(rows), int(d["PRICE_1Y_ADJ"].notna().sum())))
    return rows, rows.pop("^NIFTY500", None)


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


def yoy_consistency(ttm, qtrs=TREND_QTRS, min_cmp=3):
    """ttm = [(date, value)] consecutive quarter-ends. Compares each of the last `qtrs` points with
    4 quarters earlier. Returns (passed?, ups, checked) or (None, 0, n) when fewer than `min_cmp` comparisons."""
    diffs = [ttm[i][1] - ttm[i - 4][1] for i in range(4, len(ttm))][-qtrs:]
    if len(diffs) < min_cmp:
        return None, 0, len(diffs)
    ups = sum(1 for d in diffs if d >= 0)
    return ups >= math.ceil(len(diffs) * TREND_SHARE - 1e-9), ups, len(diffs)


def trend_checks(r, F, cut, fin, lag=True, qtrs=TREND_QTRS, min_cmp=3, pro_q=TREND_QTRS):
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
        res, ups, n = yoy_consistency(t, qtrs, min_cmp)
        chk[k] = res
        if n >= min_cmp:
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
        res, ups, n = yoy_consistency(t, qtrs, min_cmp)
        chk["opm"] = res
        if n >= min_cmp:
            det["opm"] = "%d/%d qtrs" % (ups, n)                         # same evidence format as profit / revenue
        if len(t) >= 5:
            det["opmD"] = round(t[-1][1] - t[-5][1], 2)

    # 4. promoter holding: not lower than `pro_q` quarters ago (6 normally, 3 in the same-method engine)
    pro = q_run(series(r, F.get("pro", []), scut), cut)
    chk["pro"] = None
    if len(pro) >= 4:
        base = pro[-(pro_q + 1)] if len(pro) > pro_q else pro[0]
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
def quality_extras(r, F, cut):
    """Inputs for the checklist's profit gate and double-weight checks, as known at `cut` (today or a back date):
       qp   = net profit of the last 8 quarters (oldest → newest)    → must be positive, and growing
       qoi  = other income as % of profit before tax, last 4 quarters → is profit propped up by other income?
       sg5  = 5-year sales CAGR % (annual revenue)                    → sales growth
       pyr  = [years with a net profit, years checked], last 10 years → profit every year?
       pro3 = promoter holding change, pp, over up to the last 3 years (12 quarters)"""
    qcut, acut = cut - pd.Timedelta(days=QUARTER_LAG_DAYS), cut - pd.Timedelta(days=ANNUAL_LAG_DAYS)
    scut = cut - pd.Timedelta(days=HOLDING_LAG_DAYS)
    out = {}
    q = series(r, F.get("Q_PL_PAT_CR", []), qcut)[-8:]
    if q and (cut - q[-1][0]).days < 200:                     # a stale last quarter says nothing about now
        out["qp"] = [round(p[2], 1) for p in q]
    oi = {p[0]: p[2] for p in series(r, F.get("Q_PL_OTHER_INCOME_CR", []), qcut)}
    pbt = series(r, F.get("Q_PL_PBT_CR", []), qcut)[-4:]
    if len(pbt) == 4 and all(p[0] in oi for p in pbt):
        tot = sum(p[2] for p in pbt)
        out["qoi"] = round(sum(oi[p[0]] for p in pbt) / tot * 100, 1) if tot > 0 else 999.0   # no pre-tax profit: all of it
    rev = latest_run(series(r, F.get("A_PL_REVENUE_CR", []), acut), cut)
    if len(rev) >= 4:
        a, b = rev[-6] if len(rev) >= 6 else rev[0], rev[-1]
        yrs = (b[0] - a[0]).days / 365.25
        if a[2] > 0 and b[2] > 0 and yrs >= 2.5:
            out["sg5"] = round(((b[2] / a[2]) ** (1 / yrs) - 1) * 100, 2)
    pat = series(r, F.get("A_PL_PAT_CR", []), acut)[-10:]
    if len(pat) >= 3:
        out["pyr"] = [sum(1 for p in pat if p[2] > 0), len(pat)]
    # Checklist tab (Prime): 3-year sales / profit growth, 5-year cash flow, share dilution, NPA trend
    if len(rev) >= 4:
        a, b = rev[-4], rev[-1]
        yrs = (b[0] - a[0]).days / 365.25
        if a[2] > 0 and b[2] > 0 and yrs >= 2.5:
            out["sg3"] = round(((b[2] / a[2]) ** (1 / yrs) - 1) * 100, 2)
    patr = latest_run(series(r, F.get("A_PL_PAT_CR", []), acut), cut)
    if len(patr) >= 4:
        a, b = patr[-4], patr[-1]
        yrs = (b[0] - a[0]).days / 365.25
        if a[2] > 0 and b[2] > 0 and yrs >= 2.5:
            out["pg3"] = round(((b[2] / a[2]) ** (1 / yrs) - 1) * 100, 2)
    cfo = {p[0]: p[2] for p in series(r, F.get("C_CF_OPERATING_CR", []), acut)}
    y5 = [p for p in patr[-5:] if p[0] in cfo]
    if len(y5) >= 3:
        out["cfo5"] = round(sum(cfo[p[0]] for p in y5), 1)                       # cumulative operating cash flow, ₹ Cr
        sp = sum(p[2] for p in y5)
        if sp > 0:
            out["cp5"] = round(out["cfo5"] / sp, 2)                              # CFO ÷ PAT, cumulative
    eps = {p[0]: p[2] for p in series(r, F.get("A_PL_EPS_BASIC", []), acut)}
    shs = [(p[0], p[2] / eps[p[0]]) for p in patr if eps.get(p[0], 0) > 0 and p[2] > 0]   # implied share count
    if len(shs) >= 2:
        new = shs[-1]
        old = next((x for x in shs if (new[0] - x[0]).days <= 5 * 366), shs[0])
        yrs, ratio = (new[0] - old[0]).days / 365.25, new[1] / old[1]
        if yrs >= 2.5 and 0.4 < ratio < 2.5:                                     # outside that band: a split / bonus, not dilution
            out["dil5"] = round((ratio - 1) * 100, 1)
    gq = series(r, F.get("Q_PL_GNPA_PCT", []), qcut)
    if len(gq) >= 5 and (cut - gq[-1][0]).days < 200:
        out["gnpaT"] = round(gq[-1][2] - gq[-5][2], 2)                           # Gross NPA now minus a year ago, pp
    pro = series(r, F.get("pro", []), scut)
    if len(pro) >= 2:
        then = next((p for p in pro if (pro[-1][0] - p[0]).days <= 3 * 366), pro[0])
        out["pro3"] = round(pro[-1][2] - then[2], 2)
        out["pro3y"] = round((pro[-1][0] - then[0]).days / 365.25, 1)
    return out


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

    # asset backing: what the market pays for each rupee of assets on the books (non-financials only;
    # a lender's "assets" are its loan book). Fixed assets carry land at historical cost, so they understate it.
    a = assets_at(r, F, acut) if not fin else {}
    sf = safety(r, F, acut, ref, fin)
    ratio = lambda x: (mcap / x) if (ok(mcap) and x and x > 0) else None
    pta, pfa = ratio(a.get("ta")), ratio(a.get("fa"))
    invp = (a["inv"] / mcap * 100) if (a.get("inv") is not None and ok(mcap) and mcap > 0) else None

    h = {}
    for key in ("pro", "pub", "fii", "dii"):
        pts = series(r, F.get(key, []), inp["scut"])
        if inp.get("shq"):
            pts = pts[-inp["shq"]:]                       # same-method: same window at both dates
        v = holding(pts)
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
        "cfop": list(sf["cfop"]) if sf["cfop"] else None,
        "q5": num(sf["q5"]), "debitda": num(sf["debitda"]),
        "de": num(sf["de"]), "pup": list(sf["pup"]) if sf["pup"] else None, "roa": num(sf["roa"]), "gnpa": num(sf["gnpa"]), "nnpa": num(sf["nnpa"]), "pcr": num(sf["pcr"]),
        "pta": num(pta, 2), "pfa": num(pfa, 2), "invp": num(invp), "taFy": a.get("fy"),
        "tr": inp.get("trend"),
        "roceSrc": inp.get("roceSrc"),
    }


def build_all(df, asof, prices=None, bench=None):
    """now   = today, Screener's own figures (default view)
       smnow = today, SAME-METHOD engine (LTP from Angel if available, else CSV CMP)
       backs = one snapshot per back period (3M … 1.5Y), SAME-METHOD engine (price then from Angel, if available)
       So "same method today" vs "1Y back" differ only in their inputs, never in their formulas."""
    F = column_maps(df)
    prices = prices or {}
    tol = pd.Timedelta(days=PRICE_TOL_DAYS)

    def near(d, target):                                  # price date within ±PRICE_TOL_DAYS of the date it stands for
        d = pd.to_datetime(d, errors="coerce")
        return pd.notna(d) and abs(d - target) <= tol
    n_farL = 0
    win0 = price_window_start(prices)
    now, smnow, ltps = [], [], {}
    for r in df.to_dict("records"):                       # plain dicts: much faster than pandas rows
        fin = bool(FIN_RE.search(str(r["INDUSTRY"])))
        sym = str(r["SYMBOL"]).upper()
        px = prices.get(sym, {})
        g = lambda k: px.get(k) if ok(px.get(k)) else None
        rec0 = build_record(r, F, inputs_now(r, F, asof, fin), fin)
        rec0["mom"] = momentum(px, "NOW") if history_ok(px, asof, 1, win0) else None      # < 1 year of prices (recent IPO): no momentum score
        lf_t = pd.to_datetime(px.get("LISTED_FROM"), errors="coerce")
        lf0 = lf_t.strftime("%Y-%m-%d") if pd.notna(lf_t) else ""     # first price day we have (the PF Builder leaves out recent IPOs with it)
        if lf0:
            rec0["lf"] = lf0
        qx = quality_extras(r, F, asof)
        rec0.update(qx)
        now.append(rec0)

        ltp_ok = g("LTP") and near(px.get("LTP_DATE"), asof)
        n_farL += bool(g("LTP") and not ltp_ok)
        ltp, ltp_src = (g("LTP"), "angel") if ltp_ok else (r["CMP"], "csv")   # too far from the data date → CSV CMP
        rec_n = build_record(r, F, inputs_sm(r, F, asof, fin, ltp, g("HIGH_52W_NOW") or r["HIGH_52W"]), fin)
        rec_n["pxSrc"] = ltp_src
        rec_n["mom"] = rec0["mom"]
        if lf0:
            rec_n["lf"] = lf0
        rec_n.update(qx)
        smnow.append(rec_n)
        ltps[sym] = (ltp, ltp_src)

    if prices and n_farL:
        print("Prices: %d LTPs too far from %s → CSV CMP used" % (n_farL, asof.date()))
    backs = {k: build_back(df, F, asof, k, prices, bench, ltps) for k in PERIODS}
    return now, smnow, backs


def history_ok(px, judged, years, window_start):
    """A momentum score needs real history: the stock must have been listed at least `years` whole years before the date it is judged on
    (no recent IPOs). LISTED_FROM = first price day fetched; prices are fetched from a fixed start (window_start), so a stock first seen
    within a week of that start is older than that and passes. No LISTED_FROM = unknown, passes (it has no prices anyway)."""
    lf = pd.to_datetime(px.get("LISTED_FROM"), errors="coerce")
    if pd.isna(lf) or window_start is None or lf <= window_start + pd.Timedelta(days=7):
        return True
    return lf <= pd.Timestamp(judged) - pd.Timedelta(days=365 * years)


def price_window_start(prices):
    v = [pd.to_datetime(p.get("LISTED_FROM"), errors="coerce") for p in prices.values()]
    v = [x for x in v if pd.notna(x)]
    return min(v) if v else None


def momentum(px, key):
    """[3M, 6M, 12-1M] % returns as of today (key NOW) or a back date, from prices.csv; None if not fetched yet."""
    v = [px.get("MOM_%s_%s" % (key, k)) for k in ("R3", "R6", "R12")]
    v = [round(float(x), 2) if ok(x) else None for x in v]
    return v if any(x is not None for x in v) else None


def build_back(df, F, asof, key, prices, bench, ltps):
    """One back snapshot: every company as it stood (data date − PERIODS[key] days), SAME-METHOD engine,
    priced with prices.csv's PRICE_<key> columns, plus how each business and price did since."""
    label, days = PERIODS[key]
    back = asof - pd.Timedelta(days=days)
    tol = pd.Timedelta(days=PRICE_TOL_DAYS)
    near = lambda d, t: pd.notna(pd.to_datetime(d, errors="coerce")) and abs(pd.to_datetime(d) - t) <= tol
    then, n_far = [], 0
    win_b = price_window_start(prices)
    for r in df.to_dict("records"):                       # plain dicts: much faster than pandas rows
        fin = bool(FIN_RE.search(str(r["INDUSTRY"])))
        sym = str(r["SYMBOL"]).upper()
        px = prices.get(sym, {})
        g = lambda k: px.get(k) if ok(px.get(k)) else None
        lf = pd.to_datetime(px.get("LISTED_FROM"), errors="coerce")
        if (pd.notna(lf) and lf > back) or (key == "1Y" and pd.isna(lf) and str(px.get("STATUS", "")).startswith("NOT LISTED")):
            continue                                      # listed after the back date: not in that universe
        p1 = g("PRICE_%s_ADJ" % key)
        if p1 and not near(px.get("PRICE_%s_DATE" % key), back):
            p1, n_far = None, n_far + 1                   # price from a different week → not used
        rec = build_record(r, F, inputs_sm(r, F, back, fin, p1, g("HIGH_52W_%s" % key) if p1 else None), fin)
        if rec["mcap"] is None:
            rec["mcap"] = num(r["MARKET_CAP_CR"], 0)      # no price then: today's size, only for the Mcap filters
            rec["mcapToday"] = True
        yrs = max(1, -(-days // 365))                     # whole years of history needed before the back date (1Y back → 1, 1.5Y back → 2)
        rec["mom"] = momentum(px, key) if (p1 and history_ok(px, back, yrs, win_b)) else None   # momentum as of the back date (prices up to then only); recent IPOs get none
        if lf is not None and pd.notna(lf):
            rec["lf"] = lf.strftime("%Y-%m-%d")
        rec.update(quality_extras(r, F, back))             # profit gate + growth checks, as known on that date
        a, b = inputs_fund(r, F, back, fin, with_trend=False)["fund"], inputs_fund(r, F, asof, fin, with_trend=False)["fund"]
        patG, patL = growth_pct(a["pat"], b["pat"])
        revG, _ = growth_pct(a["rev"], b["rev"])
        d = lambda k: round(b[k] - a[k], 2) if (a[k] is not None and b[k] is not None) else None
        ltp, src = ltps.get(sym, (None, None))
        ret = round((ltp / p1 - 1) * 100, 2) if (p1 and ltp and src == "angel") else None   # same source both ends
        rec["since"] = {"patG": patG, "patL": patL, "revG": revG, "roeD": d("roe"), "roceD": d("roce"),
                        "opmD": d("opm"), "proD": d("pro"), "qThen": a["q"], "qNow": b["q"],
                        "fyThen": a["fy"], "fyNow": b["fy"], "ret": ret}
        then.append(rec)
    n_px = sum(1 for t in then if t["cmp"] is not None)
    if prices:
        print("Prices %-5s back = %s: price for %d of %d stocks (±%d days); %d skipped as too far"
              % (label, back.date(), n_px, len(then), PRICE_TOL_DAYS, n_far))
    # "has prices" is judged on the 3 lists only: the Others list is priced later (or not at all for some),
    # and counting it once switched off every back date's prices (and the stock popup's sparkline with it)
    main = [t for t in then if any(u != OTHERS_LABEL for u in (t.get("uni") or []))] or then
    n_main = sum(1 for t in main if t["cmp"] is not None)
    meta = {"key": key, "label": label, "date": back.strftime("%d %b %Y"), "days": days,
            "hasPrice": n_main >= 0.5 * max(len(main), 1), "nPrice": n_px,
            "priceDate": None, "priceDateThen": None, "bench": None}
    pick = lambda col: (lambda v: max(set(v), key=v.count) if v else None)([p.get(col) for p in prices.values() if ok(p.get(col))])
    meta["priceDate"], meta["priceDateThen"] = pick("LTP_DATE"), pick("PRICE_%s_DATE" % key)
    if bench and ok(bench.get("LTP")) and ok(bench.get("PRICE_%s_ADJ" % key)):
        meta["bench"] = {"ret": round((bench["LTP"] / bench["PRICE_%s_ADJ" % key] - 1) * 100, 2),
                         "from": bench.get("PRICE_%s_DATE" % key), "to": bench.get("LTP_DATE")}
    return dict(meta, stocks=then)


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
<html lang="en"__AUTH_ATTR__>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HNImanshu — Factor Screener</title>
<script>
/* web fonts never block the page; skipped on 2G / data saver (the phone's own fonts are used instead) */
(function(){ var c = navigator.connection || {}; if (c.saveData || /2g/.test(c.effectiveType || '')) return;
  var l = document.createElement('link'); l.rel = 'stylesheet';
  l.href = 'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=DM+Sans:wght@300;400;500;600;700&display=swap';
  document.head.appendChild(l); })();
</script>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cstyle%3Erect{fill:%23141414}text{fill:%23d4b47a}@media (prefers-color-scheme:dark){rect{fill:%23f1e8d8}text{fill:%234c2e0a}}%3C/style%3E%3Crect width='32' height='32' rx='7'/%3E%3Ctext x='16' y='23' font-family='Georgia,serif' font-size='18' font-weight='bold' text-anchor='middle'%3EH%3C/text%3E%3C/svg%3E">
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

/* filter bar: Screens chips + ONE Filters button; all fields, Scoring and Reset live in its dropdown
   (laptop: drops down under the button; phone: floating window over the page) */
.fbox{position:relative;margin-bottom:10px}
.fbox .t{font:600 10.5px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:1px;white-space:nowrap}
.fbar{display:flex;align-items:center;gap:10px}
.pchips{display:flex;flex-wrap:wrap;gap:6px;min-width:0;flex:1}
.preset{font:600 11.5px var(--mono);height:28px;padding:0 11px;border:1px solid var(--line);border-radius:999px;background:transparent;color:var(--mute);cursor:pointer;letter-spacing:.2px;white-space:nowrap;flex-shrink:0}
.preset:hover{border-color:var(--brass);color:var(--ink)}
.preset.on{background:var(--brass);border-color:var(--brass);color:var(--bg)}
.preset-custom{font:600 11px var(--mono);color:var(--faint);line-height:28px;padding:0 4px;font-style:italic}
.btn.fbtn{flex-shrink:0;align-self:flex-start;border-color:var(--brass);color:var(--brass);background:transparent}
.fbox.dopen .btn.fbtn,.btn.fbtn:hover{background:var(--brass);color:var(--bg)}
.fbox.dopen .btn.fbtn .cnt{background:var(--bg);color:var(--brass)}
.fdrop{display:none;position:absolute;right:0;top:calc(100% + 8px);z-index:80;width:min(920px,calc(100vw - 48px));max-height:min(72vh,640px);overflow:auto;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px 16px;
  border-color:rgba(212,180,122,.45);box-shadow:0 0 0 1px rgba(212,180,122,.18),0 14px 34px rgba(0,0,0,.55),0 0 22px rgba(212,180,122,.10)}   /* lifted + faint brass glow */
:root[data-theme=day] .fdrop{border-color:rgba(76,46,10,.45);box-shadow:0 0 0 1px rgba(76,46,10,.15),0 14px 32px rgba(40,30,18,.28)}
.fbox.dopen .fdrop{display:block}
.fback{display:none}
.fdhead{display:flex;align-items:center;justify-content:space-between;gap:8px 12px;flex-wrap:wrap;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--line);position:sticky;top:-12px;background:var(--panel);z-index:2;padding-top:2px}
.fbtns{display:flex;gap:6px;flex-wrap:wrap}
.fbtns .btn.save-on{border-color:var(--brass);background:var(--brass);color:var(--bg)}
.fbtns .btn:disabled{opacity:.45;cursor:default}
.btn.done{border-color:var(--brass);color:var(--brass)}
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:12px 14px;align-items:end}
.fld{display:flex;flex-direction:column;gap:5px;min-width:0;margin:0}
.fld > span{font:500 10.5px var(--mono);color:var(--faint);letter-spacing:.3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fld input[type=number]{width:100%;height:32px}
.fld .fsel{width:100%}
.fld .fsel-btn.boxed{width:100%;height:32px;justify-content:space-between}
.fld.chk{flex-direction:row;align-items:center;gap:8px;min-height:32px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--mute);font-size:12.5px;line-height:1.25;cursor:pointer;user-select:none}
.fld.chk > span{font:inherit;color:inherit;letter-spacing:0;white-space:normal}
.fld.chk input{flex-shrink:0;margin:0}
.fld.chk:has(input:checked){border-color:var(--brass);color:var(--ink);background:var(--brassbg)}
.fld.chk:hover{border-color:var(--brass)}
.btn.ghost{background:transparent;height:30px;font-size:11.5px}
.btn.ghost.open{border-color:var(--brass);color:var(--brass)}
.btn .car{display:inline-block;transition:transform .15s}
.btn.open .car{transform:rotate(180deg)}
.btn .cnt{background:var(--brass);color:var(--bg);border-radius:999px;padding:1px 6px;font-size:10px}
.fpanel{display:none;margin:0 0 14px;padding-bottom:14px;border-bottom:1px dashed var(--line)}
.fpanel.open{display:block}
.fsub{font:600 10px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:1px;margin:16px 0 10px;opacity:.85}
.fpanel .fsub{margin-top:0}
.fpanel .bar{margin:0 0 12px}
.fpanel .fgrid + .btn{margin-top:12px}

/* day / night toggle: same control as the screener (moon - track - sun), same storage key */
#theme-toggle{display:flex;align-items:center;gap:6px;flex-shrink:0;cursor:pointer;padding:4px 2px;margin-left:4px}
.toggle-icon{display:flex;align-items:center;line-height:1;color:var(--faint)}
body.day .toggle-icon{color:var(--mute)}
.toggle-track{width:36px;height:20px;background:var(--line);border-radius:10px;position:relative;cursor:pointer;transition:background .3s ease;border:none;flex-shrink:0;padding:0}
body.day .toggle-track{background:var(--accent)}
.toggle-thumb{width:14px;height:14px;background:var(--panel2);border-radius:50%;position:absolute;top:3px;left:3px;transition:transform .25s cubic-bezier(.4,0,.2,1);pointer-events:none}
body.day .toggle-thumb{transform:translateX(16px)}

/* table toolbar: search left, Download CSV right (same outline style as the screener's Export CSV) */
.tbar{justify-content:space-between;position:relative;z-index:6}
.tacts{display:inline-flex;gap:6px;margin-left:auto;flex-shrink:0}
.btn.icon{width:34px;padding:0;justify-content:center;border-color:var(--brass);color:var(--brass);background:transparent;position:relative}
.btn.icon:hover{background:var(--brass);color:var(--bg)}
.btn.icon .tick{display:none}
.btn.icon.ok .ico{display:none}
.btn.icon.ok .tick{display:block;color:var(--good);animation:tickfade 1.4s ease forwards}
.btn.icon.ok,.btn.icon.ok:hover{background:transparent;border-color:var(--good)}
@keyframes tickfade{0%{opacity:0;transform:scale(.6)}15%{opacity:1;transform:scale(1.1)}25%{transform:scale(1)}70%{opacity:1}100%{opacity:0}}
.btn.icon.bad{border-color:var(--bad);color:var(--bad)}
@media (max-width:700px){.tbar{flex-wrap:nowrap}.tbar input[type=search]{flex:1;min-width:0}}

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
.pop .bt{display:block;margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
.pop .bt b{display:inline;font:inherit;font-weight:700;color:var(--warn)}
.pop a{color:var(--brass)}

@media (max-width:700px){
  .lg{display:none} .pop{left:0;right:auto;width:calc(100vw - 24px)}
  header.top{position:relative}
  #theme-toggle{position:absolute;top:14px;right:12px;margin:0}     /* top-right corner, like the screener */
  .pchips{flex-wrap:nowrap;overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch;scrollbar-width:none}   /* one swipeable row */
  .pchips::-webkit-scrollbar{display:none}
  .btn.fbtn{padding:0 10px}
  /* phone: Filters is a bottom sheet like the other menus (see sheetScroll): the .fdrop is a see-through full-screen
     scroller with an empty screen-high gap on top, .fdin is the sheet. Opens at half; scrolling lifts it to full
     height, then the body scrolls under the sticky Filters / Done bar; pulling down closes it. */
  .fback{position:fixed;inset:0;z-index:300;background:rgba(8,8,10,.6)}
  .fbox.dopen .fback{display:block}
  .fdrop,:root[data-theme=day] .fdrop{position:fixed;inset:0;top:0!important;width:auto;max-height:none;z-index:301;padding:0;border:0;border-radius:0;
         background:transparent;box-shadow:none;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:none}
  .fdrop::-webkit-scrollbar{display:none}
  .fdrop::before{content:"";display:block;height:100vh;height:100dvh}       /* the gap: tap it to close */
  .fdin{background:var(--panel);border:1px solid rgba(212,180,122,.45);border-bottom:0;border-radius:16px 16px 0 0;padding:0 16px 22px;
        box-shadow:0 -10px 30px rgba(0,0,0,.35);animation:fdUp .22s cubic-bezier(.2,.8,.2,1)}
  :root[data-theme=day] .fdin{border-color:rgba(76,46,10,.45)}
  .fdin::before{content:"";display:block;width:38px;height:4px;border-radius:2px;background:var(--line);margin:0 auto;position:relative;top:8px}
  .fdrop .fdhead{top:0;padding-top:16px}
  @keyframes fdUp{from{transform:translateY(40%)}}
  body.fmodal{overflow:hidden}
  .fgrid{grid-template-columns:1fr 1fr;gap:10px}
  .fld > span{font-size:10px}
  .fbtns .btn{padding:0 9px}
}

/* Portfolio tab: compact summary, then holdings (left) | closed + transactions (right), each panel scrolls */
.pf-top{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.pf-acts{display:flex;gap:6px;flex-wrap:wrap}
.pf-tg{border-color:var(--brass);color:var(--brass);background:transparent}
.pf-tg:hover{background:var(--brassbg)}
.pf-err{margin:0 0 8px;font-size:12.5px;color:var(--bad)}
.pf-err:empty{display:none}
.pf-note{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--mute)}
.pf-sum{display:flex;align-items:stretch;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:0 0 14px;overflow-x:auto;scrollbar-width:none}
.pf-sum::-webkit-scrollbar{display:none}
.pf-k{flex:1 0 auto;padding:8px 14px;display:flex;flex-direction:column;gap:1px;min-width:0;border-left:1px solid var(--line)}
.pf-k:first-child{border-left:0}
.pf-k > span{font:600 9.5px var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.7px}   /* label only, not the coloured number inside <b> */
.pf-k b{font:700 14.5px var(--mono);color:var(--ink);white-space:nowrap}
.pf-k small{font-size:11px;color:var(--faint);white-space:nowrap}
/* Net gain: green tint + soft glow when up, red when down */
.pf-k.gain{border-radius:9px 0 0 9px}
.pf-k.gain b{font-size:16.5px}                   /* Net gain: no tint, plain text; only the ▲ / ▼ is coloured */
.pf-ar{font-style:normal;font-size:.68em;margin-right:5px;vertical-align:1px}
.pf-ar.up{color:var(--good)} .pf-ar.dn{color:var(--bad)}
.pf-grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(0,1fr);gap:16px;align-items:start}
.pf-left,.pf-right{display:flex;flex-direction:column;gap:14px;min-width:0}
.pf-panel .tbar{margin-bottom:6px;gap:10px}
.pf-panel .tbar input[type=search]{width:200px!important}
.pf-left .wrap{max-height:max(340px,calc(100vh - 300px))}            /* each panel scrolls on its own */
.pf-right .wrap{max-height:max(200px,calc(50vh - 175px))}
.pf-h{font:700 12.5px var(--mono);color:var(--brass);margin:0;letter-spacing:.4px;white-space:nowrap;margin-right:auto}
.pf-zsort{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font:600 11px var(--mono);color:var(--mute);letter-spacing:0}
.pf-zsort .fsel-menu{left:auto;right:-8px}
.pf-zsort .fsel-btn{font:600 12px var(--mono)}
.pf-zsort .fsel-opt{font:600 12.5px var(--sans);letter-spacing:0}
#pfZDir{background:var(--panel2);color:var(--brass);border:1px solid var(--line);border-radius:8px;width:30px;height:28px;font:700 14px var(--mono);cursor:pointer}
.pf-h > span{font:600 11px var(--mono);color:var(--faint);margin-left:6px;padding:1px 7px;border:1px solid var(--line);border-radius:99px}
.pf-panel > .pf-h{margin-bottom:6px}
.pf-co{font-size:11px;color:var(--faint);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pf-panel td small{font-size:11px;margin-left:4px}
.pf-panel td.na[colspan]{white-space:normal;line-height:1.5}   /* empty-table hints wrap instead of running off-screen */
.pf-ico{border:0;background:transparent;color:var(--faint);font-size:15px;line-height:1;cursor:pointer;padding:3px 6px;border-radius:5px}
.pf-ico:hover{color:var(--brass);background:var(--brassbg)}
.pf-ico.del{font-size:18px}
.pf-ico.del:hover{color:var(--bad);background:var(--badbg)}
.pf-acts2{display:inline-flex;gap:2px}
.pf-lock{display:inline-block;padding:3px 8px;font-size:12px;opacity:.6;cursor:help}
.pf-rm{border:1px solid var(--line);background:transparent;color:var(--mute);font:600 11px var(--mono);padding:3px 9px;border-radius:6px;cursor:pointer;white-space:nowrap}
.pf-rm:hover{border-color:var(--bad);color:var(--bad);background:var(--badbg)}
.pf-rm.all{margin-right:auto}
.btn.primary.danger{background:var(--bad);border-color:var(--bad)}
.btn.primary:disabled{opacity:.45;cursor:not-allowed}
.seg button:disabled{opacity:.35;cursor:not-allowed}
.pf-t{font:600 10.5px var(--mono);padding:2px 6px;border-radius:5px;border:1px solid var(--line);white-space:nowrap}
.pf-t.buy{color:var(--good);border-color:var(--good)}
.pf-t.sell{color:var(--bad);border-color:var(--bad)}
.pf-t.in,.pf-t.out{color:var(--brass);border-color:var(--brass)}
/* header: Portfolio bag, just left of the user's initials */
.btn.hbag{gap:6px;border-color:var(--line);color:var(--ink)}
/* Portfolio page: only ‹ Value, Weekly, Portfolio, avatar and day/night stay in the header */
#pfBackV{display:none}
body.pf-mode #pfBackV{display:inline-flex}
body.pf-mode #backBtn,body.pf-mode .global > .gsep,body.pf-mode #uniChip,body.pf-mode .global > .chip.strike,body.pf-mode #histGrp,
body.pf-mode #saveHtml,body.pf-mode #nav,body.pf-mode #meta{display:none!important}
.btn.hbag[hidden]{display:none}
.btn.hbag:hover,.btn.hbag.on{border-color:var(--brass);color:var(--brass);background:var(--brassbg)}
@media (max-width:700px){.btn.hbag{position:absolute;top:13px;right:calc(var(--au-right,104px) + 80px);width:32px;height:32px;padding:0;justify-content:center;border-radius:50%}
  .btn.hbag.bld{right:calc(var(--au-right,104px) + 120px)}}
/* ---------- phone header, de-cluttered ----------
   top-right: Portfolio · Telegram · avatar · day/night.  Then three rows with thin lines between them:
   ← Screener  /  List · PSU · Semi PSU  /  Historical ······ period.  Tabs and screens live in two menu buttons (#mnav). */
#mnav{display:none}
@media (max-width:700px){
  #weeklyBtn{position:absolute;top:13px;right:calc(var(--au-right,104px) + 40px);width:32px;height:32px;padding:0;gap:0;font-size:0;justify-content:center;border-radius:50%}
  #weeklyBtn svg{width:17px;height:17px}
  header.top .global{display:grid;grid-template-columns:1fr auto auto;grid-template-areas:"back back back" "l1 l1 l1" "uni psu semi" "l2 l2 l2" "hist hist hist";
          column-gap:8px;row-gap:9px;align-items:center;width:100%}    /* "header.top" outranks the older flex rule further down */
  header.top .global::before,header.top .global::after{content:"";height:1px;background:var(--line)}
  header.top .global::before{grid-area:l1} header.top .global::after{grid-area:l2}
  header.top .global > .gsep{display:none!important}
  header.top #backBtn,header.top #pfBackV{grid-area:back;justify-self:start}
  header.top #uniChip{grid-area:uni;justify-self:start}
  header.top .global > .chip:has(#hidePSU){grid-area:psu} header.top .global > .chip:has(#hideSemi){grid-area:semi}
  header.top #histGrp{grid-area:hist;justify-content:space-between;width:100%}
  body.pf-mode header.top .global::before,body.pf-mode header.top .global::after{display:none}
  body.pf-mode header.top .global{grid-template-areas:"back back back";row-gap:0}   /* Portfolio: only the return button row */
  #nav{display:none}                                                   /* tabs → the menu button */
  #mnav{display:flex;gap:8px;padding:8px 10px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
  body.pf-mode #mnav{display:none}
  .mnav-b{display:inline-flex;align-items:center;gap:8px;height:34px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg);
          color:var(--ink);font:600 12.5px var(--mono);cursor:pointer;min-width:0}
  .mnav-b span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mnav-b i{font-style:normal;color:var(--faint);font-size:11px}
  #mnavTab{color:var(--brass);border-color:var(--brass)}
  #mnavPre{flex:1;justify-content:space-between}
  .fbox .fbar{display:none}                                            /* screens → the second menu; Filters → the round button */
}
/* the menu overlay (phone): a list of choices */
.mlist{display:flex;flex-direction:column;gap:6px;margin-top:4px}
.mlist button{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;text-align:left;padding:12px 14px;border:1px solid var(--line);
  border-radius:10px;background:var(--bg);color:var(--ink);font:600 13.5px var(--sans);cursor:pointer}
.mlist button small{display:block;font:400 11.5px var(--sans);color:var(--faint);margin-top:2px}
.mlist button.on{border-color:var(--brass);background:var(--brassbg);color:var(--brass)}
.mlist button.on::after{content:"✓";font-weight:700}
.ind{vertical-align:middle}
/* header: Weekly (Telegram) */
.btn.hweek{gap:6px;color:var(--ink)}
.btn.hweek:hover{border-color:#29a9eb;background:rgba(41,169,235,.12)}
@media (max-width:700px){.btn.hweek{padding:0 9px}}
/* icon-only buttons (charges, refresh); refresh spins while live prices are being fetched */
.pf-acts .btn.ico{width:38px;padding:0;justify-content:center}
.pf-acts .btn.ico.ltp{width:auto;padding:0 10px;gap:6px}                 /* ⟳ + the time of the latest prices */
.pf-asof{font:600 11px var(--mono);color:var(--mute)}
/* NIFTY 50 / BANK NIFTY on the Portfolio page (from the same live-price job) */
.pf-idx{display:flex;gap:6px 20px;flex-wrap:wrap;align-items:baseline;font:600 13px var(--mono);color:var(--ink)}
.pf-ix{white-space:nowrap}
.pf-ix b{font:600 10.5px var(--mono);color:var(--faint);letter-spacing:.4px;margin-right:6px}
.pf-ix em{font-style:normal;font-size:12px;margin-left:6px}
.pf-acts .btn.ico svg{display:block}
.pf-acts .btn.spin svg{animation:pfSpin 1s linear infinite}
@keyframes pfSpin{to{transform:rotate(360deg)}}
/* Day gain tile */
/* news panel */
.pf-news .pf-h{margin-bottom:6px}
.pf-nl{max-height:max(160px,calc(32vh - 60px));overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
.pf-nl .na{padding:12px 14px;margin:0}
.pf-ni{display:grid;grid-template-columns:auto auto 1fr;gap:4px 10px;align-items:baseline;padding:8px 12px;border-bottom:1px solid var(--line);color:var(--ink);text-decoration:none}
.pf-ni:last-child{border-bottom:0}
.pf-ni:hover{background:var(--hover);text-decoration:none}
.pf-ni .d{font:500 11px var(--mono);color:var(--faint);white-space:nowrap}
.pf-ni b{font:700 11.5px var(--mono);color:var(--brass)}
.pf-ni .t{font-size:12.5px;line-height:1.4;min-width:0}
/* holdings search: wide, right after the title */
.pf-panel .tbar .pf-h{margin-right:0}
.pf-panel .tbar input[type=search]{width:300px!important;margin-right:auto}
@media (max-width:700px){.pf-panel .tbar input[type=search]{width:auto!important;flex:1;min-width:0}}
/* symbol dropdown (works the same on phones; no keyboard-bar suggestions) */
.sp-wrap{position:relative}
.wip-box.ov{overflow:visible;max-height:none}   /* forms with the symbol dropdown: let it hang below the box */
.sp-list{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:5;max-height:260px;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:var(--drop-shadow);padding:4px}
.sp-it{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:baseline;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);padding:8px 10px;border-radius:6px;cursor:pointer;font:inherit}
.sp-it b{font:700 12.5px var(--mono)}
.sp-it span{font-size:12px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sp-it i{font:600 10px var(--mono);font-style:normal;color:var(--brass)}
.sp-it.on,.sp-it:hover{background:var(--brassbg)}
.sp-it.add b{color:var(--brass)}
.pf-fix{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:12.5px;color:var(--warn);border:1px dashed var(--warn);border-radius:8px;padding:8px 10px}
.pf-fix[hidden],.pf-name[hidden]{display:none}
/* bag left of every industry name: opens Buy / Sell; brass when you hold the stock */
.bag{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;margin:-3px 6px -3px -2px;vertical-align:middle;border:1px solid transparent;border-radius:6px;background:transparent;color:var(--faint);cursor:pointer;padding:0;opacity:.75}
.bag:hover{opacity:1;color:var(--brass);border-color:var(--line);background:var(--brassbg)}
.bag.held{opacity:1;color:var(--brass)}
.bag.held svg{fill:var(--brassbg)}
.toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);z-index:400;opacity:0;transition:opacity .25s,transform .25s;
  background:var(--ink);color:var(--bg);font:600 12.5px var(--sans);padding:10px 16px;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.3);width:max-content;max-width:calc(100vw - 32px);text-align:center;pointer-events:none}   /* sized to its text: one line, centred */
.toast.on{opacity:1;transform:translate(-50%,0)}
/* forms */
.pf-form{display:flex;flex-direction:column;gap:10px;margin-top:6px}
.pf-form .fld{display:flex;flex-direction:column;gap:4px;min-width:0}
.pf-form .fld span{font:500 11px var(--mono);color:var(--faint)}
.pf-form .fld span i{font-style:normal;color:var(--brass)}
.pf-form input{height:36px;padding:0 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:14px var(--sans);width:100%;box-sizing:border-box}
.pf-form input[name=sym]{text-transform:uppercase}
.pf-form input::placeholder{text-transform:none;color:var(--faint)}
.pf-form input:focus{outline:none;border-color:var(--brass)}
.pf-form input.bad,.pf-form input.bad:focus{border-color:var(--bad);background:var(--badbg);color:var(--bad)}
.pf-form .held.bad{color:var(--bad)}
.pf-form .row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.pf-form .btn.primary{height:38px;justify-content:center}
.pf-calc{margin:0;font-size:12.5px;color:var(--mute);line-height:1.5}
.pf-calc:empty{display:none}
.pf-btns{display:flex;gap:8px;justify-content:flex-end}
.seg{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:3px;border:1px solid var(--line);border-radius:9px;background:var(--bg)}
.seg button{border:0;background:transparent;color:var(--mute);font:600 12.5px var(--mono);padding:8px 0;border-radius:6px;cursor:pointer}
.seg button.on[data-t=buy],.seg button.on[data-t=in]{background:var(--good);color:var(--bg)}
.seg button.on[data-t=sell],.seg button.on[data-t=out]{background:var(--bad);color:var(--bg)}
.wip-back{position:fixed;inset:0;z-index:300;display:none;align-items:center;justify-content:center;padding:16px;background:rgba(0,0,0,.42)}
/* any popup open: the page behind is blurred ONCE as a still picture (cheap), and doesn't scroll.
   (A live backdrop blur re-draws the whole page on every scroll / hover inside the popup: that was the lag.) */
html.pop-open,html.pop-open body{overflow:hidden}
.wip-back{background:rgba(8,8,10,.72)}            /* deep dim instead of blur: any blur re-draws the page on every scroll and hover */
.wip-box{overscroll-behavior:contain;scrollbar-gutter:stable}   /* the scrollbar's space is always kept: × , chart and text never shift */
.wip-back.open{display:flex}
.au-back.open ~ .wip-back{z-index:330}                 /* a stock card opened from an admin overlay (Users → View portfolio) sits on top of it */
/* PF Builder shows this page's stock card over itself (this page runs hidden inside the builder): only the card, see-through around it */
html.pb-pop,html.pb-pop body{background:transparent!important}
html.pb-pop body>*:not(.wip-back):not(.au-back):not(.toast){visibility:hidden!important}
.wip-box{position:relative;width:min(420px,100%);max-height:calc(100vh - 32px);overflow:auto;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:14px;padding:20px 22px;box-shadow:0 18px 50px rgba(0,0,0,.35);box-sizing:border-box}
.wip-box h3{margin:0 0 8px;font:700 15px var(--mono);color:var(--brass)}
.wip-box > p{margin:0;color:var(--mute);font-size:13.5px;line-height:1.5}
.wip-x{position:absolute;top:10px;right:10px;width:30px;height:30px;border:0;background:transparent;color:var(--faint);font-size:20px;line-height:1;cursor:pointer;border-radius:6px}
.wip-x:hover{color:var(--ink);background:var(--hover)}
/* stock overlay (tap a symbol) */
.wip-box.no-box{width:min(560px,100%)}
/* stock sheet: soft dim, fades in; on phones it slides up from the bottom */
.wip-back.sheet{background:rgba(8,8,10,.6)}
/* status history sparkline (top-right of the stock popup) + its checklist tooltip */
.vh{position:absolute;top:14px;right:50px;width:176px;text-align:right;z-index:2}
.vh svg{display:block;overflow:visible;margin-left:auto}
.vh .hit{cursor:pointer}
.vh-g{stroke:var(--line);stroke-width:1;stroke-dasharray:2 3}
body.night{--pxline:#ece6da} body.day{--pxline:#36332e}           /* price line: off-white at night, charcoal by day */
.vh-px{fill:none;stroke:var(--pxline);stroke-width:1.2;stroke-linejoin:round;stroke-linecap:round}
.vh-pxk{color:var(--pxline)}
.vh-pxv{margin-left:auto;font:600 12px var(--mono);color:var(--pxline)}
.vh-cap{display:block;font:500 9.5px var(--mono);color:var(--faint);margin-top:2px;white-space:nowrap}
.vh-load{border:1px solid var(--line);background:transparent;color:var(--faint);font:600 10.5px var(--mono);padding:4px 8px;border-radius:6px;cursor:pointer}
.vh-tip{position:fixed;width:300px;max-height:calc(100vh - 24px);overflow:auto;text-align:left;background:var(--panel2,var(--panel));border:1px solid var(--line);
  border-radius:10px;padding:8px 12px 10px;box-shadow:0 6px 16px rgba(0,0,0,.35);z-index:5}
.vh-tip .vd-c{font-size:12px;padding:2px 0;break-inside:avoid}
.vh-tip.two{column-count:2;column-gap:18px}                       /* wide screens: whole checklist in two columns, no scrolling */
.vh-tip.two .vh-h,.vh-tip.two .vd-gate{column-span:all}
.vh-tip .vd-c small{font-size:10.5px}
.vh-h{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12.5px;padding-bottom:6px;margin-bottom:4px;border-bottom:1px solid var(--line)}
.vh-h small{font:500 10.5px var(--mono);color:var(--faint)}
.wip-box.no-box h3,.wip-box.no-box .no-name{padding-right:200px}
@media (max-width:700px){
  .vh{top:14px;right:46px;width:140px}                          /* phone: beside the symbol, clear of the name line */
  .vh svg{width:140px;height:34px}
  .vh-cap{display:none}
  .wip-box.no-box h3{padding-right:150px;min-height:34px}
  .wip-box.no-box .no-name{padding-right:0}
}
.wip-back.sheet .wip-box{animation:noRise .16s ease-out}
@keyframes noFade{from{opacity:0}} @keyframes noRise{from{transform:translateY(14px);opacity:.6}}
.no-g{font:600 9.5px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:.7px;margin:4px 0 6px;padding-top:8px;border-top:1px solid var(--line)}
.no-kv small{font:500 10.5px var(--mono);color:var(--faint);margin-left:4px}
.no-flags{margin:-2px 0 12px!important;font-size:12px!important;color:var(--faint)!important}
/* checklist light: a small sideways traffic light (red · amber · green); only the current lamp glows */
.tlw{display:inline-flex;align-items:center;gap:8px;vertical-align:middle}
.tl{display:inline-flex;gap:3px;padding:3px 4px;border-radius:7px;background:linear-gradient(#26262a,#0c0c0e);box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 1px 2px rgba(0,0,0,.4)}
.tl i{width:8px;height:8px;border-radius:50%;background:#2c2c30;box-shadow:inset 0 1px 2px rgba(0,0,0,.6)}
.tl-red .r{background:radial-gradient(circle at 35% 30%,#ff9b8f,#e5413a 55%,#a51d17);box-shadow:0 0 6px 1px rgba(229,65,58,.75)}
.tl-amber .a{background:radial-gradient(circle at 35% 30%,#ffe08a,#f0a020 55%,#b86c00);box-shadow:0 0 6px 1px rgba(240,160,32,.75)}
.tl-green .g{background:radial-gradient(circle at 35% 30%,#a6f5a0,#3ec65a 55%,#1d8a32);box-shadow:0 0 6px 1px rgba(62,198,90,.75)}
.tlw.big .tl{gap:5px;padding:5px 7px;border-radius:10px}
.tlw.big .tl i{width:14px;height:14px}
.tlw.big .tl-red .r,.tlw.big .tl-amber .a,.tlw.big .tl-green .g{box-shadow:0 0 10px 2px currentColor}
.tlw.big .tl-red{color:rgba(229,65,58,.65)} .tlw.big .tl-amber{color:rgba(240,160,32,.65)} .tlw.big .tl-green{color:rgba(62,198,90,.65)}
.tlw small{font:600 11.5px var(--mono);color:var(--mute)}
.vd-disc{margin:8px 0 0!important;font-size:10.5px!important;color:var(--faint)!important;line-height:1.45}
.vd{display:inline-block;font:700 10px/1 var(--mono);letter-spacing:.5px;text-transform:uppercase;padding:3px 6px;border-radius:5px;border:1px solid currentColor;vertical-align:2px;white-space:nowrap}
.vd.big{font-size:12.5px;padding:5px 9px}
.vd.big small{font:600 11px var(--mono);text-transform:none;letter-spacing:0;opacity:.85;margin-left:6px}
.vd-box{margin:0 0 12px}
.vd-btn{display:flex;align-items:center;gap:10px;border:0;background:transparent;padding:0;cursor:pointer;color:var(--ink);font:inherit}
.vd-why{font-size:12px;color:var(--faint);text-decoration:underline;text-underline-offset:3px}
.vd-list{display:none;margin-top:10px;border:1px solid var(--line);border-radius:10px;padding:4px 12px 8px}
.vd-box.open .vd-list{display:block}
.vd-g{font:600 9.5px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:.7px;margin:10px 0 4px}
.vd-c{display:flex;gap:9px;align-items:flex-start;padding:4px 0;font-size:13px;color:var(--ink);line-height:1.35}
.vd-c i{font-style:normal;font-weight:700;width:14px;flex:none;text-align:center}
.vd-c.ok i{color:var(--good)} .vd-c.no i{color:var(--bad)} .vd-c.na{color:var(--faint)} .vd-c.na i{color:var(--faint)}
.vd-c small{display:block;font:500 11px var(--mono);color:var(--faint);margin-top:1px}
.vd-w{font:700 9px var(--mono);font-style:normal;color:var(--brass);border:1px solid var(--brass);border-radius:4px;padding:0 4px;margin-left:4px;vertical-align:2px}
.vd-w.gate{color:var(--bad);border-color:var(--bad)}
.vd-gate{margin:8px 0 0!important;font-size:12px!important;color:var(--bad)!important}
.vd-note{margin:10px 0 0!important;font-size:11px!important;color:var(--faint)!important;line-height:1.45}
.pf-zh .b .tlw{margin-left:6px}
.no-name{margin:-4px 0 10px!important;color:var(--mute);font-size:13px}
.no-name span{color:var(--faint)}
.no-kv{display:flex;flex-wrap:wrap;gap:6px 18px;margin-bottom:12px}
.no-kv div{display:flex;flex-direction:column}
.no-kv span{font:600 9.5px var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.6px}
.no-kv b{font:700 14px var(--mono);color:var(--ink)}
.no-acts{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.no-acts .btn{text-decoration:none}
.no-h{font:700 11px var(--mono);color:var(--brass);text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}
.no-list{max-height:min(46vh,420px);overflow:auto;border:1px solid var(--line);border-radius:10px}
.no-list .na{padding:12px;margin:0}
.no-it{display:block;padding:9px 12px;border-bottom:1px solid var(--line);color:var(--ink);font-size:13px;line-height:1.45;text-decoration:none}
.no-it:last-child{border-bottom:0}
.no-it:hover{background:var(--hover);text-decoration:none}
.no-it span{display:block;font:500 10.5px var(--mono);color:var(--faint);margin-bottom:2px}
.pf-ni b[data-news]{cursor:pointer}
.pf-ni b[data-news]:hover{text-decoration:underline}
@media (max-width:1100px){.pf-grid{grid-template-columns:1fr}.pf-right .wrap{max-height:340px}}
@media (max-width:700px){
  .pf-top{gap:6px}
  .pf-acts{width:100%;display:grid;grid-template-columns:1.25fr 1fr auto auto;gap:6px}   /* one row: Buy / Sell, Pay in / out, ₹, ⟳ */
  .pf-idx{order:-1;width:100%;justify-content:space-between;gap:4px 8px;font-size:11px;padding:2px 2px 4px}   /* phone: indices on top, one line */
  .pf-ix b{font-size:10px;margin-right:4px}
  .pf-ix em{font-size:11px;margin-left:4px}
  .pf-ix .abs{display:none}                                          /* phone: just ▲ +0.49%, both indices on one line */
  .pf-acts .btn{white-space:nowrap}
  .pf-acts .btn.ico.ltp{padding:0 8px;gap:4px}
  .pf-asof{font-size:10.5px}
  .pf-acts .btn{justify-content:center;padding:0 8px}
  .pf-tg{width:100%;justify-content:center}
  .pf-sum{display:grid;grid-template-columns:1fr 1fr;overflow:visible}
  .pf-k{border-left:0;border-top:1px solid var(--line);padding:7px 12px}
  .pf-k.gain{grid-column:1 / -1;border-top:0;border-radius:9px 9px 0 0}
  .pf-k:not(.gain):nth-child(even){border-right:1px solid var(--line)}   /* left column of the 2-up grid */
  .pf-left .wrap{max-height:60vh}.pf-right .wrap{max-height:45vh}
  .pf-co{max-width:130px}
  .wip-back{align-items:flex-start;padding-top:40px}
}

/* phone portfolio (pfPhone): Zerodha-style summary card + one card per stock; nothing wider than the screen */
.pf-zc{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:14px 16px 4px;margin:0 0 14px}
.pf-zr{display:flex;justify-content:space-between;gap:10px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.pf-zr div{display:flex;flex-direction:column;gap:3px;min-width:0}
.pf-zr .r{text-align:right}
.pf-zr span,.pf-zl > span{font-size:12.5px;color:var(--faint)}
.pf-zr b{font:600 19px var(--mono);color:var(--ink)}
.pf-zl{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:10px 0 2px}
.pf-zl b{font:600 15px var(--mono);color:var(--ink);text-align:right}
.pf-zl small{font:500 11px var(--mono);color:var(--faint);margin-left:7px}
.pf-cash{margin:10px -16px 0;border-top:1px solid var(--line);background:color-mix(in srgb, var(--good) 5%, transparent);border-radius:0 0 11px 11px}
.pf-cash summary{list-style:none;display:flex;justify-content:space-between;gap:10px;padding:9px 16px;cursor:pointer;font-size:12px;color:var(--faint)}
.pf-cash summary::-webkit-details-marker{display:none}
.pf-cash summary > span:first-child::before{content:"▸";display:inline-block;width:12px;transition:transform .15s}
.pf-cash[open] summary > span:first-child::before{transform:rotate(90deg)}
.pf-cash summary b{font:600 12px var(--mono);color:var(--mute);margin-left:4px}
.pf-cg{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;padding:4px 16px 12px}
.pf-cg div{display:flex;flex-direction:column;gap:1px;min-width:0}
.pf-cg span{font:600 9.5px var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.6px}
.pf-cg b{font:600 13.5px var(--mono);color:var(--ink)}
.pf-cg small{font-size:10.5px;color:var(--faint)}
.pf-zs{margin:0 0 16px}
.pf-zs > .pf-h{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.pf-zs > .pf-h .pf-rm.all{margin:0 0 0 auto}
.pf-zlist{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden}
.pf-zh,.pf-zt{padding:11px 14px;border-bottom:1px solid var(--line)}
.pf-zh:last-child,.pf-zt:last-child{border-bottom:0}
.pf-zh{cursor:pointer;-webkit-tap-highlight-color:transparent}
.pf-zh:active{background:var(--hover)}
.pf-zh .a,.pf-zh .b,.pf-zt .a,.pf-zt .b{display:flex;justify-content:space-between;align-items:center;gap:10px;min-width:0}
.pf-zh .a{font:500 12px var(--mono);color:var(--faint)}
.pf-zh .a i{font-style:normal;opacity:.6;margin:0 3px}
.pf-zh .a em{font-style:normal}
.pf-zh .b{margin:5px 0;font-size:15.5px}
.pf-zh .b b{font:600 15px var(--sans);color:var(--ink)}
.pf-zh .b b:last-child{font-family:var(--mono)}
.pf-zh .up,.pf-zh .up b{color:var(--good)} .pf-zh .dn,.pf-zh .dn b{color:var(--bad)}
.pf-zh .b b.up{color:var(--good)} .pf-zh .b b.dn{color:var(--bad)}
.pf-zh em.dn{font-style:normal;font-size:12px}
.pf-zt .a{font:500 12px var(--mono);color:var(--faint)}
.pf-zt .b{margin-top:5px;font-size:13.5px;color:var(--mute)}
.pf-zt .b b{color:var(--ink)}
.pf-zt .b small{color:var(--faint)}
.pf-zt .pf-acts2,.pf-zt .pf-rm,.pf-zt .pf-lock{flex:none}
.pf-zempty{padding:14px;margin:0;font-size:13px}
@media (max-width:700px){
  /* bottom sheets (menus, stock overlay): the dimmed back is the scroller, an empty screen-high gap on top, then the sheet */
  .wip-back.sheet.open{display:block;padding:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:none}
  .wip-back.sheet::-webkit-scrollbar{display:none}
  .wip-back.sheet::before{content:"";display:block;height:100vh;height:100dvh}   /* the gap: tap it to close */
  .wip-back.sheet .wip-box{width:100%;max-height:none;overflow:visible;border-radius:16px 16px 0 0;border-bottom:0;padding:0 16px 22px;animation:noUp .24s cubic-bezier(.2,.8,.2,1)}
  .wip-back.sheet .wip-box::before{content:"";display:block;width:38px;height:4px;border-radius:2px;background:var(--line);margin:0 auto;position:relative;top:8px}
  .wip-back.sheet .wip-box > h3{position:sticky;top:0;z-index:3;background:var(--panel);margin:0 -16px 8px;padding:16px 46px 8px 16px}   /* title + × stay on top */
  .wip-back.sheet .wip-box.no-box > h3{padding-right:200px}
  .wip-back.sheet h3 > .wip-x{top:8px;right:6px}
  .wip-back.sheet h3 > .vh{top:8px;right:46px;font:inherit;font-weight:400}       /* sparkline rides in the sticky title */
  .wip-back.sheet .wip-box.no-box > h3{min-height:50px;box-sizing:border-box}
  .wip-back.sheet .no-list{max-height:none;overflow:visible}            /* one scroll: the sheet itself */
  @keyframes noUp{from{transform:translateY(40%)}}
}

/* phone: floating Filters button, bottom-right. 50% normally, 5% while scrolling or holding a finger down > 1 s */
#ffab{display:none}
@media (max-width:700px){
  #ffab.show{display:flex;align-items:center;justify-content:center;position:fixed;right:16px;bottom:18px;z-index:80;width:52px;height:52px;border-radius:50%;
    border:0;background:var(--brass);color:var(--bg);box-shadow:0 6px 18px rgba(0,0,0,.35);opacity:.5;transition:opacity .25s;cursor:pointer;-webkit-tap-highlight-color:transparent}
  #ffab.dim{opacity:.05}
  #ffab .cnt{position:absolute;top:-2px;right:-2px;min-width:18px;height:18px;padding:0 4px;border-radius:9px;background:var(--ink);color:var(--bg);font:700 10.5px/18px var(--mono);text-align:center}
  #ffab .cnt:empty{display:none}
  body.fmodal #ffab{display:none}
}

/* custom dropdown: replaces the native <select> popup (which ignores the theme) */
.fsel{position:relative;display:inline-flex;align-items:center}
.fsel > select{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.fsel-btn{display:inline-flex;align-items:center;gap:8px;background:transparent;border:0;padding:0 2px;color:var(--ink);font:inherit;font-size:12.5px;cursor:pointer;line-height:1;white-space:nowrap}   /* one line: "Smallcap 500" never wraps */
.fsel-btn.boxed{border:1px solid var(--line);border-radius:6px;padding:6px 10px;background:var(--bg)}
.fsel-btn.boxed:hover,.fsel.open .fsel-btn.boxed{border-color:var(--brass)}
.fsel-btn svg{flex-shrink:0;opacity:.7;transition:transform .15s}
.fsel.open .fsel-btn svg{transform:rotate(180deg)}
.fsel-menu{display:none;position:absolute;top:calc(100% + 8px);left:-8px;min-width:calc(100% + 16px);z-index:60;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px;box-shadow:var(--drop-shadow)}
.fsel.open .fsel-menu{display:block}
.fsel-opt{display:block;width:100%;text-align:left;white-space:nowrap;padding:8px 12px;border:0;border-radius:5px;background:transparent;color:var(--mute);font:inherit;font-size:12.5px;cursor:pointer}
.fsel-opt:hover,.fsel-opt:focus-visible{background:var(--hover);color:var(--ink);outline:none}
.fsel-opt.on{color:var(--brass);background:var(--brassbg);font-weight:600}
html{-webkit-text-size-adjust:100%;overflow-y:scroll;scrollbar-gutter:stable}   /* scrollbar space always kept: a shorter page (fewer rows) no longer widens the view and shifts the header */
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
.chip:has(input:disabled){opacity:.6;cursor:default}
.chip input{margin:0;accent-color:var(--brass);vertical-align:middle}
.chip.backsel{padding:0 8px}
.chip.backsel.on{border-color:var(--brass)}
.chip.backsel.on .fsel-btn{color:var(--brass);font-weight:700}
.chip.loading{opacity:.6;cursor:progress}
/* PSU / Semi PSU: plain text; ticked = hidden = struck through in red */
.chip.strike{position:relative}
.chip.strike input{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.chip.strike:has(input:checked){border-color:var(--bad);background:var(--badbg);color:var(--ink)}
.chip.strike:has(input:checked) span{text-decoration:line-through;text-decoration-color:var(--bad);text-decoration-thickness:2px}
.chip.ytoggle{position:relative}
.chip.ytoggle input{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.chip.ytoggle::before{content:"";width:10px;height:10px;border-radius:50%;border:1.5px solid currentColor;box-sizing:border-box;flex-shrink:0}
.chip.ytoggle:has(input:checked){background:var(--brass);border-color:var(--brass);color:var(--bg)}
.chip.ytoggle:has(input:checked)::before{background:radial-gradient(circle, currentColor 0 2px, transparent 2.6px)}   /* dot inside the ring */
/* header never shifts: chips that change content get a fixed width (set in JS from their widest option) */
.global .chip,.hgrp .chip{position:relative;flex-shrink:0}
.global .chip .fsel,.hgrp .chip .fsel{position:static;flex:1;min-width:0}
.global .chip .fsel-btn,.hgrp .chip .fsel-btn{width:100%;justify-content:space-between}
.global .chip .fsel-menu,.hgrp .chip .fsel-menu{left:-1px;right:-1px;min-width:0;top:calc(100% + 6px)}   /* exactly under the chip, same width */
.hgrp{display:flex;align-items:center;gap:8px;flex-shrink:0}
nav .hgrp{margin-left:auto;padding:4px 0}                       /* desktop: Historical + date at the far right of the tabs row */
nav .hgrp .chip{height:30px}
.btn.back .arr{display:none}
#pfBackV{gap:6px} #pfBackV .arr{display:inline-block}   /* Portfolio page: the return icon shows on computers too */
.gsep{display:none;width:1px;height:20px;background:var(--line);margin:0 2px;flex-shrink:0}
#backChip{justify-content:center;min-width:max-content;flex-shrink:0}   /* never narrower than its text, whatever width JS set */
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

nav{display:flex;align-items:center;gap:2px;padding:0 16px;border-bottom:1px solid var(--line);overflow-x:auto;position:sticky;top:0;z-index:5;background:var(--panel)}
@media (min-width:701px){nav{overflow:visible;z-index:30}}    /* the date menu drops out of the tabs row, above the table tools */
nav a{padding:11px 14px 9px;color:var(--mute);font:500 12px var(--mono);letter-spacing:.3px;border-bottom:2px solid transparent;white-space:nowrap}
nav a:hover{color:var(--ink);text-decoration:none}
nav a.on{color:var(--brass);border-color:var(--brass)}
/* Checklist tab: Prime. Free accounts see it in purple with a crown; Prime members see a plain tab */
nav a.ck-tab .ck-cr{display:none;width:12px;height:12px;margin-left:6px;vertical-align:-1px}
html:not(.hni-pro) nav a.ck-tab{color:#b892f5}
/* demo (review) login: All Stocks only */
html.hni-demo #nav a:not([data-t=all]),html.hni-demo #histGrp,html.hni-demo #histSep,html.hni-demo #pfBuilder,html.hni-demo #weeklyBtn,html.hni-demo #saveHtml{display:none!important}
html:not(.hni-pro) nav a.ck-tab .ck-cr{display:inline-block}
body.day nav a.ck-tab:not(.on){color:inherit}
html:not(.hni-pro) body.day nav a.ck-tab{color:#5b2d9c}
.ck-seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:0 0 12px}
.ck-seg button{border:0;background:transparent;color:var(--mute);font:600 12px var(--mono);padding:8px 14px;cursor:pointer}
.ck-seg button.on{background:var(--brassbg);color:var(--brass)}
.ck-rules{border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:0 0 14px;max-width:980px}
.ck-rules>summary{cursor:pointer;padding:10px 14px;font:600 12.5px var(--mono);color:var(--mute)}
.ck-rules table{width:100%;border-collapse:collapse;font-size:12.5px}
.ck-rules td{padding:6px 14px;border-top:1px solid var(--line);color:var(--mute)}
.ck-rules td:first-child{color:var(--ink)}
.ck-rules .nd{color:var(--faint);font-style:italic}
.ck-ok{display:inline-block;padding:1px 7px;border-radius:6px;font:600 11.5px var(--mono);background:rgba(120,204,144,.15);color:var(--up,#78cc90)}
.ck-lockbox{padding:28px 20px;border:1px dashed #9b6bdf;border-radius:12px;max-width:640px;margin:24px auto;text-align:center;color:var(--mute);line-height:1.6}
.ck-lockbox b{display:block;font:700 16px var(--mono);color:var(--ink);margin-bottom:6px}
.ck-lockbox button{margin-top:12px}

main{padding:20px 24px 56px;max-width:1880px;margin:0 auto}
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
/* number boxes: no up / down arrows (they were white in day mode) */
input[type=number]{-moz-appearance:textfield;appearance:textfield}
input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
/* tick boxes drawn in the theme's colours: page background, brass when ticked (the native one stays white in day mode) */
input[type=checkbox]{-webkit-appearance:none;appearance:none;flex-shrink:0;margin:0;border:1.5px solid var(--faint);border-radius:4px;background:var(--bg);
  display:inline-grid;place-content:center;cursor:pointer;transition:background .12s,border-color .12s}
input[type=checkbox]:hover{border-color:var(--brass)}
input[type=checkbox]:checked{background:var(--brass);border-color:var(--brass)}
input[type=checkbox]:checked::after{content:"";width:8px;height:4px;border:2px solid var(--bg);border-top:0;border-right:0;transform:translateY(-1px) rotate(-45deg)}
input[type=checkbox]:disabled{opacity:.5;cursor:default}
input:focus,select:focus{border-color:var(--brass);outline:none}

/* stat strip: always ONE line; if it is wider than the screen it scrolls sideways instead of wrapping */
.stats{position:relative;display:flex;flex-wrap:nowrap;overflow-x:auto;scroll-snap-type:x proximity;overflow-y:hidden;margin:4px 0 10px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);-webkit-overflow-scrolling:touch}
.stat{flex:0 0 auto;white-space:nowrap;scroll-snap-align:start;padding:6px 14px 6px 0;margin-right:14px;border-right:1px solid var(--line)}
.stat.cap{padding-left:12px;border-left:2px solid var(--brass);border-right:0;margin-right:4px}
.stat:has(+ .stat.cap){border-right:0;margin-right:0}   /* the yellow line IS the divider between families */
.stat.cap span{color:var(--brass)!important;font:600 9.5px var(--mono)!important;text-transform:uppercase;letter-spacing:1px}
.stat.cap b{font-size:12px!important;color:var(--mute)!important;font-weight:600}
.stat.grpcap{margin-left:0}
.stat:last-child{border-right:0}
.stat b{display:block;font:700 16px/1.2 var(--mono);color:var(--brass)}
.stat b small{font:600 11px var(--mono);margin-left:7px}
.stat > span{color:var(--faint);font-size:10.5px;line-height:1.3}

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
tbody td:first-child{border-left:4px solid var(--sc, transparent)}   /* sector colour strip */
body.day tbody td:first-child{border-left-color:color-mix(in srgb, var(--sc, transparent) 70%, #5a4a30)}   /* pastels need more ink on the light theme */
tbody tr:hover td:first-child{box-shadow:inset 2px 0 0 var(--brass)}
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
/* Methodology: quick cards + glossary tables with a one-line example column */
.doc .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:8px 0 14px}
.doc .card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--panel)}
.doc .card b{display:block;font:700 12.5px var(--mono);color:var(--brass);margin-bottom:3px}
.doc .card span{font-size:12.5px;color:var(--mute);line-height:1.45}
.doc table.gl td:first-child{white-space:nowrap}
.doc table.gl td.ex{color:var(--mute);font-style:italic}
@media (max-width:700px){.doc table.gl td:first-child{white-space:normal;min-width:90px}}
.doc td,.doc th{text-align:left;white-space:normal;cursor:default;position:static;vertical-align:top}
.doc td{font-family:var(--sans)}
@media (max-width:700px){
  header.top{padding:10px 12px;gap:10px}
  .brand{gap:8px}
  .brand .sub{font-size:11.5px;width:100%}
  .global{gap:6px;width:100%}
  .btn.back .arr{display:inline-block}
  .gsep{display:inline-block}
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
  .stat{padding:5px 10px 5px 0;margin-right:10px}
  .stat b{font-size:14px}
  .stat > span{font-size:10px}
  .wrap{max-height:72vh}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
__AUTH_HEAD__</head>
<body class="night">
__AUTH_BODY__<script>
(function(){ var t = 'night'; try { if (localStorage.getItem('hnimanshu_theme') === 'day') t = 'day'; } catch(e){}
  document.body.classList.remove('night', 'day'); document.body.classList.add(t);
  document.documentElement.dataset.theme = t;   /* scrollbar + dropdown shadow read this */ })();
</script>
<header class="top">
  <div class="brand">
    <a class="logo" href="./" id="homeLink" aria-label="HNImanshu home">
      <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg"><rect width="32" height="32" rx="7" fill="var(--panel2)"/><text x="16" y="23" font-family="Georgia,serif" font-size="18" font-weight="bold" fill="var(--brass)" text-anchor="middle">H</text></svg>
      <div><div class="wm"><span class="hni">HNI</span><span class="manshu">manshu</span></div><div class="tagline">Factor Screener</div></div>
    </a>
    <div class="sub" id="meta"></div>
  </div>
  <div class="global">
    <a class="btn back" id="pfBackV" href="#/value" title="Return to the Value screen"><svg class="arr" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>Value Screener</a>
    <a class="btn back" id="backBtn" href="valuation"><svg class="arr" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M11 18l-6-6 6-6"/></svg>Valuation</a>
    <span class="gsep" aria-hidden="true"></span>
    <label class="chip" id="uniChip">List <select id="uniSel"></select></label>
    <label class="chip strike" title="Click to hide PSU stocks (struck through = hidden)"><input type="checkbox" id="hidePSU"><span>PSU</span></label>
    <label class="chip strike" title="Click to hide semi-PSU stocks (struck through = hidden)"><input type="checkbox" id="hideSemi"><span>Semi PSU</span></label>
    <span class="gsep" id="histSep" aria-hidden="true"></span>
    <div class="hgrp" id="histGrp">
      <label class="chip ytoggle" id="backChip"><input type="checkbox" id="histOn"> Historical</label>
      <label class="chip backsel" id="backPer" title="Historical date"><select id="backSel" aria-label="Historical period"></select></label>
    </div>
    <button class="btn primary" id="saveHtml">Save as HTML</button>
    <button type="button" class="btn hweek" id="weeklyBtn" title="Weekly updates on Telegram"><svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#29a9eb"/><path d="M5.4 11.6l11.5-4.4c.5-.2 1 .1.8.9l-2 9.3c-.1.6-.5.8-1.1.5l-3-2.2-1.5 1.4c-.2.2-.3.3-.6.3l.2-3.1 5.6-5.1c.2-.2 0-.3-.4-.1l-6.9 4.4-3-.9c-.6-.2-.7-.6.1-1z" fill="#fff"/></svg>Weekly</button>
    <a class="btn hbag bld" id="pfBuilder" href="builder" target="_blank" rel="noopener" title="PF Builder" aria-label="PF Builder"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 10.5H4.8L3.5 21.5H10z" fill="currentColor" fill-opacity=".18"/><path d="M6.9 10.5V8.8a3 3 0 0 1 3.1-3"/><g transform="translate(1.2 -0.6) rotate(-42 16.5 11)"><path d="M12.6 3.4h8a1 1 0 0 1 1 1v2.8a1 1 0 0 1-1 1h-8l-1.9-2.4z" fill="currentColor" stroke-width="1"/><path d="M16.6 8.2v10.4" stroke-width="3"/></g></svg><span class="lg">PF Builder</span></a>
    <a class="btn hbag" id="pfBag" href="#/portfolio" title="Portfolio" aria-label="Portfolio"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 7h12l1 14H5L6 7z"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/></svg><span class="lg">Portfolio</span></a>
    <div id="theme-toggle" title="Day / night">
      <span class="toggle-icon moon-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></span>
      <button class="toggle-track" aria-label="Toggle day / night theme"><div class="toggle-thumb"></div></button>
      <span class="toggle-icon sun-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg></span>
    </div>
  </div>
</header>
<nav id="nav">
  <a href="#/" data-t="home">Industries</a>
  <a href="#/value" data-t="value">Value screen</a>
  <a href="#/momentum" data-t="momentum">Momentum</a>
  <a href="#/all" data-t="all">All Stocks</a>
  <a href="#/method" data-t="method">Methodology</a>
</nav>
<div id="mnav" aria-label="Pages and screens">
  <button type="button" class="mnav-b" id="mnavTab" aria-haspopup="dialog"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>Value screen</span><i>▾</i></button>
  <button type="button" class="mnav-b" id="mnavPre" aria-haspopup="dialog" hidden><span>Deep value</span><i>▾</i></button>
</div>
<div id="uniHint" class="uni-hint" hidden></div>
<style>
/* Lists: the helper line under the tabs when "All lists" is on */
.uni-hint{padding:8px 24px 0;font:500 11.5px var(--mono);color:var(--faint);letter-spacing:.02em}
.uni-hint b{color:var(--mute);font-weight:600}
body.pf-mode .uni-hint{display:none!important}
@media (max-width:700px){.uni-hint{padding:8px 12px 0}}
/* Prime lists in the List menu: purple; locked (free accounts) open the plans popup */
.fsel-opt.prime{color:#b892f5}
body.day .fsel-opt.prime{color:#5b2d9c}
.fsel-opt.locked svg{width:12px;height:12px;margin-left:7px;vertical-align:-1px;opacity:.9}
/* PF Builder → Portfolio: the check-before-buying list */
.pfb .pfb-tw{max-height:46vh;overflow:auto;border:1px solid var(--line);border-radius:8px}
.pfb-tbl{width:100%;border-collapse:collapse;font:12.5px var(--mono)}
.pfb-tbl th{position:sticky;top:0;background:var(--panel2);color:var(--faint);font:600 10.5px var(--mono);text-transform:uppercase;letter-spacing:.05em;text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
.pfb-tbl td{padding:6px 10px;border-bottom:1px solid var(--line);color:var(--ink)}
.pfb-tbl tr:last-child td{border-bottom:0}
.pfb-tbl .r{text-align:right}
.pfb-tbl i{font-style:normal;font-size:10px;color:var(--faint);margin-left:4px}
.pfb-tbl tr.z td{color:var(--faint);text-decoration:line-through}
.pf-src{display:block;font:500 10.5px var(--mono);color:var(--faint);letter-spacing:.01em;white-space:nowrap}
.pf-zh .pf-zsrc{margin-top:3px}.pf-zh .pf-zsrc .pf-src{overflow:hidden;text-overflow:ellipsis}
.tl-etf .a{background:radial-gradient(circle at 35% 30%,#ffe08a,#f0a020 55%,#b86c00);box-shadow:0 0 6px 1px rgba(240,160,32,.75)}.tl-etf .g{background:radial-gradient(circle at 35% 30%,#a6f5a0,#3ec65a 55%,#1d8a32);box-shadow:0 0 6px 1px rgba(62,198,90,.75)}
.pfb-sum{display:flex;flex-wrap:wrap;gap:6px 16px;font:500 12px var(--mono);color:var(--faint)}
.pfb-sum b{color:var(--ink)}
.pfb-sum i{font-style:normal;font-size:11px}
.pfb .btn.ghost{height:36px;justify-content:center}
/* phone header, right to left: avatar · Portfolio · PF Builder · Weekly (PF Builder and Weekly side by side) */
@media (max-width:700px){
  .btn.hbag{right:calc(var(--au-right,104px) + 40px)}
  .btn.hbag.bld{right:calc(var(--au-right,104px) + 80px)}
  #weeklyBtn{right:calc(var(--au-right,104px) + 120px)}
}
</style>
<main id="view"></main>
<button type="button" id="ffab" aria-label="Filters" title="Filters"><svg width="20" height="20" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 3.5h12M4.5 8h7M7 12.5h2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><b class="cnt"></b></button>

<script>
const WEB = __WEB__;
const D = __DATA__;
let S = D.stocks, BACK = false, SAME = false, NOPRICE = false;
/* SAME    = past-date engine on screen (= Historical on)
   NOPRICE = no price for this date (1Y back without prices.csv): price filters / columns / Screens switch off */
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
const GF = lsJson('gf', {psu:false, semi:false, back:false, backP:'1Y', uni:'all'});
delete GF.same;                                              // old "Same method" setting from earlier visits: dropped
const QP = new URLSearchParams(location.search);
if (QP.has('uni') && history.replaceState) history.replaceState(null, '', location.pathname + location.hash);   // old ?uni= links: ignored, URL cleaned
/* "Others" (every NSE stock outside the 3 lists) is its own list: "All lists" = the 3 lists only */
const OTH = 'Others';
const othOnly = s => !!(s.uni && s.uni.length) && s.uni.every(u => u === OTH);
const vis = s => !(GF.psu && s.psu === 'psu') && !(GF.semi && s.psu === 'semi')
  && (!GF.uni || GF.uni === 'all' ? !othOnly(s) : (s.uni || []).includes(GF.uni));
const V = () => S.filter(vis);
/* per-user settings: kept in this browser (localStorage) so a refresh reopens the same view.
   Every read / write goes through lsGet / lsSet, so a login-backed store can replace it in one place later. */
const sortState = lsJson('sorts', {}), searchState = lsJson('search', {});
const saveView = () => { lsSet('sorts', JSON.stringify(sortState)); lsSet('search', JSON.stringify(searchState)); };

/* ---------- industry maths ---------- */
const median = a => { const s=[...a].filter(x=>x!=null&&isFinite(x)).sort((x,y)=>x-y), m=s.length>>1; return s.length? (s.length%2? s[m] : (s[m-1]+s[m])/2) : null; };
const mean = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : null;
let industries = {};
/* switch between today's data and the 1-year-back snapshot */
function setMode() {
  BACK = !!(GF.back && D.then && D.then.stocks && (D.then.key || '1Y') === GF.backP);   // the chosen period once its data is in
  const bp = $('#backPer'); if (bp) bp.classList.toggle('on', BACK);
  SAME = BACK || !!D.sameToday;                                   // past dates are always rebuilt from raw rows; today too when sameToday
  S = BACK ? D.then.stocks : D.stocks;
  NOPRICE = BACK && !D.then.hasPrice;
  industries = {};
  S.forEach(s => (industries[s.ind] = industries[s.ind] || {name:s.ind, stocks:[]}).stocks.push(s));
  // industry benchmarks: from the list on screen ("Others" on its own, else the 3 lists), so the Others list
  // never moves the numbers of the main lists; an industry with none of those falls back to all its stocks
  const scope = GF.uni === OTH ? othOnly : (s => !othOnly(s));
  Object.values(industries).forEach(g => { const sub = g.stocks.filter(scope); buildIndustry(g, sub.length ? sub : g.stocks); });
}
function buildIndustry(g, list = g.stocks) {
  const pes = list.filter(s=>s.pe!=null);
  const w = pes.filter(s=>s.mcap);
  g.n = list.length; g.nPE = pes.length;
  g.mcap = list.reduce((t,s)=>t+(s.mcap||0),0);
  g.pe = {median: median(pes.map(s=>s.pe)), mean: mean(pes.map(s=>s.pe)),
          weighted: w.length ? w.reduce((t,s)=>t+s.mcap,0) / w.reduce((t,s)=>t+s.mcap/s.pe,0) : null};
  g.medPB = median(list.map(s=>s.pb));
  g.medROE = median(list.map(s=>s.roe));
  g.medDY = median(list.map(s=>s.dy));
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

const W_DEF = {pe:25, pb:15, q:15, g:15, fcf:15, cc:10, dy:5, cfo:5};
const BACK_KEYS = ['q', 'g', 'cc', 'cfo'];
const W_LABEL = {pe:'PE vs industry', pb:'PB', q:'ROCE (ROE fin.)', g:'EPS growth', fcf:'FCF yield', cc:'Cash conversion', dy:'Div yield', cfo:'CFO consistency'};
const METRICS = {
  pe:  {v:s=>prem(s), hi:false},
  pb:  {v:s=>s.pb,    hi:false},
  q:   {v:qual,       hi:true},
  g:   {v:s=>s.cagr,  hi:true},
  fcf: {v:s=>s.fcfy,  hi:true},
  cc:  {v:s=>s.cc,    hi:true},
  dy:  {v:s=>s.dy,    hi:true},
  cfo: {v:s=>s.cfop ? s.cfop[0] / s.cfop[1] : null, hi:true},   // share of years with positive operating cash flow
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
  const key = JSON.stringify(W) + '|' + method;                // same stocks, weights and PE method → scores are already on them
  if (computeScores.S === S && computeScores.key === key) return;
  computeScores.S = S; computeScores.key = key;
  const uni = {}, byInd = {}, main = s => !othOnly(s);      // peers = the 3 lists, so Others never shifts their scores
  for (const k in METRICS) uni[k] = S.filter(main).map(METRICS[k].v).filter(ok).sort((a,b)=>a-b);
  Object.values(industries).forEach(g => {
    byInd[g.name] = {};
    for (const k in METRICS) byInd[g.name][k] = g.stocks.filter(main).map(METRICS[k].v).filter(ok).sort((a,b)=>a-b);
  });
  S.forEach(s => {
    s.score = null; s.parts = {};
    if (!NOPRICE && s.pe == null) return;
    let tot = 0, wt = 0, avail = 0;
    for (const k in METRICS) {
      const w = +W[k] || 0; if (!w) continue;
      if (NOPRICE && !BACK_KEYS.includes(k)) continue;     // no price for this date: quality metrics only
      if (s.fin && (k==='fcf' || k==='cc' || k==='cfo')) continue;   // not applicable to financials
      avail += w;
      const v = METRICS[k].v(s);
      if (!ok(v)) continue;
      const peers = byInd[s.ind][k].length >= 4 ? byInd[s.ind][k] : uni[k];
      const p = pctl(peers, v, METRICS[k].hi);
      s.parts[k] = p; tot += p*w; wt += w;
    }
    if (avail && wt >= 0.5*avail) s.score = tot/wt*100;
  });
  computeMomentum();
}
/* Price momentum (0–100): average percentile, across every stock on screen, of the 3-month, 6-month and
   12-minus-1-month returns (the last month is skipped: short-term moves tend to reverse). Needs 2 of 3. */
let HAS_MOM = false;                                     // false until prices.csv carries momentum: the filter is then skipped
function computeMomentum() {
  const cols = [0, 1, 2].map(i => S.map(s => s.mom ? s.mom[i] : null).filter(ok).sort((a, b) => a - b));
  S.forEach(s => {
    s.momS = null;
    if (!s.mom) return;
    const p = [0, 1, 2].filter(i => ok(s.mom[i])).map(i => pctl(cols[i], s.mom[i], true));
    if (p.length >= 2) s.momS = p.reduce((a, x) => a + x, 0) / p.length * 100;
  });
  HAS_MOM = S.some(s => s.momS != null);
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
  if (D.then.hasPrice) {                                   // price-return backtest of this list
    const rr = rows.map(s => sv(s,'ret')).filter(ok), b = D.then.bench ? D.then.bench.ret : null;
    const avg = rr.length ? rr.reduce((a,x) => a + x, 0) / rr.length : null;
    return [['Return, eq-wt', signPct(avg)], ['NIFTY 500', signPct(b)]];   // kept short: the whole strip fits without scrolling
  }
  return [['Profit growth, list', signPct(medSince(rows,'patG'))],          // no prices.csv: business results only
          ['Profit up', r.length ? `${up} of ${r.length}` : '–']];
}
/* in 1Y-back mode, price-based columns are dropped and the "since then" columns take the price column's place */
const PRICE_KEYS = new Set(['pta','pfa','invp','qvV','cmp','pe','cpe','prem','indpe','pb','peg','gup','dy','ey','fcfy','from52','cheap','nPE','rEY']);
/* ---------- CSV export: name encodes the tab + active filters, e.g. Value_screen_mcap_20K_RCE_ROE12_MT10.csv ---------- */
const fnNum = v => String(v).replace('-', 'm').replace('.', 'p');          // filename-safe: -30 -> m30, 0.5 -> 0p5
const fnK = v => +v >= 1000 ? fnNum(+(v / 1000).toFixed(2)) + 'K' : fnNum(v); // 20000 -> 20K
const FN_CODE = {
  minMcap:v=>'mcap_'+fnK(v), maxMcap:v=>'maxmcap_'+fnK(v), maxPE:v=>'PE'+fnNum(v), maxPB:v=>'PB'+fnNum(v), minQ:v=>'RCE_ROE'+fnNum(v),
  minScore:v=>'SC'+fnNum(v), minTrend:v=>'MT'+fnNum(v), maxPrem:v=>'VSIND'+fnNum(v), maxPeg:v=>'PEG'+fnNum(v),
  minFcf:v=>'FCF'+fnNum(v), minDy:v=>'DY'+fnNum(v), minCc:v=>'CC'+fnNum(v), top:v=>'TOP'+fnNum(v),
  minChg:v=>'CHG'+fnNum(v), minNow:v=>'NOW'+fnNum(v), dir:v=>String(v).toUpperCase(),
  trap:v=>v?'NOTRAPS':'TRAPS', graham:v=>v?'GRAHAM':'', exFin:v=>v?'EXFIN':'INCLFIN',
  smart:v=>v?'SMART':'', hideJump:v=>v?'NOJUMPS':'JUMPS',
  minQ5:v=>'RCE5Y'+fnNum(v), maxDebitda:v=>'DEBITDA'+fnNum(v), maxNnpa:v=>'NNPA'+fnNum(v), minPcr:v=>'PCR'+fnNum(v), minCfo:v=>'CFO'+fnNum(v), minMom:v=>'MOM'+fnNum(v), maxMom:v=>'MOMX'+fnNum(v), minUp:v=>'UP'+fnNum(v), maxDe:v=>'DE'+fnNum(v), minRoa:v=>'ROA'+fnNum(v), maxNpa:v=>'NPA'+fnNum(v), maxPta:v=>'MA'+fnNum(v), maxPfa:v=>'MFA'+fnNum(v), minInv:v=>'INV'+fnNum(v),
  minRoce:v=>'ROCE'+fnNum(v), minRoe:v=>'ROE'+fnNum(v), minRoeFin:v=>'ROEFIN'+fnNum(v), minIcov:v=>'ICR'+fnNum(v),
  maxProDrop:v=>'PRODROP'+fnNum(v), exPsu:v=>v?'':'WITHPSU', peers:v=>v==='all'?'PEERSALL':'', showFail:v=>v?'WITHFAILS':'',
};
// numbers: kept when set and non-zero; tick boxes: only when changed from default; dropdowns: always
function csvName(tab, st, def, keys) {
  const parts = [tab];
  keys.forEach(k => {
    const v = st[k], code = FN_CODE[k];
    if (!code || v === '' || v == null) return;
    if (typeof v === 'boolean') { if (v !== def[k] && code(v)) parts.push(code(v)); return; }
    if (typeof v === 'number' && v === 0) return;
    if (code(v)) parts.push(code(v));
  });
  return parts.join('_').replace(/[^A-Za-z0-9_.-]+/g, '_') + '.csv';
}
/* list in the header picker goes first: all_ / N500_ / NS500_ / NM250_ */
const LIST_CODE = {'all':'all', 'NIFTY 500':'N500', 'Smallcap 500':'NS500', 'Microcap 250':'NM250', 'Others':'OTH'};
const listCode = () => LIST_CODE[GF.uni || 'all'] || String(GF.uni).replace(/[^A-Za-z0-9]+/g, '');
const csvCell = v => {
  if (v == null || (typeof v === 'number' && !isFinite(v))) return '';
  if (typeof v === 'number') v = Math.round(v * 100) / 100;
  const t = String(v);
  return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
};
function downloadCsv(name, cols, data, withIndex) {
  const use = cols.filter(c => !c.noCsv);
  const lines = [(withIndex ? ['#'] : []).concat(use.map(c => c.label)).map(csvCell).join(',')];
  data.forEach((r, i) => lines.push((withIndex ? [i + 1] : []).concat(use.map(c => c.x ? c.x(r) : c.v(r))).map(csvCell).join(',')));
  const blob = new Blob(['\ufeff' + lines.join('\n')], {type:'text/csv;charset=utf-8'});   // BOM so Excel shows ₹ / Δ correctly
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const mcapOk = (s, st) => (s.mcap || 0) >= (+st.minMcap || 0)
  && (st.maxMcap === '' || st.maxMcap == null || +st.maxMcap === 0 || (s.mcap != null && s.mcap <= +st.maxMcap));   // Max Mcap blank / 0 = no cap
const fMcap = st => fNum(st,'minMcap','Min Mcap ₹ Cr',{min:0,step:500,w:90}) + fNum(st,'maxMcap','Max Mcap ₹ Cr',{min:0,step:500,w:100});

/* 1Y back: split the table (in its current sort order) into top / middle / bottom thirds and show each third's
   average price return. Size k = ceil(n / 3), so 10 stocks -> top 4, middle 4, bottom 4 (the middle overlaps). */
function thirds(data, col) {
  const st = view.querySelector('.stats'); if (!st || !$('.grp', st)) return;
  const r = data.filter(s => s.since && ok(s.since.ret));
  const n = r.length, k = Math.ceil(n / 3), b = D.then.bench ? D.then.bench.ret : null;
  const parts = n ? [r.slice(0, k), r.slice(Math.floor((n - k) / 2), Math.floor((n - k) / 2) + k), r.slice(n - k)] : [[], [], []];
  $('.grpby', st).textContent = col ? col.label : 'table order';
  st.querySelectorAll('.grp').forEach((el, i) => {
    const x = parts[i], avg = x.length ? x.reduce((a, s) => a + s.since.ret, 0) / x.length : null;
    $('span', el).textContent = ['Top', 'Mid', 'Low'][i] + (k ? ' ' + k : '') + ', avg';
    $('b', el).innerHTML = signPct(avg);
    el.title = x.map(s => s.sym + ' ' + (s.since.ret > 0 ? '+' : '') + fmt(s.since.ret) + '%').join('\n') + (b != null ? '\nNIFTY 500: ' + fmt(b) + '%' : '');
  });
}
/* one light colour per sector (22), fixed by sector name, so a sector keeps its colour in every table */
const SEC_PAL = ['#FFC1CC','#FFD8B1','#FFF3A6','#D9F99D','#B6F2D0','#A7F3EB','#B3D9FF','#C7D2FE','#E0BBFF','#F3C4FB','#FFD6E7',
                 '#FFB3A7','#F6E3C1','#FFFAC2','#C8E6C9','#D6EFFF','#B3E5E0','#A7C7E7','#D1C4E9','#FFD1B3','#D0E8D0','#E4E7EB'];
const SEC_COLOR = {};
[...new Set(D.stocks.map(s => s.ind).filter(Boolean))].sort().forEach((n, i) => SEC_COLOR[n] = SEC_PAL[i % SEC_PAL.length]);
const secColor = n => SEC_COLOR[n] || null;
const TABLE_ROWS = 15;
const TSCROLL = {};                                       // table sideways scroll, kept while filters re-render the table                                    // visible rows per table, the rest scrolls
function makeTable(id, cols, rows, opt={}) {
  if (BACK) {
    const add = (D.then.hasPrice ? [C.ret] : []).concat(opt.sinceCols || [C.patG, C.revG, C.roeD, C.opmD]);
    cols = cols.flatMap(c => c.k === 'cmp' ? (NOPRICE ? add : [c].concat(add)) : (NOPRICE && PRICE_KEYS.has(c.k) ? [] : [c]));
    id += NOPRICE ? '_back' : '_backpx';
    if (!cols.find(c => c.k === opt.sortKey)) {
      const pg = cols.find(c => c.k === 'patG');
      opt = Object.assign({}, opt, {sortKey: pg ? 'patG' : cols[0].k, sortDir: pg ? -1 : 1});
    }
  }
  const box = document.createElement('div');
  let shown = rows;                                       // rows on screen after search + sort, used by the export
  if (opt.search || opt.csv) {
    const bar = document.createElement('div'); bar.className='bar tbar';
    bar.innerHTML = (opt.search ? '<input type="search" placeholder="Filter by symbol / name / industry…" style="width:280px">' : '')
      + (opt.csv ? `<span class="tacts"><button type="button" class="btn icon csv" title="Download this table as CSV" aria-label="Download CSV">
          <svg width="15" height="15" viewBox="0 0 13 13" fill="none" aria-hidden="true"><path d="M6.5 1v7M6.5 8l-2.5-2.5M6.5 8l2.5-2.5M1.5 10.5h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <button type="button" class="btn icon copy" title="Copy Symbol, Company, Industry, CMP, PE, PB of every row (pastes into Excel)" aria-label="Copy table">
          <svg class="tick" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <svg class="ico" width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="9" rx="1.6" stroke="currentColor" stroke-width="1.5"/><path d="M3.5 10.5h-.4A1.6 1.6 0 0 1 1.5 8.9V3.1c0-.9.7-1.6 1.6-1.6h5.8c.9 0 1.6.7 1.6 1.6v.4" stroke="currentColor" stroke-width="1.5"/></svg></button></span>` : '');
    const inp = $('input', bar);
    if (inp) { inp.value = searchState[id] || ''; inp.addEventListener('input', () => { searchState[id] = inp.value; saveView(); draw(); }); }
    if (opt.csv) $('.csv', bar).addEventListener('click', () => {
      const q = (searchState[id] || '').trim();
      const name = listCode() + '_' + opt.csv().replace(/\.csv$/, '') + (q ? '_q-' + q.replace(/[^A-Za-z0-9]+/g, '') : '') + '.csv';   // search text tagged too
      downloadCsv(name, cols, shown, !opt.noIndex);
    });
    if (opt.csv) $('.copy', bar).addEventListener('click', e => {           // symbols of the rows on screen, in table order
      const btn = e.currentTarget, n2 = v => v == null || !isFinite(v) ? '' : (Math.round(v * 100) / 100).toString();
      const cell = v => String(v ?? '').replace(/[\t\n]+/g, ' ');
      // tab-separated with a header row: pastes straight into Excel / Google Sheets as 6 columns
      const isInd = shown.length && Array.isArray(shown[0].stocks);           // Industries table: one row per industry
      const txt = (isInd
        ? ['Industry\tIndustry PE\tMedian PB\tStocks'].concat(shown.map(g => [g.name, n2(g.pe[method]), n2(g.medPB), g.n].map(cell).join('\t')))
        : ['Symbol\tCompany\tIndustry\tCMP\tPE\tPB'].concat(shown.map(r => [r.sym, r.name, r.ind, n2(r.cmp), n2(r.pe), n2(r.pb)].map(cell).join('\t'))))
        .join('\n');
      const done = ok => { btn.classList.add(ok ? 'ok' : 'bad'); btn.title = ok ? `Copied ${shown.length}` : 'Copy failed';
                           setTimeout(() => { btn.classList.remove('ok', 'bad'); btn.title = 'Copy Symbol, Company, Industry, CMP, PE, PB of every row (pastes into Excel)'; }, 1400); };
      const fallback = () => { const t = document.createElement('textarea'); t.value = txt; t.style.position = 'fixed'; t.style.opacity = '0';
        document.body.appendChild(t); t.select(); let r = false; try { r = document.execCommand('copy'); } catch (x) {} t.remove(); done(r); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(txt).then(() => done(true), fallback); else fallback();
    });
    box.appendChild(bar);
  }
  const card = document.createElement('div'); card.className = 'card';      // table + footer (count, help text)
  const wrap = document.createElement('div'); wrap.className='wrap';
  const tbl = document.createElement('table'); wrap.appendChild(tbl); card.appendChild(wrap);
  box.appendChild(card);
  const st = sortState[id] = sortState[id] || {k: opt.sortKey, dir: opt.sortDir || 1};
  wrap.addEventListener('scroll', () => { TSCROLL[id] = wrap.scrollLeft; if (wrap._more) wrap._more(); }, {passive: true});   // next batch near the bottom
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
    /* rows are drawn in batches (40, then 60 more as the card scrolls): Momentum / All Stocks open at once even on cheap phones.
       Sort, search, CSV and copy still use every row (data / shown). */
    const rowHtml = (r, i) => {
      const sc = secColor(r.ind), t = sc ? ` title="${esc(r.ind)}"` : '';            // hover the strip: sector name
      const cells = cols.map((c, j) => `<td class="${c.l?'l':''}"${j === 0 && opt.noIndex ? t : ''}>${c.f(r)}</td>`).join('');
      return `<tr data-i="${i}"${opt.onRow ? ' class="click"' : ''}${sc ? ` style="--sc:${sc}" data-sec="${esc(r.ind)}"` : ''}>`
        + (opt.noIndex ? '' : `<td class="l idx"${t}>${i+1}</td>`) + cells + '</tr>';
    };
    let drawn = 0;
    const more = () => {
      if (drawn >= data.length) return;
      const end = Math.min(data.length, drawn + (drawn ? 60 : 40));
      let h = ''; for (let i = drawn; i < end; i++) h += rowHtml(data[i], i);
      tb.insertAdjacentHTML('beforeend', h); drawn = end;
    };
    more();
    wrap._more = () => { if (wrap.scrollTop + wrap.clientHeight > wrap.scrollHeight - 400) more(); };
    if (opt.onRow) tb.addEventListener('click', e => { const tr = e.target.closest('tr[data-i]'); if (tr) opt.onRow(data[+tr.dataset.i]); });
    if (!data.length) tb.innerHTML = `<tr><td class="l na" colspan="${cols.length+1}">No stocks match these filters. Loosen a filter or press Reset.</td></tr>`;
    tbl.appendChild(tb);
    shown = data;
    if (BACK && D.then.hasPrice) thirds(data, col);
    (window.requestAnimationFrame || setTimeout)(() => {  // window = header + 15 rows, rest scrolls inside the card
      const r = tb.rows[0];
      if (r && r.offsetHeight) wrap.style.maxHeight = (tbl.tHead.offsetHeight + r.offsetHeight * TABLE_ROWS + 1) + 'px';
      if (TSCROLL[id]) wrap.scrollLeft = TSCROLL[id];
    });
    tbl.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
      const k = th.dataset.k; st.dir = (st.k===k) ? -st.dir : 1; st.k = k; saveView(); draw();
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
const symCell = s => `<a class="sym" href="${esc(s.url || '#')}" data-news="${esc(s.sym)}" title="News and details">${esc(s.sym)}</a>` + psuTag(s);   // opens the news overlay; Screener.in link inside
const bagBtn = s => `<button type="button" class="bag${PF_HELD.has(s.sym) ? ' held' : ''}" data-bag="${esc(s.sym)}" title="Buy / sell ${esc(s.sym)} in your Portfolio" aria-label="Buy or sell ${esc(s.sym)}">${BAG}</button>`;
const indLink = s => bagBtn(s) + `<a class="ind" href="#/industry/${encodeURIComponent(s.ind)}">${esc(s.ind)}</a>`;   // bag left of the industry: add / subtract in Portfolio
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
  x:s => { const v = s.tr ? s.tr.c[k] : null; return v==null ? '' : (v ? 'PASS ' : 'FAIL ') + TR_DETAIL[k](s); },
  f:s => { const v = s.tr ? s.tr.c[k] : null; if (v==null) return NA;
           return `<span class="pill ${v?'disc':'prem'}">${v?'✓':'✗'} ${esc(TR_DETAIL[k](s))}</span>`; }});
const trendOk = (s, min) => min === '' || min == null || (s.tr != null && s.tr.pct >= +min);

const BAG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 7h12l1 14H5L6 7z"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/></svg>';
const C = {
  sym: {k:'sym', label:'Symbol', l:1, v:s=>s.sym, f:symCell},
  name: {k:'name', label:'Company', l:1, v:s=>s.name, f:s=>`<span class="co" title="${esc(s.name)}">${esc(s.name)}</span>`},
  nameBag: {k:'name', label:'Company', l:1, v:s=>s.name, f:s=>bagBtn(s) + `<span class="co" title="${esc(s.name)}">${esc(s.name)}</span>`},   // industry page: no Industry column
  ind: {k:'ind', label:'Industry', l:1, v:s=>s.ind, f:indLink},
  cmp: {k:'cmp', label:'CMP ₹', v:s=>s.cmp, f:s=>fmt(s.cmp,2)},
  patG: {k:'patG', label:'Profit growth since', tip:'TTM profit now vs TTM profit a year earlier', x:s=>sv(s,'patL') || sv(s,'patG'),
         v:s=>sv(s,'patG') ?? (sv(s,'patL')==='turned profitable' ? 1e9 : null),
         f:s=>{const l=sv(s,'patL'); return l ? `<span class="pill ${l==='turned profitable'?'disc':'prem'}">${l}</span>` : pctPill(sv(s,'patG'), v=>v>=0);}},
  ret: {k:'ret', label:'Price return since', tip:'Angel close then → latest close, split / bonus adjusted', v:s=>sv(s,'ret'),
        f:s=>{ const v = sv(s,'ret'), b = D.then && D.then.bench ? D.then.bench.ret : null;
               return v == null ? NA : `<span class="pill ${(b == null ? v >= 0 : v >= b) ? 'disc' : 'prem'}" title="${b == null ? '' : 'NIFTY 500: ' + fmt(b,1) + '%'}">${v > 0 ? '+' : ''}${fmt(v)}%</span>`; }},
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
  mom: {k:'mom', label:'Momentum', tip:'Price momentum 0–100: percentile of the 3M, 6M and 12-minus-1-month returns vs every stock on screen, averaged. Hover a value for the returns.',
        v:s=>s.momS, x:s=>s.momS==null ? '' : Math.round(s.momS),
        f:s=>s.momS==null ? NA : `<span class="meter" title="${esc(['3M', '6M', '12-1M'].map((l, i) => l + ': ' + (ok(s.mom[i]) ? (s.mom[i] > 0 ? '+' : '') + fmt(s.mom[i]) + '%' : '–')).join('\n'))}"><span>${fmt(s.momS, 0)}</span><i><b style="width:${Math.max(3, s.momS).toFixed(0)}%"></b></i></span>`},
  pb: {k:'pb', label:'PB', v:s=>s.pb, f:s=>s.pb==null?'<span class="na">N/A</span>':fmt(s.pb,2)+(s.pbSrc==='derived'?'<span class="na">*</span>':'')},
  cfop: {k:'cfop', label:'CFO+ yrs', tip:'Years with positive operating cash flow, out of the last 10 financial years (fewer if the history is shorter, minimum 5). Not used for financials: their operating cash flow swings with deposits and loans.',
         v:s=>s.cfop ? s.cfop[0] / s.cfop[1] * 10 : null, x:s=>s.cfop ? `${s.cfop[0]}/${s.cfop[1]}` : '',
         f:s=>s.fin ? '<span class="na" title="Not meaningful for banks / NBFCs">fin</span>' : s.cfop ? `<span class="pill ${s.cfop[0] / s.cfop[1] >= 0.9 ? 'disc' : s.cfop[0] / s.cfop[1] < 0.7 ? 'prem' : ''}">${s.cfop[0]}/${s.cfop[1]}</span>` : NA},
  pup: {k:'pup', label:'Profit ↑ yrs', tip:'Years profit (PAT) grew, out of the last 10 financial years (fewer if the history is shorter). Graham: at least 7 of 10.',
        v:s=>s.pup ? s.pup[0] / s.pup[1] * 10 : null, x:s=>s.pup ? `${s.pup[0]}/${s.pup[1]}` : '',
        f:s=>s.pup ? `<span class="pill ${s.pup[0] / s.pup[1] >= 0.7 ? 'disc' : s.pup[0] / s.pup[1] < 0.5 ? 'prem' : ''}">${s.pup[0]}/${s.pup[1]}</span>` : NA},
  q5: {k:'q5', label:'ROCE 5Y med', tip:'Median of the last 5 financial years: ROCE = EBIT ÷ (equity + borrowings); ROE for financials. Shows the whole cycle, not one peak year. Graham screens: ≥ 12%.',
       v:s=>s.q5, f:s=>s.q5==null ? NA : `<span class="pill ${s.q5 >= 12 ? 'disc' : s.q5 < 8 ? 'prem' : ''}">${fmt(s.q5)}</span>` + (s.fin ? ' <span class="na">ROE</span>' : '')},
  debitda: {k:'debitda', label:'Debt ÷ EBITDA', tip:'Borrowings ÷ operating profit (EBITDA), latest financial year; non-financials. Graham screens: below 1.5x. Gross debt (Screener has no cash figure), so it is stricter than net debt ÷ EBITDA.',
            v:s=>s.fin ? null : s.debitda, f:s=>s.fin || s.debitda==null ? NA : `<span class="pill ${s.debitda <= 1.5 ? 'disc' : s.debitda > 3 ? 'prem' : ''}">${s.debitda >= 99 ? 'loss' : fmt(s.debitda, 1) + 'x'}</span>`},
  de: {k:'de', label:'Debt ÷ Equity', tip:'Borrowings ÷ (reserves + share capital), latest financial year; not used for financials. Graham: below 0.5.',
       v:s=>s.fin ? null : s.de, f:s=>s.fin || s.de==null ? (has('de') || s.fin ? NA : '<span class="na" title="Needs the Borrowings rows: filled after the next full scrape with the updated n500 / ns500 / nm250">soon</span>') : `<span class="pill ${s.de <= 0.5 ? 'disc' : s.de > 1 ? 'prem' : ''}">${fmt(s.de, 2)}</span>`},
  roa: {k:'roa', label:'ROA %', tip:'PAT ÷ total assets, latest financial year. Banks / NBFCs: 1.5%+ is strong.', v:s=>s.roa,
        f:s=>s.roa==null ? NA : (s.fin ? `<span class="pill ${s.roa >= 1.5 ? 'disc' : s.roa < 1 ? 'prem' : ''}">${fmt(s.roa, 2)}</span>` : fmt(s.roa, 1))},
  gnpa: {k:'gnpa', label:'Gross NPA %', tip:'Latest quarterly Gross NPA % (banks). Below 2% is clean.', v:s=>s.gnpa,
         f:s=>s.gnpa==null ? NA : `<span class="pill ${s.gnpa <= 2 ? 'disc' : s.gnpa > 4 ? 'prem' : ''}">${fmt(s.gnpa, 2)}</span>`},
  nnpa: {k:'nnpa', label:'Net NPA %', tip:'Latest quarterly Net NPA % (banks). Graham screens: below 1%.', v:s=>s.nnpa,
         f:s=>s.nnpa==null ? NA : `<span class="pill ${s.nnpa <= 1 ? 'disc' : s.nnpa > 2 ? 'prem' : ''}">${fmt(s.nnpa, 2)}</span>`},
  pcr: {k:'pcr', label:'PCR %', tip:'Provision coverage ≈ 1 − Net NPA ÷ Gross NPA (excludes technical write-offs, so a bank\'s reported PCR is usually higher). Graham screens: above 70%.', v:s=>s.pcr,
        f:s=>s.pcr==null ? NA : `<span class="pill ${s.pcr >= 70 ? 'disc' : s.pcr < 50 ? 'prem' : ''}">${fmt(s.pcr, 0)}</span>`},
  pta: {k:'pta', label:'Mcap ÷ Assets', tip:'Market cap ÷ total assets on the latest balance sheet (non-financials). Below 1x = the market values the company at less than everything it owns on its books (before debts). Book values are at cost, so land bought long ago is understated.',
        v:s=>s.pta, f:s=>s.pta==null?NA:`<span class="pill ${s.pta<=1?'disc':s.pta<=3?'':'prem'}" title="Balance sheet ${esc(s.taFy||'')}">${fmt(s.pta,2)}x</span>`},
  pfa: {k:'pfa', label:'Mcap ÷ Fixed assets', tip:'Market cap ÷ (net block + CWIP): plant, buildings and land at book (historical) cost', v:s=>s.pfa,
        f:s=>s.pfa==null?NA:`<span class="pill ${s.pfa<=1?'disc':s.pfa<=3?'':'prem'}">${fmt(s.pfa,2)}x</span>`},
  invp: {k:'invp', label:'Investments % Mcap', tip:'Investments on the balance sheet as % of market cap (holding companies / cash-rich firms score high)', v:s=>s.invp,
         f:s=>s.invp==null?NA:(s.invp>=50?`<b class="up">${fmt(s.invp,0)}%</b>`:fmt(s.invp,0)+'%')},
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
  flags: {k:'flags', label:'Value-trap flags', l:1, v:s=>trapReasons(s).length, x:s=>trapReasons(s).join('; ') || 'clean', f:s=>{
    const r = trapReasons(s); return r.length ? `<span class="pill warn">${esc(r.join(' · '))}</span>` : '<span class="pill disc">clean</span>'; }},
  spark: {k:'spark', label:'EPS trend', noCsv:1, v:s=>null, f:epsSpark},
  trend: {k:'trend', label:'Trend', tip:'improving-fundamentals checks passed (see Methodology, Trend score)', v:s=>s.tr?s.tr.pct+s.tr.of/100:null,
          x:s=>s.tr ? `${s.tr.pass}/${s.tr.of}` : '', f:trendCell},
};

const has = k => S.some(s => s[k] != null);
const safeCols = () => [C.q5, C.pup, C.cfop, C.de, C.debitda, C.roa].concat( has('gnpa') ? [C.gnpa, C.nnpa, C.pcr] : []);
const assetCols = () => [C.pta].concat(has('pfa') ? [C.pfa] : [], has('invp') ? [C.invp] : []);

/* ---------- views ---------- */
const view = $('#view');
/* phone: two menu buttons replace the tab row and the row of screens */
const TABS = [['#/value', 'Value screen', 'Cheap, good-quality stocks by screen'], ['#/momentum', 'Momentum', 'Strongest price trends'],
              ['#/', 'Industries', 'Industry PE, PB, ROE'], ['#/all', 'All Stocks', 'Every stock with every column'], ['#/method', 'Methodology', 'How every number is worked out']];
function syncMnav() {
  const h = location.hash || '#/', t = TABS.find(x => x[0] === h) || (h.startsWith('#/industry/') ? TABS[2] : null);
  $('#mnavTab span').textContent = t ? t[1] : 'Menu';
  const pre = view.querySelectorAll('.fbox .preset'), b = $('#mnavPre');
  b.hidden = !pre.length;
  if (pre.length) { const on = view.querySelector('.fbox .preset.on'); $('span', b).textContent = on ? on.textContent : 'Custom screen'; }
}
function menuSheet(title, items, pick) {
  const w = modal(esc(title), `<div class="mlist">${items.map((it, i) => `<button type="button" data-i="${i}" class="${it.on ? 'on' : ''}"><span>${esc(it.name)}${it.sub ? `<small>${esc(it.sub)}</small>` : ''}</span></button>`).join('')}</div>`, {noFocus: true, sheet: true});
  w.querySelectorAll('.mlist button').forEach(b => b.addEventListener('click', () => { w._close(); pick(items[+b.dataset.i]); }));
}
$('#mnavTab').addEventListener('click', () => {
  const h = location.hash || '#/';
  menuSheet('Go to', TABS.filter(t => !isDemo() || t[0] === '#/all').map(t => ({name: t[1], sub: t[2], href: t[0], on: t[0] === h || (t[0] === '#/' && h.startsWith('#/industry/'))})),
    it => { if (it.href[0] === '#') location.hash = it.href; else window.open(it.href, '_blank', 'noopener'); });   // the builder: its own tab
});
$('#mnavPre').addEventListener('click', () => {
  const pre = [...view.querySelectorAll('.fbox .preset')];
  menuSheet('Screen', pre.map(b => ({name: b.textContent, sub: (b.title || '').replace(/ \(click again to switch off\)$/, ''), btn: b, on: b.classList.contains('on')})),
    it => { if (!it.on) it.btn.click(); });                             // picking the screen already on just closes the menu
});
new MutationObserver(syncMnav).observe(view, {childList: true, subtree: true});
/* the Portfolio button is a switch: on the Portfolio page it goes back to Value screen */
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('#pfBag');
  if (b && (location.hash || '') === '#/portfolio') { e.preventDefault(); location.hash = '#/value'; }
});
function setNav(t){ document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.t===t)); }
function pePicker() {
  const d = document.createElement('div'); d.className='bar';
  if (NOPRICE) return d;
  d.innerHTML = `<label>Industry PE =</label><select>
    <option value="median">Median PE (robust to outliers)</option>
    <option value="mean">Simple average PE</option>
    <option value="weighted">Market-cap weighted PE</option></select>`;
  const sel = $('select', d); sel.value = method;
  sel.addEventListener('change', () => { method = sel.value; lsSet('pemethod', method); route(); });
  return d;
}
const pageTitle = t => view.insertAdjacentHTML('beforeend', `<h2 class="page">${t}</h2>`);
const statTiles = arr => arr.map(([l,v]) => `<div class="stat" data-key="${esc(l)}"><span>${l}</span><b>${v}</b></div>`).join('');
let STAT_ANCHOR = null;                                  // {key, off}: first tile in view + its offset, survives re-renders
function holdStrip(st) {
  const restore = () => {
    if (!STAT_ANCHOR) return;
    if (STAT_ANCHOR.end) { st.scrollLeft = st.scrollWidth; return; }   // was scrolled to the end (Thirds): stay there
    const t = st.querySelector(`[data-key="${(window.CSS && CSS.escape ? CSS.escape(STAT_ANCHOR.key) : STAT_ANCHOR.key)}"]`);
    if (t) st.scrollLeft = t.offsetLeft;
  };
  restore(); (window.requestAnimationFrame || setTimeout)(restore);    // again once the Thirds tiles are filled
  st.addEventListener('scroll', () => {
    const t = [...st.children].find(c => c.offsetLeft + c.offsetWidth / 2 >= st.scrollLeft);   // tile now at the left edge
    STAT_ANCHOR = st.scrollLeft > 2 && t ? {key: t.dataset.key, end: st.scrollLeft >= st.scrollWidth - st.clientWidth - 2} : null;
  }, {passive: true});
}
/* the 1Y snapshot (Industries "1Y ago" columns, Historical 1Y): inside the offline file; on the website it is
   fundamentals_back_1Y.json, fetched the first time Industries or Historical needs it */
let THEN1 = D.then && D.then.stocks ? D.then : null;
let THEN_BY = new Map(THEN1 ? THEN1.stocks.map(s => [s.sym, s]) : []);
/* back files leave out what is the same as today (name, link, industry, lists, PSU): copied back in here */
function fillBack(j) {
  if (!j || !j.stocks || j._filled) return j;
  for (const x of j.stocks) {
    const t = TODAY.get(x.sym); if (!t) continue;
    for (const k of ['name', 'url', 'ind', 'uni', 'psu']) if (x[k] === undefined && t[k] !== undefined) x[k] = t[k];
  }
  j._filled = true; return j;
}
/* extra stock files in this page's own format, added after load (hniMore below): today = fundamentals_<tag>.json,
   each back date = fundamentals_back_<key>_<tag>.json, sparkline = fundamentals_hist_<tag>.json */
const MORE = [];
const jget = u => fetch(u, {cache: 'no-cache'}).then(r => r.ok ? r.json() : Promise.reject(r.status));
const getBack = key => jget('fundamentals_back_' + key + '.json').then(j => !MORE.length || !j || !j.stocks ? j :
  Promise.all(MORE.map(t => jget('fundamentals_back_' + key + '_' + t + '.json').catch(() => null)))
    .then(xs => { xs.forEach(x => { if (x && x.stocks) j.stocks = j.stocks.concat(x.stocks); }); return j; }))
  .then(fillBack);
/* fundamentals_hist.json: every back date in one small file with just the fields the sparkline and the
   "1Y ago" columns read; fetched once. {key: {key, date, hasPrice, stocks}} or null (old build / offline file) */
let HIST_P = null;
const loadHist = () => HIST_P = HIST_P || Promise.all(['fundamentals_hist.json', ...MORE.map(t => 'fundamentals_hist_' + t + '.json')]
    .map(u => fetch(u).then(r => r.ok ? r.json() : null).catch(() => null))).then(([j, ...xs]) => {
  if (!j || !j.cols || !j.dates) return null;
  xs.forEach(x => { if (x && x.dates && String(x.cols) === String(j.cols))
    for (const k in x.dates) if (j.dates[k]) j.dates[k].rows = j.dates[k].rows.concat(x.dates[k].rows); });
  const out = {};
  for (const k in j.dates) {
    const d = j.dates[k];
    out[k] = {key: k, date: d.date, hasPrice: d.hasPrice, light: true, stocks: d.rows.map(r => {
      const o = {}; j.cols.forEach((c, i) => { if (r[i] != null) o[c] = r[i]; });
      if (o.tr) o.tr = {pass: o.tr[0], of: o.tr[1], d: {proD: o.tr[2]}};
      return o; })};
  }
  return out;
});
function ensureThen1() {
  if (THEN1 || ensureThen1.p || !D.then) return;
  const key = D.then.key || '1Y';
  const full = () => getBack(key).then(j => { BACK_CACHE[key] = j; return j; });
  ensureThen1.p = loadHist().then(H => H && H[key] ? H[key] : full())   // the small file has PE / PB / ROE: enough here
    .then(j => { THEN1 = j; THEN_BY = new Map(j.stocks.map(s => [s.sym, s]));
                 const h = location.hash || '#/'; if (!BACK && (h === '#/' || h === '#/portfolio' || h.startsWith('#/industry/'))) route(); })
    .catch(() => { ensureThen1.p = null; });
}
/* same stocks, 1 year ago vs now: medians over stocks that have the value at BOTH dates (listed < 1Y = ignored) */
function yoyOf(rows) {
  const both = rows.map(s => [s, THEN_BY.get(s.sym)]).filter(x => x[1]);
  const pair = (fa, okv) => { const p = both.map(([n, t]) => [fa(t), fa(n)]).filter(([a, b]) => okv(a) && okv(b));
    return p.length ? [median(p.map(q => q[0])), median(p.map(q => q[1])), p.length] : null; };   // median: one PE of 900 can't swing it
  const pos = v => ok(v) && v > 0, px = THEN1 && THEN1.hasPrice;
  return {n: both.length, pe: px ? pair(s => s.pe, pos) : null, pb: px ? pair(s => s.pb, pos) : null, roe: pair(s => s.roe, ok)};
}
const yoyPct = r => r ? (r[1] / r[0] - 1) * 100 : null;          // PE / PB change, %
const yoyPP = r => r ? r[1] - r[0] : null;                       // ROE change, pp
function yoyTiles(rows) {                                 // current view only: these stocks a year ago vs now
  if (!BACK && !THEN_BY.size) ensureThen1();
  if (BACK || !rows || !THEN_BY.size) return '';
  const Y = yoyOf(rows), both = {length: Y.n};
  const tile = (label, r, d, pp) => {
    if (!r) return '';
    const ch = pp ? r[1] - r[0] : (r[1] / r[0] - 1) * 100;
    return `<div class="stat" data-key="yoy${label}" title="Median of the ${r[2]} stocks with a value at both dates">`
      + `<span>${label}, 1Y ago → now</span><b>${fmt(r[0], d)} → ${fmt(r[1], d)}<small class="${ch >= 0 ? 'up' : 'dn'}">${ch > 0 ? '+' : ''}${fmt(ch, 1)}${pp ? ' pp' : '%'}</small></b></div>`;
  };
  const html = tile('Median PE', Y.pe, 1) + tile('Median PB', Y.pb, 2) + tile('Median ROE %', Y.roe, 1, true);
  return html ? `<div class="stat cap" data-key="yoy" title="Stocks in this list that also existed 1 year ago (${both.length} of ${rows.length}); newer listings are left out. 1Y-ago values are rebuilt from the results published by then.">`
    + `<span>vs 1Y ago</span><b>${both.length} stocks</b></div>${html}` : '';
}
const stats = (arr, rows, extra) => {                            // ONE compact strip: list stats, then (1Y back) results since that date
  const p = perfStats(rows);
  view.insertAdjacentHTML('beforeend', `<div class="stats">${statTiles(arr)}` + (extra || '')
    + (p.length ? `<div class="stat cap" data-key="since"><span>Since</span><b>${esc(D.then.date)}</b></div>${statTiles(p)}` : '')
    + (BACK && D.then.hasPrice ? `<div class="stat cap grpcap" data-key="grpcap" title="The table below split into top / middle / bottom thirds in its current order (click a column to re-rank); average price return of each since the back date">`
        + `<span>Thirds by</span><b class="grpby">–</b></div>`
        + ['Top', 'Mid', 'Low'].map((n, i) => `<div class="stat grp" data-g="${i}" data-key="grp${i}"><span>${n}</span><b>–</b></div>`).join('') : '')
    + '</div>');
  const all = view.querySelectorAll('.stats'); holdStrip(all[all.length - 1]);
};
const hint = () => {};                                   // no help text under tables; it all lives in Methodology

const cheapest = g => g.stocks.filter(s=>s.pe!=null && vis(s)).reduce((a,b)=>!a||b.pe<a.pe?b:a, null);
function homeView() {
  setNav('home'); view.innerHTML = '';
  view.appendChild(pePicker());
  if (!BACK && !THEN_BY.size) ensureThen1();                  // 1Y columns appear when the snapshot arrives
  const showYoy = !BACK && THEN_BY.size;
  if (showYoy) Object.values(industries).forEach(g => g.yoy = yoyOf(g.stocks.filter(vis)));
  const yTip = 'Median of the stocks in this industry that existed 1 year ago (newer listings ignored), value at both dates';
  const cols = [
    {k:'name', label:'Industry', l:1, v:g=>g.name, f:g=>`<b>${esc(g.name)}</b>`},
    {k:'pe', label:'Industry PE', v:g=>g.pe[method], f:g=>`<b>${fmt(g.pe[method])}</b>`},
    {k:'pb', label:'Median PB', v:g=>g.medPB, f:g=>fmt(g.medPB,2)},
    {k:'roe', label:'Median ROE %', v:g=>g.medROE, f:g=>fmt(g.medROE)},
    {k:'dy', label:'Median div yield %', v:g=>g.medDY, f:g=>fmt(g.medDY,2)},
    ...(showYoy ? [
      {k:'pe1', label:'PE 1Y ago', tip:yTip, v:g=>g.yoy.pe && g.yoy.pe[0], f:g=>g.yoy.pe ? fmt(g.yoy.pe[0]) : NA},
      {k:'peD', label:'PE Δ 1Y', tip:yTip, v:g=>yoyPct(g.yoy.pe), f:g=>pctPill(yoyPct(g.yoy.pe), v=>v<=0)},      // cheaper = green
      {k:'pbD', label:'PB Δ 1Y', tip:yTip, v:g=>yoyPct(g.yoy.pb), f:g=>pctPill(yoyPct(g.yoy.pb), v=>v<=0)},
      {k:'roeD1', label:'ROE Δ 1Y', tip:yTip, v:g=>yoyPP(g.yoy.roe), f:g=>ppPill(yoyPP(g.yoy.roe), true)}] : []),
    ...(BACK ? [{k:'patG', label:'Median profit growth since', v:g=>medSince(g.stocks.filter(vis),'patG'), f:g=>pctPill(medSince(g.stocks.filter(vis),'patG'), v=>v>=0)},
                {k:'roeD', label:'Median ROE Δ', v:g=>medSince(g.stocks.filter(vis),'roeD'), f:g=>ppPill(medSince(g.stocks.filter(vis),'roeD'), true)}] : []),
    {k:'n', label:'Stocks', v:g=>g.n, f:g=>g.n},
    {k:'nPE', label:'With valid PE', v:g=>g.nPE, f:g=>g.nPE},
    {k:'mcap', label:'Total Mcap ₹ Cr', v:g=>g.mcap, f:g=>fmt(g.mcap,0)},
    {k:'cheap', label:'Lowest-PE stock', l:1, v:g=>{const c=cheapest(g); return c?c.pe:null;}, x:g=>{const c=cheapest(g); return c?`${c.sym} (PE ${fmt(c.pe)})`:'';}, f:g=>{const c=cheapest(g); return c?`${esc(c.sym)}${psuTag(c)} <span class="na">PE ${fmt(c.pe)}</span>`:NA;}},
  ];
  const rows = Object.values(industries).map(g => Object.assign(g, {sym:g.name, ind:g.name}));
  view.appendChild(makeTable('home', cols, rows, {sortKey:'pe', sortDir:1, search:true, csv:() => 'Industries.csv',
    onRow: g => location.hash = '#/industry/' + encodeURIComponent(g.name), noIndex:true}));
  hint('Click an industry to see its stocks. Stocks with negative / missing PE are excluded from industry PE. The CSV has only broad sector buckets, so industry PE is a rough benchmark.');
}

function industryView(name) {
  const g = industries[name]; setNav('home'); view.innerHTML = '';
  if (!g) { view.innerHTML = '<p>Industry not found.</p>'; return; }
  view.insertAdjacentHTML('beforeend', `<a class="back-link" href="#/">&#8592; All industries</a><h2 class="page">${esc(g.name)}</h2>`);
  if (NOPRICE) stats([['Stocks', g.n], ['Median ROE then', fmt(g.medROE) + '%']], g.stocks.filter(vis));
  else stats([['Industry PE (selected)', fmt(g.pe[method])], ['Median PE', fmt(g.pe.median)], ['Average PE', fmt(g.pe.mean)],
         ['Cap-weighted PE', fmt(g.pe.weighted)], ['Median PB', fmt(g.medPB,2)], ['Stocks', g.n]], g.stocks.filter(vis), yoyTiles(g.stocks.filter(vis)));
  view.appendChild(pePicker());
  view.appendChild(makeTable('ind', [C.sym, C.nameBag, C.cmp, C.pe, C.cpe, C.prem, C.pb, ...assetCols(), C.score, C.q, C.cagr, C.fcfy, C.dy, C.proChg, C.mcap, C.from52],
    g.stocks.filter(vis), {sortKey:'pe', sortDir:1, search:true, csv:() => csvName('Industry_' + g.name, {}, {}, [])}));
  hint('Sorted by PE, lowest first. Green = PE below industry PE (discount), red = above (premium).');
}

/* ---------- smart filter box: screen presets + main fields + collapsible "More filters" / extra panel ---------- */
const PANEL_OPEN = {};                                    // which panels are expanded, remembered while the page is open
/* every field = caption above, control below, so the grid lines up on laptop and phone */
const fNum = (st, k, label, o={}) => `<label class="fld"><span title="${esc(label)}">${label}</span><input type="number" data-f="${k}"${o.min!=null?` min="${o.min}"`:''}${o.max!=null?` max="${o.max}"`:''} step="${o.step||1}" value="${st[k]}" placeholder="any"></label>`;
const fChk = (st, k, label) => `<label class="fld chk"><input type="checkbox" data-f="${k}" ${st[k]?'checked':''}><span>${label}</span></label>`;
const fSel = (st, k, label, opts) => `<label class="fld"><span>${label}</span><select data-f="${k}">${opts.map(([v,t]) => `<option value="${v}" ${st[k]===v?'selected':''}>${t}</option>`).join('')}</select></label>`;
const DROP = {};                                          // which filter dropdown is open (+ its scroll), kept across re-renders
const isPhone = () => matchMedia('(max-width:700px)').matches;
/* the dropdown opens BELOW the stats strip, so the list's numbers (returns etc.) stay in view while filtering */
function placeDrop(f) {
  const drop = $('.fdrop', f), back = $('.fback', f);
  let st = f.nextElementSibling;
  while (st && !st.classList.contains('stats')) st = st.nextElementSibling;
  const fr = f.getBoundingClientRect(), sb = st ? st.getBoundingClientRect().bottom : fr.bottom;
  if (isPhone()) {                                        // bottom sheet: placed by CSS
    drop.style.top = ''; back.style.top = ''; drop.style.maxHeight = '';
  } else {
    drop.style.top = (sb - fr.top + 6) + 'px'; back.style.top = ''; drop.style.maxHeight = '';
  }
}
const closeDrops = () => { document.querySelectorAll('.fbox.dopen').forEach(f => f.classList.remove('dopen')); for (const k in DROP) DROP[k].open = false; document.body.classList.remove('fmodal'); };
/* memory mode (Value screen): the screen opens on its default (Deep value) unless the user pressed Save;
   changes work at once but are kept only for this visit until Save; Default (after a warning) forgets them */
const FMEM = {}, FSESS = {};                               // FSESS: other tabs' filters, kept for this visit only
function fmem(key, def, base) {
  if (!FMEM[key]) {
    let saved = null; try { saved = JSON.parse(lsGet(key + '_saved') || 'null'); } catch (e) {}
    FMEM[key] = {base, saved, live: Object.assign({}, def, saved || base)};
  }
  return FMEM[key];
}
function filterBox(o, rerender) {
  const {key, def} = o;
  const M = o.memory ? fmem(key, def, Object.assign({}, def, o.memory)) : null;
  // every filter box opens on its predefined values after a page refresh: changes last only for this visit
  // (Value screen: unless you pressed Save there)
  const put = obj => M ? (M.live = Object.assign({}, def, obj)) : (FSESS[key] = Object.assign({}, def, obj));
  const st = M ? Object.assign({}, M.live) : Object.assign({}, def, FSESS[key] || {});
  const dirty = M && JSON.stringify(M.live) !== JSON.stringify(Object.assign({}, def, M.saved || M.base));
  const presets = (o.presets || []).filter(p => !NOPRICE || !p.price);  // price-based screens need a price for this date
  const unset = v => v === '' || v == null || v === 0;                 // blank box and 0 both mean "no filter"
  const same = (a, b) => (unset(a) && unset(b)) || String(a) === String(b);
  const isOn = p => Object.entries(Object.assign({}, def, p.st)).every(([k, v]) => same(st[k], v));
  const active = presets.find(isOn);
  const moreHtml = o.more ? o.more(st) : '';
  const nOn = Object.keys(def).filter(k => !same(st[k], def[k])).length;  // changes from default, shown on the button
  const D0 = DROP[key] = DROP[key] || {open:false, top:0};
  const f = document.createElement('div'); f.className = 'fbox' + (D0.open ? ' dopen' : '');
  f.innerHTML = `<div class="fbar"><div class="pchips">`
      + presets.map((p, i) =>
        `<button type="button" class="preset${p === active ? ' on' : ''}" data-p="${i}" aria-pressed="${p === active}" title="${esc((p.tip || '') + (p === active ? ' (click again to switch off)' : ''))}">${esc(p.name)}</button>`).join('')
      + (active || !nOn ? '' : '<span class="preset-custom">Custom</span>') + `</div>`
      + `<button type="button" class="btn fbtn" aria-expanded="${D0.open}" title="All filters, scoring and reset">`
      + `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 3.5h12M4.5 8h7M7 12.5h2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`
      + `Filters${nOn ? ` <b class="cnt">${nOn}</b>` : ''}</button></div>`
    + `<div class="fback"></div><div class="fdrop" role="dialog" aria-label="Filters"><div class="fdin">`
    + `<div class="fdhead"><span class="t">Filters</span><span class="fbtns">`
    + (o.extra ? `<button type="button" class="btn ghost" data-tog="extra">${o.extra.label} <span class="car">▾</span></button>` : '')
    + (M ? `<button type="button" class="btn ghost${dirty ? ' save-on' : ''}" data-save ${dirty ? '' : 'disabled'} title="Remember these filters on this device">Save</button>`
         + `<button type="button" class="btn ghost" data-reset title="Back to the default screen (Deep value)">Default</button>`
         : `<button type="button" class="btn ghost" data-reset title="Back to defaults">Reset</button>`)
    + `<button type="button" class="btn ghost done" data-close>Done</button></span></div>`
    + (o.extra ? `<div class="fpanel" data-panel="extra"></div>` : '')
    + `<div class="fgrid">${o.main(st)}</div>`
    + (moreHtml ? `<div class="fsub">More filters</div><div class="fgrid">${moreHtml}</div>` : '')
    + `</div></div>`;
  if (o.extra) o.extra.build($('[data-panel="extra"]', f));
  const drop = $('.fdrop', f);
  const setOpen = v => {
    if (v) { closeDrops(); D0.top = null; }                         // opens at half height
    D0.open = v; f.classList.toggle('dopen', v); $('.fbtn', f).setAttribute('aria-expanded', v);
    if (v) { placeDrop(f); sheetScroll(drop, $('.fdin', f), () => setOpen(false), D0); }
    document.body.classList.toggle('fmodal', v && isPhone());   // phone: floating window, page locked
  };
  $('.fbtn', f).addEventListener('click', () => setOpen(!D0.open));
  $('[data-close]', f).addEventListener('click', () => setOpen(false));
  drop.addEventListener('scroll', () => { D0.top = drop.scrollTop; });
  drop.addEventListener('click', e => { if (e.target === drop && isPhone()) setOpen(false); });   // phone: tap above the sheet
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
    put(st); rerender();                                                 // dropdown stays open (DROP) while you tweak
  }));
  f.querySelectorAll('[data-p]').forEach(b => b.addEventListener('click', () => {
    const p = presets[+b.dataset.p];
    put(p === active ? {} : Object.assign({}, def, p.st));                // click the active screen again = switch it off
    rerender();
  }));
  if (M) {
    const sv = $('[data-save]', f);
    sv.addEventListener('click', () => { M.saved = Object.assign({}, M.live); lsSet(key + '_saved', JSON.stringify(M.saved)); rerender(); toast('Filters saved. They open like this next time.'); });
    $('[data-reset]', f).addEventListener('click', () => {
      const w = modal('Back to default?', `<p>Every filter goes back to the default screen (<b>Deep value</b>) and your saved settings are forgotten.</p>
        <div class="pf-btns" style="margin-top:14px"><button type="button" class="btn ghost" data-no>Cancel</button><button type="button" class="btn primary danger" data-yes>Set to default</button></div>`);
      $('[data-no]', w).addEventListener('click', () => w._close());
      $('[data-yes]', w).addEventListener('click', () => { M.saved = null; lsSet(key + '_saved', ''); M.live = Object.assign({}, M.base); w._close(); rerender(); });
    });
  } else $('[data-reset]', f).addEventListener('click', () => { FSESS[key] = {}; rerender(); });
  view.appendChild(f);
  const on = $('.preset.on', f), pc = $('.pchips', f);               // phone: swipe row starts at the active screen
  if (on && pc.scrollWidth > pc.clientWidth) pc.scrollLeft = on.offsetLeft - pc.offsetLeft - 8;
  if (D0.open) {                                          // re-rendered while open: same place, same scroll
    (window.requestAnimationFrame || setTimeout)(() => { placeDrop(f); if (isPhone()) sheetScroll(drop, $('.fdin', f), () => setOpen(false), D0); else drop.scrollTop = D0.top; });   // stats strip is added after this box
    if (isPhone()) document.body.classList.add('fmodal');
  }
  return st;
}
/* any click outside the dropdown and its Filters button closes it, BEFORE that click does anything else
   (capture phase), so a Screen bubble, header chip or table click never reopens it on re-render */
document.addEventListener('click', e => {
  if (!document.querySelector('.fbox.dopen')) return;
  const t = e.target;
  if (t.closest && (t.closest('.fdrop') || t.closest('.fbtn') || t.closest('#ffab'))) return;
  closeDrops();
}, true);
addEventListener('resize', () => { const f = document.querySelector('.fbox.dopen'); if (f) placeDrop(f); });
/* phone: floating Filters button (bottom-right) opens the same filter window as the page's Filters button */
const FAB = $('#ffab');
function syncFab() {                                       // shown only on tabs that have filters; badge = filters changed
  const b = view.querySelector('.fbox .fbtn');
  FAB.classList.toggle('show', !!b);
  const c = b && b.querySelector('.cnt');
  FAB.querySelector('.cnt').textContent = c ? c.textContent : '';
}
FAB.addEventListener('click', () => { const b = view.querySelector('.fbox .fbtn'); if (b) b.click(); });
new MutationObserver(syncFab).observe(view, {childList:true, subtree:true});
let fabScrollT = 0, fabHoldT = 0;
const fabDim = on => FAB.classList.toggle('dim', on);
addEventListener('scroll', () => {                         // fades while the page (or a table) scrolls, back 0.6 s after it stops
  fabDim(true); clearTimeout(fabScrollT); fabScrollT = setTimeout(() => { if (!fabHoldT) fabDim(false); }, 600);
}, {passive:true, capture:true});
addEventListener('touchstart', e => {                      // finger held anywhere else for 1 s → fade
  if (e.target.closest && e.target.closest('#ffab')) return;
  clearTimeout(fabHoldT); fabHoldT = setTimeout(() => fabDim(true), 1000);
}, {passive:true});
const fabRelease = () => { clearTimeout(fabHoldT); fabHoldT = 0; clearTimeout(fabScrollT); fabScrollT = setTimeout(() => fabDim(false), 600); };
addEventListener('touchend', fabRelease, {passive:true});
addEventListener('touchcancel', fabRelease, {passive:true});
const pageSearch = () => view.querySelector('.tbar input[type=search]');
const typingIn = t => t && (t.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.querySelector('.fbox.dopen')) { closeDrops(); return; }
    if (document.querySelector('.fsel.open, .pop.open')) return;            // Esc closes those first
    const q = pageSearch();
    if (q && (document.activeElement === q || q.value)) {                   // Esc: clear the search, cursor off
      if (q.value) { q.value = ''; q.dispatchEvent(new Event('input')); }
      q.blur(); return;
    }
    if ((location.hash || '').startsWith('#/industry/')) location.hash = '#/';  // industry page -> All industries
    return;
  }
  // any letter / digit typed while not in a field goes straight into the page's search box
  if (e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey || typingIn(e.target) || document.querySelector('.fbox.dopen')) return;
  if (e.key === ' ' && !(pageSearch() && pageSearch().value)) return;       // space alone still scrolls the page
  const q = pageSearch(); if (!q) return;
  e.preventDefault();
  q.focus(); q.value += e.key; q.dispatchEvent(new Event('input'));
});

/* ---------- VALUE SCREEN ---------- */
const unsetF = v => v === '' || v == null;
/* Graham safety: debt (non-financials), 10-year profit record (all), ROA + Gross NPA (financials).
   A test whose data isn't scraped yet (no stock has it) is skipped, never silently failing everyone. */
function safeOk(s, f) {
  if (!s.fin && !unsetF(f.minCfo) && !(s.cfop && s.cfop[0] / s.cfop[1] * 10 >= f.minCfo - 1e-9)) return false;
  if (!unsetF(f.minQ5)) {                                 // 5Y median; until a category has it (non-financials need Borrowings) use this year's ROCE
    const has5 = S.some(x => x.fin === s.fin && x.q5 != null);
    const v = has5 ? s.q5 : qual(s);
    const proxy = !has5 && !s.fin && s.roceSrc === 'ta';  // EBIT ÷ total assets reads ~30% low: don't judge on it
    if (!proxy && !(v != null && v >= f.minQ5)) return false;
  }
  if (!s.fin && has('de') && (!unsetF(f.maxDe) || !unsetF(f.maxDebitda))) {   // leverage: debt ÷ equity OR debt ÷ EBITDA passes
    const okDe = !unsetF(f.maxDe) && s.de != null && s.de <= f.maxDe;
    const okEb = !unsetF(f.maxDebitda) && s.debitda != null && s.debitda <= f.maxDebitda;
    if (!okDe && !okEb) return false;
  }
  if (!unsetF(f.minUp) && !(s.pup && s.pup[0] / s.pup[1] * 10 >= f.minUp - 1e-9)) return false;
  if (s.fin && !unsetF(f.minRoa) && !(s.roa != null && s.roa >= f.minRoa)) return false;
  if (s.fin && s.gnpa != null) {                          // banks (NBFCs have no NPA rows on Screener: judged on ROA)
    if (!unsetF(f.maxNpa) && s.gnpa > f.maxNpa) return false;
    if (!unsetF(f.maxNnpa) && s.nnpa != null && s.nnpa > f.maxNnpa) return false;
    if (!unsetF(f.minPcr) && s.pcr != null && s.pcr < f.minPcr) return false;
  }
  return true;
}
const VF_DEF = {maxPE:'', maxPB:'', minQ:12, minScore:0, minFcf:'', minMcap:0, maxMcap:'', trap:true, graham:false, minTrend:'',
                maxPrem:'', maxPeg:'', minDy:0.01, minCc:'', maxPta:'', maxPfa:'', minInv:'', maxDe:'', minUp:'', maxNpa:'', minRoa:'', minQ5:'', maxDebitda:'', maxNnpa:'', minPcr:'', minCfo:'', minMom:40};
const GRAHAM_SAFE = {minQ5:12, minUp:7, maxDe:1, maxDebitda:2.5, minRoa:1.5, maxNpa:2.5, maxNnpa:1, minPcr:70};   // Graham's safety tests, used by both Graham screens
const VF_PRESETS = [
  {name:'Asset-backed', price:1, tip:'Market cap ≤ total assets on the books (Mcap ÷ Assets ≤ 1x), PB ≤ 1.5, ROCE ≥ 10%, debt ÷ equity ≤ 0.5 (assets not bought with borrowed money), pays a dividend, no value traps', st:{maxPta:1, maxPB:1.5, minQ:10, maxDe:0.5}},
  {name:'Graham defensive', price:1, tip:'PE × PB ≤ 22.5, pays a dividend, 5-year median ROCE (ROE for financials) ≥ 12%, profit up in 7 of the last 10 years, debt ÷ equity < 1 or debt ÷ EBITDA < 2.5x; banks: Gross NPA < 2.5%, Net NPA < 1%, PCR > 70%; financials: ROA ≥ 1.5%; cash conversion ≥ 0.5x and no equity dilution (value-trap filter)', st:{graham:true, minQ:'', ...GRAHAM_SAFE}},
  {name:'Graham deep value', price:1, tip:'Graham defensive + PE ≤ 15, PB ≤ 2', st:{graham:true, maxPE:15, maxPB:2, minQ:'', ...GRAHAM_SAFE}},
  {name:'Deep value', price:1, tip:'PE ≤ 15, PB ≤ 2, ROCE ≥ 10%, pays a dividend, no value traps', st:{maxPE:15, maxPB:2, minQ:10}},
];
/* filter fields shared by Value screen and All Stocks */
const vfMain = st => (NOPRICE ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fNum(st,'minQ','Min ROCE / ROE %')
  + fNum(st,'minScore','Min score',{min:0,max:100,step:5}) + fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10})
  + (NOPRICE ? '' : fNum(st,'minMom','Min momentum',{min:0,max:100,step:5}))
  + fChk(st,'trap','Hide value traps');
const vfMore = st => (NOPRICE ? '' : fNum(st,'maxPB','Max PB',{min:0,step:0.5}) + fNum(st,'maxPrem','Max vs industry PE %',{step:5})
  + fNum(st,'maxPeg','Max PEG',{min:0,step:0.25}) + fNum(st,'minFcf','Min FCF yield %',{step:0.5})
  + fNum(st,'minDy','Min div yield %',{min:0,step:0.01}))
  + fNum(st,'minCc','Min cash conv. x',{step:0.1})
  + fNum(st,'minQ5','Min ROCE 5Y median %',{min:0,step:1})
  + fNum(st,'minUp','Min profit-up yrs (of 10)',{min:0,max:10,step:1})
  + fNum(st,'minCfo','Min CFO-positive yrs (of 10)',{min:0,max:10,step:1})
  + fNum(st,'maxDe','Max debt ÷ equity',{min:0,step:0.1}) + fNum(st,'maxDebitda','…or max debt ÷ EBITDA x',{min:0,step:0.25})
  + fNum(st,'minRoa','Financials: min ROA %',{min:0,step:0.25})
  + (has('gnpa') ? fNum(st,'maxNpa','Banks: max Gross NPA %',{min:0,step:0.5}) + fNum(st,'maxNnpa','Banks: max Net NPA %',{min:0,step:0.25})
      + fNum(st,'minPcr','Banks: min PCR %',{min:0,max:100,step:5}) : '')
  + (NOPRICE ? '' : fNum(st,'maxPta','Max Mcap ÷ Assets x',{min:0,step:0.25})
      + (has('pfa') ? fNum(st,'maxPfa','Max Mcap ÷ Fixed assets x',{min:0,step:0.25}) : '')
      + (has('invp') ? fNum(st,'minInv','Min Investments % Mcap',{min:0,step:10}) : ''))
  + fMcap(st)
  + (NOPRICE ? '' : fChk(st,'graham','Graham: PE×PB ≤ 22.5'));
const vfMoreKeys = () => NOPRICE ? ['minCc','minQ5','minUp','minCfo','maxDe','maxDebitda','minRoa','maxNpa','maxNnpa','minPcr','minMcap','maxMcap']
  : ['maxPB','maxPrem','maxPeg','minFcf','minDy','minCc','minQ5','minUp','minCfo','maxDe','maxDebitda','minRoa','maxNpa','maxNnpa','minPcr','maxPta','maxPfa','minInv','minMcap','maxMcap','graham'];
const VF_CSV_KEYS = () => NOPRICE ? ['minMcap','maxMcap','minQ','minScore','minTrend','minCc','minQ5','minUp','minCfo','maxDe','maxDebitda','minRoa','maxNpa','maxNnpa','minPcr','trap']
  : ['minMcap','maxMcap','maxPE','minQ','minScore','minTrend','minMom','maxPB','maxPrem','maxPeg','minFcf','minDy','minCc','minQ5','minUp','minCfo','maxDe','maxDebitda','minRoa','maxNpa','maxNnpa','minPcr','maxPta','maxPfa','minInv','trap','graham'];
/* one stock against one filter set; a blank box = no filter, and a missing value fails a set filter */
function passVF(s, f) {
  const on = k => !unsetF(f[k]) && !(typeof f[k] === 'number' && f[k] === 0 && k !== 'maxPE');
  const max = (k, v) => !on(k) || (v != null && isFinite(v) && v <= f[k]);
  const min = (k, v) => !on(k) || (v != null && isFinite(v) && v >= f[k]);
  return (NOPRICE || (max('maxPE', s.pe) && max('maxPB', s.pb) && max('maxPrem', prem(s)) && max('maxPeg', peg(s))
          && min('minDy', s.dy) && (s.fin || min('minFcf', s.fcfy)) && max('maxPta', s.pta)
          && (!has('pfa') || max('maxPfa', s.pfa)) && (!has('invp') || min('minInv', s.invp))
          && (!HAS_MOM || min('minMom', s.momS)) && (!f.graham || grahamPass(s))))   // a momentum filter set: no momentum ("–") never passes
    && min('minQ', qual(s)) && (s.fin || min('minCc', s.cc)) && min('minScore', s.score)
    && safeOk(s, f) && mcapOk(s, f) && (!f.trap || !trapReasons(s).length) && trendOk(s, f.minTrend);
}

function valueView() {
  setNav('value'); view.innerHTML = '';
  const vf = filterBox({
    key:'vf', def:VF_DEF, presets:VF_PRESETS, memory: VF_PRESETS.find(p => p.name === 'Deep value').st,   // opens on Deep value unless saved
    main: vfMain, more: vfMore, moreKeys: vfMoreKeys(),
    extra: {label:'Scoring', build: el => {                 // industry PE method + score weights, out of the way
      const W = lsJson('vw', W_DEF);
      el.appendChild(pePicker());
      el.insertAdjacentHTML('beforeend', '<div class="fsub">Score weights</div><div class="fgrid">' + Object.keys(W_DEF).filter(k => !NOPRICE || BACK_KEYS.includes(k)).map(k =>
        `<label class="fld"><span>${W_LABEL[k]}</span><input type="number" data-w="${k}" min="0" max="100" step="5" value="${W[k]}"></label>`).join('')
        + '</div><button type="button" class="btn ghost" data-wr>Reset weights</button>');
      el.querySelectorAll('[data-w]').forEach(i => i.addEventListener('change', () => {
        W[i.dataset.w] = Math.max(0, +i.value || 0); lsSet('vw', JSON.stringify(W)); route();
      }));
      $('[data-wr]', el).addEventListener('click', () => { lsSet('vw', '{}'); route(); });
    }},
  }, valueView);

  const rows = V().filter(s => s.score != null && passVF(s, vf));               // Trend is a filter here, never part of the score                                       // Trend is a filter here, never part of the score
  if (NOPRICE) stats([['Passing', rows.length], ['Scored', V().filter(s=>s.score!=null).length]], rows);
  else stats([['Passing', rows.length], ['Scored', V().filter(s=>s.score!=null).length],
         ['Median PE', fmt(median(rows.map(s=>s.pe)))], ['Median PB', fmt(median(rows.map(s=>s.pb)),2)],
         ['Graham pass', rows.filter(grahamPass).length]], rows);
  view.appendChild(makeTable('value',
    [C.sym, C.ind, C.score, C.name, C.mom, C.trend, C.cmp, C.pe, C.cpe, C.prem, C.pb, ...safeCols(), ...assetCols(), C.q, C.cagr, C.peg, C.fcfy, C.cc, C.opmT, C.gup, C.dy, C.proChg, C.mcap, C.flags, C.spark],
    rows, {sortKey:'score', sortDir:-1, search:true, csv:() => csvName('Value_screen', vf, VF_DEF,
      VF_CSV_KEYS())}));
  if (NOPRICE) hint('1 year back (no prices.csv yet) the score uses only the quality metrics as they stood then (ROCE / ROE, EPS growth, cash conversion), ranked within each industry. Green / red "since" columns show what the business did after that. Mcap filter uses today\'s market cap.');
  else hint('Value score = weighted percentile rank within the stock\'s own industry (hover a score for each part). Screens are one-click presets; any change turns them into Custom. FCF yield, cash conversion and core PE are not used for financials. Orange core PE = over 20% of profit is other income.');
}

/* ---------- MAGIC FORMULA ---------- */
const MF_DEF = {exFin:true, minMcap:1000, maxMcap:'', top:50, trap:false, minTrend:''};
const MF_PRESETS = [
  {name:'Classic', tip:'Greenblatt as published: ex-financials, Mcap ≥ ₹1,000 Cr, top 30, nothing else', st:{top:30}},
  {name:'Magic + safety', tip:'Classic, then drop stocks with value-trap flags', st:{top:30, trap:true}},
  {name:'Magic + improving', tip:'Classic + no value traps + Trend ≥ 70%', st:{top:30, trap:true, minTrend:70}},
];
function magicView() {
  setNav('magic'); view.innerHTML = '';
  const mf = filterBox({
    key:'mf', def:MF_DEF, presets:MF_PRESETS,
    main: st => fChk(st,'exFin','Exclude financials') + fNum(st,'top','Show top',{min:5,max:500,step:5}) + fMcap(st),
    more: st => fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10}) + fChk(st,'trap','Hide value traps'),
    moreKeys: ['minTrend','trap'],
  }, magicView);
  const u = V().filter(s => (NOPRICE || s.pe != null) && s.roce != null && s.roce > 0 && mcapOk(s, mf) && !(mf.exFin && s.fin));
  [...u].sort((a,b) => b.roce - a.roce).forEach((s,i) => s.rQ = i+1);
  if (NOPRICE) {                                         // no PE without a price: rank on the ROCE half only
    u.forEach(s => { s.rEY = null; s.mf = s.rQ; });
    u.sort((a,b) => a.mf - b.mf).forEach((s,i) => s.mfRank = i+1);
  } else {
    [...u].sort((a,b) => a.pe - b.pe).forEach((s,i) => s.rEY = i+1);
    u.forEach(s => s.mf = s.rEY + s.rQ);
    u.sort((a,b) => a.mf - b.mf || a.pe - b.pe).forEach((s,i) => s.mfRank = i+1);
  }
  // ranks above stay pure Greenblatt; Trend / trap only filter the ranked list afterwards
  const rows = u.filter(s => (!mf.trap || !trapReasons(s).length) && trendOk(s, mf.minTrend)).slice(0, +mf.top || 50);
  stats([['Stocks ranked', u.length], ...(NOPRICE ? [] : [[`Median PE of top ${rows.length}`, fmt(median(rows.map(s=>s.pe)))]]),
         [`Median ROCE of top ${rows.length}`, fmt(median(rows.map(s=>s.roce))) + '%']], rows);
  view.appendChild(makeTable('magic', [
    {k:'mfRank', label:'Rank', l:1, v:s=>s.mfRank, f:s=>`<span class="rank ${s.mfRank<=10?'top':''}">${s.mfRank}</span>`},
    C.sym, C.name, C.ind, C.cmp, C.pe, C.ey, {k:'rEY', label:'EY rank', v:s=>s.rEY, f:s=>`<span class="na">${s.rEY}</span>`},
    C.roce, {k:'rQ', label:'ROCE rank', v:s=>s.rQ, f:s=>`<span class="na">${s.rQ}</span>`},
    C.pb, C.dy, C.cagr, C.fcfy, C.score, C.trend, C.mcap, C.flags, C.spark], rows, {sortKey:'mfRank', sortDir:1, search:true, noIndex:true,
    csv:() => csvName('Magic_formula', mf, MF_DEF, ['minMcap','maxMcap','top','exFin','minTrend','trap'])}));
  if (NOPRICE) hint('Without prices the Magic Formula is ranked on its quality half only (ROCE as it stood then, approximate), since PE needs a price. "Since" columns show how those businesses did afterwards.');
  else hint('Greenblatt: rank by earnings yield (100 ÷ PE) + rank by ROCE, lowest total wins. Uses PE instead of EBIT / EV because the CSV has no debt data. Trend and value-trap filters only remove stocks from the ranked list, they never change the ranks.');
}

/* ---------- QUALITY-VALUE (strict): hard filters first, then 50% value + 50% quality ---------- */
const QV_DEF = {minRoce:15, minRoe:15, minIcov:3.5, minCc:0.75, maxProDrop:1, top:30,
                minRoeFin:14, exPsu:true, peers:'ind', minMcap:0, maxMcap:'', minTrend:'', trap:false, showFail:false};
const QV_PRESETS = [
  {name:'Strict (as written)', tip:'ROCE ≥ 15% and ROE ≥ 15% (financials ROE ≥ 14%), interest cover > 3.5x, cash conversion > 0.75x, promoter not down > 1 pp in 1Y, no PSU', st:{}},
  {name:'Relaxed', tip:'ROCE / ROE ≥ 12%, interest cover > 2x, cash conversion > 0.6x, promoter not down > 2 pp', st:{minRoce:12, minRoe:12, minRoeFin:12, minIcov:2, minCc:0.6, maxProDrop:2}},
  {name:'Strict + improving', tip:'Strict hard filters, then Trend ≥ 70%', st:{minTrend:70}},
  {name:'Strict + no traps', tip:'Strict hard filters, then drop stocks with value-trap flags', st:{trap:true}},
];
const QV_LABEL = {pe:'PE', pb:'PB', fcf:'FCF yield', gr:'Graham upside', q:'ROCE (ROE fin.)', opm:'OPM change', inst:'FII + DII change 1Y'};
const qvOpm = s => s.tr && s.tr.d && s.tr.d.opmD != null ? s.tr.d.opmD : s.opmT;           // TTM OPM vs a year ago, else vs 5Y avg
const HW = () => SAME ? 'chg' : 'chg1';                 // same-method: change over its 3-quarter window, else 1 year
const HWL = () => SAME ? '3Q' : '1Y';
const qvInst = s => { const a = hv(s,'fii',HW()), b = hv(s,'dii',HW()); return a == null && b == null ? null : (a || 0) + (b || 0); };
const qvPro1 = s => hv(s,'pro',HW());
function qvFails(s, st) {                                  // hard filters: every reason a stock is out
  const r = [], num = v => (v === '' || v == null) ? null : +v;
  const below = (v, min, label) => { if (min == null) return; if (v == null) r.push('no ' + label); else if (v < min) r.push(label + ' < ' + min); };
  if (st.exPsu && s.psu) r.push(s.psu === 'psu' ? 'PSU' : 'semi-PSU');
  if (s.fin) below(s.roe, num(st.minRoeFin), 'ROE');
  else {
    below(s.roce, num(st.minRoce), 'ROCE');
    below(s.roe, num(st.minRoe), 'ROE');
    if (num(st.minIcov) != null && s.icov != null && s.icov <= num(st.minIcov)) r.push('interest cover ≤ ' + st.minIcov + 'x');   // no interest = debt-free = pass
    if (num(st.minCc) != null) { if (s.cc == null) r.push('no cash-conversion data'); else if (s.cc <= num(st.minCc)) r.push('cash conv. ≤ ' + st.minCc + 'x'); }
  }
  const p = qvPro1(s);
  if (num(st.maxProDrop) != null && p != null && p < -num(st.maxProDrop)) r.push('promoter sold ' + fmt(-p, 1) + ' pp in ' + HWL());
  return r;
}
function qvScore(pass, st) {
  const F = {pe:s=>s.pe, pb:s=>s.pb, fcf:s=>s.fin ? null : s.fcfy, gr:grahamUp, q:qual, opm:s=>s.fin ? null : qvOpm(s), inst:qvInst};
  const VAL = [['pe',.2,false], ['pb',.2,false], ['fcf',.4,true], ['gr',.2,true]];      // value half: PE + PB 40, FCF yield 40, Graham 20
  const QUAL = [['q',.4,true], ['opm',.3,true], ['inst',.3,true]];                    // quality half: ROCE / ROE 40, OPM 30, FII + DII 30
  const sorted = arr => arr.filter(ok).sort((a,b) => a-b);
  const all = {}, byInd = {};
  Object.keys(F).forEach(k => all[k] = sorted(pass.map(F[k])));
  pass.forEach(s => { const g = byInd[s.ind] = byInd[s.ind] || {}; Object.keys(F).forEach(k => (g[k] = g[k] || []).push(F[k](s))); });
  Object.values(byInd).forEach(g => Object.keys(g).forEach(k => g[k] = sorted(g[k])));
  const half = (s, parts, useInd) => {
    let t = 0, w = 0, tot = 0; const out = {};
    parts.forEach(([k, wt, hi]) => {
      const v = F[k](s); if (k === 'fcf' && s.fin) return; if (k === 'opm' && s.fin) return;   // not applicable to financials
      tot += wt; if (!ok(v)) return;
      const peers = useInd && byInd[s.ind][k].length >= 4 ? byInd[s.ind][k] : all[k];        // small industry -> whole list
      const p = pctl(peers, v, hi); out[k] = p; t += p * wt; w += wt;
    });
    return w && w >= 0.5 * tot ? {v: t / w * 100, parts: out} : null;
  };
  pass.forEach(s => {
    const v = NOPRICE ? null : half(s, VAL, st.peers === 'ind'), q = half(s, QUAL, false);
    s.qvV = v ? v.v : null; s.qvQ = q ? q.v : null;
    s.qvParts = Object.assign({}, v && v.parts, q && q.parts);
    s.qv = NOPRICE ? s.qvQ : (v && q ? (v.v + q.v) / 2 : null);
  });
}
function qvView() {
  setNav('qv'); view.innerHTML = '';
  const st = filterBox({
    key:'qv', def:QV_DEF, presets:QV_PRESETS,
    main: st => fNum(st,'minRoce','Min ROCE %') + fNum(st,'minRoe','Min ROE %') + fNum(st,'minIcov','Min int. cover x',{step:0.5})
      + fNum(st,'minCc','Min cash conv. x',{step:0.05}) + fNum(st,'maxProDrop','Max promoter drop 1Y pp',{min:0,step:0.5})
      + fNum(st,'top','Show top',{min:5,max:500,step:5}),
    more: st => fNum(st,'minRoeFin','Financials: min ROE %') + fChk(st,'exPsu','Exclude PSU & semi-PSU')
      + (NOPRICE ? '' : fSel(st,'peers','Rank PE / PB',[['ind','within industry'],['all','across the list']]))
      + fMcap(st) + fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10}) + fChk(st,'trap','Hide value traps')
      + fChk(st,'showFail','Show failing stocks with reason'),
    moreKeys: ['minRoeFin','exPsu','peers','minMcap','maxMcap','minTrend','trap','showFail'],
  }, qvView);
  const base = V().filter(s => (NOPRICE || s.pe != null) && mcapOk(s, st));
  base.forEach(s => { s.qv = s.qvV = s.qvQ = s.qvRank = null; s.qvParts = {}; });
  const pass = base.filter(s => !qvFails(s, st).length);
  qvScore(pass, st);
  pass.filter(s => s.qv != null).sort((a,b) => b.qv - a.qv).forEach((s,i) => s.qvRank = i + 1);   // rank = pure composite
  let rows = pass.filter(s => s.qv != null && (!st.trap || !trapReasons(s).length) && trendOk(s, st.minTrend))
                 .sort((a,b) => a.qvRank - b.qvRank).slice(0, +st.top || 30);
  if (st.showFail) rows = rows.concat(base.filter(s => qvFails(s, st).length));         // listed after the ranked ones, no rank
  const shownRanked = rows.filter(s => s.qvRank);
  stats([['Pass hard filters', `${pass.length} of ${base.length}`], ['Shown', shownRanked.length],
         ...(NOPRICE ? [] : [['Median PE of list', fmt(median(shownRanked.map(s=>s.pe)))], ['Median FCF yield', fmt(median(shownRanked.map(s=>s.fcfy))) + '%']]),
         ['Median ROCE of list', fmt(median(shownRanked.map(qual))) + '%']], shownRanked);
  const meter = (v, tip) => v == null ? NA : `<span class="meter"${tip ? ` title="${esc(tip)}"` : ''}><span>${fmt(v,0)}</span><i><b style="width:${Math.max(3, v).toFixed(0)}%"></b></i></span>`;
  const tipOf = s => Object.entries(s.qvParts || {}).map(([k,p]) => QV_LABEL[k] + ': ' + Math.round(p*100)).join('\n');
  const cols = [
    {k:'qvRank', label:'Rank', l:1, v:s=>s.qvRank, f:s=>s.qvRank ? `<span class="rank ${s.qvRank<=10?'top':''}">${s.qvRank}</span>` : NA},
    {k:'qv', label: NOPRICE ? 'Quality score (then)' : 'QV score', tip:'50% value half + 50% quality half', v:s=>s.qv, f:s=>meter(s.qv, tipOf(s))},
    {k:'qvV', label:'Value half', v:s=>s.qvV, f:s=>s.qvV==null ? NA : fmt(s.qvV,0)},
    {k:'qvQ', label:'Quality half', v:s=>s.qvQ, f:s=>s.qvQ==null ? NA : fmt(s.qvQ,0)},
    C.sym, C.name, C.ind, C.cmp, C.pe, C.prem, C.pb, C.fcfy, C.gup, C.q, C.roe,
    {k:'icov', label:'Int. cover', v:s=>s.icov, f:s=>s.icov==null ? '<span class="na">no debt</span>' : fmt(s.icov,1)+'x'},
    C.cc,
    {k:'qvOpm', label:'OPM Δ', tip:'TTM operating margin vs a year ago, pp', v:qvOpm, f:s=>ppPill(s.fin ? null : qvOpm(s), true)},
    {k:'qvInst', label:'FII + DII Δ ' + HWL(), v:qvInst, f:s=>ppPill(qvInst(s), true)},
    {k:'qvPro', label:'Promoter Δ ' + HWL(), v:qvPro1, f:s=>ppPill(qvPro1(s), true)},
    C.trend, C.mcap,
    ...(st.showFail ? [{k:'qvWhy', label:'Hard filters', l:1, v:s=>qvFails(s, st).length, x:s=>qvFails(s, st).join('; ') || 'pass',
        f:s=>{ const r = qvFails(s, st); return r.length ? `<span class="pill warn">${esc(r.join(' · '))}</span>` : '<span class="pill disc">pass</span>'; }}] : []),
  ];
  view.appendChild(makeTable('qv', cols, rows, {sortKey:'qvRank', sortDir:1, search:true, noIndex:true,
    csv:() => csvName('Quality_value', st, QV_DEF, ['minMcap','maxMcap','minRoce','minRoe','minRoeFin','minIcov','minCc','maxProDrop','top','minTrend','exPsu','peers','trap','showFail'])}));
  hint(NOPRICE ? 'Hard filters as they stood 1 year back; only the quality half can be scored (no prices.csv yet). "Since" columns show how the survivors did afterwards.'
            : 'Step 1: hard filters remove anything weak or risky. Step 2: survivors are ranked 50% on value (PE + PB 40, FCF yield 40, Graham upside 20) and 50% on quality (ROCE / ROE 40, OPM change 30, FII + DII change 30). Hover a QV score for its parts. No pledge or debt-to-equity data in the CSV, so those two rules are not applied.');
}

/* ---------- SHAREHOLDING TABS ---------- */
const sinceCell = (s, k) => { const h = s.h && s.h[k]; return h ? `<span class="na">${esc(h.since)} → ${esc(h.till)} (${fmt(h.yrs,2)}y)</span>` : NA; };
function holdCols(k, upGood) {
  return {
    now:  {k:k+'now', label:'Now %', v:s=>hv(s,k,'now'), f:s=>fmt(hv(s,k,'now'),2)},
    chg:  {k:k+'chg', label:'Δ full period', v:s=>hv(s,k,'chg'), f:s=>ppPill(hv(s,k,'chg'), upGood)},
    win:  {k:k+'win', label:'Period', l:1, v:s=>hv(s,k,'yrs'), x:s=>s.h&&s.h[k] ? `${s.h[k].since} to ${s.h[k].till}` : '', f:s=>sinceCell(s,k)},
    chg1: {k:k+'chg1', label:'Δ last 1Y', v:s=>hv(s,k,'chg1'), f:s=>ppPill(hv(s,k,'chg1'), upGood)},
    ud:   {k:k+'ud', label:'Qtrs ↑ / ↓', v:s=>s.h&&s.h[k]?s.h[k].up-s.h[k].dn:null, f:s=>s.h&&s.h[k]?`<span class="${upGood?'up':'dn'}">${s.h[k].up}↑</span> <span class="${upGood?'dn':'up'}">${s.h[k].dn}↓</span>`:NA},
    sp:   {k:k+'sp', label:'Trend', noCsv:1, v:s=>null, f:s=>s.h&&s.h[k]?sparkLine(s.h[k].s, upGood?'var(--good)':'var(--accent)'):NA},
  };
}
const jumpOf = s => Math.max(hv(s,'pro','jump')||0, hv(s,'pub','jump')||0);
const jumpCell = {k:'jump', label:'Check', l:1, tip:'largest single-quarter move', v:jumpOf,
  f:s=>{const j=jumpOf(s); return j>D.jumpPP?`<span class="pill warn">${fmt(j,1)} pp in 1 qtr: merger / OFS?</span>`:''; }};
const hasJump = s => jumpOf(s) > D.jumpPP;
const dirOk = (c, dir, m) => dir==='up' ? c >= Math.max(m, 0.01) : dir==='down' ? c <= -Math.max(m, 0.01) : Math.abs(c) >= m;

const PR_DEF = {dir:'up', minChg:1, hideJump:true, maxPE:'', minMcap:0, maxMcap:'', minNow:''};
const PR_PRESETS = [
  {name:'Insiders buying', tip:'Promoter holding up ≥ 1 pp, merger / OFS jumps hidden', st:{dir:'up', minChg:1}},
  {name:'Buying + cheap', price:1, tip:'Promoter up ≥ 0.5 pp and PE ≤ 20', st:{dir:'up', minChg:0.5, maxPE:20}},
  {name:'Insiders selling', tip:'Promoter holding down ≥ 2 pp', st:{dir:'down', minChg:2}},
];
function promoterView() {
  setNav('promoter'); view.innerHTML = '';
  const f = filterBox({
    key:'pr', def:PR_DEF, presets:PR_PRESETS,
    main: st => fSel(st,'dir','Direction',[['up','Increasing'],['down','Decreasing'],['all','All']]) + fNum(st,'minChg','Min change pp',{min:0,step:0.5})
      + (NOPRICE ? '' : fNum(st,'maxPE','Max PE',{min:0})),
    more: st => fNum(st,'minNow','Min promoter % now',{min:0,step:5}) + fMcap(st)
      + fChk(st,'hideJump',`Hide jumps > ${D.jumpPP} pp in one quarter`),
    moreKeys: ['minNow','minMcap','maxMcap','hideJump'],
  }, promoterView);
  const P = holdCols('pro', true), F = holdCols('fii', true), Dd = holdCols('dii', true);
  const all = V().filter(s => s.h && s.h.pro);
  const rows = all.filter(s => dirOk(s.h.pro.chg, f.dir, +f.minChg || 0)
      && (f.minNow === '' || s.h.pro.now >= f.minNow)
      && (NOPRICE || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && mcapOk(s, f)
      && (!f.hideJump || !hasJump(s)));
  stats([['Stocks shown', rows.length], ['Promoter ↑ ≥ 1 pp', all.filter(s=>s.h.pro.chg>=1).length],
         ['Promoter ↓ ≥ 1 pp', all.filter(s=>s.h.pro.chg<=-1).length], ['With holding history', all.length],
         ['Typical period', fmt(median(all.map(s=>s.h.pro.yrs)),2) + ' yrs']], rows);
  const st = sortState['promoter'];
  if (st && st.k === 'prochg') st.dir = f.dir==='down' ? 1 : -1;      // keep the biggest movers on top
  view.appendChild(makeTable('promoter', [C.sym, C.name, C.ind, C.cmp, P.now, Object.assign({}, P.chg, SAME ? {label:'Δ last 3Q'} : {}), ...(SAME ? [] : [P.chg1]), P.ud, P.win, P.sp,
      Object.assign({}, F.chg, {label:'FII Δ'}), Object.assign({}, Dd.chg, {label:'DII Δ'}), C.pe, C.pb, C.dy, C.score, C.trend, C.mcap, jumpCell],
    rows, {sortKey:'prochg', sortDir: f.dir==='down' ? 1 : -1, search:true, sinceCols:[C.proD, C.patG, C.roeD],
    csv:() => csvName('Promoter_holding', f, PR_DEF, ['minMcap','maxMcap','dir','minChg'].concat(NOPRICE ? [] : ['maxPE'], ['minNow','hideJump']))}));
  hint(`Δ = change in percentage points between the first and last quarter available (most stocks ~${fmt(median(all.map(s=>s.h.pro.yrs)),2)} years, see Period). Qtrs ↑ / ↓ counts quarter-on-quarter moves above 0.05 pp; steady creeping buying matters more than one jump.`);
}

const PU_DEF = {dir:'down', minChg:1, smart:false, hideJump:true, maxPE:'', minMcap:0, maxMcap:''};
const PU_PRESETS = [
  {name:'Smart money absorbing', tip:'Public % falling ≥ 1 pp while FII + DII rise', st:{dir:'down', minChg:1, smart:true}},
  {name:'Retail piling in', tip:'Public % rising ≥ 2 pp (often promoters or funds selling)', st:{dir:'up', minChg:2}},
];
function publicView() {
  setNav('public'); view.innerHTML = '';
  const f = filterBox({
    key:'pu', def:PU_DEF, presets:PU_PRESETS,
    main: st => fSel(st,'dir','Direction',[['down','Falling'],['up','Rising'],['all','All']]) + fNum(st,'minChg','Min change pp',{min:0,step:0.5})
      + fChk(st,'smart','FII + DII rising'),
    more: st => (NOPRICE ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fMcap(st)
      + fChk(st,'hideJump',`Hide jumps > ${D.jumpPP} pp in one quarter`),
    moreKeys: ['maxPE','minMcap','maxMcap','hideJump'],
  }, publicView);
  const U = holdCols('pub', false), P = holdCols('pro', true);
  const all = V().filter(s => s.h && s.h.pub);
  const rows = all.filter(s => dirOk(s.h.pub.chg, f.dir, +f.minChg || 0)
      && (!f.smart || (smart(s) != null && smart(s) > 0))
      && (NOPRICE || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && mcapOk(s, f)
      && (!f.hideJump || !hasJump(s)));
  stats([['Stocks shown', rows.length], ['Public ↓ ≥ 1 pp', all.filter(s=>s.h.pub.chg<=-1).length],
         ['Public ↑ ≥ 1 pp', all.filter(s=>s.h.pub.chg>=1).length],
         ['Public ↓ and FII+DII ↑', all.filter(s=>s.h.pub.chg<=-1 && smart(s)>0).length]], rows);
  const st = sortState['public'];
  if (st && st.k === 'pubchg') st.dir = f.dir==='up' ? -1 : 1;
  view.appendChild(makeTable('public', [C.sym, C.name, C.ind, C.cmp, U.now, Object.assign({}, U.chg, SAME ? {label:'Δ last 3Q'} : {}), ...(SAME ? [] : [U.chg1]), U.ud, U.win, U.sp,
      {k:'smart', label:'FII + DII Δ', tip:'smart money', v:smart, f:s=>ppPill(smart(s), true)},
      Object.assign({}, P.chg, {label:'Promoter Δ'}), C.pe, C.pb, C.dy, C.score, C.trend, C.mcap, jumpCell],
    rows, {sortKey:'pubchg', sortDir: f.dir==='up' ? -1 : 1, search:true, sinceCols:[C.patG, C.revG, C.roeD],
    csv:() => csvName('Public_holding', f, PU_DEF, ['minMcap','maxMcap','dir','minChg','smart'].concat(NOPRICE ? [] : ['maxPE'], ['hideJump']))}));
  hint('Public = retail and other non-institutional holders. Falling public % (green) = promoters or institutions absorbing shares; rising (red) = shares moving to retail. Strongest signal: public falling while FII + DII rise.');
}

/* ---------- IMPROVING (trend score) ---------- */
const IM_DEF = {minTrend:70, minMcap:0, maxMcap:'', maxPE:'', exFin:false, trap:false};
const IM_PRESETS = [
  {name:'All green', tip:'Every check with data passed', st:{minTrend:100}},
  {name:'Mostly green', tip:'Trend ≥ 70%', st:{minTrend:70}},
  {name:'Improving & cheap', price:1, tip:'Trend ≥ 70%, PE ≤ 20, no value traps', st:{minTrend:70, maxPE:20, trap:true}},
  {name:'Improving, ex-fin', tip:'Trend ≥ 70%, all 7 checks apply (no financials)', st:{minTrend:70, exFin:true}},
];
function improvingView() {
  setNav('improving'); view.innerHTML = '';
  const f = filterBox({
    key:'im', def:IM_DEF, presets:IM_PRESETS,
    main: st => fNum(st,'minTrend','Min Trend %',{min:0,max:100,step:10}) + (NOPRICE ? '' : fNum(st,'maxPE','Max PE',{min:0})) + fChk(st,'exFin','Exclude financials'),
    more: st => fMcap(st) + fChk(st,'trap','Hide value traps'),
    moreKeys: ['minMcap','maxMcap','trap'],
  }, improvingView);
  const all = V().filter(s => s.tr);
  const rows = all.filter(s => trendOk(s, f.minTrend)
      && (NOPRICE || f.maxPE === '' || (s.pe != null && s.pe <= f.maxPE))
      && mcapOk(s, f)
      && !(f.exFin && s.fin)
      && (!f.trap || !trapReasons(s).length));
  stats([['Stocks shown', rows.length], ['With a Trend score', all.length],
         ['All checks passed', all.filter(s => s.tr.pass === s.tr.of).length],
         ...(NOPRICE ? [] : [['Median PE of list', fmt(median(rows.map(s=>s.pe)))]]),
         ['Median Value score of list', fmt(median(rows.map(s=>s.score)),0)]], rows);
  view.appendChild(makeTable('improving', [C.trend, C.sym, C.name, C.ind, C.cmp, ...TR_KEYS.map(trCheck), C.pe, C.score, C.mcap, C.flags],
    rows, {sortKey:'trend', sortDir:-1, search:true, sinceCols:[C.patG, C.revG, C.roeD],
    csv:() => csvName('Improving', f, IM_DEF, ['minMcap','maxMcap','minTrend'].concat(NOPRICE ? [] : ['maxPE'], ['exFin','trap']))}));
  hint(BACK ? 'Trend checks as they stood 1 year back, using only results published by then. "Since" columns show whether the improving businesses kept delivering.'
            : 'Trend = share of 7 pass / fail checks that are green (financials: 4). Each ✓ / ✗ shows its evidence; hover the Trend bar for all of them. Improving is not cheap: pair it with Value screen or Magic Formula. Cyclicals look best here right at a peak.');
}

/* ---------- METHODOLOGY ---------- */
/* ---------- BACKTEST MATCH: what each tab can show on a past date; only prices decide what is available ---------- */
const btOf = k => {
  const px = D.then && D.then.hasPrice, roce = 'ROCE is EBIT ÷ total assets at both dates until the scraper stores Borrowings.';
  const T = px ? {
    home:     ['Like-for-like', 'Industry PE, PB, ROE and yields from the same engine at both dates.'],
    value:    ['Like-for-like', 'Same 7-metric Value score, same filters and Screens at both dates. ' + roce],
    magic:    ['Like-for-like', 'Full Magic Formula (earnings yield + ROCE) at both dates. ' + roce],
    qv:       ['Like-for-like', 'Both halves at both dates. Promoter-drop and FII + DII use a 3-quarter window at both dates (all the CSV holds 1 year back). ' + roce],
    promoter: ['Like-for-like', 'Same maths at both dates on a 3-quarter window.'],
    public:   ['Like-for-like', 'Same maths at both dates on a 3-quarter window.'],
    improving:['Like-for-like', 'Same 7 checks at both dates, quarterly checks on 2 year-on-year comparisons.'],
  } : {
    home:     ['Fundamentals only', 'No prices.csv yet: industry PE, PB and yields are blank 1Y back.'],
    value:    ['Fundamentals only', 'No prices.csv yet: score uses ROCE / ROE, EPS growth and cash conversion only; price filters and Screens are off.'],
    magic:    ['Not comparable', 'No prices.csv yet: earnings yield needs a price, so the rank is ROCE only.'],
    qv:       ['Quality half only', 'No prices.csv yet: value half needs a price.'],
    promoter: ['Like-for-like', 'Same maths at both dates on a 3-quarter window; Max PE off without prices.'],
    public:   ['Like-for-like', 'Same maths at both dates on a 3-quarter window; Max PE off without prices.'],
    improving:['Like-for-like', 'Same 7 checks at both dates, quarterly checks on 2 year-on-year comparisons.'],
  };
  return T[k] ? {lvl:T[k][0], why:T[k][1]} : null;
};
const BT_ROUTE = h => h.startsWith('#/industry/') || h === '#/' || h === '' ? 'home' : h.slice(2);
/* ---------- ALL STOCKS: no screen, no filter; every stock in the chosen list that has a price on the date shown ---------- */
const AF_DEF = Object.assign({}, VF_DEF, {minQ:'', minDy:'', trap:false, minMom:''});   // All Stocks: every filter off by default
function allView() {
  setNav('all'); view.innerHTML = '';
  const af = filterBox({key:'af', def:AF_DEF, main: vfMain, more: vfMore, moreKeys: vfMoreKeys()}, allView);
  const priced = V().filter(s => s.cmp != null), rows = priced.filter(s => passVF(s, af));
  stats([['Showing', rows.length], ['With a price', priced.length], ['Without a price', V().length - priced.length],
         ['Median PE', fmt(median(rows.map(s => s.pe)))], ['Median PB', fmt(median(rows.map(s => s.pb)), 2)],
         ['Median ROE %', fmt(median(rows.map(s => s.roe)))]], rows);
  view.appendChild(makeTable('all',
    [C.sym, C.ind, C.score, C.name, C.mom, C.trend, C.cmp, C.pe, C.cpe, C.prem, C.pb, ...safeCols(), ...assetCols(), C.q, C.cagr, C.peg, C.fcfy, C.cc, C.opmT, C.dy, C.proChg, C.mcap, C.from52, C.flags],
    rows, {sortKey:'mcap', sortDir:-1, search:true, csv:() => csvName('All_stocks', af, AF_DEF, VF_CSV_KEYS())}));
  if (!priced.length) view.insertAdjacentHTML('beforeend', '<p class="hint">No prices for this date yet (prices.csv has no price for it), so nothing to list.</p>');
}

/* ---------- MOMENTUM: stocks by price momentum, strongest first. Default: momentum 70+ and nothing else;
   the Filters button has every Value-screen filter (off) plus Max momentum. ---------- */
const MOM_DEF = Object.assign({}, AF_DEF, {minMom:70, maxMom:''});
const momMain = st => vfMain(st) + fNum(st,'maxMom','Max momentum',{min:0,max:100,step:5});
function momentumView() {
  setNav('momentum'); view.innerHTML = '';
  const mf = filterBox({key:'momf', def:MOM_DEF, main: momMain, more: vfMore, moreKeys: vfMoreKeys()}, momentumView);
  const hasMax = !unsetF(mf.maxMom) && +mf.maxMom > 0;
  const rows = V().filter(s => ok(s.momS) && s.cmp != null && passVF(s, mf) && (!hasMax || Math.round(s.momS) <= +mf.maxMom));   // same rounding as the column
  const lo = unsetF(mf.minMom) || !+mf.minMom ? 0 : +mf.minMom;
  stats([['Showing', rows.length], ['Momentum', hasMax ? lo + ' – ' + mf.maxMom : '≥ ' + lo], ['Median momentum', fmt(median(rows.map(s => s.momS)), 0)],
         ['Median PE', fmt(median(rows.map(s => s.pe)))], ['Median vs 52W high', fmt(median(rows.map(s => pctFrom(s.cmp, s.hi52)))) + '%']], rows);
  view.appendChild(makeTable('mom',
    [C.sym, C.ind, C.mom, C.name, C.score, C.trend, C.cmp, C.from52, C.pe, C.pb, C.q, C.cagr, C.dy, C.mcap, C.flags],
    rows, {sortKey:'mom', sortDir:-1, search:true, csv:() => csvName('Momentum', mf, MOM_DEF, VF_CSV_KEYS().concat('maxMom'))}));
  if (!V().some(s => ok(s.momS))) view.insertAdjacentHTML('beforeend', GF.uni === OTH
    ? '<p class="hint">No momentum for the Others list yet: it needs their prices, which the next weekly price update (Saturday) adds.</p>'
    : '<p class="hint">No momentum for this date yet (it needs prices.csv), so nothing to list.</p>');
}


/* ---------- CHECKLIST (Prime): two fixed rule sets, non-financial and financial companies ----------
   A stock is listed only if it passes EVERY rule that has data, and at least 70% of the rules that can have data do.
   Rules this site has no data for yet (promoter pledge, capital adequacy, insurer solvency, embedded value, 5-year
   own-history PE) are shown as "no data" and never count for or against. Ranked by valuation vs its sector:
   PE ÷ industry median PE (non-financials), PB ÷ industry median PB (financials); cheapest first. */
const CK_MIN_DATA = 0.7;
let CK_SEG = lsGet('ckseg') === 'fin' ? 'fin' : 'nonfin';
const ckIns = s => s.fin && /insur|life\b|general\b.*assur/i.test((s.ind || '') + ' ' + (s.name || ''));
const ckBank = s => s.fin && !ckIns(s) && (s.gnpa != null || /\bbank\b/i.test(s.name || ''));
const ckType = s => !s.fin ? 'nonfin' : ckIns(s) ? 'ins' : ckBank(s) ? 'bank' : 'nbfc';
const ckTTM = s => s.qp && s.qp.length >= 4 ? s.qp.slice(-4).reduce((a, b) => a + b, 0) : (ok(s.ttm) ? s.ttm : null);
const ckPct = (v, d = 1) => (v > 0 ? '+' : '') + fmt(v, d) + '%';
/* [label, value, test, shown] ; value null = no data for this stock; test null = rule with no data source (always "no data") */
function ckRules(s) {
  const t = ckType(s), R = [], add = (label, v, test, show) => R.push({label, v, test, show: show || (x => fmt(x))});
  add('5Y sales CAGR ≥ 8%', s.sg5, v => v >= 8, ckPct);
  if (t === 'nonfin') add('3Y sales CAGR ≥ 5%', s.sg3, v => v >= 5, ckPct);
  add('3Y profit CAGR ≥ 8%', s.pg3, v => v >= 8, ckPct);
  add('TTM profit positive', ckTTM(s), v => v > 0, v => v > 0 ? 'yes' : 'loss');
  if (t === 'nonfin') {
    add('ROCE ≥ 15%', s.roce, v => v >= 15, v => fmt(v) + '%');
    add('ROE ≥ 12%', s.roe, v => v >= 12, v => fmt(v) + '%');
    add('Operating cash flow positive (5Y total)', s.cfo5, v => v > 0, v => '₹' + fmt(v, 0) + ' Cr');
    add('CFO ÷ PAT ≥ 0.8 (5Y total)', s.cp5, v => v >= 0.8, v => fmt(v, 2) + 'x');
    add('Debt ÷ Equity ≤ 0.5', s.de, v => v <= 0.5, v => fmt(v, 2));
    add('Interest coverage ≥ 3×', s.icov, v => v >= 3, v => fmt(v, 1) + 'x');
    add('Promoter pledge ≤ 10%', null, null);
    add('Share dilution ≤ 10% (5Y)', s.dil5, v => v <= 10, ckPct);
    add('PE below 5Y own median', null, null);
  } else {
    add('ROE ≥ 12%', s.roe, v => v >= 12, v => fmt(v) + '%');
    if (t === 'bank') {
      add('ROA ≥ 0.8%', s.roa, v => v >= 0.8, v => fmt(v, 2) + '%');
      add('Gross NPA ≤ 5%', s.gnpa, v => v <= 5, v => fmt(v, 2) + '%');
      add('Net NPA ≤ 1.5%', s.nnpa, v => v <= 1.5, v => fmt(v, 2) + '%');
      add('Provision coverage ≥ 70%', s.pcr, v => v >= 70, v => fmt(v, 0) + '%');
      add('Gross NPA stable or falling (1Y)', s.gnpaT, v => v <= 0.25, v => (v > 0 ? '+' : '') + fmt(v, 2) + ' pp');
      add('Capital adequacy ≥ 15%', null, null);
    } else if (t === 'nbfc') {
      add('ROA ≥ 1.5%', s.roa, v => v >= 1.5, v => fmt(v, 2) + '%');
      add('Capital adequacy above minimum', null, null);
    } else {
      add('Solvency ratio above minimum', null, null);
      add('Embedded value / persistency', null, null);
    }
  }
  R.forEach(r => { r.pass = r.test && r.v != null ? !!r.test(r.v) : null; });
  return R;
}
function ckResult(s) {
  const R = ckRules(s), can = R.filter(r => r.test), known = can.filter(r => r.pass != null);
  const fail = known.filter(r => !r.pass);
  return {R, pass: !fail.length && can.length && known.length / can.length >= CK_MIN_DATA, known: known.length, can: can.length, fail};
}
const ckMedPB = ind => { const g = industries[ind]; return g ? g.medPB : null; };
const ckVal = s => s.fin ? (s.pb != null && ckMedPB(s.ind) ? (s.pb / ckMedPB(s.ind) - 1) * 100 : null) : prem(s);
function checklistView() {
  setNav('checklist'); view.innerHTML = '';
  if (!PRIMEF) {
    view.insertAdjacentHTML('beforeend', `<div class="ck-lockbox"><b>Checklist is part of Prime</b>Two fixed quality checklists, one for
      manufacturing / FMCG / IT / pharma and one for banks, NBFCs and insurers. Only the stocks that pass every rule are listed, cheapest
      versus their sector first.<br><button type="button" class="btn primary" id="ckPlans">See plans</button></div>`);
    $('#ckPlans').addEventListener('click', () => window.hniPlans && window.hniPlans());
    if (window.hniPlans) window.hniPlans();
    return;
  }
  /* Historical on: the same rules on the numbers as they stood on the back date; the table then also shows the price return
     since (and the "Since" strip), i.e. a backtest of the checklist */
  const all = V(), mine = all.filter(s => (CK_SEG === 'fin') === !!s.fin);
  const res = new Map(mine.map(s => [s.sym, ckResult(s)]));
  const rows = mine.filter(s => res.get(s.sym).pass);
  view.insertAdjacentHTML('beforeend', `<div class="ck-seg" role="tablist"><button type="button" data-seg="nonfin" class="${CK_SEG === 'nonfin' ? 'on' : ''}">Non-financial companies</button><button type="button" data-seg="fin" class="${CK_SEG === 'fin' ? 'on' : ''}">Banks, NBFCs and insurers</button></div>`);
  view.querySelectorAll('.ck-seg button').forEach(b => b.addEventListener('click', () => { CK_SEG = b.dataset.seg; lsSet('ckseg', CK_SEG); checklistView(); }));
  const ruleList = (CK_SEG === 'fin' ? [['Banks', {fin: true, gnpa: 1}], ['NBFCs', {fin: true, name: 'x'}], ['Insurers', {fin: true, name: 'Life Insurance'}]] : [['All', {}]])
    .map(([who, x]) => ckRules(Object.assign({sym: 'x'}, x)).map(r => `<tr><td>${esc(r.label)}</td><td>${who === 'All' ? '' : esc(who)}</td><td class="${r.test ? '' : 'nd'}">${r.test ? 'must pass' : 'no data yet: not counted'}</td></tr>`).join('')).join('');
  view.insertAdjacentHTML('beforeend', `<details class="ck-rules"><summary>The rules (${CK_SEG === 'fin' ? 'financial' : 'non-financial'} companies) · listed only if every rule with data passes</summary><table>${ruleList}</table></details>`);
  stats([['Pass', rows.length], ['Checked', mine.length], ['Median PE', fmt(median(rows.map(s => s.pe)))], ['Median ROE', fmt(median(rows.map(s => s.roe))) + '%']], rows);
  const R = s => res.get(s.sym);
  const cell = (label) => s => { const r = R(s).R.find(x => x.label === label); return !r || r.v == null ? NA : r.show(r.v); };
  const val = {k: 'ckv', label: 'vs sector', tip: CK_SEG === 'fin' ? 'PB vs the industry median PB (negative = cheaper)' : 'PE vs the industry median PE (negative = cheaper)',
    v: ckVal, f: s => { const v = ckVal(s); return v == null ? NA : `<span class="pill ${v <= 0 ? 'disc' : 'prem'}">${ckPct(v, 0)}</span>`; }};
  const pass = {k: 'ckp', label: 'Rules', tip: 'rules passed / rules with data (rules with no data are not counted)', v: s => R(s).known,
    x: s => `${R(s).known}/${R(s).can}`, f: s => `<span class="ck-ok" title="${esc(R(s).R.filter(r => r.pass != null).map(r => '✓ ' + r.label + ': ' + r.show(r.v)).join('\n'))}">✓ ${R(s).known}/${R(s).can}</span>`};
  const col = (k, label, tip) => ({k, label, tip, v: s => { const r = R(s).R.find(x => x.label === tip); return r ? r.v : null; }, f: cell(tip)});
  const cols = CK_SEG === 'fin'
    ? [C.sym, C.name, C.ind, val, C.pb, C.pe, pass, C.roe, C.roa, C.gnpa, C.nnpa, C.pcr, col('sg5', '5Y sales', '5Y sales CAGR ≥ 8%'), col('pg3', '3Y profit', '3Y profit CAGR ≥ 8%'), C.cmp, C.mcap]
    : [C.sym, C.name, C.ind, val, C.pe, C.pb, pass, C.roce, C.roe, col('sg5', '5Y sales', '5Y sales CAGR ≥ 8%'), col('sg3', '3Y sales', '3Y sales CAGR ≥ 5%'),
       col('pg3', '3Y profit', '3Y profit CAGR ≥ 8%'), col('cp5', 'CFO ÷ PAT', 'CFO ÷ PAT ≥ 0.8 (5Y total)'), C.de, {k: 'icov', label: 'Int. cover', v: s => s.icov, f: s => s.icov == null ? NA : fmt(s.icov, 1) + 'x'},
       col('dil5', 'Dilution 5Y', 'Share dilution ≤ 10% (5Y)'), C.cmp, C.mcap];
  view.appendChild(makeTable('ck_' + CK_SEG, cols, rows, {sortKey: 'ckv', sortDir: 1, search: true, csv: () => 'Checklist_' + CK_SEG}));
  const why = mine.length - rows.length;
  view.insertAdjacentHTML('beforeend', `<p class="hint">${rows.length} of ${mine.length} ${CK_SEG === 'fin' ? 'financial' : 'non-financial'} stocks in this list pass. The other ${why} fail at least one rule or have too little data (fewer than ${Math.round(CK_MIN_DATA * 100)}% of the rules). Valuation (vs sector) only ranks, it never excludes. Educational screen, not investment advice.</p>`);
}

/* ---------- PORTFOLIO: a small portfolio manager, built from one list of transactions ----------
   Every buy, sell, pay-in and pay-out is a transaction; holdings, cash, closed positions and gains are recomputed
   from that list each time, so deleting or editing a wrong entry fixes everything after it.
   Website: saved in Firestore under the user's account (hni_portfolio/{uid}) through auth.js (window.hniStore).
   Offline saved copy: kept in this browser only. Prices, scores and flags always come from TODAY's data. */
const PF_RATES = {brokPct:0, brokCap:0, stt:0.1, exch:0.00297, sebi:0.0001, stamp:0.015, gst:18, dp:15.93};   // equity delivery, NSE; editable under Charges
const PF_RATE_LABEL = {brokPct:'Brokerage % (0 = free)', brokCap:'Brokerage max ₹ per order (0 = no cap)', stt:'STT % (buy and sell)',
  exch:'Exchange charge %', sebi:'SEBI fee %', stamp:'Stamp duty % (buy only)', gst:'GST % (on brokerage + exchange + SEBI)', dp:'DP charge ₹ per sell'};
const PF = {txns: null, rates: Object.assign({}, PF_RATES), busy: false, err: ''};
const pfStore = () => (WEB && window.hniStore) ? window.hniStore : {
  load: async () => { try { return JSON.parse(lsGet('portfolio') || '{}'); } catch (e) { return {}; } },
  save: async data => lsSet('portfolio', JSON.stringify(data)),
};
const pfId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
let PF_WAIT = null;
function pfLoad() {                                       // loads once; every caller gets the same promise
  if (PF.txns) return Promise.resolve();
  if (WEB && !window.hniStore) return new Promise(res => window.addEventListener('hni-ready', () => pfLoad().then(res), {once: true}));   // login still being checked
  return PF_WAIT = PF_WAIT || pfLoadNow();
}
const pfTxnsOf = d => Array.isArray(d && d.txns) ? d.txns
  : ((d && Array.isArray(d.items)) ? d.items : (Array.isArray(d) ? d : [])).map(it => ({id: it.id || pfId(), t:'buy', sym: it.sym, qty: it.qty, px: it.buy, ch: 0, date: it.date || ''}));   // first version: plain holdings → buys
async function pfLoadNow() {
  PF.busy = true;
  try {
    const d = await pfStore().load() || {};
    PF.txns = pfTxnsOf(d);
    PF.rates = Object.assign({}, PF_RATES, d.rates || {});
    PF.err = '';
  } catch (e) { PF.txns = []; PF.err = 'Could not load your portfolio (' + (e.code || e) + ').'; }
  try { PF.mkt = window.hniStore && window.hniStore.market ? await window.hniStore.market() : null; } catch (e) { PF.mkt = null; }
  PF.busy = false;
  pfApplyRenames();
  PF_HELD = pfHeld();
  if ((location.hash || '') === '#/portfolio') { route(); setTimeout(pfBasketOpen, 200); } else pfMarkBags();   // a list from PF Builder: check it, then buy
  pfWatch();
}
window.addEventListener('hni-ready', () => pfLoad());
/* ---------- PF Builder → Portfolio ----------
   PF Builder leaves its picks in this browser (localStorage 'hni_pf_basket' = {at, fund, items: [{sym, name, target, px, etf}]})
   and opens #/portfolio. Here, once the portfolio is loaded: each pick is priced at the LTP (pfLtp: live price when held,
   else this page's latest close; ETFs and anything else: the builder's price), bought in whole units within its share,
   and everything is kept within the cash available, charges included (all shares shrink alike when the fund is
   bigger than the cash). Nothing is saved until "Buy" is pressed; a list older than 30 minutes is dropped. */
const PF_BASKET = 'hni_pf_basket';
function pfBasketTake() {
  let b = null;
  try { b = JSON.parse(localStorage.getItem(PF_BASKET) || 'null'); localStorage.removeItem(PF_BASKET); } catch (e) { return null; }
  return b && Array.isArray(b.items) && b.items.length && Date.now() - (b.at || 0) < 30 * 60000 ? b : null;
}
function pfBasketPlan(items, cash) {
  const want = items.reduce((s, x) => s + x.target, 0);
  let k = want > 0 && cash > 0 ? Math.min(1, cash / want) : 0;
  const rowsAt = k => items.map(x => { const qty = k > 0 ? Math.floor(x.target * k / x.px) : 0;
    return Object.assign({}, x, {qty, amt: qty * x.px, ch: qty > 0 ? pfCharges('buy', qty, x.px) : 0}); });
  let rows = rowsAt(k), tot = rows.reduce((s, r) => s + r.amt + r.ch, 0);
  for (let i = 0; i < 400 && k > 0 && tot > cash + 1e-6; i++) {       // charges on top: shrink a little until it all fits
    k *= 0.997; rows = rowsAt(k); tot = rows.reduce((s, r) => s + r.amt + r.ch, 0);
  }
  return {rows, tot, k, want};
}
function pfBasketOpen() {
  if (!PF.txns || document.querySelector('.wip-back')) return;
  const b = pfBasketTake(); if (!b) return;
  const items = b.items.map(x => ({sym: String(x.sym || '').toUpperCase(), name: x.name || '', target: +x.target || 0, etf: !!x.etf, src: String(x.src || 'PF Builder').slice(0, 60),
      live: !!(pfLive(String(x.sym || '').toUpperCase()) || {}).ltp, px: pfLtp(String(x.sym || '').toUpperCase()) || +x.px || 0}))
    .filter(x => /^[A-Z0-9&._-]{1,20}$/.test(x.sym) && x.px > 0 && x.target > 0);
  if (!items.length) return;
  const cash = Math.max(0, Math.floor(pfReplay(PF.txns).cash * 100) / 100);
  const full = pfBasketPlan(items, Infinity);                            // everything, at the fund the builder used
  const fit = pfBasketPlan(items, cash);                                 // within the cash you have
  const short = Math.max(0, Math.ceil(full.tot - cash));
  const row = r => `<tr${r.qty ? '' : ' class="z"'}><td title="${esc(r.src)}"><b>${esc(r.sym)}</b>${r.etf ? ' <i>ETF</i>' : ''}<div class="pf-src">${esc(r.src.replace(/^PF Builder → /, ''))}</div></td><td class="r">${fmt(r.qty, 0)}</td>
    <td class="r" title="${r.live ? 'Live price (Angel)' : 'Latest close'}">${fmt(r.px, 2)}${r.live ? ' <i>live</i>' : ''}</td><td class="r">${fmt(r.amt, 0)}</td></tr>`;
  const w = modal('Add from Portfolio Builder', `<form class="pf-form pfb" autocomplete="off">
    <p class="pf-calc">${items.length} picks${b.scope ? ' (' + esc(b.scope) + ')' : ''} · fund ${rs(b.fund)}${b.date ? ' · data as of ' + esc(b.date) : ''}. Quantities are at the LTP and stay within your cash, charges included.</p>
    <div class="pfb-tw"><table class="pfb-tbl"><thead><tr><th>Stock</th><th class="r">Qty</th><th class="r">LTP ₹</th><th class="r">Amount ₹</th></tr></thead>
      <tbody>${fit.rows.map(row).join('')}</tbody></table></div>
    <div class="pfb-sum"><span>Cash available <b>${rs(cash)}</b></span><span>To invest <b>${rs(fit.tot)}</b> <i>incl. charges ${rs(fit.rows.reduce((s, r) => s + r.ch, 0))}</i></span><span>Cash after <b>${rs(cash - fit.tot)}</b></span></div>
    ${fit.rows.some(r => !r.qty) ? '<p class="pf-calc">Greyed rows: one unit costs more than that pick\'s share of the cash, so they are skipped.</p>' : ''}
    ${short > 0 ? `<p class="pf-calc">Your cash is ${rs(short)} short of the builder's ${rs(b.fund)}${cash > 0 ? ', so every pick is scaled down alike' : ''}.</p>` : ''}
    <p class="pf-err"></p>
    <button type="submit" class="btn primary"${fit.rows.some(r => r.qty) ? '' : ' disabled'}>Buy within cash · ${fit.rows.filter(r => r.qty).length} stocks</button>
    ${short > 0 ? `<button type="button" class="btn ghost" data-payin>Pay in ${rs(short)} and buy the full list</button>` : ''}
  </form>`, {sheet: true});
  const f = $('form', w);
  const save = async (plan, payIn) => {
    const buys = plan.rows.filter(r => r.qty > 0), d = today();
    if (!buys.length) return;
    if (PF.txns.length + buys.length + (payIn ? 1 : 0) > 3000) return $('.pf-err', w).textContent = 'Transaction limit reached (3000).';
    const add = [];
    if (payIn) add.push({id: pfId(), t: 'in', amt: payIn, date: d});
    buys.forEach(r => {
      const rec = {id: pfId(), t: 'buy', sym: r.sym, qty: r.qty, px: +r.px.toFixed(2), ch: r.ch, date: d, src: r.src};
      const s0 = TODAY.get(r.sym);                                       // PE and value score on the day bought (checklist)
      if (s0 && s0.pe > 0) rec.pe = +(+s0.pe).toFixed(2);
      if (s0 && s0.score != null) rec.sc = Math.round(s0.score);
      if (!s0) rec.name = String(r.name || r.sym).slice(0, 80);
      add.push(rec);
    });
    f.querySelectorAll('button').forEach(x => { x.disabled = true; });
    PF.txns = PF.txns.concat(add);
    await pfSave(); w._close();
    if ((location.hash || '') === '#/portfolio') route(); else pfMarkBags();
    toast(`Added ${buys.length} buys · ${rs(buys.reduce((s, r) => s + r.amt, 0))} invested${PF.err ? ' · ' + PF.err : ''}`);
  };
  f.addEventListener('submit', e => { e.preventDefault(); save(fit, 0); });
  const pi = $('[data-payin]', f); if (pi) pi.addEventListener('click', () => save(full, short));
}
window.addEventListener('hashchange', () => { if (/^#\/portfolio/.test(location.hash) && PF.txns) setTimeout(pfBasketOpen, 200); });
/* PF Builder button: every signed-in user sees it. Admins and prime members open the builder (auth.js window.hniBuilder:
   admin@ account, hni_admins/{uid}, or a hni_prime/{uid} document still in date); everyone else gets the plans popup (window.hniPlans). */
(function () {
  const b = $('#pfBuilder'); if (!b) return;
  let ok = null;
  const check = () => { const w = window.hniBuilder || window.hniAdmin; if (w) w.then(a => { ok = !!a; }).catch(() => { ok = false; }); };
  check(); window.addEventListener('hni-ready', check);
  const phone = () => matchMedia('(max-width:700px)').matches;
  b.addEventListener('click', async e => {
    if (ok === true) {                                        // allowed: new tab on a computer, same tab on a phone
      if (phone()) { e.preventDefault(); location.href = b.href; }
      return;
    }
    e.preventDefault();
    if (ok === null) {                                        // tapped before the check finished: wait for it
      const w = window.hniBuilder || window.hniAdmin;
      ok = w ? !!(await w.catch(() => false)) : false;
      if (ok) { location.href = b.href; return; }
    }
    if (window.hniPlans) window.hniPlans('builder');             // plans here; PF Builder opens only after a payment
  });
})();
/* NSE renamed a held stock (portfolio_ltp.py publishes OLD → NEW): open holdings move to the new symbol by themselves.
   Closed (locked) records keep their old symbol. */
async function pfApplyRenames() {
  const ren = (PF.mkt && PF.mkt.renames) || {};
  if (!PF.txns || !Object.keys(ren).length) return;
  const lock = pfReplay(PF.txns).lock, done = new Set();
  PF.txns = PF.txns.map(t => (t.sym && ren[t.sym] && !lock.has(t.id)) ? (done.add(t.sym + ' → ' + ren[t.sym]), Object.assign({}, t, {sym: ren[t.sym]})) : t);
  if (done.size) { await pfSave(); toast('Renamed on NSE: ' + [...done].join(', ')); }
}
/* held symbols that portfolio_ltp.py did not find on NSE's master list today → one line, each symbol opens "Update symbol" */
function pfNotListed(open) {
  const nl = pfNotListedSet(), bad = open.filter(r => nl.has(r.sym)).map(r => r.sym);   // ETFs never count
  if (!bad.length) return '';
  return `<p class="pf-err">⚠ Not on NSE's list today: ${bad.map(x => `<button type="button" class="btn ghost" data-rn="${esc(x)}" title="Update the symbol / name">${esc(x)} ✎</button>`).join(' ')}
    — delisted, suspended or the symbol changed. Tap one to update it.</p>`;
}
/* "⟳ Live prices" (only when window.hniStore offers refresh()): asks for fresh prices, then checks the market
   doc every 15 s until they land (about 1–2 minutes), and redraws */
const pfCanRefresh = () => !!(WEB && window.hniStore && window.hniStore.canRefresh && window.hniStore.canRefresh());
let PF_POLL = null;
/* Who may fetch: admins any time, no limit. Everyone else once per 10 minutes from the LAST update (hni_market/ltp asOf,
   the same for every user: if anyone fetched at 09:30, the next fetch opens at 09:40 for all). The Worker enforces it;
   here the button never locks, it only says when the next fetch is possible. */
const PF_GAP_MIN = 10;
const istHm = ms => new Date(ms + 330 * 60000).toISOString().slice(11, 16);
async function pfRefresh(btn) {
  if (PF_POLL) return toast('Fetching live prices…');
  try { const m = await window.hniStore.market(); if (m && m.asOf && (!PF.mkt || m.asOf !== PF.mkt.asOf)) { PF.mkt = m; if ((location.hash || '') === '#/portfolio') route(); } } catch (e) {}   // latest shared time
  const admin = window.hniAdmin ? await window.hniAdmin.catch(() => false) : false;
  if (!admin && PF.mkt && PF.mkt.asOf) {
    const next = Date.parse(PF.mkt.asOf.replace(' ', 'T') + ':00+05:30') + PF_GAP_MIN * 60000;
    if (Date.now() < next) return toast(`Prices can be fetched only after ${PF_GAP_MIN} minutes (${istHm(next)})`);
  }
  btn.classList.add('spin');
  let r; try { r = await window.hniStore.refresh(); } catch (e) { r = {error: 'no connection'}; }
  if (r.error && !r.running) { btn.classList.remove('spin'); return toast(r.wait ? r.error : 'Live prices: ' + r.error); }
  toast('Live prices in about a minute');
  const was = PF.mkt && PF.mkt.asOf, t0 = Date.now();
  PF_POLL = setInterval(async () => {
    let m = null; try { m = await window.hniStore.market(); } catch (e) {}
    const fresh = m && m.asOf && m.asOf !== was;
    if (!fresh && Date.now() - t0 < 6 * 60000) return;
    clearInterval(PF_POLL); PF_POLL = null;
    if (fresh) { PF.mkt = m; await pfApplyRenames(); PF_HELD = pfHeld(); toast('Live prices updated · ' + m.asOf.slice(11) + ' IST'); }
    else toast('No new prices yet. See the Actions tab on GitHub.');
    if ((location.hash || '') === '#/portfolio') route();
  }, 15000);
}
window.addEventListener('hni-ext', () => { if ((location.hash || '') === '#/portfolio' && PF.txns) route(); });   // refresh() became available → show the button
/* LTP: live Angel price for held stocks when portfolio_ltp.py has run, else the latest close in this page */
const pfLive = sym => PF.mkt && PF.mkt.prices && PF.mkt.prices[sym];
const pfLtp = sym => { const l = pfLive(sym); if (l && l.ltp) return l.ltp; const s = TODAY.get(sym); return s && ok(s.cmp) ? s.cmp : null; };
/* the bag next to a company name turns brass when you hold that stock */
const pfHeld = () => { const h = new Set(); if (PF.txns) Object.values(pfReplay(PF.txns).pos).forEach(p => { if (p.qty > 0) h.add(p.sym); }); return h; };
let PF_HELD = new Set();                                  // symbols held now; worked out once per change, read by every Company cell
function pfMarkBags() { PF_HELD = pfHeld(); document.querySelectorAll('[data-bag]').forEach(b => b.classList.toggle('held', PF_HELD.has(b.dataset.bag))); }
async function pfSave() {
  PF_HELD = pfHeld();
  try { await pfStore().save({txns: PF.txns, rates: PF.rates}); PF.err = ''; }
  catch (e) { PF.err = 'Could not save (' + (e.code || e) + '). Your last change may be lost on reload.'; }
}
/* charges for one equity-delivery order (₹), from the editable rates */
function pfCharges(t, qty, px, r = PF.rates) {
  const v = qty * px;
  let brok = v * r.brokPct / 100; if (r.brokCap > 0) brok = Math.min(brok, r.brokCap);
  const exch = v * r.exch / 100, sebi = v * r.sebi / 100;
  const tot = brok + v * r.stt / 100 + exch + sebi + (t === 'buy' ? v * r.stamp / 100 : 0) + (brok + exch + sebi) * r.gst / 100 + (t === 'sell' ? r.dp : 0);
  return Math.round(tot * 100) / 100;
}
const pfSorted = txns => txns.map((x, i) => [x, i]).sort((a, b) => (a[0].date || '').localeCompare(b[0].date || '') || a[1] - b[1]).map(p => p[0]);   // by date, then entry order
/* replay every transaction: average-cost holdings, realised P&L, charges and cash */
function pfReplay(txns) {
  const pos = {}, led = [], closed = [], lock = new Set();
  let cash = 0, payIn = 0, payOut = 0, charges = 0, realized = 0, bad = null;
  const fresh = x => ({sym: x.sym, name: x.name, qty: 0, cost: 0, realized: 0, charges: 0, bQty: 0, bAmt: 0, sQty: 0, sAmt: 0, first: x.date, last: x.date, ids: [], srcs: []});
  for (const x of pfSorted(txns)) {
    const amt = (x.qty || 0) * (x.px || 0), ch = +x.ch || 0;
    if (x.t === 'in') { cash += x.amt; payIn += x.amt; }
    else if (x.t === 'out') { cash -= x.amt; payOut += x.amt; }
    else {
      const p = pos[x.sym] = pos[x.sym] || fresh(x);
      p.ids.push(x.id); if (x.name) p.name = x.name; if (x.t === 'buy' && x.src && !p.srcs.includes(x.src)) p.srcs.push(x.src);   // Remark: where its buys came from p.charges += ch; charges += ch; p.last = x.date || p.last;
      if (x.t === 'buy') { p.qty += x.qty; p.cost += amt; p.bQty += x.qty; p.bAmt += amt; cash -= amt + ch; }
      else {                                              // sell at average cost; never more than held at that date
        if (x.qty > p.qty + 1e-9) bad = bad || x;
        const q = Math.min(x.qty, p.qty), avg = p.qty ? p.cost / p.qty : 0;
        p.realized += (x.px - avg) * q; realized += (x.px - avg) * q;
        p.cost -= avg * q; p.qty -= q; p.sQty += q; p.sAmt += q * x.px; cash += amt - ch;
        if (p.qty < 1e-9) {                               // sold out: this round of trades becomes a closed record, its entries lock
          p.qty = 0; p.cost = 0; closed.push(p); p.ids.forEach(id => lock.add(id)); delete pos[x.sym];
        }
      }
    }
    led.push({x, cash});
  }
  return {pos, led, closed, lock, cash, payIn, payOut, charges, realized, bad};
}
const TODAY = new Map(D.stocks.map(s => [s.sym, s]));
const pfKnown = c => Object.assign({}, c, {v: r => r.known ? c.v(r) : null, f: r => r.known ? c.f(r) : NA});   // not in the lists → blank
const rs = (v, plus) => (v < 0 ? '−' : plus && v > 0 ? '+' : '') + '₹' + fmt(Math.abs(v), 0);   // −₹6, not ₹-6
const signed = (v, d = 0) => v == null ? NA : `<span class="${v >= 0 ? 'up' : 'dn'}">${v > 0 ? '+' : ''}${fmt(v, d)}</span>`;
/* summary numbers: plain text colour, direction shown only by a small green ▲ or red ▼ */
const arr = v => v == null || !v ? '' : `<i class="pf-ar ${v > 0 ? 'up' : 'dn'}">${v > 0 ? '▲' : '▼'}</i>`;
const plain = (v, d = 0) => v == null ? '–' : `${arr(v)}${v > 0 ? '+' : v < 0 ? '−' : ''}${fmt(Math.abs(v), d)}`;
const pctTxt = (v, d = 2) => v == null ? '' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${fmt(Math.abs(v), d)}%`;
const pfSymCell = r => r.known ? symCell(r) : `<b>${esc(r.sym)}</b> <span class="na" title="Not in NIFTY 500 / Smallcap 500 / Microcap 250, so no price or scores (valued at cost)">not in list</span>`;
const pnlCell = r => r.priced ? `${signed(r.pnl)} <small class="${r.pnl >= 0 ? 'up' : 'dn'}">${r.pnlPct > 0 ? '+' : ''}${fmt(r.pnlPct, 1)}%</small>` : NA;
const PFC = {
  edit: {k:'edit', label:'', noCsv:1, v:r => null, f:r => `<button type="button" class="pf-ico" data-trade="${esc(r.sym)}" title="Buy or sell ${esc(r.sym)}" aria-label="Buy or sell ${esc(r.sym)}">✎</button>`},
  sym: Object.assign({}, C.sym, {f: r => pfSymCell(r) + (r.known || r.name !== r.sym ? `<div class="pf-co">${esc(r.name)}</div>` : '')}),
  qty: {k:'qty', label:'Qty', v:r => r.qty, f:r => fmt(r.qty, 0)},
  avg: {k:'avg', label:'Avg ₹', tip:'Average buy price', v:r => r.avg, f:r => fmt(r.avg, 2)},
  cmp: {k:'ltp', label:'LTP ₹', v:r => r.ltp, f:r => r.ltp == null ? NA : fmt(r.ltp, 2)},
  day: {k:'day', label:'Day', tip:'Change since yesterday\'s close (live prices)', v:r => r.dayPct, x:r => r.day == null ? '' : fmt(r.day, 0),
        f:r => r.day == null ? NA : `${signed(r.day)} <small class="${r.day >= 0 ? 'up' : 'dn'}">${r.dayPct > 0 ? '+' : ''}${fmt(r.dayPct, 1)}%</small>`},
  val: {k:'val', label:'Value ₹', v:r => r.value, f:r => r.priced ? fmt(r.value, 0) : `<span class="na" title="No price: valued at cost">${fmt(r.value, 0)}</span>`},
  pnl: {k:'pnl', label:'P&L', tip:'Unrealised profit / loss, ₹ and %', v:r => r.priced ? r.pnlPct : null, x:r => r.priced ? fmt(r.pnl, 0) : '', f:pnlCell},
  wt: {k:'wt', label:'Wt %', tip:'Share of the holdings value', v:r => r.wt, f:r => fmt(r.wt, 1)},
  vd: {k:'vd', label:'Checklist', tip:'Checklist light (green / amber / red) and its score: tap the stock name to see every check. Not investment advice.', v:r => r.vd ? r.vd.pct : null, x:r => r.vd ? r.vd.label + ' ' + Math.round(r.vd.pct) + '%' : '', f:r => r.vd ? vdChip(r.vd) : pfIsEtf(r.sym, r.name) ? etfChip() : NA},
  score: Object.assign(pfKnown(C.score), {label:'Score'}), mom: Object.assign(pfKnown(C.mom), {label:'Mom.'}), trend: pfKnown(C.trend), flags: pfKnown(C.flags),
  ind: pfKnown(C.ind), name: pfKnown(Object.assign({}, C.name, {f: s => `<span class="co">${esc(s.name)}</span>`})),
  msym: Object.assign({}, C.sym, {f: r => pfSymCell(r) + `<div class="pf-co">${fmt(r.qty, 0)} @ ${fmt(r.avg, 2)}</div>` + (r.remark ? `<div class="pf-src">${esc(r.remark)}</div>` : '')}),
  rem: {k:'rem', label:'Remark', l:1, tip:'Where the stock was added from (PF Builder part, Factor Screener tab or Portfolio)', v:r => r.remark || '', f:r => r.remark ? `<span class="pf-src">${esc(r.remark)}</span>` : NA},
  /* closed positions */
  csym: Object.assign({}, C.sym, {f: pfSymCell}),
  cqty: {k:'cq', label:'Qty', v:r => r.sQty, f:r => fmt(r.sQty, 0)},
  cpx: {k:'cpx', label:'Buy → Sell ₹', v:r => r.sQty ? r.sAmt / r.sQty : null, f:r => `${fmt(r.bQty ? r.bAmt / r.bQty : null, 2)} → ${fmt(r.sQty ? r.sAmt / r.sQty : null, 2)}`},
  net: {k:'net', label:'Net ₹', tip:'Realised profit minus charges', v:r => r.realized - r.charges,
        f:r => `<span title="Realised ${fmt(r.realized, 0)} − charges ${fmt(r.charges, 2)}">${signed(r.realized - r.charges)}</span>`},
  closed: {k:'closed', label:'Closed', v:r => r.last || '', f:r => r.last ? esc(r.last) : NA},
  rm: {k:'rm', label:'', noCsv:1, v:r => null, f:r => `<button type="button" class="pf-rm" data-rm="${r.ci}" title="Remove this closed position and all its trades">Remove</button>`},
};
const TX_LABEL = {buy:'Buy', sell:'Sell', in:'Pay in', out:'Pay out'};
const TXC = {
  src: {k:'src', label:'Remark', l:1, tip:'Where the trade came from', v:r => r.x.src || '', f:r => r.x.src ? `<span class="pf-src">${esc(r.x.src)}</span>` : NA},
  date: {k:'date', label:'Date', l:1, v:r => r.x.date || '', f:r => r.x.date ? esc(r.x.date) : NA},
  type: {k:'type', label:'Type', l:1, v:r => r.x.t, f:r => `<span class="pf-t ${r.x.t}">${TX_LABEL[r.x.t]}</span>`},
  mtype: {k:'mtype', label:'Type', l:1, v:r => r.x.date || '', f:r => `<span class="pf-t ${r.x.t}">${TX_LABEL[r.x.t]}</span><div class="pf-co">${r.x.date ? esc(r.x.date.slice(2)) : ''}</div>`},
  what: {k:'what', label:'Detail', l:1, v:r => r.sym, x:r => r.x.sym ? `${r.x.sym} ${r.x.qty} @ ${r.x.px}` : '',
         f:r => r.x.sym ? `<b>${esc(r.x.sym)}</b> <span class="na">${fmt(r.x.qty, 0)} @ ${fmt(r.x.px, 2)}</span>` : NA},
  mwhat: {k:'what', label:'Detail', l:1, v:r => r.sym, f:r => (r.x.sym ? `<b>${esc(r.x.sym)}</b> <span class="na">${fmt(r.x.qty, 0)} @ ${fmt(r.x.px, 2)}</span>` : NA) + (r.x.src ? `<div class="pf-src">${esc(r.x.src)}</div>` : '')},   // phone: remark under the detail
  amt: {k:'tamt', label:'Cash ₹', tip:'Effect on cash, charges included', v:r => r.amt,
        f:r => `<span title="${r.x.ch ? 'Charges ₹' + fmt(r.x.ch, 2) + ' · ' : ''}Cash after ₹${fmt(r.cash, 0)}">${signed(r.amt)}</span>`},
  act: {k:'tact', label:'', noCsv:1, v:r => null, f:r => PF_LAST && PF_LAST.lock.has(r.x.id)
         ? `<span class="pf-lock" title="Part of a closed position: remove it from the Closed list instead">🔒</span>` : `<span class="pf-acts2"><button type="button" class="pf-ico" data-edit="${esc(r.x.id)}" title="Edit" aria-label="Edit">✎</button><button type="button" class="pf-ico del" data-del="${esc(r.x.id)}" title="Delete" aria-label="Delete">×</button></span>`},
};
/* news of the last 7 days for the stocks held (news.json, written by the build from fetch_news.py) */
/* news.json = the 3 lists' stocks; any other stock's news is in news_more.json (j.m lists them), fetched only
   when one of those stocks' news is wanted (syms) */
let NEWS = null, NEWS_P = null, NEWS_M = new Set(), NEWS_MP = null, NEWS_MD = false;
const newsGet = u => fetch(u, {cache: 'no-cache'}).then(r => r.ok ? r.json() : {}).catch(() => ({}));
function newsAdd(j) {
  const dt = t => `20${t.slice(0, 2)}-${t.slice(2, 4)}-${t.slice(4, 6)} ${t.slice(6, 8)}:${t.slice(8, 10)}`;
  for (const sym in (j.s || {})) NEWS[sym] = j.s[sym].map(i => { const a = j.a[i];
    return [dt(a[0]), a[1] + (a[2] ? ' – ' + a[2] : ''), a[3][0] === '!' ? a[3].slice(1) : j.p + a[3] + j.q]; });
}
const newsNeedMore = syms => !NEWS_MD && (syms || []).some(x => NEWS_M.has(x));
function loadNewsMap(syms) {                                // news.json (build.py): compact, shared with the screener
  NEWS_P = NEWS_P || newsGet('news.json').then(j => { NEWS = {}; NEWS_M = new Set(j.m || []); newsAdd(j); return NEWS; });
  return NEWS_P.then(N => !newsNeedMore(syms) ? N
    : NEWS_MP = NEWS_MP || newsGet('news_more.json').then(j => { newsAdd(j); NEWS_MD = true; return NEWS; }));
}
function pfNewsPanel(host, syms) {
  const p = document.createElement('section'); p.className = 'pf-panel pf-news'; host.appendChild(p);
  p.innerHTML = '<h3 class="pf-h">News <span>7 days</span></h3><div class="pf-nl"><p class="na">Loading…</p></div>';
  const paint = () => {
    const items = [];
    syms.forEach(sym => (NEWS[sym] || []).forEach(n => items.push([n[0], sym, n[1], n[2]])));
    items.sort((a, b) => b[0].localeCompare(a[0]));
    const d = v => { const t = new Date(v.replace(' ', 'T')); return isNaN(t) ? v : t.toLocaleDateString('en-IN', {day: '2-digit', month: 'short'}); };
    $('.pf-h span', p).textContent = items.length + ' · 7 days';
    $('.pf-nl', p).innerHTML = items.length ? items.slice(0, 60).map(n => `<a class="pf-ni" href="${esc(n[3] || '#')}" target="_blank" rel="noopener noreferrer">
        <span class="d">${esc(d(n[0]))}</span><b data-news="${esc(n[1])}" title="All news for ${esc(n[1])}">${esc(n[1])}</b><span class="t">${esc(n[2])}</span></a>`).join('')
      : `<p class="na">${syms.length ? 'No news in the last 7 days for your holdings.' : 'News for the stocks you hold shows here.'}</p>`;
  };
  if (NEWS && !newsNeedMore(syms)) return paint();
  loadNewsMap(syms).then(() => { if (p.isConnected) paint(); });
}
/* a table inside a panel: title on the left of its own tool row, body scrolls inside the panel */
function pfPanel(host, title, sub, table) {
  const p = document.createElement('section'); p.className = 'pf-panel'; host.appendChild(p);
  p.appendChild(table);
  const bar = table.querySelector('.tbar');
  const h = `<h3 class="pf-h">${title}${sub != null ? ` <span>${sub}</span>` : ''}</h3>`;
  if (bar) { bar.insertAdjacentHTML('afterbegin', h); const q = bar.querySelector('input[type=search]'); if (q) bar.querySelector('.pf-h').after(q); }   // search sits right after the title
  else p.insertAdjacentHTML('afterbegin', h);
  return p;
}
let P_DAY = '';                                            // the trading day of the live prices (yyyy-mm-dd)
function pfSummary(txns, mkt) {
  const keep = PF.mkt; PF.mkt = mkt === undefined ? PF.mkt : mkt;   // a market doc passed in is used instead of the page's own
  P_DAY = (PF.mkt && PF.mkt.asOf) || '';
  const R = pfReplay(txns || []);
  const withData = p => { const s = TODAY.get(p.sym); return Object.assign({}, s || {ind: ''}, p, {known: !!s, name: (s && s.name) || p.name || p.sym}); };   // list name wins; typed name for others
  const open = Object.values(R.pos).filter(p => p.qty > 0).map(p => {
    const r = withData(p), px = pfLtp(p.sym), lv = pfLive(p.sym);
    r.avg = p.cost / p.qty; r.priced = px != null; r.ltp = px; r.remark = (p.srcs || []).join(' · ');
    r.value = r.priced ? p.qty * px : p.cost;              // no price → valued at cost
    r.pnl = r.value - p.cost; r.pnlPct = p.cost ? r.pnl / p.cost * 100 : null;
    if (lv && lv.ltp && lv.prev) {
      /* today's gain: shares held from before today move from yesterday's close; shares bought today move from their
         buy price (else a stock bought this morning would show yesterday-to-now as "your" gain) */
      const d = (P_DAY || '').slice(0, 10), bt = (txns || []).filter(t => t.t === 'buy' && t.sym === p.sym && t.date === d);
      const qNew = Math.min(p.qty, bt.reduce((a, t) => a + t.qty, 0)), qOld = p.qty - qNew;
      const pxNew = qNew ? bt.reduce((a, t) => a + t.qty * t.px, 0) / bt.reduce((a, t) => a + t.qty, 0) : 0;
      r.day = qOld * (lv.ltp - lv.prev) + qNew * (lv.ltp - pxNew);
      const base = qOld * lv.prev + qNew * pxNew; r.dayPct = base ? r.day / base * 100 : null;
    } else { r.day = null; r.dayPct = null; }
    return r;
  });
  const closed = R.closed.map((p, i) => Object.assign(withData(p), {ci: i}));
  const hold = open.reduce((t, r) => t + r.value, 0), cost = open.reduce((t, r) => t + r.cost, 0);
  open.forEach(r => r.wt = hold ? r.value / hold * 100 : null);
  const unreal = open.filter(r => r.priced).reduce((t, r) => t + r.pnl, 0);
  const dayRows = open.filter(r => r.day != null), day = dayRows.length ? dayRows.reduce((t, r) => t + r.day, 0) : null;
  const dayBase = dayRows.reduce((t, r) => t + r.value - r.day, 0);   // value at yesterday's close (or buy price for today's buys)
  const worth = R.cash + hold, deposits = R.payIn - R.payOut, gain = worth - deposits;
  const gainPct = deposits > 0 ? gain / deposits * 100 : (cost ? gain / cost * 100 : null);
  const out = {R, open, closed, hold, cost, unreal, day, dayPct: dayBase ? day / dayBase * 100 : null, worth, deposits, gain, gainPct, asOf: PF.mkt && PF.mkt.asOf};
  PF.mkt = keep;
  return out;
}
/* portfolio figures for a {txns} doc and a market doc, plus the page's number formatters */
window.hniPf = {
  calc(d, mkt) { const t = pfTxnsOf(d); return {P: pfSummary(t, mkt), txns: pfSorted(t)}; },
  fmt, rs, signed, esc, TX_LABEL,
};
function portfolioView() {
  setNav('portfolio'); view.innerHTML = '';
  if (BACK) {                                             // a portfolio is about today; Historical would mix dates
    view.insertAdjacentHTML('beforeend', `<div class="pf-note">Portfolio uses today's prices. <button type="button" class="btn" id="pfNow">Switch Historical off</button></div>`);
    $('#pfNow').addEventListener('click', () => setHistorical(false));
    return;
  }
  if (!PF.txns) {
    pfLoad();
    view.insertAdjacentHTML('beforeend', `<p class="hint">${PF.err ? esc(PF.err) : 'Loading your portfolio…'}</p>`);
    return;
  }
  const P = pfSummary(PF.txns), R = P.R, open = P.open, closed = P.closed;
  const {hold, cost, unreal, worth, deposits, gain, gainPct} = P;
  PF_LAST = R;
  const tile = (k, v, sub, cls = '') => `<div class="pf-k ${cls}"><span>${k}</span><b>${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
  if (!THEN_BY.size) ensureThen1();                       // the checklist compares PE with a year ago
  if (isPhone()) return pfPhone(P);                       // phone: Zerodha-style cards, nothing scrolls sideways
  view.insertAdjacentHTML('beforeend', `${pfActsHtml()}${PF.err ? `<p class="pf-err">${esc(PF.err)}</p>` : ''}
    ${pfNotListed(open)}
    ${R.bad ? `<p class="pf-err">A sale of ${esc(R.bad.sym)} on ${esc(R.bad.date || 'an undated day')} is larger than the shares held then. Check the dates in Transactions.</p>` : ''}
    <div class="pf-sum">
      ${tile('Net gain', arr(gain) + rs(gain, true), gainPct == null ? '' : pctTxt(gainPct, 1) + ' on money put in', 'gain')}
      ${tile('Day gain', plain(P.day), P.day == null ? 'live prices not on yet' : pctTxt(P.dayPct))}
      ${tile('Net worth', rs(worth), 'holdings + cash')}
      ${tile('Cash', `<span class="${R.cash < 0 ? 'dn' : ''}">${rs(R.cash)}</span>`, R.cash < 0 && !R.payIn ? 'add a Pay in' : 'free to invest')}
      ${tile('Holdings', rs(hold), `${open.length} open · cost ${rs(cost)}`)}
      ${tile('Unrealised', plain(unreal), cost ? `${pctTxt(unreal / cost * 100, 1)} on cost` : 'open positions')}
      ${tile('Realised', plain(R.realized - R.charges), `after ₹${fmt(R.charges, 0)} charges`)}
      ${tile('Money in', rs(deposits), `in ${rs(R.payIn)} · out ${rs(R.payOut)}`)}
    </div>
    <div class="pf-grid"><div class="pf-left"></div><div class="pf-right"></div></div>`);
  const L = $('.pf-left'), RT = $('.pf-right'), phone = isPhone();
  const hCols = phone ? [PFC.edit, PFC.msym, PFC.val, PFC.pnl]          // phone: qty sits under the symbol, so nothing scrolls sideways
                      : [PFC.edit, PFC.sym, PFC.rem, PFC.vd, PFC.qty, PFC.avg, PFC.cmp, ...(P.day != null ? [PFC.day] : []), PFC.val, PFC.pnl, PFC.wt, PFC.score, PFC.mom, PFC.trend, PFC.flags];
  open.forEach(r => { r.vd = verdict(r.sym, P); });
  pfPanel(L, 'Holdings', open.length, makeTable('pf', hCols, open, {sortKey:'val', sortDir:-1, search:!phone || open.length > 6, noIndex:true, csv:() => 'Portfolio_holdings'}));
  if (!open.length) L.querySelector('tbody').innerHTML = `<tr><td class="l na" colspan="13">No open holdings. Start with <b>Pay in</b> (your opening cash), then <b>+ Buy / Sell</b> — or tap the bag next to any industry name on the other tabs.</td></tr>`;
  pfNewsPanel(RT, open.map(r => r.sym));
  pfPanel(RT, 'Closed', closed.length, makeTable('pfc', phone ? [PFC.csym, PFC.net, PFC.rm] : [PFC.csym, PFC.cqty, PFC.cpx, PFC.net, PFC.closed, PFC.rm],
    closed, {sortKey:'closed', sortDir:-1, noIndex:true, csv:() => 'Portfolio_closed'}));
  if (!closed.length) RT.querySelector('tbody').innerHTML = '<tr><td class="l na" colspan="5">Stocks you sell fully move here; the money goes back to Cash.</td></tr>';
  const ledRows = R.led.map(l => ({x: l.x, cash: l.cash, sym: l.x.sym || '', name: '', ind: '',
    amt: l.x.t === 'in' ? l.x.amt : l.x.t === 'out' ? -l.x.amt : l.x.t === 'buy' ? -(l.x.qty * l.x.px + (l.x.ch || 0)) : l.x.qty * l.x.px - (l.x.ch || 0)})).reverse();
  pfPanel(RT, 'Transactions', ledRows.length, makeTable('pft', phone ? [TXC.mtype, TXC.mwhat, TXC.amt, TXC.act] : [TXC.date, TXC.type, TXC.what, TXC.src, TXC.amt, TXC.act],
    ledRows, {noIndex:true, csv:() => 'Portfolio_transactions'}));
  const tb = RT.querySelectorAll('.tbar')[1];
  if (tb && ledRows.length) tb.querySelector('.pf-h').insertAdjacentHTML('afterend', '<button type="button" class="pf-rm all" id="pfWipe" title="Delete every entry in your portfolio">Delete all</button>');
  if (!ledRows.length) RT.querySelectorAll('tbody')[1].innerHTML = '<tr><td class="l na" colspan="5">Every Pay in, Pay out, Buy and Sell shows here, newest first. ✎ edits, × deletes.</td></tr>';
  pfWireTop();
}
/* the action buttons and "Delete all" (desktop and phone) */
function pfWireTop() {
  view.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.open; k === 'trade' ? pfTradeForm({}) : k === 'rates' ? pfRatesForm() : k === 'ltp' ? pfRefresh(b) : pfCashForm({t: 'in'});   // cash: Pay in / Pay out picked inside
  }));
  const wipe = $('#pfWipe');
  if (wipe) wipe.addEventListener('click', () => typeConfirm('Delete all', `Every holding, closed position, pay in / out and trade (${PF.txns.length} entries) is deleted. Your charge rates stay.`,
    async () => { PF.txns = []; await pfSave(); route(); }));
}
/* icons: ₹ on a tax document (charges & taxes), circular arrow (refresh live prices) */
const IC_TAX = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 2.8h8.5L19 7.3v13.9H6z"/><path d="M14.3 2.8v4.6H19"/><path d="M9.3 10.6h6M9.3 13h6M12.6 10.6c1.9 0 1.9 4.8 0 4.8H9.6l4.6 3.9"/></svg>';
const IC_REF = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.35-5.65"/><path d="M20 4v4.6h-4.6"/></svg>';
const pfActsHtml = () => `<div class="pf-top"><div class="pf-acts">
    <button type="button" class="btn primary" data-open="trade">+ Buy / Sell</button>
    <button type="button" class="btn" data-open="cash">Pay in / out</button>
    <button type="button" class="btn ghost ico" data-open="rates" title="Charges &amp; taxes on each trade" aria-label="Charges and taxes">${IC_TAX}</button>
    ${pfCanRefresh() ? `<button type="button" class="btn ghost ico ltp${PF_POLL ? ' spin' : ''}" data-open="ltp" title="Refresh live prices${pfAsOf().full ? ' (now as of ' + esc(pfAsOf().full) + ' IST)' : ''}" aria-label="Refresh live prices"${PF_POLL ? ' disabled' : ''}>${IC_REF}${pfAsOf().short ? `<span class="pf-asof">${esc(pfAsOf().short)}</span>` : ''}</button>` : ''}
  </div>${pfIdxHtml()}</div>`;
/* when the live prices were last fetched (same for every account): "12:20" today, else "13 Oct" */
function pfAsOf() {
  const a = String((PF.mkt && PF.mkt.asOf) || '');
  if (!/^\d{4}-\d\d-\d\d \d\d:\d\d/.test(a)) return {};
  const todayIst = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
  const d = new Date(a.slice(0, 10) + 'T00:00:00Z');
  const dm = d.getUTCDate() + ' ' + d.toLocaleString('en-IN', {month: 'short', timeZone: 'UTC'});
  return {short: a.slice(0, 10) === todayIst ? a.slice(11, 16) : dm, full: dm + ', ' + a.slice(11, 16)};
}
/* NIFTY 50 and BANK NIFTY: last price and the day's move (from hni_market/ltp "idx") */
function pfIdxHtml() {
  const I = PF.mkt && PF.mkt.idx;
  if (!I) return '';
  const one = (k, lab) => {
    const x = I[k]; if (!x || !x.ltp) return '';
    const ch = x.prev ? x.ltp - x.prev : null, pct = ch != null ? ch / x.prev * 100 : null;
    return `<span class="pf-ix"><b>${lab}</b>${fmt(x.ltp, 2)}${ch == null ? '' : `<em class="${ch >= 0 ? 'up' : 'dn'}">${ch >= 0 ? '▲' : '▼'}<span class="abs"> ${fmt(Math.abs(ch), 2)} (</span>${pct >= 0 ? '+' : '−'}${fmt(Math.abs(pct), 2)}%<span class="abs">)</span></em>`}</span>`;
  };
  const h = one('NIFTY', 'NIFTY<span class="abs"> 50</span>') + one('BANKNIFTY', 'BANK NIFTY');
  return h ? `<div class="pf-idx">${h}</div>` : '';
}
/* phone portfolio, laid out like Zerodha's Holdings: one summary card, then a card per stock.
   Tapping a stock opens a soft sheet with its value / momentum details, your holding and its news. */
function pfPhone(P) {
  const R = P.R, closed = P.closed;
  const SK = PF_ZSORT[lsGet('pfzsort')] ? lsGet('pfzsort') : 'value', SD = lsGet('pfzdir') === 'up' ? 1 : -1;   // phone sort, remembered
  const open = P.open.slice().sort((a, b) => { const x = PF_ZSORT[SK].v(a), y = PF_ZSORT[SK].v(b);
    if (x == null || x === '') return 1; if (y == null || y === '') return -1;   // blanks always last
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * SD; });
  const cur = P.hold, inv = P.cost, pnl = cur - inv, cashOpen = lsGet('pfcash') === '1';
  const f2 = v => fmt(v, 2);
  const kv = (k, v, sub) => `<div><span>${k}</span><b>${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
  view.insertAdjacentHTML('beforeend', `${pfActsHtml()}${PF.err ? `<p class="pf-err">${esc(PF.err)}</p>` : ''}
    ${pfNotListed(open)}
    ${R.bad ? `<p class="pf-err">A sale of ${esc(R.bad.sym)} on ${esc(R.bad.date || 'an undated day')} is larger than the shares held then. Check the dates in Transactions.</p>` : ''}
    <section class="pf-zc">
      <div class="pf-zr"><div><span>Invested</span><b>${f2(inv)}</b></div><div class="r"><span>Current</span><b>${f2(cur)}</b></div></div>
      <div class="pf-zl"><span>P&amp;L</span><b>${plain(pnl, 2)}<small>${inv ? pctTxt(pnl / inv * 100) : ''}</small></b></div>
      <div class="pf-zl"><span>Day's P&amp;L</span><b>${P.day == null ? '<small>live prices not on yet</small>' : `${plain(P.day, 2)}<small>${pctTxt(P.dayPct)}</small>`}</b></div>
      <details class="pf-cash"${cashOpen ? ' open' : ''}>
        <summary><span>Cash <b>${rs(R.cash)}</b></span><span>Net gain <b>${arr(P.gain)}${rs(P.gain, true)}</b></span></summary>
        <div class="pf-cg">
          ${kv('Net worth', rs(P.worth), 'holdings + cash')}
          ${kv('Net gain', arr(P.gain) + rs(P.gain, true), P.gainPct == null ? '' : pctTxt(P.gainPct, 1) + ' on money put in')}
          ${kv('Cash', rs(R.cash), R.cash < 0 && !R.payIn ? 'add a Pay in' : 'free to invest')}
          ${kv('Realised', plain(R.realized - R.charges), `after ₹${fmt(R.charges, 0)} charges`)}
          ${kv('Money in', rs(P.deposits), `in ${rs(R.payIn)} · out ${rs(R.payOut)}`)}
        </div>
      </details>
    </section>
    <div class="pf-zp"></div>`);
  $('.pf-cash').addEventListener('toggle', e => lsSet('pfcash', e.target.open ? '1' : '0'));   // remembered
  const host = $('.pf-zp');
  const sec = (title, n, extra = '') => { const p = document.createElement('section'); p.className = 'pf-panel pf-zs';
    p.innerHTML = `<h3 class="pf-h">${title} <span>${n}</span>${extra}</h3><div class="pf-zlist"></div>`; host.appendChild(p); return $('.pf-zlist', p); };
  const nl = pfNotListedSet();
  // holdings: Qty · Avg / % ; SYMBOL / P&L ; Invested / LTP (day %)
  const sortUi = open.length > 1 ? `<label class="pf-zsort"><span>Sort</span><select id="pfZSort" aria-label="Sort holdings by">${Object.entries(PF_ZSORT).map(([k, o]) => `<option value="${k}"${k === SK ? ' selected' : ''}>${o.label}</option>`).join('')}</select><button type="button" id="pfZDir" aria-label="Reverse order" title="Reverse order">${SD > 0 ? '↑' : '↓'}</button></label>` : '';
  sec('Holdings', open.length, sortUi).innerHTML = open.length ? open.map(r => {
    const c = r.priced ? (r.pnl >= 0 ? 'up' : 'dn') : '';
    return `<div class="pf-zh" data-news="${esc(r.sym)}" role="button" tabindex="0">
      <div class="a"><span>Qty. ${fmt(r.qty, 0)} <i>•</i> Avg. ${f2(r.avg)}</span><span class="${c}">${r.priced ? pctTxt(r.pnlPct) : ''}</span></div>
      <div class="b"><b>${esc(r.sym)}${nl.has(r.sym) ? ' <em class="dn">⚠</em>' : ''} ${(v => v ? vdChip(v) : pfIsEtf(r.sym, r.name) ? etfChip() : '')(r.vd || verdict(r.sym, P))}</b><b class="${c}">${r.priced ? (r.pnl > 0 ? '+' : r.pnl < 0 ? '−' : '') + f2(Math.abs(r.pnl)) : '–'}</b></div>
      <div class="a"><span>Invested ${f2(r.qty * r.avg)}</span><span>LTP ${r.ltp == null ? '–' : f2(r.ltp)}${r.dayPct != null ? ` <em class="${r.day >= 0 ? 'up' : 'dn'}">(${pctTxt(r.dayPct)})</em>` : ''}</span></div>
      ${r.remark ? `<div class="a pf-zsrc"><span class="pf-src">${esc(r.remark)}</span></div>` : ''}
    </div>`; }).join('')
    : '<p class="na pf-zempty">No open holdings. Start with <b>Pay in</b>, then <b>+ Buy / Sell</b>, or tap the bag next to any industry name on the other tabs.</p>';
  pfNewsPanel(host, open.map(r => r.sym));
  // closed: SYMBOL / net ; bought · sold · date / Remove
  sec('Closed', closed.length).innerHTML = closed.length ? closed.slice().sort((a, b) => String(b.last || '').localeCompare(String(a.last || ''))).map(r => `<div class="pf-zt">
      <div class="b"><b>${esc(r.sym)}</b><b>${signed(r.realized - r.charges)}</b></div>
      <div class="a"><span>${fmt(r.sQty, 0)} sold · ${f2(r.bQty ? r.bAmt / r.bQty : null)} → ${f2(r.sQty ? r.sAmt / r.sQty : null)}${r.last ? ' · ' + esc(r.last) : ''}</span>
        <button type="button" class="pf-rm" data-rm="${r.ci}">Remove</button></div></div>`).join('')
    : '<p class="na pf-zempty">Stocks you sell fully move here; the money goes back to Cash.</p>';
  // transactions: Type · date / cash effect ; detail / ✎ ×
  const led = R.led.slice().reverse();
  sec('Transactions', led.length, led.length ? '<button type="button" class="pf-rm all" id="pfWipe" title="Delete every entry in your portfolio">Delete all</button>' : '').innerHTML = led.length ? led.map(l => {
    const x = l.x, amt = x.t === 'in' ? x.amt : x.t === 'out' ? -x.amt : x.t === 'buy' ? -(x.qty * x.px + (x.ch || 0)) : x.qty * x.px - (x.ch || 0);
    const act = R.lock.has(x.id) ? '<span class="pf-lock" title="Part of a closed position: remove it from the Closed list instead">🔒</span>'
      : `<span class="pf-acts2"><button type="button" class="pf-ico" data-edit="${esc(x.id)}" aria-label="Edit">✎</button><button type="button" class="pf-ico del" data-del="${esc(x.id)}" aria-label="Delete">×</button></span>`;
    return `<div class="pf-zt"><div class="a"><span><span class="pf-t ${x.t}">${TX_LABEL[x.t]}</span> ${esc(x.date || '')}</span><span>${signed(amt)}</span></div>
      <div class="b"><span>${x.sym ? `<b>${esc(x.sym)}</b> ${fmt(x.qty, 0)} @ ${f2(x.px)}` : 'Cash'}${x.ch ? ` <small>· ch ₹${f2(x.ch)}</small>` : ''}</span>${act}</div></div>`; }).join('')
    : '<p class="na pf-zempty">Every Pay in, Pay out, Buy and Sell shows here, newest first.</p>';
  const zs = $('#pfZSort'), zd = $('#pfZDir');
  if (zs) zs.addEventListener('change', () => { lsSet('pfzsort', zs.value); lsSet('pfzdir', PF_ZSORT[zs.value].up ? 'up' : 'dn'); pfRedraw(); });
  if (zd) zd.addEventListener('click', () => { lsSet('pfzdir', SD > 0 ? 'dn' : 'up'); pfRedraw(); });
  pfWireTop();
}
/* phone Holdings sort: field → value; up = natural first direction (A→Z for text, high→low for numbers) */
const PF_ZSORT = {
  name:  {label: 'Stock name',  up: 1, v: r => r.sym},
  day:   {label: "Day's P&L %", v: r => r.dayPct},
  pnl:   {label: 'Overall P&L %', v: r => r.priced ? r.pnlPct : null},
  value: {label: 'Current value', v: r => r.value},
  remark:{label: 'Remark',      up: 1, v: r => r.remark || ''},
};
/* re-draw the portfolio where it is (route() jumps to the top) */
function pfRedraw() { if ((location.hash || '') !== '#/portfolio') return; const y = window.scrollY; route(); window.scrollTo(0, y); }
/* live prices: Firestore pushes hni_market/ltp the moment a new LTP lands → prices change on screen straight away */
let PF_WATCH = null;
async function pfWatch() {
  if (PF_WATCH || !window.hniStore || !window.hniStore.watchMarket) return;
  PF_WATCH = 1;
  try { PF_WATCH = await window.hniStore.watchMarket(async m => {
    if (!m || !m.asOf || (PF.mkt && m.asOf === PF.mkt.asOf) || PF.busy) return;
    const first = !PF.mkt; PF.mkt = m; await pfApplyRenames(); PF_HELD = pfHeld();
    if (PF_POLL) { clearInterval(PF_POLL); PF_POLL = null; }
    if ((location.hash || '') !== '#/portfolio' || !PF.txns) return;
    if (document.querySelector('.wip-back, .au-back.open')) return;   // a form is open: prices are in, the next draw shows them
    pfRedraw(); if (!first) toast('Live prices updated · ' + m.asOf.slice(11) + ' IST');
  }); } catch (e) { PF_WATCH = null; }
}
/* Portfolio row buttons (✎ holding, ✎ / × transaction, Remove closed): ONE click handler for the whole page, so
   rows re-drawn by the search box or added while scrolling work straight away */
document.addEventListener('click', e => {
  if ((location.hash || '') !== '#/portfolio' || !PF.txns) return;
  const b = e.target.closest && e.target.closest('[data-trade],[data-edit],[data-del],[data-rm],[data-rn]');
  if (!b || !view.contains(b)) return;
  if (b.dataset.rn) return pfRenameForm(b.dataset.rn);
  if (b.dataset.trade) return pfTradeForm({sym: b.dataset.trade, t: 'sell'});
  if (b.dataset.edit) {
    const x = PF.txns.find(t => t.id === b.dataset.edit); if (!x) return;
    return x.t === 'in' || x.t === 'out' ? pfCashForm(x) : pfTradeForm(x);
  }
  if (b.dataset.del) {
    const x = PF.txns.find(t => t.id === b.dataset.del); if (!x) return;
    const rest = PF.txns.filter(t => t !== x), bad = pfReplay(rest).bad;
    if (bad) return modal('Can\'t delete', `<p>Without this ${TX_LABEL[x.t].toLowerCase()}, the sale of ${esc(bad.sym)}${bad.date ? ' on ' + esc(bad.date) : ''} would be more than the shares held. Delete or edit that sale first.</p>`);
    return typeConfirm(`Delete ${TX_LABEL[x.t].toLowerCase()}`, `${x.sym ? `<b>${esc(x.sym)}</b> ${fmt(x.qty, 0)} @ ₹${fmt(x.px, 2)}` : `₹${fmt(x.amt, 0)}`}${x.date ? ' on ' + esc(x.date) : ''}. Holdings and cash are recalculated.`,
      async () => { PF.txns = rest; await pfSave(); route(); });
  }
  if (b.dataset.rm != null) {
    const c = PF_LAST && PF_LAST.closed[+b.dataset.rm]; if (!c) return;
    const ids = new Set(c.ids);
    typeConfirm('Remove closed position', `<b>${esc(c.sym)}</b>: bought ${fmt(c.bQty, 0)}, sold ${fmt(c.sQty, 0)}, net ${rs(c.realized - c.charges, true)}. Its ${ids.size} trades are deleted, and cash drops or rises by that net amount.`,
      async () => { PF.txns = PF.txns.filter(t => !ids.has(t.id)); await pfSave(); route(); });
  }
});
/* ---------- Checklist light: plain yes / no tests per stock, shown as a green / amber / red traffic light ----------
   Only a summary of how many checks the published numbers pass: never worded as buy / sell / hold advice.
   A test with no data for that stock (or one only for stocks you own) is skipped, not counted either way. */
const VD_LIM = {grahamOver: 1, peVsBuy: 1.5, peVsInd: 1.5, minScore: 40, hot: 95, minMom: 30, min3m: -15, maxTrend: .25,
                maxDe: 1, minQ: 12, minProD: -2, minPro3: -5, maxWt: 20, betterBy: 20, stopLoss: -20,
                minEpsG: 10, minSalesG: 8, maxOtherInc: 30, buyAt: 75, watchAt: 65};
/* How it works: two PROFIT GATES first, then weighted checks (growth and profit quality count double).
   Gate 1 (must): net profit positive in each of the latest 4 quarters, else red whatever the rest says.
   Gate 2: the best of the latest 4 quarters not below the weakest of the 4 before (2 vs 2 with fewer quarters), else amber at most.
   Score = points passed ÷ points that could be judged: ≥ 75% green · 65–75% amber · < 65% red. */
/* qty-weighted PE / score saved with the buys of the shares you still hold (saved from now on, for every new buy) */
function pfBuyStat(h, k) {
  if (!h || !PF.txns) return null;
  const ids = new Set(h.ids || []); let q = 0, t = 0;
  PF.txns.forEach(x => { if (ids.has(x.id) && x.t === 'buy' && x[k] != null) { q += x.qty; t += x.qty * x[k]; } });
  return q ? t / q : null;
}
/* snap (optional) = a past date from vdHistLoad(): judged with that date's numbers and price; the portfolio
   checks (position size, fall from your average, NSE list) are left out there because they are about today */
function verdict(sym, P, snap) {
  const s = snap ? snap.by.get(sym) : TODAY.get(sym);
  if (!s) return null;                                     // not in the three lists: no fundamentals to judge
  const IND = snap ? snap.inds : industries;
  const h = !snap && P ? P.open.find(r => r.sym === sym) : null, held = !!h;
  const px = snap ? s.cmp : pfLtp(sym) != null ? pfLtp(sym) : s.cmp;
  const ipe = s.ind && IND[s.ind] ? IND[s.ind].pe[method] : null;
  const t1 = snap ? (snap.prev ? snap.prev.by.get(sym) : null) : THEN_BY.get(sym), buyPe = pfBuyStat(h, 'pe'), buySc = pfBuyStat(h, 'sc');
  const peRef = buyPe || (t1 && t1.pe > 0 ? t1.pe : null), flags = trapReasons(s), q = qual(s), proD = trD(s, 'proD');
  const L = VD_LIM, f1 = v => fmt(v, 1), f0 = v => fmt(v, 0);
  const expensive = (s.graham && px > s.graham) || (s.pe > 0 && ipe && s.pe > L.peVsInd * ipe) || (s.pe > 0 && peRef && s.pe > L.peVsBuy * peRef);
  const worse = flags.length > 0 || (s.tr && s.tr.pass / s.tr.of <= L.maxTrend) || (!s.fin && s.de > L.maxDe) || (q != null && q < L.minQ);
  const peers = s.ind && IND[s.ind] ? IND[s.ind].stocks.filter(x => x.score != null && x.sym !== sym) : [];
  const best = peers.reduce((b, x) => !b || x.score > b.score ? x : b, null);
  const C = [], add = (g, label, pass, why, w = 1) => C.push({g, label, pass, why, w});
  // 0. profit gates (quarterly net profit, as known on that date)
  const qp = s.qp || [], l4 = qp.slice(-4), sum = a => a.reduce((t, v) => t + v, 0);
  // cap: the best of the latest quarters must not be below the weakest of the ones before (8 quarters: 4 vs 4, else 2 vs 2)
  const [cN, cA, cB] = qp.length >= 8 ? [4, qp.slice(-4), qp.slice(-8, -4)] : qp.length >= 4 ? [2, qp.slice(-2), qp.slice(-4, -2)] : [0, null, null];
  const qfmt = a => a.map(v => fmt(v, 0)).join(' · ');
  add('Profit gate', 'Net profit positive in each of the latest 4 quarters', l4.length === 4 ? l4.every(v => v > 0) : null,
      l4.length === 4 ? `₹ Cr: ${qfmt(l4)}` : 'not enough quarterly results', 'gate');
  add('Profit gate', `Best of the latest ${cN || 4} quarters not below the weakest of the ${cN || 4} before`, cA ? Math.max(...cA) >= Math.min(...cB) : null,
      cA ? `best ₹${fmt(Math.max(...cA), 0)} Cr vs weakest ₹${fmt(Math.min(...cB), 0)} Cr` : 'needs 4 quarters', 'cap');
  // 1b. growth and profit quality (count double)
  add('Growth & quality', `Profit growth: 5-year EPS growth ${L.minEpsG}%+ a year`, s.cagr != null ? s.cagr >= L.minEpsG : (s.loss5 ? false : null),
      s.cagr != null ? `EPS CAGR ${f1(s.cagr)}%` : s.loss5 ? `${s.loss5} loss year(s): no growth figure` : '', 2);
  add('Growth & quality', `Sales growth: 5-year sales growth ${L.minSalesG}%+ a year`, s.sg5 != null ? s.sg5 >= L.minSalesG : null,
      s.sg5 != null ? `sales CAGR ${fmt(s.sg5, 2)}%` : '', 2);
  add('Growth & quality', 'Net profit in every year (last 10)', s.pyr ? s.pyr[0] === s.pyr[1] : null,
      s.pyr ? `profit in ${s.pyr[0]} of ${s.pyr[1]} years` : '', 2);
  const oiPct = s.fin ? null : s.qoi != null ? s.qoi : s.oi;   // banks / NBFCs: "other income" = fees, commissions, treasury: core business
  add('Growth & quality', `Profit not propped up by other income (under ${L.maxOtherInc}% of pre-tax profit)`, oiPct != null ? oiPct < L.maxOtherInc : null,
      s.fin ? 'not used for banks / NBFCs: fees and treasury income are their normal business'
        : oiPct != null ? (oiPct >= 999 ? 'no pre-tax profit' : `other income ${f0(oiPct)}% of pre-tax profit${s.qoi != null ? ' (last 4 qtrs)' : ''}`) : '', 2);
  // 1. valuation
  add('Valuation', 'Price at or below the Graham number', s.graham ? px <= s.graham : null,
      s.graham ? `₹${fmt(px, 0)} vs Graham ₹${fmt(s.graham, 0)}` : 'no Graham number (loss or negative book)');
  add('Valuation', `PE under ${L.peVsBuy}× ${buyPe ? 'the PE you bought at' : 'its PE a year ago'}`, s.pe > 0 && peRef ? s.pe <= L.peVsBuy * peRef : null,
      s.pe > 0 && peRef ? `PE ${f1(s.pe)} vs ${f1(peRef)} ${buyPe ? 'at buy' : '1Y ago'}` : snap || THEN_BY.size ? 'no PE a year before' : 'loading 1Y data…');
  add('Valuation', `PE under ${L.peVsInd}× its industry`, s.pe > 0 && ipe ? s.pe <= L.peVsInd * ipe : null, s.pe > 0 && ipe ? `PE ${f1(s.pe)} vs industry ${f1(ipe)}` : 'no PE');
  add('Valuation', `Value score ${L.minScore} or more`, s.score != null ? s.score >= L.minScore : null,
      s.score != null ? `score ${f0(s.score)}${buySc != null ? ` (was ${f0(buySc)} at buy)` : ''}` : '');
  // 2. momentum
  add('Momentum', `Not overheated (momentum under ${L.hot}, or ${L.hot}+ but still cheap)`, s.momS != null ? s.momS < L.hot || !expensive : null,
      s.momS != null ? `momentum ${f0(s.momS)}${s.momS >= L.hot ? (expensive ? ': very strong, and valuation stretched' : ': very strong, valuation still fine') : ''}` : '');
  const r3 = s.mom && ok(s.mom[0]) ? s.mom[0] : null;
  add('Momentum', `Momentum holding up (${L.minMom}+, 3M return above ${L.min3m}%)`, s.momS != null ? s.momS >= L.minMom && !(r3 != null && r3 < L.min3m) : null,
      s.momS != null ? `momentum ${f0(s.momS)}${r3 != null ? ` · 3M ${pctTxt(r3, 1)}` : ''}` : '');
  // 3. business
  add('Business', 'No value-trap flags', flags.length === 0, flags.length ? flags.join(' · ') : 'clean');
  add('Business', 'Fundamentals trend above 1/4', s.tr ? s.tr.pass / s.tr.of > L.maxTrend : null, s.tr ? `${s.tr.pass}/${s.tr.of} improving` : '');
  add('Business', `Debt ÷ equity ${L.maxDe} or less`, s.fin || s.de == null ? null : s.de <= L.maxDe, s.fin ? 'not used for financials' : s.de != null ? `D/E ${fmt(s.de, 2)}` : '');
  add('Business', `${s.fin ? 'ROE' : 'ROCE'} ${L.minQ}% or more`, q != null ? q >= L.minQ : null, q != null ? `${s.fin ? 'ROE' : 'ROCE'} ${f1(q)}%` : '');
  add('Business', `Promoter holding steady (not down over ${-L.minProD} pp in a year or ${-L.minPro3} pp over the longer run)`,
      proD != null || s.pro3 != null ? (proD == null || proD >= L.minProD) && (s.pro3 == null || s.pro3 >= L.minPro3) : null,
      [proD != null ? `${proD > 0 ? '+' : ''}${fmt(proD, 2)} pp in a year` : '', s.pro3 != null ? `${s.pro3 > 0 ? '+' : ''}${fmt(s.pro3, 2)} pp over ${fmt(s.pro3y, 1)} yrs` : ''].filter(Boolean).join(' · '));
  // 4. portfolio
  add('Portfolio', `Position under ${L.maxWt}% of your holdings`, held && h.wt != null ? h.wt <= L.maxWt : null, held ? `${f1(h.wt)}% of holdings` : '');
  add('Portfolio', `No clearly better stock in ${esc(s.ind || 'its industry')}`, best && s.score != null ? best.score - s.score < L.betterBy : null,
      best && s.score != null ? (best.score - s.score >= L.betterBy ? `${best.sym} scores ${f0(best.score)} vs ${f0(s.score)}` : `best peer ${best.sym} ${f0(best.score)}`) : '');
  add('Portfolio', `Not down ${-L.stopLoss}%+ from your average while the business weakened`, held && h.priced ? !(h.pnlPct <= L.stopLoss && worse) : null,
      held && h.priced ? `${pctTxt(h.pnlPct, 1)} from your average${worse ? ', business signs weak' : ''}` : '');
  add('Portfolio', "Still on NSE's list", held && PF.mkt && PF.mkt.notListed ? !pfNotListedSet().has(sym) : null, '');
  if (snap) C.splice(0, C.length, ...C.filter(c => c.g !== 'Portfolio' || /better stock/.test(c.label)));   // past dates: only checks that mean something then
  const pts = C.filter(c => c.pass != null && typeof c.w === 'number');            // gates don't score; they overrule
  const pass = pts.filter(c => c.pass).reduce((t, c) => t + c.w, 0), n = pts.reduce((t, c) => t + c.w, 0), pct = n ? pass / n * 100 : null;
  let st = pct == null ? 'na' : pct >= L.buyAt ? 'green' : pct >= L.watchAt ? 'amber' : 'red';   // a light, never a buy / sell call
  const gate = C.find(c => c.w === 'gate'), cap = C.find(c => c.w === 'cap'), why = [];
  if (gate.pass === false) { st = 'red'; why.push('red: a loss in the latest 4 quarters'); }
  else if (cap.pass === false && st === 'green') { st = 'amber'; why.push('amber at most: latest quarters all below the weakest of the quarters before'); }
  return {checks: C, pass, n, pct, st, held, gateWhy: why.join(''), label: {green: 'Green', amber: 'Amber', red: 'Red', na: '–'}[st]};
}
/* ---------- status history: price at each saved date (1.5Y … 3M back) and today, coloured by that date's status ----------
   Uses the same Historical files as the Historical switch (loaded once, then from the phone's cache). */
const VH_KEYS = ['1_5Y', '1_25Y', '1Y', '9M', '6M', '3M'], VH_PREV = {'3M': '1_25Y', '6M': '1_5Y'};   // a date's "PE a year before"
const VH = {snaps: null, p: null};
function vdSnap(j) {                                      // scores + industries for one past date, without touching the page
  const keep = {S, industries, NOPRICE, HAS_MOM, cS: computeScores.S, cK: computeScores.key};
  S = j.stocks; NOPRICE = !j.hasPrice; industries = {};
  S.forEach(x => (industries[x.ind] = industries[x.ind] || {name: x.ind, stocks: []}).stocks.push(x));
  Object.values(industries).forEach(g => buildIndustry(g));
  computeScores.S = null; computeScores();
  const snap = {key: j.key, date: j.date, by: new Map(S.map(x => [x.sym, x])), inds: industries};
  ({S, industries, NOPRICE, HAS_MOM} = keep); computeScores.S = keep.cS; computeScores.key = keep.cK;
  return snap;
}
const nextFrame = () => new Promise(r => (window.requestIdleCallback || setTimeout)(() => r(), {timeout: 120}));
function vdHistLoad() {                                   // one file at a time, a pause between each: the page never freezes
  return VH.p = VH.p || (async () => {
    const by = {};
    for (const k of VH_KEYS) {
      const H = await loadHist();                           // one small file for all dates; the full ones only if it's missing
      let j = BACK_CACHE[k] || (H && H[k]);
      if (!j) { try { j = await getBack(k); } catch (e) { j = null; }
                if (j && j.stocks) BACK_CACHE[k] = j; }
      await nextFrame();
      if (j && j.stocks && j.hasPrice) by[k] = vdSnap(Object.assign({key: k}, j));
    }
    Object.values(by).forEach(sn => { sn.prev = by[VH_PREV[sn.key]] || null; });
    return VH.snaps = VH_KEYS.map(k => by[k]).filter(Boolean);
  })();
}
const VH_PTS = new Map();                                 // stock → its past points, worked out once
const VD_COL = {green: '#3ec65a', amber: '#f0a020', red: '#e5413a', na: 'var(--faint)'};
/* the sparkline; hovering (or tapping) a point shows that date's checklist */
function vdSpark(host, sym, PS) {
  const now = verdict(sym, PS), s = TODAY.get(sym);
  if (!VH_PTS.has(sym)) VH_PTS.set(sym, VH.snaps.map(sn => { const v = verdict(sym, null, sn), x = sn.by.get(sym); return v && x && x.cmp ? {d: sn.date, px: x.cmp, v} : null; }).filter(Boolean));
  const pts = VH_PTS.get(sym).slice();
  const pxNow = pfLtp(sym) != null ? pfLtp(sym) : s && s.cmp;
  if (now && pxNow) pts.push({d: 'Today', px: pxNow, v: now});
  if (pts.length < 2) { host.innerHTML = ''; return; }
  /* main line = checklist score % (0–100, fixed scale; faint guides at the amber and green lines), coloured by the light;
     thin yellow line = price on its own scale, just to see how the price moved alongside */
  const W = 176, H = 50, pad = 5, lo = Math.min(...pts.map(p => p.px)), hi = Math.max(...pts.map(p => p.px));
  const X = i => pad + i * (W - 2 * pad) / (pts.length - 1);
  const YS = v => H - pad - v / 100 * (H - 2 * pad), YP = v => hi === lo ? H / 2 : H - pad - (v - lo) / (hi - lo) * (H - 2 * pad);
  const guide = v => `<line x1="0" x2="${W}" y1="${YS(v)}" y2="${YS(v)}" class="vh-g"/>`;
  const price = `<polyline points="${pts.map((p, i) => X(i) + ',' + YP(p.px)).join(' ')}" class="vh-px"/>`;
  const segs = pts.slice(1).map((p, i) => `<line x1="${X(i)}" y1="${YS(pts[i].v.pct)}" x2="${X(i + 1)}" y2="${YS(p.v.pct)}" stroke="${VD_COL[p.v.st]}" stroke-width="2.2" stroke-linecap="round"/>`).join('');
  const dots = pts.map((p, i) => `<circle cx="${X(i)}" cy="${YS(p.v.pct)}" r="${i === pts.length - 1 ? 3.4 : 2.4}" fill="${VD_COL[p.v.st]}"/>`).join('');
  const hits = pts.map((p, i) => `<rect class="hit" data-i="${i}" x="${X(i) - (W - 2 * pad) / (pts.length - 1) / 2}" y="0" width="${(W - 2 * pad) / (pts.length - 1)}" height="${H}" fill="transparent"/>`).join('');
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" preserveAspectRatio="none" role="img" aria-label="Checklist score and price over the last ${pts.length} dates">${guide(VD_LIM.watchAt)}${guide(VD_LIM.buyAt)}${price}${segs}${dots}${hits}</svg>
    <div class="vh-cap">score % · <span class="vh-pxk">price</span> · from ${esc(pts[0].d)}</div><div class="vh-tip" hidden></div>`;
  const tip = $('.vh-tip', host), svg = $('svg', host);
  const cache = [];
  const show = i => {
    const p = pts[i], v = p.v;
    if (tip._i === i && !tip.hidden) return; tip._i = i;
    tip.innerHTML = cache[i] = cache[i] || `<div class="vh-h"><b>${esc(p.d)}</b> ${vdChip(v)} <b>${fmt(v.pct, 0)}%</b> <small>${v.pass}/${v.n} pts</small><span class="vh-pxv">₹${fmt(p.px, p.px < 100 ? 2 : 0)}</span></div>`
      + (v.gateWhy ? `<p class="vd-gate">${esc(v.gateWhy)}</p>` : '')
      + v.checks.filter(c => c.pass != null).map(c => `<div class="vd-c ${c.pass ? 'ok' : 'no'}"><i>${c.pass ? '✓' : '✗'}</i><span>${c.label} ${vdTag(c)}${c.why ? `<small>${esc(c.why)}</small>` : ''}</span></div>`).join('');
    tip.hidden = false;                                    // fixed to the screen, so the popup's edge never cuts it off
    const two = innerWidth >= 760;                          // room for two columns: everything shows without scrolling
    tip.classList.toggle('two', two);
    const r = svg.getBoundingClientRect(), tw = Math.min(two ? 620 : 320, innerWidth - 24), cx = r.left + X(i) * r.width / W;
    tip.style.width = tw + 'px';
    tip.style.left = Math.max(12, Math.min(cx - tw / 2, innerWidth - tw - 12)) + 'px';
    const top = r.bottom + 6;                               // always below the chart, so the other dates stay reachable
    tip.style.top = top + 'px'; tip.style.maxHeight = (innerHeight - top - 12) + 'px';   // fits the screen; scrolls only on small phones
  };
  svg.addEventListener('mouseover', e => { const c = e.target.closest('.hit'); if (c) show(+c.dataset.i); });
  svg.addEventListener('click', e => { const c = e.target.closest('.hit'); if (c) { e.stopPropagation(); show(+c.dataset.i); } });
  // leaving the chart hides it after a moment, unless the mouse went into the tooltip itself
  let hideT = null, touch = false;
  svg.addEventListener('pointerdown', e => { touch = e.pointerType === 'touch'; });   // phone: a tap shows it until you tap elsewhere
  const later = () => { if (touch) return; clearTimeout(hideT); hideT = setTimeout(() => { tip.hidden = true; tip._i = null; }, 220); };
  svg.addEventListener('mouseleave', later);
  svg.addEventListener('mouseenter', () => clearTimeout(hideT));
  tip.addEventListener('mouseenter', () => clearTimeout(hideT));
  tip.addEventListener('mouseleave', later);
  host.closest('.wip-box').addEventListener('click', e => { if (!e.target.closest('.hit') && !e.target.closest('.vh-tip')) { tip.hidden = true; tip._i = null; } });
}
/* ETFs: index funds, not companies. Never "not on NSE's list" (NSE keeps them in a separate ETF list), and their checklist
   light is a fixed green + amber (no company checks apply) */
const PF_ETFS = new Set(['GOLDBEES', 'ITBEES', 'ALPHA', 'JUNIORBEES', 'MON100', 'HNGSNGBEES', 'FMCGIETF', 'NIFTYBEES', 'BANKBEES', 'LIQUIDBEES', 'SILVERBEES', 'MAFANG', 'MOM100', 'CPSEETF']);
const pfIsEtf = (sym, name) => PF_ETFS.has(sym) || /(BEES|ETF)$/.test(sym || '') || /\bETF\b|\bBeES\b/i.test(name || '');
const pfNameOf = sym => ((PF.txns || []).find(t => t.sym === sym && t.name) || {}).name || '';
const pfNotListedSet = () => new Set(((PF.mkt && PF.mkt.notListed) || []).filter(s => !pfIsEtf(s, pfNameOf(s))));
const etfChip = () => '<span class="tlw" title="ETF (index fund): no company checklist, shown green and amber by default" role="img" aria-label="ETF, no checklist"><span class="tl tl-etf"><i class="r"></i><i class="a"></i><i class="g"></i></span></span>';
const vdChip = (v, big) => !v ? '' : `<span class="tlw${big ? ' big' : ''}" title="Checklist light: ${v.label.toLowerCase()} · ${v.pass} of ${v.n} points${v.gateWhy ? ' · ' + v.gateWhy : ''}" role="img" aria-label="Checklist light ${v.label}, ${v.pass} of ${v.n} points">`
  + `<span class="tl tl-${v.st}"><i class="r"></i><i class="a"></i><i class="g"></i></span>${big ? ` <small>${v.pass}/${v.n} pts · ${fmt(v.pct, 0)}%</small>` : ''}</span>`;
const VD_DISCLAIMER = `<p class="vd-disc">The light only shows how many of these checks the published numbers pass. It is an educational screen,
  not a recommendation to buy, sell or hold any security, and not investment advice. HNImanshu is not a SEBI-registered investment adviser
  or research analyst. Data can be late or wrong; please do your own research or consult a SEBI-registered adviser.</p>`;
const vdTag = c => c.w === 'gate' ? '<em class="vd-w gate">must</em>' : c.w === 'cap' ? '<em class="vd-w gate">cap</em>' : c.w === 2 ? '<em class="vd-w">×2</em>' : '';
function vdList(v) {
  if (!v) return '';
  let g0 = '';
  return v.checks.map(c => (c.g !== g0 ? `<div class="vd-g">${g0 = c.g}</div>` : '')
    + `<div class="vd-c ${c.pass == null ? 'na' : c.pass ? 'ok' : 'no'}"><i>${c.pass == null ? '–' : c.pass ? '✓' : '✗'}</i><span>${c.label} ${vdTag(c)}${c.why ? `<small>${esc(c.why)}</small>` : ''}</span></div>`).join('')
    + (v.gateWhy ? `<p class="vd-gate">Light set by the profit gate: ${esc(v.gateWhy)}.</p>` : '')
    + `<p class="vd-note"><b>must</b>: a loss in any of the latest 4 quarters → red · <b>cap</b>: latest quarters all below the weakest before → amber at most ·
       <b>×2</b> checks count double · ≥ ${VD_LIM.buyAt}% of points → green · ${VD_LIM.watchAt}–${VD_LIM.buyAt}% → amber · under ${VD_LIM.watchAt}% → red · “–” = no data, not counted.</p>`
    + VD_DISCLAIMER;
}
document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('pf-zh')) newsOverlay(e.target.dataset.news); });
/* stock name (any table, news panel) → light overlay: its last-7-days news, key numbers, Buy / Sell, Screener.in link */
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('[data-news]');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;   // ctrl / cmd-click still opens Screener.in in a new tab
  e.preventDefault(); e.stopPropagation();
  newsOverlay(a.dataset.news);
}, true);
function newsOverlay(sym) {
  const s = TODAY.get(sym) || {sym, name: sym};
  const k = (l, v) => v == null || v === '' ? '' : `<div><span>${l}</span><b>${v}</b></div>`;
  const PS = PF.txns ? pfSummary(PF.txns) : null;
  const h = PS && PF_HELD.has(sym) ? PS.open.find(r => r.sym === sym) : null;   // your holding, if any
  if (!THEN_BY.size) ensureThen1();                       // "PE a year ago" test needs the 1Y snapshot
  const ret = i => s.mom && ok(s.mom[i]) ? pctTxt(s.mom[i], 1) : null, tr = trapReasons(s);
  const grp = (t, body) => body.replace(/\s/g, '') ? `<div class="no-g">${t}</div><div class="no-kv">${body}</div>` : '';
  const w = modal(esc(sym), `<div class="vh"></div><p class="no-name">${esc(s.name || '')}${s.ind ? ` · <span>${esc(s.ind)}</span>` : ''}</p>
    <div class="vd-box"></div>
    ${h ? grp('Your holding', `${k('Qty', fmt(h.qty, 0))}${k('Avg ₹', fmt(h.avg, 2))}${k('LTP ₹', h.ltp == null ? null : fmt(h.ltp, 2))}
      ${k('P&amp;L', h.priced ? plain(h.pnl, 0) + ' <small>' + pctTxt(h.pnlPct, 1) + '</small>' : null)}${k('Day', h.day == null ? null : plain(h.day, 0) + ' <small>' + pctTxt(h.dayPct) + '</small>')}`)
      : grp('Price', k('LTP ₹', pfLtp(sym) != null ? fmt(pfLtp(sym), 2) : null))}
    ${grp('Value', `${k('Score', s.score != null ? fmt(s.score, 0) : null)}${k('PE', s.pe != null ? fmt(s.pe) : null)}${k('PB', s.pb != null ? fmt(s.pb, 2) : null)}
      ${k('ROE %', s.roe != null ? fmt(s.roe) : null)}${k('Div yield %', s.dy != null ? fmt(s.dy, 2) : null)}${k('Mcap ₹ Cr', s.mcap != null ? fmt(s.mcap, 0) : null)}`)}
    ${grp('Momentum', `${k('Momentum', s.momS != null ? fmt(s.momS, 0) + '<small>/100</small>' : null)}${k('3M', ret(0))}${k('6M', ret(1))}${k('12-1M', ret(2))}
      ${k('Trend', s.tr ? s.tr.pass + '/' + s.tr.of : null)}`)}
    ${s.score != null || tr.length ? `<p class="no-flags">${tr.length ? '⚠ ' + esc(tr.join(' · ')) : 'No value-trap flags'}</p>` : ''}
    <div class="no-acts"><button type="button" class="btn primary" data-nb>Buy / Sell</button>${s.url ? `<a class="btn" href="${esc(s.url)}" target="_blank" rel="noopener">Open on Screener.in ↗</a>` : ''}</div>
    <div class="no-h">News · last 7 days</div><div class="no-list"><p class="na">Loading…</p></div>`, {noFocus: true, cls: 'no-box', sheet: true});
  $('[data-nb]', w).addEventListener('click', async () => { w._close(); await pfLoad(); pfTradeForm({sym}); });
  const vbox = $('.vd-box', w);
  const paintVd = () => { const v = verdict(sym, PS), open = vbox.classList.contains('open');
    vbox.innerHTML = v ? `<button type="button" class="vd-btn" aria-expanded="${open}">${vdChip(v, true)}<span class="vd-why">${open ? 'Hide' : 'Why?'} checklist</span></button><div class="vd-list">${vdList(v)}</div>` : ''; };
  vbox.addEventListener('click', e => { if (e.target.closest('.vd-btn')) { vbox.classList.toggle('open'); paintVd(); } });
  paintVd();
  if (ensureThen1.p) ensureThen1.p.then(() => { if (w.isConnected) paintVd(); });
  const vh = $('.vh', w), slow = (() => { const c = navigator.connection; return !!(c && (c.saveData || /2g/.test(c.effectiveType || ''))); })();
  const drawVh = () => { vh.innerHTML = '<span class="vh-cap">loading history…</span>'; vdHistLoad().then(() => { if (w.isConnected) vdSpark(vh, sym, PS); }); };
  if (VH.snaps) requestAnimationFrame(() => { if (w.isConnected) vdSpark(vh, sym, PS); });
  else if (slow) { vh.innerHTML = '<button type="button" class="vh-load">Show status history</button>'; $('.vh-load', vh).addEventListener('click', drawVh); }   // 2G / data saver: only on request
  else if (TODAY.get(sym)) setTimeout(drawVh, 60);
  loadNewsMap([sym]).then(N => {
    if (!w.isConnected) return;
    const items = (N[sym] || []);
    const d = v => { const t = new Date(v.replace(' ', 'T')); return isNaN(t) ? v : t.toLocaleDateString('en-IN', {day: '2-digit', month: 'short'}) + ', ' + t.toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit'}); };
    $('.no-list', w).innerHTML = items.length ? items.map(n => `<a class="no-it" href="${esc(n[2] || '#')}" target="_blank" rel="noopener noreferrer"><span>${esc(d(n[0]))}</span>${esc(n[1])}</a>`).join('')
      : '<p class="na">No news in the last 7 days.</p>';
  });
}
/* phone bottom sheets (menus, stock overlay, Filters), like a broker app. sc = a full-screen scroller with an empty
   screen-high gap on top, box = the sheet under it. Opens at half the screen (a short menu at its own height).
   Scrolling up lifts the sheet to full height, then its body scrolls under the sticky title / Done bar.
   Pulling down brings it back down; let go well below where it opened and it closes. st keeps the place across
   re-renders (Filters re-draws on every change). */
function sheetScroll(sc, box, close, st = {}) {
  if (!isPhone() || !box) return;
  const H = sc.clientHeight, tall = box.scrollHeight > H * 0.5;
  box.style.minHeight = tall ? Math.round(H * 0.94) + 'px' : '';         // tall: can reach the top of the screen
  const start = tall ? Math.round(H * 0.5) : box.offsetHeight;
  sc.scrollTop = st.top != null ? st.top : start;
  if (sc._sheet) { sc._sheet.start = start; return; }                      // listeners already on this scroller
  const S = sc._sheet = {start, touching: false, tm: 0, shut: false};
  const settle = () => {
    if (S.shut || S.touching || !sc.isConnected) return;
    const y = sc.scrollTop;
    if (y < S.start - 70) { S.shut = true; sc.scrollTo({top: 0, behavior: 'smooth'}); setTimeout(() => { S.shut = false; close(); }, 180); }
    else if (y < S.start) sc.scrollTo({top: S.start, behavior: 'smooth'});   // a small pull: back to where it opened
  };
  const later = () => { clearTimeout(S.tm); S.tm = setTimeout(settle, 130); };
  sc.addEventListener('touchstart', () => { S.touching = true; clearTimeout(S.tm); }, {passive: true});
  sc.addEventListener('touchend', () => { S.touching = false; later(); }, {passive: true});
  sc.addEventListener('touchcancel', () => { S.touching = false; later(); }, {passive: true});
  sc.addEventListener('scroll', () => { st.top = sc.scrollTop; if (!S.touching) later(); }, {passive: true});
}
/* small dialog: × , Esc or a click outside closes it; returns the box */
function modal(title, html, opt = {}) {
  document.querySelectorAll('.wip-back').forEach(m => m.remove());
  const w = document.createElement('div'); w.className = 'wip-back open' + (opt.sheet ? ' sheet' : '');   // sheet: slides up softly on phones
  w.innerHTML = `<div class="wip-box${opt.ov ? ' ov' : ''}${opt.cls ? ' ' + opt.cls : ''}" role="dialog" aria-modal="true"><button type="button" class="wip-x" aria-label="Close">×</button><h3>${title}</h3>${html}</div>`;
  document.body.appendChild(w);
  document.documentElement.classList.add('pop-open');
  const close = () => { w.remove(); document.removeEventListener('keydown', onKey, true);
    if (!document.querySelector('.wip-back') && !document.querySelector('.au-back.open')) document.documentElement.classList.remove('pop-open'); };
  const onKey = e => { if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  $('.wip-x', w).addEventListener('click', close);
  w.addEventListener('mousedown', e => { if (e.target === w) close(); });
  w._close = close;
  if (opt.sheet && isPhone()) {                                           // phone: × sits in the sticky title
    const bx = $('.wip-box', w), h3 = bx.querySelector('h3');
    h3.appendChild($('.wip-x', w));
    const vh = bx.querySelector(':scope > .vh'); if (vh) h3.appendChild(vh);   // stock sparkline too: under the sticky title it was hidden
    sheetScroll(w, bx, close);
  }
  const first = w.querySelector(opt.noFocus ? '.wip-x' : 'input:not([type=hidden]), .wip-x'); if (first) setTimeout(() => first.focus({preventScroll: true}), 30);
  return w;
}
let PF_LAST = null;                                       // last replay shown, so transaction rows know which entries are locked
/* deletes need the word "confirm" typed in */
function typeConfirm(title, html, onOk) {
  const w = modal(title, `<form class="pf-form" autocomplete="off"><p class="pf-calc">${html}</p>
    <label class="fld"><span>Type <b>confirm</b> to continue</span><input name="c" spellcheck="false" autocapitalize="none"></label>
    <button type="submit" class="btn primary danger" disabled>Delete</button></form>`);
  const f = $('form', w), btn = $('button[type=submit]', f);
  f.c.addEventListener('input', () => { btn.disabled = f.c.value.trim().toLowerCase() !== 'confirm'; });
  f.addEventListener('submit', async e => { e.preventDefault(); if (btn.disabled) return; btn.disabled = true; await onOk(); w._close(); });
  setTimeout(() => f.c.focus(), 40);
}
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
/* symbol box with its own dropdown (the phone's datalist only shows above the keyboard):
   matches symbol start first, then name; last row adds a symbol that is not in the lists */
/* every NSE stock (public/nse_all.json from EQUITY_L.csv, [[SYMBOL, name], ...]): hints for stocks outside the
   lists. Fetched the first time Buy / Sell opens, then kept for the visit. */
let NSE_ALL = null, NSE_WAIT = null;
const nseAll = () => NSE_WAIT = NSE_WAIT || fetch('nse_all.json').then(r => r.ok ? r.json() : []).catch(() => [])
  .then(j => { NSE_ALL = Array.isArray(j) ? j : []; return NSE_ALL; });
function symPicker(input, onPick) {
  const box = document.createElement('div'); box.className = 'sp-list'; box.hidden = true;
  input.parentNode.classList.add('sp-wrap'); input.after(box);
  let items = [], hi = 0;
  const paint = () => {
    const q = input.value.trim().toUpperCase();
    if (!q) { box.hidden = true; return; }
    const a = [], b = [];
    for (const s of D.stocks) {
      if (s.sym.startsWith(q)) a.push(s); else if (s.sym.includes(q) || (s.name || '').toUpperCase().includes(q)) b.push(s);
      if (a.length >= 8) break;
    }
    items = a.concat(b).slice(0, 8).map(s => ({sym: s.sym, name: s.name, held: PF_HELD.has(s.sym)}));
    if (items.length < 8 && NSE_ALL) {                     // then any other NSE stock
      const na = [], nb = [];
      for (const [sym, name] of NSE_ALL) {
        if (TODAY.has(sym)) continue;
        if (sym.startsWith(q)) na.push({sym, name, nse: true}); else if (sym.includes(q) || (name || '').toUpperCase().includes(q)) nb.push({sym, name, nse: true});
        if (na.length >= 8) break;
      }
      items = items.concat(na.concat(nb).slice(0, 8 - items.length).map(it => Object.assign(it, {held: PF_HELD.has(it.sym)})));
    }
    const known = items.some(it => it.sym === q) || (NSE_ALL && NSE_ALL.some(x => x[0] === q));
    if (/^[A-Z0-9&._-]{1,20}$/.test(q) && !TODAY.has(q) && !known) items.push({sym: q, name: NSE_ALL && NSE_ALL.length ? 'Not on NSE\'s list: add anyway' : 'Add as a stock not in these lists', add: true});
    hi = 0;
    box.innerHTML = items.map((it, i) => `<button type="button" class="sp-it${i === hi ? ' on' : ''}${it.add ? ' add' : ''}" data-i="${i}"><b>${esc(it.sym)}</b><span>${esc(it.name || '')}</span>${it.held ? '<i>held</i>' : ''}</button>`).join('');
    box.hidden = !items.length;
  };
  const pick = i => { const it = items[i]; if (!it) return; input.value = it.sym; box.hidden = true; onPick(it); };
  input.addEventListener('input', paint);
  input.addEventListener('focus', paint);
  if (!NSE_ALL) nseAll().then(() => { if (document.activeElement === input) paint(); });
  input.addEventListener('keydown', e => {
    if (box.hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); hi = (hi + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      box.querySelectorAll('.sp-it').forEach((b, i) => b.classList.toggle('on', i === hi)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(hi); }
    else if (e.key === 'Escape') { e.stopPropagation(); box.hidden = true; }
  });
  box.addEventListener('mousedown', e => e.preventDefault());              // keep the focus in the box while choosing
  box.addEventListener('click', e => { const b = e.target.closest('.sp-it'); if (b) pick(+b.dataset.i); });
  input.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 120));
}
/* Buy / Sell form (new, or edit an existing trade). Charges fill in by themselves; type over them to override.
   Until you click a box: digits go to Qty, letters to Symbol. Empty price = LTP. */
/* Remark for a buy made here: the Factor Screener tab it was made from, or Portfolio */
const PF_SRC_TAB = {'': 'Industries', value: 'Value', momentum: 'Momentum', all: 'All Stocks', method: 'Methodology'};
function pfSrcHere() {
  const h = (location.hash || '#/').replace(/^#\/?/, '').split(/[/?]/)[0];
  return h === 'portfolio' ? pfName() : 'Factor Screener → ' + (PF_SRC_TAB[h] || h || 'Industries');
}
function pfTradeForm(x) {
  const edit = !!x.id, held = () => { const R = pfReplay(PF.txns.filter(t => t !== x)); return (R.pos[f.sym.value.trim().toUpperCase()] || {}).qty || 0; };
  const w = modal(edit ? 'Edit trade' : 'Buy / Sell', `<form class="pf-form" autocomplete="off">
    <div class="seg"><button type="button" data-t="buy">Buy</button><button type="button" data-t="sell">Sell</button></div>
    <label class="fld"><span>Symbol or company</span><input name="sym" placeholder="type e.g. TCS or Tata" required spellcheck="false" autocapitalize="characters" enterkeyhint="next"></label>
    <label class="fld pf-name" hidden><span>Company name <i>optional, not in the lists</i></span><input name="nm" spellcheck="false"></label>
    <div class="row2"><label class="fld"><span>Qty <i class="held"></i></span><input name="qty" type="number" inputmode="decimal" min="0" step="any" required></label>
    <label class="fld"><span>Price ₹ <i class="ltp"></i></span><input name="px" type="number" inputmode="decimal" min="0" step="any"></label></div>
    <div class="row2"><label class="fld"><span>Date</span><input name="date" type="date"></label>
    <label class="fld"><span>Charges ₹ <i>auto</i></span><input name="ch" type="number" inputmode="decimal" min="0" step="any"></label></div>
    <p class="pf-calc"></p><p class="pf-err"></p>
    <div class="pf-fix" hidden><span>No price for this symbol. Renamed or wrong?</span><button type="button" class="btn ghost" data-fix>Update symbol / name</button></div>
    <button type="submit" class="btn primary">Save</button></form>`, {noFocus: true, ov: true});   // no box chosen yet: typing is routed below
  const f = $('form', w); let t = x.t || 'buy', chTyped = edit && x.ch != null, touched = false;
  f.sym.value = x.sym || ''; f.qty.value = x.qty || ''; f.px.value = x.px || ''; f.date.value = x.date || today(); f.ch.value = edit ? (x.ch || 0) : '';
  f.nm.value = x.name || '';
  const seg = () => w.querySelectorAll('.seg button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  const ltp = () => pfLtp(f.sym.value.trim().toUpperCase());
  const calc = () => {
    const sym = f.sym.value.trim().toUpperCase(), L = ltp(), q = +f.qty.value, p = +f.px.value || L || 0;   // empty price → LTP
    f.px.placeholder = L ? 'LTP ' + fmt(L, 2) : 'price';
    $('.ltp', w).textContent = L && !f.px.value ? 'blank = LTP' : '';
    const h = sym ? held() : 0; $('.held', w).textContent = sym && h ? `(holding ${fmt(h, 0)})` : '';
    const sb = w.querySelector('.seg [data-t=sell]'); sb.disabled = !(h > 0); sb.title = h > 0 ? '' : 'Nothing to sell: you hold none';
    if (t === 'sell' && !(h > 0)) { t = 'buy'; seg(); }        // can't sell what you don't hold
    $('.pf-name', w).hidden = !sym || TODAY.has(sym);
    $('.pf-fix', w).hidden = !(sym && h > 0 && L == null);     // a holding with no price at all: offer to fix its symbol
    const over = t === 'sell' && q > h + 1e-9;                  // selling more than held: red right away
    f.qty.classList.toggle('bad', over);
    $('.held', w).classList.toggle('bad', over);
    f.querySelector('button[type=submit]').disabled = over;
    $('.pf-err', w).textContent = over ? `You hold ${fmt(h, 0)}; you can't sell ${fmt(q, 0)}.` : ($('.pf-err', w).textContent.startsWith('You hold') ? '' : $('.pf-err', w).textContent);
    if (!chTyped) f.ch.value = q > 0 && p > 0 ? pfCharges(t, q, p) : '';
    const ch = +f.ch.value || 0;
    $('.pf-calc', w).textContent = q > 0 && p > 0 ? (t === 'buy' ? `Cash out ₹${fmt(q * p + ch, 2)} (₹${fmt(q * p, 2)} + charges)` : `Cash in ₹${fmt(q * p - ch, 2)} (₹${fmt(q * p, 2)} − charges)`) : '';
  };
  symPicker(f.sym, it => { if (it && it.nse && it.name) f.nm.value = it.name; calc(); f.qty.focus(); });   // NSE stock: its name filled in
  w.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => { t = b.dataset.t; seg(); calc(); }));
  ['sym', 'qty', 'px'].forEach(k => f[k].addEventListener('input', calc));
  f.sym.addEventListener('change', calc);
  f.ch.addEventListener('input', () => { chTyped = f.ch.value !== ''; calc(); });
  /* before the user clicks a box: digits → Qty, letters → Symbol */
  w.addEventListener('pointerdown', e => { if (e.target.closest('input')) touched = true; });
  const route1 = e => {
    if (!w.isConnected) return document.removeEventListener('keydown', route1, true);
    if (touched || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) return;
    if (document.activeElement && document.activeElement.tagName === 'INPUT') { touched = true; return; }
    const into = /[0-9.]/.test(e.key) ? f.qty : /[A-Za-z&]/.test(e.key) ? f.sym : null;
    if (!into) return;
    e.preventDefault(); e.stopImmediatePropagation(); touched = true;   // the page's own "type to search" must not get it
    into.focus(); into.value = into === f.sym && !x.sym ? e.key.toUpperCase() : (into.value && into === f.qty ? into.value : '') + e.key;
    into.dispatchEvent(new Event('input'));
  };
  document.addEventListener('keydown', route1, true);
  $('[data-fix]', w).addEventListener('click', () => pfRenameForm(f.sym.value.trim().toUpperCase()));
  seg(); calc();
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const err = m => { $('.pf-err', w).textContent = m; };
    const sym = f.sym.value.trim().toUpperCase().replace(/^NSE:/, ''), qty = +f.qty.value, px = +f.px.value || ltp();
    if (!/^[A-Z0-9&._-]{1,20}$/.test(sym)) return err('Type a valid NSE symbol, e.g. TCS.');
    if (!(qty > 0)) return err('Quantity must be above 0.');
    if (!(px > 0)) return err(`No price for ${sym}. Type the price.`);
    if (t === 'sell' && !(held() > 0)) return err(`You hold no ${sym}, so there is nothing to sell.`);
    if (t === 'sell' && qty > held() + 1e-9) return err(`You hold ${fmt(held(), 0)} ${sym}; you can't sell ${fmt(qty, 0)}.`);
    if (!edit && PF.txns.length >= 3000) return err('Transaction limit reached (3000).');
    const rec = {id: x.id || pfId(), t, sym, qty, px, ch: f.ch.value === '' ? pfCharges(t, qty, px) : +f.ch.value, date: f.date.value || ''};
    if (t === 'buy') {                                     // PE and value score on the day you bought (kept when you edit the trade later)
      const s0 = TODAY.get(sym), pe = x.pe != null ? x.pe : s0 && s0.pe > 0 ? +(+s0.pe).toFixed(2) : null, sc = x.sc != null ? x.sc : s0 && s0.score != null ? Math.round(s0.score) : null;
      if (pe != null) rec.pe = pe; if (sc != null) rec.sc = sc;
    }
    if (!TODAY.has(sym) && f.nm.value.trim()) rec.name = f.nm.value.trim().slice(0, 80);
    if (t === 'buy') { const sr = x.src || (edit ? '' : pfSrcHere()); if (sr) rec.src = sr; }   // Remark (kept when the trade is edited)
    const next = edit ? PF.txns.map(o => o === x ? rec : o) : PF.txns.concat(rec);
    const bad = pfReplay(next).bad;                         // a sale can't exceed what was held on its date, now or after this change
    if (bad) return err(`That would make the sale of ${bad.sym}${bad.date ? ' on ' + bad.date : ''} larger than the shares held then. Check the date and quantity.`);
    PF.txns = next;
    await pfSave(); w._close();
    if ((location.hash || '') === '#/portfolio') route();
    else { pfMarkBags(); toast(`${t === 'buy' ? 'Bought' : 'Sold'} ${fmt(qty, 0)} ${sym} @ ₹${fmt(px, 2)} · saved to Portfolio`); }
  });
}
/* a held symbol that has no price (renamed on NSE, typo, delisted): move its open trades to a new symbol / name */
function pfRenameForm(old) {
  const lock = pfReplay(PF.txns).lock;
  const ids = PF.txns.filter(t => t.sym === old && !lock.has(t.id)).map(t => t.id);
  const w = modal('Update symbol / name', `<form class="pf-form" autocomplete="off">
    <p class="pf-calc">Moves the ${ids.length} open trade${ids.length === 1 ? '' : 's'} of <b>${esc(old)}</b> to the new symbol. Closed records keep the old one.</p>
    <label class="fld"><span>New symbol</span><input name="sym" value="${esc(old)}" spellcheck="false" autocapitalize="characters"></label>
    <label class="fld"><span>Company name <i>optional</i></span><input name="nm" spellcheck="false"></label>
    <p class="pf-err"></p><button type="submit" class="btn primary">Update</button></form>`, {ov: true});
  const f = $('form', w);
  symPicker(f.sym, () => {});
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const sym = f.sym.value.trim().toUpperCase();
    if (!/^[A-Z0-9&._-]{1,20}$/.test(sym)) { $('.pf-err', w).textContent = 'Type a valid NSE symbol.'; return; }
    const nm = f.nm.value.trim().slice(0, 80);
    PF.txns = PF.txns.map(t => ids.includes(t.id) ? Object.assign({}, t, {sym}, nm ? {name: nm} : {}) : t);
    await pfSave(); w._close(); route(); toast(`${old} → ${sym}`);
  });
}
/* Pay in / Pay out (new, or edit) */
function pfCashForm(x) {
  const edit = !!x.id;
  const w = modal(edit ? 'Edit cash entry' : 'Pay in / Pay out', `<form class="pf-form" autocomplete="off">
    <div class="seg"><button type="button" data-t="in">Pay in</button><button type="button" data-t="out">Pay out</button></div>
    <label class="fld"><span>Amount ₹</span><input name="amt" type="number" min="0" step="any" required></label>
    <label class="fld"><span>Date</span><input name="date" type="date"></label>
    <p class="pf-calc">Pay in = money added (your opening cash too). Pay out = money taken out.</p><p class="pf-err"></p>
    <button type="submit" class="btn primary">Save</button></form>`);
  const f = $('form', w); let t = x.t || 'in';
  f.amt.value = x.amt || ''; f.date.value = x.date || today();
  const seg = () => w.querySelectorAll('.seg button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  w.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => { t = b.dataset.t; seg(); }));
  seg();
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const amt = +f.amt.value;
    if (!(amt > 0)) { $('.pf-err', w).textContent = 'Amount must be above 0.'; return; }
    const rec = {id: x.id || pfId(), t, amt, date: f.date.value || ''};
    PF.txns = edit ? PF.txns.map(o => o === x ? rec : o) : PF.txns.concat(rec);
    await pfSave(); w._close(); route();
  });
}
/* brokerage / tax rates used for new trades (old trades keep the charges they were saved with) */
function pfRatesForm() {
  const w = modal('Charges', `<form class="pf-form" autocomplete="off">
    <p class="pf-calc">Equity delivery on NSE. Defaults follow the published statutory rates; set brokerage to your broker's
    (e.g. 0.1% with a ₹20 cap). Rates change from time to time, so check your contract note. Only new trades use these.</p>
    <div class="row2">${Object.keys(PF_RATES).map(k => `<label class="fld"><span>${PF_RATE_LABEL[k]}</span><input name="${k}" type="number" min="0" step="any" value="${PF.rates[k]}"></label>`).join('')}</div>
    <p class="pf-calc ex"></p>
    <div class="pf-btns"><button type="button" class="btn ghost" data-def>Defaults</button><button type="submit" class="btn primary">Save</button></div></form>`);
  const f = $('form', w), read = () => Object.fromEntries(Object.keys(PF_RATES).map(k => [k, f[k].value === '' ? PF_RATES[k] : +f[k].value]));
  const ex = () => { const r = read(); $('.ex', w).textContent = `Example: ₹1,00,000 buy = ₹${fmt(pfCharges('buy', 1, 1e5, r), 2)}, sell = ₹${fmt(pfCharges('sell', 1, 1e5, r), 2)} in charges.`; };
  f.addEventListener('input', ex); ex();
  $('[data-def]', w).addEventListener('click', () => { Object.keys(PF_RATES).forEach(k => f[k].value = PF_RATES[k]); ex(); });
  f.addEventListener('submit', async e => { e.preventDefault(); PF.rates = read(); await pfSave(); w._close(); route(); });
}

/* bag next to every company name (all tabs): opens Buy / Sell for that stock */
document.addEventListener('click', async e => {
  const b = e.target.closest && e.target.closest('[data-bag]');
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  await pfLoad();
  pfTradeForm({sym: b.dataset.bag});
}, true);
function toast(msg) {
  document.querySelectorAll('.toast').forEach(o => o.remove());          // one at a time
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('on'));
  setTimeout(() => { t.classList.remove('on'); setTimeout(() => t.remove(), 300); }, 2600);
}

function methodView() {
  setNav('method'); view.innerHTML = '';
  const W = lsJson('vw', W_DEF);
  const N = D.stocks;
  /* one glossary row: name · what it means · a one-line example */
  const g = (name, what, ex) => `<tr><td><b>${name}</b></td><td>${what}</td><td class="ex">${ex}</td></tr>`;
  const head = '<tr><th>Number</th><th>What it means</th><th>Example</th></tr>';
  const tbl = rows => `<div class="wrap"><table class="gl">${head}${rows}</table></div>`;
  const backs = (D.backs || []).map(b => `${esc(b.label)} = ${esc(b.date)}${b.hasPrice ? '' : ' (no prices yet)'}`).join(' · ');
  view.insertAdjacentHTML('beforeend', `<div class="doc">

  <h2>In one minute</h2>
  <div class="cards">
    <div class="card"><b>Industries</b><span>Every sector with its typical PE, PB and ROE. Tap a sector to see its stocks, cheapest first.</span></div>
    <div class="card"><b>Value screen</b><span>Stocks that look cheap <i>and</i> healthy, ranked by Value score. Four ready-made Screens sit on top.</span></div>
    <div class="card"><b>All Stocks</b><span>Every stock, no filter switched on. Turn on only the filters you want.</span></div>
    <div class="card"><b>Historical</b><span>Switch it on and pick a date (3M to 1.5Y) to see the page as it looked then, and how each stock did since.</span></div>
  </div>
  <p><b>Where the numbers come from:</b> Screener.in data for ${D.lists.map(esc).join(', ')} (downloaded ${esc(D.asOf)}), ${N.length} stocks, consolidated accounts where published.
  Prices for past dates come from Angel One daily closes, adjusted for splits and bonuses. Everything refreshes in the weekly run.
  ${D.sameToday ? `<br><b>One method for every date:</b> today's PE, ROE, ROCE, yields and trend are rebuilt from the published quarterly and annual results
  with exactly the formulas used for 3M … 1.5Y back (priced with Angel's latest close), so a change between dates is the business or the price, never the method.
  They can differ a little from the ratios shown on Screener.in, which uses its own conventions.` : ''}</p>
  <p><b>Header switches:</b> <b>List</b> picks the index, <b>PSU</b> / <b>Semi PSU</b> hide government companies (struck through = hidden). They apply to every tab; industry averages always use all stocks so they stay fair.</p>

  <h2>Price and valuation</h2>
  ${tbl(
    g('CMP ₹', 'Current market price of one share.', 'CMP 250 = one share costs ₹250.')
  + g('Mcap ₹ Cr', 'Market cap: price × all shares. What the whole company costs.', 'Mcap 5,000 = the market values it at ₹5,000 crore.')
  + g('PE', 'Price ÷ yearly profit per share. Years of today\'s profit to earn back the price.', 'PE 15 = you pay ₹15 for every ₹1 of yearly profit.')
  + g('Core PE', 'PE after removing "other income" (interest, one-offs) from profit. Shows how cheap the main business is.', 'PE 10 but Core PE 18 = half the profit is not from the business.')
  + g('Industry PE', 'Typical PE of the sector (median by default; average or Mcap-weighted from the picker).', 'Industry PE 25 = a normal stock here trades at 25×.')
  + g('vs Industry PE', 'How much cheaper (−) or dearer (+) than its sector.', '−30% = PE 30% below the sector\'s.')
  + g('PB', 'Price ÷ book value (what shareholders own on the balance sheet).', 'PB 1 = you pay exactly the book value.')
  + g('PEG', 'PE ÷ yearly EPS growth. Below 1 = cheap for its growth.', 'PE 20 with 25% growth → PEG 0.8.')
  + g('Earnings yield %', '100 ÷ PE: profit as a % of the price, like an interest rate.', 'PE 12.5 → earnings yield 8%.')
  + g('Div yield %', 'Yearly dividend ÷ price.', '₹5 dividend on a ₹250 share = 2%.')
  + g('Graham upside', 'Gap to Graham\'s fair price √(22.5 × EPS × book value per share).', '+40% = price is 40% below Graham\'s number.')
  + g('Mcap ÷ Assets', 'Market cap ÷ everything the company owns on its books. Below 1× = valued under its assets.', '0.8× = buy ₹100 of assets for ₹80.')
  + (has('pfa') ? g('Mcap ÷ Fixed assets', 'Market cap ÷ plant, buildings and land at book cost.', '0.5× = the factories alone are worth twice the Mcap.') : '')
  + (has('invp') ? g('Investments % Mcap', 'Investments held ÷ market cap. High for holding companies.', '60% = the shares it owns are 60% of its own price.') : '')
  + g('vs 52W high', 'How far the price is below its 1-year high.', '−35% = 35% below the year\'s top.')
  )}

  <h2>Quality and growth</h2>
  ${tbl(
    g('ROE %', 'Profit ÷ shareholders\' money. How well it uses owners\' capital.', 'ROE 18 = ₹18 profit a year on every ₹100 of equity.')
  + g('ROCE %', 'Operating profit ÷ (equity + debt). Return on all the money in the business. Banks use ROE instead.', 'ROCE 20 = ₹20 a year on every ₹100 employed.')
  + g('ROCE 5Y med', 'Middle value of the last 5 years\' ROCE, so one great or bad year can\'t fool it.', '22, 8, 15, 18, 12 → median 15.')
  + g('EPS CAGR', 'Yearly growth of profit per share over up to 5 years. "(PAT)" = total profit used because the share count changed a lot.', 'EPS 10 → 16 in 5 years ≈ 10% a year.')
  + g('Profit ↑ yrs', 'Years (of the last 10) in which profit grew.', '8/10 = profit rose in 8 of 10 years.')
  + g('OPM vs 5Y', 'Operating margin now minus its 5-year average, in percentage points.', '+3 pp = margin 18% vs usual 15%.')
  + g('ROA %', 'Profit ÷ total assets. The key return for banks / NBFCs.', 'ROA 1.5 = ₹1.5 profit per ₹100 of loans and assets.')
  )}

  <h2>Cash and debt</h2>
  ${tbl(
    g('Cash conv.', `Cash from operations ÷ reported profit, last ${D.cashYears} years. Shows if profit turns into real cash.`, '0.9× = ₹90 cash for every ₹100 of profit.')
  + g('CFO+ yrs', 'Years (of the last 10) with positive operating cash flow.', '9/10 = cash came in 9 of 10 years.')
  + g('FCF yield', 'Average free cash flow (operations + investing) ÷ market cap.', '5% = ₹5 spare cash a year per ₹100 of Mcap.')
  + g('Debt ÷ Equity', 'Borrowings ÷ shareholders\' money.', '0.3 = ₹30 of loans for every ₹100 of equity.')
  + g('Debt ÷ EBITDA', 'Years of operating profit needed to repay all debt.', '2× = two years of profit clears the debt.')
  )}

  <h2>Banks and NBFCs</h2>
  ${tbl(
    g('Gross NPA %', 'Loans not being repaid, as % of all loans.', '2% = ₹2 of every ₹100 lent is bad.')
  + g('Net NPA %', 'Bad loans left after money set aside for them.', '0.5% = only ₹0.5 per ₹100 still uncovered.')
  + g('PCR %', 'Share of bad loans already provided for (≈ 1 − Net ÷ Gross NPA).', 'Gross 2%, Net 0.5% → PCR 75%.')
  )}
  <p>Financials skip numbers that don't fit them (cash conversion, FCF yield, debt ratios, Mcap ÷ Assets) and use ROE instead of ROCE.</p>

  <h2>Scores</h2>
  ${tbl(
    g('Value score (0–100)', 'How cheap and healthy a stock is <b>versus its own industry</b>. Each metric below is ranked within the sector, then weighted.', '80 = better than ~80% of its sector on the mix below.')
  + g('Momentum (0–100)', 'How strongly the price has been rising: 3-month, 6-month and 12-month (minus the last month) returns, ranked against every stock.', '75 = price trend stronger than 75% of stocks.')
  + g('Trend', 'Business-improvement checks passed: profit, revenue, margin, promoter holding, ROE, ROCE, cash conversion vs a year earlier.', '6/7 = six of seven things got better.')
  + g('Promoter Δ', 'Change in promoter holding over the quarters available, in percentage points.', '+1.5 pp = promoters bought more.')
  )}
  <p><b>Value score weights</b> (change them under Filters → Scoring). Each metric is ranked within the stock's industry (all stocks if the sector has fewer than 4); a score needs a positive PE and at least half the weight.</p>
  <div class="wrap"><table class="gl"><tr><th>Metric</th><th>Better when</th><th>Weight</th></tr>
  ${Object.keys(W_DEF).map(k => `<tr><td>${W_LABEL[k]}</td><td>${METRICS[k].hi ? 'higher' : 'lower'}</td><td>${W[k]}</td></tr>`).join('')}</table></div>
  <p><b>Momentum tip:</b> it is not part of the Value score. Cheap stocks are often falling; Min momentum 40 (the Value screen default) skips the ones still in free fall.</p>

  <h2>Momentum (Momentum tab, Momentum column)</h2>
  <p>How strongly a stock's price has been rising, as a score from 0 to 100. Worked out every week from Angel One daily closes (split and bonus adjusted):</p>
  ${tbl(
    g('3-month return', 'Price change over the last 3 months.', '₹100 → ₹112 = +12%.')
  + g('6-month return', 'Price change over the last 6 months.', '₹100 → ₹125 = +25%.')
  + g('12-minus-1 return', 'From 12 months ago to 1 month ago. The latest month is left out because very short moves often reverse.', '₹100 a year ago → ₹140 a month ago = +40%.')
  + g('Score', 'Each return is ranked against every stock on screen (0 = worst, 100 = best) and the ranks are averaged. Needs at least 2 of the 3.', 'Ranks 80, 70, 90 → momentum 80.')
  )}
  <p><b>Momentum tab:</b> strongest first, showing <b>70 and above</b> by default (the strong trends). Filters has every Value-screen filter plus <b>Max momentum</b>, e.g. 90 to skip stocks that have already run very far.
  <b>Value screen</b> uses Min momentum 40 by default, to skip cheap stocks that are still falling. With <b>Historical</b> on, momentum uses only prices up to that date, so it can be backtested.</p>

  <h2>${pfName()}</h2>
  <p>Your own holdings, saved to your login (only you can see them). Everything is worked out from your list of transactions, so editing or deleting a wrong entry fixes all the numbers after it.</p>
  ${tbl(
    g('Pay in / Pay out', 'Money you add or take out. Your opening cash is a Pay in.', 'Pay in ₹5,00,000 on day one.')
  + g('Buy / Sell', 'Trades at average cost. Leave the price blank to use the LTP (the latest close on this page). The bag next to any industry name, on any tab, opens this; selling more than you hold on that date is refused.', 'Buy 10 @ 2,000, then 10 @ 2,200 → average 2,100.')
  + g('Charges', 'Worked out per trade from the rates under ⚙ Charges (STT, exchange, SEBI, stamp duty, GST, DP, your brokerage) and taken from cash. Type over them to use your contract note.', '₹1,00,000 delivery buy ≈ ₹118 with zero brokerage.')
  + g('Cash', 'Pay ins − pay outs − buys + sells − all charges.', '5,00,000 − 2,00,000 buy − 237 charges = 2,99,763.')
  + g('Unrealised', 'Open holdings at LTP minus what they cost.', 'Cost 21,000, now 23,000 → +2,000.')
  + g('Realised', 'Profit on what you sold (sell price − average cost), after charges.', 'Sold 10 @ 2,300, average 2,100 → +2,000 before charges.')
  + g('Net gain', 'Net worth (holdings + cash) minus the money you put in.', 'Put in 5,00,000, worth 5,40,000 → +40,000 (+8%).')
  + g('Closed', 'A stock sold down to 0 moves here and its trades lock; its money is back in Cash. Only its Remove button (type confirm) deletes it.', 'Bought 100, sold 100 → closed.')
  )}
  <p>Stocks outside these lists can be added too; they have no price here, so they are valued at cost. Prices are the latest in this page's data (updated weekly), not live.</p>

  <h2>Checklist light</h2>
  <p>Tap any stock name to see its <b>checklist</b>: plain yes / no tests on the published numbers (profit in each recent quarter, profit and sales growth,
  profit every year, other income, valuation vs Graham number / its past PE / its industry, momentum, debt, ROCE, promoter holding).
  Growth and profit-quality tests count double. The share of points passed lights a small traffic light:
  <b style="color:#3ec65a">green</b> ${VD_LIM.buyAt}% or more · <b style="color:#f0a020">amber</b> ${VD_LIM.watchAt}–${VD_LIM.buyAt}% · <b style="color:#e5413a">red</b> below ${VD_LIM.watchAt}%
  or a loss in any of the latest 4 quarters. The little line next to it shows the same score on each Historical date, with the price.</p>
  ${VD_DISCLAIMER}

  <h2>Screens (Value screen)</h2>
  ${tbl(
    g('Asset-backed', 'Mcap ÷ Assets ≤ 1, PB ≤ 1.5, ROCE ≥ 10%, Debt ÷ Equity ≤ 0.5.', 'Company owns ₹1,000 Cr of assets, priced at ₹800 Cr, little debt.')
  + g('Graham defensive', 'PE × PB ≤ 22.5 plus all the safety tests below.', 'PE 12 × PB 1.5 = 18 ✓.')
  + g('Graham deep value', 'Graham defensive + PE ≤ 15 and PB ≤ 2.', 'PE 9, PB 1.2, safe balance sheet.')
  + g('Deep value', 'PE ≤ 15, PB ≤ 2, ROCE ≥ 10%.', 'PE 11, PB 1.6, ROCE 14% ✓.')
  )}
  <p>Every screen also hides value traps and keeps only dividend payers (Min div yield 0.01%). Click a screen again to switch it off.</p>

  <h2>Graham safety tests</h2>
  ${tbl(
    g('Steady returns', 'ROCE 5Y median ≥ 12% (ROE for financials).', 'Median 14% ✓; one good year among bad ones ✗.')
  + g('Steady profits', 'Profit grew in at least 7 of the last 10 years.', '7/10 ✓, 5/10 ✗.')
  + g('Low debt', 'Debt ÷ Equity &lt; 1 <b>or</b> Debt ÷ EBITDA &lt; 2.5× (non-financials).', 'D/E 1.4 but debt = 2 years of profit ✓.')
  + g('Bank profitability', 'ROA ≥ 1.5% (banks, NBFCs, insurers).', 'ROA 1.8% ✓.')
  + g('Clean loan book', 'Gross NPA &lt; 2.5%, Net NPA &lt; 1%, PCR &gt; 70% (banks).', 'Gross 1.9%, Net 0.4%, PCR 79% ✓.')
  )}
  <p>A test with no data yet is skipped, never failed. Each test is also its own filter under Filters → More.</p>

  <h2>Value-trap flags (Hide value traps)</h2>
  ${tbl(
    g('EPS shrinking', 'Profit per share fell over the years (EPS CAGR ≤ 0).', 'EPS 20 → 14.')
  + g('no growth data / loss now', 'Growth can\'t be measured, usually because of a loss now.', 'EPS −3 this year.')
  + g('loss yr', 'A loss in any of the last 5 years.', 'Loss in FY22.')
  + g('EPS near 5Y low', 'Last 12 months\' EPS within 10% of its 5-year low.', '5Y low 10, now 10.8.')
  + g('weak cash conversion', 'Less than 0.5× of profit turns into cash.', '₹100 profit, ₹35 cash.')
  + g('other-income heavy', 'Over 40% of pre-tax profit is other income.', '₹50 of ₹100 PBT is interest income.')
  + g('low interest cover', 'Operating profit &lt; 2× the interest bill.', 'EBIT ₹15 Cr, interest ₹10 Cr.')
  + g('equity dilution', 'Share count growing over 6% a year.', '100 Cr shares → 135 Cr in 5 years.')
  )}

  <h2>Historical (backtest)</h2>
  <p>Switch <b>Historical</b> on and pick a date: ${backs || '–'}. Every company is rebuilt as it stood then, from the results already published by that date, priced at that day's close.
  Extra "since" columns show what happened next (price return, profit growth, ROE change). The same formulas run on every date, so dates compare fairly.</p>
  <p><b>Example:</b> Historical 1Y + Graham deep value → the stocks that passed a year ago, and how much each has returned since.</p>
  <p><b>Keep in mind:</b> only today's index members are included (stocks that dropped out, often the losers, are missing), and older dates have fewer quarters of history, so Trend has less to work with.
  ${D.then && D.then.hasPrice ? '' : '<b>No prices yet:</b> price-based filters are off on past dates.'}</p>

  <h2>Downloads, copy and PSU tags</h2>
  <p><b>⤓ Download</b> saves the table you see (all rows, search and sort applied) as CSV. The name starts with the list (<code>all</code>, <code>N500</code>, <code>NS500</code>, <code>NM250</code>) and the filters set, e.g. <code>N500_Value_screen_mcap_20K_ROE12.csv</code>. <b>Copy</b> puts Symbol, Company, Industry, CMP, PE and PB on the clipboard.</p>
  <p><b>PSU</b> (${N.filter(s => s.psu === 'psu').length} stocks): government owns a majority. <b>Semi</b> (${N.filter(s => s.psu === 'semi').length}): large government / PSU stake without control. Tags come from a fixed list in the script.</p>

  <h2>Limits</h2>
  <p>Not available from Screener: cash balances (so debt is gross, not net), pledge data, capital adequacy (CRAR) for banks. Past figures are as Screener shows them today (restated), and the screener's Grade / Fair Value is a separate model, so the two pages can disagree.</p>
  </div>`);
}

function backInfo() {                                    // small popover behind the (i)
  const mode = k => { const c = {}; S.forEach(s => { const v = sv(s,k); if (v) c[v] = (c[v]||0) + 1; });
                      return Object.keys(c).sort((a,b) => c[b]-c[a])[0] || '–'; };
  const bt = btOf(BT_ROUTE(location.hash || '#/')), T = D.then;
  return `<b>As it stood on ${esc(T.date)}</b>Rebuilt from the results published by then. Latest results then: quarter ${esc(mode('qThen'))}, FY ${esc(mode('fyThen'))}.
    "Since" columns compare with the latest now (quarter ${esc(mode('qNow'))}, FY ${esc(mode('fyNow'))}).
    ${T.hasPrice ? `Prices: close on ${esc(T.priceDateThen || '–')} → ${esc(T.priceDate || '–')}${T.bench ? `; NIFTY 500 ${fmt(T.bench.ret,1)}%` : ''}.`
                 : 'No prices.csv yet, so price filters and columns are off at this date.'}`
    + (bt ? `<span class="bt">This tab: <b>${bt.lvl}</b>. ${bt.why} <a href="#/method">Details</a></span>` : '');
}
/* Demo (review) login: only All Stocks and the Paper portfolio. auth.js keeps the hint (hni_demo) so the right page shows at once. */
const DEMO_HINT = (() => { try { return localStorage.getItem('hni_demo') === '1'; } catch (e) { return false; } })();
const isDemo = () => window.hniDemo != null ? !!window.hniDemo : DEMO_HINT;
if (DEMO_HINT) document.documentElement.classList.add('hni-demo');
window.addEventListener('hni-ready', () => { if (isDemo() && !/^#\/(all|portfolio)$/.test(location.hash || '')) route(); });
function route() {
  if (isDemo() && !/^#\/(all|portfolio)$/.test(location.hash || '')) history.replaceState(null, '', location.pathname + location.search + '#/all');
  const pb = $('#pfBackV'); if (pb) pb.href = isDemo() ? '#/all' : '#/value';
  const h0 = location.hash || '#/';
  if (h0 === '#/portfolio' && GF.back) { PF_RESUME = GF.backP; return setHistorical(false, null, true); }   // Portfolio: today only
  if (h0 !== '#/portfolio' && PF_RESUME && !GF.back) { const k = PF_RESUME; PF_RESUME = null; return setHistorical(true, k, true); }   // back on
  setMode();
  C.score.label = NOPRICE ? 'Quality score (then)' : 'Value score';
  C.roce.label = C.q.label = SAME && S.some(s => s.roceSrc === 'ta') ? 'ROCE % (proxy)' : 'ROCE %';   // EBIT ÷ total assets until Borrowings are scraped
  computeScores();
  const h = location.hash || '#/';
  if (h.startsWith('#/industry/')) industryView(decodeURIComponent(h.slice(11)));
  else if (h === '#/value') valueView();
  else if (h === '#/method') methodView();
  else if (h === '#/all') allView();
  else if (h === '#/momentum') momentumView();
  else if (h === '#/portfolio') portfolioView();
  else homeView();
  meta();
  $('#pfBag').classList.toggle('on', h === '#/portfolio');            // header bag lights up on the Portfolio page
  document.body.classList.toggle('pf-mode', h === '#/portfolio');
  if (h !== '#/portfolio' && !HDR_OK) requestAnimationFrame(lockHeader);   // header back from Portfolio: measure it now     // Portfolio: a quiet header (‹ Value, Weekly, Portfolio, avatar, day/night)
  window.scrollTo(0,0);
}
function nowInfo() {                                     // (i) popover for the current view
  const T = D.then || {};
  return `<b>As on ${esc(D.asOf)}</b>Fundamentals from Screener, downloaded ${esc(D.asOf)}. `
    + (D.sameToday ? "Ratios rebuilt from the quarterly and annual results with the same formulas as every Historical date, so today vs back is like for like. "
                   : "Ratios are Screener's own (PE, ROE, ROCE, yields). ")
    + (T.hasPrice ? `Prices: Angel close ${esc(T.priceDate || '–')}.` : 'Prices: CMP from the CSV.')
    + ' <a href="#/method">Methodology</a>';
}
function meta() {
  const day = BACK ? D.then.date : D.asOf;
  $('#meta').innerHTML = `<span class="backnote"><b>${V().length}</b> stocks in <b>${Object.keys(industries).length}</b> sectors as on <b>${esc(day)}</b> `
    + `<button type="button" class="info" id="backInfo" aria-label="About this data">i</button>`
    + `<span class="pop" id="backPop">${BACK ? backInfo() : nowInfo()}</span></span>`;
  const b = $('#backInfo');
  if (b) b.addEventListener('click', e => { e.stopPropagation(); $('#backPop').classList.toggle('open'); });
}
document.addEventListener('click', e => { const p = $('#backPop'); if (p && !e.target.closest('#backPop')) p.classList.remove('open'); });
const saveGF = () => lsSet('gf', JSON.stringify(GF));
[['psu','#hidePSU'], ['semi','#hideSemi']].forEach(([k, id]) => {
  const el = $(id); el.checked = !!GF[k];
  el.addEventListener('change', () => { GF[k] = el.checked; saveGF(); route(); });
});
/* Historical: an on / off chip + a period dropdown (3M … 1.5Y). Every page load starts with Historical OFF and 1Y.
   Choosing a period switches Historical on; clicking the chip again switches it off. 1Y is inside the page,
   the other periods load once, on first use (fundamentals_back_<key>.json). */
GF.back = false; GF.backP = '1Y';
const BACK_CACHE = Object.assign(D.then && D.then.stocks ? {[D.then.key || '1Y']: D.then} : {}, D.backData || {});   // website: 1Y loads like the others
const bsel = $('#backSel'), hist = $('#histOn'), bchip = $('#backChip');
bsel.innerHTML = (D.backs || [{key:'1Y', label:'1Y', date:D.then.date}]).map(b => `<option value="${b.key}">${esc(b.label)}</option>`).join('');
bsel.value = '1Y';
const backLabel = k => ((D.backs || []).find(b => b.key === k) || {label:k}).label;
bchip.title = 'Show the companies as they stood on a past date (same-method engine) and how each did since'
  + ((D.backs || []).length ? ': ' + D.backs.map(b => b.label + ' = ' + b.date).join(', ') : '');
/* Historical is remembered for this browser session (closing the tab forgets it). Portfolio is about today, so
   opening it switches Historical off for the moment and leaving it puts it back. temp = that automatic switch. */
const HS_KEY = 'hni_hist';
let PF_RESUME = null;
function setHistorical(on, key, temp) {
  key = key || GF.backP;
  GF.back = !!on; GF.backP = key; hist.checked = GF.back; bsel.value = key; if (bsel._paint) bsel._paint();
  if (!temp) { PF_RESUME = null; try { sessionStorage.setItem(HS_KEY, JSON.stringify({on: GF.back, key})); } catch (e) {} }
  if (!GF.back) { route(); return; }
  if (BACK_CACHE[key]) { D.then = BACK_CACHE[key]; route(); return; }
  bchip.classList.add('loading');
  getBack(key)
    .then(j => { BACK_CACHE[key] = j; bchip.classList.remove('loading'); if (GF.backP === key && GF.back) { D.then = j; route(); } })
    .catch(() => {
      bchip.classList.remove('loading');
      alert(backLabel(key) + ' could not be loaded. It loads from the website; a saved offline copy has 1Y only.');
      setHistorical(GF.back, '1Y');
    });
}
hist.checked = false;
hist.addEventListener('change', () => setHistorical(hist.checked));
bsel.addEventListener('change', () => setHistorical(GF.back, bsel.value));   // picking a date keeps Historical as it is
const us = $('#uniSel');
/* Lists and Prime. Free accounts: NIFTY 500 only. All lists, Smallcap 500, Microcap 250 and Others are Prime: shown in purple,
   locked for free accounts (picking one opens the plans popup). Prime members and admins start on All lists and get Others too.
   PRO starts from auth.js's hint (last visit), then the real answer (setPro, below) corrects it. */
const FREE_LIST = 'NIFTY 500';
// PRO = all lists unlocked (Plus, Prime or admin); PRIMEF = Prime or admin (the Checklist tab)
let PRO = (() => { try { return localStorage.getItem('hni_plus') === '1' || localStorage.getItem('hni_pro') === '1'; } catch (e) { return false; } })();
let PRIMEF = (() => { try { return localStorage.getItem('hni_pro') === '1'; } catch (e) { return false; } })();
const UNI_WANT = GF.uni;                                    // last visit's list: may be one added later (hniMore)
const uniFill = () => {
  const lists = D.lists.filter(l => l !== OTH).concat(OTH);  // Others is always listed (its stocks load for Prime)
  us.innerHTML = [['all', 'All lists'], ...lists.map(l => [l, l])].map(([v, t]) => {
    const pr = v !== FREE_LIST;
    return `<option value="${esc(v)}"${pr && !PRO ? ' data-prime="1" data-lock="1"' : ''}>${esc(t)}</option>`;   // Prime: plain options (no purple on the purple page)
  }).join('');
};
const uniPick = want => {                                   // Prime: the list asked for (else All lists); free: NIFTY 500
  us.value = PRO ? ([...us.options].some(o => o.value === want) ? want : 'all') : FREE_LIST;
  GF.uni = us.value;
};
const uniHintPaint = () => {                                // "NIFTY 500, Smallcap 500 and Microcap 250 selected"
  const h = $('#uniHint'); if (!h) return;
  const l = D.lists.filter(x => x !== OTH).map(x => `<b>${esc(x)}</b>`);
  h.innerHTML = (l.length > 1 ? l.slice(0, -1).join(', ') + ' and ' + l[l.length - 1] : l.join('')) + ' selected';
  h.hidden = GF.uni !== 'all' || document.body.classList.contains('pf-mode');
};
uniFill(); uniPick(GF.uni); uniHintPaint();
us.addEventListener('change', () => {
  GF.uni = us.value; saveGF(); uniHintPaint();
  if (GF.uni === OTH && PRO && window.hniMore) window.hniMore('oth');
  route();
});
function setPro(p) {
  const was = PRO, cur = us.value;
  PRO = !!p; uniFill();
  uniPick(PRO ? (was ? cur : UNI_WANT) : FREE_LIST);
  if (us._paint) us._paint(); uniHintPaint();
  if (us.value !== cur) route();
  if (PRO && window.hniMore) window.hniMore('oth');          // Others: every other NSE stock, for Plus / Prime
}
function setPrimeF(p) {
  const was = PRIMEF; PRIMEF = !!p;
  if (was !== PRIMEF && (location.hash || '') === '#/checklist') route();
}
(function () {
  const go = () => {
    const l = window.hniPlus || window.hniBuilder || window.hniAdmin; if (l) l.then(setPro).catch(() => setPro(false));
    const b = window.hniBuilder || window.hniAdmin; if (b) b.then(setPrimeF).catch(() => setPrimeF(false));
  };
  go(); window.addEventListener('hni-ready', go); window.addEventListener('hni-tier', go);
  window.addEventListener('hashchange', uniHintPaint);
})();
/* add the stocks of fundamentals_<tag>.json (+ its back / sparkline files) to this page; data loaded before
   without them is dropped and loads again with them on next use */
window.hniMore = async tag => {
  if (MORE.includes(tag)) return;
  let j = null; try { j = await jget('fundamentals_' + tag + '.json'); } catch (e) {}
  if (!j || !Array.isArray(j.stocks) || !j.stocks.length) return;
  MORE.push(tag);
  const add = j.stocks.filter(s => s && s.sym && !TODAY.has(s.sym));
  add.forEach(s => { D.stocks.push(s); TODAY.set(s.sym, s); });
  [...new Set(add.map(s => s.ind).filter(n => n && !SEC_COLOR[n]))].sort()
    .forEach(n => SEC_COLOR[n] = SEC_PAL[Object.keys(SEC_COLOR).length % SEC_PAL.length]);
  (j.lists || []).forEach(l => { if (!D.lists.includes(l)) D.lists.push(l); });
  const cur = us.value; uniFill();
  us.value = cur === 'all' && D.lists.includes(UNI_WANT) ? UNI_WANT : cur; GF.uni = us.value;
  if (us._paint) us._paint(); HDR_OK = false; lockHeader();   // a longer list name: measure the List chip again
  Object.keys(BACK_CACHE).forEach(k => delete BACK_CACHE[k]);
  HIST_P = null; THEN1 = null; THEN_BY = new Map(); ensureThen1.p = null;
  VH.p = null; VH.snaps = null; VH_PTS.clear(); computeScores.S = null;
  if (GF.back) setHistorical(true, GF.backP, true); else route();
};

/* day / night: same key as the screener, so both pages always match */
/* ---------- themed dropdown: hides the native <select>, keeps it as the source of truth ---------- */
const CHEV = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="M2 3.5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const FSEL_CROWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-label="Plus"><path d="M3 7.5l4.5 4.5L12 5l4.5 7L21 7.5 19 19H5z"/></svg>';
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
      `<button type="button" role="option" class="fsel-opt${i === sel.selectedIndex ? ' on' : ''}${o.dataset.prime ? ' prime' : ''}${o.dataset.lock ? ' locked' : ''}" data-i="${i}">${esc(o.text)}${o.dataset.lock ? FSEL_CROWN : ''}</button>`).join('');
  };
  paint();
  sel._paint = paint;                                      // lets code that sets sel.value refresh the button
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
    if (sel.options[+o.dataset.i].dataset.lock) { if (window.hniPlans) window.hniPlans(null, 'plus'); return; }   // Prime list, free account
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
function lockChipWidth(chip, sel) {                        // widest option decides the width, so choosing never moves anything
  if (!chip || !sel || !sel._paint) return;
  chip.style.width = '';
  const keep = sel.selectedIndex; let w = 0;
  for (let i = 0; i < sel.options.length; i++) { sel.selectedIndex = i; sel._paint(); w = Math.max(w, chip.offsetWidth); }
  sel.selectedIndex = keep; sel._paint();
  if (w) chip.style.width = Math.ceil(w) + 'px';
}
var HDR_OK = false;                                       // header chips measured while visible?
function lockHeader() {
  const h = $('#backChip');                                // Historical: same width on and off
  if (!h || !h.offsetWidth) { HDR_OK = false; return; }   // hidden (Portfolio's quiet header): a 0 width here shrank it to a dot
  lockChipWidth($('#uniChip'), $('#uniSel')); lockChipWidth($('#backPer'), $('#backSel'));
  h.style.width = ''; h.style.width = Math.ceil(h.offsetWidth) + 'px';
  HDR_OK = true;
}
/* Historical + date: far right of the tabs row on wide screens, back in the header (after a break) on phones */
const HG = $('#histGrp'), NAVR = $('#nav'), HSEP = $('#histSep'), MQ = matchMedia('(max-width:700px)');
function placeHist() {
  if (MQ.matches) { if (HG.previousElementSibling !== HSEP) HSEP.after(HG); }
  else if (HG.parentNode !== NAVR) NAVR.appendChild(HG);
  lockHeader();
}
placeHist();
if (MQ.addEventListener) MQ.addEventListener('change', placeHist); else MQ.addListener(placeHist);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(lockHeader);
new MutationObserver(() => view.querySelectorAll('select:not([data-fancy])').forEach(fancySelect))
  .observe(view, {childList:true, subtree:true});            // filter boxes re-render on every change, not only on route
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFsel(); });

$('#theme-toggle').addEventListener('click', () => {
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
/* page name under the HNImanshu wordmark (and the tab title): Portfolio on #/portfolio, Factor Screener everywhere else.
   Demo (review) login: "Paper portfolio" (auth.js window.hniDemo) */
function pfName() { return window.hniDemo ? 'Paper portfolio' : 'Portfolio'; }
(function () {
  const t = $('header .brand .tagline');
  const set = () => { const pf = /^#\/portfolio/.test(location.hash); if (t) t.textContent = pf ? pfName() : 'Factor Screener'; document.title = 'HNImanshu — ' + (pf ? pfName() : 'Factor Screener'); };
  set(); window.addEventListener('hashchange', set); window.addEventListener('hni-ready', set);
})();
/* Telegram alerts are part of Prime (like PF Builder): not Prime → the plans popup (auth.js window.hniPlans) */
$('#weeklyBtn').addEventListener('click', async () => {
  const w = window.hniBuilder || window.hniAdmin;
  const ok = w ? !!(await w.catch(() => false)) : false;
  if (!ok && window.hniPlans) return window.hniPlans();
  const wk = modal('Weekly updates', `<p>Work in progress. Soon you'll get your portfolio summary every week on Telegram.</p>`);
  if (window.hniFadeAway) window.hniFadeAway(wk, () => wk._close());   // closes itself after 5 s
});
window.addEventListener('hashchange', route);
if (isPhone() && !location.hash) history.replaceState(null, '', location.pathname + location.search + '#/value');   // phone: opens on Value screen
(() => {                                                  // this session's last Historical setting
  let hs = null; try { hs = JSON.parse(sessionStorage.getItem(HS_KEY) || 'null'); } catch (e) {}
  if (hs && (hs.on || (hs.key && hs.key !== GF.backP))) setHistorical(hs.on, hs.key, true); else route();
})();
</script>
<script>
/* offline cache (sw.js): pages open at once from the phone's cache while sw.js quietly downloads the fresh copy.
   No notice: when the page itself changed, it swaps to the fresh one silently the next time the tab goes to the
   background (switching apps / tabs), so you come back to new data at the same tab. Never while a popup or a
   half-typed box is open. Data files (.json: news, lists) need nothing: the fresh copy is already in the cache. */
if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
  addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  let stale = false, leaving = false;
  /* only THIS page's own file counts: the screener prefetches Fundamentals (it changes on every build), and that once
     marked the screener stale, so tapping Fundamentals reloaded the screener instead of opening Fundamentals */
  const pageKey = u => { const p = new URL(u, location.href).pathname.replace(/\.html$/, '').replace(/\/index$/, '/'); return p || '/'; };
  navigator.serviceWorker.addEventListener('message', e => {
    if (e.data && e.data.type === 'hni-updated' && pageKey(e.data.url || '/x.json') === pageKey(location.href)) stale = true;
  });
  addEventListener('pagehide', () => { leaving = true; });              // going to another page: never reload over that navigation
  addEventListener('pageshow', () => { leaving = false; });
  document.addEventListener('visibilitychange', () => {
    if (!stale || leaving || document.visibilityState !== 'hidden') return;
    const a = document.activeElement;
    const busy = document.querySelector('.au-back.open, .wip-back') || (a && /^(INPUT|TEXTAREA)$/.test(a.tagName) && a.value);
    if (!busy) location.reload();
  });
}
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# RENDER / BUILD
# ----------------------------------------------------------------------------
# Login gate (website only). auth.css / auth.js sit next to index.html in public/ and are shared with the screener.
# The inline rule hides the page even if auth.css is slow or blocked; auth.js adds html.auth-ok once a login is confirmed.
AUTH_WEB = {
    "__AUTH_ATTR__": ' data-auth="required"',
    "__AUTH_HEAD__": '<style>html[data-auth="required"]:not(.auth-ok) body>*:not(.au-wait):not(.au-back){filter:blur(8px);pointer-events:none}</style>\n'
                     # signed in and checked within the last hour → page shows at once; auth.js confirms quietly behind it
                     '<script>(function(){try{var d=document.documentElement,L=localStorage,p=L.getItem("hni_pro");if(d.dataset.page!=="home"&&L.getItem("hni_signed")==="1")d.classList.add("hni-nav");if(p==="1")d.classList.add("hni-pro");else if(p==="0"){d.classList.add("hni-free");if(L.getItem("hni_plus")==="1")d.classList.add("hni-plus")}if(L.getItem("hni_demo")==="1")d.classList.add("hni-demo");if(L.getItem("hni_signed")==="1"&&Date.now()-(+L.getItem("hni_auth_at")||0)<36e5)d.classList.add("auth-ok")}catch(e){}})()</script>\n'
                     '<link rel="stylesheet" href="auth.css?v=27">\n'          # ?v=: bump with every auth.js / auth.css change (sw.js cache)
                     '<script type="module" src="auth.js?v=27"></script>\n',
    "__AUTH_BODY__": '<div class="au-wait"><div class="au-chkbox" id="auWait" role="status"><i class="au-spin"></i><span>Checking login…</span></div></div>\n'
                     # safety net: if auth.js never answers (missing file, blocked script, offline), don't hang forever
                     '<script>setTimeout(function(){if(!document.documentElement.classList.contains("auth-ok")){'
                     'var w=document.getElementById("auWait");if(w)w.innerHTML=\'Login could not load. <a href="./">Back to home</a>\';}},10000);</script>\n',
}
AUTH_OFF = {k: "" for k in AUTH_WEB}


def slim(stocks):
    """What the page actually reads, nothing more: shareholding history → just the promoter change (Promoter Δ),
    no empty top-level fields."""
    out = []
    for r in stocks:
        r = {k: v for k, v in r.items() if v is not None}     # EPS series stays: the Value screen's EPS trend draws it
        h = r.get("h") or {}
        r["h"] = {"pro": {"chg": h["pro"].get("chg")}} if h.get("pro") and h["pro"].get("chg") is not None else {}
        out.append(r)
    return out


SAME_AS_TODAY = ("name", "url", "ind", "uni", "psu")      # back files: left out when equal to today's (the page fills them back in)


def _no_none(d):
    return {k: v for k, v in d.items() if v is not None} if isinstance(d, dict) else d


def slim_back(b, today=None):
    """A back snapshot for the website. With today ({SYMBOL: today's record}): fields that are the same as today's are
    left out (the page copies them back from today, fillBack), and empty values inside since / tr are dropped."""
    stocks = slim(b.get("stocks") or [])
    if today:
        for r in stocks:
            t = today.get(r.get("sym"))
            if t:
                for k in SAME_AS_TODAY:
                    if k in r and r[k] == t.get(k):
                        del r[k]
            if isinstance(r.get("since"), dict):
                r["since"] = _no_none(r["since"])
            if isinstance(r.get("tr"), dict):
                r["tr"] = dict(r["tr"], c=_no_none(r["tr"].get("c")), d=_no_none(r["tr"].get("d")))
    return dict(b, stocks=stocks)


# fundamentals_hist.json: every back date in ONE small file, only the fields the stock popup's sparkline (checklist
# at each date) and the "PE / PB / ROE a year ago" columns read. ~0.75 MB to download instead of 2.3 MB for the six
# full back files, which now load only when someone switches Historical on.
HIST_COLS = ["sym", "ind", "fin", "uni", "cmp", "pe", "pb", "roe", "roce", "cagr", "cc", "cfop", "dy", "fcfy", "de",
             "graham", "loss5", "lo5pct", "oi", "qoi", "icov", "shg", "tr", "mom", "pro3", "pro3y", "pyr", "qp", "sg5",
             "mcap", "ttm"]


def hist_payload(backs):
    def cell(r, c):
        v = r.get(c)
        if c == "tr":                                   # the checklist reads only pass / of and the promoter change
            return [v.get("pass"), v.get("of"), (v.get("d") or {}).get("proD")] if isinstance(v, dict) else None
        return v
    dates = {}
    for k, b in backs.items():
        dates[k] = {"date": b.get("date"), "hasPrice": b.get("hasPrice"),
                    "rows": [[cell(r, c) for c in HIST_COLS] for r in slim(b.get("stocks") or [])]}
    return {"cols": HIST_COLS, "dates": dates}


def render_html(now, backs, asof, web=False, smnow=None, embed_all=False):
    """Fill the HTML template with the computed records. The default back period is embedded; the others are
    listed in "backs" and (web) loaded on demand from fundamentals_back_<key>.json, or (embed_all) inlined."""
    pro = [r["h"]["pro"] for r in now if "pro" in r["h"]]
    span = pd.Series([p["since"] + " → " + p["till"] for p in pro]).mode()[0] if pro else "n/a"
    lists = [u for u in UNIVERSES.values() if any(u in r["uni"] for r in now)]
    lists += sorted({u for r in now for u in r["uni"]} - set(lists))
    then = slim_back(backs[DEFAULT_BACK])
    if web:                                       # website: the 1Y snapshot loads on demand (Industries 1Y columns, Historical)
        then = {k: v for k, v in then.items() if k != "stocks"}
        then["lazy"] = True
    same = bool(TODAY_SAME and smnow)
    payload = {
        "stocks": slim(smnow if same else now),           # today: same-method engine (TODAY_SAME) or Screener's own figures
        "sameToday": same,
        "lists": lists,
        "asOf": asof.strftime("%d %b %Y"),
        "asOfIso": asof.strftime("%Y-%m-%d"),
        "shSpan": span,
        "jumpPP": JUMP_PP,
        "cashYears": CASH_YEARS,
        "staleDays": MAX_STALE_DAYS,
        "then": then,
        "backs": [{"key": k, "label": b["label"], "date": b["date"], "hasPrice": b["hasPrice"]} for k, b in backs.items()],
        "backData": {k: slim_back(b) for k, b in backs.items() if k != DEFAULT_BACK} if embed_all else {},
        "smNow": {"stocks": []},                       # "Same method" removed from the page: not shipped any more
        "sm": {"trendQ": SM_TREND_QTRS, "shQ": SM_SH_QTRS},
    }
    data = json.dumps(clean(payload), allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    html = HTML.replace("__WEB__", "true" if web else "false")     # flag first, data can't contain it then
    for k, v in (AUTH_WEB if web else AUTH_OFF).items():            # login gate: website only, offline file stays open
        html = html.replace(k, v)
    return html.replace("__DATA__", data), span


def build_site(paths, out_path, minify=None, prices_path=None):
    """Called from build.py: read only `paths`, write the web version to `out_path`.
    prices.csv (fetch_prices.py) is picked up from the CSVs' folder automatically when present."""
    df, asof = load_stocks(paths)
    df = df.sort_values("PE", na_position="last")
    if prices_path is None and paths:
        prices_path = pathlib.Path(str(paths[0])).parent / PRICES_CSV
    prices, bench = load_prices(prices_path) if prices_path else ({}, None)
    now, smnow, backs = build_all(df, asof, prices, bench)
    # "Others" (every NSE stock outside the 3 lists) is NOT in the page or its usual files: it goes to its own files
    # (fundamentals_oth.json, fundamentals_back_<key>_oth.json, fundamentals_hist_oth.json), loaded by the page only
    # when the signed-in add-on asks for them. Its scores were already worked out above with the 3 lists as peers.
    oth = {r["sym"] for r in now if r.get("uni") and all(u == OTHERS_LABEL for u in r["uni"])}
    part = lambda rows, want: [r for r in (rows or []) if (r.get("sym") in oth) == want]
    now_all, smnow_all = now, smnow
    now, smnow = part(now_all, False), part(smnow_all, False)
    backs_oth = {k: dict(b, stocks=part(b.get("stocks"), True)) for k, b in backs.items()}
    backs = {k: dict(b, stocks=part(b.get("stocks"), False)) for k, b in backs.items()}
    html, span = render_html(now, backs, asof, web=True, smnow=smnow)
    if minify:
        html = minify(html)
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    extra = 0
    rows_all = smnow_all if (TODAY_SAME and smnow_all) else now_all
    today = {r["sym"]: r for r in rows_all}                # the page's TODAY (with Others once they are added)
    def put(name, obj):
        t = json.dumps(clean(obj), allow_nan=False, separators=(",", ":"))
        (out_path.parent / name).write_text(t, encoding="utf-8")
        return len(t.encode("utf-8"))
    for k in backs:                                       # every back period (1Y too): its own JSON, loaded only for Historical
        extra += put("fundamentals_back_%s.json" % k, slim_back(backs[k], today))
    hist_mb = put("fundamentals_hist.json", hist_payload(backs)) / 1e6   # sparkline + 1Y columns
    print("    fundamentals_hist.json %.2f MB (sparkline + 1Y columns)" % hist_mb)
    oth_today = [r for r in rows_all if r["sym"] in oth]
    o = put("fundamentals_%s.json" % OTH_TAG, {"stocks": slim(oth_today), "lists": [OTHERS_LABEL] if oth_today else []})
    for k in backs_oth:
        o += put("fundamentals_back_%s_%s.json" % (k, OTH_TAG), slim_back(backs_oth[k], today))
    o += put("fundamentals_hist_%s.json" % OTH_TAG, hist_payload(backs_oth))
    print("    %s: %d stocks in their own files, %.1f MB (loaded only on request)" % (OTHERS_LABEL, len(oth_today), o / 1e6))
    print("    Fundamentals: %d stocks, data as of %s, back periods %s, page %.1f MB + %.1f MB on demand -> %s" % (
        len(now), asof.strftime("%d %b %Y"), "/".join(b["label"] for b in backs.values()),
        len(html.encode("utf-8")) / 1e6, extra / 1e6, out_path))
    return len(now)


def main():
    """Local use: scan this folder, write dashboard.html + dated copy, open browser."""
    try:
        df, asof = load_stocks()
    except RuntimeError as e:
        sys.exit("%s in %s" % (e, HERE))
    df = df.sort_values("PE", na_position="last")
    write_csvs(df)
    prices, bench = load_prices(os.path.join(HERE, PRICES_CSV))
    now, smnow, backs = build_all(df, asof, prices, bench)
    html, span = render_html(now, backs, asof, web=False, smnow=smnow, embed_all=True)   # offline file: all periods inline
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
    for b in backs.values():
        print("%-5s back: fundamentals as of %s, profit growth since then for %d stocks" % (
            b["label"], b["date"], sum(1 for t in b["stocks"] if t["since"]["patG"] is not None)))
    print("Dashboard :", OUT_HTML)
    print("Saved copy:", archive)
    if OPEN_BROWSER:
        webbrowser.open(pathlib.Path(OUT_HTML).as_uri())


if __name__ == "__main__":
    main()