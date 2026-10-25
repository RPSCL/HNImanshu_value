"""
paid.py — Plus / Prime data files are published ENCRYPTED, so only signed-in Plus / Prime members can read them.

Which files: every "Others" file (all NSE stocks outside the 3 lists) — fundamentals_oth.json, fundamentals_back_<P>_oth.json,
fundamentals_hist_oth.json, fundamentals_hist_then_oth.json and the Valuation page's screener_oth.json.

How:
  build  → public/p/<name>.bin = 12-byte nonce + AES-256-GCM( gzip(json) ), key = the PAID_KEY secret (GitHub Actions secret).
           The plain <name> is NOT written. The .bin files are committed and deployed like any other file: without the key
           they are just noise, so it doesn't matter that the repo and the site are public.
  page   → auth.js sends any request for one of these names to the data Worker (data_worker.js) with the user's login token.
  Worker → checks the login and the plan (Plus, Prime or admin), fetches /p/<name>.bin from the site, decrypts it with the
           same PAID_KEY and returns the JSON (still gzipped, Content-Encoding: gzip).

The nonce is taken from the content itself (SHA-256 of the gzip), so the same data gives the same .bin: a rebuild with
unchanged data changes nothing in git or on the site.

No PAID_KEY (a local / offline build): the plain JSON is written as before, so nothing breaks while testing.

Make a key once:  python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
→ the same value as the GitHub secret PAID_KEY and the data Worker's secret PAID_KEY.
"""
import base64
import gzip
import hashlib
import os
import re
from pathlib import Path

PAID_RE = re.compile(r"^(fundamentals_(?:back_[A-Za-z0-9_]+_|hist_(?:then_)?)?oth|screener_oth)\.json$")


def _key():
    k = os.environ.get("PAID_KEY", "").strip()
    if not k:
        return None
    raw = base64.b64decode(k)
    if len(raw) != 32:
        raise ValueError("PAID_KEY must be 32 bytes, base64")
    return raw


def is_paid(name: str) -> bool:
    return bool(PAID_RE.match(name))


def write(folder, name: str, text: str) -> int:
    """Write <name> into folder (public/): encrypted to p/<name>.bin if it is a paid file and PAID_KEY is set, else plain.
    Returns the number of bytes of the JSON (for the build's size lines)."""
    folder = Path(folder)
    data = text.encode("utf-8")
    key = _key() if is_paid(name) else None
    if key is None:
        (folder / name).write_bytes(data)
        return len(data)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    nonce = hashlib.sha256(gz).digest()[:12]
    blob = nonce + AESGCM(key).encrypt(nonce, gz, None)
    (folder / "p").mkdir(exist_ok=True)
    out = folder / "p" / (name + ".bin")
    if not out.exists() or out.read_bytes() != blob:
        out.write_bytes(blob)
    plain = folder / name
    if plain.exists():                                    # an old plain copy must never be deployed again
        plain.unlink()
    return len(data)
