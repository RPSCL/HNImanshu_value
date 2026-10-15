#!/usr/bin/env python3
"""Weekly composite momentum of the ETF pair ratios (Composite Dual Momentum).

Inputs (daily history, any of these column names is accepted):
    niftyit.csv   goldbees.csv   alpha.csv        date column: Date/date/timestamp/time
                                                  price column: Adj Close/Close/close/price/ltp
Ratios scored:
    NIFTYIT / GOLDBEES        (builder pair ITBEES vs GOLDBEES)
    ALPHA   / GOLDBEES        (builder pair ALPHA  vs GOLDBEES)

For every week (last trading day, W-FRI):
    R6M  = ratio / ratio 26 weeks ago - 1
    R12M = ratio / ratio 52 weeks ago - 1
    SCORE = 0.5 * R6M + 0.5 * R12M                      (composite momentum, in %)
    relative leg : SCORE of the ratio > 0  -> challenger is beating GOLDBEES
    absolute leg : challenger's own composite score > 0
    SIGNAL = challenger if both legs pass, else GOLDBEES

Writes etf_weekly_momentum.csv (long format) and public/etf_weekly_momentum.json.
Usage:  python etf_weekly_momentum.py [--dir DATA_DIR] [--out OUT_DIR]
"""
import argparse, json
from pathlib import Path
import pandas as pd

W6, W12 = 0.5, 0.5
WK6, WK12 = 26, 52
DATE_COLS = ["date", "timestamp", "time", "datetime"]
PX_COLS = ["adj close", "adj_close", "adjclose", "close", "close price", "price", "ltp"]


def load(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    low = {c.strip().lower(): c for c in df.columns}
    dc = next((low[c] for c in DATE_COLS if c in low), df.columns[0])
    pc = next((low[c] for c in PX_COLS if c in low), None)
    if pc is None:
        raise SystemExit(f"{path.name}: no price column found (have {list(df.columns)})")
    s = pd.Series(pd.to_numeric(df[pc].astype(str).str.replace(",", ""), errors="coerce").values,
                  index=pd.to_datetime(df[dc], errors="coerce", dayfirst=True))
    return s[s.index.notna()].dropna().sort_index().groupby(level=0).last()


def weekly(s: pd.Series) -> pd.Series:
    return s.resample("W-FRI").last().dropna()


def composite(s: pd.Series) -> pd.DataFrame:
    r6 = (s / s.shift(WK6) - 1) * 100
    r12 = (s / s.shift(WK12) - 1) * 100
    return pd.DataFrame({"r6m": r6, "r12m": r12, "score": W6 * r6 + W12 * r12})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="folder holding niftyit.csv, goldbees.csv, alpha.csv")
    ap.add_argument("--out", default=".", help="folder for etf_weekly_momentum.csv and public/…json")
    a = ap.parse_args()
    d, out = Path(a.dir), Path(a.out)
    px = {n: load(d / f"{n}.csv") for n in ("niftyit", "goldbees", "alpha")}
    gold = px["goldbees"]
    rows = []
    for ch_name, ch_label in (("niftyit", "NIFTYIT"), ("alpha", "ALPHA")):
        ch = px[ch_name]
        both = pd.concat([ch, gold], axis=1, join="inner").dropna()          # same trading days only
        both.columns = ["ch", "gold"]
        wk = both.resample("W-FRI").last().dropna()
        ratio = wk["ch"] / wk["gold"]
        rc, ac = composite(ratio), composite(wk["ch"])
        for dt in wk.index:
            if pd.isna(rc.at[dt, "score"]):
                continue
            rel = rc.at[dt, "score"] > 0
            ab = pd.notna(ac.at[dt, "score"]) and ac.at[dt, "score"] > 0
            rows.append({"week": dt.strftime("%Y-%m-%d"), "pair": f"{ch_label}/GOLDBEES",
                         "ratio": round(ratio[dt], 6),
                         "r6m_pct": round(rc.at[dt, "r6m"], 2), "r12m_pct": round(rc.at[dt, "r12m"], 2),
                         "composite_score": round(rc.at[dt, "score"], 2),
                         "challenger_score": None if pd.isna(ac.at[dt, "score"]) else round(ac.at[dt, "score"], 2),
                         "signal": ch_label if (rel and ab) else "GOLDBEES"})
    df = pd.DataFrame(rows).sort_values(["pair", "week"])
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "etf_weekly_momentum.csv", index=False)
    (out / "public").mkdir(exist_ok=True)
    js = {p: g.drop(columns="pair").to_dict("records") for p, g in df.groupby("pair")}
    (out / "public" / "etf_weekly_momentum.json").write_text(json.dumps(js, separators=(",", ":")), encoding="utf-8")
    print(f"{len(df)} weekly rows -> etf_weekly_momentum.csv, public/etf_weekly_momentum.json")
    print(df.groupby("pair").tail(1).to_string(index=False))


if __name__ == "__main__":
    main()
