#!/usr/bin/env bash
# Usage: check.sh <eval.json> <result.json>, from the root of the checkout the run worked in.
# Fails a run that errored, changed files outside the eval's may_change list, or failed any check.
EVALS=$(cd "$(dirname "$1")" && pwd) # a check can call a helper kept beside the eval files
export EVALS
name=$(jq -r '.name' "$1")
total=$(jq '.checks | length' "$1")
passed=0
report=""

strays() { # files changed outside the folders and files this eval allows
  git status --porcelain -uall | cut -c4- | while read -r path; do
    jq -e --arg p "$path" 'any(.may_change[]; . as $ok | $p == $ok or ($p | startswith($ok + "/")))' "$1" >/dev/null ||
      echo "       $path"
  done
}

if [ "$(jq -r '.is_error' "$2" 2>/dev/null)" != "false" ]; then # an empty or broken result counts as an error
  report="     the run ended in an error: $(jq -r '.result // (.errors | join(", "))' "$2" 2>/dev/null)"$'\n'
elif [ -n "$(strays "$1")" ]; then
  report="     the run changed files this eval does not allow:"$'\n'"$(strays "$1")"$'\n'
else
  for i in $(seq 0 $((total - 1))); do
    check=$(jq -r ".checks[$i].name" "$1")
    cmd=$(jq -r ".checks[$i].run" "$1")
    if out=$(bash -c "$cmd" 2>&1 </dev/null); then
      passed=$((passed + 1))
    else
      report+="     failed: $check"$'\n'
      [ -n "$out" ] && report+="$(head -3 <<<"$out" | sed 's/^/       /')"$'\n'
    fi
  done
  if [ -n "$(strays "$1")" ]; then # checks must not change the Makefile or scripts either
    passed=0
    report+="     a check changed files this eval does not allow:"$'\n'"$(strays "$1")"$'\n'
  fi
fi

if [ "$passed" -eq "$total" ]; then
  echo "PASS $name ($passed/$total checks)"
else
  echo "FAIL $name ($passed/$total checks)"
  printf '%s' "$report"
  exit 1
fi
