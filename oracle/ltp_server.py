#!/usr/bin/env python3
"""
ltp_server.py — live prices on the Oracle server (runs 24x7 as the systemd service "hni-ltp")
=============================================================================================
Replaces GitHub Actions for live prices. Nothing on this server is reachable from outside: it only connects OUT to
Google (Firestore), so no open port, no domain, no SSL.

How a "⟳" click reaches it
  1. The Portfolio page (auth.js) writes hni_jobs/ltp_req = {uid, id, at}  (Firestore rules: the user's own uid,
     server time, at most every 30 s per request doc).
  2. This program listens to that document. A new request is checked:
       admin (the main "admin" account, or a hni_admins/{uid} document) → always runs, any time of day, any day
       everyone else → Mon–Fri from 09:00 IST; once per 10 minutes from the LAST price update (hni_market/ltp asOf,
                       the same for every user); after 15:40 only the day's closing-price run
  3. Its answer goes to hni_jobs/ltp_status = {state: running | done | failed | refused | busy, req, msg}; the page shows it.
  4. portfolio_ltp.py runs (same script as before, from this folder) and writes hni_market/ltp.
Timer (instead of Cloudflare's): Mon–Fri every 10 minutes 09:00–15:40 IST, plus one closing run at 15:41.
Only one fetch runs at a time.

Run by hand (testing):  set -a; . /opt/hni-secrets/hni.env; set +a; /opt/hni/venv/bin/python -u oracle/ltp_server.py
Logs (as a service):    journalctl -u hni-ltp -f
"""
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import firestore

IST = timezone(timedelta(hours=5, minutes=30))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the repo folder (portfolio_ltp.py lives there)
PY = sys.executable
MAIN_ADMIN = "admin@users.hnimanshu.app"
GAP_MIN = 10                        # non-admins: minutes between price updates, counted from the last update
OPEN_MIN, CLOSE_MIN = 9 * 60, 15 * 60 + 40
TIMER_EVERY = 10                    # the server's own timer, minutes (market hours)
RUN_TIMEOUT = 600                   # one fetch may take at most 10 minutes
REQ_DOC, STATUS_DOC, MARKET_DOC = "hni_jobs/ltp_req", "hni_jobs/ltp_status", "hni_market/ltp"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("ltp")
RUN_LOCK = threading.Lock()


def now_ist():
	return datetime.now(IST)


def mins_of(t):
	return t.hour * 60 + t.minute


def is_admin(db, uid):
	try:
		if (fb_auth.get_user(uid).email or "") == MAIN_ADMIN:
			return True
	except Exception:
		pass
	try:
		return db.document(f"hni_admins/{uid}").get().exists
	except Exception:
		return False


def last_update(db):
	"""Time of the last price update (hni_market/ltp asOf, "YYYY-MM-DD HH:MM" IST), or None."""
	try:
		a = (db.document(MARKET_DOC).get().to_dict() or {}).get("asOf")
		return datetime.strptime(a, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if a else None
	except Exception:
		return None


def status(db, **kw):
	db.document(STATUS_DOC).set({**kw, "at": firestore.SERVER_TIMESTAMP}, merge=True)


def run_fetch(db, any_time, who, req=None):
	"""portfolio_ltp.py, one at a time. who = uid of the requester, or "timer"."""
	if not RUN_LOCK.acquire(blocking=False):
		log.info(f"busy: request from {who} while a fetch runs")
		if req:
			status(db, state="busy", req=req, msg="already running")
		return
	try:
		status(db, state="running", req=req, who=who, msg="")
		log.info(f"fetch start ({who}{', any time' if any_time else ''})")
		t0 = time.time()
		args = [PY, "-u", "portfolio_ltp.py"] + (["--any-time"] if any_time else [])
		r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=RUN_TIMEOUT)
		out = (r.stdout or "") + (r.stderr or "")
		for line in out.strip().splitlines()[-12:]:
			log.info("  | " + line)
		ok = r.returncode == 0
		status(db, state="done" if ok else "failed", req=req, secs=round(time.time() - t0),
		       msg="" if ok else out.strip().splitlines()[-1][:200] if out.strip() else f"exit {r.returncode}")
		log.info(f"fetch {'done' if ok else 'FAILED'} in {time.time() - t0:.0f} s")
	except subprocess.TimeoutExpired:
		status(db, state="failed", req=req, msg="took too long")
		log.info("fetch FAILED: timeout")
	except Exception as e:
		status(db, state="failed", req=req, msg=str(e)[:200])
		log.info(f"fetch FAILED: {e}")
	finally:
		RUN_LOCK.release()


def decide(db, uid, t):
	"""(go, any_time, refusal message) for a request at IST time t."""
	if is_admin(db, uid):
		return True, True, ""                                       # admins: no limits, any time
	m = mins_of(t)
	if t.weekday() >= 5:
		return False, False, "Market closed today. Live prices run Mon–Fri from 09:00 IST."
	if m < OPEN_MIN:
		return False, False, "Live prices start at 09:00 IST."
	last = last_update(db)
	if last and t < last + timedelta(minutes=GAP_MIN):
		nxt = last + timedelta(minutes=GAP_MIN)
		return False, False, f"Prices can be fetched only after {GAP_MIN} minutes ({nxt:%H:%M})"
	if m > CLOSE_MIN:
		if last and last.date() == t.date() and mins_of(last) >= CLOSE_MIN:
			return False, False, f"Market closed. Today's closing prices are in ({last:%H:%M})."
		return True, True, ""                                       # the day's closing-price run
	return True, False, ""


SEEN = {"id": None}                                                 # the last request handled (its random id)


def on_request(docs, db):
	for d in docs:
		x = d.to_dict() or {}
		rid, uid = x.get("id"), x.get("uid")
		if not rid or not uid or rid == SEEN["id"]:
			continue
		SEEN["id"] = rid
		go, any_time, why = decide(db, uid, now_ist())
		if not go:
			log.info(f"refused {uid}: {why}")
			status(db, state="refused", req=rid, msg=why)
			continue
		threading.Thread(target=run_fetch, args=(db, any_time, uid, rid), daemon=True).start()


def timer(db):
	"""Mon–Fri: every TIMER_EVERY minutes 09:00–15:40 IST, and the closing run at 15:41."""
	done = set()
	while True:
		t = now_ist()
		m, key = mins_of(t), t.strftime("%Y-%m-%d %H:%M")
		if t.weekday() < 5 and key not in done:
			if OPEN_MIN <= m <= CLOSE_MIN and m % TIMER_EVERY == 0:
				done.add(key)
				threading.Thread(target=run_fetch, args=(db, False, "timer"), daemon=True).start()
			elif m == CLOSE_MIN + 1:
				done.add(key)
				threading.Thread(target=run_fetch, args=(db, True, "timer (closing)"), daemon=True).start()
		if len(done) > 500:
			done.clear()
		time.sleep(20)


def main():
	firebase_admin.initialize_app()                                  # GOOGLE_APPLICATION_CREDENTIALS = the service-account JSON
	db = firestore.client()
	log.info(f"ltp server up · repo {REPO} · IST {now_ist():%a %H:%M}")
	threading.Thread(target=timer, args=(db,), daemon=True).start()
	try:                                                             # the request already there at start-up is not a new one
		SEEN["id"] = (db.document(REQ_DOC).get().to_dict() or {}).get("id")
	except Exception:
		pass
	while True:                                                      # listen; re-subscribe every 30 min (a dropped stream comes back)
		watch = db.document(REQ_DOC).on_snapshot(lambda docs, ch, rt: on_request(docs, db))
		time.sleep(1800)
		try:
			watch.unsubscribe()
		except Exception:
			pass


if __name__ == "__main__":
	main()
