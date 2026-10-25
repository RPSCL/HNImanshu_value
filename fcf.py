"""
fcf.py — Buffett-style owner earnings / free cash flow value, from the yearly columns already in the valuation CSVs
=====================================================================================================================
Screener's cash flow table only gives three totals (operating, investing, financing), so capex is worked out from the
balance sheet, year by year:

    capex          = (net block this year − last year) + (CWIP this year − last year) + depreciation      (never below 0)
    maintenance    = the smaller of capex and depreciation      (what it costs just to keep the business as it is)
    owner earnings = cash from operations − maintenance capex   (Buffett, 1986 letter; working capital is already in CFO)
    free cash flow = cash from operations − all capex           (stricter: growth spending counts too)

Value per share (both, the same way):
    base     = average of the last 3 years (at least 2)                       — one lumpy year doesn't decide it
    growth g = the lower of the 5-year revenue and profit growth, 0 … 15 %    — conservative, like Buffett
    years 1–5 grow at g, years 6–10 at g / 2, then 4 % for ever; all discounted at 12 %
    equity   = that value − (borrowings − investments)                         — net debt off, net cash added
    per share= equity ÷ shares today

Not worked out for banks / NBFCs / insurers (cash flow means something else there), or when the base is ≤ 0.
Everything is "point in time": value_at(..., upto=FY) uses only years up to that one (the backtest relies on it).
"""
import math
import re

import numpy as np
import pandas as pd

DISCOUNT    = 0.12          # 12 %: Indian 10-year bond (~6.5 %) + equity premium
TERMINAL    = 0.04          # growth for ever after year 10
G_MAX       = 0.15          # growth used is capped at 15 %
G_DEFAULT   = 0.05          # when no growth history is there
AVG_YEARS   = 3             # base = average of the last 3 years …
MIN_YEARS   = 2             # … at least 2
MOS         = 0.25          # "buy below" = value × (1 − 25 %)
FIN_WORDS   = ("financial services", "bank", "nbfc", "insurance", "finance", "capital markets")
CYC_WORDS   = ("oil gas", "metals & mining", "mining", "realty")   # commodity / property cycles: 5-year average base, no growth

MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
_SUF = re.compile(r"_([A-Z]{3})(20\d\d)$")


def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def year_cols(columns, prefix):
    """{(year, month): column} for every dated column of a field, e.g. prefix 'C_CF_OPERATING_CR'."""
    out = {}
    for c in columns:
        if not c.startswith(prefix + "_"):
            continue
        m = _SUF.search(c)
        if m and m.group(1) in MONTHS and c[: m.start()] == prefix:
            out[(int(m.group(2)), MONTHS[m.group(1)])] = c
    return out


FIELDS = {"cfo": "C_CF_OPERATING_CR", "nb": "B_BS_NET_BLOCK_CR", "cwip": "B_BS_CWIP_CR", "dep": "A_PL_DEPR_CR",
          "rev": "A_PL_REVENUE_CR", "pat": "A_PL_PAT_CR", "debt": "B_BS_TOTAL_BORROWINGS_CR", "inv": "B_BS_INVESTMENTS_CR"}


def is_financial(row):
    s = " ".join(str(row.get(k) or "") for k in ("INDUSTRY", "SECTOR", "BASIC_INDUSTRY")).lower()
    return any(w in s for w in FIN_WORDS)


def is_cyclical(row):
    s = " ".join(str(row.get(k) or "") for k in ("INDUSTRY", "SECTOR", "BASIC_INDUSTRY")).lower()
    return any(w in s for w in CYC_WORDS)


def history(row, cols):
    """Year by year (oldest → newest): [{y, m, cfo, capex, dep, maint, oe, fcf, rev, pat, debt, inv}] — years with CFO only."""
    keys = sorted(set().union(*[set(cols[f]) for f in ("cfo",)]))
    rows = []
    for k in keys:
        v = {f: _num(row.get(cols[f].get(k))) if k in cols[f] else None for f in FIELDS}
        if v["cfo"] is None:
            continue
        rows.append({"y": k[0], "m": k[1], **v})
    for i, r in enumerate(rows):
        p = rows[i - 1] if i else None
        r["capex"] = r["maint"] = r["oe"] = r["fcf"] = None
        if p is None or r["nb"] is None or p["nb"] is None or r["dep"] is None:
            continue
        months = (r["y"] - p["y"]) * 12 + r["m"] - p["m"]
        if not 9 <= months <= 15:                                     # a gap in the years: no capex for this one
            continue
        cw = (r["cwip"] or 0) - (p["cwip"] or 0)
        capex = max(0.0, (r["nb"] - p["nb"]) + cw + r["dep"])
        r["capex"] = capex
        r["maint"] = min(capex, max(r["dep"], 0.0))
        r["oe"] = r["cfo"] - r["maint"]
        r["fcf"] = r["cfo"] - capex
    return rows


def _cagr(a, b, n):
    if a is None or b is None or a <= 0 or b <= 0 or n <= 0:
        return None
    return (b / a) ** (1 / n) - 1


def dcf_per_share(base, g, net_debt, shares):
    """10-year two-stage DCF of a yearly cash amount (₹ Cr) → ₹ per share."""
    if base is None or base <= 0 or not shares or shares <= 0:
        return None
    pv, cf = 0.0, base
    for t in range(1, 11):
        cf *= 1 + (g if t <= 5 else g / 2)
        pv += cf / (1 + DISCOUNT) ** t
    pv += cf * (1 + TERMINAL) / (DISCOUNT - TERMINAL) / (1 + DISCOUNT) ** 10
    eq = pv - (net_debt or 0)
    return eq / shares if eq > 0 else None


def value_at(hist, idx, shares, cyc=False):
    """Values using only years up to hist[idx] (point in time). → dict or None.
    cyc: commodity / property company → 5-year average base and no growth (only a normal year is paid for)."""
    upto = hist[: idx + 1]
    n_avg = 5 if cyc else AVG_YEARS
    oes = [r["oe"] for r in upto if r["oe"] is not None][-n_avg:]
    fcfs = [r["fcf"] for r in upto if r["fcf"] is not None][-n_avg:]
    if len(oes) < MIN_YEARS:
        return None
    cur = upto[-1]
    old = [r for r in upto if 0 < cur["y"] - r["y"] <= 5]
    o5 = old[0] if old else None
    n = (cur["y"] - o5["y"]) if o5 else 0
    gs = [x for x in (_cagr(o5 and o5["rev"], cur["rev"], n), _cagr(o5 and o5["pat"], cur["pat"], n)) if x is not None]
    g = min(gs) if gs else G_DEFAULT
    g = 0.0 if cyc else min(max(g, 0.0), G_MAX)
    nd = (cur["debt"] or 0) - (cur["inv"] or 0)
    base_oe, base_fcf = sum(oes) / len(oes), (sum(fcfs) / len(fcfs) if len(fcfs) >= MIN_YEARS else None)
    pos = [r["oe"] for r in upto if r["oe"] is not None][-5:]
    return {"y": cur["y"], "m": cur["m"], "g": g, "cyc": cyc, "nd": nd, "oe": base_oe, "fcf": base_fcf,
            "v_oe": dcf_per_share(base_oe, g, nd, shares), "v_fcf": dcf_per_share(base_fcf, g, nd, shares),
            "pos": sum(1 for x in pos if x > 0), "npos": len(pos)}


def _r(v, d=2):
    return None if v is None else round(float(v), d)


def add_columns(df):
    """Adds the owner-earnings columns to one list's DataFrame (in place) and returns it."""
    cols = {f: year_cols(df.columns, p) for f, p in FIELDS.items()}
    out = {k: [] for k in ("OE_VALUE", "FCF_VALUE", "OE_GAP_PCT", "FCF_GAP_PCT", "OE_BUY_BELOW", "OE_CR", "FCF_CR",
                           "OE_YIELD_PCT", "FCF_YLD_PCT", "OE_G_PCT", "OE_POS", "OE_NET_DEBT_CR", "OE_NOTE", "OE_HIST", "OE_FY", "OE_CYC")}
    for row in df.to_dict("records"):
        res = dict.fromkeys(out)
        sh, cmp_, mc = _num(row.get("SHARES_CR")), _num(row.get("CMP")), _num(row.get("MARKET_CAP_CR"))
        if not sh and mc and cmp_:
            sh = mc / cmp_
        if is_financial(row):
            res["OE_NOTE"] = "fin"
        else:
            h = history(row, cols)
            cyc = is_cyclical(row)
            res["OE_CYC"] = 1 if cyc else None
            v = value_at(h, len(h) - 1, sh, cyc) if h else None
            res["OE_HIST"] = [[f"{MONTHS_R[r['m']]}{r['y'] % 100:02d}", _r(r["cfo"], 0), _r(r["capex"], 0), _r(r["dep"], 0),
                               _r(r["oe"], 0), _r(r["fcf"], 0)] for r in h[-6:]] or None
            if not v:
                res["OE_NOTE"] = "short"
            else:
                res.update({"OE_CR": _r(v["oe"], 1), "FCF_CR": _r(v["fcf"], 1), "OE_G_PCT": _r(v["g"] * 100, 1),
                            "OE_POS": f"{v['pos']}/{v['npos']}", "OE_NET_DEBT_CR": _r(v["nd"], 0),
                            "OE_FY": f"{MONTHS_R[v['m']]} {v['y']}",
                            "OE_VALUE": _r(v["v_oe"]), "FCF_VALUE": _r(v["v_fcf"])})
                if mc and mc > 0:
                    res["OE_YIELD_PCT"] = _r(v["oe"] / mc * 100)
                    if v["fcf"] is not None:
                        res["FCF_YLD_PCT"] = _r(v["fcf"] / mc * 100)
                if cmp_ and cmp_ > 0:
                    if v["v_oe"]:
                        res["OE_GAP_PCT"] = _r((v["v_oe"] / cmp_ - 1) * 100, 1)
                        res["OE_BUY_BELOW"] = _r(v["v_oe"] * (1 - MOS))
                    if v["v_fcf"]:
                        res["FCF_GAP_PCT"] = _r((v["v_fcf"] / cmp_ - 1) * 100, 1)
                if v["v_oe"] is None:
                    res["OE_NOTE"] = "neg"
        for k in out:
            out[k].append(res[k])
    new = pd.DataFrame({k: pd.Series(v, index=df.index, dtype=object) for k, v in out.items()})
    return pd.concat([df.drop(columns=[c for c in out if c in df.columns]), new], axis=1)


MONTHS_R = {v: k.title() for k, v in MONTHS.items()}
OE_COLS = ["OE_VALUE", "FCF_VALUE", "OE_GAP_PCT", "FCF_GAP_PCT", "OE_BUY_BELOW", "OE_CR", "FCF_CR", "OE_YIELD_PCT",
           "FCF_YLD_PCT", "OE_G_PCT", "OE_POS", "OE_NET_DEBT_CR", "OE_NOTE", "OE_HIST", "OE_FY", "OE_CYC"]


# ─────────────────────────────────────────────────────────────────────────────
# Backtest data: the owner-earnings value of every stock at every 1 July since 2019 (point in time),
# with the split-adjusted price on that day (prices_yearly.csv, fetch_prices.py --yearly)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_M, ANCHOR_D = 7, 1           # 1 July: last year's annual results (to March) are all out by the end of May
LAG_DAYS = 75                        # a year's numbers count only if the year ended ≥ 75 days before the buy date


def backtest_rows(frames, prices):
    """frames: [(list_key, DataFrame)], prices: DataFrame of prices_yearly.csv → dict for public/oe_backtest.json."""
    import datetime as dt
    pcols = sorted(int(c[3:]) for c in prices.columns if re.fullmatch(r"PX_20\d\d", c))
    px = {str(r["SYMBOL"]).strip().upper(): r for r in prices.to_dict("records")}
    years = [y for y in pcols]
    stocks, seen = {}, {}
    for key, df in frames:
        cols = {f: year_cols(df.columns, p) for f, p in FIELDS.items()}
        for row in df.to_dict("records"):
            sym = str(row.get("SYMBOL") or "").strip().upper()
            if not sym:
                continue
            if sym in seen:                                            # same stock in two lists: one row, both flags
                if sym in stocks and key not in stocks[sym][1]:
                    stocks[sym][1].append(key)
                continue
            seen[sym] = 1
            p = px.get(sym)
            if p is None or is_financial(row):
                continue
            sh = _num(row.get("SHARES_CR"))
            h, cyc = history(row, cols), is_cyclical(row)
            vals = []
            for y in years:
                cut = dt.date(y, ANCHOR_M, ANCHOR_D) - dt.timedelta(days=LAG_DAYS)
                idx = max((i for i, r in enumerate(h) if dt.date(r["y"], r["m"], 28) <= cut), default=None)
                v = value_at(h, idx, sh, cyc) if idx is not None else None
                vals.append(_r(v["v_oe"]) if v and v["v_oe"] else None)
            pxs = [_r(_num(p.get(f"PX_{y}"))) for y in years]
            if not any(a and b for a, b in zip(vals, pxs)):
                continue
            stocks[sym] = [sym, [key], vals, pxs, _r(_num(p.get("LTP_ADJ"))), 1 if cyc else 0]
    b = px.get("^NIFTY500") or {}
    return {"years": years, "anchor": f"{ANCHOR_D} Jul", "asof": str(prices.get("LTP_DATE", pd.Series([""])).dropna().max() or ""),
            "bench": {"name": "NIFTY 500", "px": [_r(_num(b.get(f"PX_{y}"))) for y in years], "now": _r(_num(b.get("LTP_ADJ")))},
            "stocks": list(stocks.values())}
