#!/usr/bin/env bash
# .github/oracle_guard.sh — GitHub's own timetable is only a BACK-UP for the Oracle server's crontab.
# A scheduled run calls this first: did the Oracle server already start this job recently? Then the scheduled run
# stands down; if not (server off, trigger failed), it does the job itself.
#
#   oracle_guard.sh <workflow file> <minutes to look back> <title regex>
#
# "Started by Oracle" = a run of <workflow file>, NOT itself a scheduled back-up, not cancelled (refused / cancelled
# runs don't count), created in the last <minutes>, whose title (run-name, which carries the inputs) matches <regex>.
# Writes go=true / go=false to $GITHUB_OUTPUT. If GitHub's run list can't be read, it runs (better twice than never).
set -uo pipefail
WF="${1:?workflow file}"; MIN="${2:?minutes}"; RX="${3:?title regex}"
SINCE="$(date -u -d "-${MIN} min" +%Y-%m-%dT%H:%M:%SZ)"
N="$(gh api -X GET "repos/${GITHUB_REPOSITORY}/actions/workflows/${WF}/runs" -f created=">=${SINCE}" -f per_page=100 \
  --jq "[.workflow_runs[] | select(.id != ${GITHUB_RUN_ID} and .event != \"schedule\" and .conclusion != \"cancelled\"
          and (.display_title | test(\"${RX}\")))] | length" 2>/dev/null)" || N=""
NOW="$(TZ=Asia/Kolkata date '+%a %d %b %H:%M IST')"
if [ -z "$N" ]; then
  echo "$NOW · $WF: run history unreadable → running the back-up"; GO=true
elif [ "$N" -gt 0 ]; then
  echo "$NOW · $WF: $N run(s) matching /$RX/ in the last $MIN min (Oracle did it) → standing down"; GO=false
else
  echo "$NOW · $WF: nothing matching /$RX/ in the last $MIN min (Oracle didn't start it) → running the back-up"; GO=true
fi
echo "go=$GO" >> "$GITHUB_OUTPUT"
[ "$GO" = true ] && echo "### 🛟 Back-up run: Oracle did not start this (/$RX/ in last $MIN min)" >> "$GITHUB_STEP_SUMMARY" \
                 || echo "### ⏭ Oracle already started this — back-up not needed" >> "$GITHUB_STEP_SUMMARY"
exit 0
