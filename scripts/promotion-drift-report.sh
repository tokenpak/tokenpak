#!/usr/bin/env bash
# Promotion drift report — deterministic content delta between the public
# mirror's main and staging main.
#
# The commit-count between the two branches is a misleading backlog metric:
# public receives squashed promotion PRs, so staging commits never become
# public ancestors and the count grows forever even at content parity. The
# meaningful signal is the TREE DIFF, which this script reports. Same trees
# in, same manifest out.
#
# Usage:
#   scripts/promotion-drift-report.sh [--fetch]
#
# Env overrides: PUBLIC_REMOTE (default: github), STAGING_REMOTE (default:
# github-staging), HOLD_FILE (default: .github/public-promotion-hold.txt).
#
# Exit code: 0 at content parity (ignoring held paths), 1 when drift exists.
set -euo pipefail

PUBLIC_REMOTE="${PUBLIC_REMOTE:-github}"
STAGING_REMOTE="${STAGING_REMOTE:-github-staging}"
HOLD_FILE="${HOLD_FILE:-.github/public-promotion-hold.txt}"

if [[ "${1:-}" == "--fetch" ]]; then
  git fetch -q "$PUBLIC_REMOTE" main
  git fetch -q "$STAGING_REMOTE" main
fi

PUB="refs/remotes/$PUBLIC_REMOTE/main"
STG="refs/remotes/$STAGING_REMOTE/main"

hold_patterns=()
if [[ -f "$HOLD_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    hold_patterns+=("$line")
  done < "$HOLD_FILE"
fi

is_held() {
  local path="$1" pat
  for pat in "${hold_patterns[@]+"${hold_patterns[@]}"}"; do
    # shellcheck disable=SC2053  # intentional glob match against the pattern
    [[ "$path" == $pat ]] && return 0
  done
  return 1
}

echo "# Promotion drift manifest"
echo "# public:  $(git rev-parse --short "$PUB")  ($PUBLIC_REMOTE/main)"
echo "# staging: $(git rev-parse --short "$STG")  ($STAGING_REMOTE/main)"
echo "#"
echo "# status\tpath   (A=only on staging, D=only on public, M=differs)"

total=0
held=0
while IFS=$'\t' read -r status path; do
  [[ -z "${path:-}" ]] && continue
  if is_held "$path"; then
    held=$((held + 1))
    printf 'HELD-%s\t%s\n' "$status" "$path"
  else
    total=$((total + 1))
    printf '%s\t%s\n' "$status" "$path"
  fi
done < <(git diff --no-renames --name-status "$PUB" "$STG")

echo "#"
echo "# drift: $total file(s) (excluding $held held); commit-count (informational only): $(git rev-list --count "$PUB".."$STG")"

[[ "$total" -eq 0 ]] && exit 0 || exit 1
