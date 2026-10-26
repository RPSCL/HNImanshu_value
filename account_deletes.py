#!/usr/bin/env python3
"""
account_deletes.py — finishes "Delete user" from the website's Users list.

The website can remove a user's Firestore records itself, but a login account (Firebase Authentication) can only
be deleted with the service account. So the Users list also leaves a note in Firestore hni_deletes/{uid}, and this
script (run by ltp.yml and cron.yml) deletes each noted account for good:
  Firebase Authentication account, hni_users/{uid}, hni_portfolio/{uid}, hni_admins/{uid}, hni_prime/{uid}, hni_game/{uid}, hni_game_top/{uid}, then the note itself.

Run
  python account_deletes.py          # GOOGLE_APPLICATION_CREDENTIALS = service-account JSON
Also imported by portfolio_ltp.py (process(db)).
"""
import sys

QUEUE = "hni_deletes"
DOCS = ("hni_users", "hni_portfolio", "hni_admins", "hni_prime", "hni_game", "hni_game_top")


def process(db, log=print):
	"""Delete every account noted in hni_deletes. Returns how many were done. Never raises."""
	from firebase_admin import auth
	n = 0
	try:
		notes = list(db.collection(QUEUE).stream())
	except Exception as e:
		log(f"Account deletes: could not read {QUEUE} ({e})")
		return 0
	for snap in notes:
		uid = snap.id
		try:
			try:
				auth.delete_user(uid)
			except auth.UserNotFoundError:
				pass                                         # already gone
			for c in DOCS:
				db.collection(c).document(uid).delete()
			snap.reference.delete()
			n += 1
		except Exception as e:                              # tried again on the next run
			log(f"Account delete {uid}: {e}")
	if n:
		log(f"Account deletes: {n} account(s) removed")
	return n


if __name__ == "__main__":
	import firebase_admin
	from firebase_admin import firestore
	firebase_admin.initialize_app()
	process(firestore.client())
	sys.exit(0)
