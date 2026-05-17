#!/usr/bin/env python3
"""
build.py — HNImanshu Stock Screener Build Script
=================================================
Score Architecture:
  PIOTROSKI_SCORE  (0–9)     Fresh YoY-based 8-signal F-Score computed from
                              the two most recent annual columns for EACH ROW
                              independently (per-row fiscal-year resolution).
                              Missing signals count as 0 (not skipped),
                              preserving the original Piotroski intent.
  QUALITY_SCORE    (0–13.4)  Absolute-level quality addon (profitability /
                              growth / safety) with YoY direction bonuses.
  COMBINED_SCORE   (0–22.4)  Sum of both.

  FINAL_SCORE      (0–10)    MASTER = (VAL_NORM×0.40) + (PIOT_NORM×0.35) + (QUAL_NORM×0.25)
                              All three normalised to 0–10 before weighting.
  FINAL_RANK                 Rank by FINAL_SCORE descending.

F_SCORE from CSV is retained as a reference column only.

Key fix vs prior version:
  _resolve_prior_cols() previously found the two most-recent dated columns
  GLOBALLY across all stocks, then read those same two columns for every row.
  Since different companies have different fiscal year endings (MAR / DEC /
  SEP / JUN), most rows had NaN in those columns → nearly all signals failed
  → scores capped at 3.

  The fix: for EACH ROW, scan all dated columns for a metric and pick the
  two most-recent non-null values independently.  This is done once per
  metric via _build_cur_pri(), which returns two Series (cur, pri) where
  each cell is taken from that row's own most-recent fiscal column.

News:
  Reads multi_stock_news.csv (columns: stockname, datetime, news, link)
  and injects per-symbol news into the HTML as NEWS_DATA JS object.
  Headlines are hyperlinked to their source URLs.
"""

import pandas as pd
import numpy as np
import json
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime
import zoneinfo
import htmlmin
import rcssmin

# =========================
# 🗜 MINIFIER
# =========================

def minify_html_css(html: str) -> str:
    def minify_css(match):
        css = match.group(1)
        return "<style>" + rcssmin.cssmin(css) + "</style>"

    html = re.sub(r"<style>(.*?)</style>", minify_css, html, flags=re.DOTALL)
    html = htmlmin.minify(
        html,
        remove_comments=True,
        remove_empty_space=True,
        reduce_boolean_attributes=True
    )
    return html


# =========================
# 📁 PATHS
# =========================

BASE        = Path(__file__).parent
TEMPLATE    = BASE / "HNImanshu_template.html"
OUTPUT      = BASE / "public" / "index.html"

N500_CSV    = BASE / "nifty500_valuation.csv"
SC250_CSV   = BASE / "niftysmallcap500_valuation.csv"
MC250_CSV   = BASE / "niftymicrocap250_valuation.csv"
NEWS_CSV    = BASE / "multi_stock_news.csv"

PLACEHOLDER      = "%%DATASETS_PLACEHOLDER%%"
NEWS_PLACEHOLDER = "// ── NEWS DATA ──\nvar NEWS_DATA = {\n  '__default__': []\n};"

N500_NAME_COL  = "COMPANY_NAME_x"
SC250_NAME_COL = "COMPANY_NAME"
MC250_NAME_COL = "COMPANY_NAME"


# =========================
# 📊 OUTPUT COLS
# =========================

KEY_COLS = [
    "SYMBOL", "INDUSTRY", "CMP", "PE", "BOOK_VALUE",
    "ROE_PCT", "ROCE_PCT", "DIV_YIELD_PCT",
    "UPSIDE_PCT", "MARGIN_OF_SAFETY_PCT", "COMPOSITE_FAIR_VALUE",
    "VALUATION_SCORE", "VALUATION_SCORE_PCT", "VALUATION_RANK",
    "FINAL_SCORE", "FINAL_RANK", "GRADE", "FV_GRADE", "SCREENER_URL",
    "MARKET_CAP_CR", "PL_OPM_PCT", "NET_MARGIN_CALC_PCT",
    "REVENUE_CAGR_3YR_PCT", "PAT_CAGR_3YR_PCT",
    "SH_PROMOTER_PCT_LATEST", "NET_DEBT_CR", "INTEREST_COVERAGE",
    "CF_OPERATING_CR", "NET_DEBT_TO_EBITDA",
    "F_SCORE",           # 0–9    · CSV pre-computed Piotroski (reference only)
    "PIOTROSKI_SCORE",   # 0–9    · Fresh per-row F-Score (primary)
    "QUALITY_SCORE",     # 0–13.4 · Absolute-level quality addon
    "COMBINED_SCORE",    # 0–22.4 · PIOTROSKI + QUALITY
    "LOW_PROMOTER_FLAG",
]


# =========================
# 🔥 SCORING ENGINE
# =========================

def score_metric(value, thresholds, reverse=False, max_pts=1.4):
    """
    4-tier linear scoring engine.
    thresholds: [t1, t2, t3, t4] always in ASCENDING order.
    Normal  (reverse=False): higher = better.
    Reverse (reverse=True):  lower  = better.
    """
    if pd.isna(value):
        return 0
    pts = [1.1, 1.2, 1.3, max_pts]
    if reverse:
        if   value < thresholds[0]: return pts[3]
        elif value < thresholds[1]: return pts[2]
        elif value < thresholds[2]: return pts[1]
        elif value < thresholds[3]: return pts[0]
        else: return 0
    else:
        if   value > thresholds[3]: return pts[3]
        elif value > thresholds[2]: return pts[2]
        elif value > thresholds[1]: return pts[1]
        elif value > thresholds[0]: return pts[0]
        else: return 0


# =========================
# 🔍 PER-ROW FISCAL RESOLVER  ← THE CORE FIX
# =========================

# Month ordering used for sorting dated column keys
_MONTH_ORDER = {"MAR": 3, "JUN": 6, "SEP": 9, "DEC": 12}

def _dated_col_sort_key(col: str, prefix: str) -> int:
    """
    Given a column name like 'A_PL_PAT_CR_MAR2025', return an integer
    sort key (YYYY*100 + MM) so that more-recent columns sort higher.
    Returns -1 if the column doesn't match the expected pattern.
    """
    suffix = col[len(prefix) + 1:]   # e.g. "MAR2025"
    m = re.match(r'^(MAR|DEC|SEP|JUN)(\d{4})$', suffix)
    if not m:
        return -1
    month = _MONTH_ORDER.get(m.group(1), 0)
    year  = int(m.group(2))
    return year * 100 + month


def _build_cur_pri(df: pd.DataFrame, prefix: str):
    """
    Per-row resolution of current-year and prior-year values for a metric.

    Instead of picking two global columns and reading them for all rows
    (which breaks when companies have different fiscal year endings), this
    function builds two output Series — cur and pri — where each cell is
    the most-recent (or second-most-recent) non-null value for THAT ROW
    across all dated columns for the given metric prefix.

    Steps:
    1. Find all columns matching <prefix>_(MAR|DEC|SEP|JUN)<YYYY>.
       Filter out non-standard suffixes like MAR202315M, MAR20169M etc.
    2. Sort them newest → oldest by date key.
    3. For each row, iterate through sorted columns and pick:
         cur = first non-null value found
         pri = second non-null value found

    This handles mixed fiscal years (MAR, DEC, SEP, JUN) correctly because
    each row independently finds its own two most-recent data points.

    Parameters
    ----------
    df      : the full DataFrame
    prefix  : e.g. 'A_PL_PAT_CR', 'B_BS_TOTAL_ASSETS_CR'

    Returns
    -------
    cur, pri : pd.Series (float), aligned to df.index
    """
    # Match ONLY clean <MONTH><4-digit-YEAR> suffixes — exclude 15M, 9M etc.
    pat = re.compile(
        rf'^{re.escape(prefix)}_(MAR|DEC|SEP|JUN)(\d{{4}})$'
    )
    candidate_cols = [c for c in df.columns if pat.match(c)]

    if not candidate_cols:
        nan_series = pd.Series(np.nan, index=df.index)
        return nan_series, nan_series

    # Sort newest first
    candidate_cols.sort(
        key=lambda c: _dated_col_sort_key(c, prefix),
        reverse=True
    )

    # Pre-convert all candidate columns to float numpy arrays for speed
    arrays = [
        pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
        for c in candidate_cols
    ]

    n = len(df)
    cur_arr = np.full(n, np.nan)
    pri_arr = np.full(n, np.nan)

    # found[i]: 0 = nothing yet, 1 = cur filled, 2 = both filled
    found = np.zeros(n, dtype=int)

    for arr in arrays:
        has_val = ~np.isnan(arr)
        # Snapshot BEFORE this column so a single column can't fill both cur
        # and pri for the same row in the same iteration.
        found_before = found.copy()

        fill_cur = (found_before == 0) & has_val
        cur_arr[fill_cur] = arr[fill_cur]
        found[fill_cur] = 1

        fill_pri = (found_before == 1) & has_val
        pri_arr[fill_pri] = arr[fill_pri]
        found[fill_pri] = 2

        # Early exit when every row has both values
        if (found >= 2).all():
            break

    return (
        pd.Series(cur_arr, index=df.index),
        pd.Series(pri_arr, index=df.index),
    )


# =========================
# 🧠 PIOTROSKI F-SCORE  (0–9)
# =========================

def calc_piotroski_fscore(df: pd.DataFrame) -> pd.Series:
    """
    Piotroski F-Score (0–9), 8-signal variant.

    Design rules:
    ─────────────
    1. All values come from _build_cur_pri (per-row fiscal-year resolution).
       Each row independently finds its own two most-recent annual values.
       Flat columns like PL_PAT_CR are NOT used — they don't exist in our CSVs.

    2. Missing data → signal = 0 (FAIL), NOT skipped.
       This is the original Piotroski intent: a company that doesn't report
       a metric has not proven that metric is healthy.

    3. NO proportional rescaling. Raw integer sum 0–8 is scaled linearly
       to 0–9 by multiplying by 9/8.

    4. The b() helper converts NaN comparisons to 0 (not NaN), ensuring
       missing operands count as failures rather than being silently ignored.

    Signals (8 total):
      f1  ROA > 0                      (profitability)
      f2  CFO > 0                      (cash generation)
      f3  ROA improved YoY             (profitability trend)
      f4  CFO > 80% of PAT             (earnings quality / accruals)
      f5  Leverage (Net Debt / TA) fell YoY  (debt reduction)
      f6  Equity ratio (Reserves / TA) rose YoY  (equity expansion)
      f8  Operating margin improved YoY      (efficiency)
      f9  Asset turnover improved YoY        (efficiency)
    """

    def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / b.replace(0, np.nan)

    # ── Pull per-row cur & pri from dated column families ──────────────────
    print("    [PIOT] resolving per-row annual columns...")
    pat_c, pat_p = _build_cur_pri(df, "A_PL_PAT_CR")
    ta_c,  ta_p  = _build_cur_pri(df, "B_BS_TOTAL_ASSETS_CR")
    cfo_c, _     = _build_cur_pri(df, "C_CF_OPERATING_CR")
    opm_c, opm_p = _build_cur_pri(df, "A_PL_OPM_PCT")
    rev_c, rev_p = _build_cur_pri(df, "A_PL_REVENUE_CR")
    res_c, res_p = _build_cur_pri(df, "B_BS_RESERVES_CR")

    # Net debt: flat summary column (already computed), fallback to interest series
    if "NET_DEBT_CR" in df.columns:
        nd_c = pd.to_numeric(df["NET_DEBT_CR"], errors="coerce")
        # For prior-year net debt we approximate with prior-year interest expense
        _, nd_p = _build_cur_pri(df, "A_PL_INTEREST_CR")
    else:
        nd_c, nd_p = _build_cur_pri(df, "A_PL_INTEREST_CR")

    # Debug: null-rate summary
    print("    [PIOT resolver] non-null counts per family:")
    n = len(df)
    for name, cur_s, pri_s in [
        ("pat",  pat_c, pat_p), ("ta",   ta_c,  ta_p),
        ("cfo",  cfo_c, cfo_c), ("opm",  opm_c, opm_p),
        ("rev",  rev_c, rev_p), ("res",  res_c, res_p),
        ("nd",   nd_c,  nd_p),
    ]:
        print(f"      {name:6s}  cur={cur_s.notna().sum()}/{n}  pri={pri_s.notna().sum()}/{n}")

    # ── Derived ratios ────────────────────────────────────────────────────
    roa_c = safe_div(pat_c, ta_c)
    roa_p = safe_div(pat_p, ta_p)

    lev_c = safe_div(nd_c, ta_c)
    lev_p = safe_div(nd_p, ta_p)

    eq_c  = safe_div(res_c, ta_c)
    eq_p  = safe_div(res_p, ta_p)

    at_c  = safe_div(rev_c, ta_c)
    at_p  = safe_div(rev_p, ta_p)

    # ── Signal helper ─────────────────────────────────────────────────────
    def b(cond: pd.Series) -> pd.Series:
        """Convert boolean Series to float 0/1. NaN comparison → False → 0."""
        return cond.fillna(False).astype(float)

    # ── 8 signals ─────────────────────────────────────────────────────────
    signals = pd.DataFrame({
        # Profitability
        "f1": b(roa_c > 0),
        "f2": b(cfo_c > 0),
        "f3": b(roa_c > roa_p),
        "f4": b(cfo_c > pat_c * 0.8),

        # Leverage / Stability
        "f5": b(lev_c < lev_p),
        "f6": b(eq_c  > eq_p),

        # Efficiency
        "f8": b(opm_c > opm_p),
        "f9": b(at_c  > at_p),
    })

    # ── Score: raw sum 0–8, scaled linearly to 0–9 ───────────────────────
    raw   = signals.sum(axis=1)                   # 0–8
    score = (raw * 9 / 8).clip(0, 9).round(0)    # 0–9

    return score.astype(int)


# =========================
# 🏆 QUALITY SCORE  (0–13.4)
# =========================

def calc_quality_score(df: pd.DataFrame) -> pd.Series:
    """
    9-factor absolute-level quality score with YoY direction bonuses/penalties.
    Max theoretical ≈ 13.4
    """
    def calc_row(x):
        score = 0.0

        score += score_metric(x.get("ROE_PCT"),               [5, 10, 15, 20])
        score += score_metric(x.get("ROCE_PCT"),              [5, 10, 15, 20])
        score += score_metric(x.get("NET_MARGIN_CALC_PCT"),   [2,  5, 10, 15])
        score += score_metric(x.get("PL_OPM_PCT"),            [5, 10, 15, 20])
        score += score_metric(x.get("REVENUE_CAGR_3YR_PCT"),  [3,  5, 10, 15])
        score += score_metric(x.get("PAT_CAGR_3YR_PCT"),      [3,  5, 10, 15])
        score += score_metric(x.get("NET_DEBT_TO_EBITDA"),    [0, 1, 2, 3], reverse=True)
        score += score_metric(x.get("INTEREST_COVERAGE"),     [1, 2, 4, 8])
        score += score_metric(x.get("SH_PROMOTER_PCT_LATEST"),[30, 40, 50, 60], max_pts=1.0)

        pat_yoy = x.get("PAT_GROWTH_YOY_PCT")
        if pd.notna(pat_yoy):
            if   pat_yoy >   0: score += 0.2
            elif pat_yoy < -10: score -= 0.3

        rev_yoy = x.get("REVENUE_GROWTH_YOY_PCT")
        if pd.notna(rev_yoy):
            if   rev_yoy >  0: score += 0.1
            elif rev_yoy < -5: score -= 0.2

        roe     = x.get("ROE_PCT")
        roe_avg = x.get("ROE_AVG_3YR_PCT")
        if pd.notna(roe) and pd.notna(roe_avg) and roe_avg > 0:
            if   roe > roe_avg * 1.05: score += 0.2
            elif roe < roe_avg * 0.90: score -= 0.2

        cfo = x.get("CF_OPERATING_CR")
        if pd.notna(cfo):
            if   cfo > 0: score += 0.1
            elif cfo < 0: score -= 0.2

        return round(max(0.0, score), 2)

    return df.apply(calc_row, axis=1)


# =========================
# ⭐ MASTER FINAL SCORE  (0–10)
# =========================

def calc_final_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    MASTER = (VAL_NORM × 0.40) + (PIOT_NORM × 0.35) + (QUAL_NORM × 0.25)

    VAL_NORM   — MOS% normalised: -50 → 0,  +100 → 10
    PIOT_NORM  — Piotroski 0–9  → 0–10
    QUAL_NORM  — Quality 0–13.4 → 0–10

    FINAL_SCORE: 0–10   FINAL_RANK: 1 = best

    Grades (on FINAL_SCORE 0–10 scale):
      A ≥7.5  B+ ≥6.5  B ≥5.5  C ≥4.0  D ≥2.5  E <2.5
    """
    df = df.copy()

    mos = pd.to_numeric(
        df.get("MARGIN_OF_SAFETY_PCT", pd.Series(0, index=df.index)),
        errors="coerce"
    ).fillna(0).clip(-50, 100)

    val_norm  = ((mos + 50) / 150 * 10).clip(0, 10)
    piot_norm = (df["PIOTROSKI_SCORE"] / 9 * 10).clip(0, 10)
    qual_norm = (df["QUALITY_SCORE"] / 13.4 * 10).clip(0, 10)

    df["FINAL_SCORE"] = (
        val_norm  * 4.0 +
        piot_norm * 3.5 +
        qual_norm * 2.5
    ).round(2)

    df["FINAL_RANK"] = (
        df["FINAL_SCORE"]
        .rank(ascending=False, method="min", na_option="bottom")
        .astype(int)
    )

    def grade(s):
        if pd.isna(s): return "E"
        if s >= 7.5:  return "A"
        if s >= 6.5:  return "B+"
        if s >= 5.5:  return "B"
        if s >= 4.0:  return "C"
        if s >= 2.5:  return "D"
        return "E"

    df["GRADE"] = df["FINAL_SCORE"].apply(grade)
    return df


# =========================
# 📰 NEWS LOADER
# =========================

def _extract_source(url: str) -> str:
    """Extract a short source name from a Google News or direct URL."""
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.replace("www.", "")
        if host == "news.google.com":
            return "Google News"
        return host.split(".")[0].capitalize()
    except Exception:
        return ""


def load_news(path: Path) -> dict:
    """
    Load multi_stock_news.csv and return a dict:
      { "SYMBOL": [ {headline, time, link, source}, ... ], ... }

    CSV columns expected: stockname, datetime, news, link
    News items are sorted newest-first per symbol.
    """
    if not path.exists():
        print(f"  [NEWS]  {path.name} not found — skipping news injection")
        return {}

    print(f"  [NEWS]  {path.name}")
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as e:
        print(f"    ERROR reading news CSV: {e}")
        return {}

    df.columns = [c.strip() for c in df.columns]
    required = {"stockname", "datetime", "news", "link"}
    missing = required - set(df.columns)
    if missing:
        print(f"    ERROR: missing columns in news CSV: {missing}")
        return {}

    news_map = {}
    for _, row in df.iterrows():
        sym = str(row["stockname"]).strip().upper()
        if not sym:
            continue

        headline = str(row["news"]).strip()
        link     = str(row["link"]).strip()
        dt_raw   = str(row["datetime"]).strip()
        source   = _extract_source(link)

        time_str = dt_raw
        try:
            dt_clean = dt_raw.replace(" IST", "").strip()
            dt_obj   = datetime.strptime(dt_clean, "%Y-%m-%d %H:%M")
            time_str = dt_obj.strftime("%-d %b %Y, %I:%M %p")
        except Exception:
            pass

        item = {
            "headline": headline,
            "time":     time_str,
            "link":     link,
            "source":   source,
        }
        news_map.setdefault(sym, []).append(item)

    for sym in news_map:
        try:
            news_map[sym].sort(
                key=lambda x: datetime.strptime(
                    df.loc[df["news"] == x["headline"], "datetime"]
                    .iloc[0].replace(" IST", "").strip(),
                    "%Y-%m-%d %H:%M"
                ),
                reverse=True
            )
        except Exception:
            pass

    total_items = sum(len(v) for v in news_map.values())
    print(f"    Loaded: {total_items} news items across {len(news_map)} symbols")
    return news_map


def build_news_js(news_map: dict) -> str:
    """
    Render the NEWS_DATA JS block.
    Headlines are stored with their link so the template renders them as <a> tags.
    """
    if not news_map:
        return "// ── NEWS DATA ──\nvar NEWS_DATA = {\n  '__default__': []\n};"

    lines = ["// ── NEWS DATA ──", "var NEWS_DATA = {", "  '__default__': []"]
    for sym, items in sorted(news_map.items()):
        safe_items = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        lines.append(f",{json.dumps(sym)}:{safe_items}")
    lines.append("};")
    return "\n".join(lines)


# =========================
# 📂 CSV LOADER
# =========================

def load_csv(path: Path, name_col: str, label: str, optional=False):
    print(f"  [{label}]  {path.name}")

    if not path.exists():
        if optional:
            print("    SKIPPED (file not found)")
            return None
        print(f"ERROR: File not found -> {path}")
        sys.exit(1)

    df = pd.read_csv(path, low_memory=False)
    print(f"    Loaded: {len(df)} rows × {len(df.columns)} cols")

    if name_col in df.columns:
        df = df.rename(columns={name_col: "COMPANY_NAME"})
    elif "COMPANY_NAME" not in df.columns:
        print(df.columns.tolist())
        print("ERROR: Company name column missing")
        sys.exit(1)

    df = df.copy()

    # ── Deduplicate symbols within this index ──
    if "SYMBOL" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["SYMBOL"], keep="first")
        dupes = before - len(df)
        if dupes:
            print(f"    Dropped {dupes} duplicate symbols")

    # ── Derived: NET_DEBT_TO_EBITDA ──
    if "NET_DEBT_CR" in df.columns and "PL_EBITDA_CR" in df.columns:
        ebitda   = pd.to_numeric(df["PL_EBITDA_CR"], errors="coerce").replace(0, np.nan)
        net_debt = pd.to_numeric(df["NET_DEBT_CR"],  errors="coerce")
        df["NET_DEBT_TO_EBITDA"] = net_debt / ebitda
    else:
        df["NET_DEBT_TO_EBITDA"] = np.nan

    # ── Derived: LOW_PROMOTER_FLAG ──
    if "SH_PROMOTER_PCT_LATEST" in df.columns:
        df["LOW_PROMOTER_FLAG"] = (
            pd.to_numeric(df["SH_PROMOTER_PCT_LATEST"], errors="coerce") < 25
        )
    else:
        df["LOW_PROMOTER_FLAG"] = False

    # ── Compute all scores ───────────────────────────────────────────────
    df["PIOTROSKI_SCORE"] = calc_piotroski_fscore(df)   # 0–9 integer
    df["QUALITY_SCORE"]   = calc_quality_score(df)       # 0–13.4
    df["COMBINED_SCORE"]  = (df["PIOTROSKI_SCORE"] + df["QUALITY_SCORE"]).round(2)
    df = calc_final_score(df)

    # ── Debug stats ──────────────────────────────────────────────────────
    print(f"\n    {'Column':<22} {'Min':>6} {'Max':>6} {'Avg':>6}")
    for col in ["PIOTROSKI_SCORE", "QUALITY_SCORE", "COMBINED_SCORE", "FINAL_SCORE"]:
        s = df[col].dropna()
        print(f"    {col:<22} {s.min():>6.2f} {s.max():>6.2f} {s.mean():>6.2f}")

    # Piotroski distribution (key sanity check)
    dist = df["PIOTROSKI_SCORE"].value_counts().sort_index()
    print(f"\n    PIOTROSKI_SCORE distribution:")
    for val, cnt in dist.items():
        bar = "█" * int(cnt / max(dist) * 30)
        print(f"      {val:>2}  {bar}  ({cnt})")

    top5 = df.nsmallest(5, "FINAL_RANK")[
        ["COMPANY_NAME", "FINAL_RANK", "FINAL_SCORE", "PIOTROSKI_SCORE", "GRADE"]
    ]
    print(f"\n    Top 5:\n{top5.to_string(index=False)}\n")

    # ── Trim to output columns ───────────────────────────────────────────
    needed = ["COMPANY_NAME"] + KEY_COLS
    df = df[[c for c in needed if c in df.columns]]

    df = df.replace([np.inf, -np.inf], None)
    df = df.where(pd.notnull(df), None)

    for col in ["PIOTROSKI_SCORE", "QUALITY_SCORE", "COMBINED_SCORE", "FINAL_SCORE"]:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: round(float(v), 2) if v is not None else None
            )

    return df.to_dict(orient="records")


# =========================
# 📰 NEWS INJECTOR
# =========================

def inject_news(html: str, news_js: str) -> str:
    """Inject NEWS_DATA via regex — immune to whitespace/encoding mismatches."""
    pattern = re.compile(
        r'//\s*[─\-─]+\s*NEWS DATA\s*[─\-─]+.*?var\s+NEWS_DATA\s*=\s*\{.*?\};',
        re.DOTALL
    )
    if pattern.search(html):
        return pattern.sub(news_js, html)
    print("  [WARN] NEWS_DATA block not found via regex — appending before </script>")
    return html.replace('</script>', news_js + '\n</script>', 1)


# =========================
# 🏗 BUILD
# =========================

def build(deploy=False):
    print(f"\n{'='*52}")
    print("  HNImanshu — Build")
    print(f"{'='*52}\n")

    if not TEMPLATE.exists():
        print("ERROR: Template missing —", TEMPLATE)
        sys.exit(1)

    template = TEMPLATE.read_text(encoding="utf-8")

    print("Loading CSVs...\n")

    data_n500  = load_csv(N500_CSV,  N500_NAME_COL,  "Nifty 500")
    data_sc250 = load_csv(SC250_CSV, SC250_NAME_COL, "Smallcap 250", optional=True)
    data_mc250 = load_csv(MC250_CSV, MC250_NAME_COL, "Microcap 250", optional=True)

    if data_sc250 is None:
        data_sc250 = []
    if data_mc250 is None:
        data_mc250 = []

    print("\nLoading News...\n")
    news_map = load_news(NEWS_CSV)
    news_js  = build_news_js(news_map)

    # ── Build DATASETS JS ──
    datasets_js = (
        "const DATASETS = {\n"
        f"n500:{json.dumps(data_n500, separators=(',', ':'))},\n"
        f"sc250:{json.dumps(data_sc250, separators=(',', ':'))},\n"
        f"mc250:{json.dumps(data_mc250, separators=(',', ':'))}\n"
        "};"
    )

    # ── Inject into template ──
    html = template.replace(PLACEHOLDER, datasets_js)
    html = html.replace(NEWS_PLACEHOLDER, news_js)
    html = inject_news(html, news_js)

    # ── Timestamp ──
    now = datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y\n%I:%M %p IST")
    html = html.replace("%%LAST_UPDATED_PLACEHOLDER%%", now)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    html = minify_html_css(html)
    OUTPUT.write_text(html, encoding="utf-8")

    total = len(data_n500 or []) + len(data_sc250) + len(data_mc250)
    print(f"\n✅ Build Complete  —  {total} total stocks, "
          f"{sum(len(v) for v in news_map.values())} news items")
    print(f"   Output: {OUTPUT}")

    if deploy:
        import subprocess
        subprocess.run(["firebase", "deploy"])


# =========================
# ▶ RUN
# =========================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HNImanshu Stock Screener Build")
    ap.add_argument("--deploy", action="store_true", help="Deploy to Firebase after build")
    args = ap.parse_args()
    build(args.deploy)
