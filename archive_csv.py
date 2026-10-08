#!/usr/bin/env python3
"""
archive_csv.py — weekly snapshots of the valuation CSVs into ONE zip
=====================================================================
  python archive_csv.py            snapshot all 3 CSVs + sweep loose dated CSVs
  python archive_csv.py --list     show what is inside old_csv.zip

What it does
  1. SNAPSHOT  nifty500_valuation.csv  →  old_csv.zip / 021026_nifty500_valuation.csv
               (DDMMYY = the date the data was scraped = newest DATE_DOWNLOADED in
               the file; falls back to the file's last git commit date, then today)
               Same name already in the zip → skipped, so re-running is harmless.
  2. SWEEP     any loose CSV in this folder with a date in its name
               (021026_x.csv, x_2026-10-02.csv, x_20261002.csv …) is moved into the zip.
  3. RETENTION keeps the newest KEEP_PER_INDEX snapshots per index so the zip stays
               well under GitHub's 100 MB file limit (~1.1 MB per weekly set of 3).

The scrapers (n500.py / ns500.py / nm250.py) call snapshot() themselves before they
touch their CSV, and the workflow also runs this file once before a full run.
"""

import re
import sys
import argparse
import subprocess
import zipfile
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd

BASE           = Path(__file__).resolve().parent
ZIP_PATH       = BASE / "old_csv.zip"
KEEP_PER_INDEX = 40          # ≈ 40 weeks per index  (~45 MB zip). Set 0 to keep everything.
CSV_FILES      = ["nifty500_valuation.csv",
                  "niftysmallcap500_valuation.csv",
                  "niftymicrocap250_valuation.csv"]

# a date anywhere in a file name: 021026 / 20261002 / 2026-10-02 / 02-10-2026
DATE_IN_NAME = re.compile(r"(?<!\d)(\d{6}|\d{8}|\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})(?!\d)")
SNAP_NAME    = re.compile(r"^(\d{2})(\d{2})(\d{2})_(.+\.csv)$")      # DDMMYY_<name>.csv


def _today_ist() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def data_date(path: Path) -> date:
    """Date the CSV's data was scraped."""
    try:
        d = pd.read_csv(path, usecols=["DATE_DOWNLOADED"])
        dt = pd.to_datetime(d["DATE_DOWNLOADED"], errors="coerce").max()
        if pd.notna(dt):
            return dt.date()
    except Exception:
        pass
    try:   # last commit that touched the file (checkout resets file mtime, so mtime is useless in CI)
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path.name],
                             cwd=path.parent, capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            return datetime.strptime(out, "%Y-%m-%d").date()
    except Exception:
        pass
    return _today_ist()


def _names_in_zip() -> set:
    if not ZIP_PATH.exists():
        return set()
    with zipfile.ZipFile(ZIP_PATH) as z:
        return set(z.namelist())


def _add(src: Path, arcname: str) -> bool:
    if arcname in _names_in_zip():
        print(f"  = {arcname} already in {ZIP_PATH.name}")
        return False
    with zipfile.ZipFile(ZIP_PATH, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(src, arcname)
    print(f"  + {arcname}  →  {ZIP_PATH.name}")
    return True


def snapshot(csv_path) -> str | None:
    """Save the CURRENT csv into old_csv.zip as DDMMYY_<name>.csv. Returns the archive name."""
    p = Path(csv_path)
    if not p.is_absolute():
        p = BASE / p
    if not p.exists() or p.stat().st_size < 10:
        print(f"  - {p.name}: nothing to snapshot")
        return None
    arc = f"{data_date(p):%d%m%y}_{p.name}"
    _add(p, arc)
    return arc


def sweep_loose_dated_csvs() -> int:
    """Move every loose CSV with a date in its name into the zip."""
    moved = 0
    for f in sorted(BASE.glob("*.csv")):
        if f.name in CSV_FILES or not DATE_IN_NAME.search(f.name):
            continue
        _add(f, f.name)
        f.unlink()
        moved += 1
    return moved


def _snap_date(name: str):
    m = SNAP_NAME.match(name)
    if not m:
        return None
    dd, mm, yy, base = m.groups()
    try:
        return date(2000 + int(yy), int(mm), int(dd)), base
    except ValueError:
        return None


def apply_retention(keep: int = KEEP_PER_INDEX) -> int:
    if keep <= 0 or not ZIP_PATH.exists():
        return 0
    with zipfile.ZipFile(ZIP_PATH) as z:
        infos = z.infolist()
    groups = {}
    for i in infos:
        sd = _snap_date(i.filename)
        if sd:
            groups.setdefault(sd[1], []).append((sd[0], i.filename))
    drop = set()
    for base, items in groups.items():
        items.sort(reverse=True)
        drop |= {n for _, n in items[keep:]}
    if not drop:
        return 0
    tmp = ZIP_PATH.with_suffix(".tmp.zip")
    with zipfile.ZipFile(ZIP_PATH) as zin, \
         zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for i in zin.infolist():
            if i.filename not in drop:
                zout.writestr(i, zin.read(i.filename))
    tmp.replace(ZIP_PATH)
    print(f"  ✂ retention: removed {len(drop)} old snapshots (keeping {keep} per index)")
    return len(drop)


def list_zip():
    if not ZIP_PATH.exists():
        print("old_csv.zip does not exist yet"); return
    with zipfile.ZipFile(ZIP_PATH) as z:
        for i in sorted(z.infolist(), key=lambda i: (_snap_date(i.filename) or (date.min, ""))[0]):
            print(f"  {i.filename:<55} {i.file_size/1e6:6.2f} MB  →  {i.compress_size/1e6:5.2f} MB")
    print(f"  zip size: {ZIP_PATH.stat().st_size/1e6:.2f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        list_zip(); return
    print(f"Archiving into {ZIP_PATH.name} …")
    for name in CSV_FILES:
        snapshot(BASE / name)
    sweep_loose_dated_csvs()
    apply_retention()
    if ZIP_PATH.exists():
        mb = ZIP_PATH.stat().st_size / 1e6
        print(f"Done — {ZIP_PATH.name} is {mb:.1f} MB")
        if mb > 90:
            print("::warning::old_csv.zip is close to GitHub's 100 MB limit — lower KEEP_PER_INDEX")


if __name__ == "__main__":
    sys.exit(main())
