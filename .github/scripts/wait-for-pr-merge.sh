#!/usr/bin/env bash
set -euo pipefail
pr_url=${1:?usage: wait-for-pr-merge.sh PR_URL [TIMEOUT_SECONDS]}
timeout_seconds=${2:-1800}
deadline=$((SECONDS + timeout_seconds))
while (( SECONDS < deadline )); do
  read -r state merge_state < <(gh pr view "$pr_url" --json state,mergeStateStatus --jq '[.state,.mergeStateStatus]|@tsv')
  [[ "$state" == MERGED ]] && exit 0
  [[ "$state" == CLOSED || "$merge_state" == DIRTY ]] && exit 1
  sleep 10
done
exit 1
