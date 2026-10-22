#!/usr/bin/env bash
# oracle/trigger.sh — start a GitHub Actions workflow from the Oracle server's crontab (exact times, unlike GitHub's own
# schedule, which runs late or skips). The work itself still runs on GitHub; this only presses "Run workflow".
#
#   trigger.sh <workflow file> [--if-idle] [input=value ...]
#     trigger.sh cron.yml force_full=1                   Smart Build & Deploy, full run
#     trigger.sh cron.yml force_full=2 price_part=1      Angel prices, first third
#     trigger.sh others.yml half=1                       Others fundamentals, first half
#     trigger.sh ltp.yml --if-idle                       Portfolio LTP, skipped while the last run is still going
#
# Token: the same fine-grained GitHub token news.sh pushes with (hni-secrets/git-credentials), which needs
# "Actions: Read and write" as well as "Contents: Read and write". Log: ~/hni-trigger.log
set -uo pipefail
REPO_SLUG="RPSCL/HNImanshu_value"
BASE="$(cd "$(dirname "$0")/../.." && pwd)"                      # …/newalgo
TOKEN="$(sed -nE 's#^https://[^:]+:([^@]+)@github\.com.*#\1#p' "$BASE/hni-secrets/git-credentials" | head -1)"
[ -n "$TOKEN" ] || { echo "$(date '+%F %T') no token in $BASE/hni-secrets/git-credentials"; exit 1; }

WF="${1:?workflow file, e.g. cron.yml}"; shift
IDLE=0; INPUTS=""
for a in "$@"; do
  if [ "$a" = "--if-idle" ]; then IDLE=1; continue; fi
  k="${a%%=*}"; v="${a#*=}"
  INPUTS="${INPUTS:+$INPUTS,}\"$k\":\"$v\""
done
API="https://api.github.com/repos/$REPO_SLUG/actions/workflows/$WF"
H=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")

if [ $IDLE = 1 ]; then                                          # last run still queued / running → skip this one
  st="$(curl -fsS "${H[@]}" "$API/runs?per_page=1" | grep -m1 '"status"' | sed -E 's/.*"status": *"([a-z_]+)".*/\1/')"
  case "$st" in queued|in_progress|waiting|pending|requested) echo "$(date '+%F %T') $WF still $st, skipped"; exit 0 ;; esac
fi

code="$(curl -sS -o /tmp/hni-trigger.out -w '%{http_code}' -X POST "${H[@]}" "$API/dispatches" \
  -d "{\"ref\":\"main\",\"inputs\":{${INPUTS}}}")"
if [ "$code" = "204" ]; then echo "$(date '+%F %T') started $WF {${INPUTS}}"
else echo "$(date '+%F %T') FAILED $WF {${INPUTS}}: HTTP $code $(head -c 300 /tmp/hni-trigger.out)"; exit 1; fi
