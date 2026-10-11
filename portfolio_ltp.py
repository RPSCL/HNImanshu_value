#!/usr/bin/env python3
"""
portfolio_ltp.py — live LTP (+ previous close) from Angel One for the stocks held in users' portfolios only
==========================================================================================================
What it does
  1. Reads every portfolio in Firestore (hni_portfolio/{uid}) with the Firebase service account and works out
     which symbols are still held (buys − sells > 0). Nothing else is read.
  2. Once per Indian day, checks every held symbol against NSE's master lists (EQUITY_L.csv main board,
     SME_EQUITY_L.csv) and the symbol-change list:
       • renamed by NSE (OLDNAME → NEWNAME): priced under the new symbol; the website moves the holding itself
       • not on NSE's list any more and not renamed (delisted / suspended / typo): listed in "notListed",
         the Portfolio page asks the owner to update the symbol
       • the company's current name on NSE goes into "names" (a name change shows up by itself)
     The three NSE files and Angel's scrip master are downloaded on the first run of the day only and kept in
     .cache/ (the workflow carries .cache/ between runs with actions/cache, keyed by the IST date).
  3. Logs in to Angel One (same secrets / login as fetch_prices.py) and asks for LTP + previous close,
     50 symbols per call (Angel's limit), about 1 call a second.
  4. Writes ONE Firestore document, hni_market/ltp:
        {asOf, prices: {SYMBOL: {ltp, prev}}, renames: {OLD: NEW}, missing: [symbols Angel doesn't know],
         notListed: [held symbols on no NSE list], names: {SYMBOL: company name}, checked: "YYYY-MM-DD"}
     The Portfolio page reads it: Value, P&L and Day gain use these prices; renames are applied automatically.

Run
  python portfolio_ltp.py            # normal
  python portfolio_ltp.py --dry      # no Firestore write; prints what it would write
Needs
  pip install firebase-admin smartapi-python pyotp logzero websocket-client
  GOOGLE_APPLICATION_CREDENTIALS = path of the service-account JSON (the workflow writes it from GOOGLE_CREDENTIALS)
  ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET (same as fetch_prices.py)
"""

import argparse
import csv
import io
import sys
import time
from datetime import datetime

import requests

from fetch_prices import Angel, scrip_master, build_token_map, log, IST, CACHE_DIR   # reuse login + symbol → token map

PORTFOLIOS = "hni_portfolio"
OUT_COLL, OUT_DOC = "hni_market", "ltp"
BATCH = 50                                   # Angel getMarketData: max 50 tokens per request
SYMBOL_CHANGE_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
EQUITY_URLS = ["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",          # main board
               "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"]   # SME (Emerge)
NSE_HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.nseindia.com/"}


def nse_file(url):
	"""Text of an NSE archive file, downloaded once per IST day and kept in .cache/. None if NSE can't be reached."""
	CACHE_DIR.mkdir(exist_ok=True)
	day = datetime.now(IST).strftime("%Y%m%d")
	name = url.rsplit("/", 1)[1].split(".")[0].lower()
	f = CACHE_DIR / f"nse_{name}_{day}.csv"
	if f.exists():
		return f.read_text(encoding="utf-8")
	try:
		r = requests.get(url, timeout=25, headers=NSE_HEAD)
		r.raise_for_status()
		text = r.content.decode("utf-8", "ignore")
	except Exception as e:                                  # NSE sometimes blocks cloud IPs; tried again next run
		log(f"NSE {name}: not reachable ({e})")
		return None
	for old in CACHE_DIR.glob(f"nse_{name}_*.csv"):
		old.unlink()
	f.write_text(text, encoding="utf-8")
	log(f"NSE {name}: downloaded (first run today)")
	return text


def nse_master():
	"""{SYMBOL: company name} for everything listed on NSE today (main board + SME). Empty if NSE was unreachable."""
	out = {}
	for url in EQUITY_URLS:
		text = nse_file(url)
		if not text:
			continue
		rows = list(csv.reader(io.StringIO(text)))
		if not rows:
			continue
		head = [c.strip().upper() for c in rows[0]]
		si = next((i for i, c in enumerate(head) if c == "SYMBOL"), 0)
		ni = next((i for i, c in enumerate(head) if "NAME" in c), 1)
		for r_ in rows[1:]:
			if len(r_) > max(si, ni) and r_[si].strip():
				out[r_[si].strip().upper()] = r_[ni].strip()
	return out


def held_symbols(db):
	"""Every symbol with a positive quantity in any user's portfolio (buys − sells)."""
	held = set()
	n_users = 0
	for snap in db.collection(PORTFOLIOS).stream():
		n_users += 1
		qty = {}
		for t in (snap.to_dict() or {}).get("txns", []) or []:
			if t.get("t") not in ("buy", "sell") or not t.get("sym"):
				continue
			s = str(t["sym"]).upper().strip()
			qty[s] = qty.get(s, 0) + (t.get("qty") or 0) * (1 if t["t"] == "buy" else -1)
		held |= {s for s, q in qty.items() if q > 1e-9}
	log(f"Portfolios read: {n_users} users, {len(held)} symbols held")
	return sorted(held)


def nse_renames():
	"""{OLD: NEW} from NSE's symbol-change file. Best effort: an empty dict if NSE can't be reached."""
	text = nse_file(SYMBOL_CHANGE_URL)
	if not text:                                            # prices still work without it
		return {}
	rows = list(csv.reader(io.StringIO(text)))
	if not rows:
		return {}
	head = [c.strip().upper() for c in rows[0]]
	o = next((i for i, c in enumerate(head) if "OLD" in c or "KEY_SYMBOL" in c), None)   # NSE: SM_KEY_SYMBOL = old symbol
	n = next((i for i, c in enumerate(head) if "NEW" in c and "SYMBOL" in c), None)
	body = rows[1:] if o is not None and n is not None else rows
	if o is None or n is None:                              # no header: company, old symbol, new symbol, date
		o, n = 1, 2
	out = {}
	for r_ in body:                                         # file is oldest → newest, so chains resolve to the latest
		if len(r_) > max(o, n):
			old, new = r_[o].strip().upper(), r_[n].strip().upper()
			if old and new and old != new:
				out[old] = new
	def final(s, seen=()):                                  # A → B → C becomes A → C
		return final(out[s], seen + (s,)) if s in out and s not in seen else s
	return {k: final(k) for k in out}


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--dry", action="store_true", help="print, don't write to Firestore")
	a = ap.parse_args()

	import firebase_admin
	from firebase_admin import firestore
	firebase_admin.initialize_app()                        # uses GOOGLE_APPLICATION_CREDENTIALS
	db = firestore.client()

	syms = held_symbols(db)
	if not syms:
		log("Nobody holds anything yet; nothing to price.")
		return 0

	ren_all = nse_renames()
	renames = {s: ren_all[s] for s in syms if s in ren_all}    # only renames that touch a held stock
	if renames:
		log("Renamed by NSE: " + ", ".join(f"{k}→{v}" for k, v in renames.items()))

	tmap = build_token_map(scrip_master())                 # {SYMBOL: (angel_symbol, token)}

	# daily match of every held symbol with NSE's master list
	master = nse_master()
	names, not_listed = {}, []
	for s in syms:
		live = renames.get(s, s)
		if master:
			if live in master:
				names[live] = master[live]
			else:
				not_listed.append(s)
		elif live not in tmap:                              # NSE unreachable: Angel's list is the fallback check
			not_listed.append(s)
	log(f"NSE check: {len(syms) - len(not_listed)} of {len(syms)} held symbols listed"
	    + (f"; not listed: {', '.join(not_listed)}" if not_listed else "")
	    + ("" if master else " (NSE list unreachable, checked against Angel's list)"))
	want, missing = {}, []
	for s in syms:
		live = renames.get(s, s)
		if live in tmap:
			want[tmap[live][1]] = live
		else:
			missing.append(s)
	if missing:
		log(f"Not on Angel (unlisted / suspended / typo): {', '.join(missing)}")

	angel = Angel()                                        # logs in (same as fetch_prices.py)
	prices = {}
	tokens = list(want)
	for i in range(0, len(tokens), BATCH):
		chunk = tokens[i:i + BATCH]
		for attempt in range(3):
			try:
				res = angel.api.getMarketData("OHLC", {"NSE": chunk})
				for f in ((res or {}).get("data") or {}).get("fetched", []) or []:
					sym = want.get(str(f.get("symbolToken")))
					if sym and f.get("ltp"):
						prices[sym] = {"ltp": round(float(f["ltp"]), 2), "prev": round(float(f.get("close") or 0), 2) or None}
				break
			except Exception as e:
				log(f"  batch {i // BATCH + 1}: {e} (retry {attempt + 1}/3)")
				time.sleep(2 + attempt * 2)
		time.sleep(1.1)                                     # stay under Angel's rate limit
	for old, new in renames.items():                       # held under the old name → same price under it too
		if new in prices:
			prices[old] = prices[new]
	log(f"Priced {len(prices)} of {len(syms)} held symbols")

	doc = {"asOf": datetime.now(IST).strftime("%Y-%m-%d %H:%M"), "prices": prices, "renames": renames, "missing": missing,
	       "notListed": not_listed, "names": names, "checked": datetime.now(IST).strftime("%Y-%m-%d")}
	if a.dry:
		print(doc)
		return 0
	db.collection(OUT_COLL).document(OUT_DOC).set(doc)
	log(f"Written {OUT_COLL}/{OUT_DOC} at {doc['asOf']} IST")
	return 0


if __name__ == "__main__":
	sys.exit(main())
