#!/usr/bin/env bash
# oracle/news.sh — news scans on the Oracle server (crontab). Each scan: fetch_news.py → commit multi_stock_news.csv →
# push to GitHub → GitHub's "Smart Build & Deploy" (news mode: build.py + Firebase deploy, ~2 min, no Telegram message).
# The Oracle server never deploys the site itself, so it can never overwrite pages that GitHub built.
#
#   news.sh market   portfolio stocks → push (site has them in ~2 min), THEN the next 1,000 of the list → push
#   news.sh chunk    only the next 1,000 of the list → push
#
#   Portfolio stocks = everything anyone holds, read LIVE from Firestore (hni_portfolio) at the start of every scan.
#   "Next 1,000" = the 1,000 stocks asked longest ago (news_state.json remembers when each was asked), so
#   chunk after chunk walks through the whole list (~2,600 stocks → 3 chunks), then starts over.
#   Google slows us down 3 times, or the time limit is used up → that scan stops, what was found is pushed + deployed.
#   Ctrl+C once → stops in a few seconds, saves, pushes what was found.   Ctrl+C twice → quits, nothing pushed.
#   Folders: repo = <base>/hni, secrets = <base>/hni-secrets (base = /home/opc/newalgo). Log: ~/hni-news.log
set -uo pipefail

MODE="${1:-chunk}"
CHUNK=1000
REPO="$(cd "$(dirname "$0")/.." && pwd)"          # …/newalgo/hni (this file is …/hni/oracle/news.sh)
SECRETS="$(dirname "$REPO")/hni-secrets"          # …/newalgo/hni-secrets
PY="$REPO/venv/bin/python"
export GOOGLE_APPLICATION_CREDENTIALS="$SECRETS/sa.json"   # Firestore: who holds what
export TZ=Asia/Kolkata PYTHONUNBUFFERED=1

# never two at once (one still running when the next cron time comes → the new one just skips)
exec 9>/tmp/hni-news.lock
flock -n 9 || { echo "$(date '+%F %T') [$MODE] previous scan still running, skipped"; exit 0; }

cd "$REPO" || exit 1
echo "===== $(date '+%F %T') news scan: $MODE"

# a scan stopped before its push: commit its CSV now so the pull below can't lose it
git add multi_stock_news.csv
git diff --cached --quiet || git commit -q -m "News from Oracle (left over)"

# newest code + stock lists (NOT the holdings: those come from Firestore). An unpushed commit is replayed on top.
git pull --rebase -X theirs -q origin main || { git rebase --abort 2>/dev/null; echo "pull failed, going on with the code here"; }

push() {   # commit the CSV and push; GitHub builds + deploys
  git add multi_stock_news.csv
  git diff --cached --quiet || git commit -q -m "News from Oracle: $1 ($(date '+%d %b %H:%M IST'))"
  if [ -z "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]; then echo "no new news, nothing pushed"; return 0; fi
  for i in 1 2 3; do
    if git pull --rebase -X theirs -q origin main && git push -q origin HEAD:main; then echo "pushed $(date '+%T') → site updated in ~2 min"; return 0; fi
    git rebase --abort 2>/dev/null || true
    sleep $((i * 20))
  done
  echo "push FAILED (kept here, goes with the next scan)"
}

scan() {   # scan <label> <fetch_news options…>; returns 130 on Ctrl+C twice
  local label="$1"; shift
  echo "--- $label"
  "$PY" -u fetch_news.py "$@" --max-slow 3 --quiet
  local rc=$?
  if [ $rc -eq 130 ]; then echo "stopped by hand (Ctrl+C twice): nothing pushed"; return 130; fi
  [ $rc -ne 0 ] && echo "fetch_news.py ended with error $rc (whatever it saved is still pushed)"
  push "$label"
}

case "$MODE" in
  market)
    scan "portfolio" --held-only --max-min 8 || exit $?
    scan "next $CHUNK" --skip-held --chunk $CHUNK --max-min 15
    ;;
  chunk)
    scan "next $CHUNK" --skip-held --chunk $CHUNK --max-min 20
    ;;
  *) echo "usage: news.sh market|chunk"; exit 2 ;;
esac
