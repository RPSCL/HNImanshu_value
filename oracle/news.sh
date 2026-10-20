#!/usr/bin/env bash
# oracle/news.sh — news scan on the Oracle server (crontab, every 2 hours).
#   git pull → fetch_news.py (2 halves, 15 min apart) → commit multi_stock_news.csv → push to GitHub.
#   The push starts GitHub's "Smart Build & Deploy" in news mode (build.py + Firebase deploy, ~2 min, no Telegram message).
#   The Oracle server never deploys the site itself, so it can never overwrite pages that GitHub built.
#
#   Google slows us down 3 times, or 40 min of fetching used up → fetching stops, what was found is pushed + deployed.
#   Ctrl+C once  → stops fetching in a few seconds, saves, pushes what was found.
#   Ctrl+C twice → quits at once, nothing pushed.
#   Log: ~/hni-news.log
set -uo pipefail

REPO=/opt/hni
PY=/opt/hni/venv/bin/python
export GOOGLE_APPLICATION_CREDENTIALS=/opt/hni-secrets/sa.json   # Firestore: portfolio stocks get news first, every run
export TZ=Asia/Kolkata PYTHONUNBUFFERED=1

# never two scans at once (one still running when the next cron time comes → the new one just skips)
exec 9>/tmp/hni-news.lock
flock -n 9 || { echo "$(date '+%F %T') previous scan still running, skipped"; exit 0; }

cd "$REPO" || exit 1
echo "===== $(date '+%F %T') news scan"

# a scan that was stopped before its push: commit its CSV now so the pull below can't lose it
git add multi_stock_news.csv
git diff --cached --quiet || git commit -q -m "News from Oracle (left over)"

# 1. newest code + stock lists from GitHub (an unpushed commit is replayed on top; in a clash this server's CSV wins)
git pull --rebase -X theirs -q origin main || { git rebase --abort 2>/dev/null; echo "pull failed, going on with the code here"; }

# 2. scan: portfolio stocks + 1,750 more, in 2 halves 15 min apart (news_state.json stays here: which stock was asked when)
"$PY" -u fetch_news.py --halves 2 --gap 15 --max-slow 3 --max-min 40 --quiet
RC=$?
if [ $RC -eq 130 ]; then echo "stopped by hand (Ctrl+C twice): nothing pushed"; exit 130; fi
[ $RC -ne 0 ] && echo "fetch_news.py ended with error $RC (whatever it saved is still pushed)"

# 3. commit only the news CSV; no [skip ci], so the push starts the build + deploy on GitHub
git add multi_stock_news.csv
git diff --cached --quiet || git commit -q -m "News from Oracle ($(date '+%d %b %H:%M IST'))"
if [ -z "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]; then echo "no new news, nothing pushed"; exit 0; fi

# 4. push (retry if GitHub's own run pushed meanwhile)
for i in 1 2 3; do
  if git pull --rebase -X theirs -q origin main && git push -q origin HEAD:main; then echo "pushed $(date '+%T') → GitHub builds + deploys in ~2 min"; exit 0; fi
  git rebase --abort 2>/dev/null || true
  sleep $((i * 20))
done
echo "push FAILED (kept here, goes with the next scan)"
exit 1
