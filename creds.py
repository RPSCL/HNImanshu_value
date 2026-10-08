#!/usr/bin/env python3
"""
creds.py — broker + Telegram credentials, read from the environment (never hardcoded)
=====================================================================================
On GitHub Actions  : values come from repo secrets (Settings → Secrets and variables → Actions),
                     passed to the step through `env:` in cron.yml.
On your own PC     : put the same NAME=value lines in a `.env` file next to this script
                     (`.env` is already in .gitignore, so it is never committed).

Usage (drop-in for the old hardcoded dicts — same keys):
    from creds import KOTAK, ANGEL, TELEGRAM
    KOTAK["mpin"], ANGEL["totp_token"], TELEGRAM["token"] ...
    from creds import require; require(ANGEL)      # fail fast with a clear message if anything is missing
"""

import os
from pathlib import Path


def _load_dotenv(path=Path(__file__).resolve().parent / ".env"):
    """Minimal .env reader (no extra dependency). Real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
_env = lambda name: os.environ.get(name, "").strip() or None


KOTAK = {
    "api_key"         : _env("KOTAK_API_KEY"),
    "consumer_secret" : _env("KOTAK_CONSUMER_SECRET"),
    "mobile_number"   : _env("KOTAK_MOBILE"),
    "mpin"            : _env("KOTAK_MPIN"),
    "totp_secret"     : _env("KOTAK_TOTP_SECRET"),      # base32 secret for pyotp
    "ucc"             : _env("KOTAK_UCC"),              # Kotak client code
}

ANGEL = {
    "api_key"    : _env("ANGEL_API_KEY"),
    "username"   : _env("ANGEL_CLIENT_CODE"),
    "pwd"        : _env("ANGEL_PIN"),
    "totp_token" : _env("ANGEL_TOTP_SECRET"),          # base32 secret for pyotp
}

TELEGRAM = {
    "token"   : _env("TELEGRAM_BOT_TOKEN"),
    "chat_id" : _env("TELEGRAM_CHAT_ID"),
}


def require(d: dict, optional=()):
    """Raise with the missing key names (never the values) if any credential is empty."""
    missing = [k for k, v in d.items() if not v and k not in optional]
    if missing:
        raise RuntimeError("Missing credentials: " + ", ".join(missing)
                           + " — add them as repo secrets (CI) or in .env (local).")
    return d


def totp(secret: str) -> str:
    """Current 6-digit login code from a base32 TOTP secret."""
    import pyotp                                       # pip install pyotp
    return pyotp.TOTP(secret).now()


if __name__ == "__main__":                              # quick check: shows which creds are present, never their values
    for name, d in (("KOTAK", KOTAK), ("ANGEL", ANGEL), ("TELEGRAM", TELEGRAM)):
        print(f"{name:9s}", "  ".join(f"{k}={'set' if v else 'MISSING'}" for k, v in d.items()))
