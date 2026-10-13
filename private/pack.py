"""pack.py — minify + scramble admin_ext.js and put it into public/auth.js (the line `const PACK = "...";`).

Run after changing admin_ext.js:   python private/pack.py
Needs Node (npx terser). Keep this folder (private/) out of the public repo.
"""
import base64, pathlib, re, subprocess

here = pathlib.Path(__file__).resolve().parent
src, auth = here / "admin_ext.js", here.parent / "public" / "auth.js"
mini = subprocess.run(["npx", "--yes", "terser", str(src), "--module", "-c", "-m"],
                      check=True, capture_output=True, text=True).stdout.strip()
b = bytearray(mini.encode("utf-8"))
for i in range(len(b)):
    b[i] ^= (i * 7 + 13) & 255                      # same formula as unpack() in auth.js
packed = base64.b64encode(bytes(b)).decode()
js = auth.read_text(encoding="utf-8")
js, n = re.subn(r'const PACK = "[^"]*";', f'const PACK = "{packed}";', js)
assert n == 1, "PACK line not found in auth.js"
auth.write_text(js, encoding="utf-8")
print(f"packed {len(mini):,} chars → {len(packed):,} into {auth}")
