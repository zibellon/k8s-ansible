#!/usr/bin/env bash
# Run ansible-playbook --syntax-check over every playbook in
# playbook-system/ and playbook-app/, then over every task-file in
# their tasks/ subdirectories (wrapped in a temporary import_tasks
# playbook because bare task-files are not valid Plays).
#
# Each cycle is ONE batched ansible-playbook invocation: the interpreter
# import + inventory parse (~1.4 s) is paid once per cycle instead of
# once per file. A batch stops at the first broken file and reports its
# path, line and column, so the workflow on red is: fix that file, re-run.
# Exit 1 if either cycle fails; otherwise exit 0. Prints OK per file.

set -u

REPO_ROOT="$(pwd)"
BATCH_WRAPPER=/tmp/syntax-wrapper-batch.yaml
BATCH_LOG=/tmp/syntax-batch.log
fail=0

PLAYBOOKS=$(find playbook-system playbook-app -name '*.yaml' -not -path '*/tasks/*' -not -path '*/charts/*' | sort)
TASKFILES=$(ls playbook-system/tasks/*.yaml playbook-app/tasks/*.yaml playbook-app/tasks/vault/*.yaml playbook-app/tasks/argocd/*.yaml)

# === Cycle 1: playbooks (recursive, excluding tasks/ + charts/) ===
if ansible-playbook --syntax-check \
     -i hosts-vars/ -i hosts-vars-test/ $PLAYBOOKS >"$BATCH_LOG" 2>&1; then
  for f in $PLAYBOOKS; do
    echo "OK:   $f"
  done
else
  echo "FAIL: playbook cycle (batch stops at the first broken file; fix it and re-run)"
  cat "$BATCH_LOG"
  fail=1
fi

# === Cycle 2: task files (wrapped in import_tasks playbooks) ===
: > "$BATCH_WRAPPER"
for f in $TASKFILES; do
  cat >> "$BATCH_WRAPPER" <<EOF
- hosts: localhost
  gather_facts: false
  tasks:
    - import_tasks: $REPO_ROOT/$f
EOF
done

if ansible-playbook --syntax-check \
     -i hosts-vars/ -i hosts-vars-test/ "$BATCH_WRAPPER" >"$BATCH_LOG" 2>&1; then
  for f in $TASKFILES; do
    echo "OK:   $f"
  done
else
  echo "FAIL: task-file cycle (batch stops at the first broken file; fix it and re-run)"
  cat "$BATCH_LOG"
  fail=1
fi

rm -f "$BATCH_WRAPPER" "$BATCH_LOG"
exit $fail
