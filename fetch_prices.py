#!/usr/bin/env python3
"""
fetch_prices.py — weekly prices from Angel One SmartAPI  →  prices.csv
=======================================================================
For every symbol in the 3 valuation CSVs (+ the NIFTY 500 index as benchmark) it downloads ~2 years of
daily candles and writes ONE row per symbol:

    SYMBOL, ANGEL_SYMBOL, ANGEL_TOKEN,
    LTP, LTP_DATE                         latest close
    PRICE_1Y, PRICE_1Y_DATE               raw close on the last trading day on/before BACK_DATE
    PRICE_1Y_ADJ                          same price on TODAY's share basis (splits / bonuses after it divided out)
    HIGH_52W_NOW, HIGH_52W_1Y             52-week high now / as of BACK_DATE (both on today's share basis)
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
CSV_FILES   = ["nifty500_valuation.csv", "niftysmallcap500_valuation.csv", "niftymicrocap250_valuation.csv"]
SCRIP_URL   = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
BENCH_SYM   = "^NIFTY500"                 # row name used for the benchmark in prices.csv
BENCH_NAMES = {"nifty500"}               # matched against the index's symbol / name, spaces removed, lower case
BACK_DAYS   = 365                         # must match fundamentals.BACK_DAYS
RATE        = 2.5                         # max candle requests per second, shared by all workers (Angel throttles ~3/s)
WORKERS     = 3                           # parallel fetches; network delay overlaps, the rate limit stays global
CHUNK_DAYS  = 380                         # 2 years in 2 calls (~255 rows each, under the 500-row cap some users hit)
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
		d = pd.read_csv(p, usecols=lambda c: c in ("SYMBOL", "DATE_DOWNLOADED"), low_memory=False)
		syms += [str(s).strip().upper() for s in d["SYMBOL"].dropna()]
		if "DATE_DOWNLOADED" in d:
			dt = pd.to_datetime(d["DATE_DOWNLOADED"], errors="coerce").max()
			if pd.notna(dt) and (asof is None or dt > asof):
				asof = dt
	asof = (asof.date() if asof is not None else datetime.now(IST).date())
	return list(dict.fromkeys(syms)), asof


# ─────────────────────────────────────────────────────────────────────────────
# Angel scrip master: SYMBOL → token
# ─────────────────────────────────────────────────────────────────────────────
def scrip_master():
	CACHE_DIR.mkdir(exist_ok=True)
	cache = CACHE_DIR / f"scrip_master_{date.today():%Y%m%d}.json"
	if cache.exists():
		return json.loads(cache.read_text())
	log("Downloading Angel scrip master …")
	r = requests.get(SCRIP_URL, timeout=120)
	r.raise_for_status()
	data = r.json()
	for old in CACHE_DIR.glob("scrip_master_*.json"):
		old.unlink()
	cache.write_text(json.dumps(data))
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

	def candles(self, token, start: date, end: date):
		"""Daily candles [(date, open, close, high)] between start and end, fetched in ~1-year chunks."""
		rows, s = [], start
		while s <= end:
			e = min(end, s + timedelta(days=CHUNK_DAYS))
			rows += self._call(token, s, e)
			s = e + timedelta(days=1)
		out = {}
		for ts, o, h, l, c, v in rows:
			out[str(ts)[:10]] = (float(o), float(c), float(h))
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


def summarise(c, back: date):
	"""Numbers for one symbol from its candles (oldest → newest)."""
	ev = corporate_actions(c)
	def factor_after(d):                                             # product of events strictly after day d
		return math.prod(f for e, f, _ in ev if e > d) if ev else 1.0
	adj = [(d, cl * factor_after(d), hi * factor_after(d)) for d, _, cl, hi in c]
	last = adj[-1]
	before = [x for x in adj if x[0] <= back]
	row = {"LTP": round(c[-1][2], 2), "LTP_DATE": f"{c[-1][0]:%Y-%m-%d}",
	       "HIGH_52W_NOW": round(max(h for d, _, h in adj if d > last[0] - timedelta(days=365)), 2),
	       "CORP_ACTIONS": "; ".join(f"{d:%Y-%m-%d} ×{f:.4g} ({lab})" for d, f, lab in ev)}
	if before:
		d1y = before[-1][0]
		raw = next(cl for d, _, cl, _ in c if d == d1y)
		row.update({"PRICE_1Y": round(raw, 2), "PRICE_1Y_DATE": f"{d1y:%Y-%m-%d}",
		            "PRICE_1Y_ADJ": round(before[-1][1], 4), "ADJ_FACTOR": round(factor_after(d1y), 6),
		            "HIGH_52W_1Y": round(max(h for d, _, h in before if d > back - timedelta(days=365)), 2)})
		row["STATUS"] = "OK"
	else:
		row["STATUS"] = "NOT LISTED ON BACK DATE"                    # listed after it → not in the 1Y-back universe
	return row


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
	a = ap.parse_args()
	if a.is_due:
		return 0 if is_due(a.force) else 1
	if not a.map_only and not is_due(a.force):
		return 0                                                     # weekly rule: nothing to do

	syms, asof = load_symbols()
	back = asof - timedelta(days=BACK_DAYS)
	start = back - timedelta(days=380)                               # enough for the 52-week high as of BACK_DATE
	log(f"{len(syms)} symbols  |  data date {asof}  |  BACK_DATE {back}  |  candles from {start}")

	tmap = build_token_map(scrip_master())
	todo = syms[:a.limit] if a.limit else syms
	todo = todo + [BENCH_SYM]
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
			c = api.candles(tok, start, end)
			if not c:
				raise RuntimeError("no candles")
			row = summarise(c, back)
			p1 = f"1Y {row['PRICE_1Y']:>10,.2f} ({row['PRICE_1Y_DATE']})" if row.get("PRICE_1Y") is not None else "1Y        n/a"
			ca = f"  split/bonus: {row['CORP_ACTIONS']}" if row.get("CORP_ACTIONS") else ""
			return ({**base, **row},
			        f"{tag} LTP {row['LTP']:>10,.2f} ({row['LTP_DATE']})  {p1}  {len(c)} candles  {row['STATUS']}{ca}", True)
		except Exception as ex:
			return ({**base, "STATUS": f"FAILED: {str(ex)[:80]}"}, f"{tag} FAILED  {str(ex)[:100]}", False)

	t0 = time.monotonic()
	results, done = {}, 0
	log(f"Fetching {n_all} symbols  |  {WORKERS} workers  |  max {RATE} requests/s  |  2 requests per symbol")
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
	el = time.monotonic() - t0
	log(f"Fetch time {el/60:.1f} min ({el / max(n_all, 1):.2f} s per symbol)  |  rate-limit replies: {api.n_rate}"
	    f"  |  final pace {api.limiter.rate:.2f} requests/s")

	cols = ["SYMBOL", "ANGEL_SYMBOL", "ANGEL_TOKEN", "LTP", "LTP_DATE", "PRICE_1Y", "PRICE_1Y_DATE", "PRICE_1Y_ADJ",
	        "ADJ_FACTOR", "HIGH_52W_NOW", "HIGH_52W_1Y", "CORP_ACTIONS", "BACK_DATE", "STATUS", "FETCHED_AT"]
	out = pd.DataFrame(rows).reindex(columns=cols)
	out.to_csv(OUT_CSV, index=False)
	n_ca = int(out["CORP_ACTIONS"].fillna("").str.len().gt(0).sum())
	log(f"Done → {OUT_CSV.name}: {n_ok} ok, {n_fail} failed / not mapped, {n_ca} with a split / bonus detected")
	return 0 if n_ok else 1


if __name__ == "__main__":
	sys.exit(main())
