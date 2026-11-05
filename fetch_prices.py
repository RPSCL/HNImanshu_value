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
    MOM_NOW_R1, MOM_M<k>_R1               1-month return (%) as of today / each month point (the 1Y adj backtest's momentum score)
    PX_MONTHLY                            every symbol: split-adjusted close at k = 0 (latest) … 12, '|' separated
    PX_WEEKLY                             every symbol: split-adjusted close 0, 7, 14 … 364 days back (53 points), '|' separated
    MOM_CAL, CIRC_CAL                     every symbol, calendar months: first session of this month and the 12 before it →
                                          "YYYY-MM:date,adj close,1M,3M,6M,12-1M %" / "YYYY-MM:circuit days" ('|' separated)
    CIRC_M<k>                             circuit-locked days in the 250 sessions up to each month point k = 1 … 12
    ADJ_FACTOR, CORP_ACTIONS              detected split / bonus factor after BACK_DATE, and every event found
    BACK_DATE, STATUS, FETCHED_AT

BACK_DATE = (the day the prices are fetched, Friday night) − 365 days; fundamentals.py matches it within ±7 days.

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
    python fetch_prices.py --part 1      # weekly part 1 = the 3 lists (+ NIFTY 500 + ETFs) · --part 2 / 3 / 4 = Others in thirds
    python fetch_prices.py --history --symbols A,B,C   # wipe etf_prices/, then ETFs + NIFTY 500 + these stocks, day by day (PF Builder chart)
    python fetch_prices.py --yearly            # prices_yearly.csv: split-adjusted close on 1 July of every year since 2019:
                                               # the 3 lists (~765) + NIFTY 500, ~40 min
    python fetch_prices.py --yearly --part 2   # … Others, second third (~620, ~35 min). fcf_prices.yml, started by the Oracle crontab
    python fetch_prices.py --etf-only    # ONLY the Portfolio Builder ETFs + NIFTY 500 benchmark (cron.yml mode 4);
                                         # every other row of prices.csv stays as it is
"""

import argparse
import json
import math
import os
import re
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
PERIODS     = {"1M": 30, "3M": 91, "6M": 182, "9M": 273, "1Y": 365, "1_25Y": 456, "1_5Y": 548}   # must match fundamentals.PERIODS
RATE        = 2.5                         # max candle requests per second, shared by all workers (Angel throttles ~3/s)
WORKERS     = 3                           # parallel fetches; network delay overlaps, the rate limit stays global
CHUNK_DAYS  = 380                         # ~2.5 years in 3 calls (~255 rows each, under the 500-row cap some users hit)
TIME_BUDGET_MIN = 110                     # auto stop: no new symbols after 110 min, then prices.csv is SAVED (the rest keep their
                                          # earlier rows; the next run fetches only those). GitHub step limit must be above it (130)
FRESH_HOURS = float(os.environ.get("PRICE_FRESH_HOURS", "20"))       # a symbol fetched OK within this many hours (new columns
                                          # included) is NOT fetched again: run the same part again and only the rest are fetched.
                                          # --refetch fetches everything anyway
CHECKPOINT_EVERY = 50                     # prices.csv is written every 50 symbols too, so even a run killed mid-way keeps its work
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


def n_list_symbols(syms):
	"""How many of load_symbols()'s list (3 lists first, then Others) belong to NIFTY 500 / Smallcap 500 / Microcap 250."""
	lists = set()
	for f in CSV_FILES:
		p = BASE / f
		if p.exists() and "others" not in f.lower():
			lists |= {str(s).strip().upper() for s in pd.read_csv(p, usecols=["SYMBOL"])["SYMBOL"].dropna()}
	n = 0
	while n < len(syms) and syms[n] in lists:
		n += 1
	return n


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
	tmp = CACHE_DIR / "scrip_master_download.tmp"
	for attempt in range(1, 5):                                            # Angel's server sometimes cuts the ~36 MB download half-way
		try:
			with requests.get(SCRIP_URL, timeout=120, stream=True) as r:      # straight to disk: never the whole ~40 MB in memory
				r.raise_for_status()
				with open(tmp, "wb") as f:
					for chunk in r.iter_content(1 << 20):
						f.write(chunk)
			break
		except requests.RequestException as e:
			log(f"  download {attempt} of 4 broke ({type(e).__name__}); " + ("trying again" if attempt < 4 else "giving up"))
			if attempt < 4:
				time.sleep(10 * attempt)                                         # 10 s, 20 s, 30 s
				continue
			older = sorted(CACHE_DIR.glob("scrip_master_*.json"))            # an earlier day's copy (tokens rarely change)
			if older:
				log(f"Angel scrip master: using the older saved copy {older[-1].name}")
				return json.loads(older[-1].read_text())
			raise
	try:
		import ijson                                                       # one item at a time: fine on a 1 GB server (Oracle free tier)
		with open(tmp, "rb") as f:
			data = [{k: m.get(k) for k in SCRIP_KEYS} for m in ijson.items(f, "item") if m.get("exch_seg") == "NSE"]
	except ImportError:                                                    # GitHub runners (plenty of memory): plain json
		data = [{k: m.get(k) for k in SCRIP_KEYS} for m in json.loads(tmp.read_text()) if m.get("exch_seg") == "NSE"]
	tmp.unlink()
	log(f"Angel scrip master: {len(data)} NSE rows kept")
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
		self.limiter, self.login_lock, self.n_rate, self.n_net = RateLimiter(RATE), threading.Lock(), 0, 0
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

	def _call(self, token, s, e, tries=8):
		params = {"exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
		          "fromdate": f"{s:%Y-%m-%d} 09:15", "todate": f"{e:%Y-%m-%d} 15:30"}
		msg = ""
		for i in range(tries):
			self.limiter.wait()                                      # global pacing across all workers
			try:
				res = self.api.getCandleData(params)
			except Exception as ex:                                  # network / throttling (library raises on non-JSON)
				res = {"status": False, "message": str(ex)[:400]}   # full text: "Access denied because of exceeding access rate"
			if res and res.get("status"):
				return res.get("data") or []
			msg = str((res or {}).get("message", "")) + " " + str((res or {}).get("errorcode", ""))
			low = msg.lower()
			if "access rate" in low or "access denied" in low or "exceeding" in low or "too many" in low or "AB1019" in msg:
				self.n_rate += 1
				self.limiter.cooldown(min(2.0 * (i + 1), 10.0))      # "too fast": everyone pauses 2, 4, 6 … 10 s, then the SAME call again
			elif "timed out" in low or "max retries" in low or "connection" in low:   # Angel not answering: back off longer
				self.n_net += 1
				time.sleep(3.0 * (i + 1))
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


CIRCUIT_DAYS = 250                                                   # trading sessions looked at for circuit days (≈ 1 year)

def circuit_days(raw, upto=None):
	"""Days in the last CIRCUIT_DAYS sessions when the price was locked all day: open = high = low = close (stuck at the upper or
	lower circuit, nothing traded at any other price). raw = Angel's candles (date, open, high, low, close, volume), oldest first.
	upto (a date): the 250 sessions up to that day (a back date: circuit days as known then), else up to the latest."""
	if upto is not None:
		u = f"{upto:%Y-%m-%d}"
		raw = [x for x in raw if str(x[0])[:10] <= u]
		if not raw:
			return None                                              # not listed yet then
	n = 0
	for _, o, h, l, c, _v in raw[-CIRCUIT_DAYS:]:
		try:
			o, h, l, c = float(o), float(h), float(l), float(c)
		except (TypeError, ValueError):
			continue
		if o > 0 and o == h == l == c:
			n += 1
	return n


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
		            f"MOM_M{k}_R12": pct(px(d + 30), px(d + 365)), f"MOM_M{k}_R1": pct(now_p, px(d + 30))})
	row["MOM_NOW_R1"] = pct(last[1], px(30))
	if with_px:                                                      # the monthly price path (k = 0 … 12): every symbol (1Y adj backtest)
		path = [last[1]] + [px(MONTH_DAYS[k]) for k in range(1, 13)]
		row["PX_MONTHLY"] = "|".join("" if v is None else f"{v:.4f}" for v in path)
		wk = [last[1]] + [px(7 * w) for w in range(1, 53)]           # … and week by week (w = 0 … 52 weeks back): weekly Value re-picks
		row["PX_WEEKLY"] = "|".join("" if v is None else f"{v:.4f}" for v in wk)
	# calendar months (1Y adj backtest): the FIRST trading session of each month, this month and the 12 before it.
	# "YYYY-MM:session date,adjusted close,1M,3M,6M,12-1M return %" per month, '|' separated (same returns as above, as of that session)
	def px_on(t):                                                    # adjusted close on / before day t (within 10 days)
		b = [x for x in adj if x[0] <= t]
		return b[-1][1] if b and (t - b[-1][0]).days <= 10 else None
	m0, cal = asof.replace(day=1), []
	for i in range(12, -1, -1):
		y, mo = m0.year, m0.month - i
		while mo <= 0:
			mo += 12; y -= 1
		first = date(y, mo, 1)
		aft = next((x for x in adj if first <= x[0] <= asof), None)
		if not aft or (aft[0] - first).days > 10:                    # not listed yet / suspended then
			cal.append(f"{y}-{mo:02d}:")
			continue
		d1, p1 = aft[0], aft[1]
		f = lambda v: "" if v is None else str(v)
		cal.append(f"{y}-{mo:02d}:{d1:%Y-%m-%d},{p1:.4f},{f(pct(p1, px_on(d1 - timedelta(days=30))))},{f(pct(p1, px_on(d1 - timedelta(days=91))))},"
		           f"{f(pct(p1, px_on(d1 - timedelta(days=182))))},{f(pct(px_on(d1 - timedelta(days=30)), px_on(d1 - timedelta(days=365))))}")
	row["MOM_CAL"] = "|".join(cal)
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
# ─────────────────────────────────────────────────────────────────────────────
# --yearly: one price a year (1 July) since 2019 → prices_yearly.csv (owner-earnings backtest on the Valuation page)
# ─────────────────────────────────────────────────────────────────────────────
YEARLY_CSV   = BASE / "prices_yearly.csv"
YEARLY_FROM  = 2019                       # first buy date: 1 July 2019 (needs owner earnings for FY2018 + FY2019)
YEARLY_M, YEARLY_D = 7, 1                 # 1 July: the year's annual results (to March) are all out by the end of May


def run_yearly(limit=0, symbols_arg="", part=0):
	"""Daily candles from June 2019 to today (~8 requests a symbol), split / bonus adjusted to TODAY's share basis exactly
	like summarise(), then ONE close per year: the last trading day on / before 1 July.
	  part 0 = the 3 lists (NIFTY 500 + Smallcap 500 + Microcap 250, ~765 stocks) + the NIFTY 500 index  (~40 min)
	  part 1 / 2 / 3 = a third of the Others list each (~620 stocks, ~35 min)
	Each part only rewrites its own rows; every other row of prices_yearly.csv stays.
	Columns: SYMBOL, PX_2019 … PX_<this year> (adjusted), LTP_ADJ, LTP_DATE, LISTED_FROM, CORP_ACTIONS, STATUS, FETCHED_AT.
	A symbol that fails keeps its last good row."""
	main_syms = []
	for f in CSV_FILES[:3]:
		pth = BASE / f
		if pth.exists():
			main_syms += [str(x).strip().upper() for x in pd.read_csv(pth, usecols=["SYMBOL"])["SYMBOL"].dropna()]
	main_syms = list(dict.fromkeys(main_syms))
	if part:
		all_syms, _ = load_symbols()                                     # 3 lists + the Others the site shows
		oth = [x for x in all_syms if x not in set(main_syms)]
		n = len(oth)
		b = [0, -(-n // 3), -(-2 * n // 3), n]
		syms = oth[b[part - 1]:b[part]]
		log(f"Yearly prices, Others part {part} of 3: {len(syms)} of {n} stocks")
	else:
		syms = main_syms
		log(f"Yearly prices, the 3 lists: {len(syms)} stocks + NIFTY 500")
	if symbols_arg:
		want = [x for x in symbols_arg.upper().split(",") if x]
		syms = [x for x in syms if x in want]
	if limit:
		syms = syms[:limit]
	todo = ([] if part else [BENCH_SYM]) + syms
	today = datetime.now(IST).date()
	years = [y for y in range(YEARLY_FROM, today.year + 1) if date(y, YEARLY_M, YEARLY_D) <= today]
	start = date(YEARLY_FROM, YEARLY_M, YEARLY_D) - timedelta(days=20)
	tmap = build_token_map(scrip_master())
	log(f"Yearly prices: {len(todo)} symbols  |  1 July of {years[0]} … {years[-1]}  |  candles {start} … {today}")
	old = {}
	if YEARLY_CSV.exists():
		try:
			old = {r["SYMBOL"]: r for r in pd.read_csv(YEARLY_CSV).to_dict("records")}
		except Exception:
			pass
	api = Angel()
	now_s = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
	n_all = len(todo)

	def one(i, s):
		base = {"SYMBOL": s, "FETCHED_AT": now_s}
		if s not in tmap:
			return {**base, "STATUS": "NO ANGEL TOKEN"}, f"  [{i:>4}/{n_all}] {s:<14} no Angel token", None
		try:
			c = api.candles(tmap[s][1], start, today)
			if not c:
				raise RuntimeError("no candles")
			adj = adjusted_closes(c)
			row = {**base, "LTP_ADJ": round(adj[-1][1], 4), "LTP_DATE": f"{adj[-1][0]:%Y-%m-%d}", "LISTED_FROM": f"{c[0][0]:%Y-%m-%d}",
			       "CORP_ACTIONS": "; ".join(f"{d:%Y-%m-%d} ×{f:.4g} ({lab})" for d, f, lab in corporate_actions(c)), "STATUS": "OK"}
			for y in years:
				t = date(y, YEARLY_M, YEARLY_D)
				b = [x for x in adj if x[0] <= t]
				if b and (t - b[-1][0]).days <= 10:                            # not listed yet (or no trade for 10 days): left empty
					row[f"PX_{y}"] = round(b[-1][1], 4)
			got = sum(1 for y in years if f"PX_{y}" in row)
			return row, f"  [{i:>4}/{n_all}] {s:<14} {got}/{len(years)} years  now {row['LTP_ADJ']:,.2f}  {len(c)} candles", True
		except Exception as ex:
			return {**base, "STATUS": f"FAILED: {str(ex)[:80]}"}, f"  [{i:>4}/{n_all}] {s:<14} FAILED  {str(ex)[:100]}", False

	t0, res = time.monotonic(), {}
	with ThreadPoolExecutor(max_workers=WORKERS) as pool:
		futs = {pool.submit(one, i, s): i for i, s in enumerate(todo, 1)}
		for k, f in enumerate(as_completed(futs), 1):
			row, line, ok = f.result()
			res[futs[f]] = (row, ok)
			log(line)
			if k % 100 == 0:
				el = time.monotonic() - t0
				log(f"  ── {k}/{n_all} in {el / 60:.1f} min  |  ~{el / k * (n_all - k) / 60:.1f} min left  |  rate-limit replies {api.n_rate}")
	for i in sorted(i for i, (r, o) in res.items() if o is False):       # one slow retry
		time.sleep(1)
		row, line, ok = one(i, todo[i - 1])
		res[i] = (row, ok)
		log("  retry" + line[1:])
	rows = []
	for i in range(1, n_all + 1):
		row, ok = res[i]
		prev = old.get(row["SYMBOL"])
		if not ok and prev is not None and str(prev.get("STATUS", "")).startswith("OK"):
			row = {**prev, "STATUS": f"OK (kept: fetch failed {now_s[:10]})"}
		rows.append(row)
	fresh = {r["SYMBOL"]: r for r in rows}                              # this part's rows; every other saved row stays
	rows = list(fresh.values()) + [r for k, r in old.items() if k not in fresh]
	years = sorted(set(years) | {int(c[3:]) for r in rows for c in r if re.fullmatch(r"PX_20\d\d", str(c))})
	cols = ["SYMBOL"] + [f"PX_{y}" for y in years] + ["LTP_ADJ", "LTP_DATE", "LISTED_FROM", "CORP_ACTIONS", "STATUS", "FETCHED_AT"]
	pd.DataFrame(rows).reindex(columns=cols).to_csv(YEARLY_CSV, index=False)
	n_ok = sum(1 for r in rows if str(r.get("STATUS", "")).startswith("OK"))
	log(f"prices_yearly.csv: {n_ok} / {len(rows)} OK  |  {(time.monotonic() - t0) / 60:.1f} min")
	return 0 if n_ok else 1


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
	ap.add_argument("--refetch", action="store_true", help="fetch every symbol again, also those fetched in the last FRESH_HOURS")
	ap.add_argument("--is-due", action="store_true", help="only report: exit 0 if a fetch is due, 1 if not")
	ap.add_argument("--etf-only", action="store_true",
	                help="fetch only ETF_SYMS + the NIFTY 500 benchmark (cron.yml mode 4: ETF prices + momentum for the "
	                     "Portfolio Builder); all other rows in prices.csv stay as they are")
	ap.add_argument("--history", action="store_true",
	                help="PF Builder chart (builder_prices.yml): wipe etf_prices/, download the ETFs + NIFTY 500 + --symbols day by day, "
	                     "save them there. prices.csv is not touched")
	ap.add_argument("--symbols", default="", help="with --history: comma separated stock symbols (the page's 1-year-back value + momentum picks)")
	ap.add_argument("--part", type=int, choices=(1, 2, 3, 4), default=None,
	                help="weekly prices in 4 parts (cron.yml, Friday 20:00 / 21:00 / 22:00 / 23:00 IST): 1 = the 3 lists "
	                     "(+ NIFTY 500 + PF Builder ETFs), 2 / 3 / 4 = Others in thirds; the other parts' rows stay as they are. "
	                     "With --yearly: 1 / 2 / 3 = Others in thirds (no part = the 3 lists)"),
	ap.add_argument("--yearly", action="store_true",
	                help="prices_yearly.csv: split-adjusted close on 1 July of each year since 2019 (3 lists + NIFTY 500), "
	                     "for the Valuation page's owner-earnings backtest. --limit / --symbols for a partial run")
	a = ap.parse_args()
	if a.yearly:
		return run_yearly(a.limit, a.symbols, a.part or 0)
	if a.history:
		return run_history(a.symbols)
	if a.part or a.etf_only:
		a.force = True                                               # a scheduled part / ETF run always fetches
	if a.is_due:
		return 0 if is_due(a.force) else 1
	if not a.map_only and not is_due(a.force):
		return 0                                                     # weekly rule: nothing to do

	syms, data_date = load_symbols()
	# Back dates (1M … 1.5Y) count from the day the prices are fetched (Friday night), not from the last scrape: the
	# scrape now runs on Sunday, after the prices, and the site accepts a price within ±7 days of the date it stands for
	# (fundamentals.PRICE_TOL_DAYS), so Friday-based prices fit both the old and the new data date.
	asof = datetime.now(IST).date()
	if asof < data_date:
		asof = data_date
	back = asof - timedelta(days=BACK_DAYS)
	start = asof - timedelta(days=max(PERIODS.values()) + 380)       # oldest back date + its 52-week high / 12-month momentum
	log(f"{len(syms)} symbols  |  prices as of {asof} (data date {data_date})  |  BACK_DATE {back}  |  candles from {start}")

	tmap = build_token_map(scrip_master())
	if a.etf_only:                                                   # mode 4: just the builder's ETFs + benchmark
		todo = [BENCH_SYM] + list(ETF_SYMS)
		log(f"ETF-only: {len(todo)} symbols ({', '.join(todo)})")
	else:
		if a.part:                                                       # part 1 = the 3 lists (NIFTY 500 + Smallcap 500 + Microcap 250);
			nl = n_list_symbols(syms)                                    # parts 2 / 3 / 4 = Others in thirds
			oth = syms[nl:]
			n = len(oth)
			b = [0, -(-n // 3), -(-2 * n // 3), n]
			part_syms = syms[:nl] if a.part == 1 else oth[b[a.part - 2]:b[a.part - 1]]
			log(f"Part {a.part} of 4 ({'the 3 lists' if a.part == 1 else 'Others, ' + ['first', 'middle', 'last'][a.part - 2] + ' third'}): "
			    f"{len(part_syms)} symbols ({part_syms[0] if part_syms else '-'} … {part_syms[-1] if part_syms else '-'})")
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

	# fetched OK in the last FRESH_HOURS with the current columns → not fetched again (a run cut short is simply run again)
	cut = (datetime.now(IST) - timedelta(hours=FRESH_HOURS)).strftime("%Y-%m-%d %H:%M")
	def recent(s):
		o = old.get(s) or {}
		px = str(o.get("PX_MONTHLY", "") or "")
		return (str(o.get("STATUS", "")).startswith("OK") and str(o.get("FETCHED_AT", "")) >= cut
		        and px not in ("", "nan") and "MOM_M1_R1" in o and str(o.get("PX_WEEKLY", "") or "") not in ("", "nan")
		        and str(o.get("MOM_CAL", "") or "") not in ("", "nan"))
	if not a.etf_only and not a.refetch:
		n0 = len(todo)
		todo = [s for s in todo if not recent(s)]
		if n0 - len(todo):
			log(f"{n0 - len(todo)} of {n0} symbols were fetched in the last {FRESH_HOURS:g} h: kept as they are (--refetch to fetch them again)")
		if not todo:
			log("Nothing to fetch: every symbol of this part is recent.")
			return 0

	api = Angel()
	etf_raw, etf_close = {}, {}                                      # raw candles (ETFs + benchmark only) / daily closes (every symbol) → etf_prices/
	keep_from = asof - timedelta(days=BACK_DAYS + DAILY_LEAD)
	now_s = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
	end = datetime.now(IST).date()
	n_all = len(todo)

	def fetch_one(i, s):
		"""→ (row, log line, ok?) for one symbol. ok = "skip" once the time budget is used up (the old row is kept)."""
		base = {"SYMBOL": s, "BACK_DATE": f"{back:%Y-%m-%d}", "FETCHED_AT": now_s}
		if time.monotonic() - t0 > TIME_BUDGET_MIN * 60:
			return (base, f"  [{i:>4}/{n_all}] {s:<14} skipped: time budget ({TIME_BUDGET_MIN:.0f} min) used, earlier row kept", "skip")
		if s not in tmap:
			return ({**base, "STATUS": "NO ANGEL TOKEN"},
			        f"  [{i:>4}/{n_all}] {s:<14} no Angel token (not in scrip master as NSE EQ/BE/BZ/SM/ST)", None)
		asym, tok = tmap[s]
		base.update({"ANGEL_SYMBOL": asym, "ANGEL_TOKEN": tok})
		tag = f"  [{i:>4}/{n_all}] {s:<14} {asym:<18} token {tok:<8}"
		try:
			keep = s in ETF_SYMS or s == BENCH_SYM
			raw = []                                                 # every symbol: its raw candles give the circuit days
			c = api.candles(tok, start, end, raw)
			if not c:
				raise RuntimeError("no candles")
			circ = circuit_days(raw)
			if keep:                                                 # ETFs + benchmark: raw candles + last 365 days of closes → etf_prices/
				etf_raw[s], etf_close[s] = raw, [(d, v) for d, v in adjusted_closes(c) if d >= keep_from]
			row = summarise(c, asof, with_px=True)
			row["CIRCUIT_250"] = circ                                # weekly (Friday price parts): circuit-locked days in the last 250 sessions
			for k, days in PERIODS.items():                          # … and in the 250 sessions before each back date (backtests: "then")
				cb = circuit_days(raw, asof - timedelta(days=days))
				if cb is not None:
					row[f"CIRCUIT_250_{k}"] = cb
			for k in range(1, 13):                                   # … and at each month point (1Y adj backtest)
				cb = circuit_days(raw, asof - timedelta(days=MONTH_DAYS[k]))
				if cb is not None:
					row[f"CIRC_M{k}"] = cb
			cc = []                                                  # … and on each month's first session (MOM_CAL): "YYYY-MM:days|…"
			for e in str(row.get("MOM_CAL") or "").split("|"):
				key, _, rest = e.partition(":")
				cb = circuit_days(raw, date.fromisoformat(rest.split(",")[0])) if rest else None
				cc.append(f"{key}:{'' if cb is None else cb}")
			row["CIRC_CAL"] = "|".join(cc)
			p1 = f"1Y {row['PRICE_1Y']:>10,.2f} ({row['PRICE_1Y_DATE']})" if row.get("PRICE_1Y") is not None else "1Y        n/a"
			ca = f"  split/bonus: {row['CORP_ACTIONS']}" if row.get("CORP_ACTIONS") else ""
			return ({**base, **row},
			        f"{tag} LTP {row['LTP']:>10,.2f} ({row['LTP_DATE']})  {p1}  {len(c)} candles  circuit {circ}d  {row['STATUS']}{ca}", True)
		except Exception as ex:
			return ({**base, "STATUS": f"FAILED: {str(ex)[:80]}"}, f"{tag} FAILED  {str(ex)[:100]}", False)

	def save(final):
		"""results so far → prices.csv (symbols not fetched yet keep their earlier rows)."""
		rows, n_ok, n_fail = [], 0, 0
		for i in range(1, n_all + 1):                                # keep the original order in the CSV
			row, okk = results.get(i, ({"SYMBOL": todo[i - 1]}, "skip"))
			if okk == "skip":                                        # not fetched (yet / time budget): keep the earlier row as it was
				prev = old.get(row["SYMBOL"])
				if prev is not None:
					rows.append(prev)
				continue
			if okk:
				n_ok += 1
			else:
				n_fail += 1
				prev = old.get(row["SYMBOL"])
				if okk is False and prev is not None and str(prev.get("STATUS", "")).startswith("OK"):
					row = {**prev, "STATUS": f"STALE (fetch failed {now_s[:10]})"}      # keep last good prices
			rows.append(row)
		fresh = {r["SYMBOL"]: r for r in rows}                       # every other row (other parts, recent ones) stays
		keep = list(dict.fromkeys(x for x in syms + [BENCH_SYM] + ETF_SYMS if x in fresh or x in old))
		rows = [fresh.get(x) or old[x] for x in keep]
		if final:
			log(f"{'ETF-only' if a.etf_only else f'Part {a.part}' if a.part else 'Run'}: {n_ok + n_fail} rows fetched now, "
			    f"{len(rows) - n_ok - n_fail} kept from earlier parts / runs")
		cols = ["SYMBOL", "ANGEL_SYMBOL", "ANGEL_TOKEN", "LTP", "LTP_DATE", "HIGH_52W_NOW", "LISTED_FROM"]
		for k in PERIODS:
			cols += [f"PRICE_{k}", f"PRICE_{k}_DATE", f"PRICE_{k}_ADJ", f"HIGH_52W_{k}"]
		for k in ["NOW"] + list(PERIODS):
			cols += [f"MOM_{k}_R3", f"MOM_{k}_R6", f"MOM_{k}_R12"]
		for k in range(1, 13):
			cols += [f"MOM_M{k}_R3", f"MOM_M{k}_R6", f"MOM_M{k}_R12", f"MOM_M{k}_R1"]
		cols += ["MOM_NOW_R1", "PX_MONTHLY", "PX_WEEKLY", "MOM_CAL", "CIRC_CAL", "ADJ_FACTOR", "CORP_ACTIONS", "CIRCUIT_250"] + [f"CIRCUIT_250_{k}" for k in PERIODS] + [f"CIRC_M{k}" for k in range(1, 13)] + ["BACK_DATE", "STATUS", "FETCHED_AT"]
		out = pd.DataFrame(rows).reindex(columns=cols)
		tmp = OUT_CSV.with_suffix(".tmp")
		out.to_csv(tmp, index=False)
		tmp.replace(OUT_CSV)                                         # never a half-written prices.csv
		return out, n_ok, n_fail

	t0 = time.monotonic()
	results, done = {}, 0
	log(f"Fetching {n_all} symbols  |  {WORKERS} workers  |  max {RATE} requests/s  |  3 requests per symbol  |  time budget {TIME_BUDGET_MIN:g} min")
	with ThreadPoolExecutor(max_workers=WORKERS) as pool:
		futs = {pool.submit(fetch_one, i, s): i for i, s in enumerate(todo, 1)}
		for f in as_completed(futs):
			row, line, okk = f.result()
			results[futs[f]] = (row, okk)
			done += 1
			el = max(time.monotonic() - t0, 1e-6)
			log(f"{line}  [{int(el // 60):02d}:{int(el % 60):02d}  {done / el * 60:.0f}/min]")
			if done % CHECKPOINT_EVERY == 0 and done < n_all:
				try:
					save(False)
					log(f"  ── checkpoint: prices.csv saved ({done}/{n_all})")
				except Exception as ex:
					log(f"  [WARN] checkpoint not saved: {ex}")
			if done % 100 == 0:
				el = time.monotonic() - t0
				nok = sum(1 for r, o in results.values() if o is True)
				log(f"  ── {done}/{n_all} done in {el/60:.1f} min  |  ok {nok}  |  rate-limit replies so far {api.n_rate}"
				    f"  |  ~{el / done * (n_all - done) / 60:.1f} min left")

	retry = sorted(i for i, (r, o) in results.items() if o is False)
	n_skip = sum(1 for r, o in results.values() if o == "skip")
	if n_skip:
		log(f"TIME BUDGET: {n_skip} of {n_all} symbols not fetched (earlier rows kept). Run this part again to fetch them first.")
	if RETRY_PASS and retry and time.monotonic() - t0 < TIME_BUDGET_MIN * 60:
		log(f"Retry pass: {len(retry)} symbols failed, waiting 20 s then retrying one by one …")
		time.sleep(20)
		for i in retry:
			row, line, okk = fetch_one(i, todo[i - 1])
			results[i] = (row, okk)
			log("  retry" + line[1:])

	el = time.monotonic() - t0
	log(f"Fetch time {el/60:.1f} min ({el / max(n_all, 1):.2f} s per symbol)  |  rate-limit replies: {api.n_rate}  |  connection timeouts: {api.n_net}"
	    f"  |  final pace {api.limiter.rate:.2f} requests/s")
	out, n_ok, n_fail = save(True)
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
