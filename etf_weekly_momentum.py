#!/usr/bin/env python3
"""Weekly composite momentum of the ETF pair ratios (Composite Dual Momentum).
The builder reads public/etf_weekly_momentum.json (weekly closes, scores and signals) for its weekly backtest.

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


def find(d: Path, name: str):
    """niftyit / goldbees / alpha .csv: any capitalisation, any subfolder, spaces/underscores ignored"""
    key = lambda s: s.lower().replace(" ", "").replace("_", "").replace("-", "")
    hits = [f for f in d.rglob("*") if f.suffix.lower() == ".csv" and key(f.stem) == name and ".git" not in f.parts]
    return sorted(hits, key=lambda f: len(f.parts))[0] if hits else None


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


def clean(s):
    return [None if pd.isna(v) else round(float(v), 4) for v in s]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="folder holding niftyit.csv, goldbees.csv, alpha.csv")
    ap.add_argument("--out", default=".", help="folder for etf_weekly_momentum.csv and public/…json")
    a = ap.parse_args()
    d, out = Path(a.dir), Path(a.out)
    files = {n: find(d, n) for n in ("niftyit", "goldbees", "alpha")}
    missing = [n + ".csv" for n, f in files.items() if f is None]
    if missing:                       # do not break the ETF pipeline / deploy: just skip this step
        print(f"[SKIP] weekly momentum not computed, missing in {d.resolve()}: {', '.join(missing)}")
        return
    print("Using:", ", ".join(str(f) for f in files.values()))
    df = pd.concat([load(files["niftyit"]), load(files["alpha"]), load(files["goldbees"])], axis=1, join="inner").dropna()
    df.columns = ["ITBEES", "ALPHA", "GOLDBEES"]                      # same trading days only
    wk = df.assign(_d=df.index).groupby(pd.Grouper(freq="W-FRI")).last().dropna()
    wk.index = pd.DatetimeIndex(wk.pop("_d"))                          # label each week by its last trading day
    weeks = [x.strftime("%Y-%m-%d") for x in wk.index]
    own = {s: composite(wk[s]) for s in ("ITBEES", "ALPHA", "GOLDBEES")}
    rows, pairs = [], {}
    for ch in ("ITBEES", "ALPHA"):
        ratio = wk[ch] / wk["GOLDBEES"]
        rc = composite(ratio)
        sig = []
        for dt in wk.index:
            sc, oc = rc.at[dt, "score"], own[ch].at[dt, "score"]
            s = None if pd.isna(sc) else (ch if (sc > 0 and pd.notna(oc) and oc > 0) else "GOLDBEES")
            sig.append(s)
            if s:
                rows.append({"week": dt.strftime("%Y-%m-%d"), "pair": f"{ch}/GOLDBEES", "ratio": round(ratio[dt], 6),
                             "r6m_pct": round(rc.at[dt, "r6m"], 2), "r12m_pct": round(rc.at[dt, "r12m"], 2),
                             "composite_score": round(sc, 2),
                             "challenger_score": None if pd.isna(oc) else round(oc, 2), "signal": s})
        pairs[f"{ch}/GOLDBEES"] = {"ratio": clean(ratio), "r6": clean(rc["r6m"]), "r12": clean(rc["r12m"]),
                                   "comp": clean(rc["score"]), "own": clean(own[ch]["score"]), "signal": sig}
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["pair", "week"]).to_csv(out / "etf_weekly_momentum.csv", index=False)
    js = {"asOf": weeks[-1], "weeks": weeks, "px": {s: clean(wk[s]) for s in wk.columns},
          "score": {s: clean(own[s]["score"]) for s in own}, "pairs": pairs}
    (out / "public").mkdir(exist_ok=True)
    (out / "public" / "etf_weekly_momentum.json").write_text(json.dumps(js, separators=(",", ":")), encoding="utf-8")
    print(f"{len(weeks)} weeks ({weeks[0]} .. {weeks[-1]}), {len(rows)} signal rows -> etf_weekly_momentum.csv, public/etf_weekly_momentum.json")
    for k, p in pairs.items():
        print(f"  latest {k}: ratio composite {p['comp'][-1]}, own {p['own'][-1]} -> {p['signal'][-1]}")


if __name__ == "__main__":
    main()
