#!/usr/bin/env bash
# oracle/news.sh — news scan on the Oracle server (crontab, every 2 hours).
#   git pull → fetch_news.py → commit multi_stock_news.csv → push to GitHub.
#   The push starts GitHub's "Smart Build & Deploy" in news mode (build.py + Firebase deploy, ~2 min, no Telegram message).
#   The Oracle server never deploys the site itself, so it can never overwrite pages that GitHub built.
# One-time setup: see NOTES (step-by-step in the chat). Log: ~/hni-news.log
set -uo pipefail

REPO=/opt/hni
PY=/opt/hni/venv/bin/python
export GOOGLE_APPLICATION_CREDENTIALS=/opt/hni-secrets/sa.json   # Firestore: portfolio stocks get news first, every run
export TZ=Asia/Kolkata PYTHONUNBUFFERED=1

# never two scans at once (a slow run is still going when the next cron time comes → this one just skips)
exec 9>/tmp/hni-news.lock
flock -n 9 || { echo "$(date '+%F %T') previous scan still running, skipped"; exit 0; }

cd "$REPO" || exit 1
echo "===== $(date '+%F %T') news scan"

# 1. newest code + stock lists from GitHub (a commit left unpushed last time is kept and replayed on top)
git pull --rebase -X theirs -q origin main || { git rebase --abort 2>/dev/null; git fetch -q origin main && git reset -q --hard origin/main; }

# 2. scan (news_state.json stays on this server: it remembers which stocks were asked when)
timeout 50m "$PY" -u fetch_news.py | grep -vE '^[·•] ' || echo "fetch_news.py ended with an error (whatever it saved is still pushed)"

# 3. commit only the news CSV; no [skip ci], so the push starts the build on GitHub
git add multi_stock_news.csv
if git diff --cached --quiet; then echo "no new news, nothing pushed"; exit 0; fi
git commit -q -m "News from Oracle ($(date '+%d %b %H:%M IST'))"

# 4. push (retry if GitHub's own run pushed meanwhile; in a clash this scan's CSV wins)
for i in 1 2 3; do
  if git pull --rebase -X theirs -q origin main && git push -q origin HEAD:main; then echo "pushed"; exit 0; fi
  git rebase --abort 2>/dev/null || true
  sleep $((i * 20))
done
echo "push FAILED (kept here, goes with the next scan)"
exit 1
