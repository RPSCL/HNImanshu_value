#!/usr/bin/env python3
"""
fetch_prices.py — weekly prices from Angel One SmartAPI  →  prices.csv
=======================================================================
For every symbol in the 3 valuation CSVs (+ the NIFTY 500 index as benchmark) it downloads ~2 years of
daily candles and writes ONE row per symbol:

    SYMBOL, ANGEL_SYMBOL, ANGEL_TOKEN,
    LTP, LTP_DATE                         latest close
    PRICE_<P>, PRICE_<P>_DATE             raw close on the last trading day on/before (data date − P), for every
                                          back period P = 3M, 6M, 9M, 1Y, 1_25Y, 1_5Y (the site's "Back" dropdown)
    PRICE_<P>_ADJ                         same price on TODAY's share basis (splits / bonuses after it divided out)
    HIGH_52W_NOW, HIGH_52W_<P>            52-week high now / as of each back date (today's share basis)
    LISTED_FROM                           first candle fetched (a stock listed after a back date is left out there)
    MOM_<P>_R3, MOM_<P>_R6, MOM_<P>_R12   price momentum as of today (P = NOW) and as of each back date:
                                          3-month, 6-month and 12-minus-1-month returns, %, split-adjusted
    MOM_M<k>_R3 / _R6 / _R12              the same three momentum returns as of each month point k = 1 … 12 months back
                                          (MONTH_DAYS: 30, 61, 91 … 365 days; k = 12 is the 1Y back date). With the NOW set these
                                          give the Portfolio Builder the whole stock universe's momentum month by month, so the ETF
                                          pairs can be re-scored every month in the 1-year backtest
    PX_MONTHLY                            ETFs + benchmark only: split-adjusted close at k = 0 (latest) … 12, '|' separated
    ADJ_FACTOR, CORP_ACTIONS              detected split / bonus factor after BACK_DATE, and every event found
    BACK_DATE, STATUS, FETCHED_AT

BACK_DATE = (newest DATE_DOWNLOADED in the CSVs) − 365 days, the same date fundamentals.py uses for 1Y back.

Splits / bonuses: NSE price bands make a 38%+ overnight drop practically impossible without a corporate
action, so a close-to-close drop that matches a standard ratio (1:2, 1:5, 1:10 split, 1:1 / 1:2 bonus …)
is treated as one and the older prices are divided by it. If Angel already adjusts its candles, nothing is
detected and ADJ_FACTOR stays 1.

Credentials come from creds.py (repo secrets on GitHub, .env locally). Nothing secret is ever printed.

    python fetch_prices.py               # all symbols
    python fetch_prices.py --limit 20    # quick test
    python fetch_prices.py --map-only    # check symbol → Angel token mapping, no login, no candles
    python fetch_prices.py --is-due      # exit 0 if a fetch is due (prices.csv ≥ MIN_AGE_DAYS old), else 1
    python fetch_prices.py --force       # fetch even if prices.csv is recent
    python fetch_prices.py --history --symbols A,B,C   # wipe etf_prices/, then ETFs + NIFTY 500 + these stocks, day by day (PF Builder chart)
    python fetch_prices.py --etf-only    # ONLY the Portfolio Builder ETFs + NIFTY 500 benchmark (cron.yml mode 4);
                                         # every other row of prices.csv stays as it is
"""

import argparse
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE        = Path(__file__).resolve().parent
OUT_CSV     = BASE / "prices.csv"
CACHE_DIR   = BASE / ".cache"
CSV_FILES   = ["nifty500_valuation.csv", "niftysmallcap500_valuation.csv", "niftymicrocap250_valuation.csv",
               "Nifty_Others_Fundamentals.csv"]          # others: priced too (momentum, Historical); not used for the data date
SCRIP_URL   = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
BENCH_SYM   = "^NIFTY500"                 # row name used for the benchmark in prices.csv
BENCH_NAMES = {"nifty500"}               # matched against the index's symbol / name, spaces removed, lower case
# ETFs for the Portfolio Builder (builder.html, index part): fetched with part 1, like the benchmark
ETF_SYMS    = ["GOLDBEES", "ITBEES", "ALPHA", "JUNIORBEES", "MON100", "HNGSNGBEES", "FMCGIETF"]
# Saved price history for the Portfolio Builder (committed to the repo, so every build can read it):
#   etf_prices/raw/<SYMBOL>.csv   every daily candle exactly as Angel returned it (date, open, high, low, close, volume), NOT adjusted
#   etf_prices/daily_close.csv    one column per symbol: split-adjusted close, one row per trading day, last 365 days (+ a few days
#                                 before, so the 1Y-back date always finds a close on / before it).
#   etf_prices/requested.json     what the last --history run asked for / could not get
#   Normal runs (mode 4, price part 1) refresh the ETF + NIFTY 500 files and keep every other column.
#   --history (cron-less, builder_prices.yml, started from the PF Builder page): wipes etf_prices/, then downloads the ETFs, NIFTY 500
#   and every stock of the page's 1-year-back list day by day. build.py turns daily_close.csv into public/builder_daily.json.
ETF_DIR     = BASE / "etf_prices"
DAILY_CSV   = ETF_DIR / "daily_close.csv"
DAILY_LEAD  = 10                          # extra days kept before (data date − 365)
BACK_DAYS   = 365                         # must match fundamentals.BACK_DAYS
KEEP_ASKED_DAYS = 60                      # PF Builder stocks nobody asked for in this many days leave the saved prices (ETFs + NIFTY 500 never do)
MAX_STOCKS      = 300                     # at most this many PF Builder stocks are kept (the least recently asked go first)
MONTH_DAYS  = [round(k * 365 / 12) for k in range(13)]   # k = 0 (now) … 12 (= 365 days, the 1Y back date): month points for the Portfolio Builder
PERIODS     = {"3M": 91, "6M": 182, "9M": 273, "1Y": 365, "1_25Y": 456, "1_5Y": 548}   # must match fundamentals.PERIODS
RATE        = 2.5                         # max candle requests per second, shared by all workers (Angel throttles ~3/s)
WORKERS     = 3                           # parallel fetches; network delay overlaps, the rate limit stays global
CHUNK_DAYS  = 380                         # ~2.5 years in 3 calls (~255 rows each, under the 500-row cap some users hit)
RETRY_PASS  = True                        # after the main pass, retry every failed symbol once more, slowly
MIN_AGE_DAYS = 5                          # weekly: skip the fetch if prices.csv is younger than this (--force overrides)
SERIES_PREF = ["EQ", "BE", "BZ", "SM", "ST"]   # which NSE series to use when a stock has several
# standard split / bonus price ratios (new price ÷ old price)
CA_RATIOS   = {1/2: "1:2 split or 1:1 bonus", 1/3: "1:3 split or 2:1 bonus", 1/4: "1:4 split or 3:1 bonus",
               1/5: "1:5 split", 1/10: "1:10 split", 2/3: "1:2 bonus", 1/2.5: "2:5 split",
               1/6: "1:6 split", 1/8: "1:8 split", 1/20: "1:20 split"}
CA_MAX_RATIO = 0.62                       # only overnight drops deeper than this are checked
CA_TOL       = 0.10                       # |ratio / standard ratio − 1| must be within 10 %
IST          = ZoneInfo("Asia/Kolkata")


def log(msg):
	print(msg, flush=True)


def mask(v, keep=2):
	"""'H62425962' -> 'H6*****62' : enough to recognise which account, never the full value."""
	v = str(v or "")
	return (v[:keep] + "*" * max(len(v) - 2 * keep, 3) + v[-keep:]) if len(v) > 2 * keep else "*" * len(v)


# ─────────────────────────────────────────────────────────────────────────────
# Symbols + dates from the valuation CSVs
# ─────────────────────────────────────────────────────────────────────────────
def load_symbols():
	syms, asof = [], None
	for f in CSV_FILES:
		p = BASE / f
		if not p.exists():
			continue
		d = pd.read_csv(p, usecols=lambda c: c in ("SYMBOL", "DATE_DOWNLOADED", "CMP", "MARKET_CAP_CR"), low_memory=False)
		if "others" in f.lower():                          # Others: only those the site shows (screener had a price / mcap)
			has = pd.Series(False, index=d.index)
			for c in ("CMP", "MARKET_CAP_CR"):
				if c in d.columns:
					has |= pd.to_numeric(d[c], errors="coerce").notna()
			d = d[has]
		syms += [str(s).strip().upper() for s in d["SYMBOL"].dropna()]
		if "DATE_DOWNLOADED" in d and "others" not in f.lower():
			dt = pd.to_datetime(d["DATE_DOWNLOADED"], errors="coerce").max()
			if pd.notna(dt) and (asof is None or dt > asof):
				asof = dt
	asof = (asof.date() if asof is not None else datetime.now(IST).date())
	return list(dict.fromkeys(syms)), asof


# ─────────────────────────────────────────────────────────────────────────────
# Angel scrip master: SYMBOL → token
# ─────────────────────────────────────────────────────────────────────────────
SCRIP_KEYS = ("token", "symbol", "name", "exch_seg", "instrumenttype")   # all that build_token_map reads


def scrip_master():
	"""Angel's list of every instrument → SYMBOL / token. Downloaded once per Indian day, then read from
	.cache/scrip_master_YYYYMMDD.json. Only NSE rows and the 5 fields used are kept (~40 MB → ~3 MB),
	so the GitHub cache that carries it between runs stays small and restores in a second."""
	CACHE_DIR.mkdir(exist_ok=True)
	cache = CACHE_DIR / f"scrip_master_{datetime.now(IST):%Y%m%d}.json"      # IST day, not the runner's UTC day
	if cache.exists():
		log(f"Angel scrip master: today's saved copy ({cache.name})")
		return json.loads(cache.read_text())
	log("Downloading Angel scrip master (first run today) …")
	r = requests.get(SCRIP_URL, timeout=120)
	r.raise_for_status()
	data = [{k: m.get(k) for k in SCRIP_KEYS} for m in r.json() if m.get("exch_seg") == "NSE"]
	for old in CACHE_DIR.glob("scrip_master_*.json"):
		old.unlink()
	cache.write_text(json.dumps(data, separators=(",", ":")))
	return data


def build_token_map(master):
	"""{SYMBOL: (angel_symbol, token)} for NSE cash stocks, plus the benchmark index."""
	nse = [m for m in master if m.get("exch_seg") == "NSE"]
	best = {}
	for m in nse:
		sym = str(m.get("symbol", ""))
		if "-" not in sym:
			continue
		base, series = sym.rsplit("-", 1)
		if series not in SERIES_PREF:
			continue
		rank = SERIES_PREF.index(series)
		key = base.upper()
		if key not in best or rank < best[key][0]:
			best[key] = (rank, sym, str(m["token"]))
	out = {k: (v[1], v[2]) for k, v in best.items()}
	for m in nse:                                                    # benchmark index
		if str(m.get("instrumenttype", "")).upper() != "AMXIDX":
			continue
		names = {str(m.get("symbol", "")), str(m.get("name", ""))}
		if any(n.replace(" ", "").lower() in BENCH_NAMES for n in names):
			out[BENCH_SYM] = (str(m.get("symbol")), str(m["token"]))
			break
	return out


# ─────────────────────────────────────────────────────────────────────────────
# Angel login + candles
# ─────────────────────────────────────────────────────────────────────────────
class RateLimiter:
	"""Spaces calls at least 1/RATE s apart across all threads; cooldown() pauses everyone after a 'too fast' reply."""
	def __init__(self, rate):
		self.gap, self.next, self.lock = 1.0 / rate, 0.0, threading.Lock()

	def wait(self):
		with self.lock:
			now = time.monotonic()
			t = max(self.next, now)
			self.next = t + self.gap
		time.sleep(max(0.0, t - now))

	def cooldown(self, sec):
		with self.lock:
			self.next = max(self.next, time.monotonic() + sec)
			self.gap = min(self.gap * 1.1, 1.0)                      # and slow down for good: settles under Angel's real limit

	@property
	def rate(self):
		return 1.0 / self.gap


class Angel:
	def __init__(self):
		from creds import ANGEL, require, totp                       # values from secrets / .env
		from SmartApi import SmartConnect                            # pip install smartapi-python
		require(ANGEL)
		self._c, self._totp, self._api = ANGEL, totp, SmartConnect
		self.limiter, self.login_lock, self.n_rate = RateLimiter(RATE), threading.Lock(), 0
		self.login()

	def login(self):
		c = self._c
		log("── Angel login ─────────────────────────────────────────")
		log(f"  1/4 credentials loaded   client {mask(c['username'])}  |  API key {mask(c['api_key'])}"
		    f"  |  PIN {len(str(c['pwd']))} digits  |  TOTP secret {len(str(c['totp_token']))} chars")
		self.api = self._api(api_key=c["api_key"])
		log("  2/4 SmartConnect created")
		code = self._totp(c["totp_token"])
		log(f"  3/4 TOTP generated       {len(code)} digits (valid ~30 s)")
		res = self.api.generateSession(c["username"], c["pwd"], code)
		if not res or not res.get("status"):
			log(f"  4/4 generateSession      FAILED  message: {(res or {}).get('message')}  errorcode: {(res or {}).get('errorcode')}")
			raise RuntimeError(f"Angel login failed: {(res or {}).get('message')} ({(res or {}).get('errorcode')})")
		data = res.get("data") or {}
		who = data.get("name") or data.get("clientcode") or ""
		log(f"  4/4 generateSession      OK  {('logged in as ' + str(who)) if who else ''}"
		    f"  |  session token received: {'yes' if data.get('jwtToken') or data.get('jwttoken') else 'n/a'}")
		log("────────────────────────────────────────────────────────")

	def candles(self, token, start: date, end: date, raw=None):
		"""Daily candles [(date, open, close, high)] between start and end, fetched in ~1-year chunks.
		raw: optional list; every candle is also appended to it as Angel sent it (date, open, high, low, close, volume)."""
		rows, s = [], start
		while s <= end:
			e = min(end, s + timedelta(days=CHUNK_DAYS))
			rows += self._call(token, s, e)
			s = e + timedelta(days=1)
		out, seen = {}, {}
		for ts, o, h, l, c, v in rows:
			out[str(ts)[:10]] = (float(o), float(c), float(h))
			seen[str(ts)[:10]] = (str(ts)[:10], o, h, l, c, v)
		if raw is not None:
			raw.extend(seen[k] for k in sorted(seen))
		return [(datetime.strptime(k, "%Y-%m-%d").date(), *v) for k, v in sorted(out.items())]

	def _call(self, token, s, e, tries=6):
		params = {"exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
		          "fromdate": f"{s:%Y-%m-%d} 09:15", "todate": f"{e:%Y-%m-%d} 15:30"}
		msg = ""
		for i in range(tries):
			self.limiter.wait()                                      # global pacing across all workers
			try:
				res = self.api.getCandleData(params)
			except Exception as ex:                                  # network / throttling (library raises on non-JSON)
				res = {"status": False, "message": str(ex)[:160]}
			if res and res.get("status"):
				return res.get("data") or []
			msg = str((res or {}).get("message", "")) + " " + str((res or {}).get("errorcode", ""))
			low = msg.lower()
			if "access rate" in low or "exceeding" in low or "too many" in low or "AB1019" in msg:
				self.n_rate += 1
				self.limiter.cooldown(2.0 * (i + 1))                 # "too fast": everyone pauses 2, 4, 6 … s
			elif "token" in low or "AG8001" in msg or "AG8002" in msg:
				with self.login_lock:                                # session expired → one thread logs in again
					self.login()
			else:
				time.sleep(0.5 * (i + 1))
		raise RuntimeError(f"candles failed: {msg.strip()[:120]}")


# ─────────────────────────────────────────────────────────────────────────────
# Split / bonus detection and the numbers written to prices.csv
# ─────────────────────────────────────────────────────────────────────────────
def corporate_actions(c):
	"""[(date, factor, label)] for overnight drops matching a standard split / bonus ratio."""
	ev = []
	for (d0, _, c0, _), (d1, o1, c1, _) in zip(c, c[1:]):
		if c0 <= 0:
			continue
		for x in (o1 / c0, c1 / c0):                                 # open is cleanest; close as a fallback
			if x >= CA_MAX_RATIO:
				continue
			ratio, label = min(CA_RATIOS.items(), key=lambda kv: abs(x / kv[0] - 1))
			if abs(x / ratio - 1) <= CA_TOL:
				ev.append((d1, ratio, label))
				break
	return ev


def summarise(c, asof: date, with_px=False):
	"""Numbers for one symbol from its candles (oldest → newest): latest close + one price per back period."""
	ev = corporate_actions(c)
	def factor_after(d):                                             # product of events strictly after day d
		return math.prod(f for e, f, _ in ev if e > d) if ev else 1.0
	adj = [(d, cl * factor_after(d), hi * factor_after(d)) for d, _, cl, hi in c]
	last = adj[-1]
	row = {"LTP": round(c[-1][2], 2), "LTP_DATE": f"{c[-1][0]:%Y-%m-%d}", "LISTED_FROM": f"{c[0][0]:%Y-%m-%d}",
	       "HIGH_52W_NOW": round(max(h for d, _, h in adj if d > last[0] - timedelta(days=365)), 2),
	       "CORP_ACTIONS": "; ".join(f"{d:%Y-%m-%d} ×{f:.4g} ({lab})" for d, f, lab in ev)}
	for k, days in PERIODS.items():
		back = asof - timedelta(days=days)
		before = [x for x in adj if x[0] <= back]
		if not before:
			continue                                                 # not listed yet on that date
		d0 = before[-1][0]
		raw = next(cl for d, _, cl, _ in c if d == d0)
		row.update({f"PRICE_{k}": round(raw, 2), f"PRICE_{k}_DATE": f"{d0:%Y-%m-%d}",
		            f"PRICE_{k}_ADJ": round(before[-1][1], 4),
		            f"HIGH_52W_{k}": round(max(h for d, _, h in before if d > back - timedelta(days=365)), 2)})
		if k == "1Y":
			row["ADJ_FACTOR"] = round(factor_after(d0), 6)
	# momentum as of today and as of every back date (only prices on / before that date: no look-ahead)
	def px(days):                                                    # adjusted close on / before (asof − days)
		t = asof - timedelta(days=days)
		b = [x for x in adj if x[0] <= t]
		return b[-1][1] if b and (t - b[-1][0]).days <= 10 else None
	pct = lambda a, b: round((a / b - 1) * 100, 2) if (a and b) else None
	for k, d in [("NOW", 0)] + list(PERIODS.items()):
		now_p = last[1] if d == 0 else px(d)
		row.update({f"MOM_{k}_R3": pct(now_p, px(d + 91)), f"MOM_{k}_R6": pct(now_p, px(d + 182)),
		            f"MOM_{k}_R12": pct(px(d + 30), px(d + 365))})       # 12-1: skips the last month (short-term reversal)
	# month by month (k = 1 … 12): the same three returns, for the Portfolio Builder's monthly ETF-pair check
	for k in range(1, 13):
		d = MONTH_DAYS[k]
		now_p = px(d)
		row.update({f"MOM_M{k}_R3": pct(now_p, px(d + 91)), f"MOM_M{k}_R6": pct(now_p, px(d + 182)),
		            f"MOM_M{k}_R12": pct(px(d + 30), px(d + 365))})
	if with_px:                                                      # ETFs + benchmark: the monthly price path (k = 0 … 12)
		path = [last[1]] + [px(MONTH_DAYS[k]) for k in range(1, 13)]
		row["PX_MONTHLY"] = "|".join("" if v is None else f"{v:.4f}" for v in path)
	row["STATUS"] = "OK" if "PRICE_1Y" in row else "NOT LISTED ON BACK DATE"   # 1Y status kept for older readers
	return row


# ─────────────────────────────────────────────────────────────────────────────
# Saved ETF history (etf_prices/): raw candles + daily split-adjusted closes
# ─────────────────────────────────────────────────────────────────────────────
def file_name(sym):
	return sym.replace("^", "")                                      # ^NIFTY500 → NIFTY500


def adjusted_closes(c):
	"""[(date, close on TODAY's share basis)] from candles (oldest → newest): same split / bonus handling as summarise()."""
	ev = corporate_actions(c)
	return [(d, cl * (math.prod(f for e, f, _ in ev if e > d) if ev else 1.0)) for d, _, cl, _ in c]


def save_etf_history(raw, closes, asof: date):
	"""raw {SYM: [(date, o, h, l, c, v)]}, closes {SYM: [(date, adj close)]} → etf_prices/.
	Raw files are rewritten per symbol that was fetched; daily_close.csv keeps the columns of symbols not fetched this time."""
	(ETF_DIR / "raw").mkdir(parents=True, exist_ok=True)
	for sym, rows in raw.items():
		pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"]).to_csv(ETF_DIR / "raw" / f"{file_name(sym)}.csv", index=False)
	if not closes:
		return
	cut = f"{asof - timedelta(days=BACK_DAYS + DAILY_LEAD):%Y-%m-%d}"
	fresh = pd.DataFrame({file_name(s): pd.Series({f"{d:%Y-%m-%d}": round(v, 4) for d, v in r if f"{d:%Y-%m-%d}" >= cut}) for s, r in closes.items()})
	if DAILY_CSV.exists():
		try:
			old = pd.read_csv(DAILY_CSV, index_col="date")
			old.index = old.index.astype(str)
			fresh = fresh.combine_first(old.drop(columns=[c for c in fresh.columns if c in old.columns]).reindex(fresh.index.union(old.index)))
		except Exception as ex:
			log(f"  Old {DAILY_CSV.name} not readable, rewriting it ({ex})")
	fresh = fresh.sort_index()
	fresh = fresh[fresh.index >= cut]
	front = [file_name(s) for s in ETF_SYMS + [BENCH_SYM] if file_name(s) in fresh.columns]       # ETFs + benchmark first, then the stocks
	cols = front + [c for c in fresh.columns if c not in front]
	fresh[cols].rename_axis("date").to_csv(DAILY_CSV)
	log(f"Saved price history → {ETF_DIR.name}/: {len(raw)} raw ETF candle files, {DAILY_CSV.name} {fresh.shape[0]} days × {len(cols)} columns")


# ─────────────────────────────────────────────────────────────────────────────
# --history: day-by-day prices of the ETFs + the stocks of the PF Builder's 1-year-back list
# ─────────────────────────────────────────────────────────────────────────────
def run_history(symbols_arg):
	"""PF Builder: day-by-day prices for the ETFs + the stocks somebody asked for. ONE shared store (etf_prices/) serves every user:
	market prices are the same for everyone, so nothing is kept per user and nothing is wiped. Each run
	  - fetches the stocks asked for that are not already saved with fresh prices (+ the ETFs / benchmark, always),
	  - MERGES them into daily_close.csv (other users' stocks stay),
	  - notes in requested.json when each stock was last asked for, and drops stocks nobody asked for in KEEP_ASKED_DAYS days
	    (or beyond MAX_STOCKS, oldest first) so the files do not grow for ever."""
	import re
	syms, asof = load_symbols()
	want = [x.strip().upper() for x in str(symbols_arg or "").replace(";", ",").split(",")]
	stocks = [x for x in dict.fromkeys(want) if x and x not in ETF_SYMS and re.fullmatch(r"[A-Z0-9&_\-]{1,24}", x)][:80]
	ETF_DIR.mkdir(parents=True, exist_ok=True)
	REQ = ETF_DIR / "requested.json"
	today = datetime.now(IST).date()

	# what is saved already, and who asked for what, when
	cache = None
	if DAILY_CSV.exists():
		try:
			cache = pd.read_csv(DAILY_CSV, index_col="date", low_memory=False)
			cache.index = cache.index.astype(str)
		except Exception as ex:
			log(f"  Saved {DAILY_CSV.name} not readable, starting it again ({ex})")
	asked = {}
	try:
		reg = json.loads(REQ.read_text(encoding="utf-8"))
		asked = {k: str(v) for k, v in (reg.get("asked") or {}).items()}
		for k in reg.get("requested", []):                               # older files: a plain list, no dates → counts as asked at the file's date
			asked.setdefault(k, str(reg.get("fetched_at", f"{today}"))[:10])
	except Exception:
		pass

	def fresh(sym):                                                      # saved and up to date (its last close is within 4 days of the data date)
		col = file_name(sym)
		if cache is None or col not in cache.columns:
			return False
		last = cache[col].last_valid_index()
		return bool(last) and (asof - date.fromisoformat(last)).days <= 4
	todo_stocks = [x for x in stocks if not fresh(x)]
	todo = [BENCH_SYM] + ETF_SYMS + todo_stocks
	etf_start = asof - timedelta(days=max(PERIODS.values()) + 380)       # ETFs: the same long history as always
	stk_start = asof - timedelta(days=BACK_DAYS + DAILY_LEAD + 10)      # stocks: only the backtest year
	end = datetime.now(IST).date()
	keep_from = asof - timedelta(days=BACK_DAYS + DAILY_LEAD)
	log(f"History run: {len(ETF_SYMS) + 1} ETF / benchmark + {len(todo_stocks)} stocks to fetch ({len(stocks) - len(todo_stocks)} already saved)  |  data date {asof}  |  closes from {keep_from}")
	log("Stocks: " + (", ".join(stocks) if stocks else "(none)"))
	tmap = build_token_map(scrip_master())
	api = Angel()
	raw, closes, failed = {}, {}, {}

	def one(s):
		if s not in tmap:
			return s, None, None, "no Angel token"
		try:
			r = []
			c = api.candles(tmap[s][1], etf_start if (s in ETF_SYMS or s == BENCH_SYM) else stk_start, end, r)
			if not c:
				raise RuntimeError("no candles")
			return s, r, [(d, v) for d, v in adjusted_closes(c) if d >= keep_from], None
		except Exception as ex:
			return s, None, None, str(ex)[:100]

	def collect(results):
		for s, r, cl, err in results:
			if err:
				failed[s] = err
				log(f"  {s:<14} FAILED  {err}")
			else:
				failed.pop(s, None)
				raw[s], closes[s] = r, cl
				log(f"  {s:<14} {len(r):>4} candles  last close {r[-1][4]}  ({r[-1][0]})")

	with ThreadPoolExecutor(max_workers=WORKERS) as pool:
		collect(list(pool.map(one, todo)))
	retry = [s for s in todo if s in failed and failed[s] != "no Angel token"]
	if retry:
		log(f"Retry pass: {len(retry)} symbols, waiting 20 s …")
		time.sleep(20)
		collect([one(s) for s in retry])
	if not any(s in closes for s in ETF_SYMS + [BENCH_SYM]):
		log("No ETF got prices: etf_prices/ is left as it was (nothing changed).")
		return 1
	core = set(ETF_SYMS + [BENCH_SYM])
	save_etf_history({k: v for k, v in raw.items() if k in core}, closes, asof)       # raw candles: ETFs + benchmark only; closes of everything fetched are MERGED in

	# who asked for what: stamp today on every stock of this request, then drop the stale / surplus ones
	for x in stocks:
		asked[x] = f"{today}"
	cutoff = f"{today - timedelta(days=KEEP_ASKED_DAYS)}"
	keep = sorted([k for k, v in asked.items() if v >= cutoff], key=lambda k: asked[k], reverse=True)[:MAX_STOCKS]
	dropped = [k for k in asked if k not in keep]
	asked = {k: asked[k] for k in keep}
	if DAILY_CSV.exists() and dropped:
		df = pd.read_csv(DAILY_CSV, index_col="date", low_memory=False)
		gone = [file_name(k) for k in dropped if file_name(k) in df.columns]
		if gone:
			df.drop(columns=gone).to_csv(DAILY_CSV)
			log(f"Dropped {len(gone)} stocks nobody asked for in {KEEP_ASKED_DAYS} days: {', '.join(gone)}")
	core_files = {file_name(k) for k in core}
	for f in (ETF_DIR / "raw").glob("*.csv"):                          # old versions kept a raw file per stock: only ETFs + benchmark have one now
		if f.stem not in core_files:
			f.unlink()
	REQ.write_text(json.dumps({
		"asof": f"{asof:%Y-%m-%d}", "fetched_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
		"asked": dict(sorted(asked.items())), "requested": sorted(asked)}, indent=1), encoding="utf-8")
	miss = [s for s in stocks if s not in closes and not fresh(s)]
	log(f"History done: {len(closes)} of {len(todo)} symbols fetched, {len(asked)} stocks kept in the shared store" + (f"  |  not available: {', '.join(miss)}" if miss else ""))
	return 0


# ─────────────────────────────────────────────────────────────────────────────
def prices_age_days():
	"""Age of prices.csv in days from its newest FETCHED_AT (file times are reset by every git checkout),
	falling back to the last git commit of the file. None = no usable prices.csv yet."""
	if not OUT_CSV.exists():
		return None
	try:
		t = pd.to_datetime(pd.read_csv(OUT_CSV, usecols=["FETCHED_AT"])["FETCHED_AT"], errors="coerce").max()
		if pd.notna(t):
			return (datetime.now(IST).replace(tzinfo=None) - t.to_pydatetime()).total_seconds() / 86400
	except Exception:
		pass
	try:
		import subprocess
		out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", OUT_CSV.name], cwd=BASE,
		                     capture_output=True, text=True, timeout=20).stdout.strip()
		if out:
			return (time.time() - int(out)) / 86400
	except Exception:
		pass
	return None


def is_due(force=False):
	age = prices_age_days()
	if force:
		log(f"prices.csv age: {'none yet' if age is None else f'{age:.1f} days'}  →  --force: fetching anyway")
		return True
	if age is None:
		log("prices.csv: none yet  →  fetch is due")
		return True
	try:                                                             # older file without the 3M … 1.5Y prices → refresh once
		if "MOM_NOW_R12" not in pd.read_csv(OUT_CSV, nrows=1).columns:
			log("prices.csv has no 3M … 1.5Y prices / momentum yet  →  fetch is due")
			return True
	except Exception:
		pass
	due = age >= MIN_AGE_DAYS
	log(f"prices.csv is {age:.1f} days old (weekly rule: fetch when ≥ {MIN_AGE_DAYS} days)  →  "
	    + ("fetch is due" if due else "skipping, still fresh"))
	return due


# ─────────────────────────────────────────────────────────────────────────────
def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--limit", type=int, default=0)
	ap.add_argument("--map-only", action="store_true")
	ap.add_argument("--force", action="store_true", help="fetch even if prices.csv is younger than MIN_AGE_DAYS")
	ap.add_argument("--is-due", action="store_true", help="only report: exit 0 if a fetch is due, 1 if not")
	ap.add_argument("--etf-only", action="store_true",
	                help="fetch only ETF_SYMS + the NIFTY 500 benchmark (cron.yml mode 4: ETF prices + momentum for the "
	                     "Portfolio Builder); all other rows in prices.csv stay as they are")
	ap.add_argument("--history", action="store_true",
	                help="PF Builder chart (builder_prices.yml): wipe etf_prices/, download the ETFs + NIFTY 500 + --symbols day by day, "
	                     "save them there. prices.csv is not touched")
	ap.add_argument("--symbols", default="", help="with --history: comma separated stock symbols (the page's 1-year-back value + momentum picks)")
	ap.add_argument("--part", type=int, choices=(1, 2, 3), default=None,
	                help="fetch only this third of the symbols (cron.yml: Saturday 02:00 / 04:00 / 06:00 IST); "
	                     "the other thirds' rows in prices.csv stay as they are")
	a = ap.parse_args()
	if a.history:
		return run_history(a.symbols)
	if a.part or a.etf_only:
		a.force = True                                               # a scheduled part / ETF run always fetches
	if a.is_due:
		return 0 if is_due(a.force) else 1
	if not a.map_only and not is_due(a.force):
		return 0                                                     # weekly rule: nothing to do

	syms, asof = load_symbols()
	back = asof - timedelta(days=BACK_DAYS)
	start = asof - timedelta(days=max(PERIODS.values()) + 380)       # oldest back date + its 52-week high / 12-month momentum
	log(f"{len(syms)} symbols  |  data date {asof}  |  BACK_DATE {back}  |  candles from {start}")

	tmap = build_token_map(scrip_master())
	if a.etf_only:                                                   # mode 4: just the builder's ETFs + benchmark
		todo = [BENCH_SYM] + list(ETF_SYMS)
		log(f"ETF-only: {len(todo)} symbols ({', '.join(todo)})")
	else:
		if a.part:                                                       # thirds in list order (3 lists first, then Others)
			n = len(syms)
			b = [0, -(-n // 3), -(-2 * n // 3), n]
			part_syms = syms[b[a.part - 1]:b[a.part]]
			log(f"Part {a.part} of 3: {len(part_syms)} symbols ({part_syms[0] if part_syms else '-'} … {part_syms[-1] if part_syms else '-'})")
		else:
			part_syms = syms
		todo = part_syms[:a.limit] if a.limit else part_syms
		if a.part in (None, 1):
			todo = todo + [BENCH_SYM] + [e for e in ETF_SYMS if e not in todo]   # benchmark + builder ETFs: once, with part 1
	missing = [s for s in todo if s not in tmap]
	log(f"Mapped to Angel tokens: {len(todo) - len(missing)} / {len(todo)}"
	    + (f"  (not found: {', '.join(missing[:15])}{' …' if len(missing) > 15 else ''})" if missing else ""))
	if a.map_only:
		return 0

	if OUT_CSV.exists():                                             # 1. archive the current prices.csv first …
		try:
			import archive_csv
			archive_csv.snapshot(OUT_CSV)                            # → old_csv.zip / DDMMYY_prices.csv
		except Exception as ex:
			log(f"  Snapshot of {OUT_CSV.name} to old_csv.zip failed (continuing): {ex}")

	old = {}                                                         # 2. … then update it
	if OUT_CSV.exists():                                             # keep last good row if a fetch fails
		try:
			old = {r["SYMBOL"]: r for r in pd.read_csv(OUT_CSV).to_dict("records")}
		except Exception:
			pass

	api = Angel()
	etf_raw, etf_close = {}, {}                                      # raw candles (ETFs + benchmark only) / daily closes (every symbol) → etf_prices/
	keep_from = asof - timedelta(days=BACK_DAYS + DAILY_LEAD)
	now_s = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
	end = datetime.now(IST).date()
	n_all = len(todo)

	def fetch_one(i, s):
		"""→ (row, log line, ok?) for one symbol."""
		base = {"SYMBOL": s, "BACK_DATE": f"{back:%Y-%m-%d}", "FETCHED_AT": now_s}
		if s not in tmap:
			return ({**base, "STATUS": "NO ANGEL TOKEN"},
			        f"  [{i:>4}/{n_all}] {s:<14} no Angel token (not in scrip master as NSE EQ/BE/BZ/SM/ST)", None)
		asym, tok = tmap[s]
		base.update({"ANGEL_SYMBOL": asym, "ANGEL_TOKEN": tok})
		tag = f"  [{i:>4}/{n_all}] {s:<14} {asym:<18} token {tok:<8}"
		try:
			keep = s in ETF_SYMS or s == BENCH_SYM
			raw = [] if keep else None
			c = api.candles(tok, start, end, raw)
			if not c:
				raise RuntimeError("no candles")
			if keep:                                                 # ETFs + benchmark: raw candles + last 365 days of closes → etf_prices/
				etf_raw[s], etf_close[s] = raw, [(d, v) for d, v in adjusted_closes(c) if d >= keep_from]
			row = summarise(c, asof, with_px=(s in ETF_SYMS or s == BENCH_SYM))
			p1 = f"1Y {row['PRICE_1Y']:>10,.2f} ({row['PRICE_1Y_DATE']})" if row.get("PRICE_1Y") is not None else "1Y        n/a"
			ca = f"  split/bonus: {row['CORP_ACTIONS']}" if row.get("CORP_ACTIONS") else ""
			return ({**base, **row},
			        f"{tag} LTP {row['LTP']:>10,.2f} ({row['LTP_DATE']})  {p1}  {len(c)} candles  {row['STATUS']}{ca}", True)
		except Exception as ex:
			return ({**base, "STATUS": f"FAILED: {str(ex)[:80]}"}, f"{tag} FAILED  {str(ex)[:100]}", False)

	t0 = time.monotonic()
	results, done = {}, 0
	log(f"Fetching {n_all} symbols  |  {WORKERS} workers  |  max {RATE} requests/s  |  3 requests per symbol")
	with ThreadPoolExecutor(max_workers=WORKERS) as pool:
		futs = {pool.submit(fetch_one, i, s): i for i, s in enumerate(todo, 1)}
		for f in as_completed(futs):
			row, line, okk = f.result()
			results[futs[f]] = (row, okk)
			log(line)
			done += 1
			if done % 100 == 0:
				el = time.monotonic() - t0
				nok = sum(1 for r, o in results.values() if o)
				log(f"  ── {done}/{n_all} done in {el/60:.1f} min  |  ok {nok}  |  rate-limit replies so far {api.n_rate}"
				    f"  |  ~{el / done * (n_all - done) / 60:.1f} min left")

	retry = sorted(i for i, (r, o) in results.items() if o is False)
	if RETRY_PASS and retry:
		log(f"Retry pass: {len(retry)} symbols failed, waiting 20 s then retrying one by one …")
		time.sleep(20)
		for i in retry:
			row, line, okk = fetch_one(i, todo[i - 1])
			results[i] = (row, okk)
			log("  retry" + line[1:])

	rows, n_ok, n_fail = [], 0, 0
	for i in range(1, n_all + 1):                                    # keep the original order in the CSV
		row, okk = results[i]
		if okk:
			n_ok += 1
		else:
			n_fail += 1
			prev = old.get(row["SYMBOL"])
			if okk is False and prev is not None and str(prev.get("STATUS", "")).startswith("OK"):
				row = {**prev, "STATUS": f"STALE (fetch failed {now_s[:10]})"}      # keep last good prices
		rows.append(row)
	if a.part or a.etf_only:                                         # one third / the ETFs fetched: keep every other row
		fresh = {r["SYMBOL"]: r for r in rows}
		keep = list(dict.fromkeys(s for s in syms + [BENCH_SYM] + ETF_SYMS if s in fresh or s in old))
		rows = [fresh.get(s) or old[s] for s in keep]
		log(f"{'ETF-only' if a.etf_only else f'Part {a.part}'}: {len(fresh)} rows fetched now, "
		    f"{len(rows) - len(fresh)} kept from earlier parts / runs")
	el = time.monotonic() - t0
	log(f"Fetch time {el/60:.1f} min ({el / max(n_all, 1):.2f} s per symbol)  |  rate-limit replies: {api.n_rate}"
	    f"  |  final pace {api.limiter.rate:.2f} requests/s")

	cols = ["SYMBOL", "ANGEL_SYMBOL", "ANGEL_TOKEN", "LTP", "LTP_DATE", "HIGH_52W_NOW", "LISTED_FROM"]
	for k in PERIODS:
		cols += [f"PRICE_{k}", f"PRICE_{k}_DATE", f"PRICE_{k}_ADJ", f"HIGH_52W_{k}"]
	for k in ["NOW"] + list(PERIODS):
		cols += [f"MOM_{k}_R3", f"MOM_{k}_R6", f"MOM_{k}_R12"]
	for k in range(1, 13):
		cols += [f"MOM_M{k}_R3", f"MOM_M{k}_R6", f"MOM_M{k}_R12"]
	cols += ["PX_MONTHLY", "ADJ_FACTOR", "CORP_ACTIONS", "BACK_DATE", "STATUS", "FETCHED_AT"]
	out = pd.DataFrame(rows).reindex(columns=cols)
	out.to_csv(OUT_CSV, index=False)
	if etf_close:                                                    # raw candles + 365 days of adjusted closes (ETFs + benchmark) → the repo
		try:
			save_etf_history(etf_raw, etf_close, asof)
		except Exception as ex:
			log(f"  [WARN] etf_prices/ not saved: {ex}")
	n_ca = int(out["CORP_ACTIONS"].fillna("").str.len().gt(0).sum())
	log(f"Done → {OUT_CSV.name}: {n_ok} ok, {n_fail} failed / not mapped, {n_ca} with a split / bonus detected")
	return 0 if n_ok else 1


if __name__ == "__main__":
	sys.exit(main())
