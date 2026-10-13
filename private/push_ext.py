"""push_ext.py — upload the admin tools (admin_ext.js) to Firestore hni_ext/ui, where only admins can read them.

Run it again whenever admin_ext.js changes (the website picks it up on the next login / new tab):

    GOOGLE_APPLICATION_CREDENTIALS=serviceAccount.json python push_ext.py admin_ext.js

Needs:  pip install firebase-admin   and the same service-account JSON the workflows use (GOOGLE_CREDENTIALS).
Keep admin_ext.js, this script and serviceAccount.json OUT of the public repo.
"""
import hashlib
import sys

import firebase_admin
from firebase_admin import firestore

src = sys.argv[1] if len(sys.argv) > 1 else "admin_ext.js"
code = open(src, encoding="utf-8").read()
if "export default" not in code:
    sys.exit(f"{src}: no 'export default' — is this the right file?")

firebase_admin.initialize_app()          # reads GOOGLE_APPLICATION_CREDENTIALS
ver = hashlib.sha256(code.encode()).hexdigest()[:12]
firestore.client().collection("hni_ext").document("ui").set(
    {"code": code, "v": ver, "at": firestore.SERVER_TIMESTAMP})
print(f"uploaded {src}: {len(code):,} chars, version {ver}")
