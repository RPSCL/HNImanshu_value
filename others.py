#!/usr/bin/env python3
"""
others.py — fundamentals for every NSE stock OUTSIDE the three lists (NIFTY 500, Smallcap 500, Microcap 250)
==============================================================================================================
Two jobs:

  python others.py --list-only
      Downloads NSE's list of every listed company, EQUITY_L.csv, into this folder (repo root).
      The website uses it for the Portfolio's symbol hints (any NSE stock can be added), and the full run below
      uses it to know which stocks are "others". If NSE can't be reached, the existing file is kept.

  python others.py --half 1   (others.yml, Sunday 00:00 IST)   first half of the others (A…, by symbol)
  python others.py --half 2   (others.yml, Monday 00:00 IST)   second half
  python others.py            all of them in one go (by hand)
      Two halves on two nights because screener.in slows down to minutes per stock after ~1,000 stocks from
      one machine. Each half updates only its own rows; the other half's rows stay as they are.
      1. refreshes EQUITY_L.csv as above
      2. others = every symbol in EQUITY_L.csv that is in none of nifty500_valuation.csv,
         niftysmallcap500_valuation.csv, niftymicrocap250_valuation.csv
      3. scrapes them from screener.in with the SAME scraper, merge rules, scores and master-file upsert as
         n500.py (imported, not copied: a fix in n500.py reaches this list too), into
         Nifty_Others_Fundamentals.csv
      4. fills INDUSTRY (the index lists come with one, EQUITY_L does not) from screener's sector,
         else keeps last week's, else "Others"

  Many of these companies are small: screener often has little or no data for them. Such rows are kept
  (name only, or price only) and the website shows N/A for whatever is missing. The build drops rows
  with neither a price nor a market cap.

  Other options: --limit 20 (test: only the first 20 of the half), --delay 2 (seconds between stocks).
"""
import argparse
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
EQUITY_CSV = HERE / "EQUITY_L.csv"
OTHERS_CSV = HERE / "Nifty_Others_Fundamentals.csv"
INDEX_CSVS = [HERE / "nifty500_valuation.csv", HERE / "niftysmallcap500_valuation.csv", HERE / "niftymicrocap250_valuation.csv"]
NSE_HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/csv,text/plain,*/*", "Referer": "https://www.nseindia.com/"}


def say(msg):
	print(msg, flush=True)


def refresh_equity_list():
	"""Download EQUITY_L.csv into the repo root. Keeps the old file if NSE fails or sends something odd."""
	try:
		r = requests.get(EQUITY_URL, headers=NSE_HEAD, timeout=30)
		r.raise_for_status()
		text = r.content.decode("utf-8", "ignore")
		head = text.splitlines()[0].upper() if text else ""
		rows = text.count("\n")
		if "SYMBOL" not in head or rows < 1000:                  # an error page or a cut-off file: keep the old one
			raise ValueError(f"unexpected file ({rows} lines)")
		EQUITY_CSV.write_text(text, encoding="utf-8")
		say(f"EQUITY_L.csv: {rows - 1} companies saved")
		return True
	except Exception as e:
		say(f"EQUITY_L.csv not refreshed ({e})" + ("; using the existing file" if EQUITY_CSV.exists() else ""))
		return False


def equity_list():
	"""NSE's list as a DataFrame shaped like the index lists: SYMBOL, COMPANY_NAME, SERIES, ISIN_CODE."""
	import pandas as pd
	d = pd.read_csv(EQUITY_CSV, dtype=str)
	d.columns = [str(c).strip().upper().replace(" ", "_") for c in d.columns]
	ren = {}
	for c in d.columns:
		if c == "SYMBOL" or "SYMBOL" in c:
			ren[c] = "SYMBOL"
		elif "NAME" in c:
			ren[c] = "COMPANY_NAME"
		elif c.startswith("SERIES"):
			ren[c] = "SERIES"
		elif "ISIN" in c:
			ren[c] = "ISIN_CODE"
	d = d.rename(columns=ren)
	d = d[[c for c in ("SYMBOL", "COMPANY_NAME", "SERIES", "ISIN_CODE") if c in d.columns]]
	d["SYMBOL"] = d["SYMBOL"].astype(str).str.strip().str.upper()
	return d[d["SYMBOL"].ne("") & d["SYMBOL"].ne("NAN")].drop_duplicates("SYMBOL")


def in_three_lists():
	import pandas as pd
	out = set()
	for p in INDEX_CSVS:
		if p.exists():
			try:
				out |= set(pd.read_csv(p, usecols=["SYMBOL"])["SYMBOL"].dropna().astype(str).str.strip().str.upper())
			except Exception as e:
				say(f"  could not read {p.name}: {e}")
	return out


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--list-only", action="store_true", help="only refresh EQUITY_L.csv")
	ap.add_argument("--half", type=int, choices=(1, 2), default=None, help="scrape only this half (by symbol)")
	ap.add_argument("--limit", type=int, default=None, help="test: only this many stocks")
	ap.add_argument("--delay", type=float, default=None, help="seconds between stocks")
	a = ap.parse_args()
	if a.list_only:
		refresh_equity_list()
		return 0

	refresh_equity_list()
	if not EQUITY_CSV.exists():
		say("No EQUITY_L.csv and NSE not reachable: nothing to do.")
		return 0

	import pandas as pd
	import n500 as base                                   # the scraper + scoring engine (its main() is not run on import)
	from bs4 import BeautifulSoup

	allx = equity_list()
	taken = in_three_lists()
	others = allx[~allx["SYMBOL"].isin(taken)].sort_values("SYMBOL").reset_index(drop=True)
	all_syms = others["SYMBOL"].tolist()
	cut = (len(all_syms) + 1) // 2
	todo = all_syms[:cut] if a.half == 1 else all_syms[cut:] if a.half == 2 else all_syms
	if a.limit:
		todo = todo[:a.limit]
	say(f"NSE lists {len(allx)} companies; {len(taken)} are in the 3 lists; {len(all_syms)} others"
	    + (f"; half {a.half}: {len(todo)} stocks ({todo[0] if todo else '-'} … {todo[-1] if todo else '-'})" if a.half else f"; scraping {len(todo)}"))

	# point n500.py's engine at this list and this file
	base.INDEX = "others"
	base.OUTPUT_FILE = OTHERS_CSV.name
	base.fetch_nifty500 = lambda: others.copy()           # INDUSTRY is deliberately not here (filled below)

	parse0 = base._parse_screener_page                    # + the sector, for an INDUSTRY column
	def parse(symbol, html, url):
		r = parse0(symbol, html, url)
		if not r.get("SECTOR"):
			try:
				soup = BeautifulSoup(html, "lxml")
				a = soup.select_one("a[title='Sector']") or soup.select_one("a[title='Broad Sector']")
				if not a:
					links = soup.select("#peers a[href*='/market/'], .sub a[href*='/market/']")
					a = links[1] if len(links) > 1 else (links[0] if links else None)
				if a and a.get_text(strip=True):
					r["SECTOR"] = a.get_text(strip=True)
			except Exception:
				pass
		return r
	base._parse_screener_page = parse

	old_ind = {}
	if OTHERS_CSV.exists():
		try:
			o = pd.read_csv(OTHERS_CSV, usecols=lambda c: c in ("SYMBOL", "INDUSTRY"), low_memory=False)
			if "INDUSTRY" in o:
				old_ind = dict(zip(o["SYMBOL"].astype(str), o["INDUSTRY"]))
		except Exception:
			pass

	# scrape this half; rows of the other half are carried over unchanged (n500.py's master upsert:
	# "not scraped this run → old row kept"), then the whole file is re-scored
	master = base.build_master_csv(others, todo, str(OTHERS_CSV), delay=a.delay if a.delay is not None else base.REQUEST_DELAY,
	                               force_refresh=False, index_symbols=all_syms)
	if master is None or master.empty:
		say("Nothing scraped and no earlier file: stopping.")
		return 0
	d = base.compute_final_score(base.compute_fair_values(base.compute_scores(master.copy())))

	# INDUSTRY: screener's sector, else last week's, else "Others"
	sec = d["SECTOR"] if "SECTOR" in d.columns else pd.Series(index=d.index, dtype=object)
	prev = d["SYMBOL"].astype(str).map(old_ind)
	d = d.assign(INDUSTRY=sec.where(sec.notna() & sec.astype(str).str.strip().ne(""), prev).fillna("Others"))
	d.to_csv(OTHERS_CSV, index=False)
	has = d["CMP"].notna().sum() if "CMP" in d.columns else 0
	say(f"{OTHERS_CSV.name}: {len(d)} rows, {has} with a price, {d['INDUSTRY'].ne('Others').sum()} with a sector")
	return 0


if __name__ == "__main__":
	sys.exit(main())
