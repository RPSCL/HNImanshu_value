import os
import sys
import time
import random
import signal
import argparse
import re
import threading
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, wait as fwait, FIRST_COMPLETED
from email.utils import parsedate_to_datetime
import requests
from io import StringIO
import pandas as pd
from news_dedup import dedup_frame        # same story, different words ("₹59 crore" = "Rs 59 cr")

IST          = timezone(timedelta(hours=5, minutes=30))
NEWS_DAYS    = 7
MAX_WORKERS  = 8    # parallel RSS calls; with RATE below this is plenty
RATE         = 6    # max Google News calls per second (all threads together); ~2,250 stocks (3 lists + Others) ≈ 6–7 min
MAX_RETRIES  = 3    # only on errors / "slow down" replies, never on a stock that simply has no news
TASK_TIMEOUT = 20
SAVE_EVERY   = 50
MAX_RESULTS  = 15
RSS_URL      = "https://news.google.com/rss/search?q={q}%20when%3A{d}d&hl=en-IN&gl=IN&ceid=IN:en"
UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

OTHERS_CSV   = "Nifty_Others_Fundamentals.csv"   # every other NSE stock (others.py): news for these too
BATCH        = 1750   # per run: every stock in anyone's portfolio, then this many more (random, least recently fetched first)
STATE_FILE   = "news_state.json"   # {SYMBOL: "YYYY-MM-DD HH:MM" last fetched}: 2 runs cover the whole list
INDEX_URLS = {
    "niftymicrocap250": "https://nsearchives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
    "nifty500":         "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "niftysmallcap500": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap500_list.csv",
}

# Why this is fast now: the gnews library sent 1 search + 1 extra HEAD request PER ARTICLE (15 per stock, no
# timeout) to "resolve" Google links, which no longer resolve anyway. So ~16 calls per stock → now exactly 1.
# 16× fewer calls is also what keeps us clear of Google's rate limit.
_local = threading.local()

def session():
    """one keep-alive connection per thread (no new TLS handshake for every stock)"""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update({"User-Agent": UA, "Accept": "application/rss+xml, application/xml"})
    return _local.s


SLOWED = threading.Event()  # stopped because Google said "slow down" (--max-slow 1): exit code 75, news.sh asks nothing more this time
ABORT = threading.Event()   # set at the first "now 0.0 calls/s": Google is blocking us, so stop fetching and let the workflow build + deploy


class Pace:
    """Spaces calls 1/RATE s apart across all threads. On a 429 / 503 everyone pauses (30 s, then 60 s, …)."""
    def __init__(self, rate):
        self.gap, self.next, self.lock, self.hits = 1.0 / rate, 0.0, threading.Lock(), 0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(self.next, now)
            self.next = t + self.gap
        ABORT.wait(max(0.0, t - now))             # a sleep that ends at once when the run is given up

    def slow_down(self):
        if ABORT.is_set():                        # already stopping: other threads' refusals change nothing
            return
        if OPT.max_slow == 1:                     # Oracle: the FIRST "slow down" ends the fetch at once (no pausing, no retries)
            SLOWED.set(); ABORT.set()
            print("🛑 Google asked to slow down: stopping the news fetch now; what is done is kept, push + deploy go on.", flush=True)
            return
        with self.lock:
            self.hits += 1
            pause = min(30 * self.hits, 120)
            self.next = max(self.next, time.monotonic() + pause)
            self.gap *= 1.5                       # stay slower for the rest of the run
        shown = f"{1 / self.gap:.1f}"
        print(f"⏸ Google asked to slow down; pausing {pause}s, now {shown} calls/s", flush=True)
        if OPT.max_slow and self.hits >= OPT.max_slow and not ABORT.is_set():   # Oracle: stop early, push what we have
            ABORT.set()
            print(f"🛑 Google asked to slow down {self.hits} times: stopping the news fetch; what is done is kept, push + deploy go on.", flush=True)
        if shown == "0.0" and not ABORT.is_set():                # the first 0.0 calls/s: no point going on (it once ran 5 hours like this)
            ABORT.set()
            print("🛑 Google keeps refusing (0.0 calls/s): stopping the news fetch here; what is done is kept, the build + deploy go on.", flush=True)


PACE = Pace(RATE)

# Options (GitHub runs it with none = the old behaviour; oracle/news.sh: --held-only, or --skip-held --chunk 1000, + --max-slow 3 --quiet)
_ap = argparse.ArgumentParser()
_ap.add_argument("--halves",   type=int,   default=1,  help="split this run's stocks into N parts …")
_ap.add_argument("--gap",      type=float, default=15, help="… with this many minutes of rest between parts")
_ap.add_argument("--max-slow", type=int,   default=0,  help="stop fetching after Google says 'slow down' this many times (0 = only at 0.0 calls/s)")
_ap.add_argument("--max-min",  type=float, default=0,  help="stop fetching after this many minutes of fetching (rest not counted; 0 = no limit)")
_ap.add_argument("--part-of",  type=int,   default=0,  help="take 1/N of the stocks per run (least recently fetched first, so N runs cover all); 0 = BATCH")
_ap.add_argument("--chunk",    type=int,   default=0,  help="take this many of the other stocks (least recently fetched first); overrides --part-of")
_ap.add_argument("--held-only", action="store_true",   help="only the stocks held in anyone's portfolio")
_ap.add_argument("--skip-held", action="store_true",   help="leave out the portfolio stocks (a separate run does them)")
_ap.add_argument("--quiet",    action="store_true",    help="don't print the 'no news' lines")
OPT = _ap.parse_known_args()[0]

# Ctrl+C / kill: 1st = stop asking Google, save what is done and finish normally; 2nd Ctrl+C = quit at once (nothing saved)
def _stop(sig, _frame):
    if ABORT.is_set() and sig == signal.SIGINT:
        print("\n✋ Second Ctrl+C: quitting now, nothing saved from this run.", flush=True)
        os._exit(130)
    ABORT.set()
    print("\n✋ Stopping: finishing the stocks in hand, then saving (Ctrl+C again = quit without saving)…", flush=True)
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def google_news(query):
    """[{title, published date, url}] from Google News RSS for one query; [] if there is no news."""
    url = RSS_URL.format(q=quote(query), d=NEWS_DAYS)
    for attempt in range(MAX_RETRIES):
        PACE.wait()
        if ABORT.is_set():
            return None                                        # given up: not "no news", just not asked
        try:
            r = session().get(url, timeout=15)
            if r.status_code in (429, 503):
                PACE.slow_down()
                if ABORT.is_set():
                    return None                                # stopping: not "no news", just not asked
                continue
            r.raise_for_status()
            items = ET.fromstring(r.content).iter("item")
            return [{"title": (i.findtext("title") or ""), "published date": i.findtext("pubDate") or "",
                     "url": i.findtext("link") or ""} for i in list(items)[:MAX_RESULTS]]
        except Exception:
            if ABORT.wait(1 + attempt * 2):
                return None
    return []


# ══════════════════════════════════════════════
# FETCH STOCKS
# ══════════════════════════════════════════════

def fetch_index_csvs():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer":    "https://www.nseindia.com/",
    })

    frames = []
    for url in INDEX_URLS.values():
        try:
            r = session.get(url, timeout=10)
            r.raise_for_status()
            df   = pd.read_csv(StringIO(r.text))
            cols = df.columns.str.strip()
            sym_col  = next((c for c in cols if "Symbol" in c), cols[0])
            name_col = next((c for c in cols if "Company Name" in c), cols[1])
            df = df[[sym_col, name_col]]
            df.columns = ["symbol", "name"]
            frames.append(df.dropna())
        except Exception:
            continue

    # + every stock in the repo's own lists: the 3 valuation CSVs (a fallback when NSE blocks the download)
    #   and the Others list (Nifty_Others_Fundamentals.csv, others.py), so news covers every stock on the site
    for f, name_cols in (("nifty500_valuation.csv", ("COMPANY_NAME_x", "COMPANY_NAME")),
                         ("niftysmallcap500_valuation.csv", ("COMPANY_NAME",)),
                         ("niftymicrocap250_valuation.csv", ("COMPANY_NAME",)),
                         (OTHERS_CSV, ("COMPANY_NAME",))):
        if not os.path.exists(f):
            continue
        try:
            d = pd.read_csv(f, usecols=lambda c: c in ("SYMBOL", "CMP", "MARKET_CAP_CR") + name_cols, low_memory=False)
            nc = next((c for c in name_cols if c in d.columns), None)
            if f == OTHERS_CSV:                            # only the Others the site shows (a price or market cap)
                has = pd.Series(False, index=d.index)
                for c in ("CMP", "MARKET_CAP_CR"):
                    if c in d.columns:
                        has |= pd.to_numeric(d[c], errors="coerce").notna()
                d = d[has]
            d = pd.DataFrame({"symbol": d["SYMBOL"].astype(str).str.strip(),
                              "name": d[nc].astype(str) if nc else d["SYMBOL"].astype(str)})
            frames.append(d[d["symbol"].ne("") & d["symbol"].ne("nan")])
            print(f"  + {len(d)} stocks from {f}", flush=True)
        except Exception as e:
            print(f"  (could not read {f}: {e})", flush=True)

    if not frames:
        print("❌ Failed to fetch index CSVs", flush=True)
        sys.exit(1)

    all_stocks = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["symbol"])
        .reset_index(drop=True)
    )

    stocks = list(zip(all_stocks["symbol"], all_stocks["name"]))
    random.shuffle(stocks)
    return stocks


def portfolio_symbols():
    """Every symbol anyone holds (Firestore hni_portfolio, buys − sells > 0). Needs the service account
    (GOOGLE_APPLICATION_CREDENTIALS); without it, or on any error, an empty set and the run carries on."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("  (no service account: portfolio stocks not put first)", flush=True)
        return set()
    try:
        import firebase_admin
        from firebase_admin import firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        held = set()
        for snap in firestore.client().collection("hni_portfolio").stream():
            qty = {}
            for t in (snap.to_dict() or {}).get("txns", []) or []:
                if t.get("t") in ("buy", "sell") and t.get("sym"):
                    k = str(t["sym"]).upper().strip()
                    qty[k] = qty.get(k, 0) + (t.get("qty") or 0) * (1 if t["t"] == "buy" else -1)
            held |= {k for k, q in qty.items() if q > 1e-9}
        print(f"  {len(held)} stocks held in portfolios: fetched first, every run", flush=True)
        return held
    except Exception as e:
        print(f"  (portfolio stocks not read: {e})", flush=True)
        return set()


def load_state():
    try:
        import json
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    import json
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, separators=(",", ":"), sort_keys=True)


# ══════════════════════════════════════════════
# FETCH NEWS (1 query per stock, not 3)
# ══════════════════════════════════════════════

def fetch_stock(symbol, name):
    if ABORT.is_set():
        return None
    # "Infosys Limited" → "infosys": headlines rarely say "Limited", so match on the name without it
    core      = re.sub(r"\b(limited|ltd\.?|the)\b", " ", name.lower()).replace("&", " and ")
    name_low  = " ".join(core.split())
    sym_low   = symbol.lower()
    first_two = " ".join(name_low.split()[:2])

    # Use ONE best query — company name is most precise
    query = name

    articles = google_news(query)                          # one call; retries only on errors
    if articles is None:                                   # run given up while this one was waiting
        return None

    if not articles:
        return pd.DataFrame()

    rows = []
    for a in articles:
        try:
            dt = parsedate_to_datetime(a.get("published date", ""))
        except Exception:
            continue

        title     = str(a.get("title", "")).strip()
        title_low = title.lower()
        link      = a.get("url", "")

        if not link:
            continue

        # Relevance filter
        if not (sym_low in title_low or name_low in title_low or first_two in title_low):
            continue

        rows.append({
            "stockname": symbol,
            "datetime":  dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"),
            "news":      title,
            "link":      link,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.drop_duplicates(subset=["link"], inplace=True)   # exact link dedup first
    df.sort_values("datetime", ascending=False, inplace=True)
    return df


# ══════════════════════════════════════════════
# FAST DEDUP USING TF-IDF STYLE FINGERPRINT
# ══════════════════════════════════════════════

def title_fingerprint(title: str) -> frozenset:
    """
    Bag-of-words fingerprint: remove stopwords, keep meaningful tokens.
    Two titles are near-duplicates if they share >= 60% of tokens.
    """
    STOPWORDS = {"the","a","an","in","of","to","for","and","or","is","are",
                 "on","at","with","by","from","its","as","this","that","was",
                 "be","has","have","had","it","s","co","ltd","limited"}
    tokens = set(title.lower().split()) - STOPWORDS
    return frozenset(tokens)


def jaccard(fp1: frozenset, fp2: frozenset) -> float:
    if not fp1 or not fp2:
        return 0.0
    return len(fp1 & fp2) / len(fp1 | fp2)


def dedup_similar_news(df: pd.DataFrame, threshold=0.6) -> pd.DataFrame:
    """
    Fast near-duplicate removal using pre-computed fingerprints.
    O(n * k) where k = kept titles so far (much faster than O(n²)).
    """
    keep_indices = []

    for sym, group in df.groupby("stockname", sort=False):
        kept_fps = []
        for idx, row in group.iterrows():
            fp = title_fingerprint(row["news"])
            if not any(jaccard(fp, kfp) >= threshold for kfp in kept_fps):
                kept_fps.append(fp)
                keep_indices.append(idx)

    return df.loc[keep_indices]


# ══════════════════════════════════════════════
# TRIM + FULL DEDUP PIPELINE
# ══════════════════════════════════════════════

def parse_dt(df):
    df["dt_parsed"] = pd.to_datetime(
        df["datetime"].str.replace(" IST", "", regex=False),
        errors="coerce"
    ).dt.tz_localize(IST)
    return df


def trim_and_dedup(df: pd.DataFrame, cutoff) -> pd.DataFrame:
    if df.empty:
        return df

    df = parse_dt(df)

    # 1. Time filter
    df = df[df["dt_parsed"] >= cutoff]

    # 2. Exact link dedup (fastest, do first)
    df = df.sort_values("dt_parsed", ascending=False)
    df = df.drop_duplicates(subset=["link"], keep="first")

    # 3. Exact title dedup
    df = df.drop_duplicates(subset=["stockname", "news"])

    # 4. Same story told twice (per stock): news_dedup.py normalises ₹/Rs/crore/%, compares key figures + wording
    df = dedup_frame(df)                                  # already sorted newest first, so the newest version is kept

    return df[["stockname", "datetime", "news", "link"]].reset_index(drop=True)


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

def main():
    print("🚀 STARTED FETCH NEWS", flush=True)

    outfile = "multi_stock_news.csv"
    cutoff  = datetime.now(IST) - timedelta(days=NEWS_DAYS)
    
    # Delete existing CSV and start fresh
    #if os.path.exists(outfile):
        #os.remove(outfile)
        #print("🗑️ Deleted existing CSV, starting fresh", flush=True)

    if os.path.exists(outfile):
        existing_df = trim_and_dedup(pd.read_csv(outfile), cutoff)
        seen_links  = set(existing_df["link"].dropna())
        seen_keys   = set(zip(existing_df["stockname"], existing_df["news"]))
    else:
        existing_df = pd.DataFrame(columns=["stockname","datetime","news","link"])
        seen_links  = set()
        seen_keys   = set()

    all_stocks = fetch_index_csvs()
    names = dict(all_stocks)
    held = portfolio_symbols()                                     # 1. everything people hold, every run
    state = load_state()
    rest = [(s, n) for s, n in all_stocks if s not in held]
    random.shuffle(rest)                                           # random among equals …
    rest.sort(key=lambda x: state.get(x[0], ""))                  # … never-fetched / longest-ago first
    take   = -(-len(rest) // OPT.part_of) if OPT.part_of > 0 else BATCH    # --part-of 2: half of the list this run, the other half next run
    if OPT.chunk > 0:
        take = OPT.chunk                                            # --chunk 1000: the 1,000 asked longest ago (next run: the next 1,000)
    if OPT.held_only:
        take = 0                                                    # --held-only: portfolio stocks, nothing else
    if OPT.skip_held:
        held = set()                                                # --skip-held: the market-hours portfolio run does them
    stocks = [(s, names.get(s, s)) for s in sorted(held)] + rest[:take]    # 2. + that many more
    total  = len(stocks)
    print(f"\n📋 {total} stocks this run: {len(held)} held in portfolios + {min(take, len(rest))} of {len(rest)} others"
          f" (least recently fetched first) | workers={MAX_WORKERS} | ≤{RATE} calls/s\n", flush=True)

    accumulated = []
    done = found = 0
    asked = []                                                     # stocks really asked this run (the others stay "least recently fetched")
    t0 = time.time()
    fetching = 0.0                                                 # minutes spent asking Google (rests between parts not counted)

    # --halves N: portfolio stocks + the rest split into N parts, --gap minutes apart (spreads the load on Google)
    n_parts = max(1, OPT.halves)
    size    = -(-total // n_parts) if total else 0
    parts   = [stocks[i:i + size] for i in range(0, total, size)] if size else []

    for pi, part in enumerate(parts):
      if ABORT.is_set():
          break
      if pi:
          print(f"\n😴 Part {pi} of {len(parts)} done ({done} stocks). Resting {OPT.gap:g} min before part {pi + 1}…\n", flush=True)
          if ABORT.wait(OPT.gap * 60):                             # Ctrl+C / kill during the rest → skip the next part
              break
          PACE.next = time.monotonic()
      elif len(parts) > 1:
          print(f"▶ Part 1 of {len(parts)}: {len(part)} stocks", flush=True)
      tp = time.time()

      # --max-min: a watchdog that stops the fetch when the fetching time is used up
      if OPT.max_min:
          left = OPT.max_min * 60 - fetching * 60
          if left <= 0:
              print("⏱ Fetching time used up: stopping here.", flush=True); break
          timer = threading.Timer(left, lambda: (ABORT.set(), print(f"\n⏱ {OPT.max_min:g} min of fetching used up: stopping, push + deploy go on.", flush=True)))
          timer.daemon = True; timer.start()

      # no "with" block: once the run is stopped we must NOT wait for calls still hanging on Google (a run once hung for hours)
      pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
      futures = {pool.submit(fetch_stock, s, n): (s, n) for s, n in part}
      pending = set(futures)
      while pending:
        fin, pending = fwait(pending, timeout=1, return_when=FIRST_COMPLETED)
        if ABORT.is_set():                                         # stopping: keep what has finished, drop the rest
            fin = {f for f in fin | pending if f.done()}
            pending = set()
        for f in fin:
            sym, name = futures[f]
            if f.cancelled():
                continue

            try:
                df = f.result(timeout=TASK_TIMEOUT)
            except Exception:
                df = pd.DataFrame()

            if df is None:                                         # given up: not asked, not counted
                continue
            done += 1
            asked.append(sym)

            if df is not None and not df.empty:
                # Filter by BOTH link and (stockname, news) to catch all dups
                mask = df.apply(
                    lambda r: r["link"] not in seen_links and
                              (r["stockname"], r["news"]) not in seen_keys,
                    axis=1
                )
                new_rows = df[mask]

                if not new_rows.empty:
                    seen_links.update(new_rows["link"])
                    seen_keys.update(zip(new_rows["stockname"], new_rows["news"]))
                    accumulated.append(new_rows)
                    found += len(new_rows)
                    print(f"✓ [{done}/{total}] {sym} → {len(new_rows)} new", flush=True)
                elif not OPT.quiet:
                    print(f"· [{done}/{total}] {sym} → no new", flush=True)
            else:
                if not OPT.quiet:
                    print(f"• [{done}/{total}] {sym} → no news", flush=True)

            # Periodic save
            if done % SAVE_EVERY == 0 and accumulated:
                new_df = pd.concat(accumulated, ignore_index=True)
                existing_df = trim_and_dedup(
                    pd.concat([existing_df, new_df], ignore_index=True),
                    cutoff
                )
                existing_df.to_csv(outfile, index=False, encoding="utf-8-sig")
                accumulated = []

                elapsed = time.time() - t0
                rate    = done / elapsed
                eta     = (total - done) / rate if rate else 0
                print(f"\n💾 Saved | {done}/{total} | ETA {eta/60:.1f} min\n", flush=True)

      pool.shutdown(wait=False, cancel_futures=True)              # never waits for a call still in the air
      fetching += (time.time() - tp) / 60
      if OPT.max_min:
          timer.cancel()

    # Final save
    if accumulated:
        new_df = pd.concat(accumulated, ignore_index=True)
        existing_df = pd.concat([existing_df, new_df], ignore_index=True)

    existing_df = trim_and_dedup(existing_df, cutoff)
    existing_df.to_csv(outfile, index=False, encoding="utf-8-sig")
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M")         # these were asked this run, news or not
    for s in asked:
        state[s] = stamp
    keep = {s for s, _ in all_stocks}
    save_state({k: v for k, v in state.items() if k in keep})

    elapsed = time.time() - t0
    if ABORT.is_set():
        print(f"\n⚠️ Stopped early: {done} of {total} stocks asked, {total - done} left for the next run.", flush=True)
    print(f"\n✅ Done | {found} new rows | total {len(existing_df)} | time {elapsed/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(75 if SLOWED.is_set() else 0)   # saved already: end NOW, even if a worker is still stuck on a Google call (the lock is freed)
