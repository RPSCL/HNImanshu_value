#!/usr/bin/env python3
"""fetch_etf_history.py -- daily price history of the Portfolio Builder's ETFs (+ NIFTY 500), saved in the repo.

Uses the same Angel One login / candle call / split detection as fetch_prices.py (the "LTP code"),
so no new credentials are needed (ANGEL_* secrets, see creds.py).

Writes (all committed to the repo, so every build can read them without calling Angel):

  data/prices/raw/<SYM>.csv          EVERYTHING Angel returns, as it is: date,open,high,low,close,volume
                                     (listing date -> today, unadjusted). Re-runs only fetch the new days
                                     (last saved date - 10 days) and merge; use --full to download it all again.
  data/prices/daily/<SYM>.csv        the past 365 days (+30 days lead-in) day by day, with adj_close
                                     (split / bonus adjusted with fetch_prices.corporate_actions)
  data/prices/manifest.json          rows / first date / last date per symbol, split events found
  public/etf_daily.json              the same 395 days for the website: builder.html uses it for the
                                     total-return and max-drawdown charts of the 1-year backtest

Usage:
    python -u fetch_etf_history.py                 # update (first run downloads the full history)
    python -u fetch_etf_history.py --full          # re-download the full history of every symbol
    python -u fetch_etf_history.py --only GOLDBEES,ALPHA
    python -u fetch_etf_history.py --offline       # no Angel: rebuild daily/ and etf_daily.json from raw/
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import fetch_prices as fp          # Angel login, rate limiter, scrip master, split detection

BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "data" / "prices" / "raw"
DAILY_DIR = BASE / "data" / "prices" / "daily"
MANIFEST = BASE / "data" / "prices" / "manifest.json"
SITE_JSON = BASE / "public" / "etf_daily.json"

SYMBOLS = list(fp.ETF_SYMS) + [fp.BENCH_SYM]          # 7 ETFs + ^NIFTY500
DAYS, LEAD = 365, 30                                  # past 365 days, plus a lead-in so "1 year back" has a close on / before it
FLOOR = date(2007, 1, 1)                              # nothing on NSE ETFs is older
COLS = ["date", "open", "high", "low", "close", "volume"]
log = fp.log


def fname(sym: str) -> str:
    return sym.replace("^", "")                       # ^NIFTY500 -> NIFTY500


def to_df(rows) -> pd.DataFrame:
    """Angel candles [[ts, o, h, l, c, v], ...] -> DataFrame, one row per day."""
    if not rows:
        return pd.DataFrame(columns=COLS)
    d = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    d["date"] = d["ts"].astype(str).str[:10]
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[COLS].dropna(subset=["close"]).drop_duplicates("date", keep="last").sort_values("date")


def fetch_forward(api, token, start: date, end: date):
    rows, s = [], start
    while s <= end:
        e = min(end, s + timedelta(days=fp.CHUNK_DAYS))
        rows += api._call(token, s, e)
        s = e + timedelta(days=1)
    return rows


def fetch_full(api, token, end: date):
    """Walk backwards in ~1-year chunks until Angel has nothing older (two empty chunks in a row)."""
    rows, e, empty = [], end, 0
    while e >= FLOOR and empty < 2:
        s = max(FLOOR, e - timedelta(days=fp.CHUNK_DAYS))
        got = api._call(token, s, e)
        rows += got
        empty = 0 if got else empty + 1
        e = s - timedelta(days=1)
    return rows


def add_adj(df: pd.DataFrame):
    """adj_close = close x product of the split / bonus factors AFTER that day. -> (df, events)"""
    c = [(datetime.strptime(r.date, "%Y-%m-%d").date(), r.open, r.close, r.high) for r in df.itertuples()]
    ev = fp.corporate_actions(c) if len(c) > 1 else []
    out = df.copy()
    out["adj_close"] = [round(r.close * (math.prod(f for e, f, _ in ev if e > d) if ev else 1.0), 4)
                        for r, (d, *_rest) in zip(df.itertuples(), c)]
    return out, [f"{d:%Y-%m-%d} x{f:.4g} ({lab})" for d, f, lab in ev]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="download the whole history again (default: only the new days)")
    ap.add_argument("--only", default="", help="comma separated symbols (default: the 7 ETFs + ^NIFTY500)")
    ap.add_argument("--offline", action="store_true", help="no Angel call: rebuild daily/ + etf_daily.json from raw/")
    a = ap.parse_args()
    todo = [s.strip().upper() for s in a.only.split(",") if s.strip()] or SYMBOLS
    todo = [("^" + s if s == "NIFTY500" else s) for s in todo]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    api = tmap = None
    end = datetime.now(fp.IST).date()
    if not a.offline:
        tmap = fp.build_token_map(fp.scrip_master())
        api = fp.Angel()

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    n_ok = 0
    for sym in todo:
        path = RAW_DIR / f"{fname(sym)}.csv"
        old = pd.read_csv(path, dtype={"date": str}) if path.exists() else pd.DataFrame(columns=COLS)
        df = old
        if not a.offline:
            try:
                if sym not in tmap:
                    raise RuntimeError("no Angel token (not in scrip master)")
                tok = tmap[sym][1]
                if a.full or old.empty:
                    log(f"{sym:<12} full history ...")
                    new = to_df(fetch_full(api, tok, end))
                else:
                    start = datetime.strptime(old["date"].max(), "%Y-%m-%d").date() - timedelta(days=10)
                    log(f"{sym:<12} new days since {start} ...")
                    new = to_df(fetch_forward(api, tok, start, end))
                if new.empty and old.empty:
                    raise RuntimeError("Angel returned no candles")
                df = pd.concat([old, new]).drop_duplicates("date", keep="last").sort_values("date")
            except Exception as ex:                                  # keep whatever is already saved
                log(f"{sym:<12} FAILED: {str(ex)[:120]}" + ("  (keeping the saved file)" if not old.empty else ""))
                if old.empty:
                    continue
        df = df[COLS].reset_index(drop=True)
        df.to_csv(path, index=False)                                 # raw, as it is

        adj, events = add_adj(df)
        last = datetime.strptime(df["date"].max(), "%Y-%m-%d").date()
        cut = f"{last - timedelta(days=DAYS + LEAD):%Y-%m-%d}"
        adj[adj["date"] >= cut].to_csv(DAILY_DIR / f"{fname(sym)}.csv", index=False)
        manifest[sym] = {"rows": int(len(df)), "first": df["date"].min(), "last": df["date"].max(),
                         "splits": events, "updated": f"{datetime.now(fp.IST):%Y-%m-%d %H:%M} IST"}
        log(f"{sym:<12} {len(df):>5} rows  {df['date'].min()} -> {df['date'].max()}"
            + (f"   split/bonus: {'; '.join(events)}" if events else ""))
        n_ok += 1

    # ---- public/etf_daily.json: one shared date axis, adjusted + raw close per symbol ----
    series, dates = {}, set()
    for sym in SYMBOLS:
        p = RAW_DIR / f"{fname(sym)}.csv"
        if not p.exists():
            continue
        adj, _ = add_adj(pd.read_csv(p, dtype={"date": str})[COLS])
        last = datetime.strptime(adj["date"].max(), "%Y-%m-%d").date()
        adj = adj[adj["date"] >= f"{last - timedelta(days=DAYS + LEAD):%Y-%m-%d}"]
        series[fname(sym)] = adj.set_index("date")
        dates |= set(adj["date"])
    axis = sorted(dates)
    if axis:
        out = {"asOf": axis[-1], "generated": f"{datetime.now(fp.IST):%Y-%m-%d %H:%M} IST", "dates": axis,
               "symbols": list(series), "close": {}, "adj": {}}
        for k, s in series.items():
            out["close"][k] = [None if d not in s.index else round(float(s.at[d, "close"]), 4) for d in axis]
            out["adj"][k] = [None if d not in s.index else round(float(s.at[d, "adj_close"]), 4) for d in axis]
        SITE_JSON.parent.mkdir(parents=True, exist_ok=True)
        SITE_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        log(f"Wrote {SITE_JSON.relative_to(BASE)}: {len(series)} symbols x {len(axis)} days ({axis[0]} -> {axis[-1]})")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    log(f"Done: {n_ok} of {len(todo)} symbols updated -> data/prices/")
    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
